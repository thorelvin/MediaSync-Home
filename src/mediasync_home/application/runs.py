from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol

from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanStore,
    SealedPlan,
    verify_plan_checksum,
)
from mediasync_home.domain.capabilities import MutationPermit


APP_VERSION = "0B-dev"
PLAN_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RunStartViolation(ValueError):
    pass


class RunCommandName(str, Enum):
    START_RUN = "START_RUN"


class RunState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PREFLIGHT = "PREFLIGHT"
    EXECUTING = "EXECUTING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class RunTargetState(str, Enum):
    PENDING = "PENDING"
    ACQUIRING_LEASE = "ACQUIRING_LEASE"
    REVALIDATING = "REVALIDATING"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"
    WAITING_FOR_ENDPOINT = "WAITING_FOR_ENDPOINT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_WARNINGS = "SUCCEEDED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class RunTriggerType(str, Enum):
    MANUAL_LOCAL_PREVIEW = "MANUAL_LOCAL_PREVIEW"


@dataclass(frozen=True)
class StartRunCommand:
    request_id: str
    idempotency_key: str
    plan_id: str
    plan_checksum: str
    run_idempotency_key: str | None = None
    trigger_occurrence_id: str | None = None


@dataclass(frozen=True)
class RunIds:
    run_id: str
    logical_run_group_id: str


@dataclass(frozen=True)
class StartedRunTarget:
    run_target_id: str
    endpoint_id: str
    endpoint_revision_id: str
    state: RunTargetState
    required_owner_installation_id: str | None = None
    required_ownership_epoch: int | None = None
    lease_resource_key: str | None = None
    last_lease_id: str | None = None
    last_ownership_epoch: int | None = None
    last_fencing_token: int | None = None
    planned_operations: int = 0
    planned_bytes: int = 0
    completed_operations: int = 0
    completed_bytes: int = 0


@dataclass(frozen=True)
class StartedRun:
    run_id: str
    job_id: str
    job_revision_id: str
    plan_id: str
    command_request_id: str
    idempotency_key: str
    command_receipt_id: str
    logical_run_group_id: str
    trigger_type: RunTriggerType
    state: RunState
    app_version: str
    plan_checksum: str
    planned_operations: int
    planned_bytes: int
    targets: tuple[StartedRunTarget, ...] = ()
    summary: Mapping[str, object] = field(default_factory=dict)
    warning_count: int = 0
    error_count: int = 0
    trigger_occurrence_id: str | None = None
    resumed_from_run_id: str | None = None


@dataclass(frozen=True)
class RunStartReadiness:
    plan_id: str
    plan_found: bool
    plan_checksum_matches: bool
    plan_checksum_valid: bool
    plan_runnable: bool
    validation_codes: tuple[str, ...]
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "plan_found": self.plan_found,
            "plan_checksum_matches": self.plan_checksum_matches,
            "plan_checksum_valid": self.plan_checksum_valid,
            "plan_runnable": self.plan_runnable,
            "validation_codes": list(self.validation_codes),
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class RunStartOutcome:
    created: bool
    idempotent_replay: bool
    readiness: RunStartReadiness
    run: StartedRun | None = None


@dataclass(frozen=True)
class RunTargetPreflightOutcome:
    claimed: bool
    run_id: str
    run_target_id: str | None
    target: StartedRunTarget | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class EndpointLeaseRequest:
    run_id: str
    run_target_id: str
    endpoint_id: str
    endpoint_revision_id: str
    resource_key: str
    required_owner_installation_id: str | None
    required_ownership_epoch: int | None


class LiveEndpointLease(Protocol):
    @property
    def lease_id(self) -> str: ...

    @property
    def owner_installation_id(self) -> str: ...

    @property
    def ownership_epoch(self) -> int: ...

    @property
    def fencing_token(self) -> int: ...

    def issue_mutation_permit(self) -> MutationPermit: ...

    def release(self) -> None: ...


@dataclass(frozen=True)
class EndpointLeaseAttempt:
    acquired: bool
    lease: LiveEndpointLease | None
    validation_codes: tuple[str, ...]
    next_action: str


