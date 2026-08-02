from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from mediasync_home.application.job_lifecycle import JobLifecycleState
from mediasync_home.application.job_read_models import (
    StandardBackupJobDetail,
    StandardBackupJobDetailReadModelStore,
)
from mediasync_home.application.schedules import (
    ScheduleDefinition,
    ScheduleStore,
)
from mediasync_home.application.task_scheduler import (
    bind_same_user_task_scheduler_definition_hash,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_LOCAL_TIME = re.compile(r"^(?P<hour>[01][0-9]|2[0-3]):(?P<minute>[0-5][0-9])$")


class JobSchedulingCommandName(str, Enum):
    CONFIGURE_DAILY_BACKUP_SCHEDULE = "CONFIGURE_DAILY_BACKUP_SCHEDULE"


class JobSchedulingPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class ConfigureDailyBackupScheduleCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    expected_job_revision_id: str
    expected_lifecycle_row_version: int
    expected_schedule_row_version: int
    enabled: bool
    local_time: str


@dataclass(frozen=True)
class JobSchedulingOutcome:
    configured: bool
    validation_code: str
    next_action: str
    schedule: ScheduleDefinition | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "validation_code": self.validation_code,
            "next_action": self.next_action,
            "idempotent_replay": self.idempotent_replay,
            "schedule": (
                None if self.schedule is None else schedule_definition_to_dict(self.schedule)
            ),
        }


def parse_configure_daily_backup_schedule_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> ConfigureDailyBackupScheduleCommand:
    required = {
        "job_id",
        "expected_job_revision_id",
        "expected_lifecycle_row_version",
        "expected_schedule_row_version",
        "enabled",
        "local_time",
    }
    if set(payload) != required:
        raise JobSchedulingPayloadError("JOB_SCHEDULING_PAYLOAD_INVALID")
    lifecycle_version = _required_int(
        payload.get("expected_lifecycle_row_version"),
        minimum=1,
        code="JOB_SCHEDULING_LIFECYCLE_VERSION_INVALID",
    )
    schedule_version = _required_int(
        payload.get("expected_schedule_row_version"),
        minimum=0,
        code="JOB_SCHEDULING_SCHEDULE_VERSION_INVALID",
    )
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise JobSchedulingPayloadError("JOB_SCHEDULING_ENABLED_INVALID")
    local_time = payload.get("local_time")
    if not isinstance(local_time, str) or _LOCAL_TIME.fullmatch(local_time) is None:
        raise JobSchedulingPayloadError("JOB_SCHEDULING_LOCAL_TIME_INVALID")
    return ConfigureDailyBackupScheduleCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=_required_identifier(payload.get("job_id"), "JOB_ID"),
        expected_job_revision_id=_required_identifier(
            payload.get("expected_job_revision_id"),
            "JOB_REVISION_ID",
        ),
        expected_lifecycle_row_version=lifecycle_version,
        expected_schedule_row_version=schedule_version,
        enabled=enabled,
        local_time=local_time,
    )


