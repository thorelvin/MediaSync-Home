from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PureWindowsPath
from typing import Protocol

from mediasync_home.application.clocks import (
    ClockPort,
    MonotonicClaimExpired,
    MonotonicClaimWindow,
)
from mediasync_home.application.schedules import (
    ScheduleDefinition,
    ScheduleStore,
    validate_schedule_definition,
    validate_schedule_reconciliation_page_request,
)
from mediasync_home.application.external_resources import (
    ExternalResourceRecord,
    ExternalResourceState,
    ExternalResourceStateStore,
    ExternalResourceType,
    validate_external_resource_claim,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


TASK_SCHEDULER_HASH_PLACEHOLDER = "<TASK_DEFINITION_HASH>"
TASK_SCHEDULER_SCHEMA_VERSION = 1
LOCAL_TASK_SCHEDULER_LOGON_TYPE = "INTERACTIVE_TOKEN"
MAX_TASK_SCHEDULER_RECONCILIATION_PUMP_PAGES = 100
MAX_TASK_SCHEDULER_RECONCILIATION_PUMP_CLAIMS = 500
MAX_TASK_SCHEDULER_ORPHAN_RECONCILIATION_LIMIT = 100


class TaskSchedulerDefinitionViolation(ValueError):
    pass


class TaskSchedulerClaimExpired(MonotonicClaimExpired):
    def __init__(self, *, applied: bool) -> None:
        super().__init__("TASK_SCHEDULER_CLAIM_DEADLINE_EXPIRED")
        self.applied = applied


class TaskSchedulerReconciliationAction(str, Enum):
    CREATE = "CREATE"
    IN_SYNC = "IN_SYNC"
    UPDATE_DRIFTED = "UPDATE_DRIFTED"
    ENABLE_OWNED_TASK = "ENABLE_OWNED_TASK"
    DISABLE_OWNED_TASK = "DISABLE_OWNED_TASK"
    UPDATE_OWNED_DEFINITION = "UPDATE_OWNED_DEFINITION"
    DELETE_OWNED_TASK = "DELETE_OWNED_TASK"
    BLOCK_ARGUMENT_DRIFT = "BLOCK_ARGUMENT_DRIFT"
    BLOCK_BINARY_DRIFT = "BLOCK_BINARY_DRIFT"
    BLOCK_INVALID_DESIRED_STATE = "BLOCK_INVALID_DESIRED_STATE"
    BLOCK_UNKNOWN_TASK = "BLOCK_UNKNOWN_TASK"
    RETRY_CLAIM_EXPIRED = "RETRY_CLAIM_EXPIRED"


SAFE_TASK_SCHEDULER_APPLY_ACTIONS = frozenset(
    {
        TaskSchedulerReconciliationAction.CREATE,
        TaskSchedulerReconciliationAction.UPDATE_DRIFTED,
        TaskSchedulerReconciliationAction.ENABLE_OWNED_TASK,
        TaskSchedulerReconciliationAction.DISABLE_OWNED_TASK,
        TaskSchedulerReconciliationAction.UPDATE_OWNED_DEFINITION,
    }
)


@dataclass(frozen=True)
class TriggerTaskArgumentBinding:
    installation_id: str
    schedule_id: str
    schedule_revision_hash: str
    trigger_kind: TriggerKind
    task_definition_hash: str


@dataclass(frozen=True)
class TaskSchedulerDefinition:
    task_path: str
    executable_path: str
    arguments: tuple[str, ...]
    definition_hash: str
    enabled: bool
    trigger_type: TriggerKind
    configuration_json: str
    time_zone_id: str | None
    task_logon_type: str
    run_only_when_logged_on: bool
    requires_network: bool
    multiple_instances_policy: str = "PARALLEL"
    execution_time_limit_seconds: int = 0
    stop_on_execution_time_limit: bool = False


@dataclass(frozen=True)
class ObservedTaskSchedulerDefinition:
    task_path: str
    executable_path: str
    arguments: tuple[str, ...]
    enabled: bool
    trigger_type: TriggerKind
    configuration_json: str
    time_zone_id: str | None
    task_logon_type: str
    run_only_when_logged_on: bool
    requires_network: bool
    multiple_instances_policy: str = "PARALLEL"
    execution_time_limit_seconds: int = 0
    stop_on_execution_time_limit: bool = False


@dataclass(frozen=True)
class TaskSchedulerReconciliationPlan:
    action: TaskSchedulerReconciliationAction
    desired: TaskSchedulerDefinition
    observed: ObservedTaskSchedulerDefinition | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TaskSchedulerReconciliationRequest:
    installation_id: str
    executable_path: str
    limit: int
    after_schedule_id: str | None = None


@dataclass(frozen=True)
class TaskSchedulerPendingResourceReconciliationRequest:
    installation_id: str
    executable_path: str
    owner_instance_id: str
    claim_token: str
    claim_ttl_ms: int


@dataclass(frozen=True)
class TaskSchedulerResourcePumpRequest:
    installation_id: str
    executable_path: str
    owner_instance_id: str
    claim_token_prefix: str
    claim_ttl_ms: int
    schedule_page_limit: int
    max_schedule_pages: int
    max_claims: int
    after_schedule_id: str | None = None
    orphan_task_page_limit: int = MAX_TASK_SCHEDULER_ORPHAN_RECONCILIATION_LIMIT
    after_orphan_task_name: str | None = None


@dataclass(frozen=True)
class TaskSchedulerOrphanReconciliationRequest:
    installation_id: str
    executable_path: str
    limit: int
    after_task_name: str | None = None


@dataclass(frozen=True)
class TaskSchedulerUninstallCleanupRequest:
    installation_id: str
    executable_path: str
    limit: int
    after_task_name: str | None = None


@dataclass(frozen=True)
class TaskSchedulerReconciliationFinding:
    schedule_id: str
    action: TaskSchedulerReconciliationAction
    task_path: str | None = None
    reason: str | None = None
    applied: bool = False


@dataclass(frozen=True)
class TaskSchedulerReconciliationReport:
    scanned: int
    applied: int
    blocked: int
    next_cursor: str | None
    findings: tuple[TaskSchedulerReconciliationFinding, ...]


@dataclass(frozen=True)
class TaskSchedulerDesiredResourceFinding:
    schedule_id: str
    task_path: str | None
    staged: bool
    reason: str | None = None


@dataclass(frozen=True)
class TaskSchedulerDesiredResourceReport:
    scanned: int
    staged: int
    blocked: int
    next_cursor: str | None
    findings: tuple[TaskSchedulerDesiredResourceFinding, ...]


@dataclass(frozen=True)
class TaskSchedulerClaimedResourceReconciliation:
    resource_id: str
    action: TaskSchedulerReconciliationAction
    applied: bool
    completed: bool
    blocked: bool
    reason: str | None = None


@dataclass(frozen=True)
class TaskSchedulerOrphanReconciliationFinding:
    task_path: str
    task_name: str
    action: TaskSchedulerReconciliationAction
    deleted: bool
    blocked: bool
    schedule_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TaskSchedulerOrphanReconciliationReport:
    scanned: int
    deleted: int
    blocked: int
    next_cursor: str | None
    findings: tuple[TaskSchedulerOrphanReconciliationFinding, ...]


@dataclass(frozen=True)
class TaskSchedulerUninstallCleanupFinding:
    task_path: str
    task_name: str
    deleted: bool
    blocked: bool
    schedule_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TaskSchedulerUninstallCleanupReport:
    scanned: int
    deleted: int
    blocked: int
    next_cursor: str | None
    findings: tuple[TaskSchedulerUninstallCleanupFinding, ...]


@dataclass(frozen=True)
class TaskSchedulerResourcePumpReport:
    schedule_pages_attempted: int
    schedules_scanned: int
    resources_staged: int
    stage_blocked: int
    stage_completed: bool
    stage_next_cursor: str | None
    claims_attempted: int
    resources_reconciled: int
    resources_applied: int
    resources_completed: int
    resources_blocked: int
    claim_idle: bool
    stage_findings: tuple[TaskSchedulerDesiredResourceFinding, ...]
    claim_findings: tuple[TaskSchedulerClaimedResourceReconciliation, ...]
    orphan_tasks_scanned: int = 0
    orphan_tasks_deleted: int = 0
    orphan_tasks_blocked: int = 0
    orphan_next_cursor: str | None = None
    orphan_findings: tuple[TaskSchedulerOrphanReconciliationFinding, ...] = ()


class TaskSchedulerRegistryPort(Protocol):
    def load_task(self, task_path: str) -> ObservedTaskSchedulerDefinition | None: ...

    def list_tasks(
        self,
        folder_path: str,
        *,
        limit: int,
        after_task_name: str | None = None,
    ) -> tuple[ObservedTaskSchedulerDefinition, ...]: ...

    def apply_task_definition(self, definition: TaskSchedulerDefinition) -> None: ...

    def delete_task(self, task_path: str) -> None: ...


def bind_same_user_task_scheduler_definition_hash(
    schedule: ScheduleDefinition,
    *,
    installation_id: str,
    executable_path: str,
) -> ScheduleDefinition:
    return replace(
        schedule,
        desired_definition_hash=derive_same_user_task_scheduler_definition_hash(
            schedule,
            installation_id=installation_id,
            executable_path=executable_path,
        ),
    )


def build_same_user_task_scheduler_definition(
    schedule: ScheduleDefinition,
    *,
    installation_id: str,
    executable_path: str,
) -> TaskSchedulerDefinition:
    validate_schedule_definition(schedule)
    _validate_same_user_task_scheduler_policy(schedule)
    expected_hash = derive_same_user_task_scheduler_definition_hash(
        schedule,
        installation_id=installation_id,
        executable_path=executable_path,
    )
    if schedule.desired_definition_hash != expected_hash:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_DESIRED_HASH_MISMATCH")

    canonical_configuration = _canonical_configuration_json(schedule.configuration_json)
    return TaskSchedulerDefinition(
        task_path=_task_path(installation_id, schedule.schedule_id),
        executable_path=_normalized_executable_path(executable_path),
        arguments=_trigger_task_arguments(
            installation_id=installation_id,
            schedule_id=schedule.schedule_id,
            schedule_revision_hash=expected_hash,
            trigger_kind=schedule.trigger_type,
            task_definition_hash=expected_hash,
        ),
        definition_hash=expected_hash,
        enabled=schedule.enabled,
        trigger_type=schedule.trigger_type,
        configuration_json=canonical_configuration,
        time_zone_id=schedule.time_zone_id,
        task_logon_type=schedule.task_logon_type,
        run_only_when_logged_on=schedule.run_only_when_logged_on,
        requires_network=schedule.requires_network,
    )


def derive_same_user_task_scheduler_definition_hash(
    schedule: ScheduleDefinition,
    *,
    installation_id: str,
    executable_path: str,
) -> str:
    validate_schedule_definition(schedule)
    _validate_same_user_task_scheduler_policy(schedule)
    material = _definition_hash_material(
        schedule,
        installation_id=installation_id,
        executable_path=_normalized_executable_path(executable_path),
    )
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_task_scheduler_reconciliation(
    schedule: ScheduleDefinition,
    *,
    installation_id: str,
    executable_path: str,
    observed: ObservedTaskSchedulerDefinition | None,
) -> TaskSchedulerReconciliationPlan:
    desired = build_same_user_task_scheduler_definition(
        schedule,
        installation_id=installation_id,
        executable_path=executable_path,
    )
    if observed is None:
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.CREATE,
            desired=desired,
            reason="TASK_SCHEDULER_TASK_MISSING",
        )
    if observed.task_path != desired.task_path:
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.BLOCK_UNKNOWN_TASK,
            desired=desired,
            observed=observed,
            reason="TASK_SCHEDULER_TASK_PATH_MISMATCH",
        )

    binding = parse_trigger_task_arguments(observed.arguments)
    if binding is None:
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.BLOCK_ARGUMENT_DRIFT,
            desired=desired,
            observed=observed,
            reason="TASK_SCHEDULER_ARGUMENTS_NOT_RECOGNIZED",
        )
    if binding.installation_id != installation_id or binding.schedule_id != schedule.schedule_id:
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.BLOCK_UNKNOWN_TASK,
            desired=desired,
            observed=observed,
            reason="TASK_SCHEDULER_ARGUMENT_OWNER_MISMATCH",
        )
    if _windows_path_key(observed.executable_path) != _windows_path_key(desired.executable_path):
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.BLOCK_BINARY_DRIFT,
            desired=desired,
            observed=observed,
            reason="TASK_SCHEDULER_EXECUTABLE_DRIFT",
        )
    observed_payload = _observed_payload(observed)
    desired_payload = _desired_payload(desired)
    if observed_payload == desired_payload:
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.IN_SYNC,
            desired=desired,
            observed=observed,
        )
    if _payload_without_enabled(observed_payload) == _payload_without_enabled(desired_payload):
        if desired.enabled:
            return TaskSchedulerReconciliationPlan(
                TaskSchedulerReconciliationAction.ENABLE_OWNED_TASK,
                desired=desired,
                observed=observed,
                reason="TASK_SCHEDULER_OWNED_TASK_DISABLED",
            )
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.DISABLE_OWNED_TASK,
            desired=desired,
            observed=observed,
            reason="TASK_SCHEDULER_OWNED_TASK_STILL_ENABLED",
        )
    return TaskSchedulerReconciliationPlan(
        TaskSchedulerReconciliationAction.UPDATE_OWNED_DEFINITION,
        desired=desired,
        observed=observed,
        reason="TASK_SCHEDULER_OWNED_DEFINITION_DRIFT",
    )


