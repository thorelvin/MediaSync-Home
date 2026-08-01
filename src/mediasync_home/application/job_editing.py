from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from mediasync_home.application.job_creation import (
    STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB,
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
    parse_inline_standard_backup_draft,
)
from mediasync_home.application.job_drafts import (
    StandardBackupJobDraft,
    draft_path_labels_overlap,
)
from mediasync_home.application.job_lifecycle import (
    JobLifecycleRecord,
    JobLifecycleState,
)


class JobEditingCommandName(str, Enum):
    UPDATE_STANDARD_BACKUP_JOB = "UPDATE_STANDARD_BACKUP_JOB"


class JobEditingPayloadError(ValueError):
    pass


class JobScheduleInvalidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateStandardBackupJobCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    expected_job_revision_id: str
    expected_lifecycle_row_version: int
    draft: StandardBackupJobDraft
    explicit_save: bool
    check_after_save: bool


@dataclass(frozen=True, slots=True)
class JobEditingOutcome:
    saved: bool
    validation_code: str
    next_action: str
    job: SealedStandardBackupJob | None = None
    requires_full_check: bool = False
    changed_fields: tuple[str, ...] = ()
    idempotent_replay: bool = False
    disabled_schedule_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "saved": self.saved,
            "validation_code": self.validation_code,
            "next_action": self.next_action,
            "requires_full_check": self.requires_full_check,
            "changed_fields": list(self.changed_fields),
            "idempotent_replay": self.idempotent_replay,
            "disabled_schedule_count": self.disabled_schedule_count,
        }


class StandardBackupJobRevisionCatalog(Protocol):
    def load_standard_backup_job(
        self,
        job_id: str,
    ) -> SealedStandardBackupJob | None: ...

    def load_standard_backup_job_revision(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> SealedStandardBackupJob | None: ...

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None: ...

    def list_active_standard_backup_jobs(
        self,
    ) -> tuple[SealedStandardBackupJob, ...]: ...

    def append_standard_backup_job_revision(
        self,
        job: SealedStandardBackupJob,
        *,
        expected_active_revision_id: str,
    ) -> None: ...


class StandardBackupJobRevisionIdFactory(Protocol):
    def new_standard_backup_job_revision_id(self) -> str: ...


class ActiveJobRunLookup(Protocol):
    def load_active_run_for_job(self, job_id: str) -> object | None: ...


class JobScheduleInvalidator(Protocol):
    def disable_enabled_schedules(self, job_id: str) -> int: ...


class JobLifecycleLookup(Protocol):
    def load_job_lifecycle(self, job_id: str) -> JobLifecycleRecord | None: ...


def parse_update_standard_backup_job_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> UpdateStandardBackupJobCommand:
    required = {
        "job_id",
        "expected_job_revision_id",
        "expected_lifecycle_row_version",
        "draft",
        "explicit_save",
        "check_after_save",
    }
    if set(payload) != required:
        raise JobEditingPayloadError("UPDATE_STANDARD_BACKUP_JOB_PAYLOAD_INVALID")
    job_id = _required_identifier(payload.get("job_id"), "JOB_ID")
    expected_revision_id = _required_identifier(
        payload.get("expected_job_revision_id"),
        "JOB_REVISION_ID",
    )
    row_version = payload.get("expected_lifecycle_row_version")
    if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 1:
        raise JobEditingPayloadError(
            "UPDATE_STANDARD_BACKUP_JOB_LIFECYCLE_VERSION_INVALID"
        )
    if payload.get("explicit_save") is not True:
        raise JobEditingPayloadError("UPDATE_STANDARD_BACKUP_JOB_SAVE_REQUIRED")
    check_after_save = payload.get("check_after_save")
    if not isinstance(check_after_save, bool):
        raise JobEditingPayloadError(
            "UPDATE_STANDARD_BACKUP_JOB_CHECK_AFTER_SAVE_INVALID"
        )
    raw_draft = payload.get("draft")
    if not isinstance(raw_draft, dict):
        raise JobEditingPayloadError("UPDATE_STANDARD_BACKUP_JOB_DRAFT_INVALID")
    draft_id = raw_draft.get("draft_id")
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise JobEditingPayloadError("UPDATE_STANDARD_BACKUP_JOB_DRAFT_ID_INVALID")
    try:
        draft = parse_inline_standard_backup_draft(raw_draft, draft_id=draft_id)
    except ValueError as exc:
        raise JobEditingPayloadError(str(exc)) from exc
    if draft is None:
        raise JobEditingPayloadError("UPDATE_STANDARD_BACKUP_JOB_DRAFT_INVALID")
    return UpdateStandardBackupJobCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        expected_job_revision_id=expected_revision_id,
        expected_lifecycle_row_version=row_version,
        draft=draft,
        explicit_save=True,
        check_after_save=check_after_save,
    )


