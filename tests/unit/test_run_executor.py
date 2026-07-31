from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.run_executor import (
    MAX_RUN_EXECUTOR_PUMP_STEPS,
    HeldRunTargetLeaseRegistry,
    RunExecutorQueueStore,
    RunExecutorPumpStopReason,
    RunExecutorViolation,
    execute_bounded_run_executor_preflight_pump,
    execute_one_executing_run_target_lease_reacquire_step,
    execute_one_run_target_execution_start_step,
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
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


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


def test_execute_one_run_target_preflight_step_reports_idle_when_no_run_is_ready() -> (
    None
):
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


def test_execute_one_run_target_preflight_step_waits_when_lease_is_unavailable() -> (
    None
):
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
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.WAITING_FOR_ENDPOINT
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.WAITING_FOR_ENDPOINT


def test_bounded_run_executor_pump_retains_acquired_leases_until_idle() -> None:
    first_lease = _FakeLiveLease("lease-a")
    second_lease = _FakeLiveLease("lease-b")
    leases = _SequencedLeaseAuthority(
        (
            EndpointLeaseAttempt(True, first_lease, (), "first acquired"),
            EndpointLeaseAttempt(True, second_lease, (), "second acquired"),
        )
    )
    runs = _InMemoryRunStore(
        _queued_run(
            targets=(
                _target(run_target_id="run-a-target-0000", endpoint_id="target-a"),
                _target(run_target_id="run-a-target-0001", endpoint_id="target-b"),
            )
        )
    )
    registry = HeldRunTargetLeaseRegistry()

    outcome = execute_bounded_run_executor_preflight_pump(
        runs=runs,
        leases=leases,
        lease_registry=registry,
        max_steps=3,
    )

    assert outcome.steps_attempted == 3
    assert outcome.leases_retained == 2
    assert outcome.stopped_reason is RunExecutorPumpStopReason.IDLE
    assert registry.retained_count == 2
    assert (
        registry.load_retained_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        )
        is first_lease
    )
    assert (
        registry.load_retained_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0001",
        )
        is second_lease
    )
    assert first_lease.released is False
    assert second_lease.released is False
    loaded = runs.load_started_run("run-a")
    assert loaded is not None
    assert [target.state for target in loaded.targets] == [
        RunTargetState.REVALIDATING,
        RunTargetState.REVALIDATING,
    ]


def test_bounded_run_executor_pump_stops_on_blocked_step() -> None:
    leases = _FakeLeaseAuthority(
        EndpointLeaseAttempt(
            acquired=False,
            lease=None,
            validation_codes=("ENDPOINT_CONTROL_AREA_MISSING",),
            next_action="Review the endpoint before continuing.",
        )
    )
    registry = HeldRunTargetLeaseRegistry()

    outcome = execute_bounded_run_executor_preflight_pump(
        runs=_InMemoryRunStore(_queued_run()),
        leases=leases,
        lease_registry=registry,
        max_steps=5,
    )

    assert outcome.steps_attempted == 1
    assert outcome.leases_retained == 0
    assert outcome.stopped_reason is RunExecutorPumpStopReason.BLOCKED
    assert outcome.validation_codes == ("ENDPOINT_CONTROL_AREA_MISSING",)
    assert registry.retained_count == 0


def test_bounded_run_executor_pump_stops_at_step_limit_with_retained_lease() -> None:
    lease = _FakeLiveLease("lease-a")
    registry = HeldRunTargetLeaseRegistry()

    outcome = execute_bounded_run_executor_preflight_pump(
        runs=_InMemoryRunStore(
            _queued_run(
                targets=(
                    _target(run_target_id="run-a-target-0000", endpoint_id="target-a"),
                    _target(run_target_id="run-a-target-0001", endpoint_id="target-b"),
                )
            )
        ),
        leases=_SequencedLeaseAuthority(
            (EndpointLeaseAttempt(True, lease, (), "acquired"),)
        ),
        lease_registry=registry,
        max_steps=1,
    )

    assert outcome.steps_attempted == 1
    assert outcome.leases_retained == 1
    assert outcome.stopped_reason is RunExecutorPumpStopReason.STEP_LIMIT_REACHED
    assert registry.retained_count == 1
    assert lease.released is False