def reconcile_task_scheduler_page(
    request: TaskSchedulerReconciliationRequest,
    *,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
) -> TaskSchedulerReconciliationReport:
    validate_schedule_reconciliation_page_request(
        limit=request.limit,
        after_schedule_id=request.after_schedule_id,
    )
    page = schedules.list_schedules_for_reconciliation(
        limit=request.limit,
        after_schedule_id=request.after_schedule_id,
    )
    findings: list[TaskSchedulerReconciliationFinding] = []
    applied = 0
    blocked = 0
    for schedule in page:
        try:
            desired = build_same_user_task_scheduler_definition(
                schedule,
                installation_id=request.installation_id,
                executable_path=request.executable_path,
            )
        except TaskSchedulerDefinitionViolation as exc:
            blocked += 1
            findings.append(
                TaskSchedulerReconciliationFinding(
                    schedule_id=schedule.schedule_id,
                    action=TaskSchedulerReconciliationAction.BLOCK_INVALID_DESIRED_STATE,
                    task_path=_task_path_or_none(request.installation_id, schedule.schedule_id),
                    reason=str(exc),
                )
            )
            continue

        plan = classify_task_scheduler_reconciliation(
            schedule,
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            observed=registry.load_task(desired.task_path),
        )
        did_apply = False
        if plan.action in SAFE_TASK_SCHEDULER_APPLY_ACTIONS:
            registry.apply_task_definition(plan.desired)
            applied += 1
            did_apply = True
        if plan.action.value.startswith("BLOCK_"):
            blocked += 1
        findings.append(
            TaskSchedulerReconciliationFinding(
                schedule_id=schedule.schedule_id,
                action=plan.action,
                task_path=plan.desired.task_path,
                reason=plan.reason,
                applied=did_apply,
            )
        )

    return TaskSchedulerReconciliationReport(
        scanned=len(page),
        applied=applied,
        blocked=blocked,
        next_cursor=page[-1].schedule_id if len(page) == request.limit else None,
        findings=tuple(findings),
    )


