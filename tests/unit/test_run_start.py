from __future__ import annotations

from dataclasses import replace

import pytest

from tests.support.source_preconditions import source_precondition_json

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
    RunControlCommand,
    StartRunCommand,
    RunStartViolation,
    RunStore,
    RunState,
    RunTargetState,
    StartedRun,
    StartedRunTarget,
    parse_start_run_command,
    parse_run_control_command,
    request_run_stop_after_active_file,
    request_run_pause,
    resume_paused_run,
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
        self.stop_requests: set[str] = set()

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

    def request_run_pause(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {
            RunState.CREATED,
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
        }:
            return None
        updated = replace(run, state=RunState.PAUSING)
        self.runs[run_id] = updated
        return updated

    def resume_paused_run(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.PAUSED:
            return None
        updated = replace(
            run,
            state=RunState.QUEUED,
            targets=tuple(
                replace(
                    target,
                    state=RunTargetState.PENDING,
                    last_lease_id=None,
                    last_ownership_epoch=None,
                    last_fencing_token=None,
                )
                if target.state is RunTargetState.PAUSED
                else target
                for target in run.targets
            ),
        )
        self.runs[run_id] = updated
        return updated

    def request_run_stop_after_active_file(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {
            RunState.CREATED,
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
            RunState.PAUSING,
            RunState.PAUSED,
        }:
            return None
        self.stop_requests.add(run_id)
        return run


class FixedRunIdFactory(RunIdFactory):
    def __init__(
        self,
        run_id: str = "run-a",
        logical_run_group_id: str = "run-group-a",
    ) -> None:
        self.calls = 0
        self.run_id = run_id
        self.logical_run_group_id = logical_run_group_id

    def new_run_ids(self) -> RunIds:
        self.calls += 1
        return RunIds(
            run_id=self.run_id,
            logical_run_group_id=self.logical_run_group_id,
        )


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


def test_parse_start_run_command_validates_retry_target_scope() -> None:
    payload = {"plan_id": "plan-a", "plan_checksum": "a" * 64}
    with pytest.raises(RunStartViolation, match="START_RUN_RETRY_REQUIRES_TARGET_SCOPE"):
        parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={**payload, "resumed_from_run_id": "run-source"},
        )
    with pytest.raises(RunStartViolation, match="START_RUN_TARGET_SCOPE_DUPLICATE"):
        parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={**payload, "target_endpoint_ids": ["target-a", "target-a"]},
        )

    parsed = parse_start_run_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={
            **payload,
            "target_endpoint_ids": ["target-b"],
            "resumed_from_run_id": "run-source",
        },
    )

    assert parsed.target_endpoint_ids == ("target-b",)
    assert parsed.resumed_from_run_id == "run-source"


def test_pause_request_waits_for_executor_boundary_and_resume_requeues_target() -> None:
    plan = _sealed_plan()
    runs = InMemoryRunStore()
    started = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=FixedRunIdFactory(),
    ).run
    assert started is not None
    executing = replace(
        started,
        state=RunState.EXECUTING,
        targets=(
            replace(
                started.targets[0],
                state=RunTargetState.EXECUTING,
                last_lease_id="lease-a",
                last_ownership_epoch=1,
                last_fencing_token=7,
            ),
        ),
    )
    runs.runs[executing.run_id] = executing
    pause_command = RunControlCommand(
        request_id="pause-request",
        idempotency_key="pause-key",
        run_id=executing.run_id,
    )

    pause = request_run_pause(command=pause_command, runs=runs)

    assert pause.applied is True
    assert pause.run is not None
    assert pause.run.state is RunState.PAUSING
    assert pause.run.targets[0].state is RunTargetState.EXECUTING

    paused = replace(
        pause.run,
        state=RunState.PAUSED,
        targets=(
            replace(
                pause.run.targets[0],
                state=RunTargetState.PAUSED,
                last_lease_id=None,
                last_ownership_epoch=None,
                last_fencing_token=None,
            ),
        ),
    )
    runs.runs[paused.run_id] = paused
    resume = resume_paused_run(
        command=RunControlCommand(
            request_id="resume-request",
            idempotency_key="resume-key",
            run_id=paused.run_id,
        ),
        runs=runs,
    )

    assert resume.applied is True
    assert resume.run is not None
    assert resume.run.state is RunState.QUEUED
    assert resume.run.targets[0].state is RunTargetState.PENDING
    assert resume.run.targets[0].last_lease_id is None


