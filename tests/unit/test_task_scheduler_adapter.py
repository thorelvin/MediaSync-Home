from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.adapters.task_scheduler import (
    TaskSchedulerAdapterError,
    TaskSchedulerGatewayTask,
    WindowsTaskSchedulerRegistry,
)
from mediasync_home.adapters.windows_argv import (
    build_windows_argument_line,
    parse_windows_argument_line,
)
from mediasync_home.application.task_scheduler import (
    TaskSchedulerDefinition,
    TaskSchedulerReconciliationAction,
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
    classify_task_scheduler_reconciliation,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


EXECUTABLE = r"C:\Program Files\MediaSync Home\MediaSyncHome.exe"


def test_windows_task_scheduler_registry_applies_com_shaped_task_registration() -> None:
    gateway = _Gateway()
    registry = WindowsTaskSchedulerRegistry(gateway)
    definition = _definition(arguments=("plain", "two words", "", r"trailing\\"))

    registry.apply_task_definition(definition)

    assert len(gateway.applied) == 1
    applied = gateway.applied[0]
    assert applied.task_path == r"\MediaSync Home\install-a\schedule-a"
    assert applied.folder_path == r"\MediaSync Home\install-a"
    assert applied.task_name == "schedule-a"
    assert applied.executable_path == EXECUTABLE
    assert parse_windows_argument_line(applied.argument_line) == definition.arguments
    assert applied.multiple_instances_policy == "PARALLEL"
    assert applied.execution_time_limit_seconds == 0
    assert applied.stop_on_execution_time_limit is False


def test_windows_task_scheduler_registry_loads_observed_definition_from_gateway() -> None:
    definition = _definition()
    registry = WindowsTaskSchedulerRegistry(
        _Gateway(_gateway_task(definition, configuration_json='{ "kind": "daily" }'))
    )

    observed = registry.load_task(definition.task_path)

    assert observed is not None
    assert observed.task_path == definition.task_path
    assert observed.executable_path == definition.executable_path
    assert observed.arguments == definition.arguments
    assert observed.configuration_json == '{ "kind": "daily" }'
    assert observed.task_logon_type == "INTERACTIVE_TOKEN"


def test_windows_task_scheduler_registry_turns_unparseable_arguments_into_safe_drift() -> None:
    schedule = bind_same_user_task_scheduler_definition_hash(
        _schedule(),
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = WindowsTaskSchedulerRegistry(
        _Gateway(replace(_gateway_task(definition), argument_line="bad\x00argument"))
    )

    plan = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=registry.load_task(definition.task_path),
    )

    assert plan.action is TaskSchedulerReconciliationAction.BLOCK_ARGUMENT_DRIFT
    assert plan.reason == "TASK_SCHEDULER_ARGUMENTS_NOT_RECOGNIZED"


def test_windows_task_scheduler_registry_rejects_invalid_task_path_before_apply() -> None:
    registry = WindowsTaskSchedulerRegistry(_Gateway())

    with pytest.raises(TaskSchedulerAdapterError, match="TASK_SCHEDULER_TASK_PATH_INVALID"):
        registry.apply_task_definition(replace(_definition(), task_path=r"MediaSync Home\a"))


def test_windows_task_scheduler_registry_rejects_overlong_action_before_apply() -> None:
    registry = WindowsTaskSchedulerRegistry(_Gateway())

    with pytest.raises(TaskSchedulerAdapterError, match="WINDOWS_COMMAND_LINE_TOO_LONG"):
        registry.apply_task_definition(
            replace(_definition(), arguments=("x" * 40000,))
        )


class _Gateway:
    def __init__(self, *tasks: TaskSchedulerGatewayTask) -> None:
        self.tasks = {task.task_path: task for task in tasks}
        self.applied: list[TaskSchedulerGatewayTask] = []

    def load_task(self, task_path: str) -> TaskSchedulerGatewayTask | None:
        return self.tasks.get(task_path)

    def apply_task(self, task: TaskSchedulerGatewayTask) -> None:
        self.applied.append(task)
        self.tasks[task.task_path] = task


def _definition(
    *,
    arguments: tuple[str, ...] = (
        "--enqueue-trigger-occurrence",
        "--installation-id",
        "install-a",
        "--schedule-id",
        "schedule-a",
        "--schedule-revision-hash",
        "a" * 64,
        "--trigger-kind",
        "SCHEDULED_TIME",
        "--task-definition-hash",
        "a" * 64,
    ),
) -> TaskSchedulerDefinition:
    return TaskSchedulerDefinition(
        task_path=r"\MediaSync Home\install-a\schedule-a",
        executable_path=EXECUTABLE,
        arguments=arguments,
        definition_hash="a" * 64,
        enabled=True,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        time_zone_id="Europe/Oslo",
        task_logon_type="INTERACTIVE_TOKEN",
        run_only_when_logged_on=True,
        requires_network=False,
    )


def _gateway_task(
    definition: TaskSchedulerDefinition,
    *,
    configuration_json: str | None = None,
) -> TaskSchedulerGatewayTask:
    return TaskSchedulerGatewayTask(
        task_path=definition.task_path,
        folder_path=r"\MediaSync Home\install-a",
        task_name="schedule-a",
        executable_path=definition.executable_path,
        argument_line=build_windows_argument_line(definition.arguments),
        enabled=definition.enabled,
        trigger_type=definition.trigger_type,
        configuration_json=configuration_json or definition.configuration_json,
        time_zone_id=definition.time_zone_id,
        task_logon_type=definition.task_logon_type,
        run_only_when_logged_on=definition.run_only_when_logged_on,
        requires_network=definition.requires_network,
        multiple_instances_policy=definition.multiple_instances_policy,
        execution_time_limit_seconds=definition.execution_time_limit_seconds,
        stop_on_execution_time_limit=definition.stop_on_execution_time_limit,
    )


def _schedule() -> object:
    from mediasync_home.application.schedules import ScheduleDefinition

    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id="job-a",
        plan_id="plan-a",
        plan_checksum="a" * 64,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash="0" * 64,
        time_zone_id="Europe/Oslo",
        dst_policy="PRESERVE_WALL_TIME",
        misfire_policy="QUEUE_ONCE",
        coalescing_window_seconds=60,
        task_logon_type="INTERACTIVE_TOKEN",
        requires_network=False,
        run_only_when_logged_on=True,
        enabled=True,
        row_version=1,
    )