def stage_task_scheduler_desired_resource_page(
    request: TaskSchedulerReconciliationRequest,
    *,
    schedules: ScheduleStore,
    external_resources: ExternalResourceStateStore,
) -> TaskSchedulerDesiredResourceReport:
    validate_schedule_reconciliation_page_request(
        limit=request.limit,
        after_schedule_id=request.after_schedule_id,
    )
    page = schedules.list_schedules_for_reconciliation(
        limit=request.limit,
        after_schedule_id=request.after_schedule_id,
    )
    findings: list[TaskSchedulerDesiredResourceFinding] = []
    staged = 0
    blocked = 0
    for schedule in page:
        try:
            definition = build_same_user_task_scheduler_definition(
                schedule,
                installation_id=request.installation_id,
                executable_path=request.executable_path,
            )
        except TaskSchedulerDefinitionViolation as exc:
            blocked += 1
            findings.append(
                TaskSchedulerDesiredResourceFinding(
                    schedule_id=schedule.schedule_id,
                    task_path=_task_path_or_none(request.installation_id, schedule.schedule_id),
                    staged=False,
                    reason=str(exc),
                )
            )
            continue

        external_resources.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id=schedule.schedule_id,
            desired_generation=schedule.definition_generation,
            desired_hash=definition.definition_hash,
        )
        staged += 1
        findings.append(
            TaskSchedulerDesiredResourceFinding(
                schedule_id=schedule.schedule_id,
                task_path=definition.task_path,
                staged=True,
            )
        )

    return TaskSchedulerDesiredResourceReport(
        scanned=len(page),
        staged=staged,
        blocked=blocked,
        next_cursor=page[-1].schedule_id if len(page) == request.limit else None,
        findings=tuple(findings),
    )


