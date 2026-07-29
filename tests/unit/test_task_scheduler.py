from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.task_scheduler import (
    ObservedTaskSchedulerDefinition,
    TaskSchedulerDefinition,
    TaskSchedulerDefinitionViolation,
    TaskSchedulerOrphanReconciliationRequest,
    TaskSchedulerPendingResourceReconciliationRequest,
    TaskSchedulerResourcePumpRequest,
    TaskSchedulerReconciliationAction,
    TaskSchedulerReconciliationRequest,
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
    classify_task_scheduler_reconciliation,
    parse_trigger_task_arguments,
    reconcile_claimed_task_scheduler_resource,
    reconcile_next_pending_task_scheduler_resource,
    reconcile_task_scheduler_orphan_page,
    reconcile_task_scheduler_resources_bounded,
    reconcile_task_scheduler_page,
    stage_task_scheduler_desired_resource_page,
)
from mediasync_home.application.external_resources import (
    ExternalResourceRecord,
    ExternalResourceState,
    ExternalResourceStateStore,
    ExternalResourceStartupReconciliationReport,
    ExternalResourceStartupReconciliationRequest,
    ExternalResourceType,
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


def test_task_scheduler_reconciliation_classifies_safe_owned_updates() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    enable_plan = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=replace(_observed(definition), enabled=False),
    )

    disabled_schedule = _bound_schedule(enabled=False)
    disabled_definition = build_same_user_task_scheduler_definition(
        disabled_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    disable_plan = classify_task_scheduler_reconciliation(
        disabled_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=replace(_observed(disabled_definition), enabled=True),
    )

    network_schedule = _bound_schedule(requires_network=True)
    network_definition = build_same_user_task_scheduler_definition(
        network_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    definition_plan = classify_task_scheduler_reconciliation(
        network_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=replace(_observed(network_definition), requires_network=False),
    )

    assert enable_plan.action is TaskSchedulerReconciliationAction.ENABLE_OWNED_TASK
    assert enable_plan.reason == "TASK_SCHEDULER_OWNED_TASK_DISABLED"
    assert disable_plan.action is TaskSchedulerReconciliationAction.DISABLE_OWNED_TASK
    assert disable_plan.reason == "TASK_SCHEDULER_OWNED_TASK_STILL_ENABLED"
    assert definition_plan.action is TaskSchedulerReconciliationAction.UPDATE_OWNED_DEFINITION
    assert definition_plan.reason == "TASK_SCHEDULER_OWNED_DEFINITION_DRIFT"


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


def test_task_scheduler_reconciliation_page_applies_safe_actions_and_blocks_drift() -> None:
    missing = _bound_schedule(schedule_id="schedule-a")
    drifted = _bound_schedule(schedule_id="schedule-b")
    blocked = _bound_schedule(schedule_id="schedule-c")
    drifted_definition = build_same_user_task_scheduler_definition(
        drifted,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    blocked_definition = build_same_user_task_scheduler_definition(
        blocked,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = _Registry(
        _observed(drifted_definition, enabled=False),
        _observed(blocked_definition, executable_path=r"C:\Other\App.exe"),
    )

    report = reconcile_task_scheduler_page(
        TaskSchedulerReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=10,
        ),
        schedules=_ScheduleStore(missing, drifted, blocked),
        registry=registry,
    )

    assert report.scanned == 3
    assert report.applied == 2
    assert report.blocked == 1
    assert report.next_cursor is None
    assert tuple(finding.action for finding in report.findings) == (
        TaskSchedulerReconciliationAction.CREATE,
        TaskSchedulerReconciliationAction.ENABLE_OWNED_TASK,
        TaskSchedulerReconciliationAction.BLOCK_BINARY_DRIFT,
    )
    assert tuple(definition.task_path for definition in registry.applied) == (
        r"\MediaSync Home\install-a\schedule-a",
        r"\MediaSync Home\install-a\schedule-b",
    )


def test_task_scheduler_reconciliation_page_is_bounded_with_keyset_cursor() -> None:
    schedules = _ScheduleStore(
        _bound_schedule(schedule_id="schedule-a"),
        _bound_schedule(schedule_id="schedule-b"),
    )

    report = reconcile_task_scheduler_page(
        TaskSchedulerReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=1,
        ),
        schedules=schedules,
        registry=_Registry(),
    )

    assert report.scanned == 1
    assert report.next_cursor == "schedule-a"
    assert report.findings[0].schedule_id == "schedule-a"


def test_task_scheduler_reconciliation_reports_invalid_desired_hash() -> None:
    report = reconcile_task_scheduler_page(
        TaskSchedulerReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=10,
        ),
        schedules=_ScheduleStore(_schedule(desired_definition_hash="b" * 64)),
        registry=_Registry(),
    )

    assert report.applied == 0
    assert report.blocked == 1
    assert report.findings[0].action is (
        TaskSchedulerReconciliationAction.BLOCK_INVALID_DESIRED_STATE
    )
    assert report.findings[0].reason == "TASK_SCHEDULER_DESIRED_HASH_MISMATCH"


def test_stage_task_scheduler_desired_resource_page_records_claimable_resources() -> None:
    schedule_a = _bound_schedule(schedule_id="schedule-a")
    schedule_b = _bound_schedule(schedule_id="schedule-b")
    resources = _ExternalResourceStore()

    report = stage_task_scheduler_desired_resource_page(
        TaskSchedulerReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=10,
        ),
        schedules=_ScheduleStore(schedule_a, schedule_b),
        external_resources=resources,
    )

    assert report.scanned == 2
    assert report.staged == 2
    assert report.blocked == 0
    assert tuple(finding.schedule_id for finding in report.findings) == (
        "schedule-a",
        "schedule-b",
    )
    assert resources.desired == (
        (
            ExternalResourceType.TASK_SCHEDULER,
            "schedule-a",
            schedule_a.definition_generation,
            schedule_a.desired_definition_hash,
        ),
        (
            ExternalResourceType.TASK_SCHEDULER,
            "schedule-b",
            schedule_b.definition_generation,
            schedule_b.desired_definition_hash,
        ),
    )


