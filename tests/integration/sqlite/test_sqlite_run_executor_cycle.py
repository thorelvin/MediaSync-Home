from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from mediasync_home.adapters.final_commit import LocalResolvingFinalCommitAdapter
from mediasync_home.adapters.staging import LocalFileStagingTransferAdapter
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
from mediasync_home.adapters.sqlite.endpoint_roots import SqliteEndpointRootResolver
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
from mediasync_home.application.recovery_operations import (
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
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
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.composition.engine_host import EngineHostRuntime
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy
from mediasync_home.ipc.server import EngineHostIpcService


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

    def assert_mutation_permit_current(self, permit: MutationPermit) -> None:
        assert self.released is False
        assert permit.lease_id == self.lease_id
        assert permit.resource_key == "endpoint:target-a"
        assert permit.owner_installation_id == self.owner_installation_id
        assert permit.ownership_epoch == self.ownership_epoch
        assert permit.fencing_token == self.fencing_token
        assert permit.run_id == "run-a"
        assert permit.run_target_id == "run-a-target-0000"
        assert permit.endpoint_id == "target-a"
        assert permit.endpoint_revision_id == "target-rev-a"


def test_sqlite_run_executor_cycle_advances_staged_operation_to_completed_run(
    tmp_path: Path,
) -> None:
    payload = b"x" * 128
    content_hash = hashlib.sha256(payload).hexdigest()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures").mkdir(parents=True)
    (source_root / "Pictures" / "A.jpg").write_bytes(payload)
    _write_endpoint_marker(target_root)

    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        _insert_plan_parent_rows(
            catalog_connection,
            source_root=source_root,
            target_root=target_root,
        )
        _insert_receipt(catalog_connection)
        plans = SqlitePlanStore(catalog_connection)
        runs = SqliteRunStore(catalog_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        lease = FixedLiveLease()
        registry = HeldRunTargetLeaseRegistry()
        final_commit = LocalResolvingFinalCommitAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
            permit_validator=lease,
        )
        staging = LocalFileStagingTransferAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
        )
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
        _register_resource_lease(recovery_connection)

        runtime = EngineHostRuntime(
            service=_service(),
            run_executor_queue_store=runs,
            run_executor_lease_authority=FixedLeaseAuthority(lease),
            run_executor_lease_registry=registry,
            run_executor_plan_store=plans,
            run_executor_recovery_operation_store=recovery_operations,
            run_executor_recovery_intent_segment_store=intent_segments,
            run_executor_catalog_handoff_store=catalog_handoffs,
            run_executor_staging_transfer_port=staging,
            run_executor_final_commit_port=final_commit,
            run_executor_old_target_preservation_port=final_commit,
            run_executor_process_instance_id="host-a",
        )
        outcome = runtime.run_executor_cycle(max_steps=15)

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
        assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert outcome.steps_attempted == 15
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
        assert operation.staging_object_id == "op-a"
        assert operation.intent_segment_id == "run-a-target-0000-intent-000000"
        assert operation.catalog_handoff_id == "final-file:run-a:op-a"
        assert operation.expected_source_fingerprint_json is not None
        assert operation.expected_staging_fingerprint_json is not None
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == payload
        assert (staging_root / "op-a.payload").read_bytes() == payload
        assert handoff is not None
        assert handoff.content_hash == content_hash
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_sqlite_run_executor_cycle_replaces_existing_target_from_match_fingerprint_plan(
    tmp_path: Path,
) -> None:
    old_payload = b"old-image"
    new_payload = b"new-image"
    content_hash = hashlib.sha256(new_payload).hexdigest()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures").mkdir(parents=True)
    (source_root / "Pictures" / "A.jpg").write_bytes(new_payload)
    (target_root / "Pictures" / "A.jpg").write_bytes(old_payload)
    _write_endpoint_marker(target_root)

    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        _insert_plan_parent_rows(
            catalog_connection,
            source_root=source_root,
            target_root=target_root,
        )
        _insert_receipt(catalog_connection)
        plans = SqlitePlanStore(catalog_connection)
        runs = SqliteRunStore(catalog_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        lease = FixedLiveLease()
        registry = HeldRunTargetLeaseRegistry()
        final_commit = LocalResolvingFinalCommitAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
            permit_validator=lease,
        )
        staging = LocalFileStagingTransferAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
        )
        plan = _sealed_plan(
            planned_bytes=len(new_payload),
            reason_code="REPLACE_CHANGED",
            target_precondition_kind=TargetPreconditionKind.MATCH_FINGERPRINT,
        )
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
        _register_resource_lease(recovery_connection)

        outcome = execute_bounded_run_executor_cycle(
            runs=runs,
            leases=FixedLeaseAuthority(lease),
            lease_registry=registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            final_commit_port=final_commit,
            old_target_preservation_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=15,
        )

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
        version_payload = target_root / ".mediasync" / "objects" / "versions" / "op-a.payload"
        version_manifest = target_root / ".mediasync" / "objects" / "versions" / "op-a.manifest.json"
        assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert outcome.last_step is not None
        assert outcome.last_step.action is RunExecutorCycleAction.IDLE
        assert registry.retained_count == 0
        assert lease.released is True
        assert loaded_run is not None
        assert loaded_run.state is RunState.COMPLETED
        assert loaded_run.targets[0].state is RunTargetState.SUCCEEDED
        assert loaded_run.targets[0].completed_operations == 1
        assert loaded_run.targets[0].completed_bytes == len(new_payload)
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        assert operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT
        assert operation.version_object_id == "op-a"
        assert operation.expected_target_fingerprint_json == json.dumps(
            {
                "byte_count": len(old_payload),
                "content_hash": hashlib.sha256(old_payload).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == new_payload
        assert (staging_root / "op-a.payload").read_bytes() == new_payload
        assert version_payload.read_bytes() == old_payload
        assert json.loads(version_manifest.read_text(encoding="utf-8"))["object_role"] == "OLD_TARGET_VERSION"
        assert handoff is not None
        assert handoff.content_hash == content_hash
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_sqlite_run_executor_cycle_quarantines_empty_directory_before_file_commit(
    tmp_path: Path,
) -> None:
    payload = b"new-image"
    content_hash = hashlib.sha256(payload).hexdigest()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures" / "A.jpg").mkdir(parents=True)
    (source_root / "Pictures" / "A.jpg").write_bytes(payload)
    _write_endpoint_marker(target_root)

    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        _insert_plan_parent_rows(
            catalog_connection,
            source_root=source_root,
            target_root=target_root,
        )
        _insert_receipt(catalog_connection)
        plans = SqlitePlanStore(catalog_connection)
        runs = SqliteRunStore(catalog_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        lease = FixedLiveLease()
        registry = HeldRunTargetLeaseRegistry()
        final_commit = LocalResolvingFinalCommitAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
            permit_validator=lease,
        )
        staging = LocalFileStagingTransferAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
        )
        plan = _sealed_plan(
            planned_bytes=len(payload),
            reason_code="COPY_OVER_EMPTY_DIRECTORY",
            target_precondition_kind=TargetPreconditionKind.DIRECTORY_EMPTY,
        )
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
        _register_resource_lease(recovery_connection)

        outcome = execute_bounded_run_executor_cycle(
            runs=runs,
            leases=FixedLeaseAuthority(lease),
            lease_registry=registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            final_commit_port=final_commit,
            old_target_preservation_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=15,
        )

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
        quarantine_payload = target_root / ".mediasync" / "objects" / "quarantine" / "op-a.payload"
        quarantine_manifest = (
            target_root / ".mediasync" / "objects" / "quarantine" / "op-a.manifest.json"
        )
        assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert outcome.last_step is not None
        assert outcome.last_step.action is RunExecutorCycleAction.IDLE
        assert registry.retained_count == 0
        assert lease.released is True
        assert loaded_run is not None
        assert loaded_run.state is RunState.COMPLETED
        assert loaded_run.targets[0].state is RunTargetState.SUCCEEDED
        assert loaded_run.targets[0].completed_operations == 1
        assert loaded_run.targets[0].completed_bytes == len(payload)
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        assert operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY
        assert operation.quarantine_object_id == "op-a"
        assert operation.expected_target_fingerprint_json == json.dumps(
            {
                "entry_count": 0,
                "kind": "DIRECTORY_EMPTY",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert (target_root / "Pictures" / "A.jpg").is_file()
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == payload
        assert quarantine_payload.is_dir()
        assert json.loads(quarantine_manifest.read_text(encoding="utf-8"))["object_role"] == (
            "EMPTY_DIRECTORY_QUARANTINE"
        )
        assert handoff is not None
        assert handoff.content_hash == content_hash
    finally:
        catalog_connection.close()
        recovery_connection.close()


def _prepared_catalog_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
    return connection


def _service() -> EngineHostIpcService:
    return EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash="same-user",
            expected_session_id=42,
        ),
        status=startup_status(ProcessRole.ENGINE_HOST),
        installation_id="install-a",
    )


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


def _insert_plan_parent_rows(
    connection: sqlite3.Connection,
    *,
    source_root: Path,
    target_root: Path,
) -> None:
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
    connection.execute("INSERT INTO endpoints (id) VALUES ('source-a')")
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('source-a', 'source-rev-a', 'Source', ?)
        """,
        (source_root.as_uri(),),
    )
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('target-a', 'target-rev-a', 'USB', ?)
        """,
        (target_root.as_uri(),),
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


def _sealed_plan(
    *,
    planned_bytes: int = 128,
    reason_code: str = "COPY_NEW",
    target_precondition_kind: TargetPreconditionKind = TargetPreconditionKind.ABSENT,
) -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_source_endpoint(), _target_endpoint(planned_bytes=planned_bytes)),
        operations=(
            PlanOperation(
                operation_id="op-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=target_precondition_kind,
                target_relative_path="Pictures/A.jpg",
                planned_bytes=planned_bytes,
                reason_code=reason_code,
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
        control_schema_version=1,
        planned_operations=0,
        planned_bytes=0,
    )


def _target_endpoint(*, planned_bytes: int = 128) -> PlanEndpoint:
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
        planned_bytes=planned_bytes,
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


def _write_endpoint_marker(target_root: Path) -> None:
    (target_root / ".mediasync" / "locks").mkdir(parents=True)
    (target_root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(
            {
                "endpoint_id": "target-a",
                "owner_installation_id": "owner-a",
                "ownership_epoch": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