def test_bounded_run_executor_pump_requires_bounded_step_limit() -> None:
    with pytest.raises(
        RunExecutorViolation, match="RUN_EXECUTOR_PUMP_REQUIRES_POSITIVE_STEP_LIMIT"
    ):
        execute_bounded_run_executor_preflight_pump(
            runs=_InMemoryRunStore(None),
            leases=_FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused")),
            lease_registry=HeldRunTargetLeaseRegistry(),
            max_steps=0,
        )
    with pytest.raises(
        RunExecutorViolation, match="RUN_EXECUTOR_PUMP_STEP_LIMIT_TOO_LARGE"
    ):
        execute_bounded_run_executor_preflight_pump(
            runs=_InMemoryRunStore(None),
            leases=_FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused")),
            lease_registry=HeldRunTargetLeaseRegistry(),
            max_steps=MAX_RUN_EXECUTOR_PUMP_STEPS + 1,
        )


def test_execute_one_run_target_execution_start_step_issues_permit() -> None:
    lease = _FakeLiveLease("lease-a")
    registry = HeldRunTargetLeaseRegistry()
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=lease,
    )
    runs = _InMemoryRunStore(_preflighted_run())

    outcome = execute_one_run_target_execution_start_step(
        runs=runs,
        lease_registry=registry,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.idle is False
    assert outcome.execution_started is True
    assert outcome.validation_codes == ()
    assert outcome.mutation_permit is not None
    assert outcome.mutation_permit.run_target_id == "run-a-target-0000"
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.EXECUTING
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert registry.retained_count == 1
    assert lease.released is False


def test_execute_one_run_target_execution_start_step_reports_idle_without_revalidating_target() -> (
    None
):
    outcome = execute_one_run_target_execution_start_step(
        runs=_InMemoryRunStore(_queued_run()),
        lease_registry=HeldRunTargetLeaseRegistry(),
    )

    assert outcome.idle is True
    assert outcome.execution_started is False
    assert outcome.validation_codes == ()


def test_execute_one_run_target_execution_start_step_requires_retained_lease() -> None:
    runs = _InMemoryRunStore(_preflighted_run())

    outcome = execute_one_run_target_execution_start_step(
        runs=runs,
        lease_registry=HeldRunTargetLeaseRegistry(),
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.idle is False
    assert outcome.execution_started is False
    assert outcome.validation_codes == ("RUN_TARGET_RETAINED_LEASE_NOT_FOUND",)
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.REVALIDATING
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT


def test_execute_one_run_target_execution_start_step_reacquires_missing_retained_lease() -> (
    None
):
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(True, lease, (), "reacquired"))
    registry = HeldRunTargetLeaseRegistry()
    runs = _InMemoryRunStore(_preflighted_run())

    outcome = execute_one_run_target_execution_start_step(
        runs=runs,
        lease_registry=registry,
        leases=leases,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.execution_started is True
    assert outcome.validation_codes == ()
    assert outcome.mutation_permit is not None
    assert outcome.mutation_permit.lease_id == "lease-b"
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING
    assert loaded.targets[0].last_lease_id == "lease-b"
    assert loaded.targets[0].last_fencing_token == 43
    assert (
        registry.load_retained_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        )
        is lease
    )
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


def test_execute_one_run_target_execution_start_step_waits_when_reacquire_unavailable() -> (
    None
):
    leases = _FakeLeaseAuthority(
        EndpointLeaseAttempt(
            acquired=False,
            lease=None,
            validation_codes=("ENDPOINT_LEASE_UNAVAILABLE",),
            next_action="Wait for the current endpoint writer to release the lock.",
        )
    )
    registry = HeldRunTargetLeaseRegistry()
    runs = _InMemoryRunStore(_preflighted_run())

    outcome = execute_one_run_target_execution_start_step(
        runs=runs,
        lease_registry=registry,
        leases=leases,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.execution_started is False
    assert outcome.validation_codes == ()
    assert outcome.next_action == (
        "Target is waiting safely and will be retried on a later maintenance pass."
    )
    assert registry.retained_count == 0
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.WAITING_FOR_ENDPOINT
    assert loaded.targets[0].last_lease_id is None


def test_execute_one_run_target_execution_start_step_releases_stale_retained_lease() -> (
    None
):
    stale_lease = _FakeLiveLease("stale-lease")
    registry = HeldRunTargetLeaseRegistry()
    registry.retain_run_target_lease(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease=stale_lease,
    )

    outcome = execute_one_run_target_execution_start_step(
        runs=_InMemoryRunStore(_preflighted_run()),
        lease_registry=registry,
    )

    assert outcome.execution_started is False
    assert outcome.validation_codes == ("RUN_TARGET_RETAINED_LEASE_MISMATCH",)
    assert stale_lease.released is True
    assert registry.retained_count == 0


def test_execute_one_executing_run_target_lease_reacquire_step_retains_new_lease() -> (
    None
):
    lease = _FakeLiveLease("lease-b", fencing_token=43)
    leases = _FakeLeaseAuthority(EndpointLeaseAttempt(True, lease, (), "reacquired"))
    registry = HeldRunTargetLeaseRegistry()
    runs = _InMemoryRunStore(_executing_run())

    outcome = execute_one_executing_run_target_lease_reacquire_step(
        runs=runs,
        leases=leases,
        lease_registry=registry,
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.idle is False
    assert outcome.reacquired is True
    assert outcome.validation_codes == ()
    assert outcome.lease is lease
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert loaded.targets[0].state is RunTargetState.EXECUTING
    assert loaded.targets[0].last_lease_id == "lease-b"
    assert loaded.targets[0].last_fencing_token == 43
    assert (
        registry.load_retained_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        )
        is lease
    )


def test_execute_one_executing_run_target_lease_reacquire_step_reports_idle_without_executing_target() -> (
    None
):
    outcome = execute_one_executing_run_target_lease_reacquire_step(
        runs=_InMemoryRunStore(_preflighted_run()),
        leases=_FakeLeaseAuthority(EndpointLeaseAttempt(False, None, (), "unused")),
        lease_registry=HeldRunTargetLeaseRegistry(),
    )

    assert outcome.idle is True
    assert outcome.reacquired is False
    assert outcome.validation_codes == ()


def test_lease_registry_releases_replaced_and_shutdown_leases() -> None:
    first = _FakeLiveLease("lease-a")
    second = _FakeLiveLease("lease-b")
    registry = HeldRunTargetLeaseRegistry()

    registry.retain_run_target_lease(
        run_id="run-a", run_target_id="target-a", lease=first
    )
    registry.retain_run_target_lease(
        run_id="run-a", run_target_id="target-a", lease=second
    )

    assert first.released is True
    assert second.released is False
    assert registry.retained_count == 1

    registry.release_all()

    assert second.released is True
    assert registry.retained_count == 0


class _InMemoryRunStore(RunExecutorQueueStore):
    def __init__(
        self, run: StartedRun | None, *, preflight_conflict: bool = False
    ) -> None:
        self.run = run
        self.preflight_conflict = preflight_conflict

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

    def load_next_runnable_run(self) -> StartedRun | None:
        if self.run is None or self.run.state not in {
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
        }:
            return None
        if not any(
            target.state is RunTargetState.PENDING for target in self.run.targets
        ):
            return None
        return self.run

    def requeue_next_due_waiting_run_target(self) -> StartedRunTarget | None:
        if self.run is None or self.run.state not in {
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
        }:
            return None
        waiting = next(
            (
                target
                for target in self.run.targets
                if target.state is RunTargetState.WAITING_FOR_ENDPOINT
            ),
            None,
        )
        if waiting is None:
            return None
        pending = replace(waiting, state=RunTargetState.PENDING)
        self.run = replace(
            self.run,
            targets=tuple(
                pending if target.run_target_id == waiting.run_target_id else target
                for target in self.run.targets
            ),
        )
        return pending

    def load_next_pausing_run(self) -> StartedRun | None:
        if self.run is None or self.run.state is not RunState.PAUSING:
            return None
        return self.run

    def finalize_requested_run_pause(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.PAUSING:
            return None
        self.run = replace(
            run,
            state=RunState.PAUSED,
            targets=tuple(
                replace(
                    target,
                    state=RunTargetState.PAUSED,
                    last_lease_id=None,
                    last_ownership_epoch=None,
                    last_fencing_token=None,
                )
                if target.state
                in {
                    RunTargetState.ACQUIRING_LEASE,
                    RunTargetState.REVALIDATING,
                    RunTargetState.EXECUTING,
                }
                else target
                for target in run.targets
            ),
        )
        return self.run

    def load_next_revalidating_run_target_key(self) -> tuple[str, str] | None:
        if self.run is None or self.run.state not in {
            RunState.PREFLIGHT,
            RunState.EXECUTING,
        }:
            return None
        target = next(
            (
                target
                for target in self.run.targets
                if target.state is RunTargetState.REVALIDATING
            ),
            None,
        )
        if target is None:
            return None
        return self.run.run_id, target.run_target_id

    def load_next_executing_run_target_key(self) -> tuple[str, str] | None:
        if self.run is None or self.run.state is not RunState.EXECUTING:
            return None
        target = next(
            (
                target
                for target in self.run.targets
                if target.state is RunTargetState.EXECUTING
            ),
            None,
        )
        if target is None:
            return None
        return self.run.run_id, target.run_target_id

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
        if self.preflight_conflict:
            return None
        run = self.load_started_run(run_id)
        if run is None or run.state not in {
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
        }:
            return None
        updated_targets: list[StartedRunTarget] = []
        claimed: StartedRunTarget | None = None
        for target in run.targets:
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.PENDING
            ):
                claimed = replace(target, state=RunTargetState.ACQUIRING_LEASE)
                updated_targets.append(claimed)
            else:
                updated_targets.append(target)
        if claimed is None:
            return None
        self.run = replace(
            run,
            state=(
                RunState.EXECUTING
                if run.state is RunState.EXECUTING
                else RunState.PREFLIGHT
            ),
            targets=tuple(updated_targets),
        )
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
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.ACQUIRING_LEASE
            ):
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
        waiting: StartedRunTarget | None = None
        updated_targets: list[StartedRunTarget] = []
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is expected_state:
                waiting = replace(
                    target,
                    state=RunTargetState.WAITING_FOR_ENDPOINT,
                    last_lease_id=None,
                    last_ownership_epoch=None,
                    last_fencing_token=None,
                )
                updated_targets.append(waiting)
            else:
                updated_targets.append(target)
        if waiting is None:
            return None
        self.run = replace(run, targets=tuple(updated_targets))
        return waiting

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
                if target.required_owner_installation_id not in (
                    None,
                    owner_installation_id,
                ):
                    return None
                if target.required_ownership_epoch not in (None, ownership_epoch):
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
        if run is None or run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
            return None
        updated_targets: list[StartedRunTarget] = []
        started: StartedRunTarget | None = None
        for target in run.targets:
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.REVALIDATING
            ):
                if (
                    target.last_lease_id != lease_id
                    or target.last_ownership_epoch != ownership_epoch
                    or target.last_fencing_token != fencing_token
                ):
                    return None
                if target.required_owner_installation_id not in (
                    None,
                    owner_installation_id,
                ):
                    return None
                if target.required_ownership_epoch not in (None, ownership_epoch):
                    return None
                started = replace(target, state=RunTargetState.EXECUTING)
                updated_targets.append(started)
            else:
                updated_targets.append(target)
        if started is None:
            return None
        self.run = replace(
            run, state=RunState.EXECUTING, targets=tuple(updated_targets)
        )
        return started


