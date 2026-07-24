from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.adapters.windows_argv import (
    WindowsCommandLineError,
    build_windows_argument_line,
    build_windows_command_line,
    parse_windows_argument_line,
)
from mediasync_home.application.task_scheduler import (
    ObservedTaskSchedulerDefinition,
    TaskSchedulerDefinition,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


UNPARSEABLE_TASK_SCHEDULER_ARGUMENTS = "<TASK_SCHEDULER_ARGUMENT_PARSE_FAILED>"


class TaskSchedulerAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSchedulerGatewayTask:
    task_path: str
    folder_path: str
    task_name: str
    executable_path: str
    argument_line: str
    enabled: bool
    trigger_type: TriggerKind
    configuration_json: str
    time_zone_id: str | None
    task_logon_type: str
    run_only_when_logged_on: bool
    requires_network: bool
    multiple_instances_policy: str
    execution_time_limit_seconds: int
    stop_on_execution_time_limit: bool


class TaskSchedulerGateway(Protocol):
    def load_task(self, task_path: str) -> TaskSchedulerGatewayTask | None: ...

    def apply_task(self, task: TaskSchedulerGatewayTask) -> None: ...


class WindowsTaskSchedulerRegistry:
    def __init__(self, gateway: TaskSchedulerGateway) -> None:
        self._gateway = gateway

    def load_task(self, task_path: str) -> ObservedTaskSchedulerDefinition | None:
        task = self._gateway.load_task(task_path)
        if task is None:
            return None
        try:
            arguments = parse_windows_argument_line(task.argument_line)
        except WindowsCommandLineError:
            arguments = (UNPARSEABLE_TASK_SCHEDULER_ARGUMENTS,)
        return ObservedTaskSchedulerDefinition(
            task_path=task.task_path,
            executable_path=task.executable_path,
            arguments=arguments,
            enabled=task.enabled,
            trigger_type=task.trigger_type,
            configuration_json=task.configuration_json,
            time_zone_id=task.time_zone_id,
            task_logon_type=task.task_logon_type,
            run_only_when_logged_on=task.run_only_when_logged_on,
            requires_network=task.requires_network,
            multiple_instances_policy=task.multiple_instances_policy,
            execution_time_limit_seconds=task.execution_time_limit_seconds,
            stop_on_execution_time_limit=task.stop_on_execution_time_limit,
        )

    def apply_task_definition(self, definition: TaskSchedulerDefinition) -> None:
        self._gateway.apply_task(_task_from_definition(definition))


def _task_from_definition(definition: TaskSchedulerDefinition) -> TaskSchedulerGatewayTask:
    folder_path, task_name = _split_task_path(definition.task_path)
    _validate_action_command_line_budget(definition)
    return TaskSchedulerGatewayTask(
        task_path=definition.task_path,
        folder_path=folder_path,
        task_name=task_name,
        executable_path=definition.executable_path,
        argument_line=build_windows_argument_line(definition.arguments),
        enabled=definition.enabled,
        trigger_type=definition.trigger_type,
        configuration_json=definition.configuration_json,
        time_zone_id=definition.time_zone_id,
        task_logon_type=definition.task_logon_type,
        run_only_when_logged_on=definition.run_only_when_logged_on,
        requires_network=definition.requires_network,
        multiple_instances_policy=definition.multiple_instances_policy,
        execution_time_limit_seconds=definition.execution_time_limit_seconds,
        stop_on_execution_time_limit=definition.stop_on_execution_time_limit,
    )


def _validate_action_command_line_budget(definition: TaskSchedulerDefinition) -> None:
    try:
        build_windows_command_line((definition.executable_path, *definition.arguments))
    except WindowsCommandLineError as exc:
        raise TaskSchedulerAdapterError(str(exc)) from exc


def _split_task_path(task_path: str) -> tuple[str, str]:
    if not task_path.startswith("\\") or task_path == "\\" or "\\\\" in task_path:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_TASK_PATH_INVALID")
    parts = tuple(part for part in task_path.split("\\") if part)
    if not parts:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_TASK_PATH_INVALID")
    task_name = parts[-1]
    folder_path = "\\" if len(parts) == 1 else "\\" + "\\".join(parts[:-1])
    return folder_path, task_name
