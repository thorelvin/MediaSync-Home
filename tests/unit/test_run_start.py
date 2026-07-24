from __future__ import annotations

from dataclasses import replace

import pytest

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
from mediasync_home.application.runs import (
    RunIdFactory,
    RunIds,
    StartRunCommand,
    RunStartViolation,
    RunStore,
    RunState,
    RunTargetState,
    StartedRun,
    StartedRunTarget,
    parse_start_run_command,
    start_run_from_sealed_plan,
)


class InMemoryPlanStore(PlanStore):
    def __init__(self, plan: SealedPlan | None = None) -> None:
        self.plan = plan

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self.plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        if self.plan is None or self.plan.plan_id != plan_id:
            return None
        return self.plan


class InMemoryRunStore(RunStore):
    def __init__(self) -> None:
        self.runs: dict[str, StartedRun] = {}
        self.idempotency_keys: dict[str, str] = {}

    def save_started_run(self, run: StartedRun) -> None:
        self.runs[run.run_id] = run
        self.idempotency_keys[run.idempotency_key] = run.run_id

    def load_started_run(self, run_id: str) -> StartedRun | None:
        return self.runs.get(run_id)

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
        run_id = self.idempotency_keys.get(idempotency_key)
        if run_id is None:
            return None
        return self.runs[run_id]

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
        self.runs[run_id] = replace(run, state=RunState.PREFLIGHT, targets=tuple(updated_targets))
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
        if run is None or run.state is not RunState.PREFLIGHT:
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
        self.runs[run_id] = replace(run, targets=tuple(updated_targets))
        return recorded


class FixedRunIdFactory(RunIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_run_ids(self) -> RunIds:
        self.calls += 1
        return RunIds(run_id="run-a", logical_run_group_id="run-group-a")


def test_parse_start_run_command_requires_plan_id_and_checksum() -> None:
    with pytest.raises(RunStartViolation, match="START_RUN_REQUIRES_PLAN_ID"):
        parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={"plan_checksum": "a" * 64},
        )
    with pytest.raises(RunStartViolation, match="START_RUN_REQUIRES_PLAN_CHECKSUM"):
        parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={"plan_id": "plan-a", "plan_checksum": "not-a-checksum"},
        )


def test_start_run_from_sealed_plan_queues_checksum_bound_run() -> None:
    plan = _sealed_plan()
    runs = InMemoryRunStore()
    ids = FixedRunIdFactory()
    command = _start_command(plan)

    outcome = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=ids,
    )

    assert outcome.created is True
    assert outcome.idempotent_replay is False
    assert outcome.readiness.plan_runnable is True
    assert outcome.run is not None
    assert outcome.run.run_id == "run-a"
    assert outcome.run.state is RunState.QUEUED
    assert outcome.run.job_id == "job-a"
    assert outcome.run.job_revision_id == "job-rev-a"
    assert outcome.run.plan_id == "plan-a"
    assert outcome.run.plan_checksum == plan.plan_checksum
    assert outcome.run.planned_operations == 1
    assert outcome.run.planned_bytes == 128
    assert len(outcome.run.targets) == 1
    assert outcome.run.targets[0].run_target_id == "run-a-target-0000"
    assert outcome.run.targets[0].endpoint_id == "target-a"
    assert outcome.run.targets[0].planned_operations == 1
    assert outcome.run.targets[0].planned_bytes == 128
    assert outcome.run.summary["executor_pending"] is True
    assert runs.load_started_run("run-a") == outcome.run
    assert ids.calls == 1


def test_start_run_replays_existing_idempotency_key() -> None:
    plan = _sealed_plan()
    runs = InMemoryRunStore()
    ids = FixedRunIdFactory()
    command = _start_command(plan)

    first = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=ids,
    )
    second = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=ids,
    )

    assert first.created is True
    assert second.created is False
    assert second.idempotent_replay is True
    assert second.run == first.run
    assert ids.calls == 1