class _FakeLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, attempt: EndpointLeaseAttempt) -> None:
        self._attempt = attempt
        self.requests: tuple[EndpointLeaseRequest, ...] = ()

    def acquire_endpoint_lease(
        self, request: EndpointLeaseRequest
    ) -> EndpointLeaseAttempt:
        self.requests = (*self.requests, request)
        return self._attempt


class _SequencedLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, attempts: tuple[EndpointLeaseAttempt, ...]) -> None:
        self._attempts = list(attempts)
        self.requests: tuple[EndpointLeaseRequest, ...] = ()

    def acquire_endpoint_lease(
        self, request: EndpointLeaseRequest
    ) -> EndpointLeaseAttempt:
        self.requests = (*self.requests, request)
        if not self._attempts:
            raise AssertionError("unexpected lease request")
        return self._attempts.pop(0)


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


def _queued_run(
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
        state=RunState.QUEUED,
        app_version="0B-dev",
        plan_checksum="a" * 64,
        planned_operations=1,
        planned_bytes=128,
        targets=targets or (_target(),),
    )


def _preflighted_run() -> StartedRun:
    return replace(
        _queued_run(),
        state=RunState.PREFLIGHT,
        targets=(
            replace(
                _target(),
                state=RunTargetState.REVALIDATING,
                last_lease_id="lease-a",
                last_ownership_epoch=1,
                last_fencing_token=42,
            ),
        ),
    )


def _executing_run() -> StartedRun:
    return replace(
        _queued_run(),
        state=RunState.EXECUTING,
        targets=(
            replace(
                _target(),
                state=RunTargetState.EXECUTING,
                last_lease_id="lease-a",
                last_ownership_epoch=1,
                last_fencing_token=42,
            ),
        ),
    )


def _target(
    *,
    run_target_id: str = "run-a-target-0000",
    endpoint_id: str = "target-a",
) -> StartedRunTarget:
    return StartedRunTarget(
        run_target_id=run_target_id,
        endpoint_id=endpoint_id,
        endpoint_revision_id="target-rev-a"
        if endpoint_id == "target-a"
        else f"{endpoint_id}-rev-a",
        state=RunTargetState.PENDING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key=f"endpoint:{endpoint_id}",
        planned_operations=1,
        planned_bytes=128,
    )
