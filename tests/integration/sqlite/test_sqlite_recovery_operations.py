from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, recovery_migration_plan
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import (
    SqliteRecoveryOperationStore,
    SqliteRecoveryOperationStoreError,
)
from mediasync_home.application.recovery_intents import durable_recovery_intent_segment
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


def test_sqlite_recovery_operation_store_records_planned_operation_and_event(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryOperationStore(connection)
        operation = _operation()

        assert store.record_planned_operation(
            operation,
            process_instance_id="host-a",
            payload={"reason": "start"},
        ) == operation
        assert store.load_operation(run_id="run-a", operation_id="operation-a") == operation

        row = connection.execute(
            """
            SELECT run_sequence, from_phase, to_phase, payload_json, previous_event_hash, event_hash
            FROM recovery_events
            WHERE run_id = ?
            """,
            ("run-a",),
        ).fetchone()
        assert row[:5] == (0, None, "PLANNED", '{"reason":"start"}', None)
        assert len(str(row[5])) == 64
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_appends_hash_chained_transition_events(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryOperationStore(connection)
        store.record_planned_operation(_operation(), process_instance_id="host-a")

        first = store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.PLANNED,
            next_phase=RecoveryOperationPhase.SOURCE_VALIDATED,
            process_instance_id="host-a",
            payload={"phase": "source"},
        )
        second = store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.SOURCE_VALIDATED,
            next_phase=RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
            process_instance_id="host-a",
            payload={"phase": "guard"},
        )

        assert first is not None
        assert second is not None
        assert second.phase is RecoveryOperationPhase.SOURCE_STABILITY_BOUND
        rows = connection.execute(
            """
            SELECT run_sequence, from_phase, to_phase, previous_event_hash, event_hash
            FROM recovery_events
            WHERE run_id = ?
            ORDER BY run_sequence
            """,
            ("run-a",),
        ).fetchall()
        assert [row[:3] for row in rows] == [
            (0, None, "PLANNED"),
            (1, "PLANNED", "SOURCE_VALIDATED"),
            (2, "SOURCE_VALIDATED", "SOURCE_STABILITY_BOUND"),
        ]
        assert rows[1][3] == rows[0][4]
        assert rows[2][3] == rows[1][4]
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_persists_transition_metadata(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryOperationStore(connection)
        store.record_planned_operation(_operation(), process_instance_id="host-a")

        updated = store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.PLANNED,
            next_phase=RecoveryOperationPhase.SOURCE_VALIDATED,
            process_instance_id="host-a",
            payload={"phase": "source"},
            operation_metadata=RecoveryOperationMetadata(
                expected_source_fingerprint_json='{"byte_count":5,"content_hash":"'
                + ("a" * 64)
                + '"}',
                source_hash_evidence_kind="SHA256_CURRENT_SOURCE_FILE",
            ),
        )

        assert updated is not None
        assert updated.expected_source_fingerprint_json == (
            '{"byte_count":5,"content_hash":"' + ("a" * 64) + '"}'
        )
        assert updated.source_hash_evidence_kind == "SHA256_CURRENT_SOURCE_FILE"
        loaded = store.load_operation(run_id="run-a", operation_id="operation-a")
        assert loaded == updated
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_rebinds_pre_commit_operation_lease(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        old_token = _register_resource_lease(connection)
        store = SqliteRecoveryOperationStore(connection)
        store.record_planned_operation(_operation(), process_instance_id="host-a")
        SqliteResourceLeaseStore(connection).release_resource_lease(lease_id="lease-a")
        new_token = _register_resource_lease(connection, lease_id="lease-b")

        updated = store.record_operation_lease_rebound(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.PLANNED,
            expected_lease_id="lease-a",
            expected_ownership_epoch=1,
            expected_fencing_token=old_token,
            lease_id="lease-b",
            owner_installation_id="owner-a",
            ownership_epoch=1,
            fencing_token=new_token,
            process_instance_id="host-b",
            payload={"reason": "restart"},
        )

        assert updated is not None
        assert updated.phase is RecoveryOperationPhase.PLANNED
        assert updated.lease_id == "lease-b"
        assert updated.fencing_token == new_token
        assert store.load_operation(run_id="run-a", operation_id="operation-a") == updated
        rows = connection.execute(
            """
            SELECT run_sequence, from_phase, to_phase, payload_json
            FROM recovery_events
            WHERE run_id = ?
            ORDER BY run_sequence
            """,
            ("run-a",),
        ).fetchall()
        assert rows == [
            (0, None, "PLANNED", "{}"),
            (1, "PLANNED", "PLANNED", '{"reason":"restart"}'),
        ]
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_records_commit_intent_with_matching_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())
        store = SqliteRecoveryOperationStore(connection)
        _record_staging_verified_operation(store)

        updated = store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.STAGING_VERIFIED,
            next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            process_instance_id="host-a",
            intent_segment_id="segment-a",
            intent_ordinal=0,
        )

        assert updated is not None
        assert updated.phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED
        assert updated.intent_segment_id == "segment-a"
        assert updated.intent_ordinal == 0
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_rejects_commit_intent_lease_rebind(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())
        store = SqliteRecoveryOperationStore(connection)
        _record_staging_verified_operation(store)
        operation = store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.STAGING_VERIFIED,
            next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            process_instance_id="host-a",
            intent_segment_id="segment-a",
            intent_ordinal=0,
        )
        assert operation is not None

        with pytest.raises(SqliteRecoveryOperationStoreError, match="LEASE_REBIND_PHASE_UNSUPPORTED"):
            store.record_operation_lease_rebound(
                run_id="run-a",
                operation_id="operation-a",
                expected_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
                expected_lease_id="lease-a",
                expected_ownership_epoch=1,
                expected_fencing_token=1,
                lease_id="lease-b",
                owner_installation_id="owner-a",
                ownership_epoch=1,
                fencing_token=2,
                process_instance_id="host-b",
            )
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_rejects_commit_intent_for_mismatched_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        _register_resource_lease(connection, lease_id="lease-b", resource_key="endpoint:target-b")
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(
            _segment(
                segment_id="segment-b",
                target_endpoint_id="target-b",
                target_endpoint_revision_id="target-rev-b",
                lease_id="lease-b",
                relative_path="installations/owner-a/recovery/run-a/segment-target-b.intent.jsonl",
            )
        )
        store = SqliteRecoveryOperationStore(connection)
        _record_staging_verified_operation(store)

        with pytest.raises(SqliteRecoveryOperationStoreError, match="INTENT_SEGMENT_MISMATCH"):
            store.record_operation_phase_transition(
                run_id="run-a",
                operation_id="operation-a",
                expected_phase=RecoveryOperationPhase.STAGING_VERIFIED,
                next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
                process_instance_id="host-a",
                intent_segment_id="segment-b",
                intent_ordinal=0,
            )

        operation = store.load_operation(run_id="run-a", operation_id="operation-a")
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.STAGING_VERIFIED
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_rejects_duplicate_intent_ordinal(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())
        store = SqliteRecoveryOperationStore(connection)
        _record_staging_verified_operation(store, operation_id="operation-a")
        _record_staging_verified_operation(
            store,
            operation_id="operation-b",
            final_relative_path="Photos/2026/other.jpg",
        )
        store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.STAGING_VERIFIED,
            next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            process_instance_id="host-a",
            intent_segment_id="segment-a",
            intent_ordinal=0,
        )

        with pytest.raises(SqliteRecoveryOperationStoreError, match="PERSISTENCE_CONFLICT"):
            store.record_operation_phase_transition(
                run_id="run-a",
                operation_id="operation-b",
                expected_phase=RecoveryOperationPhase.STAGING_VERIFIED,
                next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
                process_instance_id="host-a",
                intent_segment_id="segment-a",
                intent_ordinal=0,
            )

        operation = store.load_operation(run_id="run-a", operation_id="operation-b")
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.STAGING_VERIFIED
    finally:
        connection.close()


