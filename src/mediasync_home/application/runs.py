from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Protocol

from mediasync_home.application.plans import (
    MUTATING_OPERATION_TYPES,
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanStore,
    SealedPlan,
    verify_plan_checksum,
)
from mediasync_home.application.job_lifecycle import JobLifecycleState, JobLifecycleStore
from mediasync_home.application.operation_audit_read_models import (
    OperationAuditReadModelStore,
)
from mediasync_home.domain.capabilities import MutationPermit


APP_VERSION = "0B-dev"
WAITABLE_ENDPOINT_LEASE_CODES = frozenset(
    {
        "ENDPOINT_LEASE_UNAVAILABLE",
        "ENDPOINT_ROOT_UNAVAILABLE",
    }
)
PLAN_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_RUN_TARGET_SCOPE = 32
MAX_RUN_OPERATION_SCOPE = 100
MAX_RUN_OPERATION_ID_LENGTH = 256


class RunStartViolation(ValueError):
    pass


class RunCommandName(str, Enum):
    START_RUN = "START_RUN"
    PAUSE_RUN = "PAUSE_RUN"
    RESUME_RUN = "RESUME_RUN"
    STOP_RUN_AFTER_ACTIVE_FILE = "STOP_RUN_AFTER_ACTIVE_FILE"


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


_TERMINAL_RUN_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.COMPLETED_WITH_WARNINGS,
        RunState.PARTIAL_FAILURE,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.BLOCKED_BY_SAFETY,
        RunState.RECOVERY_REQUIRED,
    }
)
_RETRYABLE_RUN_TARGET_STATES = frozenset(
    {
        RunTargetState.FAILED,
        RunTargetState.CANCELLED,
        RunTargetState.BLOCKED,
    }
)
_OPERATION_RETRYABLE_RUN_TARGET_STATES = frozenset(
    {*_RETRYABLE_RUN_TARGET_STATES, RunTargetState.SUCCEEDED_WITH_WARNINGS}
)


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
    target_endpoint_ids: tuple[str, ...] = ()
    resumed_from_run_id: str | None = None
    source_operation_ids: tuple[str, ...] = ()
    selected_plan_operation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunControlCommand:
    request_id: str
    idempotency_key: str
    run_id: str


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
class RunControlOutcome:
    applied: bool
    idempotent_replay: bool
    run_id: str
    validation_codes: tuple[str, ...]
    next_action: str
    run: StartedRun | None = None


@dataclass(frozen=True)
class RunStopRequest:
    run_id: str
    boundary_run_target_id: str | None = None
    boundary_operation_id: str | None = None


@dataclass(frozen=True)
class RunTargetStopProgress:
    run_target_id: str
    completed_operations: int
    completed_bytes: int


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
    def acquire_endpoint_lease(
        self, request: EndpointLeaseRequest
    ) -> EndpointLeaseAttempt: ...


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

    def load_started_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> StartedRun | None: ...

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

    def record_run_target_waiting_for_endpoint(
        self,
        *,
        run_id: str,
        run_target_id: str,
        expected_state: RunTargetState,
        reason_code: str,
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


class RunWarningCompletionStore(RunStore, Protocol):
    def record_run_target_succeeded_with_warnings(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
        skipped_operations: int,
        skipped_bytes: int,
        last_error_code: str,
    ) -> StartedRun | None: ...


class RunControlStore(Protocol):
    def load_started_run(self, run_id: str) -> StartedRun | None: ...

    def request_run_pause(self, run_id: str) -> StartedRun | None: ...

    def resume_paused_run(self, run_id: str) -> StartedRun | None: ...

    def request_run_stop_after_active_file(self, run_id: str) -> StartedRun | None: ...


class RunIdFactory(Protocol):
    def new_run_ids(self) -> RunIds: ...


def parse_start_run_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> StartRunCommand:
    allowed_fields = {
        "plan_id",
        "plan_checksum",
        "target_endpoint_ids",
        "resumed_from_run_id",
        "source_operation_ids",
    }
    if not set(payload).issubset(allowed_fields):
        raise RunStartViolation("START_RUN_PAYLOAD_INVALID")
    plan_id = payload.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise RunStartViolation("START_RUN_REQUIRES_PLAN_ID")
    plan_checksum = payload.get("plan_checksum")
    if (
        not isinstance(plan_checksum, str)
        or PLAN_CHECKSUM_PATTERN.fullmatch(plan_checksum) is None
    ):
        raise RunStartViolation("START_RUN_REQUIRES_PLAN_CHECKSUM")
    target_endpoint_ids = _parse_target_endpoint_ids(payload)
    resumed_from_run_id = _parse_resumed_from_run_id(payload)
    source_operation_ids = _parse_source_operation_ids(payload)
    if resumed_from_run_id is not None and not target_endpoint_ids:
        raise RunStartViolation("START_RUN_RETRY_REQUIRES_TARGET_SCOPE")
    if source_operation_ids and resumed_from_run_id is None:
        raise RunStartViolation("START_RUN_OPERATION_RETRY_REQUIRES_SOURCE_RUN")
    return StartRunCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        plan_id=plan_id,
        plan_checksum=plan_checksum,
        target_endpoint_ids=target_endpoint_ids,
        resumed_from_run_id=resumed_from_run_id,
        source_operation_ids=source_operation_ids,
    )


