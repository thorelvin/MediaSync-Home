from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.trigger_occurrences import TriggerKind


HEX_256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ScheduleViolation(ValueError):
    pass


class ScheduleTriggerResolutionKind(str, Enum):
    READY = "READY"
    NOT_FOUND = "NOT_FOUND"
    DISABLED = "DISABLED"
    REVISION_MISMATCH = "REVISION_MISMATCH"


@dataclass(frozen=True)
class ScheduleDefinition:
    schedule_id: str
    job_id: str
    plan_id: str
    plan_checksum: str
    trigger_type: TriggerKind
    configuration_json: str
    definition_generation: int
    desired_definition_hash: str
    time_zone_id: str | None = None
    dst_policy: str = "PRESERVE_WALL_TIME"
    misfire_policy: str = "QUEUE_ONCE"
    coalescing_window_seconds: int = 60
    task_logon_type: str = "INTERACTIVE_TOKEN"
    requires_network: bool = False
    run_only_when_logged_on: bool = True
    enabled: bool = True
    row_version: int = 1
    last_triggered_utc: str | None = None


@dataclass(frozen=True)
class ScheduleTriggerResolution:
    kind: ScheduleTriggerResolutionKind
    schedule: ScheduleDefinition | None = None


class ScheduleStore(Protocol):
    def save_schedule(self, schedule: ScheduleDefinition) -> None: ...

    def load_schedule(self, schedule_id: str) -> ScheduleDefinition | None: ...


def resolve_schedule_for_trigger(
    *,
    schedules: ScheduleStore,
    schedule_id: str,
    schedule_revision_hash: str,
) -> ScheduleTriggerResolution:
    schedule = schedules.load_schedule(schedule_id)
    if schedule is None:
        return ScheduleTriggerResolution(ScheduleTriggerResolutionKind.NOT_FOUND)
    if not schedule.enabled:
        return ScheduleTriggerResolution(
            ScheduleTriggerResolutionKind.DISABLED,
            schedule=schedule,
        )
    if schedule.desired_definition_hash != schedule_revision_hash:
        return ScheduleTriggerResolution(
            ScheduleTriggerResolutionKind.REVISION_MISMATCH,
            schedule=schedule,
        )
    return ScheduleTriggerResolution(ScheduleTriggerResolutionKind.READY, schedule=schedule)


def validate_schedule_definition(schedule: ScheduleDefinition) -> None:
    _identifier(schedule.schedule_id, "SCHEDULE_ID")
    _identifier(schedule.job_id, "JOB_ID")
    _identifier(schedule.plan_id, "PLAN_ID")
    _hash(schedule.plan_checksum, "PLAN_CHECKSUM")
    _hash(schedule.desired_definition_hash, "DESIRED_DEFINITION_HASH")
    if not schedule.configuration_json.strip():
        raise ScheduleViolation("SCHEDULE_REQUIRES_CONFIGURATION")
    if schedule.definition_generation < 1:
        raise ScheduleViolation("SCHEDULE_DEFINITION_GENERATION_MUST_BE_POSITIVE")
    if schedule.coalescing_window_seconds < 0:
        raise ScheduleViolation("SCHEDULE_COALESCING_WINDOW_MUST_NOT_BE_NEGATIVE")
    if schedule.row_version < 1:
        raise ScheduleViolation("SCHEDULE_ROW_VERSION_MUST_BE_POSITIVE")
    for value, field_name in (
        (schedule.dst_policy, "DST_POLICY"),
        (schedule.misfire_policy, "MISFIRE_POLICY"),
        (schedule.task_logon_type, "TASK_LOGON_TYPE"),
    ):
        _identifier(value, field_name)
    if schedule.time_zone_id is not None and not schedule.time_zone_id.strip():
        raise ScheduleViolation("SCHEDULE_TIME_ZONE_ID_MUST_NOT_BE_EMPTY")


def _identifier(value: str, field_name: str) -> None:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ScheduleViolation(f"SCHEDULE_INVALID_{field_name}")


def _hash(value: str, field_name: str) -> None:
    if HEX_256_PATTERN.fullmatch(value) is None:
        raise ScheduleViolation(f"SCHEDULE_INVALID_{field_name}")