def test_sqlite_recovery_operation_store_returns_none_on_phase_conflict(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryOperationStore(connection)
        store.record_planned_operation(_operation(), process_instance_id="host-a")

        result = store.record_operation_phase_transition(
            run_id="run-a",
            operation_id="operation-a",
            expected_phase=RecoveryOperationPhase.SOURCE_VALIDATED,
            next_phase=RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
            process_instance_id="host-a",
        )

        assert result is None
        assert connection.in_transaction is False
        assert _event_count(connection) == 1
    finally:
        connection.close()


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection


def _register_resource_lease(
    connection: sqlite3.Connection,
    *,
    lease_id: str = "lease-a",
    resource_key: str = "endpoint:target-a",
) -> int:
    endpoint_id = resource_key.removeprefix("endpoint:")
    return SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
        lease_id=lease_id,
        resource_key=resource_key,
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id=endpoint_id,
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    )


def _record_staging_verified_operation(
    store: SqliteRecoveryOperationStore,
    *,
    operation_id: str = "operation-a",
    final_relative_path: str = "Photos/2026/image.jpg",
) -> RecoveryOperation:
    operation = store.record_planned_operation(
        _operation(operation_id=operation_id, final_relative_path=final_relative_path),
        process_instance_id="host-a",
    )
    for next_phase in (
        RecoveryOperationPhase.SOURCE_VALIDATED,
        RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
        RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
        RecoveryOperationPhase.STAGING_ALLOCATED,
        RecoveryOperationPhase.TRANSFERRED,
        RecoveryOperationPhase.STAGING_DURABLE,
        RecoveryOperationPhase.STAGING_VERIFIED,
    ):
        updated = store.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=next_phase,
            process_instance_id="host-a",
        )
        assert updated is not None
        operation = updated
    return operation


def _operation(**overrides: object) -> RecoveryOperation:
    values: dict[str, object] = {
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "operation_id": "operation-a",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "owner_installation_id": "owner-a",
        "ownership_epoch": 1,
        "lease_id": "lease-a",
        "lease_resource_key": "endpoint:target-a",
        "fencing_token": 1,
        "final_relative_path": "Photos/2026/image.jpg",
        "source_relative_path": "Photos/2026/image.jpg",
        "source_endpoint_id": "source-a",
        "source_endpoint_revision_id": "source-rev-a",
        "target_precondition_kind": RecoveryTargetPreconditionKind.ABSENT,
    }
    values.update(overrides)
    return planned_recovery_operation(**values)


def _segment(**overrides: object):
    values: dict[str, object] = {
        "segment_id": "segment-a",
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "owner_installation_id": "owner-a",
        "ownership_epoch": 1,
        "lease_id": "lease-a",
        "fencing_token": 1,
        "segment_sequence": 0,
        "relative_path": "installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        "schema_version": 1,
        "operation_count": 2,
        "byte_count": 256,
        "segment_hash": "a" * 64,
        "previous_segment_hash": None,
    }
    values.update(overrides)
    return durable_recovery_intent_segment(**values)


def _event_count(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT count(*) FROM recovery_events").fetchone()
    assert row is not None
    return int(row[0])