def parse_run_control_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> RunControlCommand:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise RunStartViolation("RUN_CONTROL_REQUIRES_RUN_ID")
    return RunControlCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        run_id=run_id.strip(),
    )


def request_run_pause(
    *,
    command: RunControlCommand,
    runs: RunControlStore,
) -> RunControlOutcome:
    run = runs.load_started_run(command.run_id)
    if run is None:
        return _run_control_rejected(
            command.run_id,
            "RUN_NOT_FOUND",
            "Refresh backup activity before pausing the run.",
        )
    if run.state in {RunState.PAUSING, RunState.PAUSED}:
        return RunControlOutcome(
            applied=True,
            idempotent_replay=True,
            run_id=run.run_id,
            run=run,
            validation_codes=(),
            next_action=(
                "The executor is pausing at the next safe boundary."
                if run.state is RunState.PAUSING
                else "The run is paused."
            ),
        )
    if run.state not in {
        RunState.CREATED,
        RunState.QUEUED,
        RunState.PREFLIGHT,
        RunState.EXECUTING,
    }:
        return _run_control_rejected(
            run.run_id,
            "RUN_NOT_PAUSABLE",
            "Only an active backup run can be paused.",
            run=run,
        )
    updated = runs.request_run_pause(run.run_id)
    if updated is None:
        return _run_control_rejected(
            run.run_id,
            "RUN_PAUSE_STATE_CONFLICT",
            "Refresh run progress and retry pause.",
        )
    return RunControlOutcome(
        applied=True,
        idempotent_replay=False,
        run_id=updated.run_id,
        run=updated,
        validation_codes=(),
        next_action="The executor will pause after its current safe operation boundary.",
    )


def resume_paused_run(
    *,
    command: RunControlCommand,
    runs: RunControlStore,
) -> RunControlOutcome:
    run = runs.load_started_run(command.run_id)
    if run is None:
        return _run_control_rejected(
            command.run_id,
            "RUN_NOT_FOUND",
            "Refresh backup activity before resuming the run.",
        )
    if run.state is RunState.PAUSING:
        return _run_control_rejected(
            run.run_id,
            "RUN_PAUSE_BOUNDARY_PENDING",
            "Wait until the run reaches its safe paused boundary.",
            run=run,
        )
    if run.state is not RunState.PAUSED:
        return _run_control_rejected(
            run.run_id,
            "RUN_NOT_RESUMABLE",
            "Only a paused backup run can be resumed.",
            run=run,
        )
    updated = runs.resume_paused_run(run.run_id)
    if updated is None:
        return _run_control_rejected(
            run.run_id,
            "RUN_RESUME_STATE_CONFLICT",
            "Refresh run progress and retry resume.",
        )
    return RunControlOutcome(
        applied=True,
        idempotent_replay=False,
        run_id=updated.run_id,
        run=updated,
        validation_codes=(),
        next_action="The run is queued for endpoint lease reacquisition and revalidation.",
    )


def request_run_stop_after_active_file(
    *,
    command: RunControlCommand,
    runs: RunControlStore,
) -> RunControlOutcome:
    run = runs.load_started_run(command.run_id)
    if run is None:
        return _run_control_rejected(
            command.run_id,
            "RUN_NOT_FOUND",
            "Refresh backup activity before stopping the run.",
        )
    if run.state is RunState.CANCELLED:
        return RunControlOutcome(
            applied=True,
            idempotent_replay=True,
            run_id=run.run_id,
            run=run,
            validation_codes=(),
            next_action="The run is already stopped.",
        )
    if run.state not in {
        RunState.CREATED,
        RunState.QUEUED,
        RunState.PREFLIGHT,
        RunState.EXECUTING,
        RunState.PAUSING,
        RunState.PAUSED,
    }:
        return _run_control_rejected(
            run.run_id,
            "RUN_NOT_STOPPABLE",
            "Only an active or paused backup run can be stopped.",
            run=run,
        )
    updated = runs.request_run_stop_after_active_file(run.run_id)
    if updated is None:
        return _run_control_rejected(
            run.run_id,
            "RUN_STOP_STATE_CONFLICT",
            "Refresh run progress and retry stop.",
        )
    return RunControlOutcome(
        applied=True,
        idempotent_replay=False,
        run_id=updated.run_id,
        run=updated,
        validation_codes=(),
        next_action="The executor will stop after the active file reaches a safe boundary.",
    )