def test_stage_task_scheduler_desired_resource_page_reports_invalid_desired_state() -> None:
    resources = _ExternalResourceStore()

    report = stage_task_scheduler_desired_resource_page(
        TaskSchedulerReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=10,
        ),
        schedules=_ScheduleStore(_schedule(desired_definition_hash="b" * 64)),
        external_resources=resources,
    )

    assert report.scanned == 1
    assert report.staged == 0
    assert report.blocked == 1
    assert report.findings[0].reason == "TASK_SCHEDULER_DESIRED_HASH_MISMATCH"
    assert resources.desired == ()


def test_reconcile_claimed_task_scheduler_resource_applies_and_completes_claim() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = _Registry(_observed(definition, enabled=False))
    resources = _ExternalResourceStore()

    result = reconcile_claimed_task_scheduler_resource(
        _claimed_resource(schedule),
        installation_id="install-a",
        executable_path=EXECUTABLE,
        schedules=_ScheduleStore(schedule),
        registry=registry,
        external_resources=resources,
    )

    assert result.action is TaskSchedulerReconciliationAction.ENABLE_OWNED_TASK
    assert result.applied is True
    assert result.completed is True
    assert result.blocked is False
    assert tuple(definition.task_path for definition in registry.applied) == (
        r"\MediaSync Home\install-a\schedule-a",
    )
    assert resources.completed == (
        (
            ExternalResourceType.TASK_SCHEDULER,
            "schedule-a",
            schedule.definition_generation,
            "claim-a",
            schedule.desired_definition_hash,
        ),
    )
    assert resources.blocked == ()


