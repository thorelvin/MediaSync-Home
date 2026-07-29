from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from mediasync_home.application.catalog_handoff import (
    FinalFileCatalogHandoff,
    FinalFileCatalogHandoffStore,
)
from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    RelativePath,
    VerifiedStagingArtifact,
)
from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    PlanStore,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentStore,
    durable_recovery_intent_segment,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_executor import (
    HeldRunTargetLeaseRegistry,
    RunExecutorPumpStopReason,
    RunExecutorQueueStore,
)
from mediasync_home.application.run_executor_cycle import (
    RunExecutorCycleAction,
    RunExecutorCycleRecoveryOperationStore,
    execute_bounded_run_executor_cycle,
    execute_one_run_executor_cycle,
)
from mediasync_home.application.run_staging import (
    SourceStabilityEvidence,
    SourceValidationEvidence,
    StagingAllocation,
    StagingDurabilityEvidence,
    StagingTransferEvidence,
    StagingVerificationEvidence,
    TargetPreconditionEvidence,
)
from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    RunState,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_bounded_executor_cycle_progresses_queued_target_through_staging() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(_run(state=RunState.QUEUED, plan=plan))
    lease = _FakeLiveLease()
    registry = HeldRunTargetLeaseRegistry()
    recovery_operations = _FakeRecoveryOperationStore(())

    outcome = execute_bounded_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        staging_transfer_port=_FakeStagingPort(),
        process_instance_id="host-a",
        max_steps=10,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.steps_attempted == 10
    assert outcome.stopped_reason is RunExecutorPumpStopReason.STEP_LIMIT_REACHED
    assert outcome.last_step is not None
    assert outcome.last_step.action is RunExecutorCycleAction.STAGING_ADVANCED
    assert outcome.validation_codes == ()
    assert registry.retained_count == 1
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.STAGING_VERIFIED
    assert operation.staging_object_id == "op-a"
    assert operation.expected_source_fingerprint_json == _fingerprint_json()
    assert operation.expected_staging_fingerprint_json == _fingerprint_json()


def test_bounded_executor_cycle_reports_missing_staging_port_after_planning() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(_run(state=RunState.QUEUED, plan=plan))
    lease = _FakeLiveLease()
    registry = HeldRunTargetLeaseRegistry()
    recovery_operations = _FakeRecoveryOperationStore(())

    outcome = execute_bounded_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
        max_steps=4,
    )

    assert outcome.stopped_reason is RunExecutorPumpStopReason.BLOCKED
    assert outcome.last_step is not None
    assert outcome.last_step.action is RunExecutorCycleAction.WAITING_FOR_STAGING
    assert outcome.validation_codes == ("RUN_EXECUTOR_STAGING_PORT_NOT_CONFIGURED",)
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.PLANNED


def test_executor_cycle_reacquires_revalidating_target_after_registry_loss() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(
        _run(
            state=RunState.PREFLIGHT,
            plan=plan,
            target_state=RunTargetState.REVALIDATING,
        )
    )
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    registry = HeldRunTargetLeaseRegistry()

    outcome = execute_one_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore(()),
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.action is RunExecutorCycleAction.EXECUTION_STARTED
    assert outcome.advanced is True
    assert outcome.validation_codes == ()
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING
    assert loaded.targets[0].last_lease_id == "lease-b"
    assert loaded.targets[0].last_fencing_token == 43
    assert registry.load_retained_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
    ) is lease


def test_executor_cycle_reacquires_executing_target_after_registry_loss_before_planning() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(
        _run(
            state=RunState.EXECUTING,
            plan=plan,
            target_state=RunTargetState.EXECUTING,
        )
    )
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    registry = HeldRunTargetLeaseRegistry()
    recovery_operations = _FakeRecoveryOperationStore(())

    outcome = execute_bounded_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
        max_steps=2,
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.steps_attempted == 2
    assert outcome.stopped_reason is RunExecutorPumpStopReason.STEP_LIMIT_REACHED
    assert outcome.last_step is not None
    assert outcome.last_step.action is RunExecutorCycleAction.OPERATIONS_PLANNED
    assert outcome.validation_codes == ()
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING
    assert loaded.targets[0].last_lease_id == "lease-b"
    assert loaded.targets[0].last_fencing_token == 43
    assert registry.load_retained_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
    ) is lease
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.PLANNED
    assert operation.lease_id == "lease-b"
    assert operation.fencing_token == 43