def evaluate_start_run(
    *,
    command: StartRunCommand,
    plans: PlanStore,
    job_lifecycle: JobLifecycleStore | None = None,
) -> RunStartReadiness:
    plan = plans.load_sealed_plan(command.plan_id)
    if plan is None:
        return _missing_plan(command.plan_id)
    lifecycle_rejection = _job_lifecycle_run_rejection(
        job_lifecycle=job_lifecycle,
        job_id=plan.job_id,
        readiness=_readiness_for_plan(command=command, plan=plan),
    )
    if lifecycle_rejection is not None:
        return lifecycle_rejection
    return _readiness_for_plan(command=command, plan=plan)


def start_run_from_sealed_plan(
    *,
    command: StartRunCommand,
    plans: PlanStore,
    runs: RunStore,
    id_factory: RunIdFactory,
    operation_audit_store: OperationAuditReadModelStore | None = None,
    job_lifecycle: JobLifecycleStore | None = None,
    defer_until_recovery_bound: bool = False,
) -> RunStartOutcome:
    existing = runs.load_started_run_by_idempotency_key(
        _effective_run_idempotency_key(command)
    )
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

    lifecycle_rejection = _job_lifecycle_run_rejection(
        job_lifecycle=job_lifecycle,
        job_id=plan.job_id,
        readiness=_readiness_for_plan(command=command, plan=plan),
    )
    if lifecycle_rejection is not None:
        return RunStartOutcome(
            created=False,
            idempotent_replay=False,
            readiness=lifecycle_rejection,
        )

    retry_source: StartedRun | None = None
    effective_command = command
    if command.source_operation_ids and command.resumed_from_run_id is None:
        return RunStartOutcome(
            created=False,
            idempotent_replay=False,
            readiness=_not_ready_to_queue(
                _readiness_for_plan(command=command, plan=plan),
                "RUN_RETRY_OPERATION_REQUIRES_SOURCE_RUN",
                "Select a failed file from a terminal backup before retrying it.",
            ),
        )
    if command.resumed_from_run_id is not None:
        retry_source = runs.load_started_run(command.resumed_from_run_id)
        retry_error = _retry_source_validation_code(
            source=retry_source,
            plan=plan,
            target_endpoint_ids=command.target_endpoint_ids,
            operation_retry=bool(command.source_operation_ids),
        )
        if retry_error is not None:
            return RunStartOutcome(
                created=False,
                idempotent_replay=False,
                readiness=_not_ready_to_queue(
                    _readiness_for_plan(command=command, plan=plan),
                    retry_error,
                    "Run a fresh control and select a failed target before retrying.",
                ),
            )
        selected_operation_ids, operation_scope_error = _retry_operation_scope(
            source=retry_source,
            fresh_plan=plan,
            plans=plans,
            operation_audits=operation_audit_store,
            target_endpoint_ids=command.target_endpoint_ids,
            source_operation_ids=command.source_operation_ids,
        )
        if operation_scope_error is not None:
            return RunStartOutcome(
                created=False,
                idempotent_replay=False,
                readiness=_not_ready_to_queue(
                    _readiness_for_plan(command=command, plan=plan),
                    operation_scope_error,
                    "Run a fresh control and select an unfinished file that still needs work.",
                ),
            )
        if selected_operation_ids:
            effective_command = replace(
                command,
                selected_plan_operation_ids=selected_operation_ids,
            )

    readiness = _readiness_for_plan(command=effective_command, plan=plan)
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
        command=effective_command,
        plan=plan,
        ids=id_factory.new_run_ids(),
        retry_source=retry_source,
        initial_state=(
            RunState.CREATED if defer_until_recovery_bound else RunState.QUEUED
        ),
    )
    runs.save_started_run(run)
    return RunStartOutcome(
        created=True,
        idempotent_replay=False,
        readiness=readiness,
        run=run,
    )