def configure_daily_backup_schedule(
    *,
    command: ConfigureDailyBackupScheduleCommand,
    jobs: StandardBackupJobDetailReadModelStore,
    schedules: ScheduleStore,
    installation_id: str,
    executable_path: str,
    time_zone_id: str,
) -> JobSchedulingOutcome:
    job = jobs.load_standard_backup_job_detail(command.job_id)
    if job is None:
        return _rejected(
            "BACKUP_AUTOMATION_JOB_NOT_FOUND",
            "Refresh Jobs and choose an active backup job.",
        )
    if job.lifecycle_state is not JobLifecycleState.ACTIVE:
        return _rejected(
            "BACKUP_AUTOMATION_JOB_ARCHIVED",
            "Reactivate the backup job before changing automation.",
        )
    if job.job_revision_id != command.expected_job_revision_id:
        return _rejected(
            "BACKUP_AUTOMATION_JOB_REVISION_STALE",
            "Refresh the job before changing automation.",
        )
    if job.lifecycle_row_version != command.expected_lifecycle_row_version:
        return _rejected(
            "BACKUP_AUTOMATION_LIFECYCLE_STALE",
            "Refresh the job before changing automation.",
        )

    schedule_id = daily_backup_schedule_id(command.job_id)
    current = schedules.load_schedule(schedule_id)
    current_row_version = 0 if current is None else current.row_version
    if current_row_version != command.expected_schedule_row_version:
        return _rejected(
            "BACKUP_AUTOMATION_SCHEDULE_STALE",
            "Refresh the job before changing automation.",
        )
    if current is not None and current.job_id != command.job_id:
        return _rejected(
            "BACKUP_AUTOMATION_SCHEDULE_ID_CONFLICT",
            "Repair the conflicting schedule before enabling automation.",
        )
    if not installation_id.strip() or not executable_path.strip() or not time_zone_id.strip():
        return _rejected(
            "BACKUP_AUTOMATION_RUNTIME_UNAVAILABLE",
            "Restart MediaSync Home from the installed desktop application.",
        )
    if not command.enabled and current is None:
        return _rejected(
            "BACKUP_AUTOMATION_NO_CHANGES",
            "Enable automatic backup before saving a new schedule.",
        )

    hour, minute = _local_time_parts(command.local_time)
    configuration_json = json.dumps(
        {
            "days_interval": 1,
            "hour": hour,
            "kind": "daily",
            "minute": minute,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if command.enabled:
        plan = job.initial_plan
        if (
            plan is None
            or plan.state != "SEALED"
            or not plan.plan_runnable
            or plan.plan_id is None
            or plan.plan_checksum is None
        ):
            return _rejected(
                "BACKUP_AUTOMATION_PLAN_NOT_READY",
                "Run a successful backup check before enabling automation.",
            )
        plan_id = plan.plan_id
        plan_checksum = plan.plan_checksum
    else:
        assert current is not None
        plan_id = current.plan_id
        plan_checksum = current.plan_checksum

    if (
        current is not None
        and current.enabled is command.enabled
        and current.configuration_json == configuration_json
        and current.plan_id == plan_id
        and current.plan_checksum == plan_checksum
        and current.time_zone_id == time_zone_id
    ):
        return _rejected(
            "BACKUP_AUTOMATION_NO_CHANGES",
            "Change the daily time or enabled state before saving.",
        )

    schedule = ScheduleDefinition(
        schedule_id=schedule_id,
        job_id=command.job_id,
        plan_id=plan_id,
        plan_checksum=plan_checksum,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json=configuration_json,
        definition_generation=(
            1 if current is None else current.definition_generation + 1
        ),
        desired_definition_hash="0" * 64,
        time_zone_id=time_zone_id,
        dst_policy="PRESERVE_WALL_TIME",
        misfire_policy="QUEUE_ONCE",
        coalescing_window_seconds=60,
        task_logon_type="INTERACTIVE_TOKEN",
        requires_network=_job_requires_network(job),
        run_only_when_logged_on=True,
        enabled=command.enabled,
        row_version=1 if current is None else current.row_version + 1,
        last_triggered_utc=(
            None if current is None else current.last_triggered_utc
        ),
    )
    schedule = bind_same_user_task_scheduler_definition_hash(
        schedule,
        installation_id=installation_id,
        executable_path=executable_path,
    )
    schedules.save_schedule(schedule)
    return JobSchedulingOutcome(
        configured=True,
        validation_code="BACKUP_AUTOMATION_SCHEDULE_UPDATED",
        next_action=(
            "Windows Task Scheduler reconciliation is pending."
            if command.enabled
            else "The Windows scheduled task will be disabled."
        ),
        schedule=schedule,
    )


def daily_backup_schedule_id(job_id: str) -> str:
    digest = hashlib.sha256(f"mediasync-daily-schedule-v1:{job_id}".encode()).hexdigest()
    return f"daily-{digest[:32]}"


def schedule_definition_to_dict(schedule: ScheduleDefinition) -> dict[str, object]:
    configuration = json.loads(schedule.configuration_json)
    return {
        "schedule_id": schedule.schedule_id,
        "job_id": schedule.job_id,
        "plan_id": schedule.plan_id,
        "plan_checksum": schedule.plan_checksum,
        "trigger_type": schedule.trigger_type.value,
        "configuration": configuration,
        "definition_generation": schedule.definition_generation,
        "desired_definition_hash": schedule.desired_definition_hash,
        "time_zone_id": schedule.time_zone_id,
        "task_logon_type": schedule.task_logon_type,
        "requires_network": schedule.requires_network,
        "run_only_when_logged_on": schedule.run_only_when_logged_on,
        "enabled": schedule.enabled,
        "row_version": schedule.row_version,
        "last_triggered_utc": schedule.last_triggered_utc,
    }


def _job_requires_network(job: StandardBackupJobDetail) -> bool:
    return _network_path(job.source_path_label) or any(
        _network_path(target.path_label) for target in job.targets
    )


def _network_path(path_label: str) -> bool:
    normalized = path_label.strip().replace("/", "\\")
    return normalized.startswith("\\\\")


def _local_time_parts(value: str) -> tuple[int, int]:
    match = _LOCAL_TIME.fullmatch(value)
    if match is None:
        raise JobSchedulingPayloadError("JOB_SCHEDULING_LOCAL_TIME_INVALID")
    return int(match.group("hour")), int(match.group("minute"))


def _required_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value.strip()) is None:
        raise JobSchedulingPayloadError(f"JOB_SCHEDULING_{field_name}_INVALID")
    return value.strip()


def _required_int(value: object, *, minimum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise JobSchedulingPayloadError(code)
    return value


def _rejected(validation_code: str, next_action: str) -> JobSchedulingOutcome:
    return JobSchedulingOutcome(
        configured=False,
        validation_code=validation_code,
        next_action=next_action,
    )