def update_standard_backup_job_from_draft(
    *,
    command: UpdateStandardBackupJobCommand,
    catalog: StandardBackupJobRevisionCatalog,
    runs: ActiveJobRunLookup,
    id_factory: StandardBackupJobRevisionIdFactory,
    schedules: JobScheduleInvalidator,
    lifecycle: JobLifecycleLookup,
) -> JobEditingOutcome:
    replay = catalog.load_standard_backup_job_by_idempotency_key(
        command.idempotency_key
    )
    if replay is not None:
        previous = catalog.load_standard_backup_job_revision(
            job_id=command.job_id,
            job_revision_id=command.expected_job_revision_id,
        )
        changed_fields = () if previous is None else _changed_fields(previous, replay)
        active = catalog.load_standard_backup_job(command.job_id)
        return JobEditingOutcome(
            saved=True,
            validation_code="STANDARD_BACKUP_JOB_UPDATED",
            next_action=_next_action(_requires_full_check(changed_fields)),
            job=active if active is not None else replay,
            requires_full_check=_requires_full_check(changed_fields),
            changed_fields=changed_fields,
            idempotent_replay=True,
        )

    lifecycle_record = lifecycle.load_job_lifecycle(command.job_id)
    if lifecycle_record is None:
        return _rejected(
            "STANDARD_BACKUP_JOB_NOT_FOUND",
            "Refresh Jobs and choose an active backup job.",
        )
    if lifecycle_record.state is not JobLifecycleState.ACTIVE:
        return _rejected(
            "STANDARD_BACKUP_JOB_ARCHIVED",
            "Reactivate the backup job before editing it.",
        )
    if lifecycle_record.row_version != command.expected_lifecycle_row_version:
        return _rejected(
            "STANDARD_BACKUP_JOB_LIFECYCLE_STALE",
            "Refresh the job before saving changes.",
        )
    current = catalog.load_standard_backup_job(command.job_id)
    if current is None:
        return _rejected(
            "STANDARD_BACKUP_JOB_NOT_FOUND",
            "Refresh Jobs and choose an active backup job.",
        )
    if current.job_revision_id != command.expected_job_revision_id:
        return _rejected(
            "STANDARD_BACKUP_JOB_REVISION_STALE",
            "Refresh the job before saving changes.",
        )
    active_run = runs.load_active_run_for_job(command.job_id) is not None
    issues = command.draft.validation_issues()
    if issues:
        return _rejected(
            issues[0].code.value,
            "Complete the edited backup setup before saving changes.",
        )
    if _overlaps_other_active_job(
        command.draft,
        catalog.list_active_standard_backup_jobs(),
        edited_job_id=command.job_id,
    ):
        return _rejected(
            STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB,
            "Choose roots that do not overlap another active backup job.",
        )

    changed_fields = _draft_changed_fields(current, command.draft)
    if not changed_fields:
        return _rejected(
            "STANDARD_BACKUP_JOB_NO_CHANGES",
            "Change at least one job setting before saving.",
        )
    requires_full_check = _requires_full_check(changed_fields)
    if active_run and requires_full_check:
        return _rejected(
            "STANDARD_BACKUP_JOB_ACTIVE_RUN",
            "Wait for the active backup to finish or stop it safely before editing.",
        )
    edited = _seal_edited_job(
        command=command,
        current=current,
        revision_id=id_factory.new_standard_backup_job_revision_id(),
    )
    catalog.append_standard_backup_job_revision(
        edited,
        expected_active_revision_id=current.job_revision_id,
    )
    disabled_schedule_count = (
        schedules.disable_enabled_schedules(command.job_id)
        if requires_full_check
        else 0
    )
    return JobEditingOutcome(
        saved=True,
        validation_code="STANDARD_BACKUP_JOB_UPDATED",
        next_action=_next_action(requires_full_check),
        job=edited,
        requires_full_check=requires_full_check,
        changed_fields=changed_fields,
        disabled_schedule_count=disabled_schedule_count,
    )


