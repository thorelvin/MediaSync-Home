from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PureWindowsPath

from mediasync_home.application.schedules import ScheduleDefinition, validate_schedule_definition
from mediasync_home.application.trigger_occurrences import TriggerKind


TASK_SCHEDULER_HASH_PLACEHOLDER = "<TASK_DEFINITION_HASH>"
TASK_SCHEDULER_SCHEMA_VERSION = 1


class TaskSchedulerDefinitionViolation(ValueError):
    pass


class TaskSchedulerReconciliationAction(str, Enum):
    CREATE = "CREATE"
    IN_SYNC = "IN_SYNC"
    UPDATE_DRIFTED = "UPDATE_DRIFTED"
    BLOCK_ARGUMENT_DRIFT = "BLOCK_ARGUMENT_DRIFT"
    BLOCK_BINARY_DRIFT = "BLOCK_BINARY_DRIFT"
    BLOCK_UNKNOWN_TASK = "BLOCK_UNKNOWN_TASK"


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
    if _observed_payload(observed) == _desired_payload(desired):
        return TaskSchedulerReconciliationPlan(
            TaskSchedulerReconciliationAction.IN_SYNC,
            desired=desired,
            observed=observed,
        )
    return TaskSchedulerReconciliationPlan(
        TaskSchedulerReconciliationAction.UPDATE_DRIFTED,
        desired=desired,
        observed=observed,
        reason="TASK_SCHEDULER_OWNED_DEFINITION_DRIFT",
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