class EndpointLeaseAuthority(Protocol):
    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt: ...


@dataclass(frozen=True)
class RunTargetLeaseOutcome:
    acquired: bool
    run_id: str
    run_target_id: str
    target: StartedRunTarget | None
    lease: LiveEndpointLease | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class RunTargetExecutionStartOutcome:
    started: bool
    run_id: str
    run_target_id: str
    target: StartedRunTarget | None
    mutation_permit: MutationPermit | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class RunTargetCompletionOutcome:
    completed: bool
    run_completed: bool
    run_id: str
    run_target_id: str
    run: StartedRun | None
    target: StartedRunTarget | None
    validation_codes: tuple[str, ...]
    next_action: str


class RunStore(Protocol):
    def save_started_run(self, run: StartedRun) -> None: ...

    def load_started_run(self, run_id: str) -> StartedRun | None: ...

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None: ...

    def load_active_run_for_job(self, job_id: str) -> StartedRun | None: ...

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None: ...

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None: ...

    def record_run_target_lease_acquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None: ...

    def record_run_target_execution_started(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None: ...

    def record_run_target_succeeded(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
    ) -> StartedRun | None: ...

    def record_run_target_recovery_required(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None: ...

    def record_run_target_cancelled(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None: ...


class RunIdFactory(Protocol):
    def new_run_ids(self) -> RunIds: ...


def parse_start_run_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> StartRunCommand:
    plan_id = payload.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise RunStartViolation("START_RUN_REQUIRES_PLAN_ID")
    plan_checksum = payload.get("plan_checksum")
    if not isinstance(plan_checksum, str) or PLAN_CHECKSUM_PATTERN.fullmatch(plan_checksum) is None:
        raise RunStartViolation("START_RUN_REQUIRES_PLAN_CHECKSUM")
    return StartRunCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        plan_id=plan_id,
        plan_checksum=plan_checksum,
    )


def evaluate_start_run(
    *,
    command: StartRunCommand,
    plans: PlanStore,
) -> RunStartReadiness:
    plan = plans.load_sealed_plan(command.plan_id)
    if plan is None:
        return _missing_plan(command.plan_id)
    return _readiness_for_plan(command=command, plan=plan)


def start_run_from_sealed_plan(
    *,
    command: StartRunCommand,
    plans: PlanStore,
    runs: RunStore,
    id_factory: RunIdFactory,
) -> RunStartOutcome:
    existing = runs.load_started_run_by_idempotency_key(_effective_run_idempotency_key(command))
    if existing is not None:
        return RunStartOutcome(
            created=False,
            idempotent_replay=True,
            readiness=_ready_to_queue(command.plan_id),
            run=existing,
        )

    plan = plans.load_sealed_plan(command.plan_id)
    if plan is None:
        return RunStartOutcome(
            created=False,
            idempotent_replay=False,
            readiness=_missing_plan(command.plan_id),
        )

    readiness = _readiness_for_plan(command=command, plan=plan)
    if not readiness.plan_runnable:
        return RunStartOutcome(
            created=False,
            idempotent_replay=False,
            readiness=readiness,
        )

    active = _load_active_run_for_job(runs=runs, job_id=plan.job_id)
    if active is not None:
        return RunStartOutcome(
            created=False,
            idempotent_replay=True,
            readiness=readiness,
            run=active,
        )

    run = _started_run_from_plan(
        command=command,
        plan=plan,
        ids=id_factory.new_run_ids(),
    )
    runs.save_started_run(run)
    return RunStartOutcome(
        created=True,
        idempotent_replay=False,
        readiness=readiness,
        run=run,
    )


def begin_next_run_target_preflight(
    *,
    run_id: str,
    runs: RunStore,
) -> RunTargetPreflightOutcome:
    run = runs.load_started_run(run_id)
    if run is None:
        return RunTargetPreflightOutcome(
            claimed=False,
            run_id=run_id,
            run_target_id=None,
            target=None,
            validation_codes=("RUN_NOT_FOUND",),
            next_action="Create a queued run before target preflight.",
        )
    if run.state not in {RunState.QUEUED, RunState.PREFLIGHT}:
        return RunTargetPreflightOutcome(
            claimed=False,
            run_id=run_id,
            run_target_id=None,
            target=None,
            validation_codes=("RUN_NOT_READY_FOR_TARGET_PREFLIGHT",),
            next_action="Only queued or preflight runs can acquire target work.",
        )

    target = runs.load_next_pending_run_target(run_id)
    if target is None:
        return RunTargetPreflightOutcome(
            claimed=False,
            run_id=run_id,
            run_target_id=None,
            target=None,
            validation_codes=("RUN_HAS_NO_PENDING_TARGETS",),
            next_action="No pending run targets are available for lease preflight.",
        )
    if target.lease_resource_key is None or not target.lease_resource_key.strip():
        return RunTargetPreflightOutcome(
            claimed=False,
            run_id=run_id,
            run_target_id=target.run_target_id,
            target=target,
            validation_codes=("RUN_TARGET_REQUIRES_LEASE_RESOURCE_KEY",),
            next_action="Refresh the sealed plan so each writable target has a lease resource key.",
        )

    claimed = runs.begin_run_target_preflight(
        run_id=run_id,
        run_target_id=target.run_target_id,
    )
    if claimed is None:
        return RunTargetPreflightOutcome(
            claimed=False,
            run_id=run_id,
            run_target_id=target.run_target_id,
            target=None,
            validation_codes=("RUN_TARGET_PREFLIGHT_CLAIM_CONFLICT",),
            next_action="Reload run state and retry target preflight.",
        )
    return RunTargetPreflightOutcome(
        claimed=True,
        run_id=run_id,
        run_target_id=claimed.run_target_id,
        target=claimed,
        validation_codes=(),
        next_action="Target is ready for the lease adapter to acquire the endpoint lock.",
    )


def acquire_run_target_lease(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunStore,
    leases: EndpointLeaseAuthority,
) -> RunTargetLeaseOutcome:
    run = runs.load_started_run(run_id)
    if run is None:
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            lease=None,
            validation_codes=("RUN_NOT_FOUND",),
            next_action="Create a queued run before acquiring an endpoint lease.",
        )
    if run.state is not RunState.PREFLIGHT:
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            lease=None,
            validation_codes=("RUN_NOT_IN_PREFLIGHT",),
            next_action="Begin target preflight before acquiring an endpoint lease.",
        )

    target = _target_by_id(run, run_target_id)
    if target is None:
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            lease=None,
            validation_codes=("RUN_TARGET_NOT_FOUND",),
            next_action="Reload run targets before acquiring an endpoint lease.",
        )
    if target.state is not RunTargetState.ACQUIRING_LEASE:
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            lease=None,
            validation_codes=("RUN_TARGET_NOT_ACQUIRING_LEASE",),
            next_action="Only targets in lease acquisition can request an endpoint lock.",
        )
    if target.lease_resource_key is None or not target.lease_resource_key.strip():
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            lease=None,
            validation_codes=("RUN_TARGET_REQUIRES_LEASE_RESOURCE_KEY",),
            next_action="Refresh the sealed plan so the target has a lease resource key.",
        )

    attempt = leases.acquire_endpoint_lease(
        EndpointLeaseRequest(
            run_id=run_id,
            run_target_id=run_target_id,
            endpoint_id=target.endpoint_id,
            endpoint_revision_id=target.endpoint_revision_id,
            resource_key=target.lease_resource_key,
            required_owner_installation_id=target.required_owner_installation_id,
            required_ownership_epoch=target.required_ownership_epoch,
        )
    )
    if not attempt.acquired:
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            lease=None,
            validation_codes=attempt.validation_codes or ("RUN_TARGET_ENDPOINT_LEASE_UNAVAILABLE",),
            next_action=attempt.next_action,
        )
    lease = attempt.lease
    if lease is None:
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            lease=None,
            validation_codes=("RUN_TARGET_ENDPOINT_LEASE_INVALID",),
            next_action="The lease adapter reported success without a live lease handle.",
        )

    updated = runs.record_run_target_lease_acquired(
        run_id=run_id,
        run_target_id=run_target_id,
        lease_id=lease.lease_id,
        owner_installation_id=lease.owner_installation_id,
        ownership_epoch=lease.ownership_epoch,
        fencing_token=lease.fencing_token,
    )
    if updated is None:
        lease.release()
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            lease=None,
            validation_codes=("RUN_TARGET_LEASE_RECORD_CONFLICT",),
            next_action="Released the endpoint lease because run-target state changed during persistence.",
        )
    return RunTargetLeaseOutcome(
        acquired=True,
        run_id=run_id,
        run_target_id=run_target_id,
        target=updated,
        lease=lease,
        validation_codes=(),
        next_action="Target has a live endpoint lease and is ready for revalidation.",
    )


