from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version
from tests.support.source_preconditions import source_precondition_json

from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
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
from mediasync_home.adapters.sqlite.operation_audit import SqliteOperationAuditStore
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore
from mediasync_home.application.command_receipts import CommandReceipt
from mediasync_home.application.operation_audit import (
    OperationAttemptAudit,
    OperationAttemptState,
    OperationOutcomeAudit,
    OperationOutcomeState,
    reconcile_next_run_target_operation_audit,
)
from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.run_executor import (
    HeldRunTargetLeaseRegistry,
    execute_one_run_target_execution_start_step,
    execute_one_run_target_preflight_step,
)
from mediasync_home.application.run_operation_planning import plan_run_target_recovery_operations
from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    RunIdFactory,
    RunIds,
    RunState,
    RunTargetState,
    parse_start_run_command,
    start_run_from_sealed_plan,
)
from mediasync_home.application.recovery_operations import RecoveryOperationPhase
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


class FixedRunIdFactory(RunIdFactory):
    def new_run_ids(self) -> RunIds:
        return RunIds(run_id="run-a", logical_run_group_id="run-group-a")


class FixedLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, lease: "FixedLiveLease") -> None:
        self.lease = lease

    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt:
        return EndpointLeaseAttempt(
            acquired=True,
            lease=self.lease,
            validation_codes=(),
            next_action="Lease acquired.",
        )


class FixedLiveLease:
    lease_id = "lease-a"
    owner_installation_id = "owner-a"
    ownership_epoch = 1
    fencing_token = 1

    def __init__(self) -> None:
        self.released = False

    def issue_mutation_permit(self) -> MutationPermit:
        return _issue_mutation_permit(
            lease_id=self.lease_id,
            resource_key="endpoint:target-a",
            owner_installation_id=self.owner_installation_id,
            ownership_epoch=self.ownership_epoch,
            fencing_token=self.fencing_token,
            run_id="run-a",
            run_target_id="run-a-target-0000",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
        )

    def release(self) -> None:
        self.released = True


def test_sqlite_run_operation_planning_records_only_bound_target_operations(
    tmp_path: Path,
) -> None:
    catalog_database = tmp_path / "catalog.sqlite"
    recovery_database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(catalog_database) as catalog_connection:
        recovery_connection = sqlite3.connect(recovery_database)
        try:
            _prepare_catalog(catalog_connection, catalog_database)
            _prepare_recovery(recovery_connection, recovery_database)
            _insert_plan_parent_rows(catalog_connection)
            _insert_receipt(catalog_connection)
            _register_resource_lease(recovery_connection)
            plans = SqlitePlanStore(catalog_connection)
            runs = SqliteRunStore(catalog_connection)
            recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
            lease = FixedLiveLease()
            registry = HeldRunTargetLeaseRegistry()
            plan = _sealed_plan()
            plans.save_sealed_plan(plan)
            start_run_from_sealed_plan(
                command=parse_start_run_command(
                    request_id="request-a",
                    idempotency_key="idempotency-a",
                    payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
                ),
                plans=plans,
                runs=runs,
                id_factory=FixedRunIdFactory(),
            )
            preflight = execute_one_run_target_preflight_step(
                runs=runs,
                leases=FixedLeaseAuthority(lease),
            )
            assert preflight.lease is lease
            registry.retain_run_target_lease(
                run_id="run-a",
                run_target_id="run-a-target-0000",
                lease=lease,
            )
            execution = execute_one_run_target_execution_start_step(
                runs=runs,
                lease_registry=registry,
            )
            assert execution.mutation_permit is not None

            outcome = plan_run_target_recovery_operations(
                permit=execution.mutation_permit,
                runs=runs,
                plans=plans,
                recovery_operations=recovery_operations,
                process_instance_id="host-a",
            )

            loaded_run = runs.load_started_run("run-a")
            operation = recovery_operations.load_operation(
                run_id="run-a",
                operation_id="op-copy",
            )
            assert outcome.planned is True
            assert outcome.operations_planned == 1
            assert outcome.validation_codes == ()
            assert loaded_run is not None
            assert loaded_run.state is RunState.EXECUTING
            assert len(loaded_run.targets) == 2
            assert loaded_run.targets[0].state is RunTargetState.EXECUTING
            assert loaded_run.targets[1].state is RunTargetState.PENDING
            assert operation is not None
            assert operation == outcome.operations[0]
            assert operation.phase is RecoveryOperationPhase.PLANNED
            assert operation.lease_id == "lease-a"
            assert operation.fencing_token == 1
            assert operation.final_relative_path == "Pictures/A.jpg"
            assert operation.source_endpoint_id == "source-a"
            assert operation.source_endpoint_revision_id == "source-rev-a"
            assert operation.source_relative_path == "Pictures/A.jpg"
            assert recovery_operations.load_operation(
                run_id="run-a",
                operation_id="op-copy-b",
            ) is None
            assert _row_count(recovery_connection, "recovery_events") == 1

            operation_audits = SqliteOperationAuditStore(catalog_connection)
            audit_outcome = reconcile_next_run_target_operation_audit(
                run_id="run-a",
                run_target_id="run-a-target-0000",
                recovery_operations=recovery_operations,
                operation_audits=operation_audits,
                max_operations=2,
            )
            assert audit_outcome.changed is True
            assert _row_count(catalog_connection, "run_attempts") == 1
            run_attempt_id = str(
                catalog_connection.execute(
                    "SELECT id FROM run_attempts WHERE run_id = 'run-a'"
                ).fetchone()[0]
            )
            write = operation_audits.reconcile_operation_audit(
                run_attempts=(),
                operation_attempts=(
                    OperationAttemptAudit(
                        id="attempt-a",
                        run_attempt_id=run_attempt_id,
                        run_id="run-a",
                        run_target_id="run-a-target-0000",
                        operation_id="op-copy",
                        attempt_number=1,
                        state=OperationAttemptState.SUCCEEDED,
                        process_instance_id="host-a",
                        finished_utc="2026-07-31T10:00:00.000Z",
                        batch_id="staging-a",
                        lease_id="lease-a",
                        ownership_epoch=1,
                        fencing_token=1,
                        source_guard_kind="FILE_ID",
                        source_guard_evidence_hash="b" * 64,
                        transfer_state="TRANSFERRED",
                        assurance_level="PRIMARY_STREAM_HASH_VERIFIED",
                        durability_level="LOCAL_FILE_FLUSH_CONFIRMED",
                        bytes_transferred=128,
                        verification_json='{"verified":true}',
                        error_code=None,
                    ),
                ),
                operation_outcome=OperationOutcomeAudit(
                    run_id="run-a",
                    run_target_id="run-a-target-0000",
                    operation_id="op-copy",
                    final_state=OperationOutcomeState.SUCCEEDED,
                    bytes_transferred=128,
                    transfer_state="TRANSFERRED",
                    assurance_level="PRIMARY_STREAM_HASH_VERIFIED",
                    hash_evidence_kind="CURRENT_READ_HASH",
                    durability_level="WRITE_THROUGH_REQUEST_CONFIRMED",
                    verification_json='{"verified":true}',
                    error_code=None,
                    completed_utc="2026-07-31T10:00:01.000Z",
                ),
            )
            assert write.operation_attempts_inserted == 1
            assert write.operation_outcome_inserted is True
            assert _row_count(catalog_connection, "operation_attempts") == 1
            assert _row_count(catalog_connection, "operation_outcomes") == 1
            with pytest.raises(sqlite3.IntegrityError, match="OPERATION_ATTEMPT_IMMUTABLE"):
                catalog_connection.execute(
                    "UPDATE operation_attempts SET state = 'FAILED' WHERE id = 'attempt-a'"
                )
        finally:
            recovery_connection.close()


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _prepare_recovery(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())


