from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.run_executor import (
    RunExecutorQueueStore,
    execute_one_run_target_preflight_step,
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


def test_execute_one_run_target_preflight_step_claims_and_acquires_lease() -> None:
    lease = _FakeLiveLease()
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(True, lease, (), "acquired"))
    runs = _InMemoryRunStore(_queued_run())

    outcome = execute_one_run_target_preflight_step(runs=runs, leases=leases)

    loaded = runs.load_started_run("run-a")
    assert outcome.idle is False
    assert outcome.claimed is True
    assert outcome.lease_acquired is True
    assert outcome.validation_codes == ()
    assert outcome.lease is lease
    assert outcome.run_id == "run-a"
    assert outcome.run_target_id == "run-a-target-0000"
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.REVALIDATING
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0] == outcome.target
    assert leases.requests == (
        EndpointLeaseRequest(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
            resource_key="endpoint:target-a",
            required_owner_installation_id="owner-a",
            required_ownership_epoch=1,
        ),
    )


def test_execute_one_run_target_preflight_step_reports_idle_when_no_run_is_ready() -> None:
    outcome = execute_one_run_target_preflight_step(
        runs=_InMemoryRunStore(None),
        leases=_FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused")),
    )

    assert outcome.idle is True
    assert outcome.claimed is False
    assert outcome.lease_acquired is False
    assert outcome.validation_codes == ()
    assert outcome.run_id is None
    assert outcome.run_target_id is None


def test_execute_one_run_target_preflight_step_surfaces_preflight_failure() -> None:
    run = replace(_queued_run(), targets=(replace(_target(), lease_resource_key=None),))
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused"))

    outcome = execute_one_run_target_preflight_step(
        runs=_InMemoryRunStore(run),
        leases=leases,
    )

    assert outcome.idle is False
    assert outcome.claimed is False
    assert outcome.lease_acquired is False
    assert outcome.run_id == "run-a"
    assert outcome.run_target_id == "run-a-target-0000"
    assert outcome.validation_codes == ("RUN_TARGET_REQUIRES_LEASE_RESOURCE_KEY",)
    assert leases.requests == ()


def test_execute_one_run_target_preflight_step_reports_claim_conflict() -> None:
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused"))

    outcome = execute_one_run_target_preflight_step(
        runs=_InMemoryRunStore(_queued_run(), preflight_conflict=True),
        leases=leases,
    )

    assert outcome.claimed is False
    assert outcome.lease_acquired is False
    assert outcome.validation_codes == ("RUN_TARGET_PREFLIGHT_CLAIM_CONFLICT",)
    assert leases.requests == ()


def test_execute_one_run_target_preflight_step_reports_lease_unavailable_after_claim() -> None:
    runs = _InMemoryRunStore(_queued_run())
    leases = _FakeLeaseAuthority(
        EndpointLeaseAttempt(
            acquired=False,
            lease=None,
            validation_codes=("ENDPOINT_LEASE_UNAVAILABLE",),
            next_action="Wait for the current endpoint writer to release the lock.",
        )
    )

    outcome = execute_one_run_target_preflight_step(runs=runs, leases=leases)

    loaded = runs.load_started_run("run-a")
    assert outcome.idle is False
    assert outcome.claimed is True
    assert outcome.lease_acquired is False
    assert outcome.validation_codes == ("ENDPOINT_LEASE_UNAVAILABLE",)
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.ACQUIRING_LEASE
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.ACQUIRING_LEASE


class _InMemoryRunStore(RunExecutorQueueStore):
    def __init__(self, run: StartedRun | None, *, preflight_conflict: bool = False) -> None:
        self.run = run
        self.preflight_conflict = preflight_conflict

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
        if self.preflight_conflict:
            return None
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
        self.run = replace(run, targets=tuple(updated_targets))
        return recorded


class _FakeLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, attempt: EndpointLeaseAttempt) -> None:
        self._attempt = attempt
        self.requests: tuple[EndpointLeaseRequest, ...] = ()

    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt:
        self.requests = (*self.requests, request)
        return self._attempt


class _FakeLiveLease:
    lease_id = "lease-a"
    owner_installation_id = "owner-a"
    ownership_epoch = 1
    fencing_token = 42

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def _queued_run() -> StartedRun:
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
        state=RunState.QUEUED,
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
        state=RunTargetState.PENDING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        planned_operations=1,
        planned_bytes=128,
    )
