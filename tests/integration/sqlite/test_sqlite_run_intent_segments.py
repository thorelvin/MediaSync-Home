from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, recovery_migration_plan
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_intent_segments import (
    publish_run_target_recovery_intent_segment,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_sqlite_run_intent_segment_publisher_records_segment_and_binds_operations(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        recovery_operations = SqliteRecoveryOperationStore(connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        _record_staging_verified_operation(
            recovery_operations,
            operation_id="op-a",
            final_relative_path="Pictures/A.jpg",
        )
        _record_staging_verified_operation(
            recovery_operations,
            operation_id="op-b",
            final_relative_path="Pictures/B.jpg",
        )

        selected = recovery_operations.list_operations_for_run_target_in_phase(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            phase=RecoveryOperationPhase.STAGING_VERIFIED,
            limit=10,
        )
        outcome = publish_run_target_recovery_intent_segment(
            permit=_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )

        segment = intent_segments.load_intent_segment("run-a-target-0000-intent-000000")
        op_a = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        op_b = recovery_operations.load_operation(run_id="run-a", operation_id="op-b")
        assert [operation.operation_id for operation in selected] == ["op-a", "op-b"]
        assert outcome.published is True
        assert outcome.operations_bound == 2
        assert outcome.validation_codes == ()
        assert segment is not None
        assert segment == outcome.segment
        assert segment.operation_count == 2
        assert segment.byte_count == 256
        assert len(segment.segment_hash) == 64
        assert op_a is not None
        assert op_a.phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED
        assert op_a.intent_segment_id == segment.segment_id
        assert op_a.intent_ordinal == 0
        assert op_b is not None
        assert op_b.phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED
        assert op_b.intent_segment_id == segment.segment_id
        assert op_b.intent_ordinal == 1
        assert _event_phases(connection)[-2:] == [
            "COMMIT_INTENT_RECORDED",
            "COMMIT_INTENT_RECORDED",
        ]
    finally:
        connection.close()


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


def _record_staging_verified_operation(
    store: SqliteRecoveryOperationStore,
    *,
    operation_id: str,
    final_relative_path: str,
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


def _operation(*, operation_id: str, final_relative_path: str) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id=operation_id,
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id="lease-a",
            lease_resource_key="endpoint:target-a",
            fencing_token=1,
            final_relative_path=final_relative_path,
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        ),
        staging_object_id=operation_id,
        expected_staging_fingerprint_json='{"byte_count":128}',
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