def test_reconcile_claimed_task_scheduler_resource_completes_in_sync_without_apply() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = _Registry(_observed(definition))
    resources = _ExternalResourceStore()

    result = reconcile_claimed_task_scheduler_resource(
        _claimed_resource(schedule),
        installation_id="install-a",
        executable_path=EXECUTABLE,
        schedules=_ScheduleStore(schedule),
        registry=registry,
        external_resources=resources,
    )

    assert result.action is TaskSchedulerReconciliationAction.IN_SYNC
    assert result.applied is False
    assert result.completed is True
    assert registry.applied == []
    assert resources.completed[0][4] == schedule.desired_definition_hash


def test_reconcile_claimed_task_scheduler_resource_blocks_unsafe_drift() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = _Registry(_observed(definition, executable_path=r"C:\Other\App.exe"))
    resources = _ExternalResourceStore()

    result = reconcile_claimed_task_scheduler_resource(
        _claimed_resource(schedule),
        installation_id="install-a",
        executable_path=EXECUTABLE,
        schedules=_ScheduleStore(schedule),
        registry=registry,
        external_resources=resources,
    )

    assert result.action is TaskSchedulerReconciliationAction.BLOCK_BINARY_DRIFT
    assert result.completed is False
    assert result.blocked is True
    assert registry.applied == []
    assert resources.completed == ()
    assert resources.blocked == (
        (
            ExternalResourceType.TASK_SCHEDULER,
            "schedule-a",
            "claim-a",
            "TASK_SCHEDULER_EXECUTABLE_DRIFT",
        ),
    )


def test_reconcile_claimed_task_scheduler_resource_blocks_stale_claim_desired_state() -> None:
    schedule = _bound_schedule()
    resources = _ExternalResourceStore()

    result = reconcile_claimed_task_scheduler_resource(
        _claimed_resource(schedule, desired_hash="b" * 64),
        installation_id="install-a",
        executable_path=EXECUTABLE,
        schedules=_ScheduleStore(schedule),
        registry=_Registry(),
        external_resources=resources,
    )

    assert result.action is TaskSchedulerReconciliationAction.BLOCK_INVALID_DESIRED_STATE
    assert result.reason == "TASK_SCHEDULER_CLAIM_DESIRED_DRIFT"
    assert resources.blocked == (
        (
            ExternalResourceType.TASK_SCHEDULER,
            "schedule-a",
            "claim-a",
            "TASK_SCHEDULER_CLAIM_DESIRED_DRIFT",
        ),
    )


def test_reconcile_next_pending_task_scheduler_resource_claims_and_completes() -> None:
    schedule = _bound_schedule()
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    resources = _ExternalResourceStore(
        ExternalResourceRecord(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id=schedule.schedule_id,
            desired_generation=schedule.definition_generation,
            desired_hash=schedule.desired_definition_hash,
        )
    )

    result = reconcile_next_pending_task_scheduler_resource(
        TaskSchedulerPendingResourceReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        ),
        schedules=_ScheduleStore(schedule),
        registry=_Registry(_observed(definition)),
        external_resources=resources,
    )

    assert result is not None
    assert result.action is TaskSchedulerReconciliationAction.IN_SYNC
    assert result.completed is True
    assert resources.claims == (
        (
            ExternalResourceType.TASK_SCHEDULER,
            "host-a",
            "claim-a",
            30_000,
        ),
    )
    assert resources.completed == (
        (
            ExternalResourceType.TASK_SCHEDULER,
            "schedule-a",
            schedule.definition_generation,
            "claim-a",
            schedule.desired_definition_hash,
        ),
    )


def test_reconcile_next_pending_task_scheduler_resource_reports_idle() -> None:
    result = reconcile_next_pending_task_scheduler_resource(
        TaskSchedulerPendingResourceReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        ),
        schedules=_ScheduleStore(),
        registry=_Registry(),
        external_resources=_ExternalResourceStore(),
    )

    assert result is None