def test_executor_cycle_rebinds_pre_commit_operation_after_reacquired_executing_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(
        _run(
            state=RunState.EXECUTING,
            plan=plan,
            target_state=RunTargetState.EXECUTING,
        )
    )
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    registry = HeldRunTargetLeaseRegistry()
    recovery_operations = _FakeRecoveryOperationStore((_planned_operation(),))

    outcome = execute_bounded_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
        max_steps=2,
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.steps_attempted == 2
    assert outcome.stopped_reason is RunExecutorPumpStopReason.STEP_LIMIT_REACHED
    assert outcome.last_step is not None
    assert outcome.last_step.action is RunExecutorCycleAction.OPERATION_LEASE_REBOUND
    assert outcome.validation_codes == ()
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.PLANNED
    assert operation.lease_id == "lease-b"
    assert operation.fencing_token == 43


def test_executor_cycle_refreshes_commit_intent_after_reacquired_executing_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(
        _run(
            state=RunState.EXECUTING,
            plan=plan,
            target_state=RunTargetState.EXECUTING,
        )
    )
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    registry = HeldRunTargetLeaseRegistry()
    recovery_operations = _FakeRecoveryOperationStore((_commit_intent_operation(),))
    intent_segments = _FakeIntentSegmentStore((_intent_segment(),))

    outcome = execute_bounded_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=intent_segments,
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
        max_steps=2,
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    refreshed_segment = intent_segments.load_intent_segment("run-a-target-0000-intent-000001")
    assert outcome.steps_attempted == 2
    assert outcome.stopped_reason is RunExecutorPumpStopReason.STEP_LIMIT_REACHED
    assert outcome.last_step is not None
    assert outcome.last_step.action is RunExecutorCycleAction.COMMIT_INTENT_REFRESHED
    assert outcome.validation_codes == ()
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED
    assert operation.lease_id == "lease-b"
    assert operation.fencing_token == 43
    assert operation.intent_segment_id == "run-a-target-0000-intent-000001"
    assert operation.intent_ordinal == 0
    assert refreshed_segment is not None
    assert refreshed_segment.segment_sequence == 1
    assert refreshed_segment.previous_segment_hash == _intent_segment().segment_hash
    assert refreshed_segment.lease_id == "lease-b"
    assert refreshed_segment.fencing_token == 43


def test_executor_cycle_refreshes_preserved_replacement_after_reacquired_executing_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(
        _run(
            state=RunState.EXECUTING,
            plan=plan,
            target_state=RunTargetState.EXECUTING,
        )
    )
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    registry = HeldRunTargetLeaseRegistry()
    recovery_operations = _FakeRecoveryOperationStore((_old_target_preserved_operation(),))
    intent_segments = _FakeIntentSegmentStore((_intent_segment(),))

    outcome = execute_bounded_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=intent_segments,
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
        max_steps=2,
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    refreshed_segment = intent_segments.load_intent_segment("run-a-target-0000-intent-000001")
    assert outcome.steps_attempted == 2
    assert outcome.stopped_reason is RunExecutorPumpStopReason.STEP_LIMIT_REACHED
    assert outcome.last_step is not None
    assert outcome.last_step.action is RunExecutorCycleAction.COMMIT_INTENT_REFRESHED
    assert outcome.validation_codes == ()
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.OLD_TARGET_PRESERVED
    assert operation.lease_id == "lease-b"
    assert operation.fencing_token == 43
    assert operation.intent_segment_id == "run-a-target-0000-intent-000001"
    assert operation.version_object_id == "op-a"
    assert refreshed_segment is not None
    assert refreshed_segment.segment_sequence == 1
    assert refreshed_segment.previous_segment_hash == _intent_segment().segment_hash
    assert refreshed_segment.lease_id == "lease-b"
    assert refreshed_segment.fencing_token == 43


