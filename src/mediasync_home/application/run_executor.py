from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from mediasync_home.application.runs import (
    EndpointLeaseAuthority,
    LiveEndpointLease,
    RunTargetExecutionStartOutcome,
    RunStore,
    StartedRun,
    StartedRunTarget,
    acquire_run_target_lease,
    begin_next_run_target_preflight,
    start_run_target_execution,
)
from mediasync_home.domain.capabilities import MutationPermit


MAX_RUN_EXECUTOR_PUMP_STEPS = 100


class RunExecutorViolation(ValueError):
    pass


class RunExecutorPumpStopReason(str, Enum):
    IDLE = "IDLE"
    BLOCKED = "BLOCKED"
    STEP_LIMIT_REACHED = "STEP_LIMIT_REACHED"


class RunExecutorQueueStore(RunStore, Protocol):
    def load_next_runnable_run(self) -> StartedRun | None: ...

    def load_next_revalidating_run_target_key(self) -> tuple[str, str] | None: ...


class RunTargetLeaseRegistry(Protocol):
    def retain_run_target_lease(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease: LiveEndpointLease,
    ) -> None: ...

    def load_retained_run_target_lease(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> LiveEndpointLease | None: ...

    def release_retained_run_target_lease(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> None: ...

    def retained_run_target_keys(self) -> tuple[tuple[str, str], ...]: ...


@dataclass
class HeldRunTargetLeaseRegistry(RunTargetLeaseRegistry):
    _leases: dict[tuple[str, str], LiveEndpointLease] = field(default_factory=dict)

    @property
    def retained_count(self) -> int:
        return len(self._leases)

    def retain_run_target_lease(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease: LiveEndpointLease,
    ) -> None:
        key = (run_id, run_target_id)
        existing = self._leases.get(key)
        if existing is lease:
            return
        if existing is not None:
            existing.release()
        self._leases[key] = lease

    def load_retained_run_target_lease(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> LiveEndpointLease | None:
        return self._leases.get((run_id, run_target_id))

    def release_retained_run_target_lease(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> None:
        lease = self._leases.pop((run_id, run_target_id), None)
        if lease is not None:
            lease.release()

    def retained_run_target_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._leases))

    def release_all(self) -> None:
        leases = tuple(self._leases.values())
        self._leases.clear()
        for lease in leases:
            lease.release()


@dataclass(frozen=True)
class RunExecutorStepOutcome:
    idle: bool
    claimed: bool
    lease_acquired: bool
    run_id: str | None
    run_target_id: str | None
    target: StartedRunTarget | None
    lease: LiveEndpointLease | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class RunExecutorPumpOutcome:
    steps_attempted: int
    leases_retained: int
    stopped_reason: RunExecutorPumpStopReason
    last_step: RunExecutorStepOutcome | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class RunExecutorExecutionStartStepOutcome:
    idle: bool
    execution_started: bool
    run_id: str | None
    run_target_id: str | None
    target: StartedRunTarget | None
    mutation_permit: MutationPermit | None
    validation_codes: tuple[str, ...]
    next_action: str


def execute_bounded_run_executor_preflight_pump(
    *,
    runs: RunExecutorQueueStore,
    leases: EndpointLeaseAuthority,
    lease_registry: RunTargetLeaseRegistry,
    max_steps: int,
) -> RunExecutorPumpOutcome:
    if max_steps < 1:
        raise RunExecutorViolation("RUN_EXECUTOR_PUMP_REQUIRES_POSITIVE_STEP_LIMIT")
    if max_steps > MAX_RUN_EXECUTOR_PUMP_STEPS:
        raise RunExecutorViolation("RUN_EXECUTOR_PUMP_STEP_LIMIT_TOO_LARGE")

    leases_retained = 0
    last_step: RunExecutorStepOutcome | None = None
    for step_index in range(1, max_steps + 1):
        last_step = execute_one_run_target_preflight_step(runs=runs, leases=leases)
        if last_step.lease_acquired:
            if last_step.run_id is None or last_step.run_target_id is None or last_step.lease is None:
                raise RunExecutorViolation("RUN_EXECUTOR_ACQUIRED_LEASE_INCOMPLETE")
            lease_registry.retain_run_target_lease(
                run_id=last_step.run_id,
                run_target_id=last_step.run_target_id,
                lease=last_step.lease,
            )
            leases_retained += 1

        if last_step.idle:
            return RunExecutorPumpOutcome(
                steps_attempted=step_index,
                leases_retained=leases_retained,
                stopped_reason=RunExecutorPumpStopReason.IDLE,
                last_step=last_step,
                validation_codes=(),
                next_action=last_step.next_action,
            )
        if last_step.validation_codes and not last_step.lease_acquired:
            return RunExecutorPumpOutcome(
                steps_attempted=step_index,
                leases_retained=leases_retained,
                stopped_reason=RunExecutorPumpStopReason.BLOCKED,
                last_step=last_step,
                validation_codes=last_step.validation_codes,
                next_action=last_step.next_action,
            )

    return RunExecutorPumpOutcome(
        steps_attempted=max_steps,
        leases_retained=leases_retained,
        stopped_reason=RunExecutorPumpStopReason.STEP_LIMIT_REACHED,
        last_step=last_step,
        validation_codes=(),
        next_action="Run executor preflight pump reached its configured step limit.",
    )


def execute_one_run_target_preflight_step(
    *,
    runs: RunExecutorQueueStore,
    leases: EndpointLeaseAuthority,
) -> RunExecutorStepOutcome:
    run = runs.load_next_runnable_run()
    if run is None:
        return RunExecutorStepOutcome(
            idle=True,
            claimed=False,
            lease_acquired=False,
            run_id=None,
            run_target_id=None,
            target=None,
            lease=None,
            validation_codes=(),
            next_action="No queued or preflight run has a pending target.",
        )

    preflight = begin_next_run_target_preflight(run_id=run.run_id, runs=runs)
    if not preflight.claimed or preflight.run_target_id is None:
        return RunExecutorStepOutcome(
            idle=False,
            claimed=False,
            lease_acquired=False,
            run_id=run.run_id,
            run_target_id=preflight.run_target_id,
            target=preflight.target,
            lease=None,
            validation_codes=preflight.validation_codes,
            next_action=preflight.next_action,
        )

    lease_outcome = acquire_run_target_lease(
        run_id=run.run_id,
        run_target_id=preflight.run_target_id,
        runs=runs,
        leases=leases,
    )
    return RunExecutorStepOutcome(
        idle=False,
        claimed=True,
        lease_acquired=lease_outcome.acquired,
        run_id=run.run_id,
        run_target_id=preflight.run_target_id,
        target=lease_outcome.target or preflight.target,
        lease=lease_outcome.lease,
        validation_codes=lease_outcome.validation_codes,
        next_action=lease_outcome.next_action,
    )


def execute_one_run_target_execution_start_step(
    *,
    runs: RunExecutorQueueStore,
    lease_registry: RunTargetLeaseRegistry,
) -> RunExecutorExecutionStartStepOutcome:
    key = runs.load_next_revalidating_run_target_key()
    if key is None:
        return RunExecutorExecutionStartStepOutcome(
            idle=True,
            execution_started=False,
            run_id=None,
            run_target_id=None,
            target=None,
            mutation_permit=None,
            validation_codes=(),
            next_action="No preflighted run target is waiting for execution start.",
        )

    run_id, run_target_id = key
    lease = lease_registry.load_retained_run_target_lease(
        run_id=run_id,
        run_target_id=run_target_id,
    )
    if lease is None:
        target = _load_target(runs=runs, run_id=run_id, run_target_id=run_target_id)
        return RunExecutorExecutionStartStepOutcome(
            idle=False,
            execution_started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_RETAINED_LEASE_NOT_FOUND",),
            next_action="Reacquire the endpoint lease before starting target execution.",
        )

    execution = start_run_target_execution(
        run_id=run_id,
        run_target_id=run_target_id,
        runs=runs,
        lease=lease,
    )
    if not execution.started:
        lease_registry.release_retained_run_target_lease(
            run_id=run_id,
            run_target_id=run_target_id,
        )
    return _execution_start_step_from_outcome(execution)


def _execution_start_step_from_outcome(
    outcome: RunTargetExecutionStartOutcome,
) -> RunExecutorExecutionStartStepOutcome:
    return RunExecutorExecutionStartStepOutcome(
        idle=False,
        execution_started=outcome.started,
        run_id=outcome.run_id,
        run_target_id=outcome.run_target_id,
        target=outcome.target,
        mutation_permit=outcome.mutation_permit,
        validation_codes=outcome.validation_codes,
        next_action=outcome.next_action,
    )


def _load_target(
    *,
    runs: RunExecutorQueueStore,
    run_id: str,
    run_target_id: str,
) -> StartedRunTarget | None:
    run = runs.load_started_run(run_id)
    if run is None:
        return None
    return next((target for target in run.targets if target.run_target_id == run_target_id), None)