def start_run_target_execution(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunStore,
    lease: LiveEndpointLease,
) -> RunTargetExecutionStartOutcome:
    run = runs.load_started_run(run_id)
    if run is None:
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            mutation_permit=None,
            validation_codes=("RUN_NOT_FOUND",),
            next_action="Create a preflighted run before starting target execution.",
        )
    if run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            mutation_permit=None,
            validation_codes=("RUN_NOT_READY_FOR_EXECUTION_START",),
            next_action="Acquire all endpoint leases before starting target execution.",
        )

    target = _target_by_id(run, run_target_id)
    if target is None:
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_NOT_FOUND",),
            next_action="Reload run targets before starting target execution.",
        )
    if target.state is not RunTargetState.REVALIDATING:
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_NOT_REVALIDATING",),
            next_action="Only revalidating targets can start execution.",
        )
    if (
        target.last_lease_id is None
        or target.last_ownership_epoch is None
        or target.last_fencing_token is None
    ):
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_LEASE_METADATA_MISSING",),
            next_action="Reacquire the endpoint lease before starting target execution.",
        )
    if (
        lease.lease_id != target.last_lease_id
        or lease.ownership_epoch != target.last_ownership_epoch
        or lease.fencing_token != target.last_fencing_token
    ):
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_RETAINED_LEASE_MISMATCH",),
            next_action="Release the stale retained lease and reacquire the endpoint lock.",
        )

    try:
        permit = lease.issue_mutation_permit()
    except RuntimeError as exc:
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            mutation_permit=None,
            validation_codes=(_exception_validation_code(exc),),
            next_action=_exception_next_action(
                exc,
                "Reacquire the endpoint lease before starting target execution.",
            ),
        )

    if not _permit_matches_run_target(permit=permit, run_id=run_id, target=target):
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_MUTATION_PERMIT_MISMATCH",),
            next_action="Discard the stale mutation permit and reacquire the endpoint lease.",
        )

    updated = runs.record_run_target_execution_started(
        run_id=run_id,
        run_target_id=run_target_id,
        lease_id=permit.lease_id,
        owner_installation_id=permit.owner_installation_id,
        ownership_epoch=permit.ownership_epoch,
        fencing_token=permit.fencing_token,
    )
    if updated is None:
        return RunTargetExecutionStartOutcome(
            started=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=None,
            mutation_permit=None,
            validation_codes=("RUN_TARGET_EXECUTION_START_CONFLICT",),
            next_action="Reload run state before retrying target execution.",
        )
    return RunTargetExecutionStartOutcome(
        started=True,
        run_id=run_id,
        run_target_id=run_target_id,
        target=updated,
        mutation_permit=permit,
        validation_codes=(),
        next_action="Target is executing with a live mutation permit.",
    )