def reconcile_claimed_task_scheduler_resource(
    claimed: ExternalResourceRecord,
    *,
    installation_id: str,
    executable_path: str,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
    external_resources: ExternalResourceStateStore,
    clock: ClockPort,
    claim_window: MonotonicClaimWindow,
) -> TaskSchedulerClaimedResourceReconciliation:
    if claimed.resource_type is not ExternalResourceType.TASK_SCHEDULER:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_CLAIM_RESOURCE_TYPE_MISMATCH")
    if claimed.state is not ExternalResourceState.CLAIMED or claimed.claim_token is None:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_RESOURCE_MUST_BE_CLAIMED")
    if (
        claimed.claim_started_utc != claim_window.started_utc
        or claimed.claim_ttl_ms != claim_window.ttl_ms
    ):
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_CLAIM_WINDOW_MISMATCH")
    _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
    schedule = schedules.load_schedule(claimed.resource_id)
    _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
    if schedule is None:
        _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
        external_resources.mark_external_resource_blocked(
            resource_type=claimed.resource_type,
            resource_id=claimed.resource_id,
            claim_token=claimed.claim_token,
            error_code="TASK_SCHEDULER_SCHEDULE_NOT_FOUND",
        )
        return TaskSchedulerClaimedResourceReconciliation(
            resource_id=claimed.resource_id,
            action=TaskSchedulerReconciliationAction.BLOCK_INVALID_DESIRED_STATE,
            applied=False,
            completed=False,
            blocked=True,
            reason="TASK_SCHEDULER_SCHEDULE_NOT_FOUND",
        )
    try:
        desired = build_same_user_task_scheduler_definition(
            schedule,
            installation_id=installation_id,
            executable_path=executable_path,
        )
        if (
            schedule.definition_generation != claimed.desired_generation
            or desired.definition_hash != claimed.desired_hash
        ):
            raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_CLAIM_DESIRED_DRIFT")
    except TaskSchedulerDefinitionViolation as exc:
        _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
        external_resources.mark_external_resource_blocked(
            resource_type=claimed.resource_type,
            resource_id=claimed.resource_id,
            claim_token=claimed.claim_token,
            error_code=str(exc),
        )
        return TaskSchedulerClaimedResourceReconciliation(
            resource_id=claimed.resource_id,
            action=TaskSchedulerReconciliationAction.BLOCK_INVALID_DESIRED_STATE,
            applied=False,
            completed=False,
            blocked=True,
            reason=str(exc),
        )

    plan = classify_task_scheduler_reconciliation(
        schedule,
        installation_id=installation_id,
        executable_path=executable_path,
        observed=registry.load_task(desired.task_path),
    )
    _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
    if plan.action.value.startswith("BLOCK_"):
        _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
        external_resources.mark_external_resource_blocked(
            resource_type=claimed.resource_type,
            resource_id=claimed.resource_id,
            claim_token=claimed.claim_token,
            error_code=plan.reason or plan.action.value,
        )
        return TaskSchedulerClaimedResourceReconciliation(
            resource_id=claimed.resource_id,
            action=plan.action,
            applied=False,
            completed=False,
            blocked=True,
            reason=plan.reason,
        )

    applied = False
    if plan.action in SAFE_TASK_SCHEDULER_APPLY_ACTIONS:
        _assert_task_scheduler_claim_active(clock, claim_window, applied=False)
        registry.apply_task_definition(plan.desired)
        applied = True
        _assert_task_scheduler_claim_active(clock, claim_window, applied=True)
    _assert_task_scheduler_claim_active(clock, claim_window, applied=applied)
    external_resources.mark_external_resource_in_sync(
        resource_type=claimed.resource_type,
        resource_id=claimed.resource_id,
        desired_generation=claimed.desired_generation,
        claim_token=claimed.claim_token,
        observed_hash=desired.definition_hash,
    )
    return TaskSchedulerClaimedResourceReconciliation(
        resource_id=claimed.resource_id,
        action=plan.action,
        applied=applied,
        completed=True,
        blocked=False,
        reason=plan.reason,
    )


