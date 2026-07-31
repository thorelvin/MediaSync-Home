from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
    acquire_run_target_lease,
)


def test_acquire_run_target_lease_records_live_lease_and_returns_handle() -> None:
    lease = _FakeLiveLease()
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(True, lease, (), "acquired"))
    runs = _InMemoryRunStore(_preflight_run())

    outcome = acquire_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        leases=leases,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.acquired is True
    assert outcome.validation_codes == ()
    assert outcome.lease is lease
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.REVALIDATING
    assert outcome.target.last_lease_id == "lease-a"
    assert outcome.target.last_ownership_epoch == 1
    assert outcome.target.last_fencing_token == 42
    assert loaded is not None
    assert loaded.targets[0] == outcome.target
    assert lease.released is False
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


def test_acquire_run_target_lease_requires_preflight_run() -> None:
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused"))
    runs = _InMemoryRunStore(replace(_preflight_run(), state=RunState.QUEUED))

    outcome = acquire_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        leases=leases,
    )

    assert outcome.acquired is False
    assert outcome.validation_codes == ("RUN_NOT_IN_PREFLIGHT",)
    assert leases.requests == ()


def test_acquire_run_target_lease_requires_acquiring_target_state() -> None:
    run = replace(
        _preflight_run(),
        targets=(replace(_target(), state=RunTargetState.PENDING),),
    )
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused"))

    outcome = acquire_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=_InMemoryRunStore(run),
        leases=leases,
    )

    assert outcome.acquired is False
    assert outcome.validation_codes == ("RUN_TARGET_NOT_ACQUIRING_LEASE",)
    assert leases.requests == ()


def test_acquire_run_target_lease_records_waiting_target_when_unavailable() -> None:
    runs = _InMemoryRunStore(_preflight_run())
    leases = _FakeLeaseAuthority(
        EndpointLeaseAttempt(
            acquired=False,
            lease=None,
            validation_codes=("ENDPOINT_LEASE_UNAVAILABLE",),
            next_action="Wait for the current endpoint writer to release the lock.",
        )
    )

    outcome = acquire_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        leases=leases,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.acquired is False
    assert outcome.validation_codes == ()
    assert loaded is not None
    assert loaded.targets[0].state is RunTargetState.WAITING_FOR_ENDPOINT
    assert loaded.targets[0].last_lease_id is None


def test_acquire_run_target_lease_releases_handle_when_recording_conflicts() -> None:
    lease = _FakeLiveLease()
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(True, lease, (), "acquired"))
    runs = _InMemoryRunStore(_preflight_run(), record_conflict=True)

    outcome = acquire_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=runs,
        leases=leases,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.acquired is False
    assert outcome.validation_codes == ("RUN_TARGET_LEASE_RECORD_CONFLICT",)
    assert outcome.lease is None
    assert lease.released is True
    assert loaded is not None
    assert loaded.targets[0].state is RunTargetState.ACQUIRING_LEASE


def test_acquire_run_target_lease_rejects_success_without_live_handle() -> None:
    leases = _FakeLeaseAuthority(
        EndpointLeaseAttempt(
            acquired=True,
            lease=None,
            validation_codes=(),
            next_action="invalid",
        )
    )

    outcome = acquire_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        runs=_InMemoryRunStore(_preflight_run()),
        leases=leases,
    )

    assert outcome.acquired is False
    assert outcome.validation_codes == ("RUN_TARGET_ENDPOINT_LEASE_INVALID",)


class _InMemoryRunStore(RunStore):
    def __init__(
        self, run: StartedRun | None, *, record_conflict: bool = False
    ) -> None:
        self.run = run
        self.record_conflict = record_conflict

    def save_started_run(self, run: StartedRun) -> None:
        self.run = run

    def load_started_run(self, run_id: str) -> StartedRun | None:
        if self.run is not None and self.run.run_id == run_id:
            return self.run
        return None

    def load_started_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> StartedRun | None:
        if self.run is not None and self.run.idempotency_key == idempotency_key:
            return self.run
        return None

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None:
            return None
        return next(
            (
                target
                for target in run.targets
                if target.state is RunTargetState.PENDING
            ),
            None,
        )

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
        if self.record_conflict:
            return None
        run = self.load_started_run(run_id)
        if run is None:
            return None
        updated_targets: list[StartedRunTarget] = []
        recorded: StartedRunTarget | None = None
        for target in run.targets:
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.ACQUIRING_LEASE
            ):
                if target.required_owner_installation_id not in (
                    None,
                    owner_installation_id,
                ):
                    return None
                if target.required_ownership_epoch not in (None, ownership_epoch):
                    return None
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

    def record_run_target_waiting_for_endpoint(
        self,
        *,
        run_id: str,
        run_target_id: str,
        expected_state: RunTargetState,
        reason_code: str,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or not reason_code.strip():
            return None
        target = next(
            (
                target
                for target in run.targets
                if target.run_target_id == run_target_id
                and target.state is expected_state
            ),
            None,
        )
        if target is None:
            return None
        waiting = replace(
            target,
            state=RunTargetState.WAITING_FOR_ENDPOINT,
            last_lease_id=None,
            last_ownership_epoch=None,
            last_fencing_token=None,
        )
        self.run = replace(
            run,
            targets=tuple(
                waiting if item.run_target_id == run_target_id else item
                for item in run.targets
            ),
        )
        return waiting


class _FakeLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, attempt: EndpointLeaseAttempt) -> None:
        self._attempt = attempt
        self._requests: list[EndpointLeaseRequest] = []

    @property
    def requests(self) -> tuple[EndpointLeaseRequest, ...]:
        return tuple(self._requests)

    def acquire_endpoint_lease(
        self, request: EndpointLeaseRequest
    ) -> EndpointLeaseAttempt:
        self._requests.append(request)
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
        state=RunTargetState.ACQUIRING_LEASE,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        planned_operations=1,
        planned_bytes=128,
    )