def complete_run_target_success(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunStore,
    completed_operations: int,
    completed_bytes: int,
) -> RunTargetCompletionOutcome:
    if completed_operations < 0 or completed_bytes < 0:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_COMPLETION_REQUIRES_NON_NEGATIVE_COUNTS",),
            next_action="Retry completion with non-negative operation and byte counts.",
        )

    run = runs.load_started_run(run_id)
    if run is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_NOT_FOUND",),
            next_action="Create and execute a run before completing target work.",
        )
    if run.state is not RunState.EXECUTING:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=None,
            validation_codes=("RUN_NOT_EXECUTING",),
            next_action="Only executing runs can complete target work.",
        )

    target = _target_by_id(run, run_target_id)
    if target is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=None,
            validation_codes=("RUN_TARGET_NOT_FOUND",),
            next_action="Reload run targets before completing target work.",
        )
    if target.state is not RunTargetState.EXECUTING:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=target,
            validation_codes=("RUN_TARGET_NOT_EXECUTING",),
            next_action="Only executing targets can be marked succeeded.",
        )
    if (
        completed_operations != target.planned_operations
        or completed_bytes != target.planned_bytes
    ):
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=target,
            validation_codes=("RUN_TARGET_COMPLETION_COUNTS_MISMATCH",),
            next_action="Complete all planned target work before marking the target succeeded.",
        )

    updated_run = runs.record_run_target_succeeded(
        run_id=run_id,
        run_target_id=run_target_id,
        completed_operations=completed_operations,
        completed_bytes=completed_bytes,
    )
    if updated_run is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_COMPLETION_CONFLICT",),
            next_action="Reload run state before retrying target completion.",
        )
    updated_target = _target_by_id(updated_run, run_target_id)
    if updated_target is None:
        raise RunStartViolation("RUN_TARGET_COMPLETION_LOAD_FAILED")
    return RunTargetCompletionOutcome(
        completed=True,
        run_completed=updated_run.state is RunState.COMPLETED,
        run_id=run_id,
        run_target_id=run_target_id,
        run=updated_run,
        target=updated_target,
        validation_codes=(),
        next_action="Target succeeded; run is complete." if updated_run.state is RunState.COMPLETED else "Target succeeded; remaining targets continue.",
    )


