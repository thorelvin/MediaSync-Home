from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
    complete_run_target_success,
)


def test_complete_run_target_success_marks_single_target_and_run_completed() -> None:
    runs = _InMemoryRunStore(_executing_run())

    outcome = complete_run_target_success(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        completed_operations=1,
        completed_bytes=128,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.run_completed is True
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.SUCCEEDED
    assert outcome.target.completed_operations == 1
    assert outcome.target.completed_bytes == 128
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert loaded.targets[0] == outcome.target


def test_complete_run_target_success_keeps_run_executing_until_all_targets_succeed() -> None:
    runs = _InMemoryRunStore(
        _executing_run(
            targets=(
                _target(run_target_id="run-a-target-0000"),
                _target(run_target_id="run-a-target-0001"),
            )
        )
    )

    outcome = complete_run_target_success(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        completed_operations=1,
        completed_bytes=128,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.run_completed is False
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert [target.state for target in loaded.targets] == [
        RunTargetState.SUCCEEDED,
        RunTargetState.EXECUTING,
    ]


def test_complete_run_target_success_requires_executing_target() -> None:
    run = replace(
        _executing_run(),
        targets=(replace(_target(), state=RunTargetState.REVALIDATING),),
    )

    outcome = complete_run_target_success(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=_InMemoryRunStore(run),
        completed_operations=1,
        completed_bytes=128,
    )

    assert outcome.completed is False
    assert outcome.validation_codes == ("RUN_TARGET_NOT_EXECUTING",)


def test_complete_run_target_success_requires_matching_counts() -> None:
    outcome = complete_run_target_success(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=_InMemoryRunStore(_executing_run()),
        completed_operations=0,
        completed_bytes=128,
    )

    assert outcome.completed is False
    assert outcome.validation_codes == ("RUN_TARGET_COMPLETION_COUNTS_MISMATCH",)


def test_complete_run_target_success_reports_persistence_conflict() -> None:
    runs = _InMemoryRunStore(_executing_run(), conflict=True)

    outcome = complete_run_target_success(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        completed_operations=1,
        completed_bytes=128,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is False
    assert outcome.validation_codes == ("RUN_TARGET_COMPLETION_CONFLICT",)
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING


class _InMemoryRunStore(RunStore):
    def __init__(self, run: StartedRun | None, *, conflict: bool = False) -> None:
        self.run = run
        self.conflict = conflict

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

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        return None

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        return None

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
        return None

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
        return None

    def record_run_target_succeeded(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
    ) -> StartedRun | None:
        if self.conflict:
            return None
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
        run_state = (
            RunState.COMPLETED
            if all(target.state is RunTargetState.SUCCEEDED for target in updated_targets)
            else RunState.EXECUTING
        )
        self.run = replace(run, state=run_state, targets=tuple(updated_targets))
        return self.run


def _executing_run(
    *,
    targets: tuple[StartedRunTarget, ...] | None = None,
) -> StartedRun:
    return StartedRun(
        run_id="run-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        plan_id="plan-a",
        command_request_id="request-a",
        idempotency_key="idempotency-a",
        command_receipt_id="idempotency-a",
        logical_run_group_id="run-group-a",
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=RunState.EXECUTING,
        app_version="0B-dev",
        plan_checksum="a" * 64,
        planned_operations=1,
        planned_bytes=128,
        targets=targets or (_target(),),
    )


def _target(*, run_target_id: str = "run-a-target-0000") -> StartedRunTarget:
    return StartedRunTarget(
        run_target_id=run_target_id,
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state=RunTargetState.EXECUTING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        last_lease_id="lease-a",
        last_ownership_epoch=1,
        last_fencing_token=42,
        planned_operations=1,
        planned_bytes=128,
    )
