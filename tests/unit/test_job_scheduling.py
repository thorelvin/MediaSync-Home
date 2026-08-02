from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.job_read_models import (
    InitialBackupPlanSummary,
    StandardBackupJobDetail,
    StandardBackupTargetSummary,
)
from mediasync_home.application.job_scheduling import (
    JobSchedulingPayloadError,
    configure_daily_backup_schedule,
    daily_backup_schedule_id,
    parse_configure_daily_backup_schedule_command,
)
from mediasync_home.application.schedules import ScheduleDefinition


class _Jobs:
    def __init__(self, job: StandardBackupJobDetail | None) -> None:
        self.job = job

    def load_standard_backup_job_detail(
        self,
        job_id: str,
    ) -> StandardBackupJobDetail | None:
        return self.job if self.job is not None and self.job.job_id == job_id else None


class _Schedules:
    def __init__(self) -> None:
        self.values: dict[str, ScheduleDefinition] = {}

    def save_schedule(self, schedule: ScheduleDefinition) -> None:
        self.values[schedule.schedule_id] = schedule

    def load_schedule(self, schedule_id: str) -> ScheduleDefinition | None:
        return self.values.get(schedule_id)

    def list_schedules_for_reconciliation(
        self,
        *,
        limit: int,
        after_schedule_id: str | None = None,
    ) -> tuple[ScheduleDefinition, ...]:
        del limit, after_schedule_id
        return tuple(self.values.values())


def test_configure_daily_schedule_binds_current_plan_and_same_user_policy() -> None:
    schedules = _Schedules()
    outcome = configure_daily_backup_schedule(
        command=_command(enabled=True, local_time="18:30"),
        jobs=_Jobs(_job(source_path="C:/Pictures", target_path=r"\\nas\backup")),
        schedules=schedules,
        installation_id="install-a",
        executable_path=r"C:\Program Files\MediaSync Home\MediaSyncHome.exe",
        time_zone_id="W. Europe Standard Time",
    )

    assert outcome.configured is True
    schedule = outcome.schedule
    assert schedule is not None
    assert schedule.schedule_id == daily_backup_schedule_id("job-a")
    assert schedule.plan_id == "plan-a"
    assert schedule.plan_checksum == "a" * 64
    assert schedule.configuration_json == (
        '{"days_interval":1,"hour":18,"kind":"daily","minute":30}'
    )
    assert schedule.task_logon_type == "INTERACTIVE_TOKEN"
    assert schedule.run_only_when_logged_on is True
    assert schedule.requires_network is True
    assert schedule.time_zone_id == "W. Europe Standard Time"
    assert schedule.desired_definition_hash != "0" * 64
    assert len(schedule.desired_definition_hash) == 64
    assert schedules.values[schedule.schedule_id] == schedule


def test_configure_daily_schedule_rejects_stale_version_and_unready_plan() -> None:
    schedules = _Schedules()
    first = configure_daily_backup_schedule(
        command=_command(enabled=True),
        jobs=_Jobs(_job()),
        schedules=schedules,
        installation_id="install-a",
        executable_path=r"C:\MediaSyncHome.exe",
        time_zone_id="W. Europe Standard Time",
    )
    assert first.configured is True

    stale = configure_daily_backup_schedule(
        command=replace(
            _command(enabled=True, local_time="19:00"),
            expected_schedule_row_version=0,
        ),
        jobs=_Jobs(_job()),
        schedules=schedules,
        installation_id="install-a",
        executable_path=r"C:\MediaSyncHome.exe",
        time_zone_id="W. Europe Standard Time",
    )
    no_plan = configure_daily_backup_schedule(
        command=_command(enabled=True),
        jobs=_Jobs(replace(_job(), initial_plan=None)),
        schedules=_Schedules(),
        installation_id="install-a",
        executable_path=r"C:\MediaSyncHome.exe",
        time_zone_id="W. Europe Standard Time",
    )

    assert stale.validation_code == "BACKUP_AUTOMATION_SCHEDULE_STALE"
    assert no_plan.validation_code == "BACKUP_AUTOMATION_PLAN_NOT_READY"


def test_configure_daily_schedule_can_disable_existing_binding() -> None:
    schedules = _Schedules()
    enabled = configure_daily_backup_schedule(
        command=_command(enabled=True),
        jobs=_Jobs(_job()),
        schedules=schedules,
        installation_id="install-a",
        executable_path=r"C:\MediaSyncHome.exe",
        time_zone_id="W. Europe Standard Time",
    ).schedule
    assert enabled is not None

    disabled = configure_daily_backup_schedule(
        command=replace(
            _command(enabled=False),
            expected_schedule_row_version=enabled.row_version,
        ),
        jobs=_Jobs(replace(_job(), initial_plan=None)),
        schedules=schedules,
        installation_id="install-a",
        executable_path=r"C:\MediaSyncHome.exe",
        time_zone_id="W. Europe Standard Time",
    )

    assert disabled.configured is True
    assert disabled.schedule is not None
    assert disabled.schedule.enabled is False
    assert disabled.schedule.plan_id == enabled.plan_id
    assert disabled.schedule.definition_generation == 2
    assert disabled.schedule.row_version == 2


def test_parse_configure_daily_schedule_is_strict() -> None:
    command = parse_configure_daily_backup_schedule_command(
        request_id="request-a",
        idempotency_key="key-a",
        payload=_payload(),
    )
    assert command.local_time == "18:00"

    with pytest.raises(JobSchedulingPayloadError, match="LOCAL_TIME_INVALID"):
        parse_configure_daily_backup_schedule_command(
            request_id="request-a",
            idempotency_key="key-a",
            payload={**_payload(), "local_time": "8:00"},
        )
    with pytest.raises(JobSchedulingPayloadError, match="PAYLOAD_INVALID"):
        parse_configure_daily_backup_schedule_command(
            request_id="request-a",
            idempotency_key="key-a",
            payload={**_payload(), "task_path": r"\unsafe"},
        )


def _command(*, enabled: bool, local_time: str = "18:00"):
    return parse_configure_daily_backup_schedule_command(
        request_id="request-a",
        idempotency_key="key-a",
        payload={**_payload(), "enabled": enabled, "local_time": local_time},
    )


def _payload() -> dict[str, object]:
    return {
        "job_id": "job-a",
        "expected_job_revision_id": "job-rev-a",
        "expected_lifecycle_row_version": 1,
        "expected_schedule_row_version": 0,
        "enabled": True,
        "local_time": "18:00",
    }


def _job(
    *,
    source_path: str = "C:/Pictures",
    target_path: str = "E:/Backup",
) -> StandardBackupJobDetail:
    return StandardBackupJobDetail(
        job_id="job-a",
        job_revision_id="job-rev-a",
        filter_set_id="filter-a",
        source_name="Pictures",
        source_path_label=source_path,
        targets=(StandardBackupTargetSummary("Target", target_path),),
        defaults=_defaults(),
        initial_plan=InitialBackupPlanSummary(
            state="SEALED",
            reason_code="INITIAL_BACKUP_PLAN_SEALED",
            operation_count=1,
            planned_bytes=10,
            plan_runnable=True,
            next_action="Start backup.",
            plan_id="plan-a",
            plan_checksum="a" * 64,
        ),
    )


def _defaults():
    from mediasync_home.application.job_drafts import StandardBackupDefaults

    return StandardBackupDefaults()