def complete_run_target_recovery_required(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunStore,
    last_error_code: str,
) -> RunTargetCompletionOutcome:
    normalized_error_code = _normalized_error_code(last_error_code)
    if normalized_error_code is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_TERMINAL_RECOVERY_REQUIRES_ERROR_CODE",),
            next_action="Retry terminal recovery completion with a non-empty error code.",
        )
    readiness = _terminal_completion_readiness(
        run_id=run_id,
        run_target_id=run_target_id,
        runs=runs,
        target_state_validation_code="RUN_TARGET_NOT_EXECUTING",
        target_state_next_action="Only executing targets can be marked recovery-required.",
    )
    if readiness.completed is False:
        return readiness

    updated_run = runs.record_run_target_recovery_required(
        run_id=run_id,
        run_target_id=run_target_id,
        last_error_code=normalized_error_code,
    )
    if updated_run is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_TERMINAL_RECOVERY_CONFLICT",),
            next_action="Reload run state before retrying terminal recovery completion.",
        )
    updated_target = _target_by_id(updated_run, run_target_id)
    if updated_target is None:
        raise RunStartViolation("RUN_TARGET_TERMINAL_RECOVERY_LOAD_FAILED")
    return RunTargetCompletionOutcome(
        completed=True,
        run_completed=updated_run.state is RunState.RECOVERY_REQUIRED,
        run_id=run_id,
        run_target_id=run_target_id,
        run=updated_run,
        target=updated_target,
        validation_codes=(),
        next_action="Target requires user recovery before the run can continue.",
    )