def _seal_edited_job(
    *,
    command: UpdateStandardBackupJobCommand,
    current: SealedStandardBackupJob,
    revision_id: str,
) -> SealedStandardBackupJob:
    draft = command.draft
    assert draft.source_name is not None
    assert draft.source_path_label is not None
    return SealedStandardBackupJob(
        job_id=current.job_id,
        job_revision_id=revision_id,
        filter_set_id=current.filter_set_id,
        draft_id=draft.draft_id,
        command_request_id=command.request_id,
        idempotency_key=command.idempotency_key,
        source_name=draft.source_name,
        source_path_label=draft.source_path_label,
        targets=tuple(
            SealedStandardBackupTarget(
                name=target.name,
                path_label=target.path_label,
                independent_device_id=target.independent_device_id,
            )
            for target in draft.targets
        ),
        defaults=draft.defaults,
        filter_set_version=(
            current.filter_set_version
            if draft.defaults.file_selection is current.defaults.file_selection
            else current.filter_set_version + 1
        ),
    )


def _changed_fields(
    current: SealedStandardBackupJob,
    edited: SealedStandardBackupJob,
) -> tuple[str, ...]:
    changed: list[str] = []
    if current.source_name != edited.source_name:
        changed.append("name")
    if current.source_path_label != edited.source_path_label:
        changed.append("source")
    if current.targets != edited.targets:
        changed.append("targets")
    if current.defaults != edited.defaults:
        changed.append("defaults")
    return tuple(changed)


def _draft_changed_fields(
    current: SealedStandardBackupJob,
    draft: StandardBackupJobDraft,
) -> tuple[str, ...]:
    changed: list[str] = []
    if current.source_name != draft.source_name:
        changed.append("name")
    if current.source_path_label != draft.source_path_label:
        changed.append("source")
    draft_targets = tuple(
        SealedStandardBackupTarget(
            name=target.name,
            path_label=target.path_label,
            independent_device_id=target.independent_device_id,
        )
        for target in draft.targets
    )
    if current.targets != draft_targets:
        changed.append("targets")
    if current.defaults != draft.defaults:
        changed.append("defaults")
    return tuple(changed)


def _requires_full_check(changed_fields: tuple[str, ...]) -> bool:
    return any(field != "name" for field in changed_fields)


def _next_action(requires_full_check: bool) -> str:
    if requires_full_check:
        return "A full check must complete before the edited backup can run."
    return "The job name was saved without invalidating the current analysis."


def _overlaps_other_active_job(
    draft: StandardBackupJobDraft,
    jobs: tuple[SealedStandardBackupJob, ...],
    *,
    edited_job_id: str,
) -> bool:
    draft_roots = _draft_roots(draft)
    for job in jobs:
        if job.job_id == edited_job_id:
            continue
        for draft_root in draft_roots:
            for job_root in _job_roots(job):
                if not draft_root[1] and not job_root[1]:
                    continue
                if draft_path_labels_overlap(draft_root[0], job_root[0]):
                    return True
    return False


def _draft_roots(draft: StandardBackupJobDraft) -> tuple[tuple[str, bool], ...]:
    roots: list[tuple[str, bool]] = []
    if draft.source_path_label is not None:
        roots.append((draft.source_path_label, False))
    roots.extend((target.path_label, True) for target in draft.targets)
    return tuple(roots)


def _job_roots(job: SealedStandardBackupJob) -> tuple[tuple[str, bool], ...]:
    return (
        (job.source_path_label, False),
        *((target.path_label, True) for target in job.targets),
    )


def _rejected(validation_code: str, next_action: str) -> JobEditingOutcome:
    return JobEditingOutcome(
        saved=False,
        validation_code=validation_code,
        next_action=next_action,
    )


def _required_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise JobEditingPayloadError(
            f"UPDATE_STANDARD_BACKUP_JOB_{field_name}_INVALID"
        )
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise JobEditingPayloadError(
            f"UPDATE_STANDARD_BACKUP_JOB_{field_name}_INVALID"
        )
    return normalized