def test_stop_after_active_file_records_request_without_terminalizing_run() -> None:
    plan = _sealed_plan()
    runs = InMemoryRunStore()
    started = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=FixedRunIdFactory(),
    ).run
    assert started is not None
    executing = replace(started, state=RunState.EXECUTING)
    runs.runs[executing.run_id] = executing

    outcome = request_run_stop_after_active_file(
        command=RunControlCommand(
            request_id="stop-request",
            idempotency_key="stop-key",
            run_id=executing.run_id,
        ),
        runs=runs,
    )

    assert outcome.applied
    assert outcome.run is not None
    assert outcome.run.state is RunState.EXECUTING
    assert runs.stop_requests == {executing.run_id}


def test_run_control_parser_and_state_preconditions_fail_closed() -> None:
    with pytest.raises(RunStartViolation, match="RUN_CONTROL_REQUIRES_RUN_ID"):
        parse_run_control_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={},
        )
    runs = InMemoryRunStore()
    missing = request_run_pause(
        command=RunControlCommand("request-a", "idempotency-a", "missing"),
        runs=runs,
    )
    assert missing.applied is False
    assert missing.validation_codes == ("RUN_NOT_FOUND",)


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


def test_retry_run_queues_only_failed_target_with_original_lineage() -> None:
    source_plan = _multi_target_plan("plan-source")
    retry_plan = _multi_target_plan("plan-retry")
    runs = InMemoryRunStore()
    source = start_run_from_sealed_plan(
        command=_start_command(source_plan),
        plans=InMemoryPlanStore(source_plan),
        runs=runs,
        id_factory=FixedRunIdFactory("run-source", "run-group-original"),
    ).run
    assert source is not None
    source = replace(
        source,
        state=RunState.PARTIAL_FAILURE,
        targets=(
            replace(
                source.targets[0],
                state=RunTargetState.SUCCEEDED,
                completed_operations=1,
                completed_bytes=128,
            ),
            replace(
                source.targets[1],
                state=RunTargetState.FAILED,
                completed_operations=1,
                completed_bytes=64,
            ),
        ),
    )
    runs.runs[source.run_id] = source
    command = parse_start_run_command(
        request_id="request-retry",
        idempotency_key="idempotency-retry",
        payload={
            "plan_id": retry_plan.plan_id,
            "plan_checksum": retry_plan.plan_checksum,
            "target_endpoint_ids": ["target-b"],
            "resumed_from_run_id": source.run_id,
        },
    )

    outcome = start_run_from_sealed_plan(
        command=command,
        plans=InMemoryPlanStore(retry_plan),
        runs=runs,
        id_factory=FixedRunIdFactory("run-retry", "run-group-unused"),
    )

    assert outcome.created is True
    assert outcome.run is not None
    assert outcome.run.resumed_from_run_id == "run-source"
    assert outcome.run.logical_run_group_id == "run-group-original"
    assert outcome.run.planned_operations == 1
    assert outcome.run.planned_bytes == 256
    assert tuple(target.endpoint_id for target in outcome.run.targets) == ("target-b",)
    assert outcome.run.summary["scope"] == "TARGET_RETRY"
    assert outcome.run.summary["target_endpoint_ids"] == ["target-b"]


