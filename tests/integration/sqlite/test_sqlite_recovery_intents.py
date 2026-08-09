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
from mediasync_home.adapters.sqlite.recovery_intents import (
    SqliteRecoveryIntentSegmentStore,
    SqliteRecoveryIntentSegmentStoreError,
)
from mediasync_home.adapters.sqlite.recovery_operations import (
    SqliteRecoveryOperationStore,
)
from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentState,
    durable_recovery_intent_segment,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


def test_sqlite_recovery_intent_segment_store_publishes_and_loads_durable_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)
        segment = _segment()

        published = store.publish_intent_segment(segment)

        assert published == segment
        assert store.load_intent_segment("segment-a") == segment
        row = connection.execute(
            """
            SELECT lease_id, fencing_token, durability_state, state
            FROM recovery_intent_segments
            WHERE id = ?
            """,
            ("segment-a",),
        ).fetchone()
        assert row == ("lease-a", 1, "DURABLE", "DURABLE")
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_is_idempotent_for_same_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)
        segment = _segment()

        assert store.publish_intent_segment(segment) == segment
        assert store.publish_intent_segment(segment) == segment

        conflicting = _segment(segment_hash="b" * 64)
        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="IDEMPOTENCY_CONFLICT"):
            store.publish_intent_segment(conflicting)
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_requires_active_matching_lease(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        lease_store = _register_resource_lease(connection)
        lease_store.release_resource_lease(lease_id="lease-a")
        store = SqliteRecoveryIntentSegmentStore(connection)

        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="LEASE_MISMATCH"):
            store.publish_intent_segment(_segment())

        assert connection.in_transaction is False
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_imports_target_first_evidence_after_lease_release(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        lease_store = _register_resource_lease(connection)
        lease_store.release_resource_lease(lease_id="lease-a")
        store = SqliteRecoveryIntentSegmentStore(connection)

        imported = store.import_intent_segment(_segment())

        assert imported == _segment()
        assert store.list_unresolved_intent_segments(limit=10) == (_segment(),)
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_import_requires_historical_lease_binding(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)

        with pytest.raises(
            SqliteRecoveryIntentSegmentStoreError,
            match="HISTORICAL_LEASE_MISMATCH",
        ):
            store.import_intent_segment(_segment(run_target_id="other-target"))
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_chains_segments_by_previous_hash(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)

        first = _segment()
        second = _segment(
            segment_id="segment-b",
            segment_sequence=1,
            relative_path="installations/owner-a/recovery/run-a/segment-000001.intent.jsonl",
            segment_hash="b" * 64,
            previous_segment_hash=first.segment_hash,
        )
        broken = _segment(
            segment_id="segment-c",
            segment_sequence=2,
            relative_path="installations/owner-a/recovery/run-a/segment-000002.intent.jsonl",
            segment_hash="c" * 64,
            previous_segment_hash="0" * 64,
        )

        assert store.publish_intent_segment(first) == first
        assert store.publish_intent_segment(second) == second
        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="CHAIN_MISMATCH"):
            store.publish_intent_segment(broken)
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_loads_latest_segment_for_run_target(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)
        first = _segment()
        second = _segment(
            segment_id="segment-b",
            segment_sequence=1,
            relative_path="installations/owner-a/recovery/run-a/segment-000001.intent.jsonl",
            segment_hash="b" * 64,
            previous_segment_hash=first.segment_hash,
        )
        store.publish_intent_segment(first)
        store.publish_intent_segment(second)

        latest = store.load_latest_intent_segment_for_run_target(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        )

        assert latest == second
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_rejects_duplicate_sequence(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)

        store.publish_intent_segment(_segment())
        duplicate_sequence = _segment(
            segment_id="segment-b",
            relative_path="installations/owner-a/recovery/run-a/segment-duplicate.intent.jsonl",
            segment_hash="b" * 64,
        )

        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="PERSISTENCE_CONFLICT"):
            store.publish_intent_segment(duplicate_sequence)
    finally:
        connection.close()


def test_recovery_intent_segment_migration_blocks_durable_field_mutation(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())

        with pytest.raises(sqlite3.IntegrityError, match="INTENT_SEGMENT_IMMUTABLE"):
            connection.execute(
                """
                UPDATE recovery_intent_segments
                SET segment_hash = ?
                WHERE id = ?
                """,
                ("b" * 64, "segment-a"),
            )
    finally:
        connection.close()