def reconcile_next_pending_task_scheduler_resource(
    request: TaskSchedulerPendingResourceReconciliationRequest,
    *,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
    external_resources: ExternalResourceStateStore,
    clock: ClockPort,
) -> TaskSchedulerClaimedResourceReconciliation | None:
    _non_empty_text(request.installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    _normalized_executable_path(request.executable_path)
    claim_window = MonotonicClaimWindow.start(clock, ttl_ms=request.claim_ttl_ms)
    validate_external_resource_claim(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        owner_instance_id=request.owner_instance_id,
        claim_token=request.claim_token,
        claim_started_utc=claim_window.started_utc,
        claim_ttl_ms=request.claim_ttl_ms,
    )
    claimed = external_resources.claim_next_pending_external_resource(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        owner_instance_id=request.owner_instance_id,
        claim_token=request.claim_token,
        claim_started_utc=claim_window.started_utc,
        claim_ttl_ms=request.claim_ttl_ms,
    )
    if claimed is None:
        return None
    try:
        return reconcile_claimed_task_scheduler_resource(
            claimed,
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            schedules=schedules,
            registry=registry,
            external_resources=external_resources,
            clock=clock,
            claim_window=claim_window,
        )
    except TaskSchedulerClaimExpired as exc:
        if claimed.claim_owner_instance_id is None or claimed.claim_token is None:
            raise TaskSchedulerDefinitionViolation(
                "TASK_SCHEDULER_EXPIRED_CLAIM_IDENTITY_MISSING"
            ) from None
        external_resources.requeue_expired_external_resource_claim(
            resource_type=claimed.resource_type,
            resource_id=claimed.resource_id,
            owner_instance_id=claimed.claim_owner_instance_id,
            claim_generation=claimed.claim_generation,
            claim_token=claimed.claim_token,
            requeued_utc=clock.utc_now(),
        )
        return TaskSchedulerClaimedResourceReconciliation(
            resource_id=claimed.resource_id,
            action=TaskSchedulerReconciliationAction.RETRY_CLAIM_EXPIRED,
            applied=exc.applied,
            completed=False,
            blocked=False,
            reason="TASK_SCHEDULER_CLAIM_DEADLINE_EXPIRED",
        )


def reconcile_task_scheduler_orphan_page(
    request: TaskSchedulerOrphanReconciliationRequest,
    *,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
) -> TaskSchedulerOrphanReconciliationReport:
    _validate_task_scheduler_orphan_reconciliation_request(request)
    page = registry.list_tasks(
        _task_folder_path(request.installation_id),
        limit=request.limit,
        after_task_name=request.after_task_name,
    )
    findings: list[TaskSchedulerOrphanReconciliationFinding] = []
    deleted = 0
    blocked = 0
    for observed in page:
        finding = _reconcile_task_scheduler_orphan(
            observed,
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            schedules=schedules,
            registry=registry,
        )
        if finding.deleted:
            deleted += 1
        if finding.blocked:
            blocked += 1
        findings.append(finding)
    return TaskSchedulerOrphanReconciliationReport(
        scanned=len(page),
        deleted=deleted,
        blocked=blocked,
        next_cursor=_task_name(page[-1].task_path) if len(page) == request.limit else None,
        findings=tuple(findings),
    )


def cleanup_owned_task_scheduler_page(
    request: TaskSchedulerUninstallCleanupRequest,
    *,
    registry: TaskSchedulerRegistryPort,
    delete_verified: bool = True,
) -> TaskSchedulerUninstallCleanupReport:
    _validate_task_scheduler_orphan_reconciliation_request(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            limit=request.limit,
            after_task_name=request.after_task_name,
        )
    )
    page = registry.list_tasks(
        _task_folder_path(request.installation_id),
        limit=request.limit,
        after_task_name=request.after_task_name,
    )
    findings = tuple(
        _cleanup_owned_task_scheduler_task(
            observed,
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            registry=registry,
            delete_verified=delete_verified,
        )
        for observed in page
    )
    return TaskSchedulerUninstallCleanupReport(
        scanned=len(page),
        deleted=sum(1 for finding in findings if finding.deleted),
        blocked=sum(1 for finding in findings if finding.blocked),
        next_cursor=_task_name(page[-1].task_path) if len(page) == request.limit else None,
        findings=findings,
    )


def reconcile_task_scheduler_resources_bounded(
    request: TaskSchedulerResourcePumpRequest,
    *,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
    external_resources: ExternalResourceStateStore,
    clock: ClockPort,
) -> TaskSchedulerResourcePumpReport:
    _validate_task_scheduler_resource_pump_request(request)
    cursor = request.after_schedule_id
    stage_findings: list[TaskSchedulerDesiredResourceFinding] = []
    schedule_pages_attempted = 0
    schedules_scanned = 0
    resources_staged = 0
    stage_blocked = 0
    stage_completed = False
    for _ in range(request.max_schedule_pages):
        page_report = stage_task_scheduler_desired_resource_page(
            TaskSchedulerReconciliationRequest(
                installation_id=request.installation_id,
                executable_path=request.executable_path,
                limit=request.schedule_page_limit,
                after_schedule_id=cursor,
            ),
            schedules=schedules,
            external_resources=external_resources,
        )
        schedule_pages_attempted += 1
        schedules_scanned += page_report.scanned
        resources_staged += page_report.staged
        stage_blocked += page_report.blocked
        stage_findings.extend(page_report.findings)
        cursor = page_report.next_cursor
        if cursor is None:
            stage_completed = True
            break

    claim_findings: list[TaskSchedulerClaimedResourceReconciliation] = []
    claims_attempted = 0
    claim_idle = False
    for claim_index in range(1, request.max_claims + 1):
        claims_attempted += 1
        finding = reconcile_next_pending_task_scheduler_resource(
            TaskSchedulerPendingResourceReconciliationRequest(
                installation_id=request.installation_id,
                executable_path=request.executable_path,
                owner_instance_id=request.owner_instance_id,
                claim_token=f"{request.claim_token_prefix}:{claim_index:04d}",
                claim_ttl_ms=request.claim_ttl_ms,
            ),
            schedules=schedules,
            registry=registry,
            external_resources=external_resources,
            clock=clock,
        )
        if finding is None:
            claim_idle = True
            break
        claim_findings.append(finding)
        if finding.action is TaskSchedulerReconciliationAction.RETRY_CLAIM_EXPIRED:
            break

    orphan_report = reconcile_task_scheduler_orphan_page(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            limit=request.orphan_task_page_limit,
            after_task_name=request.after_orphan_task_name,
        ),
        schedules=schedules,
        registry=registry,
    )

    return TaskSchedulerResourcePumpReport(
        schedule_pages_attempted=schedule_pages_attempted,
        schedules_scanned=schedules_scanned,
        resources_staged=resources_staged,
        stage_blocked=stage_blocked,
        stage_completed=stage_completed,
        stage_next_cursor=cursor,
        claims_attempted=claims_attempted,
        resources_reconciled=len(claim_findings),
        resources_applied=sum(1 for finding in claim_findings if finding.applied),
        resources_completed=sum(1 for finding in claim_findings if finding.completed),
        resources_blocked=sum(1 for finding in claim_findings if finding.blocked),
        claim_idle=claim_idle,
        stage_findings=tuple(stage_findings),
        claim_findings=tuple(claim_findings),
        orphan_tasks_scanned=orphan_report.scanned,
        orphan_tasks_deleted=orphan_report.deleted,
        orphan_tasks_blocked=orphan_report.blocked,
        orphan_next_cursor=orphan_report.next_cursor,
        orphan_findings=orphan_report.findings,
    )


