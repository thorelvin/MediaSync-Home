from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
    start_run_target_execution,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_start_run_target_execution_issues_permit_and_marks_target_executing() -> None:
    lease = _FakeLiveLease()
    runs = _InMemoryRunStore(_preflight_run())

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        lease=lease,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.started is True
    assert outcome.validation_codes == ()
    assert outcome.mutation_permit is not None
    assert outcome.mutation_permit.run_target_id == "run-a-target-0000"
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.EXECUTING
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0] == outcome.target
    assert lease.issue_calls == 1


def test_start_run_target_execution_allows_additional_revalidated_targets() -> None:
    lease = _FakeLiveLease()
    runs = _InMemoryRunStore(replace(_preflight_run(), state=RunState.EXECUTING))

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        lease=lease,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.started is True
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING


def test_start_run_target_execution_requires_revalidating_target() -> None:
    lease = _FakeLiveLease()
    run = replace(
        _preflight_run(),
        targets=(replace(_target(), state=RunTargetState.ACQUIRING_LEASE),),
    )

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=_InMemoryRunStore(run),
        lease=lease,
    )

    assert outcome.started is False
    assert outcome.validation_codes == ("RUN_TARGET_NOT_REVALIDATING",)
    assert lease.issue_calls == 0


def test_start_run_target_execution_rejects_stale_retained_lease() -> None:
    lease = _FakeLiveLease(lease_id="stale-lease")
    runs = _InMemoryRunStore(_preflight_run())

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        lease=lease,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.started is False
    assert outcome.validation_codes == ("RUN_TARGET_RETAINED_LEASE_MISMATCH",)
    assert lease.issue_calls == 0
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.REVALIDATING


def test_start_run_target_execution_reports_lost_lease_without_mutating() -> None:
    lease = _FakeLiveLease(
        issue_error=_PermitIssueError(
            "MUTATION_PERMIT_LEASE_LOST",
            "Stop mutation work and enter recovery.",
        )
    )
    runs = _InMemoryRunStore(_preflight_run())

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        lease=lease,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.started is False
    assert outcome.validation_codes == ("MUTATION_PERMIT_LEASE_LOST",)
    assert outcome.next_action == "Stop mutation work and enter recovery."
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.REVALIDATING


def test_start_run_target_execution_rejects_mismatched_permit() -> None:
    lease = _FakeLiveLease(permit=_permit(run_target_id="other-target"))
    runs = _InMemoryRunStore(_preflight_run())

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        lease=lease,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.started is False
    assert outcome.validation_codes == ("RUN_TARGET_MUTATION_PERMIT_MISMATCH",)
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.REVALIDATING


def test_start_run_target_execution_reports_persistence_conflict() -> None:
    lease = _FakeLiveLease()
    runs = _InMemoryRunStore(_preflight_run(), record_conflict=True)

    outcome = start_run_target_execution(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        lease=lease,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.started is False
    assert outcome.validation_codes == ("RUN_TARGET_EXECUTION_START_CONFLICT",)
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.REVALIDATING


class _InMemoryRunStore(RunStore):
    def __init__(self, run: StartedRun | None, *, record_conflict: bool = False) -> None:
        self.run = run
        self.record_conflict = record_conflict

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
        if self.record_conflict:
            return None
        run = self.load_started_run(run_id)
        if run is None or run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
            return None
        updated_targets: list[StartedRunTarget] = []
        started: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.REVALIDATING:
                if (
                    target.last_lease_id != lease_id
                    or target.last_ownership_epoch != ownership_epoch
                    or target.last_fencing_token != fencing_token
                ):
                    return None
                if target.required_owner_installation_id not in (None, owner_installation_id):
                    return None
                if target.required_ownership_epoch not in (None, ownership_epoch):
                    return None
                started = replace(target, state=RunTargetState.EXECUTING)
                updated_targets.append(started)
            else:
                updated_targets.append(target)
        if started is None:
            return None
        self.run = replace(run, state=RunState.EXECUTING, targets=tuple(updated_targets))
        return started


class _PermitIssueError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class _FakeLiveLease:
    owner_installation_id = "owner-a"
    ownership_epoch = 1
    fencing_token = 42

    def __init__(
        self,
        *,
        lease_id: str = "lease-a",
        permit: MutationPermit | None = None,
        issue_error: RuntimeError | None = None,
    ) -> None:
        self.lease_id = lease_id
        self._permit = permit
        self._issue_error = issue_error
        self.issue_calls = 0
        self.released = False

    def issue_mutation_permit(self) -> MutationPermit:
        self.issue_calls += 1
        if self._issue_error is not None:
            raise self._issue_error
        return self._permit or _permit(lease_id=self.lease_id)

    def release(self) -> None:
        self.released = True


def _preflight_run() -> StartedRun:
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
        state=RunState.PREFLIGHT,
        app_version="0B-dev",
        plan_checksum="a" * 64,
        planned_operations=1,
        planned_bytes=128,
        targets=(_target(),),
    )


def _target() -> StartedRunTarget:
    return StartedRunTarget(
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state=RunTargetState.REVALIDATING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        last_lease_id="lease-a",
        last_ownership_epoch=1,
        last_fencing_token=42,
        planned_operations=1,
        planned_bytes=128,
    )


def _permit(
    *,
    lease_id: str = "lease-a",
    run_target_id: str = "run-a-target-0000",
) -> MutationPermit:
    return _issue_mutation_permit(
        lease_id=lease_id,
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=42,
        run_id="run-a",
        run_target_id=run_target_id,
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