def _job_lifecycle_run_rejection(
    *,
    job_lifecycle: JobLifecycleStore | None,
    job_id: str,
    readiness: RunStartReadiness,
) -> RunStartReadiness | None:
    if job_lifecycle is None:
        return None
    record = job_lifecycle.load_job_lifecycle(job_id)
    if record is not None and record.state is JobLifecycleState.ACTIVE:
        return None
    return _not_ready_to_queue(
        readiness,
        "RUN_JOB_ARCHIVED" if record is not None else "RUN_JOB_NOT_FOUND",
        "Reactivate and check the job before starting another backup.",
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
    if run.state not in {RunState.QUEUED, RunState.PREFLIGHT, RunState.EXECUTING}:
        return RunTargetPreflightOutcome(
            claimed=False,
            run_id=run_id,
            run_target_id=None,
            target=None,
            validation_codes=("RUN_NOT_READY_FOR_TARGET_PREFLIGHT",),
            next_action="Only queued, preflight, or executing runs can acquire target work.",
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
    if run.state not in {RunState.PREFLIGHT, RunState.EXECUTING}:
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
        wait_reason = endpoint_wait_reason(attempt)
        if wait_reason is not None:
            waiting = runs.record_run_target_waiting_for_endpoint(
                run_id=run_id,
                run_target_id=run_target_id,
                expected_state=RunTargetState.ACQUIRING_LEASE,
                reason_code=wait_reason,
            )
            if waiting is None:
                return RunTargetLeaseOutcome(
                    acquired=False,
                    run_id=run_id,
                    run_target_id=run_target_id,
                    target=None,
                    lease=None,
                    validation_codes=("RUN_TARGET_ENDPOINT_WAIT_RECORD_CONFLICT",),
                    next_action="Reload run state before recording endpoint wait.",
                )
            return RunTargetLeaseOutcome(
                acquired=False,
                run_id=run_id,
                run_target_id=run_target_id,
                target=waiting,
                lease=None,
                validation_codes=(),
                next_action="Target is waiting safely and will be retried on a later maintenance pass.",
            )
        return RunTargetLeaseOutcome(
            acquired=False,
            run_id=run_id,
            run_target_id=run_target_id,
            target=target,
            lease=None,
            validation_codes=attempt.validation_codes
            or ("RUN_TARGET_ENDPOINT_LEASE_UNAVAILABLE",),
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


def endpoint_wait_reason(attempt: EndpointLeaseAttempt) -> str | None:
    if len(attempt.validation_codes) != 1:
        return None
    reason_code = attempt.validation_codes[0]
    return reason_code if reason_code in WAITABLE_ENDPOINT_LEASE_CODES else None


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
        next_action="Target succeeded; run is complete."
        if updated_run.state is RunState.COMPLETED
        else "Target succeeded; remaining targets continue.",
    )


def complete_run_target_with_warnings(
    *,
    run_id: str,
    run_target_id: str,
    runs: RunWarningCompletionStore,
    completed_operations: int,
    completed_bytes: int,
    skipped_operations: int,
    skipped_bytes: int,
    last_error_code: str,
) -> RunTargetCompletionOutcome:
    normalized_error_code = _normalized_error_code(last_error_code)
    if (
        completed_operations < 0
        or completed_bytes < 0
        or skipped_operations < 1
        or skipped_bytes < 0
        or normalized_error_code is None
    ):
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=None,
            target=None,
            validation_codes=("RUN_TARGET_WARNING_COMPLETION_REQUIRES_VALID_COUNTS",),
            next_action="Retry warning completion with valid completed and skipped counts.",
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
    if target is None or target.state is not RunTargetState.EXECUTING:
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=target,
            validation_codes=("RUN_TARGET_NOT_EXECUTING",),
            next_action="Only executing targets can complete with warnings.",
        )
    if (
        completed_operations + skipped_operations != target.planned_operations
        or completed_bytes + skipped_bytes != target.planned_bytes
    ):
        return RunTargetCompletionOutcome(
            completed=False,
            run_completed=False,
            run_id=run_id,
            run_target_id=run_target_id,
            run=run,
            target=target,
            validation_codes=("RUN_TARGET_COMPLETION_COUNTS_MISMATCH",),
            next_action="Account for every completed or skipped file before finishing the target.",
        )

    updated_run = runs.record_run_target_succeeded_with_warnings(
        run_id=run_id,
        run_target_id=run_target_id,
        completed_operations=completed_operations,
        completed_bytes=completed_bytes,
        skipped_operations=skipped_operations,
        skipped_bytes=skipped_bytes,
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
            validation_codes=("RUN_TARGET_WARNING_COMPLETION_CONFLICT",),
            next_action="Reload run state before retrying warning completion.",
        )
    updated_target = _target_by_id(updated_run, run_target_id)
    if updated_target is None:
        raise RunStartViolation("RUN_TARGET_WARNING_COMPLETION_LOAD_FAILED")
    return RunTargetCompletionOutcome(
        completed=True,
        run_completed=updated_run.state is not RunState.EXECUTING,
        run_id=run_id,
        run_target_id=run_target_id,
        run=updated_run,
        target=updated_target,
        validation_codes=(),
        next_action=(
            "Target completed with skipped files; run is complete with warnings."
            if updated_run.state is RunState.COMPLETED_WITH_WARNINGS
            else "Target completed with skipped files; remaining targets continue."
        ),
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
        run_completed=updated_run.state
        in {RunState.CANCELLED, RunState.PARTIAL_FAILURE},
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


def _readiness_for_plan(
    *, command: StartRunCommand, plan: SealedPlan
) -> RunStartReadiness:
    validation_codes: list[str] = []
    target_scope_error = _target_scope_validation_code(command.target_endpoint_ids)
    if target_scope_error is not None:
        validation_codes.append(target_scope_error)
    operation_scope_error = _operation_scope_validation_code(
        command.selected_plan_operation_ids
    )
    if operation_scope_error is not None:
        validation_codes.append(operation_scope_error)
    if command.source_operation_ids and not command.selected_plan_operation_ids:
        validation_codes.append("RUN_RETRY_OPERATION_SCOPE_UNRESOLVED")
    checksum_matches = plan.plan_checksum == command.plan_checksum
    checksum_valid = verify_plan_checksum(plan)
    if not checksum_matches:
        validation_codes.append("PLAN_CHECKSUM_MISMATCH")
    if not checksum_valid:
        validation_codes.append("PLAN_CHECKSUM_INVALID")
    if not plan.immutable:
        validation_codes.append("PLAN_NOT_IMMUTABLE")
    selected_operations = _selected_plan_operations(
        plan,
        command.selected_plan_operation_ids,
    )
    known_operation_ids = {operation.operation_id for operation in plan.operations}
    if (
        command.selected_plan_operation_ids
        and set(command.selected_plan_operation_ids) - known_operation_ids
    ):
        validation_codes.append("PLAN_OPERATION_SCOPE_UNKNOWN")
    if command.selected_plan_operation_ids and any(
        operation.operation_type not in MUTATING_OPERATION_TYPES
        for operation in selected_operations
    ):
        validation_codes.append("PLAN_OPERATION_SCOPE_NOT_MUTATING")
    target_endpoints = _selected_target_endpoints(
        plan,
        command.target_endpoint_ids,
        command.selected_plan_operation_ids,
    )
    known_target_ids = {endpoint.endpoint_id for endpoint in _target_endpoints(plan)}
    if command.target_endpoint_ids and set(command.target_endpoint_ids) - known_target_ids:
        validation_codes.append("PLAN_TARGET_SCOPE_UNKNOWN")
    if command.selected_plan_operation_ids and any(
        operation.target_endpoint_id not in set(command.target_endpoint_ids)
        for operation in selected_operations
    ):
        validation_codes.append("PLAN_OPERATION_SCOPE_TARGET_MISMATCH")
    scope_blocked = _selected_scope_is_blocked(
        plan,
        command.target_endpoint_ids,
        command.selected_plan_operation_ids,
    )
    if scope_blocked:
        validation_codes.append("PLAN_BLOCKED")
    executable_operations = _executable_operations_for_endpoints(
        plan,
        target_endpoints,
    )
    selected_operation_count = (
        len(selected_operations)
        if command.selected_plan_operation_ids
        else len(executable_operations)
    )
    if selected_operation_count < 1 and target_endpoints and not scope_blocked:
        validation_codes.append("PLAN_REQUIRES_EXECUTABLE_OPERATIONS")
    if not target_endpoints:
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
    retry_source: StartedRun | None = None,
    initial_state: RunState = RunState.QUEUED,
) -> StartedRun:
    target_endpoints = _selected_target_endpoints(
        plan,
        command.target_endpoint_ids,
        command.selected_plan_operation_ids,
    )
    selected_operations = _selected_plan_operations(
        plan,
        command.selected_plan_operation_ids,
    )
    executable_operations = _executable_operations_for_endpoints(
        plan,
        target_endpoints,
    )
    deferred_operations = tuple(
        operation
        for operation in plan.operations
        if operation.operation_type is PlanOperationType.DEFER_AUTOMATION_POLICY
        and operation.target_endpoint_id
        in {endpoint.endpoint_id for endpoint in target_endpoints}
    )
    operation_scoped = bool(command.selected_plan_operation_ids)
    scoped = bool(command.target_endpoint_ids) or operation_scoped
    return StartedRun(
        run_id=ids.run_id,
        job_id=plan.job_id,
        job_revision_id=plan.job_revision_id,
        plan_id=plan.plan_id,
        command_request_id=command.request_id,
        idempotency_key=_effective_run_idempotency_key(command),
        command_receipt_id=command.idempotency_key,
        logical_run_group_id=(
            retry_source.logical_run_group_id
            if retry_source is not None
            else ids.logical_run_group_id
        ),
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=initial_state,
        app_version=APP_VERSION,
        plan_checksum=plan.plan_checksum,
        planned_operations=(
            len(selected_operations)
            if operation_scoped
            else len(executable_operations)
        ),
        planned_bytes=(
            sum(operation.planned_bytes for operation in selected_operations)
            if operation_scoped
            else sum(operation.planned_bytes for operation in executable_operations)
        ),
        trigger_occurrence_id=command.trigger_occurrence_id,
        resumed_from_run_id=command.resumed_from_run_id,
        targets=tuple(
            _started_run_target(
                ids.run_id,
                endpoint,
                operations=(selected_operations if operation_scoped else ()),
            )
            for endpoint in target_endpoints
        ),
        summary={
            "executor_pending": True,
            "action_required": bool(deferred_operations),
            "automation_policy": plan.execution_policy,
            "deferred_operation_count": len(deferred_operations),
            "deferred_planned_bytes": sum(
                operation.planned_bytes for operation in deferred_operations
            ),
            "scope": (
                "OPERATION_RETRY"
                if command.source_operation_ids
                else "TARGET_RETRY"
                if retry_source is not None
                else "0B_RUN_START_SKELETON"
            ),
            **(
                {"target_endpoint_ids": list(command.target_endpoint_ids)}
                if scoped
                else {}
            ),
            **(
                {"resumed_from_run_id": command.resumed_from_run_id}
                if command.resumed_from_run_id is not None
                else {}
            ),
            **(
                {"source_operation_ids": list(command.source_operation_ids)}
                if command.source_operation_ids
                else {}
            ),
            **(
                {"operation_ids": list(command.selected_plan_operation_ids)}
                if command.selected_plan_operation_ids
                else {}
            ),
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


def _selected_target_endpoints(
    plan: SealedPlan,
    target_endpoint_ids: tuple[str, ...],
    operation_ids: tuple[str, ...] = (),
) -> tuple[PlanEndpoint, ...]:
    endpoints = _target_endpoints(plan)
    if operation_ids:
        selected_operation_ids = set(operation_ids)
        selected_endpoint_ids = {
            operation.target_endpoint_id
            for operation in plan.operations
            if operation.operation_id in selected_operation_ids
            and operation.target_endpoint_id is not None
        }
        return tuple(
            endpoint
            for endpoint in endpoints
            if endpoint.endpoint_id in selected_endpoint_ids
        )
    if not target_endpoint_ids:
        return endpoints
    selected = set(target_endpoint_ids)
    return tuple(endpoint for endpoint in endpoints if endpoint.endpoint_id in selected)


def _selected_scope_is_blocked(
    plan: SealedPlan,
    target_endpoint_ids: tuple[str, ...],
    operation_ids: tuple[str, ...] = (),
) -> bool:
    if operation_ids:
        selected = set(operation_ids)
        return any(
            operation.operation_id in selected
            and operation.risk_level.value == "BLOCKED"
            for operation in plan.operations
        )
    if not target_endpoint_ids:
        return plan.risk_summary.get("highest") == "BLOCKED"
    selected = set(target_endpoint_ids)
    return any(
        operation.risk_level.value == "BLOCKED"
        and (
            operation.target_endpoint_id is None
            or operation.target_endpoint_id in selected
        )
        for operation in plan.operations
    )


def _selected_plan_operations(
    plan: SealedPlan,
    operation_ids: tuple[str, ...],
) -> tuple[PlanOperation, ...]:
    if not operation_ids:
        return plan.operations
    selected = set(operation_ids)
    return tuple(
        operation
        for operation in plan.operations
        if operation.operation_id in selected
    )


def _executable_operations_for_endpoints(
    plan: SealedPlan,
    endpoints: tuple[PlanEndpoint, ...],
) -> tuple[PlanOperation, ...]:
    endpoint_ids = {endpoint.endpoint_id for endpoint in endpoints}
    return tuple(
        operation
        for operation in plan.operations
        if operation.operation_type in MUTATING_OPERATION_TYPES
        and operation.target_endpoint_id in endpoint_ids
    )


def _retry_operation_scope(
    *,
    source: StartedRun | None,
    fresh_plan: SealedPlan,
    plans: PlanStore,
    operation_audits: OperationAuditReadModelStore | None,
    target_endpoint_ids: tuple[str, ...],
    source_operation_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], str | None]:
    if not source_operation_ids:
        return (), None
    if source is None:
        return (), "RUN_RETRY_SOURCE_NOT_FOUND"
    if operation_audits is None:
        return (), "RUN_RETRY_OPERATION_AUDIT_UNAVAILABLE"
    source_plan = plans.load_sealed_plan(source.plan_id)
    if (
        source_plan is None
        or source_plan.plan_checksum != source.plan_checksum
        or not source_plan.immutable
        or not verify_plan_checksum(source_plan)
    ):
        return (), "RUN_RETRY_SOURCE_PLAN_INVALID"

    source_operations = {
        operation.operation_id: operation for operation in source_plan.operations
    }
    source_targets = {target.endpoint_id: target for target in source.targets}
    selected_fresh_ids: set[str] = set()
    for source_operation_id in source_operation_ids:
        source_operation = source_operations.get(source_operation_id)
        if source_operation is None:
            return (), "RUN_RETRY_OPERATION_NOT_IN_SOURCE_PLAN"
        endpoint_id = source_operation.target_endpoint_id
        relative_path = source_operation.target_relative_path
        source_target = source_targets.get(endpoint_id or "")
        if (
            endpoint_id is None
            or endpoint_id not in target_endpoint_ids
            or relative_path is None
            or source_target is None
        ):
            return (), "RUN_RETRY_OPERATION_TARGET_MISMATCH"
        try:
            identity = operation_audits.load_operation_audit_identity(
                run_id=source.run_id,
                operation_id=source_operation_id,
            )
            outcome = operation_audits.load_operation_outcome_summary(
                run_id=source.run_id,
                operation_id=source_operation_id,
            )
        except (RuntimeError, ValueError):
            return (), "RUN_RETRY_OPERATION_AUDIT_UNAVAILABLE"
        if (
            identity is None
            or identity.run_target_id != source_target.run_target_id
            or identity.target_relative_path != relative_path
        ):
            return (), "RUN_RETRY_OPERATION_AUDIT_MISMATCH"
        if outcome is None:
            return (), "RUN_RETRY_OPERATION_OUTCOME_NOT_FOUND"
        if outcome.final_state == "SUCCEEDED":
            return (), "RUN_RETRY_OPERATION_ALREADY_SUCCEEDED"
        if outcome.final_state not in {"SKIPPED", "CANCELLED", "RECOVERY_REQUIRED"}:
            return (), "RUN_RETRY_OPERATION_NOT_RETRYABLE"

        matches = tuple(
            operation
            for operation in fresh_plan.operations
            if operation.operation_type in MUTATING_OPERATION_TYPES
            and operation.target_endpoint_id == endpoint_id
            and operation.target_relative_path == relative_path
        )
        if len(matches) != 1:
            return (), "RUN_RETRY_OPERATION_NOT_IN_FRESH_PLAN"
        selected_fresh_ids.add(matches[0].operation_id)

    dependency_map: dict[str, set[str]] = {}
    for dependency in fresh_plan.dependencies:
        dependency_map.setdefault(dependency.after_operation_id, set()).add(
            dependency.before_operation_id
        )
    pending = list(selected_fresh_ids)
    while pending:
        operation_id = pending.pop()
        for required_id in dependency_map.get(operation_id, set()):
            if required_id not in selected_fresh_ids:
                selected_fresh_ids.add(required_id)
                pending.append(required_id)

    selected_operations = tuple(
        operation
        for operation in fresh_plan.operations
        if operation.operation_id in selected_fresh_ids
    )
    if len(selected_operations) != len(selected_fresh_ids):
        return (), "RUN_RETRY_OPERATION_DEPENDENCY_NOT_FOUND"
    if any(
        operation.operation_type not in MUTATING_OPERATION_TYPES
        or operation.target_endpoint_id not in target_endpoint_ids
        for operation in selected_operations
    ):
        return (), "RUN_RETRY_OPERATION_DEPENDENCY_INVALID"
    return tuple(operation.operation_id for operation in selected_operations), None


def _retry_source_validation_code(
    *,
    source: StartedRun | None,
    plan: SealedPlan,
    target_endpoint_ids: tuple[str, ...],
    operation_retry: bool = False,
) -> str | None:
    if source is None:
        return "RUN_RETRY_SOURCE_NOT_FOUND"
    if not target_endpoint_ids:
        return "RUN_RETRY_REQUIRES_TARGET_SCOPE"
    if source.state not in _TERMINAL_RUN_STATES:
        return "RUN_RETRY_SOURCE_NOT_TERMINAL"
    if source.job_id != plan.job_id:
        return "RUN_RETRY_SOURCE_JOB_MISMATCH"
    source_targets = {target.endpoint_id: target for target in source.targets}
    retryable_states = (
        _OPERATION_RETRYABLE_RUN_TARGET_STATES
        if operation_retry
        else _RETRYABLE_RUN_TARGET_STATES
    )
    for endpoint_id in target_endpoint_ids:
        target = source_targets.get(endpoint_id)
        if target is None:
            return "RUN_RETRY_TARGET_NOT_IN_SOURCE"
        if target.state not in retryable_states:
            return "RUN_RETRY_TARGET_NOT_FAILED"
        if source.plan_id == plan.plan_id:
            return "RUN_RETRY_REQUIRES_FRESH_PLAN"
    return None


def _not_ready_to_queue(
    readiness: RunStartReadiness,
    validation_code: str,
    next_action: str,
) -> RunStartReadiness:
    return RunStartReadiness(
        plan_id=readiness.plan_id,
        plan_found=readiness.plan_found,
        plan_checksum_matches=readiness.plan_checksum_matches,
        plan_checksum_valid=readiness.plan_checksum_valid,
        plan_runnable=False,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _parse_target_endpoint_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    if "target_endpoint_ids" not in payload:
        return ()
    value = payload.get("target_endpoint_ids")
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_RUN_TARGET_SCOPE:
        raise RunStartViolation("START_RUN_TARGET_SCOPE_INVALID")
    normalized: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 128
            or item != item.strip()
        ):
            raise RunStartViolation("START_RUN_TARGET_SCOPE_INVALID")
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        raise RunStartViolation("START_RUN_TARGET_SCOPE_DUPLICATE")
    return tuple(normalized)


def _target_scope_validation_code(
    target_endpoint_ids: tuple[str, ...],
) -> str | None:
    if not target_endpoint_ids:
        return None
    if not 1 <= len(target_endpoint_ids) <= MAX_RUN_TARGET_SCOPE:
        return "START_RUN_TARGET_SCOPE_INVALID"
    if len(set(target_endpoint_ids)) != len(target_endpoint_ids):
        return "START_RUN_TARGET_SCOPE_DUPLICATE"
    if any(
        not endpoint_id.strip()
        or len(endpoint_id) > 128
        or endpoint_id != endpoint_id.strip()
        for endpoint_id in target_endpoint_ids
    ):
        return "START_RUN_TARGET_SCOPE_INVALID"
    return None


def _operation_scope_validation_code(
    operation_ids: tuple[str, ...],
) -> str | None:
    if not operation_ids:
        return None
    if not 1 <= len(operation_ids) <= MAX_RUN_OPERATION_SCOPE:
        return "START_RUN_OPERATION_SCOPE_INVALID"
    if len(set(operation_ids)) != len(operation_ids):
        return "START_RUN_OPERATION_SCOPE_DUPLICATE"
    if any(
        not operation_id.strip()
        or len(operation_id) > MAX_RUN_OPERATION_ID_LENGTH
        or operation_id != operation_id.strip()
        for operation_id in operation_ids
    ):
        return "START_RUN_OPERATION_SCOPE_INVALID"
    return None


def _parse_source_operation_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    if "source_operation_ids" not in payload:
        return ()
    value = payload.get("source_operation_ids")
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_RUN_OPERATION_SCOPE:
        raise RunStartViolation("START_RUN_OPERATION_SCOPE_INVALID")
    normalized: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > MAX_RUN_OPERATION_ID_LENGTH
            or item != item.strip()
        ):
            raise RunStartViolation("START_RUN_OPERATION_SCOPE_INVALID")
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        raise RunStartViolation("START_RUN_OPERATION_SCOPE_DUPLICATE")
    return tuple(normalized)


def _parse_resumed_from_run_id(payload: dict[str, Any]) -> str | None:
    if "resumed_from_run_id" not in payload:
        return None
    value = payload.get("resumed_from_run_id")
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or value != value.strip()
    ):
        raise RunStartViolation("START_RUN_RETRY_SOURCE_INVALID")
    return value