def test_task_scheduler_orphan_page_deletes_only_owned_absent_schedules() -> None:
    schedule = _bound_schedule(schedule_id="schedule-a")
    orphan_schedule = _bound_schedule(schedule_id="schedule-b")
    binary_drift_schedule = _bound_schedule(schedule_id="schedule-c")
    owned_definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    orphan_definition = build_same_user_task_scheduler_definition(
        orphan_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    binary_drift_definition = build_same_user_task_scheduler_definition(
        binary_drift_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = _Registry(
        _observed(owned_definition),
        _observed(orphan_definition),
        _observed(binary_drift_definition, executable_path=r"C:\Other\App.exe"),
    )

    report = reconcile_task_scheduler_orphan_page(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=10,
        ),
        schedules=_ScheduleStore(schedule),
        registry=registry,
    )

    assert report.scanned == 3
    assert report.deleted == 1
    assert report.blocked == 1
    assert report.next_cursor is None
    assert tuple(finding.action for finding in report.findings) == (
        TaskSchedulerReconciliationAction.IN_SYNC,
        TaskSchedulerReconciliationAction.DELETE_OWNED_TASK,
        TaskSchedulerReconciliationAction.BLOCK_BINARY_DRIFT,
    )
    assert registry.deleted == (r"\MediaSync Home\install-a\schedule-b",)
    assert r"\MediaSync Home\install-a\schedule-c" in registry.observed


def test_task_scheduler_orphan_page_blocks_unknown_or_ambiguous_tasks() -> None:
    owned_schedule = _bound_schedule(schedule_id="schedule-a")
    wrong_owner_schedule = _bound_schedule(schedule_id="schedule-b")
    unparseable_schedule = _bound_schedule(schedule_id="schedule-c")
    owned_definition = build_same_user_task_scheduler_definition(
        owned_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    wrong_owner_definition = build_same_user_task_scheduler_definition(
        wrong_owner_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    unparseable_definition = build_same_user_task_scheduler_definition(
        unparseable_schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = _Registry(
        replace(
            _observed(wrong_owner_definition),
            arguments=(
                "--enqueue-trigger-occurrence",
                "--installation-id",
                "install-b",
                "--schedule-id",
                "schedule-b",
                "--schedule-revision-hash",
                wrong_owner_schedule.desired_definition_hash,
                "--trigger-kind",
                "SCHEDULED_TIME",
                "--task-definition-hash",
                wrong_owner_schedule.desired_definition_hash,
            ),
        ),
        replace(_observed(unparseable_definition), arguments=("--unknown",)),
        replace(
            _observed(owned_definition),
            task_path=r"\MediaSync Home\install-a\renamed-task",
        ),
    )

    report = reconcile_task_scheduler_orphan_page(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=10,
        ),
        schedules=_ScheduleStore(),
        registry=registry,
    )

    assert report.deleted == 0
    assert report.blocked == 3
    assert tuple(finding.reason for finding in report.findings) == (
        "TASK_SCHEDULER_TASK_PATH_MISMATCH",
        "TASK_SCHEDULER_ARGUMENT_OWNER_MISMATCH",
        "TASK_SCHEDULER_ARGUMENTS_NOT_RECOGNIZED",
    )
    assert registry.deleted == ()


def test_task_scheduler_orphan_page_is_bounded_with_task_cursor() -> None:
    definitions = tuple(
        build_same_user_task_scheduler_definition(
            _bound_schedule(schedule_id=schedule_id),
            installation_id="install-a",
            executable_path=EXECUTABLE,
        )
        for schedule_id in ("schedule-a", "schedule-b", "schedule-c")
    )
    registry = _Registry(*(_observed(definition) for definition in definitions))

    first = reconcile_task_scheduler_orphan_page(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=2,
        ),
        schedules=_ScheduleStore(*(_bound_schedule(schedule_id="schedule-a"),)),
        registry=registry,
    )
    second = reconcile_task_scheduler_orphan_page(
        TaskSchedulerOrphanReconciliationRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            limit=2,
            after_task_name=first.next_cursor,
        ),
        schedules=_ScheduleStore(*(_bound_schedule(schedule_id="schedule-a"),)),
        registry=registry,
    )

    assert first.next_cursor == "schedule-b"
    assert tuple(finding.task_name for finding in first.findings) == (
        "schedule-a",
        "schedule-b",
    )
    assert second.next_cursor is None
    assert tuple(finding.task_name for finding in second.findings) == ("schedule-c",)


def test_task_scheduler_resource_pump_stages_pages_and_drains_claims() -> None:
    schedules = (
        _bound_schedule(schedule_id="schedule-a"),
        _bound_schedule(schedule_id="schedule-b"),
        _bound_schedule(schedule_id="schedule-c"),
    )
    resources = _ExternalResourceStore()
    registry = _Registry()

    report = reconcile_task_scheduler_resources_bounded(
        TaskSchedulerResourcePumpRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            owner_instance_id="host-a",
            claim_token_prefix="pump-a",
            claim_ttl_ms=30_000,
            schedule_page_limit=2,
            max_schedule_pages=2,
            max_claims=4,
        ),
        schedules=_ScheduleStore(*schedules),
        registry=registry,
        external_resources=resources,
    )

    assert report.schedule_pages_attempted == 2
    assert report.schedules_scanned == 3
    assert report.resources_staged == 3
    assert report.stage_completed is True
    assert report.stage_next_cursor is None
    assert report.claims_attempted == 4
    assert report.resources_reconciled == 3
    assert report.resources_applied == 3
    assert report.resources_completed == 3
    assert report.resources_blocked == 0
    assert report.claim_idle is True
    assert tuple(definition.task_path for definition in registry.applied) == (
        r"\MediaSync Home\install-a\schedule-a",
        r"\MediaSync Home\install-a\schedule-b",
        r"\MediaSync Home\install-a\schedule-c",
    )
    assert tuple(completed[3] for completed in resources.completed) == (
        "pump-a:0001",
        "pump-a:0002",
        "pump-a:0003",
    )
    assert report.orphan_tasks_scanned == 0
    assert report.orphan_tasks_deleted == 0


