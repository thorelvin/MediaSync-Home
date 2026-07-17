from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.plans import (
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
    StartedRun,
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
        operations=(
            PlanOperation(
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
            ),
        ),
    )


def _start_command(plan: SealedPlan) -> StartRunCommand:
    return parse_start_run_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
    )