def test_executor_cycle_commits_preserved_replacement_with_retained_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(
        _run(state=RunState.EXECUTING, plan=plan, target_state=RunTargetState.EXECUTING)
    )
    lease = _FakeLiveLease()
    registry = HeldRunTargetLeaseRegistry()
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=lease,
    )
    recovery_operations = _FakeRecoveryOperationStore((_old_target_preserved_operation(),))
    final_commit = _FakeFinalCommitPort()

    outcome = execute_one_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        intent_segments=_FakeIntentSegmentStore((_intent_segment(),)),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.action is RunExecutorCycleAction.FINAL_COMMITTED
    assert outcome.advanced is True
    assert outcome.validation_codes == ()
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.FINAL_VERIFIED
    assert operation.version_object_id == "op-a"
    assert operation.final_durability_state == "FINAL_COMMIT_ADAPTER_COMPLETED"
    assert final_commit.calls == (
        (
            "lease-a",
            VerifiedStagingArtifact(
                object_id="op-a",
                relative_path=RelativePath("Pictures/A.jpg"),
                content_hash="a" * 64,
            ),
        ),
    )


def test_executor_cycle_completes_catalog_recorded_target_and_releases_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(_run(state=RunState.EXECUTING, plan=plan, target_state=RunTargetState.EXECUTING))
    lease = _FakeLiveLease()
    registry = HeldRunTargetLeaseRegistry()
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=lease,
    )

    outcome = execute_one_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore((_operation(),)),
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.action is RunExecutorCycleAction.TARGET_COMPLETED
    assert outcome.advanced is True
    assert outcome.validation_codes == ()
    assert registry.retained_count == 0
    assert lease.released is True
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert loaded.targets[0].state is RunTargetState.SUCCEEDED


def test_executor_cycle_marks_user_decision_target_recovery_required_and_releases_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(_run(state=RunState.EXECUTING, plan=plan, target_state=RunTargetState.EXECUTING))
    lease = _FakeLiveLease()
    registry = HeldRunTargetLeaseRegistry()
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=lease,
    )

    outcome = execute_one_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.USER_DECISION_REQUIRED,
                    last_error_code="LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",
                ),
            )
        ),
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.action is RunExecutorCycleAction.TARGET_RECOVERY_REQUIRED
    assert outcome.advanced is True
    assert outcome.validation_codes == ()
    assert registry.retained_count == 0
    assert lease.released is True
    assert loaded is not None
    assert loaded.state is RunState.RECOVERY_REQUIRED
    assert loaded.targets[0].state is RunTargetState.RECOVERY_REQUIRED


def test_executor_cycle_marks_restored_old_target_cancelled_and_releases_lease() -> None:
    plan = _sealed_plan()
    runs = _InMemoryRunStore(_run(state=RunState.EXECUTING, plan=plan, target_state=RunTargetState.EXECUTING))
    lease = _FakeLiveLease()
    registry = HeldRunTargetLeaseRegistry()
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=lease,
    )

    outcome = execute_one_run_executor_cycle(
        runs=runs,
        leases=_FakeLeaseAuthority(lease),
        lease_registry=registry,
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.CANCELLED,
                    last_error_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED",
                ),
            )
        ),
        intent_segments=_FakeIntentSegmentStore(),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.action is RunExecutorCycleAction.TARGET_CANCELLED
    assert outcome.advanced is True
    assert outcome.validation_codes == ()
    assert registry.retained_count == 0
    assert lease.released is True
    assert loaded is not None
    assert loaded.state is RunState.CANCELLED
    assert loaded.targets[0].state is RunTargetState.CANCELLED


