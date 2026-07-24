from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.task_scheduler import (
    ObservedTaskSchedulerDefinition,
    TaskSchedulerDefinitionViolation,
    TaskSchedulerReconciliationAction,
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
    classify_task_scheduler_reconciliation,
    parse_trigger_task_arguments,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


EXECUTABLE = r"C:\Program Files\MediaSync Home\MediaSyncHome.exe"


def test_same_user_task_scheduler_definition_uses_only_protocol_arguments() -> None:
    schedule = _bound_schedule(configuration_json='{ "kind": "daily", "hour": 2 }')

    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    binding = parse_trigger_task_arguments(definition.arguments)

    assert definition.task_path == r"\MediaSync Home\install-a\schedule-a"
    assert definition.configuration_json == '{"hour":2,"kind":"daily"}'
    assert definition.definition_hash == schedule.desired_definition_hash
    assert definition.arguments == (
        "--enqueue-trigger-occurrence",
        "--installation-id",
        "install-a",
        "--schedule-id",
        "schedule-a",
        "--schedule-revision-hash",
        schedule.desired_definition_hash,
        "--trigger-kind",
        "SCHEDULED_TIME",
        "--task-definition-hash",
        schedule.desired_definition_hash,
    )
    assert binding is not None
    assert binding.schedule_revision_hash == schedule.desired_definition_hash


def test_task_scheduler_hash_is_canonical_for_configuration_whitespace() -> None:
    first = _bound_schedule(configuration_json='{ "kind": "daily", "hour": 2 }')
    second = _bound_schedule(configuration_json='{"hour":2,"kind":"daily"}')

    assert first.desired_definition_hash == second.desired_definition_hash


def test_task_scheduler_definition_requires_matching_desired_hash() -> None:
    with pytest.raises(
        TaskSchedulerDefinitionViolation,
        match="TASK_SCHEDULER_DESIRED_HASH_MISMATCH",
    ):
        build_same_user_task_scheduler_definition(
            _schedule(desired_definition_hash="b" * 64),
            installation_id="install-a",
            executable_path=EXECUTABLE,
        )


def test_task_scheduler_reconciliation_classifies_missing_and_in_sync_tasks() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )

    missing = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=None,
    )
    in_sync = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=_observed(definition),
    )

    assert missing.action is TaskSchedulerReconciliationAction.CREATE
    assert missing.reason == "TASK_SCHEDULER_TASK_MISSING"
    assert in_sync.action is TaskSchedulerReconciliationAction.IN_SYNC
    assert in_sync.reason is None


def test_task_scheduler_reconciliation_updates_owned_definition_drift() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    observed = replace(
        _observed(definition),
        enabled=False,
    )

    plan = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=observed,
    )

    assert plan.action is TaskSchedulerReconciliationAction.UPDATE_DRIFTED
    assert plan.reason == "TASK_SCHEDULER_OWNED_DEFINITION_DRIFT"


def test_task_scheduler_reconciliation_blocks_unknown_or_unsafe_drift() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )

    wrong_binary = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=replace(_observed(definition), executable_path=r"C:\Other\App.exe"),
    )
    unknown_arguments = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=replace(_observed(definition), arguments=("--delete-everything",)),
    )
    wrong_owner = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=replace(
            _observed(definition),
            arguments=(
                "--enqueue-trigger-occurrence",
                "--installation-id",
                "install-b",
                "--schedule-id",
                "schedule-a",
                "--schedule-revision-hash",
                schedule.desired_definition_hash,
                "--trigger-kind",
                "SCHEDULED_TIME",
                "--task-definition-hash",
                schedule.desired_definition_hash,
            ),
        ),
    )

    assert wrong_binary.action is TaskSchedulerReconciliationAction.BLOCK_BINARY_DRIFT
    assert unknown_arguments.action is TaskSchedulerReconciliationAction.BLOCK_ARGUMENT_DRIFT
    assert wrong_owner.action is TaskSchedulerReconciliationAction.BLOCK_UNKNOWN_TASK


def test_trigger_task_argument_parser_rejects_extra_or_malformed_arguments() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )

    assert parse_trigger_task_arguments(definition.arguments + ("--extra", "x")) is None
    assert parse_trigger_task_arguments(("--schedule-id", "schedule-a")) is None
    assert (
        parse_trigger_task_arguments(
            (
                "--enqueue-trigger-occurrence",
                "--installation-id",
                "install-a",
                "--schedule-id",
                "schedule-a",
                "--schedule-revision-hash",
                "not-a-hash",
                "--trigger-kind",
                "SCHEDULED_TIME",
                "--task-definition-hash",
                schedule.desired_definition_hash,
            )
        )
        is None
    )


def _bound_schedule(*, configuration_json: str = '{"kind":"daily"}') -> ScheduleDefinition:
    return bind_same_user_task_scheduler_definition_hash(
        _schedule(configuration_json=configuration_json),
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )


def _schedule(
    *,
    desired_definition_hash: str = "0" * 64,
    configuration_json: str = '{"kind":"daily"}',
) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id="job-a",
        plan_id="plan-a",
        plan_checksum="a" * 64,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json=configuration_json,
        definition_generation=1,
        desired_definition_hash=desired_definition_hash,
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


def _observed(definition) -> ObservedTaskSchedulerDefinition:
    return ObservedTaskSchedulerDefinition(
        task_path=definition.task_path,
        executable_path=definition.executable_path,
        arguments=definition.arguments,
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
