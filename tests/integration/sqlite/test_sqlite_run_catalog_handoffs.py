from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.adapters.sqlite.catalog_handoffs import SqliteFinalFileCatalogHandoffStore
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
from mediasync_home.application.recovery_intents import durable_recovery_intent_segment
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_catalog_handoffs import record_next_run_target_catalog_handoff
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_sqlite_run_catalog_handoff_step_records_catalog_and_recovery(
    tmp_path: Path,
) -> None:
    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        _register_resource_lease(recovery_connection)
        SqliteRecoveryIntentSegmentStore(recovery_connection).publish_intent_segment(_segment())
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        _record_final_verified_operation(recovery_operations)

        outcome = record_next_run_target_catalog_handoff(
            permit=_permit(),
            recovery_operations=recovery_operations,
            catalog_handoffs=catalog_handoffs,
            process_instance_id="host-a",
        )

        operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
        assert outcome.idle is False
        assert outcome.recorded is True
        assert outcome.validation_codes == ()
        assert outcome.handoff_id == "final-file:run-a:op-a"
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        assert operation.catalog_handoff_id == "final-file:run-a:op-a"
        assert handoff is not None
        assert handoff.content_hash == "a" * 64
        assert _event_phases(recovery_connection)[-1] == "CATALOG_RECORDED"
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
    return replace(
        planned_recovery_operation(
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
        ),
        staging_object_id="op-a",
        expected_final_fingerprint_json=json.dumps(
            {"byte_count": 5, "content_hash": "a" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ),
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
        byte_count=5,
        segment_hash="b" * 64,
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )


def _event_phases(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT to_phase
            FROM recovery_events
            WHERE run_id = 'run-a'
            ORDER BY run_sequence
            """
        ).fetchall()
    ]