def parse_trigger_task_arguments(arguments: tuple[str, ...]) -> TriggerTaskArgumentBinding | None:
    parsed: dict[str, str | bool] = {}
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "--enqueue-trigger-occurrence":
            if parsed.get(item) is True:
                return None
            parsed[item] = True
            index += 1
            continue
        if item not in {
            "--installation-id",
            "--schedule-id",
            "--schedule-revision-hash",
            "--trigger-kind",
            "--task-definition-hash",
        }:
            return None
        if item in parsed or index + 1 >= len(arguments):
            return None
        value = arguments[index + 1]
        if not value.strip() or value.startswith("--"):
            return None
        parsed[item] = value
        index += 2

    if parsed.get("--enqueue-trigger-occurrence") is not True:
        return None
    try:
        return TriggerTaskArgumentBinding(
            installation_id=str(parsed["--installation-id"]),
            schedule_id=str(parsed["--schedule-id"]),
            schedule_revision_hash=_hash_argument(parsed["--schedule-revision-hash"]),
            trigger_kind=TriggerKind(str(parsed["--trigger-kind"])),
            task_definition_hash=_hash_argument(parsed["--task-definition-hash"]),
        )
    except (KeyError, ValueError):
        return None


def _definition_hash_material(
    schedule: ScheduleDefinition,
    *,
    installation_id: str,
    executable_path: str,
) -> dict[str, object]:
    return {
        "arguments": _trigger_task_arguments(
            installation_id=installation_id,
            schedule_id=schedule.schedule_id,
            schedule_revision_hash=TASK_SCHEDULER_HASH_PLACEHOLDER,
            trigger_kind=schedule.trigger_type,
            task_definition_hash=TASK_SCHEDULER_HASH_PLACEHOLDER,
        ),
        "configuration": json.loads(_canonical_configuration_json(schedule.configuration_json)),
        "coalescing_window_seconds": schedule.coalescing_window_seconds,
        "definition_generation": schedule.definition_generation,
        "dst_policy": schedule.dst_policy,
        "enabled": schedule.enabled,
        "executable_path": executable_path,
        "installation_id": installation_id,
        "job_id": schedule.job_id,
        "misfire_policy": schedule.misfire_policy,
        "multiple_instances_policy": "PARALLEL",
        "plan_checksum": schedule.plan_checksum,
        "plan_id": schedule.plan_id,
        "requires_network": schedule.requires_network,
        "run_only_when_logged_on": schedule.run_only_when_logged_on,
        "schema_version": TASK_SCHEDULER_SCHEMA_VERSION,
        "schedule_id": schedule.schedule_id,
        "stop_on_execution_time_limit": False,
        "task_logon_type": schedule.task_logon_type,
        "task_path": _task_path(installation_id, schedule.schedule_id),
        "time_zone_id": schedule.time_zone_id,
        "trigger_type": schedule.trigger_type.value,
    }


def _validate_same_user_task_scheduler_policy(schedule: ScheduleDefinition) -> None:
    if schedule.task_logon_type == "PASSWORD":
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_PASSWORD_LOGON_UNSUPPORTED")
    if schedule.task_logon_type == "S4U":
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_S4U_LOGON_UNSUPPORTED")
    if schedule.task_logon_type != LOCAL_TASK_SCHEDULER_LOGON_TYPE:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_LOGON_TYPE_UNSUPPORTED")
    if not schedule.run_only_when_logged_on:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_LOGGED_OFF_RUN_UNSUPPORTED")