def test_sqlite_recovery_intent_lifecycle_waits_for_terminal_referenced_operations(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        intents = SqliteRecoveryIntentSegmentStore(connection)
        operations = SqliteRecoveryOperationStore(connection)
        segment = _segment(operation_count=1)
        intents.publish_intent_segment(segment)
        operations.record_planned_operation(
            _operation(),
            process_instance_id="engine-a",
        )
        connection.execute(
            """
            UPDATE recovery_operations
            SET intent_segment_id = ?, intent_ordinal = ?
            WHERE run_id = ? AND operation_id = ?
            """,
            (segment.segment_id, 0, "run-a", "op-a"),
        )
        connection.commit()

        assert intents.intent_segment_reconciliation_ready(
            segment_id=segment.segment_id
        ) is False

        connection.execute(
            """
            UPDATE recovery_operations
            SET phase = 'CANCELLED'
            WHERE run_id = ? AND operation_id = ?
            """,
            ("run-a", "op-a"),
        )
        connection.commit()
        assert intents.intent_segment_reconciliation_ready(
            segment_id=segment.segment_id
        ) is True

        reconciled = intents.transition_intent_segment_state(
            segment_id=segment.segment_id,
            expected_state=RecoveryIntentSegmentState.DURABLE,
            next_state=RecoveryIntentSegmentState.RECONCILED,
        )
        assert reconciled is not None
        assert reconciled.state is RecoveryIntentSegmentState.RECONCILED
        assert intents.list_unresolved_intent_segments(limit=10) == (reconciled,)
        assert intents.transition_intent_segment_state(
            segment_id=segment.segment_id,
            expected_state=RecoveryIntentSegmentState.DURABLE,
            next_state=RecoveryIntentSegmentState.RECONCILED,
        ) is None

        cleanup_eligible = intents.transition_intent_segment_state(
            segment_id=segment.segment_id,
            expected_state=RecoveryIntentSegmentState.RECONCILED,
            next_state=RecoveryIntentSegmentState.CLEANUP_ELIGIBLE,
        )
        assert cleanup_eligible is not None
        assert intents.list_unresolved_intent_segments(limit=10) == ()
        cleaned = intents.transition_intent_segment_state(
            segment_id=segment.segment_id,
            expected_state=RecoveryIntentSegmentState.CLEANUP_ELIGIBLE,
            next_state=RecoveryIntentSegmentState.CLEANED,
        )
        assert cleaned is not None
        assert cleaned.state is RecoveryIntentSegmentState.CLEANED
        assert intents.load_next_intent_segment_lifecycle_candidate(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        ) is None
    finally:
        connection.close()


def test_sqlite_recovery_intent_lifecycle_allows_superseded_unbound_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)
        first = _segment(operation_count=1)
        second = _segment(
            segment_id="segment-b",
            segment_sequence=1,
            relative_path=(
                "installations/owner-a/recovery/run-a/segment-000001.intent.jsonl"
            ),
            operation_count=1,
            segment_hash="b" * 64,
            previous_segment_hash=first.segment_hash,
        )
        store.publish_intent_segment(first)
        store.publish_intent_segment(second)

        assert store.intent_segment_reconciliation_ready(
            segment_id=first.segment_id
        ) is True
        assert store.intent_segment_reconciliation_ready(
            segment_id=second.segment_id
        ) is False
    finally:
        connection.close()


def test_sqlite_missing_intent_finalization_requires_bound_terminal_operations(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        intents = SqliteRecoveryIntentSegmentStore(connection)
        operations = SqliteRecoveryOperationStore(connection)
        segment = _segment(operation_count=1)
        intents.publish_intent_segment(segment)
        operations.record_planned_operation(_operation(), process_instance_id="engine-a")
        connection.execute(
            """
            UPDATE recovery_operations
            SET intent_segment_id = ?, intent_ordinal = 0
            WHERE run_id = 'run-a' AND operation_id = 'op-a'
            """,
            (segment.segment_id,),
        )
        connection.commit()

        assert not intents.finalize_missing_terminal_intent_segment(segment.segment_id)

        connection.execute(
            """
            UPDATE recovery_operations
            SET phase = 'CANCELLED'
            WHERE run_id = 'run-a' AND operation_id = 'op-a'
            """
        )
        connection.commit()

        assert intents.finalize_missing_terminal_intent_segment(segment.segment_id)
        finalized = intents.load_intent_segment(segment.segment_id)
        assert finalized is not None
        assert finalized.state is RecoveryIntentSegmentState.CLEANED
        assert not intents.finalize_missing_terminal_intent_segment(segment.segment_id)
    finally:
        connection.close()


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection


def _register_resource_lease(connection: sqlite3.Connection) -> SqliteResourceLeaseStore:
    store = SqliteResourceLeaseStore(connection)
    assert store.register_acquired_resource_lease(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    ) == 1
    return store


def _segment(**overrides: object) -> RecoveryIntentSegment:
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


def _operation() -> RecoveryOperation:
    return planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="op-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Pictures/A.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        planned_bytes=128,
    )