class _InMemoryRunStore(RunExecutorQueueStore):
    def __init__(self, run: StartedRun | None) -> None:
        self.run = run

    def save_started_run(self, run: StartedRun) -> None:
        self.run = run

    def load_started_run(self, run_id: str) -> StartedRun | None:
        if self.run is not None and self.run.run_id == run_id:
            return self.run
        return None

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
        if self.run is not None and self.run.idempotency_key == idempotency_key:
            return self.run
        return None

    def load_next_runnable_run(self) -> StartedRun | None:
        if self.run is None or self.run.state not in {RunState.QUEUED, RunState.PREFLIGHT}:
            return None
        if not any(target.state is RunTargetState.PENDING for target in self.run.targets):
            return None
        return self.run

    def load_next_revalidating_run_target_key(self) -> tuple[str, str] | None:
        if self.run is None or self.run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
            return None
        target = next(
            (target for target in self.run.targets if target.state is RunTargetState.REVALIDATING),
            None,
        )
        if target is None:
            return None
        return self.run.run_id, target.run_target_id

    def load_next_executing_run_target_key(self) -> tuple[str, str] | None:
        if self.run is None or self.run.state is not RunState.EXECUTING:
            return None
        target = next(
            (target for target in self.run.targets if target.state is RunTargetState.EXECUTING),
            None,
        )
        if target is None:
            return None
        return self.run.run_id, target.run_target_id

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None:
            return None
        return next((target for target in run.targets if target.state is RunTargetState.PENDING), None)

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {RunState.QUEUED, RunState.PREFLIGHT}:
            return None
        updated_targets: list[StartedRunTarget] = []
        claimed: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.PENDING:
                claimed = replace(target, state=RunTargetState.ACQUIRING_LEASE)
                updated_targets.append(claimed)
            else:
                updated_targets.append(target)
        if claimed is None:
            return None
        self.run = replace(run, state=RunState.PREFLIGHT, targets=tuple(updated_targets))
        return claimed

    def record_run_target_lease_acquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
            return None
        updated_targets: list[StartedRunTarget] = []
        recorded: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.ACQUIRING_LEASE:
                recorded = replace(
                    target,
                    state=RunTargetState.REVALIDATING,
                    last_lease_id=lease_id,
                    last_ownership_epoch=ownership_epoch,
                    last_fencing_token=fencing_token,
                )
                updated_targets.append(recorded)
            else:
                updated_targets.append(target)
        if recorded is None:
            return None
        self.run = replace(run, targets=tuple(updated_targets))
        return recorded

    def record_run_target_lease_reacquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
            return None
        updated_targets: list[StartedRunTarget] = []
        recorded: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state in {
                RunTargetState.REVALIDATING,
                RunTargetState.EXECUTING,
            }:
                if (
                    target.last_lease_id != expected_lease_id
                    or target.last_ownership_epoch != expected_ownership_epoch
                    or target.last_fencing_token != expected_fencing_token
                ):
                    return None
                recorded = replace(
                    target,
                    last_lease_id=lease_id,
                    last_ownership_epoch=ownership_epoch,
                    last_fencing_token=fencing_token,
                )
                updated_targets.append(recorded)
            else:
                updated_targets.append(target)
        if recorded is None:
            return None
        self.run = replace(run, targets=tuple(updated_targets))
        return recorded

    def record_run_target_execution_started(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.PREFLIGHT:
            return None
        updated_targets: list[StartedRunTarget] = []
        started: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.REVALIDATING:
                started = replace(target, state=RunTargetState.EXECUTING)
                updated_targets.append(started)
            else:
                updated_targets.append(target)
        if started is None:
            return None
        self.run = replace(run, state=RunState.EXECUTING, targets=tuple(updated_targets))
        return started

    def record_run_target_succeeded(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
    ) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.EXECUTING:
            return None
        updated_targets: list[StartedRunTarget] = []
        completed: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.EXECUTING:
                if (
                    target.planned_operations != completed_operations
                    or target.planned_bytes != completed_bytes
                ):
                    return None
                completed = replace(
                    target,
                    state=RunTargetState.SUCCEEDED,
                    completed_operations=completed_operations,
                    completed_bytes=completed_bytes,
                )
                updated_targets.append(completed)
            else:
                updated_targets.append(target)
        if completed is None:
            return None
        self.run = replace(run, state=RunState.COMPLETED, targets=tuple(updated_targets))
        return self.run

    def record_run_target_recovery_required(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None:
        return self._record_terminal_target(
            run_id=run_id,
            run_target_id=run_target_id,
            target_state=RunTargetState.RECOVERY_REQUIRED,
            run_state=RunState.RECOVERY_REQUIRED,
        )

    def record_run_target_cancelled(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None:
        return self._record_terminal_target(
            run_id=run_id,
            run_target_id=run_target_id,
            target_state=RunTargetState.CANCELLED,
            run_state=RunState.CANCELLED,
        )

    def _record_terminal_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
        target_state: RunTargetState,
        run_state: RunState,
    ) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.EXECUTING:
            return None
        updated_targets: list[StartedRunTarget] = []
        completed: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.EXECUTING:
                completed = replace(target, state=target_state)
                updated_targets.append(completed)
            else:
                updated_targets.append(target)
        if completed is None:
            return None
        self.run = replace(
            run,
            state=run_state,
            targets=tuple(updated_targets),
            error_count=run.error_count + 1,
        )
        return self.run


class _FakeRecoveryOperationStore(RunExecutorCycleRecoveryOperationStore):
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self.operations = {
            (operation.run_id, operation.operation_id): operation for operation in operations
        }

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        existing = self.operations.get((operation.run_id, operation.operation_id))
        if existing is not None:
            return existing
        self.operations[(operation.run_id, operation.operation_id)] = operation
        return operation

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: object | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if operation is None or operation.phase is not expected_phase:
            return None
        metadata_updates: dict[str, object] = {}
        if operation_metadata is not None:
            for field_name in (
                "source_guard_kind",
                "source_guard_evidence_hash",
                "source_hash_evidence_kind",
                "staging_object_id",
                "version_object_id",
                "quarantine_object_id",
                "expected_source_fingerprint_json",
                "expected_target_fingerprint_json",
                "expected_staging_fingerprint_json",
                "expected_final_fingerprint_json",
                "transfer_state",
                "assurance_level",
                "staging_durability_state",
                "final_durability_state",
                "last_error_code",
            ):
                value = getattr(operation_metadata, field_name, None)
                if value is not None:
                    metadata_updates[field_name] = value
        updated = replace(
            operation,
            phase=next_phase,
            intent_segment_id=intent_segment_id or operation.intent_segment_id,
            intent_ordinal=operation.intent_ordinal if intent_ordinal is None else intent_ordinal,
            catalog_handoff_id=catalog_handoff_id or operation.catalog_handoff_id,
            **metadata_updates,
        )
        self.operations[(run_id, operation_id)] = updated
        return updated

    def record_operation_lease_rebound(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if (
            operation is None
            or operation.phase is not expected_phase
            or operation.lease_id != expected_lease_id
            or operation.ownership_epoch != expected_ownership_epoch
            or operation.fencing_token != expected_fencing_token
        ):
            return None
        updated = replace(
            operation,
            owner_installation_id=owner_installation_id,
            ownership_epoch=ownership_epoch,
            lease_id=lease_id,
            fencing_token=fencing_token,
        )
        self.operations[(run_id, operation_id)] = updated
        return updated

    def record_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if (
            operation is None
            or operation.phase is not RecoveryOperationPhase.COMMIT_INTENT_RECORDED
            or operation.lease_id != expected_lease_id
            or operation.ownership_epoch != expected_ownership_epoch
            or operation.fencing_token != expected_fencing_token
        ):
            return None
        updated = replace(
            operation,
            owner_installation_id=owner_installation_id,
            ownership_epoch=ownership_epoch,
            lease_id=lease_id,
            fencing_token=fencing_token,
            intent_segment_id=intent_segment_id,
            intent_ordinal=intent_ordinal,
        )
        self.operations[(run_id, operation_id)] = updated
        return updated

    def record_old_target_preserved_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if (
            operation is None
            or operation.phase is not RecoveryOperationPhase.OLD_TARGET_PRESERVED
            or operation.lease_id != expected_lease_id
            or operation.ownership_epoch != expected_ownership_epoch
            or operation.fencing_token != expected_fencing_token
        ):
            return None
        updated = replace(
            operation,
            owner_installation_id=owner_installation_id,
            ownership_epoch=ownership_epoch,
            lease_id=lease_id,
            fencing_token=fencing_token,
            intent_segment_id=intent_segment_id,
            intent_ordinal=intent_ordinal,
        )
        self.operations[(run_id, operation_id)] = updated
        return updated

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        return self.operations.get((run_id, operation_id))

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in sorted(self.operations.values(), key=lambda item: item.operation_id)
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _SinglePlanStore(PlanStore):
    def __init__(self, plan: SealedPlan) -> None:
        self.plan = plan

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self.plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        if self.plan.plan_id == plan_id:
            return self.plan
        return None


class _FakeStagingPort:
    def validate_source_file(self, operation: RecoveryOperation) -> SourceValidationEvidence:
        return SourceValidationEvidence(
            fingerprint_json=_fingerprint_json(),
            hash_evidence_kind="SHA256_CURRENT_SOURCE_FILE",
        )

    def bind_source_stability(self, operation: RecoveryOperation) -> SourceStabilityEvidence:
        return SourceStabilityEvidence(
            guard_kind="POST_TRANSFER_HASH_ONLY",
            guard_evidence_hash="a" * 64,
        )

    def validate_target_precondition(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence:
        return TargetPreconditionEvidence(fingerprint_json='{"kind":"ABSENT"}')

    def allocate_staging_object(self, operation: RecoveryOperation) -> StagingAllocation:
        return StagingAllocation(staging_object_id=operation.operation_id)

    def transfer_to_staging(self, operation: RecoveryOperation) -> StagingTransferEvidence:
        return StagingTransferEvidence(transfer_state="TRANSFERRED_TO_STAGING")

    def ensure_staging_durable(self, operation: RecoveryOperation) -> StagingDurabilityEvidence:
        return StagingDurabilityEvidence(durability_state="FILE_FSYNC_COMPLETED")

    def verify_staging_artifact(self, operation: RecoveryOperation) -> StagingVerificationEvidence:
        return StagingVerificationEvidence(
            fingerprint_json=_fingerprint_json(),
            final_fingerprint_json=_fingerprint_json(),
            assurance_level="STAGING_HASH_MATCHES_POST_TRANSFER_SOURCE_HASH",
        )


class _FakeFinalCommitPort(FinalCommitPort):
    def __init__(self) -> None:
        self.calls: tuple[tuple[str, VerifiedStagingArtifact], ...] = ()

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        self.calls = (*self.calls, (permit.lease_id, artifact))
        return CommitReceipt(
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path,
        )


class _FakeLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, lease: "_FakeLiveLease") -> None:
        self.lease = lease

    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt:
        return EndpointLeaseAttempt(
            acquired=True,
            lease=self.lease,
            validation_codes=(),
            next_action="Lease acquired.",
        )


class _FakeLiveLease:
    owner_installation_id = "owner-a"
    ownership_epoch = 1

    def __init__(self, lease_id: str = "lease-a", *, fencing_token: int = 42) -> None:
        self.lease_id = lease_id
        self.fencing_token = fencing_token
        self.released = False

    def release(self) -> None:
        self.released = True

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


class _FakeIntentSegmentStore(RecoveryIntentSegmentStore):
    def __init__(self, segments: tuple[RecoveryIntentSegment, ...] = ()) -> None:
        self.segments = {segment.segment_id: segment for segment in segments}

    def publish_intent_segment(self, segment: RecoveryIntentSegment) -> RecoveryIntentSegment:
        self.segments.setdefault(segment.segment_id, segment)
        return segment

    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None:
        return self.segments.get(segment_id)

    def load_latest_intent_segment_for_run_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> RecoveryIntentSegment | None:
        matches = tuple(
            segment
            for segment in self.segments.values()
            if segment.run_id == run_id and segment.run_target_id == run_target_id
        )
        if not matches:
            return None
        return max(matches, key=lambda segment: segment.segment_sequence)


class _FakeCatalogHandoffStore(FinalFileCatalogHandoffStore):
    def record_final_file_handoff(
        self,
        handoff: FinalFileCatalogHandoff,
    ) -> FinalFileCatalogHandoff:
        return handoff

    def load_final_file_handoff(self, handoff_id: str) -> FinalFileCatalogHandoff | None:
        return None


def _run(
    *,
    state: RunState,
    plan: SealedPlan,
    target_state: RunTargetState = RunTargetState.PENDING,
) -> StartedRun:
    target = _target(state=target_state)
    if target_state in {RunTargetState.REVALIDATING, RunTargetState.EXECUTING}:
        target = replace(
            target,
            last_lease_id="lease-a",
            last_ownership_epoch=1,
            last_fencing_token=42,
        )
    return StartedRun(
        run_id="run-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        plan_id=plan.plan_id,
        command_request_id="request-a",
        idempotency_key="idempotency-a",
        command_receipt_id="idempotency-a",
        logical_run_group_id="run-group-a",
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=state,
        app_version="0B-dev",
        plan_checksum=plan.plan_checksum,
        planned_operations=1,
        planned_bytes=128,
        targets=(target,),
    )


def _target(*, state: RunTargetState) -> StartedRunTarget:
    return StartedRunTarget(
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state=state,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        planned_operations=1,
        planned_bytes=128,
    )


def _operation(
    *,
    phase: RecoveryOperationPhase = RecoveryOperationPhase.CATALOG_RECORDED,
    last_error_code: str | None = None,
) -> RecoveryOperation:
    return replace(
        _planned_operation(),
        phase=phase,
        staging_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        catalog_handoff_id="final-file:run-a:op-a",
        expected_final_fingerprint_json='{"byte_count":128,"content_hash":"' + ("a" * 64) + '"}',
        last_error_code=last_error_code,
    )


def _planned_operation() -> RecoveryOperation:
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
        fencing_token=42,
        final_relative_path="Pictures/A.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
    )


def _commit_intent_operation() -> RecoveryOperation:
    return replace(
        _planned_operation(),
        phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        staging_object_id="op-a",
        intent_segment_id="run-a-target-0000-intent-000000",
        intent_ordinal=0,
        expected_staging_fingerprint_json=_fingerprint_json(),
        expected_final_fingerprint_json=_fingerprint_json(),
    )


def _old_target_preserved_operation() -> RecoveryOperation:
    return replace(
        _commit_intent_operation(),
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json='{"byte_count":128,"content_hash":"' + ("b" * 64) + '"}',
        version_object_id="op-a",
    )


def _intent_segment(
    *,
    segment_id: str = "run-a-target-0000-intent-000000",
    segment_sequence: int = 0,
    lease_id: str = "lease-a",
    fencing_token: int = 42,
    previous_segment_hash: str | None = None,
) -> RecoveryIntentSegment:
    return durable_recovery_intent_segment(
        segment_id=segment_id,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id=lease_id,
        fencing_token=fencing_token,
        segment_sequence=segment_sequence,
        relative_path=f"installations/owner-a/recovery/run-a/segment-{segment_sequence:06d}.intent.jsonl",
        schema_version=1,
        operation_count=1,
        byte_count=128,
        segment_hash="a" * 64 if segment_sequence == 0 else "b" * 64,
        previous_segment_hash=previous_segment_hash,
    )


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_source_endpoint(), _target_endpoint()),
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


def _fingerprint_json() -> str:
    return '{"byte_count":128,"content_hash":"' + ("a" * 64) + '"}'