def _validate_task_scheduler_resource_pump_request(
    request: TaskSchedulerResourcePumpRequest,
) -> None:
    _non_empty_text(request.installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    _normalized_executable_path(request.executable_path)
    validate_schedule_reconciliation_page_request(
        limit=request.schedule_page_limit,
        after_schedule_id=request.after_schedule_id,
    )
    if not 1 <= request.max_schedule_pages <= MAX_TASK_SCHEDULER_RECONCILIATION_PUMP_PAGES:
        raise TaskSchedulerDefinitionViolation(
            "TASK_SCHEDULER_RECONCILIATION_PUMP_PAGE_LIMIT_OUT_OF_RANGE"
        )
    if not 1 <= request.max_claims <= MAX_TASK_SCHEDULER_RECONCILIATION_PUMP_CLAIMS:
        raise TaskSchedulerDefinitionViolation(
            "TASK_SCHEDULER_RECONCILIATION_PUMP_CLAIM_LIMIT_OUT_OF_RANGE"
        )
    validate_external_resource_claim(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        owner_instance_id=request.owner_instance_id,
        claim_token=f"{request.claim_token_prefix}:0001",
        claim_ttl_ms=request.claim_ttl_ms,
    )
    _validate_task_scheduler_orphan_reconciliation_request(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id=request.installation_id,
            executable_path=request.executable_path,
            limit=request.orphan_task_page_limit,
            after_task_name=request.after_orphan_task_name,
        )
    )