def test_task_scheduler_resource_pump_reports_stage_cursor_when_page_budget_exhausted() -> None:
    resources = _ExternalResourceStore()

    report = reconcile_task_scheduler_resources_bounded(
        TaskSchedulerResourcePumpRequest(
            installation_id="install-a",
            executable_path=EXECUTABLE,
            owner_instance_id="host-a",
            claim_token_prefix="pump-a",
            claim_ttl_ms=30_000,
            schedule_page_limit=1,
            max_schedule_pages=1,
            max_claims=2,
        ),
        schedules=_ScheduleStore(
            _bound_schedule(schedule_id="schedule-a"),
            _bound_schedule(schedule_id="schedule-b"),
        ),
        registry=_Registry(),
        external_resources=resources,
    )

    assert report.schedule_pages_attempted == 1
    assert report.schedules_scanned == 1
    assert report.stage_completed is False
    assert report.stage_next_cursor == "schedule-a"
    assert report.resources_reconciled == 1
    assert report.claim_idle is True


def test_task_scheduler_resource_pump_requires_bounded_limits() -> None:
    with pytest.raises(
        TaskSchedulerDefinitionViolation,
        match="TASK_SCHEDULER_RECONCILIATION_PUMP_PAGE_LIMIT_OUT_OF_RANGE",
    ):
        reconcile_task_scheduler_resources_bounded(
            TaskSchedulerResourcePumpRequest(
                installation_id="install-a",
                executable_path=EXECUTABLE,
                owner_instance_id="host-a",
                claim_token_prefix="pump-a",
                claim_ttl_ms=30_000,
                schedule_page_limit=1,
                max_schedule_pages=0,
                max_claims=1,
            ),
            schedules=_ScheduleStore(),
            registry=_Registry(),
            external_resources=_ExternalResourceStore(),
        )
    with pytest.raises(
        TaskSchedulerDefinitionViolation,
        match="TASK_SCHEDULER_RECONCILIATION_PUMP_CLAIM_LIMIT_OUT_OF_RANGE",
    ):
        reconcile_task_scheduler_resources_bounded(
            TaskSchedulerResourcePumpRequest(
                installation_id="install-a",
                executable_path=EXECUTABLE,
                owner_instance_id="host-a",
                claim_token_prefix="pump-a",
                claim_ttl_ms=30_000,
                schedule_page_limit=1,
                max_schedule_pages=1,
                max_claims=0,
            ),
            schedules=_ScheduleStore(),
            registry=_Registry(),
            external_resources=_ExternalResourceStore(),
        )


