from __future__ import annotations

import pytest

from mediasync_home.application.schedules import (
    ScheduleDefinition,
    ScheduleTriggerResolutionKind,
    ScheduleViolation,
    resolve_schedule_for_trigger,
    validate_schedule_definition,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


class _ScheduleStore:
    def __init__(self, schedule: ScheduleDefinition | None) -> None:
        self.schedule = schedule

    def save_schedule(self, schedule: ScheduleDefinition) -> None:
        self.schedule = schedule

    def load_schedule(self, schedule_id: str) -> ScheduleDefinition | None:
        if self.schedule is None or self.schedule.schedule_id != schedule_id:
            return None
        return self.schedule


def test_resolve_schedule_for_trigger_accepts_matching_enabled_definition() -> None:
    resolution = resolve_schedule_for_trigger(
        schedules=_ScheduleStore(_schedule()),
        schedule_id="schedule-a",
        schedule_revision_hash="b" * 64,
    )

    assert resolution.kind is ScheduleTriggerResolutionKind.READY
    assert resolution.schedule is not None
    assert resolution.schedule.plan_id == "plan-a"


def test_resolve_schedule_for_trigger_reports_missing_disabled_and_revision_drift() -> None:
    missing = resolve_schedule_for_trigger(
        schedules=_ScheduleStore(None),
        schedule_id="schedule-a",
        schedule_revision_hash="b" * 64,
    )
    disabled = resolve_schedule_for_trigger(
        schedules=_ScheduleStore(_schedule(enabled=False)),
        schedule_id="schedule-a",
        schedule_revision_hash="b" * 64,
    )
    drift = resolve_schedule_for_trigger(
        schedules=_ScheduleStore(_schedule()),
        schedule_id="schedule-a",
        schedule_revision_hash="c" * 64,
    )

    assert missing.kind is ScheduleTriggerResolutionKind.NOT_FOUND
    assert disabled.kind is ScheduleTriggerResolutionKind.DISABLED
    assert drift.kind is ScheduleTriggerResolutionKind.REVISION_MISMATCH


def test_schedule_definition_requires_stable_ids_and_hashes() -> None:
    with pytest.raises(ScheduleViolation, match="SCHEDULE_INVALID_SCHEDULE_ID"):
        validate_schedule_definition(_schedule(schedule_id="../schedule"))
    with pytest.raises(ScheduleViolation, match="SCHEDULE_INVALID_PLAN_CHECKSUM"):
        validate_schedule_definition(_schedule(plan_checksum="not-a-hash"))


def _schedule(
    *,
    schedule_id: str = "schedule-a",
    plan_checksum: str = "a" * 64,
    enabled: bool = True,
) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id=schedule_id,
        job_id="job-a",
        plan_id="plan-a",
        plan_checksum=plan_checksum,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash="b" * 64,
        enabled=enabled,
    )
