from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version
from tests.support.source_preconditions import source_precondition_for_file

from mediasync_home.adapters.final_commit import LocalResolvingFinalCommitAdapter
from mediasync_home.adapters.robocopy import (
    RobocopyStagingTransferAdapter,
    RobocopyTransferProfile,
)
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
from mediasync_home.adapters.sqlite.operation_audit import SqliteOperationAuditStore
from mediasync_home.adapters.sqlite.endpoint_roots import SqliteEndpointRootResolver
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore
from mediasync_home.application.command_receipts import CommandReceipt
from mediasync_home.application.operation_audit_read_models import query_operation_audit
from mediasync_home.application.directory_artifacts import DIRECTORY_MARKER_NAME
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
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.run_executor import HeldRunTargetLeaseRegistry, RunExecutorPumpStopReason
from mediasync_home.application.run_executor_cycle import (
    RunExecutorCycleAction,
    execute_bounded_run_executor_cycle,
)
from mediasync_home.application.run_staging import (
    RunTargetEndpointWaitRequired,
    StagingTransferEvidence,
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


class MultiTargetLiveLease:
    owner_installation_id = "owner-a"
    ownership_epoch = 1

    def __init__(
        self,
        *,
        endpoint_id: str,
        endpoint_revision_id: str,
        run_target_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.endpoint_revision_id = endpoint_revision_id
        self.run_target_id = run_target_id
        self.lease_id = lease_id
        self.fencing_token = fencing_token
        self.released = False

    def issue_mutation_permit(self) -> MutationPermit:
        return _issue_mutation_permit(
            lease_id=self.lease_id,
            resource_key=f"endpoint:{self.endpoint_id}",
            owner_installation_id=self.owner_installation_id,
            ownership_epoch=self.ownership_epoch,
            fencing_token=self.fencing_token,
            run_id="run-a",
            run_target_id=self.run_target_id,
            endpoint_id=self.endpoint_id,
            endpoint_revision_id=self.endpoint_revision_id,
        )

    def release(self) -> None:
        self.released = True

    def assert_mutation_permit_current(self, permit: MutationPermit) -> None:
        assert self.released is False
        assert permit.lease_id == self.lease_id
        assert permit.resource_key == f"endpoint:{self.endpoint_id}"
        assert permit.owner_installation_id == self.owner_installation_id
        assert permit.ownership_epoch == self.ownership_epoch
        assert permit.fencing_token == self.fencing_token
        assert permit.run_id == "run-a"
        assert permit.run_target_id == self.run_target_id
        assert permit.endpoint_id == self.endpoint_id
        assert permit.endpoint_revision_id == self.endpoint_revision_id


class MultiTargetLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, *leases: MultiTargetLiveLease) -> None:
        self.leases = {lease.endpoint_id: lease for lease in leases}

    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt:
        lease = self.leases[request.endpoint_id]
        assert request.run_id == "run-a"
        assert request.run_target_id == lease.run_target_id
        assert request.endpoint_revision_id == lease.endpoint_revision_id
        return EndpointLeaseAttempt(
            acquired=True,
            lease=lease,
            validation_codes=(),
            next_action="Lease acquired.",
        )

    def assert_mutation_permit_current(self, permit: MutationPermit) -> None:
        self.leases[permit.endpoint_id].assert_mutation_permit_current(permit)


class EndpointLossStagingAdapter(LocalFileStagingTransferAdapter):
    def transfer_to_staging(
        self,
        operation: RecoveryOperation,
    ) -> StagingTransferEvidence:
        raise RunTargetEndpointWaitRequired(
            reason_code="NETWORK_INTERRUPTED",
            next_action="Reconnect the endpoint and retry after fresh preflight.",
        )


@pytest.mark.parametrize(
    "staging_backend",
    (
        "local-file",
        pytest.param(
            "robocopy",
            marks=pytest.mark.skipif(
                os.name != "nt",
                reason="full Robocopy executor evidence requires Windows",
            ),
        ),
    ),
)
def test_sqlite_run_executor_cycle_advances_staged_operation_to_completed_run(
    tmp_path: Path,
    staging_backend: str,
) -> None:
    payload = b"x" * 128
    content_hash = hashlib.sha256(payload).hexdigest()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures").mkdir(parents=True)
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.write_bytes(payload)
    named_stream_payload = b"mediasync-named-stream"
    if os.name == "nt":
        Path(f"{source_file}:metadata").write_bytes(named_stream_payload)
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
        operation_audits = SqliteOperationAuditStore(catalog_connection)
        lease = FixedLiveLease()
        registry = HeldRunTargetLeaseRegistry()
        final_commit = LocalResolvingFinalCommitAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
            permit_validator=lease,
        )
        root_resolver = SqliteEndpointRootResolver(catalog_connection)
        staging = (
            RobocopyStagingTransferAdapter(
                root_resolver=root_resolver,
                staging_root=staging_root,
                robocopy_work_root=tmp_path / "robocopy-work",
                profile=RobocopyTransferProfile(timeout_seconds=15.0),
            )
            if staging_backend == "robocopy"
            else LocalFileStagingTransferAdapter(
                root_resolver=root_resolver,
                staging_root=staging_root,
            )
        )
        plan = _sealed_plan(source_file=source_file)
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
            run_executor_operation_audit_store=operation_audits,
            run_executor_staging_transfer_port=staging,
            run_executor_final_commit_port=final_commit,
            run_executor_old_target_preservation_port=final_commit,
            run_executor_recovery_object_cleanup_port=final_commit,
            run_executor_process_instance_id="host-a",
        )
        outcome = runtime.run_executor_cycle(max_steps=19)

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
        assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert outcome.steps_attempted == 19
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
        assert operation.phase is RecoveryOperationPhase.CLEANED
        assert operation.staging_object_id == "op-a"
        assert operation.intent_segment_id == "run-a-target-0000-intent-000000"
        assert operation.catalog_handoff_id == "final-file:run-a:op-a"
        assert operation.expected_source_fingerprint_json is not None
        assert operation.expected_staging_fingerprint_json is not None
        verified_event_payload = json.loads(
            str(
                recovery_connection.execute(
                    """
                    SELECT payload_json
                    FROM recovery_events
                    WHERE run_id = 'run-a'
                        AND operation_id = 'op-a'
                        AND to_phase = 'STAGING_VERIFIED'
                    """
                ).fetchone()[0]
            )
        )
        assert verified_event_payload["operation_audit"]["lease_id"] == "lease-a"
        assert verified_event_payload["operation_audit"]["fencing_token"] == 1
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == payload
        if os.name == "nt":
            assert Path(f"{target_root / 'Pictures' / 'A.jpg'}:metadata").read_bytes() == (
                named_stream_payload
            )
        assert not (staging_root / "op-a.payload").exists()
        assert not (staging_root / "op-a.manifest.json").exists()
        cleaned_event_payload = json.loads(
            str(
                recovery_connection.execute(
                    """
                    SELECT payload_json
                    FROM recovery_events
                    WHERE run_id = 'run-a'
                        AND operation_id = 'op-a'
                        AND to_phase = 'CLEANED'
                    """
                ).fetchone()[0]
            )
        )
        assert cleaned_event_payload["cleaned_object_ids"] == ["op-a"]
        assert handoff is not None
        assert handoff.content_hash == content_hash
        assert catalog_connection.execute(
            "SELECT attempt_number, process_instance_id FROM run_attempts"
        ).fetchall() == [(1, "host-a")]
        assert catalog_connection.execute(
            """
            SELECT attempt_number, state, bytes_transferred
            FROM operation_attempts
            WHERE run_id = 'run-a' AND operation_id = 'op-a'
            """
        ).fetchall() == [(1, "SUCCEEDED", 128)]
        outcome_row = catalog_connection.execute(
            """
            SELECT final_state, bytes_transferred, transfer_state,
                   assurance_level, durability_level, verification_json
            FROM operation_outcomes
            WHERE run_id = 'run-a' AND operation_id = 'op-a'
            """
        ).fetchone()
        assert outcome_row is not None
        assert outcome_row[:5] == (
            "SUCCEEDED",
            128,
            "TRANSFERRED",
            "NAMED_STREAMS_VERIFIED",
            "WRITE_THROUGH_REQUEST_CONFIRMED",
        )
        outcome_verification = json.loads(str(outcome_row[5]))
        assert outcome_verification["raw_transfer_state"] == (
            "ROBOCOPY_EXIT_1_COPIED_TRANSFERRED_TO_STAGING"
            if staging_backend == "robocopy"
            else "TRANSFERRED_TO_STAGING"
        )
        assert outcome_verification["raw_assurance_level"] == "NAMED_STREAMS_VERIFIED"
        expected_final_fingerprint = json.loads(
            outcome_verification["expected_final_fingerprint_json"]
        )
        assert "named_streams" in expected_final_fingerprint
        assert len(expected_final_fingerprint["named_streams"]) == (
            1 if os.name == "nt" else 0
        )
        assert outcome_verification["raw_final_durability_state"] == (
            "LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED"
        )
        assert outcome_verification["final_file_flush_succeeded"] is True
        assert outcome_verification["final_write_through_move_used"] is True
        audit_detail = query_operation_audit(
            operation_audit_store=operation_audits,
            run_id="run-a",
            operation_id="op-a",
        )
        assert audit_detail.found is True
        assert audit_detail.target_relative_path == "Pictures/A.jpg"
        assert audit_detail.attempts[0].process_instance_id == "host-a"
        assert audit_detail.outcome is not None
        assert audit_detail.outcome.final_state == "SUCCEEDED"
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_sqlite_run_executor_cycle_durably_waits_after_network_interruption(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image")
    (target_root / "Pictures").mkdir(parents=True)
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
        staging = EndpointLossStagingAdapter(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            staging_root=staging_root,
        )
        plan = _sealed_plan(source_file=source_file, planned_bytes=5)
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

        waiting_step = None
        for _ in range(12):
            outcome = execute_bounded_run_executor_cycle(
                runs=runs,
                leases=FixedLeaseAuthority(lease),
                lease_registry=registry,
                plans=plans,
                recovery_operations=recovery_operations,
                intent_segments=intent_segments,
                catalog_handoffs=catalog_handoffs,
                staging_transfer_port=staging,
                process_instance_id="host-a",
                max_steps=1,
            )
            if (
                outcome.last_step is not None
                and outcome.last_step.action
                is RunExecutorCycleAction.TARGET_WAITING_FOR_ENDPOINT
            ):
                waiting_step = outcome.last_step
                break

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(
            run_id="run-a",
            operation_id="op-a",
        )
        event = catalog_connection.execute(
            """
            SELECT attempt_no, reason_code, backoff_ms, retry_not_before_utc
            FROM run_target_endpoint_wait_events
            WHERE run_id = 'run-a' AND run_target_id = 'run-a-target-0000'
            """
        ).fetchone()
        assert waiting_step is not None
        assert registry.retained_count == 0
        assert lease.released is True
        assert loaded_run is not None
        assert loaded_run.state is RunState.EXECUTING
        assert loaded_run.targets[0].state is RunTargetState.WAITING_FOR_ENDPOINT
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.STAGING_ALLOCATED
        assert operation.staging_failure_count == 0
        assert event is not None
        assert event[0] == 1
        assert event[1] == "NETWORK_INTERRUPTED"
        assert int(event[2]) > 0
        assert str(event[3]).endswith("Z")
        assert not (staging_root / "op-a.payload").exists()
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_sqlite_run_executor_cycle_creates_parent_before_copying_nested_file(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (source_root / "Pictures" / "A.jpg").write_bytes(b"image")
    target_root.mkdir()
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
        plan = _sealed_directory_plan(
            source_file=source_root / "Pictures" / "A.jpg"
        )
        plans.save_sealed_plan(plan)
        outcome = start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        assert outcome.created is True
        duplicate = start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-b",
                idempotency_key="idempotency-b",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        assert duplicate.created is False
        assert duplicate.idempotent_replay is True
        assert duplicate.run is not None
        assert duplicate.run.run_id == "run-a"
        _register_resource_lease(recovery_connection)

        executor = execute_bounded_run_executor_cycle(
            runs=runs,
            leases=FixedLeaseAuthority(lease),
            lease_registry=registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            final_commit_port=final_commit,
            old_target_preservation_port=final_commit,
            recovery_object_cleanup_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=50,
        )

        loaded_run = runs.load_started_run("run-a")
        operation = recovery_operations.load_operation(
            run_id="run-a",
            operation_id="op-directory",
        )
        handoff = catalog_handoffs.load_final_file_handoff(
            "final-directory:run-a:op-directory"
        )
        file_handoff = catalog_handoffs.load_final_file_handoff(
            "final-file:run-a:op-file"
        )
        assert executor.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert loaded_run is not None
        assert loaded_run.state is RunState.COMPLETED
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.CLEANED
        assert operation.operation_kind.value == "CREATE_DIRECTORY"
        assert operation.plan_sequence_no == 10
        assert (target_root / "Pictures").is_dir()
        assert not (target_root / "Pictures" / DIRECTORY_MARKER_NAME).exists()
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == b"image"
        assert handoff is not None
        assert handoff.effect_kind == "CREATE_DIRECTORY"
        assert file_handoff is not None
        assert file_handoff.effect_kind == "COPY_NEW_FINAL_FILE"
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_sqlite_run_executor_cycle_completes_bound_operations_for_two_targets(
    tmp_path: Path,
) -> None:
    payload = b"replicated-image"
    source_root = tmp_path / "source"
    target_a_root = tmp_path / "target-a"
    target_b_root = tmp_path / "target-b"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (target_a_root / "Pictures").mkdir(parents=True)
    (target_b_root / "Pictures").mkdir(parents=True)
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.write_bytes(payload)
    _write_endpoint_marker(target_a_root)
    _write_endpoint_marker(target_b_root, endpoint_id="target-b")

    catalog_connection = _prepared_catalog_connection(tmp_path)
    recovery_connection = _prepared_recovery_connection(tmp_path)
    try:
        _insert_plan_parent_rows(
            catalog_connection,
            source_root=source_root,
            target_root=target_a_root,
            additional_target_root=target_b_root,
        )
        _insert_receipt(catalog_connection)
        plans = SqlitePlanStore(catalog_connection)
        runs = SqliteRunStore(catalog_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        operation_audits = SqliteOperationAuditStore(catalog_connection)
        leases = (
            MultiTargetLiveLease(
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                run_target_id="run-a-target-0000",
                lease_id="lease-a",
                fencing_token=1,
            ),
            MultiTargetLiveLease(
                endpoint_id="target-b",
                endpoint_revision_id="target-rev-b",
                run_target_id="run-a-target-0001",
                lease_id="lease-b",
                fencing_token=1,
            ),
        )
        lease_authority = MultiTargetLeaseAuthority(*leases)
        registry = HeldRunTargetLeaseRegistry()
        root_resolver = SqliteEndpointRootResolver(catalog_connection)
        final_commit = LocalResolvingFinalCommitAdapter(
            root_resolver=root_resolver,
            staging_root=staging_root,
            permit_validator=lease_authority,
        )
        staging = LocalFileStagingTransferAdapter(
            root_resolver=root_resolver,
            staging_root=staging_root,
        )
        plan = _sealed_multi_target_plan(source_file=source_file, planned_bytes=len(payload))
        plans.save_sealed_plan(plan)
        outcome = start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        assert outcome.created is True
        assert outcome.run is not None
        assert [target.endpoint_id for target in outcome.run.targets] == [
            "target-a",
            "target-b",
        ]
        _register_resource_lease_for_target(
            recovery_connection,
            lease_id="lease-a",
            endpoint_id="target-a",
            run_target_id="run-a-target-0000",
        )
        _register_resource_lease_for_target(
            recovery_connection,
            lease_id="lease-b",
            endpoint_id="target-b",
            run_target_id="run-a-target-0001",
        )

        executor = execute_bounded_run_executor_cycle(
            runs=runs,
            leases=lease_authority,
            lease_registry=registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            operation_audits=operation_audits,
            final_commit_port=final_commit,
            old_target_preservation_port=final_commit,
            recovery_object_cleanup_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=50,
        )

        loaded_run = runs.load_started_run("run-a")
        assert executor.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert registry.retained_count == 0
        assert all(lease.released for lease in leases)
        assert loaded_run is not None
        assert loaded_run.state is RunState.COMPLETED
        assert [target.state for target in loaded_run.targets] == [
            RunTargetState.SUCCEEDED,
            RunTargetState.SUCCEEDED,
        ]
        assert [target.completed_operations for target in loaded_run.targets] == [1, 1]
        assert [target.completed_bytes for target in loaded_run.targets] == [
            len(payload),
            len(payload),
        ]
        assert loaded_run.planned_operations == 2
        assert loaded_run.planned_bytes == len(payload) * 2
        assert (target_a_root / "Pictures" / "A.jpg").read_bytes() == payload
        assert (target_b_root / "Pictures" / "A.jpg").read_bytes() == payload
        target_operations = [
            recovery_operations.load_operation(
                run_id="run-a",
                operation_id=operation_id,
            )
            for operation_id in ("op-target-a", "op-target-b")
        ]
        assert all(operation is not None for operation in target_operations)
        assert [
            operation.phase
            for operation in target_operations
            if operation is not None
        ] == [
            RecoveryOperationPhase.CLEANED,
            RecoveryOperationPhase.CLEANED,
        ]
        for operation_id in ("op-target-a", "op-target-b"):
            assert not (staging_root / f"{operation_id}.payload").exists()
            assert not (staging_root / f"{operation_id}.manifest.json").exists()
        assert catalog_connection.execute(
            """
            SELECT outcomes.run_target_id,
                   targets.endpoint_id,
                   details.target_relative_path
            FROM operation_outcomes AS outcomes
            JOIN run_targets AS targets
              ON targets.run_id = outcomes.run_id
             AND targets.id = outcomes.run_target_id
            JOIN plan_operation_seal_details AS details
              ON details.plan_id = outcomes.plan_id
             AND details.operation_id = outcomes.operation_id
            WHERE outcomes.run_id = 'run-a'
            ORDER BY outcomes.run_target_id
            """
        ).fetchall() == [
            ("run-a-target-0000", "target-a", "Pictures/A.jpg"),
            ("run-a-target-0001", "target-b", "Pictures/A.jpg"),
        ]
        assert catalog_handoffs.load_final_file_handoff(
            "final-file:run-a:op-target-a"
        ) is not None
        assert catalog_handoffs.load_final_file_handoff(
            "final-file:run-a:op-target-b"
        ) is not None
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_new_files_only_cycle_copies_new_file_and_defers_changed_target(
    tmp_path: Path,
) -> None:
    new_payload = b"new-file-payload"
    changed_source_payload = b"changed-source-payload"
    original_target_payload = b"original-target-payload"
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (source_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures").mkdir(parents=True)
    new_source_file = source_root / "Pictures" / "New.jpg"
    changed_source_file = source_root / "Pictures" / "Changed.jpg"
    changed_target_file = target_root / "Pictures" / "Changed.jpg"
    new_source_file.write_bytes(new_payload)
    changed_source_file.write_bytes(changed_source_payload)
    changed_target_file.write_bytes(original_target_payload)
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
        plan = _new_files_only_mixed_plan(
            new_source_file=new_source_file,
            changed_source_file=changed_source_file,
        )
        plans.save_sealed_plan(plan)
        started = start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        assert started.created is True
        assert started.run is not None
        assert started.run.planned_operations == 1
        assert started.run.planned_bytes == len(new_payload)
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
            recovery_object_cleanup_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=25,
        )

        loaded_run = runs.load_started_run("run-a")
        progress = runs.load_run_progress_snapshot("run-a")
        assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
        assert loaded_run is not None
        assert loaded_run.state is RunState.COMPLETED_WITH_WARNINGS
        assert loaded_run.targets[0].completed_operations == 1
        assert loaded_run.targets[0].completed_bytes == len(new_payload)
        assert progress is not None
        assert progress.action_required is True
        assert progress.deferred_operation_count == 1
        assert progress.deferred_planned_bytes == len(changed_source_payload)
        summary_row = catalog_connection.execute(
            "SELECT summary_json FROM runs WHERE id = 'run-a'"
        ).fetchone()
        assert summary_row is not None
        assert json.loads(str(summary_row[0]))["automation_policy"] == "NEW_FILES_ONLY"
        assert (target_root / "Pictures" / "New.jpg").read_bytes() == new_payload
        assert changed_target_file.read_bytes() == original_target_payload
        assert recovery_operations.load_operation(
            run_id="run-a",
            operation_id="op-new",
        ) is not None
        assert recovery_operations.load_operation(
            run_id="run-a",
            operation_id="op-changed",
        ) is None
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
            source_file=source_root / "Pictures" / "A.jpg",
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
            recovery_object_cleanup_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=16,
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
        assert operation.phase is RecoveryOperationPhase.CLEANED
        assert operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT
        assert operation.version_object_id == "op-a"
        assert operation.version_created_utc is not None
        assert operation.version_retention_until_utc is not None
        assert operation.version_manifest_hash is not None
        assert operation.expected_target_fingerprint_json == json.dumps(
            {
                "byte_count": len(old_payload),
                "content_hash": hashlib.sha256(old_payload).hexdigest(),
                "named_streams": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == new_payload
        assert not (staging_root / "op-a.payload").exists()
        assert not (staging_root / "op-a.manifest.json").exists()
        assert version_payload.read_bytes() == old_payload
        assert json.loads(version_manifest.read_text(encoding="utf-8"))["object_role"] == "OLD_TARGET_VERSION"
        assert handoff is not None
        assert handoff.content_hash == content_hash
        assert handoff.retained_version is not None
        assert handoff.retained_version.version_object_id == "op-a"
        assert handoff.retained_version.job_id == "job-a"
        assert handoff.retained_version.job_revision_id == "job-rev-a"
        assert handoff.retained_version.manifest_hash == operation.version_manifest_hash
        assert catalog_connection.execute(
            "SELECT state FROM retained_version_objects WHERE version_object_id = 'op-a'"
        ).fetchone() == ("RETAINED",)
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
            source_file=source_root / "Pictures" / "A.jpg",
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
            recovery_object_cleanup_port=final_commit,
            staging_transfer_port=staging,
            process_instance_id="host-a",
            max_steps=16,
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
        assert operation.phase is RecoveryOperationPhase.CLEANED
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
        assert list(quarantine_payload.iterdir()) == []
        assert json.loads(quarantine_manifest.read_text(encoding="utf-8"))[
            "object_role"
        ] == "EMPTY_DIRECTORY_QUARANTINE"
        assert handoff is not None
        assert handoff.content_hash == content_hash
        assert handoff.retained_version is not None
        assert handoff.retained_version.version_object_id == "op-a"
        assert (
            handoff.retained_version.object_role
            == "EMPTY_DIRECTORY_QUARANTINE"
        )
        assert catalog_connection.execute(
            """
            SELECT state, object_role
            FROM retained_version_objects
            WHERE version_object_id = 'op-a'
            """
        ).fetchone() == ("RETAINED", "EMPTY_DIRECTORY_QUARANTINE")
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
    additional_target_root: Path | None = None,
) -> None:
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
    connection.execute("INSERT INTO endpoints (id) VALUES ('source-a')")
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-a')")
    if additional_target_root is not None:
        connection.execute("INSERT INTO endpoints (id) VALUES ('target-b')")
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
    if additional_target_root is not None:
        connection.execute(
            """
            INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
                VALUES ('target-b', 'target-rev-b', 'USB B', ?)
            """,
            (additional_target_root.as_uri(),),
        )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'source-a', 'source-rev-a')
        """
    )
    if additional_target_root is not None:
        connection.execute(
            """
            INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
                VALUES ('analysis-a', 'target-b', 'target-rev-b')
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
    if additional_target_root is not None:
        connection.execute(
            """
            INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
                VALUES ('target-snapshot-b', 'analysis-a', 'target-b', 'target-rev-b')
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
    source_file: Path,
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
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_for_file(
                    source_file,
                    relative_path="Pictures/A.jpg",
                ),
                planned_bytes=planned_bytes,
                reason_code=reason_code,
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _new_files_only_mixed_plan(
    *,
    new_source_file: Path,
    changed_source_file: Path,
) -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        execution_policy="NEW_FILES_ONLY",
        endpoints=(
            _source_endpoint(),
            _target_endpoint(
                planned_bytes=len(new_source_file.read_bytes()),
                planned_operations=1,
            ),
        ),
        operations=(
            PlanOperation(
                operation_id="op-new",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/New.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/New.jpg",
                source_relative_path="Pictures/New.jpg",
                source_precondition_json=source_precondition_for_file(
                    new_source_file,
                    relative_path="Pictures/New.jpg",
                ),
                planned_bytes=new_source_file.stat().st_size,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-changed",
                operation_type=PlanOperationType.DEFER_AUTOMATION_POLICY,
                deferred_operation_type=PlanOperationType.REPLACE_CHANGED,
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:Pictures/Changed.jpg",
                target_precondition_kind=TargetPreconditionKind.MATCH_FINGERPRINT,
                target_relative_path="Pictures/Changed.jpg",
                source_relative_path="Pictures/Changed.jpg",
                source_precondition_json=source_precondition_for_file(
                    changed_source_file,
                    relative_path="Pictures/Changed.jpg",
                ),
                planned_bytes=changed_source_file.stat().st_size,
                reason_code="REPLACE_WITH_VERSION",
                risk_level=PlanRiskLevel.MEDIUM,
            ),
        ),
    )


def _sealed_directory_plan(*, source_file: Path) -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _source_endpoint(),
            _target_endpoint(planned_bytes=5, planned_operations=2),
        ),
        operations=(
            PlanOperation(
                operation_id="op-directory",
                operation_type=PlanOperationType.CREATE_DIRECTORY,
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:Pictures",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures",
                planned_bytes=0,
                reason_code="CREATE_MISSING_DIRECTORY",
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-file",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_for_file(
                    source_file,
                    relative_path="Pictures/A.jpg",
                ),
                planned_bytes=5,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _sealed_multi_target_plan(*, source_file: Path, planned_bytes: int) -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _source_endpoint(),
            _target_endpoint(planned_bytes=planned_bytes),
            PlanEndpoint(
                endpoint_id="target-b",
                endpoint_revision_id="target-rev-b",
                snapshot_id="target-snapshot-b",
                role=PlanEndpointRole.TARGET_WRITABLE,
                target_ordinal=1,
                capabilities_hash="capabilities-b",
                root_case_context_hash="case-b",
                endpoint_generation=1,
                required_owner_installation_id="owner-a",
                required_ownership_epoch=1,
                control_schema_version=1,
                planned_operations=1,
                planned_bytes=planned_bytes,
            ),
        ),
        operations=tuple(
            PlanOperation(
                operation_id=f"op-{endpoint_id}",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=sequence_no,
                execution_phase=20,
                stable_order_key=(
                    f"020:{target_ordinal:04d}:{endpoint_id}:Pictures/A.jpg"
                ),
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_endpoint_id=endpoint_id,
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_for_file(
                    source_file,
                    relative_path="Pictures/A.jpg",
                ),
                planned_bytes=planned_bytes,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            )
            for endpoint_id, target_ordinal, sequence_no in (
                ("target-a", 0, 10),
                ("target-b", 1, 20),
            )
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


def _target_endpoint(
    *,
    planned_bytes: int = 128,
    planned_operations: int = 1,
) -> PlanEndpoint:
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
        planned_operations=planned_operations,
        planned_bytes=planned_bytes,
    )


def _register_resource_lease(connection: sqlite3.Connection) -> None:
    _register_resource_lease_for_target(
        connection,
        lease_id="lease-a",
        endpoint_id="target-a",
        run_target_id="run-a-target-0000",
    )


def _register_resource_lease_for_target(
    connection: sqlite3.Connection,
    *,
    lease_id: str,
    endpoint_id: str,
    run_target_id: str,
) -> None:
    assert SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
        lease_id=lease_id,
        resource_key=f"endpoint:{endpoint_id}",
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id=run_target_id,
        endpoint_id=endpoint_id,
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    ) == 1


def _write_endpoint_marker(target_root: Path, *, endpoint_id: str = "target-a") -> None:
    (target_root / ".mediasync" / "locks").mkdir(parents=True)
    (target_root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(
            {
                "endpoint_id": endpoint_id,
                "owner_installation_id": "owner-a",
                "ownership_epoch": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
