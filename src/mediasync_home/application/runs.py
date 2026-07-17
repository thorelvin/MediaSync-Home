from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol

from mediasync_home.application.plans import PlanEndpoint, PlanEndpointRole, PlanStore, SealedPlan, verify_plan_checksum


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


class RunStore(Protocol):
    def save_started_run(self, run: StartedRun) -> None: ...

    def load_started_run(self, run_id: str) -> StartedRun | None: ...

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None: ...

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
    existing = runs.load_started_run_by_idempotency_key(command.idempotency_key)
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
        idempotency_key=command.idempotency_key,
        command_receipt_id=command.idempotency_key,
        logical_run_group_id=ids.logical_run_group_id,
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=RunState.QUEUED,
        app_version=APP_VERSION,
        plan_checksum=plan.plan_checksum,
        planned_operations=plan.operation_count,
        planned_bytes=plan.planned_bytes,
        targets=tuple(_started_run_target(ids.run_id, endpoint) for endpoint in _target_endpoints(plan)),
        summary={
            "executor_pending": True,
            "scope": "0B_RUN_START_SKELETON",
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