def complete_run_target_cancelled(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunStore,
    last_error_code: str,
) -> RunTargetCompletionOutcome:
    normalized_error_code = _normalized_error_code(last_error_code)
    if normalized_error_code is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_TERMINAL_RECOVERY_REQUIRES_ERROR_CODE",),
            next_action="Retry terminal recovery completion with a non-empty cancellation code.",
        )
    readiness = _terminal_completion_readiness(
        run_id=run_id,
        run_target_id=run_target_id,
        runs=runs,
        target_state_validation_code="RUN_TARGET_NOT_EXECUTING",
        target_state_next_action="Only executing targets can be marked cancelled.",
    )
    if readiness.completed is False:
        return readiness

    updated_run = runs.record_run_target_cancelled(
        run_id=run_id,
        run_target_id=run_target_id,
        last_error_code=normalized_error_code,
    )
    if updated_run is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_TERMINAL_RECOVERY_CONFLICT",),
            next_action="Reload run state before retrying terminal recovery completion.",
        )
    updated_target = _target_by_id(updated_run, run_target_id)
    if updated_target is None:
        raise RunStartViolation("RUN_TARGET_TERMINAL_RECOVERY_LOAD_FAILED")
    return RunTargetCompletionOutcome(
        completed=True,
        run_completed=updated_run.state in {RunState.CANCELLED, RunState.PARTIAL_FAILURE},
        run_id=run_id,
        run_target_id=run_target_id,
        run=updated_run,
        target=updated_target,
        validation_codes=(),
        next_action="Target cancelled after terminal recovery; remaining targets continue."
        if updated_run.state is RunState.EXECUTING
        else "Target cancelled after terminal recovery; run is terminal.",
    )


def _terminal_completion_readiness(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunStore,
    target_state_validation_code: str,
    target_state_next_action: str,
) -> RunTargetCompletionOutcome:
    run = runs.load_started_run(run_id)
    if run is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_NOT_FOUND",),
            next_action="Create and execute a run before completing target work.",
        )
    if run.state is not RunState.EXECUTING:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=None,
            validation_codes=("RUN_NOT_EXECUTING",),
            next_action="Only executing runs can complete target work.",
        )

    target = _target_by_id(run, run_target_id)
    if target is None:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=None,
            validation_codes=("RUN_TARGET_NOT_FOUND",),
            next_action="Reload run targets before completing target work.",
        )
    if target.state is not RunTargetState.EXECUTING:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=target,
            validation_codes=(target_state_validation_code,),
            next_action=target_state_next_action,
        )
    return RunTargetCompletionOutcome(
        completed=True,
        run_completed=False,
        run_id=run_id,
        run_target_id=run_target_id,
        run=run,
        target=target,
        validation_codes=(),
        next_action="Target can be completed from terminal recovery state.",
    )


