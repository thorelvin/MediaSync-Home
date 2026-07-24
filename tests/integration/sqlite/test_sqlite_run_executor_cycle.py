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
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore
from mediasync_home.application.command_receipts import CommandReceipt
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
from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    RelativePath,
    VerifiedStagingArtifact,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_executor import HeldRunTargetLeaseRegistry, RunExecutorPumpStopReason
from mediasync_home.application.run_executor_cycle import (
    RunExecutorCycleAction,
    execute_bounded_run_executor_cycle,
)
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


def test_sqlite_run_executor_cycle_advances_staged_operation_to_completed_run(
    tmp_path: Path,
) -> None:
    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        _insert_plan_parent_rows(catalog_connection)
        _insert_receipt(catalog_connection)
        plans = SqlitePlanStore(catalog_connection)
        runs = SqliteRunStore(catalog_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        lease = FixedLiveLease()
        registry = HeldRunTargetLeaseRegistry()
        final_commit = _FakeFinalCommitPort()
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
        _mark_target_executing(runs=runs, lease=lease, registry=registry)
        _register_resource_lease(recovery_connection)
        _record_staging_verified_operation(recovery_operations)

        outcome = execute_bounded_run_executor_cycle(
            runs=runs,
            leases=FixedLeaseAuthority(lease),
            lease_registry=registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            final_commit_port=final_commit,
            process_instance_id="host-a",
            max_steps=5,
        )

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
        assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert outcome.steps_attempted == 5
        assert outcome.last_step is not None
        assert outcome.last_step.action is RunExecutorCycleAction.IDLE
        assert registry.retained_count == 0
        assert lease.released is True
        assert loaded_run is not None
        assert loaded_run.state is RunState.COMPLETED
        assert loaded_run.targets[0].state is RunTargetState.SUCCEEDED
        assert loaded_run.targets[0].completed_operations == 1
        assert loaded_run.targets[0].completed_bytes == 128
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        assert operation.intent_segment_id == "run-a-target-0000-intent-000000"
        assert operation.catalog_handoff_id == "final-file:run-a:op-a"
        assert handoff is not None
        assert handoff.content_hash == "a" * 64
        assert final_commit.calls == (
            VerifiedStagingArtifact(
                object_id="op-a",
                relative_path=RelativePath("Pictures/A.jpg"),
                content_hash="a" * 64,
            ),
        )
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


def _mark_target_executing(
    *,
    runs: SqliteRunStore,
    lease: FixedLiveLease,
    registry: HeldRunTargetLeaseRegistry,
) -> None:
    preflight = runs.begin_run_target_preflight(
        run_id="run-a",
        run_target_id="run-a-target-0000",
    )
    assert preflight is not None
    acquired = runs.record_run_target_lease_acquired(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease_id=lease.lease_id,
        owner_installation_id=lease.owner_installation_id,
        ownership_epoch=lease.ownership_epoch,
        fencing_token=lease.fencing_token,
    )
    assert acquired is not None
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=lease,
    )
    started = runs.record_run_target_execution_started(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease_id=lease.lease_id,
        owner_installation_id=lease.owner_installation_id,
        ownership_epoch=lease.ownership_epoch,
        fencing_token=lease.fencing_token,
    )
    assert started is not None


def _insert_plan_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
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
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('target-a', 'target-rev-a', 'USB', 'file:///E:/Backup')
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
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('target-snapshot-a', 'analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.commit()


def _insert_receipt(connection: sqlite3.Connection) -> None:
    receipt = CommandReceipt(
        request_id="request-a",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key="idempotency-a",
        command_name="START_RUN",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
    )
    connection.execute(
        """
        INSERT INTO command_receipts (
            idempotency_key,
            request_id,
            client_instance_id,
            principal_fingerprint,
            command_name,
            payload_hash,
            protocol_version,
            schema_version,
            state,
            expected_entity_revision,
            payload_hash_scope,
            payload_canonicalization_algorithm,
            payload_hash_algorithm,
            result_entity_type,
            result_entity_id,
            rejection_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt.idempotency_key,
            receipt.request_id,
            receipt.client_instance_id,
            receipt.principal_fingerprint,
            receipt.command_name,
            receipt.payload_hash,
            receipt.protocol_version,
            receipt.schema_version,
            receipt.state.value,
            receipt.expected_entity_revision,
            receipt.payload_hash_scope,
            receipt.payload_canonicalization_algorithm,
            receipt.payload_hash_algorithm,
            receipt.result_entity_type,
            receipt.result_entity_id,
            receipt.rejection_reason,
        ),
    )
    connection.commit()


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
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
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
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


def _record_staging_verified_operation(store: SqliteRecoveryOperationStore) -> RecoveryOperation:
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
        expected_staging_fingerprint_json=json.dumps(
            {"byte_count": 128, "content_hash": "a" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


class _FakeFinalCommitPort(FinalCommitPort):
    def __init__(self) -> None:
        self.calls: tuple[VerifiedStagingArtifact, ...] = ()

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        self.calls = (*self.calls, artifact)
        return CommitReceipt(
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path,
        )