def _insert_plan_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
    insert_default_filter_set_version(
        connection,
        job_id="job-a",
        filter_set_id="filter-a",
    )
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
        """
    )
    connection.execute("INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'job-rev-a')")
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-a')")
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-b')")
    connection.execute("INSERT INTO endpoints (id) VALUES ('source-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('source-a', 'source-rev-a', 'Source', 'file:///C:/Source')
        """
    )
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('target-a', 'target-rev-a', 'USB', 'file:///E:/Backup')
        """
    )
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('target-b', 'target-rev-b', 'USB 2', 'file:///F:/Backup')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'source-a', 'source-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'target-b', 'target-rev-b')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('source-snapshot-a', 'analysis-a', 'source-a', 'source-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('target-snapshot-a', 'analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('target-snapshot-b', 'analysis-a', 'target-b', 'target-rev-b')
        """
    )
    connection.commit()


def _insert_receipt(connection: sqlite3.Connection) -> None:
    SqliteCommandReceiptStore(connection).record_received(
        CommandReceipt(
            request_id="request-a",
            client_instance_id="client-a",
            principal_fingerprint="principal-a",
            idempotency_key="idempotency-a",
            command_name="START_RUN",
            payload_hash="a" * 64,
            protocol_version=1,
            schema_version=1,
        )
    )


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


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _source_endpoint(),
            _target_endpoint(),
            replace(
                _target_endpoint(),
                endpoint_id="target-b",
                endpoint_revision_id="target-rev-b",
                snapshot_id="target-snapshot-b",
                target_ordinal=1,
            ),
        ),
        operations=(
            PlanOperation(
                operation_id="op-copy",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_endpoint_id="target-a",
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_json(),
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-copy-b",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:target-b:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_endpoint_id="target-b",
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_json(),
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _source_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="source-a",
        endpoint_revision_id="source-rev-a",
        snapshot_id="source-snapshot-a",
        role=PlanEndpointRole.SOURCE,
        target_ordinal=None,
        capabilities_hash="capabilities-source-a",
        root_case_context_hash="case-source-a",
        endpoint_generation=1,
        control_schema_version=1,
        planned_operations=0,
        planned_bytes=0,
    )


def _target_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        snapshot_id="target-snapshot-a",
        role=PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        endpoint_generation=1,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