def test_retry_run_rejects_successful_target_and_stale_partial_plan() -> None:
    plan = _multi_target_plan("plan-source")
    runs = InMemoryRunStore()
    source = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=FixedRunIdFactory("run-source", "run-group-original"),
    ).run
    assert source is not None
    source = replace(
        source,
        state=RunState.PARTIAL_FAILURE,
        targets=(
            replace(source.targets[0], state=RunTargetState.SUCCEEDED),
            replace(
                source.targets[1],
                state=RunTargetState.FAILED,
                completed_operations=1,
            ),
        ),
    )
    runs.runs[source.run_id] = source

    successful_target = start_run_from_sealed_plan(
        command=replace(
            _start_command(plan),
            idempotency_key="retry-successful",
            target_endpoint_ids=("target-a",),
            resumed_from_run_id=source.run_id,
        ),
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=FixedRunIdFactory("run-retry-a"),
    )
    stale_partial = start_run_from_sealed_plan(
        command=replace(
            _start_command(plan),
            idempotency_key="retry-stale",
            target_endpoint_ids=("target-b",),
            resumed_from_run_id=source.run_id,
        ),
        plans=InMemoryPlanStore(plan),
        runs=runs,
        id_factory=FixedRunIdFactory("run-retry-b"),
    )

    assert successful_target.run is None
    assert successful_target.readiness.validation_codes == (
        "RUN_RETRY_TARGET_NOT_FAILED",
    )
    assert stale_partial.run is None
    assert stale_partial.readiness.validation_codes == (
        "RUN_RETRY_REQUIRES_FRESH_PLAN",
    )


def test_target_scope_does_not_inherit_another_targets_blocked_operation() -> None:
    base = _multi_target_plan("plan-scoped")
    plan = seal_plan(
        plan_id=base.plan_id,
        analysis_id=base.analysis_id,
        job_id=base.job_id,
        job_revision_id=base.job_revision_id,
        endpoints=base.endpoints,
        operations=(
            replace(base.operations[0], risk_level=PlanRiskLevel.BLOCKED),
            base.operations[1],
        ),
    )

    full = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory("run-full"),
    )
    scoped = start_run_from_sealed_plan(
        command=replace(
            _start_command(plan),
            idempotency_key="scoped-target-b",
            target_endpoint_ids=("target-b",),
        ),
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory("run-scoped"),
    )

    assert full.run is None
    assert full.readiness.validation_codes == ("PLAN_BLOCKED",)
    assert scoped.created is True
    assert scoped.run is not None
    assert tuple(target.endpoint_id for target in scoped.run.targets) == ("target-b",)


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


def test_start_run_accepts_journaled_directory_operations() -> None:
    plan = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-directory",
                operation_type=PlanOperationType.CREATE_DIRECTORY,
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:Pictures",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures",
                reason_code="CREATE_MISSING_DIRECTORY",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )

    outcome = start_run_from_sealed_plan(
        command=_start_command(plan),
        plans=InMemoryPlanStore(plan),
        runs=InMemoryRunStore(),
        id_factory=FixedRunIdFactory(),
    )

    assert outcome.created is True
    assert outcome.run is not None
    assert outcome.readiness.validation_codes == ()


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(_copy_operation(),),
    )


def _multi_target_plan(plan_id: str) -> SealedPlan:
    return seal_plan(
        plan_id=plan_id,
        analysis_id=f"analysis-{plan_id}",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _target_endpoint(),
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
                planned_bytes=256,
            ),
        ),
        operations=(
            replace(
                _copy_operation(),
                operation_id="op-copy-a",
                target_endpoint_id="target-a",
            ),
            replace(
                _copy_operation(),
                operation_id="op-copy-b",
                sequence_no=20,
                stable_order_key="021:Pictures/B.jpg",
                target_endpoint_id="target-b",
                target_relative_path="Pictures/B.jpg",
                source_relative_path="Pictures/B.jpg",
                source_precondition_json=source_precondition_json(
                    relative_path="Pictures/B.jpg",
                    size_bytes=256,
                ),
                planned_bytes=256,
            ),
        ),
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
        source_relative_path="Pictures/A.jpg",
        source_precondition_json=source_precondition_json(),
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
        endpoint_generation=1,
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