def _normalized_error_code(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _readiness_for_plan(*, command: StartRunCommand, plan: SealedPlan) -> RunStartReadiness:
    validation_codes: list[str] = []
    checksum_matches = plan.plan_checksum == command.plan_checksum
    checksum_valid = verify_plan_checksum(plan)
    if not checksum_matches:
        validation_codes.append("PLAN_CHECKSUM_MISMATCH")
    if not checksum_valid:
        validation_codes.append("PLAN_CHECKSUM_INVALID")
    if not plan.immutable:
        validation_codes.append("PLAN_NOT_IMMUTABLE")
    if plan.risk_summary.get("highest") == "BLOCKED":
        validation_codes.append("PLAN_BLOCKED")
    if plan.operation_count < 1:
        validation_codes.append("PLAN_REQUIRES_OPERATIONS")
    if not _target_endpoints(plan):
        validation_codes.append("PLAN_REQUIRES_TARGET_ENDPOINT")

    if validation_codes:
        return RunStartReadiness(
            plan_id=command.plan_id,
            plan_found=True,
            plan_checksum_matches=checksum_matches,
            plan_checksum_valid=checksum_valid,
            plan_runnable=False,
            validation_codes=tuple(validation_codes),
            next_action="Refresh analysis and approve a runnable sealed plan before starting.",
        )
    return _ready_to_queue(command.plan_id)


def _started_run_from_plan(
    *,
    command: StartRunCommand,
    plan: SealedPlan,
    ids: RunIds,
) -> StartedRun:
    return StartedRun(
        run_id=ids.run_id,
        job_id=plan.job_id,
        job_revision_id=plan.job_revision_id,
        plan_id=plan.plan_id,
        command_request_id=command.request_id,
        idempotency_key=_effective_run_idempotency_key(command),
        command_receipt_id=command.idempotency_key,
        logical_run_group_id=ids.logical_run_group_id,
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=RunState.QUEUED,
        app_version=APP_VERSION,
        plan_checksum=plan.plan_checksum,
        planned_operations=plan.operation_count,
        planned_bytes=plan.planned_bytes,
        trigger_occurrence_id=command.trigger_occurrence_id,
        targets=tuple(_started_run_target(ids.run_id, endpoint) for endpoint in _target_endpoints(plan)),
        summary={
            "executor_pending": True,
            "scope": "0B_RUN_START_SKELETON",
            **(
                {"trigger_occurrence_id": command.trigger_occurrence_id}
                if command.trigger_occurrence_id is not None
                else {}
            ),
        },
    )


def _target_endpoints(plan: SealedPlan) -> tuple[PlanEndpoint, ...]:
    return tuple(
        sorted(
            (
                endpoint
                for endpoint in plan.endpoints
                if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
            ),
            key=lambda endpoint: (
                -1 if endpoint.target_ordinal is None else endpoint.target_ordinal,
                endpoint.endpoint_id,
            ),
        )
    )


def _target_by_id(run: StartedRun, run_target_id: str) -> StartedRunTarget | None:
    return next((target for target in run.targets if target.run_target_id == run_target_id), None)


def _permit_matches_run_target(
    *,
    permit: MutationPermit,
    run_id: str,
    target: StartedRunTarget,
) -> bool:
    return (
        permit.run_id == run_id
        and permit.run_target_id == target.run_target_id
        and permit.endpoint_id == target.endpoint_id
        and permit.endpoint_revision_id == target.endpoint_revision_id
        and permit.resource_key == target.lease_resource_key
        and permit.lease_id == target.last_lease_id
        and permit.ownership_epoch == target.last_ownership_epoch
        and permit.fencing_token == target.last_fencing_token
    )


def _exception_validation_code(exc: RuntimeError) -> str:
    validation_code = getattr(exc, "validation_code", None)
    if isinstance(validation_code, str) and validation_code.strip():
        return validation_code
    return "RUN_TARGET_MUTATION_PERMIT_UNAVAILABLE"


def _exception_next_action(exc: RuntimeError, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback


def _started_run_target(run_id: str, endpoint: PlanEndpoint) -> StartedRunTarget:
    target_ordinal = 0 if endpoint.target_ordinal is None else endpoint.target_ordinal
    return StartedRunTarget(
        run_target_id=f"{run_id}-target-{target_ordinal:04d}",
        endpoint_id=endpoint.endpoint_id,
        endpoint_revision_id=endpoint.endpoint_revision_id,
        state=RunTargetState.PENDING,
        required_owner_installation_id=endpoint.required_owner_installation_id,
        required_ownership_epoch=endpoint.required_ownership_epoch,
        lease_resource_key=f"endpoint:{endpoint.endpoint_id}",
        planned_operations=endpoint.planned_operations,
        planned_bytes=endpoint.planned_bytes,
    )


def _missing_plan(plan_id: str) -> RunStartReadiness:
    return RunStartReadiness(
        plan_id=plan_id,
        plan_found=False,
        plan_checksum_matches=False,
        plan_checksum_valid=False,
        plan_runnable=False,
        validation_codes=("PLAN_NOT_FOUND",),
        next_action="Create and approve a sealed plan before starting a run.",
    )


def _ready_to_queue(plan_id: str) -> RunStartReadiness:
    return RunStartReadiness(
        plan_id=plan_id,
        plan_found=True,
        plan_checksum_matches=True,
        plan_checksum_valid=True,
        plan_runnable=True,
        validation_codes=(),
        next_action="Run is queued for the 0B local executor skeleton.",
    )


def _effective_run_idempotency_key(command: StartRunCommand) -> str:
    return command.run_idempotency_key or command.idempotency_key


def _load_active_run_for_job(*, runs: RunStore, job_id: str) -> StartedRun | None:
    loader = getattr(runs, "load_active_run_for_job", None)
    if not callable(loader):
        return None
    loaded = loader(job_id)
    return loaded if isinstance(loaded, StartedRun) else None