class _ScheduleStore:
    def __init__(self, *schedules: ScheduleDefinition) -> None:
        self.schedules = sorted(schedules, key=lambda item: item.schedule_id)

    def save_schedule(self, schedule: ScheduleDefinition) -> None:
        self.schedules = [
            existing
            for existing in self.schedules
            if existing.schedule_id != schedule.schedule_id
        ]
        self.schedules.append(schedule)
        self.schedules.sort(key=lambda item: item.schedule_id)

    def load_schedule(self, schedule_id: str) -> ScheduleDefinition | None:
        for schedule in self.schedules:
            if schedule.schedule_id == schedule_id:
                return schedule
        return None

    def list_schedules_for_reconciliation(
        self,
        *,
        limit: int,
        after_schedule_id: str | None = None,
    ) -> tuple[ScheduleDefinition, ...]:
        page = [
            schedule
            for schedule in self.schedules
            if after_schedule_id is None or schedule.schedule_id > after_schedule_id
        ]
        return tuple(page[:limit])


class _Registry:
    def __init__(self, *observed: ObservedTaskSchedulerDefinition) -> None:
        self.observed = {definition.task_path: definition for definition in observed}
        self.applied: list[TaskSchedulerDefinition] = []
        self.deleted: tuple[str, ...] = ()

    def load_task(self, task_path: str) -> ObservedTaskSchedulerDefinition | None:
        return self.observed.get(task_path)

    def list_tasks(
        self,
        folder_path: str,
        *,
        limit: int,
        after_task_name: str | None = None,
    ) -> tuple[ObservedTaskSchedulerDefinition, ...]:
        folder_prefix = folder_path.rstrip("\\") + "\\"
        tasks = sorted(
            (
                definition
                for definition in self.observed.values()
                if definition.task_path.startswith(folder_prefix)
            ),
            key=lambda definition: definition.task_path.rsplit("\\", 1)[-1],
        )
        if after_task_name is not None:
            tasks = [
                definition
                for definition in tasks
                if definition.task_path.rsplit("\\", 1)[-1] > after_task_name
            ]
        return tuple(tasks[:limit])

    def apply_task_definition(self, definition: TaskSchedulerDefinition) -> None:
        self.applied.append(definition)

    def delete_task(self, task_path: str) -> None:
        self.deleted = (*self.deleted, task_path)
        self.observed.pop(task_path, None)