def _target_by_id(run: StartedRun, run_target_id: str) -> StartedRunTarget | None:
    return next(
        (target for target in run.targets if target.run_target_id == run_target_id),
        None,
    )


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


def _run_control_rejected(
    run_id: str,
    validation_code: str,
    next_action: str,
    *,
    run: StartedRun | None = None,
) -> RunControlOutcome:
    return RunControlOutcome(
        applied=False,
        idempotent_replay=False,
        run_id=run_id,
        run=run,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _started_run_target(
    run_id: str,
    endpoint: PlanEndpoint,
    *,
    operations: tuple[PlanOperation, ...] = (),
) -> StartedRunTarget:
    target_ordinal = 0 if endpoint.target_ordinal is None else endpoint.target_ordinal
    scoped_operations = tuple(
        operation
        for operation in operations
        if operation.target_endpoint_id == endpoint.endpoint_id
    )
    return StartedRunTarget(
        run_target_id=f"{run_id}-target-{target_ordinal:04d}",
        endpoint_id=endpoint.endpoint_id,
        endpoint_revision_id=endpoint.endpoint_revision_id,
        state=RunTargetState.PENDING,
        required_owner_installation_id=endpoint.required_owner_installation_id,
        required_ownership_epoch=endpoint.required_ownership_epoch,
        lease_resource_key=f"endpoint:{endpoint.endpoint_id}",
        planned_operations=(
            len(scoped_operations) if operations else endpoint.planned_operations
        ),
        planned_bytes=(
            sum(operation.planned_bytes for operation in scoped_operations)
            if operations
            else endpoint.planned_bytes
        ),
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
