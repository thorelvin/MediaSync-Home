from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.catalog_handoffs import (
    SqliteCatalogHandoffStoreError,
    SqliteFinalFileCatalogHandoffStore,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.application.catalog_handoff import (
    CatalogHandoffReconciliationStatus,
    FinalFileCatalogHandoff,
    reconcile_catalog_handoffs_after_startup,
    record_catalog_handoff_after_final_verification,
)
from mediasync_home.application.recovery_intents import durable_recovery_intent_segment
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


def test_sqlite_final_file_catalog_handoff_store_is_idempotent(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        store = SqliteFinalFileCatalogHandoffStore(connection)
        handoff = _handoff()

        first = store.record_final_file_handoff(handoff)
        second = store.record_final_file_handoff(handoff)

        assert second == first
        assert _row_count(connection, "final_file_catalog_handoffs") == 1
        with pytest.raises(SqliteCatalogHandoffStoreError, match="IDEMPOTENCY_CONFLICT"):
            store.record_final_file_handoff(replace(handoff, content_hash="b" * 64))
    finally:
        connection.close()


def test_sqlite_catalog_handoff_records_catalog_then_recovery_phase(
    tmp_path: Path,
) -> None:
    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        catalog_store = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        _register_resource_lease(recovery_connection)
        SqliteRecoveryIntentSegmentStore(recovery_connection).publish_intent_segment(_segment())
        recovery_store = SqliteRecoveryOperationStore(recovery_connection)
        _record_final_verified_operation(recovery_store)

        outcome = record_catalog_handoff_after_final_verification(
            run_id="run-a",
            operation_id="operation-a",
            content_hash="a" * 64,
            recovery_operations=recovery_store,
            catalog_handoffs=catalog_store,
            process_instance_id="host-a",
        )
        event_count = _row_count(recovery_connection, "recovery_events")
        replay = record_catalog_handoff_after_final_verification(
            run_id="run-a",
            operation_id="operation-a",
            content_hash="a" * 64,
            recovery_operations=recovery_store,
            catalog_handoffs=catalog_store,
            process_instance_id="host-a",
        )

        operation = recovery_store.load_operation(run_id="run-a", operation_id="operation-a")
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        assert operation.catalog_handoff_id == "final-file:run-a:operation-a"
        assert outcome.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert replay.handoff == outcome.handoff
        assert catalog_store.load_final_file_handoff("final-file:run-a:operation-a") == outcome.handoff
        assert _row_count(catalog_connection, "final_file_catalog_handoffs") == 1
        assert _event_phases(recovery_connection)[-1] == "CATALOG_RECORDED"
        assert _last_event_payload(recovery_connection)["catalog_handoff_id"] == (
            "final-file:run-a:operation-a"
        )
        assert _row_count(recovery_connection, "recovery_events") == event_count
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_sqlite_startup_reconciliation_completes_partial_catalog_handoff(
    tmp_path: Path,
) -> None:
    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        catalog_store = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        _register_resource_lease(recovery_connection)
        SqliteRecoveryIntentSegmentStore(recovery_connection).publish_intent_segment(_segment())
        recovery_store = SqliteRecoveryOperationStore(recovery_connection)
        _record_final_verified_operation(recovery_store)
        catalog_store.record_final_file_handoff(_handoff())
        event_count = _row_count(recovery_connection, "recovery_events")

        report = reconcile_catalog_handoffs_after_startup(
            recovery_operations=recovery_store,
            catalog_handoffs=catalog_store,
            process_instance_id="host-a",
        )
        replay = reconcile_catalog_handoffs_after_startup(
            recovery_operations=recovery_store,
            catalog_handoffs=catalog_store,
            process_instance_id="host-a",
        )

        operation = recovery_store.load_operation(run_id="run-a", operation_id="operation-a")
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        assert operation.catalog_handoff_id == "final-file:run-a:operation-a"
        assert report.scanned == 1
        assert report.recovered[0].status is CatalogHandoffReconciliationStatus.RECOVERED
        assert report.pending == ()
        assert report.ambiguous == ()
        assert replay.scanned == 0
        assert _row_count(catalog_connection, "final_file_catalog_handoffs") == 1
        assert _event_phases(recovery_connection)[-1] == "CATALOG_RECORDED"
        assert _last_event_payload(recovery_connection)["catalog_handoff_id"] == (
            "final-file:run-a:operation-a"
        )
        assert _row_count(recovery_connection, "recovery_events") == event_count + 1
    finally:
        catalog_connection.close()
        recovery_connection.close()


def _prepared_catalog_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
    return connection


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection


def _register_resource_lease(connection: sqlite3.Connection) -> None:
    assert SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
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


def _record_final_verified_operation(store: SqliteRecoveryOperationStore) -> RecoveryOperation:
    operation = store.record_planned_operation(_operation(), process_instance_id="host-a")
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
    updated = store.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        process_instance_id="host-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
    )
    assert updated is not None
    operation = updated
    for next_phase in (
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
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


def _operation() -> RecoveryOperation:
    return planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="operation-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Photos/image.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
    )


def _segment():
    return durable_recovery_intent_segment(
        segment_id="segment-a",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        fencing_token=1,
        segment_sequence=0,
        relative_path="installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        schema_version=1,
        operation_count=1,
        byte_count=256,
        segment_hash="a" * 64,
    )


def _handoff() -> FinalFileCatalogHandoff:
    return FinalFileCatalogHandoff(
        handoff_id="final-file:run-a:operation-a",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="operation-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        final_relative_path="Photos/image.jpg",
        content_hash="a" * 64,
        lease_id="lease-a",
        fencing_token=1,
    )


def _event_phases(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT to_phase
            FROM recovery_events
            WHERE run_id = ?
            ORDER BY run_sequence
            """,
            ("run-a",),
        ).fetchall()
    ]


def _last_event_payload(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT payload_json
        FROM recovery_events
        WHERE run_id = ?
        ORDER BY run_sequence DESC
        LIMIT 1
        """,
        ("run-a",),
    ).fetchone()
    assert row is not None
    payload = json.loads(str(row[0]))
    assert isinstance(payload, dict)
    return payload


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