class _ExternalResourceStore(ExternalResourceStateStore):
    def __init__(self, *pending: ExternalResourceRecord) -> None:
        self.pending = list(pending)
        self.desired: tuple[tuple[ExternalResourceType, str, int, str], ...] = ()
        self.completed: tuple[tuple[ExternalResourceType, str, int, str, str], ...] = ()
        self.blocked: tuple[tuple[ExternalResourceType, str, str, str], ...] = ()
        self.claims: tuple[tuple[ExternalResourceType, str, str, int], ...] = ()

    def upsert_desired_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        desired_hash: str,
    ) -> ExternalResourceRecord:
        self.desired = (
            *self.desired,
            (resource_type, resource_id, desired_generation, desired_hash),
        )
        self.pending.append(
            ExternalResourceRecord(
                resource_type=resource_type,
                resource_id=resource_id,
                desired_generation=desired_generation,
                desired_hash=desired_hash,
            )
        )
        return ExternalResourceRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            desired_generation=desired_generation,
            desired_hash=desired_hash,
        )

    def load_external_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
    ) -> ExternalResourceRecord | None:
        return None

    def claim_next_pending_external_resource(
        self,
        *,
        resource_type: ExternalResourceType,
        owner_instance_id: str,
        claim_token: str,
        claim_ttl_ms: int,
    ) -> ExternalResourceRecord | None:
        self.claims = (
            *self.claims,
            (resource_type, owner_instance_id, claim_token, claim_ttl_ms),
        )
        for index, record in enumerate(self.pending):
            if record.resource_type is resource_type:
                self.pending.pop(index)
                return replace(
                    record,
                    state=ExternalResourceState.CLAIMED,
                    claim_owner_instance_id=owner_instance_id,
                    claim_token=claim_token,
                    claim_ttl_ms=claim_ttl_ms,
                )
        return None

    def mark_external_resource_in_sync(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        claim_token: str,
        observed_hash: str,
    ) -> ExternalResourceRecord:
        self.completed = (
            *self.completed,
            (resource_type, resource_id, desired_generation, claim_token, observed_hash),
        )
        return ExternalResourceRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            desired_generation=desired_generation,
            desired_hash=observed_hash,
            observed_generation=desired_generation,
            observed_hash=observed_hash,
            state=ExternalResourceState.IN_SYNC,
        )

    def mark_external_resource_blocked(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        claim_token: str,
        error_code: str,
    ) -> ExternalResourceRecord:
        self.blocked = (
            *self.blocked,
            (resource_type, resource_id, claim_token, error_code),
        )
        return ExternalResourceRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            desired_generation=1,
            desired_hash="0" * 64,
            state=ExternalResourceState.BLOCKED,
            last_error_code=error_code,
        )

    def requeue_claimed_after_startup(
        self,
        request: ExternalResourceStartupReconciliationRequest,
    ) -> ExternalResourceStartupReconciliationReport:
        return ExternalResourceStartupReconciliationReport(
            reconciler_instance_id=request.reconciler_instance_id,
            resource_type=request.resource_type,
            scanned=0,
            requeued_resource_ids=(),
        )


def _bound_schedule(
    *,
    schedule_id: str = "schedule-a",
    configuration_json: str = '{"kind":"daily"}',
    enabled: bool = True,
    requires_network: bool = False,
) -> ScheduleDefinition:
    return bind_same_user_task_scheduler_definition_hash(
        _schedule(
            schedule_id=schedule_id,
            configuration_json=configuration_json,
            enabled=enabled,
            requires_network=requires_network,
        ),
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )


def _schedule(
    *,
    schedule_id: str = "schedule-a",
    desired_definition_hash: str = "0" * 64,
    configuration_json: str = '{"kind":"daily"}',
    enabled: bool = True,
    requires_network: bool = False,
) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id=schedule_id,
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
        requires_network=requires_network,
        run_only_when_logged_on=True,
        enabled=enabled,
        row_version=1,
    )


def _observed(
    definition: TaskSchedulerDefinition,
    *,
    executable_path: str | None = None,
    enabled: bool | None = None,
) -> ObservedTaskSchedulerDefinition:
    return ObservedTaskSchedulerDefinition(
        task_path=definition.task_path,
        executable_path=definition.executable_path if executable_path is None else executable_path,
        arguments=definition.arguments,
        enabled=definition.enabled if enabled is None else enabled,
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


def _claimed_resource(
    schedule: ScheduleDefinition,
    *,
    desired_hash: str | None = None,
) -> ExternalResourceRecord:
    return ExternalResourceRecord(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        resource_id=schedule.schedule_id,
        desired_generation=schedule.definition_generation,
        desired_hash=schedule.desired_definition_hash if desired_hash is None else desired_hash,
        state=ExternalResourceState.CLAIMED,
        claim_token="claim-a",
    )
