from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.job_creation import (
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
)
from mediasync_home.application.job_drafts import StandardBackupDefaults
from mediasync_home.application.job_editing import (
    ActiveJobRunLookup,
    JobEditingPayloadError,
    JobLifecycleLookup,
    JobScheduleInvalidator,
    StandardBackupJobRevisionCatalog,
    StandardBackupJobRevisionIdFactory,
    UpdateStandardBackupJobCommand,
    parse_update_standard_backup_job_command,
    update_standard_backup_job_from_draft,
)
from mediasync_home.application.job_lifecycle import (
    JobLifecycleRecord,
    JobLifecycleState,
)


class InMemoryRevisionCatalog(StandardBackupJobRevisionCatalog):
    def __init__(self, *jobs: SealedStandardBackupJob) -> None:
        self.active = {job.job_id: job for job in jobs}
        self.revisions = {
            (job.job_id, job.job_revision_id): job for job in jobs
        }
        self.idempotency = {job.idempotency_key: job for job in jobs}
        self.appended: list[SealedStandardBackupJob] = []

    def load_standard_backup_job(
        self,
        job_id: str,
    ) -> SealedStandardBackupJob | None:
        return self.active.get(job_id)

    def load_standard_backup_job_revision(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> SealedStandardBackupJob | None:
        return self.revisions.get((job_id, job_revision_id))

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None:
        return self.idempotency.get(idempotency_key)

    def list_active_standard_backup_jobs(
        self,
    ) -> tuple[SealedStandardBackupJob, ...]:
        return tuple(self.active.values())

    def append_standard_backup_job_revision(
        self,
        job: SealedStandardBackupJob,
        *,
        expected_active_revision_id: str,
    ) -> None:
        assert self.active[job.job_id].job_revision_id == expected_active_revision_id
        self.active[job.job_id] = job
        self.revisions[(job.job_id, job.job_revision_id)] = job
        self.idempotency[job.idempotency_key] = job
        self.appended.append(job)


class FixedRevisionIdFactory(StandardBackupJobRevisionIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_standard_backup_job_revision_id(self) -> str:
        self.calls += 1
        return "revision-b"


class ActiveRuns(ActiveJobRunLookup):
    def __init__(self, active_job_id: str | None = None) -> None:
        self.active_job_id = active_job_id

    def load_active_run_for_job(self, job_id: str) -> object | None:
        return object() if job_id == self.active_job_id else None


class ScheduleInvalidator(JobScheduleInvalidator):
    def __init__(self, disabled_count: int = 0) -> None:
        self.disabled_count = disabled_count
        self.calls: list[str] = []

    def disable_enabled_schedules(self, job_id: str) -> int:
        self.calls.append(job_id)
        return self.disabled_count


class LifecycleLookup(JobLifecycleLookup):
    def __init__(
        self,
        *,
        state: JobLifecycleState = JobLifecycleState.ACTIVE,
        row_version: int = 1,
    ) -> None:
        self.state = state
        self.row_version = row_version

    def load_job_lifecycle(self, job_id: str) -> JobLifecycleRecord | None:
        return JobLifecycleRecord(
            job_id=job_id,
            job_revision_id="revision-a",
            state=self.state,
            row_version=self.row_version,
        )


def test_parse_update_standard_backup_job_command_requires_explicit_save() -> None:
    payload = _payload(name="Pictures renamed")
    payload["explicit_save"] = False

    with pytest.raises(
        JobEditingPayloadError,
        match="UPDATE_STANDARD_BACKUP_JOB_SAVE_REQUIRED",
    ):
        parse_update_standard_backup_job_command(
            request_id="request-b",
            idempotency_key="idempotency-b",
            payload=payload,
        )


def test_name_only_edit_appends_revision_without_requiring_full_check() -> None:
    catalog = InMemoryRevisionCatalog(_job())
    ids = FixedRevisionIdFactory()
    schedules = ScheduleInvalidator()

    outcome = update_standard_backup_job_from_draft(
        command=_command(name="Pictures renamed"),
        catalog=catalog,
        runs=ActiveRuns(),
        id_factory=ids,
        schedules=schedules,
        lifecycle=LifecycleLookup(),
    )

    assert outcome.saved is True
    assert outcome.requires_full_check is False
    assert outcome.changed_fields == ("name",)
    assert outcome.job is not None
    assert outcome.job.job_revision_id == "revision-b"
    assert outcome.job.source_name == "Pictures renamed"
    assert outcome.job.filter_set_version == 1
    assert catalog.load_standard_backup_job_revision(
        job_id="job-a",
        job_revision_id="revision-a",
    ) == _job()
    assert ids.calls == 1
    assert schedules.calls == []


def test_target_edit_requires_full_check_and_preserves_filter_version() -> None:
    catalog = InMemoryRevisionCatalog(_job())
    schedules = ScheduleInvalidator(disabled_count=2)

    outcome = update_standard_backup_job_from_draft(
        command=_command(target_path="F:/Backup"),
        catalog=catalog,
        runs=ActiveRuns(),
        id_factory=FixedRevisionIdFactory(),
        schedules=schedules,
        lifecycle=LifecycleLookup(),
    )

    assert outcome.saved is True
    assert outcome.requires_full_check is True
    assert outcome.changed_fields == ("targets",)
    assert outcome.job is not None
    assert outcome.job.targets[0].path_label == "F:/Backup"
    assert outcome.job.filter_set_version == 1
    assert outcome.disabled_schedule_count == 2
    assert schedules.calls == ["job-a"]


def test_edit_rejects_stale_revision_without_allocating_id() -> None:
    catalog = InMemoryRevisionCatalog(_job())
    ids = FixedRevisionIdFactory()
    command = replace(_command(name="Pictures renamed"), expected_job_revision_id="stale")

    outcome = update_standard_backup_job_from_draft(
        command=command,
        catalog=catalog,
        runs=ActiveRuns(),
        id_factory=ids,
        schedules=ScheduleInvalidator(),
        lifecycle=LifecycleLookup(),
    )

    assert outcome.saved is False
    assert outcome.validation_code == "STANDARD_BACKUP_JOB_REVISION_STALE"
    assert catalog.appended == []
    assert ids.calls == 0


def test_edit_rejects_safety_change_during_active_run_without_allocating_id() -> None:
    catalog = InMemoryRevisionCatalog(_job())
    ids = FixedRevisionIdFactory()

    outcome = update_standard_backup_job_from_draft(
        command=_command(target_path="F:/Backup"),
        catalog=catalog,
        runs=ActiveRuns("job-a"),
        id_factory=ids,
        schedules=ScheduleInvalidator(),
        lifecycle=LifecycleLookup(),
    )

    assert outcome.saved is False
    assert outcome.validation_code == "STANDARD_BACKUP_JOB_ACTIVE_RUN"
    assert catalog.appended == []
    assert ids.calls == 0


def test_edit_allows_name_only_change_during_active_run() -> None:
    catalog = InMemoryRevisionCatalog(_job())

    outcome = update_standard_backup_job_from_draft(
        command=_command(name="Pictures renamed"),
        catalog=catalog,
        runs=ActiveRuns("job-a"),
        id_factory=FixedRevisionIdFactory(),
        schedules=ScheduleInvalidator(),
        lifecycle=LifecycleLookup(),
    )

    assert outcome.saved is True
    assert outcome.changed_fields == ("name",)
    assert outcome.requires_full_check is False


def test_edit_rejects_overlap_with_another_active_job() -> None:
    other = replace(
        _job(),
        job_id="job-other",
        job_revision_id="revision-other",
        idempotency_key="idempotency-other",
        source_name="Documents",
        source_path_label="D:/Documents",
        targets=(SealedStandardBackupTarget("Archive", "F:/Archive"),),
    )
    catalog = InMemoryRevisionCatalog(_job(), other)

    outcome = update_standard_backup_job_from_draft(
        command=_command(target_path="F:/Archive/Child"),
        catalog=catalog,
        runs=ActiveRuns(),
        id_factory=FixedRevisionIdFactory(),
        schedules=ScheduleInvalidator(),
        lifecycle=LifecycleLookup(),
    )

    assert outcome.saved is False
    assert (
        outcome.validation_code
        == "STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB"
    )


def test_edit_replays_the_saved_revision_without_appending_again() -> None:
    catalog = InMemoryRevisionCatalog(_job())
    ids = FixedRevisionIdFactory()
    command = _command(name="Pictures renamed")
    first = update_standard_backup_job_from_draft(
        command=command,
        catalog=catalog,
        runs=ActiveRuns(),
        id_factory=ids,
        schedules=ScheduleInvalidator(),
        lifecycle=LifecycleLookup(),
    )

    replay = update_standard_backup_job_from_draft(
        command=command,
        catalog=catalog,
        runs=ActiveRuns(),
        id_factory=ids,
        schedules=ScheduleInvalidator(),
        lifecycle=LifecycleLookup(),
    )

    assert first.saved is True
    assert replay.saved is True
    assert replay.idempotent_replay is True
    assert replay.changed_fields == ("name",)
    assert len(catalog.appended) == 1
    assert ids.calls == 1


def _command(
    *,
    name: str = "Pictures",
    target_path: str = "E:/Backup",
) -> UpdateStandardBackupJobCommand:
    return parse_update_standard_backup_job_command(
        request_id="request-b",
        idempotency_key="idempotency-b",
        payload=_payload(name=name, target_path=target_path),
    )


def _payload(
    *,
    name: str = "Pictures",
    target_path: str = "E:/Backup",
) -> dict[str, object]:
    return {
        "job_id": "job-a",
        "expected_job_revision_id": "revision-a",
        "expected_lifecycle_row_version": 1,
        "explicit_save": True,
        "check_after_save": True,
        "draft": {
            "draft_id": "draft-edit-a",
            "schema_version": 1,
            "source_name": name,
            "source_path_label": "C:/Users/Ada/Pictures",
            "targets": [
                {
                    "name": "USB 1",
                    "path_label": target_path,
                    "independent_device_id": "disk-a",
                }
            ],
        },
    }


def _job() -> SealedStandardBackupJob:
    return SealedStandardBackupJob(
        job_id="job-a",
        job_revision_id="revision-a",
        filter_set_id="filter-a",
        draft_id="draft-a",
        command_request_id="request-a",
        idempotency_key="idempotency-a",
        source_name="Pictures",
        source_path_label="C:/Users/Ada/Pictures",
        targets=(
            SealedStandardBackupTarget(
                name="USB 1",
                path_label="E:/Backup",
                independent_device_id="disk-a",
            ),
        ),
        defaults=StandardBackupDefaults(),
    )
