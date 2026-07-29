from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PureWindowsPath
from typing import Protocol

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
MAX_TASK_SCHEDULER_RECONCILIATION_PUMP_PAGES = 100
MAX_TASK_SCHEDULER_RECONCILIATION_PUMP_CLAIMS = 500


class TaskSchedulerDefinitionViolation(ValueError):
    pass


class TaskSchedulerReconciliationAction(str, Enum):
    CREATE = "CREATE"
    IN_SYNC = "IN_SYNC"
    UPDATE_DRIFTED = "UPDATE_DRIFTED"
    ENABLE_OWNED_TASK = "ENABLE_OWNED_TASK"
    DISABLE_OWNED_TASK = "DISABLE_OWNED_TASK"
    UPDATE_OWNED_DEFINITION = "UPDATE_OWNED_DEFINITION"
    BLOCK_ARGUMENT_DRIFT = "BLOCK_ARGUMENT_DRIFT"
    BLOCK_BINARY_DRIFT = "BLOCK_BINARY_DRIFT"
    BLOCK_INVALID_DESIRED_STATE = "BLOCK_INVALID_DESIRED_STATE"
    BLOCK_UNKNOWN_TASK = "BLOCK_UNKNOWN_TASK"


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


class TaskSchedulerRegistryPort(Protocol):
    def load_task(self, task_path: str) -> ObservedTaskSchedulerDefinition | None: ...

    def apply_task_definition(self, definition: TaskSchedulerDefinition) -> None: ...


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
) -> TaskSchedulerClaimedResourceReconciliation:
    if claimed.resource_type is not ExternalResourceType.TASK_SCHEDULER:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_CLAIM_RESOURCE_TYPE_MISMATCH")
    if claimed.state is not ExternalResourceState.CLAIMED or claimed.claim_token is None:
        raise TaskSchedulerDefinitionViolation("TASK_SCHEDULER_RESOURCE_MUST_BE_CLAIMED")
    schedule = schedules.load_schedule(claimed.resource_id)
    if schedule is None:
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
    if plan.action.value.startswith("BLOCK_"):
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
        registry.apply_task_definition(plan.desired)
        applied = True
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
) -> TaskSchedulerClaimedResourceReconciliation | None:
    _non_empty_text(request.installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    _normalized_executable_path(request.executable_path)
    validate_external_resource_claim(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        owner_instance_id=request.owner_instance_id,
        claim_token=request.claim_token,
        claim_ttl_ms=request.claim_ttl_ms,
    )
    claimed = external_resources.claim_next_pending_external_resource(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        owner_instance_id=request.owner_instance_id,
        claim_token=request.claim_token,
        claim_ttl_ms=request.claim_ttl_ms,
    )
    if claimed is None:
        return None
    return reconcile_claimed_task_scheduler_resource(
        claimed,
        installation_id=request.installation_id,
        executable_path=request.executable_path,
        schedules=schedules,
        registry=registry,
        external_resources=external_resources,
    )


def reconcile_task_scheduler_resources_bounded(
    request: TaskSchedulerResourcePumpRequest,
    *,
    schedules: ScheduleStore,
    registry: TaskSchedulerRegistryPort,
    external_resources: ExternalResourceStateStore,
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
        )
        if finding is None:
            claim_idle = True
            break
        claim_findings.append(finding)

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


def _task_path(installation_id: str, schedule_id: str) -> str:
    _non_empty_text(installation_id, "TASK_SCHEDULER_INSTALLATION_ID_REQUIRED")
    return f"\\MediaSync Home\\{installation_id}\\{schedule_id}"


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