def test_start_run_can_use_trigger_occurrence_idempotency_key() -> None:
    plan = _sealed_plan()
    runs = InMemoryRunStore()
    ids = FixedRunIdFactory()
    first_command = StartRunCommand(
        request_id="trigger-delivery-a",
        idempotency_key="delivery-a",
        plan_id=plan.plan_id,
        plan_checksum=plan.plan_checksum,
        run_idempotency_key="trigger:occurrence-a",
        trigger_occurrence_id="trigger:occurrence-a",
    )
    retry_command = StartRunCommand(
        request_id="trigger-delivery-b",
        idempotency_key="delivery-b",
        plan_id=plan.plan_id,
        plan_checksum=plan.plan_checksum,
        run_idempotency_key="trigger:occurrence-a",
        trigger_occurrence_id="trigger:occurrence-a",
    )

    first = start_run_from_sealed_plan(
        command=first_command,
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=ids,
    )
    retry = start_run_from_sealed_plan(
        command=retry_command,
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=ids,
    )

    assert first.created is True
    assert retry.created is False
    assert retry.idempotent_replay is True
    assert first.run is not None
    assert first.run.idempotency_key == "trigger:occurrence-a"
    assert first.run.command_receipt_id == "delivery-a"
    assert first.run.trigger_occurrence_id == "trigger:occurrence-a"
    assert first.run.summary["trigger_occurrence_id"] == "trigger:occurrence-a"
    assert retry.run == first.run
    assert ids.calls == 1


def test_start_run_requires_existing_plan() -> None:
    runs = InMemoryRunStore()
    ids = FixedRunIdFactory()
    command = parse_start_run_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"plan_id": "missing", "plan_checksum": "a" * 64},
    )

    outcome = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(),
        runs=runs,
        id_factory=ids,
    )

    assert outcome.created is False
    assert outcome.run is None
    assert outcome.readiness.validation_codes == ("PLAN_NOT_FOUND",)
    assert ids.calls == 0


def test_start_run_requires_target_endpoint_binding() -> None:
    plan = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=(_copy_operation(),),
    )

    outcome = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory(),
    )

    assert outcome.created is False
    assert outcome.readiness.validation_codes == ("PLAN_REQUIRES_TARGET_ENDPOINT",)


def test_start_run_blocks_checksum_mismatch() -> None:
    plan = _sealed_plan()
    command = parse_start_run_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"plan_id": "plan-a", "plan_checksum": "a" * 64},
    )

    outcome = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory(),
    )

    assert outcome.created is False
    assert outcome.readiness.validation_codes == ("PLAN_CHECKSUM_MISMATCH",)


def test_start_run_blocks_tampered_stored_plan() -> None:
    plan = replace(_sealed_plan(), plan_checksum="a" * 64)
    command = _start_command(plan)

    outcome = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory(),
    )

    assert outcome.created is False
    assert outcome.readiness.validation_codes == ("PLAN_CHECKSUM_INVALID",)


def test_start_run_blocks_plan_with_blocked_risk() -> None:
    plan = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-blocked",
                operation_type=PlanOperationType.BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN,
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:block",
                target_precondition_kind=TargetPreconditionKind.NONE,
                reason_code="BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN",
                risk_level=PlanRiskLevel.BLOCKED,
            ),
        ),
    )

    outcome = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory(),
    )

    assert outcome.created is False
    assert outcome.readiness.validation_codes == ("PLAN_BLOCKED",)


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(_copy_operation(),),
    )


def _copy_operation() -> PlanOperation:
    return PlanOperation(
        operation_id="op-copy",
        operation_type=PlanOperationType.COPY_NEW,
        sequence_no=10,
        execution_phase=20,
        stable_order_key="020:Pictures/A.jpg",
        target_precondition_kind=TargetPreconditionKind.ABSENT,
        target_relative_path="Pictures/A.jpg",
        planned_bytes=128,
        reason_code="COPY_NEW",
        risk_level=PlanRiskLevel.LOW,
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


def _start_command(plan: SealedPlan) -> StartRunCommand:
    return parse_start_run_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
    )