def _validate_task_scheduler_orphan_reconciliation_request(
    request: TaskSchedulerOrphanReconciliationRequest,
) -> None:
    _non_empty_text(request.installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    _normalized_executable_path(request.executable_path)
    if not 1 <= request.limit <= MAX_TASK_SCHEDULER_ORPHAN_RECONCILIATION_LIMIT:
        raise TaskSchedulerDefinitionViolation(
            "TASK_SCHEDULER_ORPHAN_RECONCILIATION_LIMIT_OUT_OF_RANGE"
        )
    if request.after_task_name is not None:
        _task_name_cursor(request.after_task_name)


def _trigger_task_arguments(
    *,
    installation_id: str,
    schedule_id: str,
    schedule_revision_hash: str,
    trigger_kind: TriggerKind,
    task_definition_hash: str,
) -> tuple[str, ...]:
    return (
        "--enqueue-trigger-occurrence",
        "--installation-id",
        installation_id,
        "--schedule-id",
        schedule_id,
        "--schedule-revision-hash",
        schedule_revision_hash,
        "--trigger-kind",
        trigger_kind.value,
        "--task-definition-hash",
        task_definition_hash,
    )


def _assert_task_scheduler_claim_active(
    clock: ClockPort,
    claim_window: MonotonicClaimWindow,
    *,
    applied: bool,
) -> None:
    try:
        claim_window.assert_active(clock)
    except MonotonicClaimExpired as exc:
        raise TaskSchedulerClaimExpired(applied=applied) from exc


def _task_path(installation_id: str, schedule_id: str) -> str:
    _non_empty_text(installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    return f"\\MediaSync Home\\{installation_id}\\{schedule_id}"


def _task_folder_path(installation_id: str) -> str:
    _non_empty_text(installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    return f"\\MediaSync Home\\{installation_id}"


def _task_path_or_none(installation_id: str, schedule_id: str) -> str | None:
    try:
        return _task_path(installation_id, schedule_id)
    except TaskSchedulerDefinitionViolation:
        return None


def _normalized_executable_path(executable_path: str) -> str:
    return _non_empty_text(executable_path, "TASK_SCHEDULER_EXECUTABLE_REQUIRED")


def _canonical_configuration_json(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskSchedulerDefinitionViolation(
            "TASK_SCHEDULER_CONFIGURATION_JSON_INVALID"
        ) from exc
    if not isinstance(parsed, dict):
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_CONFIGURATION_MUST_BE_OBJECT")
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def _hash_argument(value: object) -> str:
    parsed = str(value)
    if len(parsed) != 64 or any(char not in "0123456789abcdef" for char in parsed):
        raise ValueError("hash arguments must be lowercase SHA-256 hex")
    return parsed


def _non_empty_text(value: str, error_code: str) -> str:
    parsed = value.strip()
    if not parsed:
        raise TaskSchedulerDefinitionViolation(error_code)
    return parsed


def _task_name(task_path: str) -> str:
    if not task_path.startswith("\\") or task_path == "\\" or "\\\\" in task_path:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_TASK_PATH_INVALID")
    return _task_name_cursor(task_path.rsplit("\\", 1)[-1])


def _task_name_cursor(value: str) -> str:
    parsed = _non_empty_text(value, "TASK_SCHEDULER_TASK_NAME_REQUIRED")
    if "\\" in parsed or parsed in {".", ".."}:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_TASK_NAME_INVALID")
    return parsed


def _windows_path_key(path: str) -> str:
    return str(PureWindowsPath(path)).casefold()


def _desired_payload(definition: TaskSchedulerDefinition) -> dict[str, object]:
    return {
        "arguments": definition.arguments,
        "configuration_json": definition.configuration_json,
        "enabled": definition.enabled,
        "execution_time_limit_seconds": definition.execution_time_limit_seconds,
        "multiple_instances_policy": definition.multiple_instances_policy,
        "requires_network": definition.requires_network,
        "run_only_when_logged_on": definition.run_only_when_logged_on,
        "stop_on_execution_time_limit": definition.stop_on_execution_time_limit,
        "task_logon_type": definition.task_logon_type,
        "time_zone_id": definition.time_zone_id,
        "trigger_type": definition.trigger_type,
    }


def _observed_payload(definition: ObservedTaskSchedulerDefinition) -> dict[str, object]:
    return {
        "arguments": definition.arguments,
        "configuration_json": _canonical_configuration_json(definition.configuration_json),
        "enabled": definition.enabled,
        "execution_time_limit_seconds": definition.execution_time_limit_seconds,
        "multiple_instances_policy": definition.multiple_instances_policy,
        "requires_network": definition.requires_network,
        "run_only_when_logged_on": definition.run_only_when_logged_on,
        "stop_on_execution_time_limit": definition.stop_on_execution_time_limit,
        "task_logon_type": definition.task_logon_type,
        "time_zone_id": definition.time_zone_id,
        "trigger_type": definition.trigger_type,
    }


def _payload_without_enabled(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "enabled"}


def _reconcile_task_scheduler_orphan(
    observed: ObservedTaskSchedulerDefinition,
    *,
    installation_id: str,
    executable_path: str,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
) -> TaskSchedulerOrphanReconciliationFinding:
    expected_executable_path = _normalized_executable_path(executable_path)
    task_name = _task_name(observed.task_path)
    binding = parse_trigger_task_arguments(observed.arguments)
    if binding is None:
        return TaskSchedulerOrphanReconciliationFinding(
            task_path=observed.task_path,
            task_name=task_name,
            action=TaskSchedulerReconciliationAction.BLOCK_ARGUMENT_DRIFT,
            deleted=False,
            blocked=True,
            reason="TASK_SCHEDULER_ARGUMENTS_NOT_RECOGNIZED",
        )
    if binding.installation_id != installation_id:
        return TaskSchedulerOrphanReconciliationFinding(
            task_path=observed.task_path,
            task_name=task_name,
            action=TaskSchedulerReconciliationAction.BLOCK_UNKNOWN_TASK,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_ARGUMENT_OWNER_MISMATCH",
        )
    expected_path = _task_path(installation_id, binding.schedule_id)
    if _windows_path_key(observed.task_path) != _windows_path_key(expected_path):
        return TaskSchedulerOrphanReconciliationFinding(
            task_path=observed.task_path,
            task_name=task_name,
            action=TaskSchedulerReconciliationAction.BLOCK_UNKNOWN_TASK,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_TASK_PATH_MISMATCH",
        )
    if _windows_path_key(observed.executable_path) != _windows_path_key(expected_executable_path):
        return TaskSchedulerOrphanReconciliationFinding(
            task_path=observed.task_path,
            task_name=task_name,
            action=TaskSchedulerReconciliationAction.BLOCK_BINARY_DRIFT,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_EXECUTABLE_DRIFT",
        )
    if schedules.load_schedule(binding.schedule_id) is not None:
        return TaskSchedulerOrphanReconciliationFinding(
            task_path=observed.task_path,
            task_name=task_name,
            action=TaskSchedulerReconciliationAction.IN_SYNC,
            deleted=False,
            blocked=False,
            schedule_id=binding.schedule_id,
        )
    registry.delete_task(observed.task_path)
    return TaskSchedulerOrphanReconciliationFinding(
        task_path=observed.task_path,
        task_name=task_name,
        action=TaskSchedulerReconciliationAction.DELETE_OWNED_TASK,
        deleted=True,
        blocked=False,
        schedule_id=binding.schedule_id,
        reason="TASK_SCHEDULER_OWNED_TASK_ORPHANED",
    )


def _cleanup_owned_task_scheduler_task(
    observed: ObservedTaskSchedulerDefinition,
    *,
    installation_id: str,
    executable_path: str,
    registry: TaskSchedulerRegistryPort,
    delete_verified: bool,
) -> TaskSchedulerUninstallCleanupFinding:
    task_name = _task_name(observed.task_path)
    binding = parse_trigger_task_arguments(observed.arguments)
    if binding is None:
        return TaskSchedulerUninstallCleanupFinding(
            task_path=observed.task_path,
            task_name=task_name,
            deleted=False,
            blocked=True,
            reason="TASK_SCHEDULER_ARGUMENTS_NOT_RECOGNIZED",
        )
    if binding.installation_id != installation_id:
        return TaskSchedulerUninstallCleanupFinding(
            task_path=observed.task_path,
            task_name=task_name,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_ARGUMENT_OWNER_MISMATCH",
        )
    if binding.schedule_revision_hash != binding.task_definition_hash:
        return TaskSchedulerUninstallCleanupFinding(
            task_path=observed.task_path,
            task_name=task_name,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_ARGUMENT_HASH_MISMATCH",
        )
    expected_path = _task_path(installation_id, binding.schedule_id)
    if _windows_path_key(observed.task_path) != _windows_path_key(expected_path):
        return TaskSchedulerUninstallCleanupFinding(
            task_path=observed.task_path,
            task_name=task_name,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_TASK_PATH_MISMATCH",
        )
    if _windows_path_key(observed.executable_path) != _windows_path_key(
        _normalized_executable_path(executable_path)
    ):
        return TaskSchedulerUninstallCleanupFinding(
            task_path=observed.task_path,
            task_name=task_name,
            deleted=False,
            blocked=True,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_EXECUTABLE_DRIFT",
        )
    if not delete_verified:
        return TaskSchedulerUninstallCleanupFinding(
            task_path=observed.task_path,
            task_name=task_name,
            deleted=False,
            blocked=False,
            schedule_id=binding.schedule_id,
            reason="TASK_SCHEDULER_OWNERSHIP_VERIFIED",
        )
    registry.delete_task(observed.task_path)
    return TaskSchedulerUninstallCleanupFinding(
        task_path=observed.task_path,
        task_name=task_name,
        deleted=True,
        blocked=False,
        schedule_id=binding.schedule_id,
        reason="TASK_SCHEDULER_OWNED_TASK_REMOVED_FOR_UNINSTALL",
    )
