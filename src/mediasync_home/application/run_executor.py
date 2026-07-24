from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.runs import (
    EndpointLeaseAuthority,
    LiveEndpointLease,
    RunStore,
    StartedRun,
    StartedRunTarget,
    acquire_run_target_lease,
    begin_next_run_target_preflight,
)


class RunExecutorQueueStore(RunStore, Protocol):
    def load_next_runnable_run(self) -> StartedRun | None: ...


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
