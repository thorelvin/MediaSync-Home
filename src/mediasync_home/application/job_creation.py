from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from mediasync_home.application.job_drafts import (
    DraftTarget,
    JobDraftStore,
    StandardBackupDefaults,
    StandardBackupJobDraft,
)


class JobCreationCommandName(str, Enum):
    CREATE_STANDARD_BACKUP_JOB = "CREATE_STANDARD_BACKUP_JOB"


class JobCreationPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class CreateStandardBackupJobCommand:
    request_id: str
    idempotency_key: str
    draft_id: str


@dataclass(frozen=True)
class StandardBackupJobIds:
    job_id: str
    job_revision_id: str
    filter_set_id: str


@dataclass(frozen=True)
class SealedStandardBackupTarget:
    name: str
    path_label: str
    independent_device_id: str | None = None


@dataclass(frozen=True)
class SealedStandardBackupJob:
    job_id: str
    job_revision_id: str
    filter_set_id: str
    draft_id: str
    command_request_id: str
    idempotency_key: str
    source_name: str
    source_path_label: str
    targets: tuple[SealedStandardBackupTarget, ...]
    defaults: StandardBackupDefaults


@dataclass(frozen=True)
class JobCreationReadiness:
    draft_id: str
    draft_found: bool
    draft_valid: bool
    validation_codes: tuple[str, ...]
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "draft_found": self.draft_found,
            "draft_valid": self.draft_valid,
            "validation_codes": list(self.validation_codes),
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class JobCreationOutcome:
    created: bool
    idempotent_replay: bool
    readiness: JobCreationReadiness
    job: SealedStandardBackupJob | None = None


class StandardBackupJobCatalog(Protocol):
    def save_standard_backup_job(self, job: SealedStandardBackupJob) -> None: ...

    def load_standard_backup_job(self, job_id: str) -> SealedStandardBackupJob | None: ...

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None: ...


class StandardBackupJobIdFactory(Protocol):
    def new_standard_backup_job_ids(self) -> StandardBackupJobIds: ...


def parse_create_standard_backup_job_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> CreateStandardBackupJobCommand:
    draft_id = payload.get("draft_id")
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_REQUIRES_DRAFT_ID")
    return CreateStandardBackupJobCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        draft_id=draft_id,
    )


def evaluate_standard_backup_job_creation(
    *,
    command: CreateStandardBackupJobCommand,
    drafts: JobDraftStore,
) -> JobCreationReadiness:
    draft = drafts.load_standard_backup_draft(command.draft_id)
    if draft is None:
        return JobCreationReadiness(
            draft_id=command.draft_id,
            draft_found=False,
            draft_valid=False,
            validation_codes=("DRAFT_NOT_FOUND",),
            next_action="Select source and targets before creating a backup job.",
        )

    issues = draft.validation_issues()
    if issues:
        return JobCreationReadiness(
            draft_id=command.draft_id,
            draft_found=True,
            draft_valid=False,
            validation_codes=tuple(issue.code.value for issue in issues),
            next_action="Complete the backup setup draft before creating a backup job.",
        )

    return JobCreationReadiness(
        draft_id=command.draft_id,
        draft_found=True,
        draft_valid=True,
        validation_codes=(),
        next_action="Backup job creation is recognized but disabled in the 0B local preview.",
    )


def create_standard_backup_job_from_draft(
    *,
    command: CreateStandardBackupJobCommand,
    drafts: JobDraftStore,
    catalog: StandardBackupJobCatalog,
    id_factory: StandardBackupJobIdFactory,
) -> JobCreationOutcome:
    existing = catalog.load_standard_backup_job_by_idempotency_key(command.idempotency_key)
    if existing is not None:
        return JobCreationOutcome(
            created=False,
            idempotent_replay=True,
            readiness=_ready_for_creation(command.draft_id),
            job=existing,
        )

    draft = drafts.load_standard_backup_draft(command.draft_id)
    if draft is None:
        return JobCreationOutcome(
            created=False,
            idempotent_replay=False,
            readiness=_missing_draft(command.draft_id),
        )

    issues = draft.validation_issues()
    if issues:
        return JobCreationOutcome(
            created=False,
            idempotent_replay=False,
            readiness=JobCreationReadiness(
                draft_id=command.draft_id,
                draft_found=True,
                draft_valid=False,
                validation_codes=tuple(issue.code.value for issue in issues),
                next_action="Complete the backup setup draft before creating a backup job.",
            ),
        )

    ids = id_factory.new_standard_backup_job_ids()
    job = _seal_standard_backup_job(command=command, draft=draft, ids=ids)
    catalog.save_standard_backup_job(job)
    return JobCreationOutcome(
        created=True,
        idempotent_replay=False,
        readiness=_ready_for_creation(command.draft_id),
        job=job,
    )


def _seal_standard_backup_job(
    *,
    command: CreateStandardBackupJobCommand,
    draft: StandardBackupJobDraft,
    ids: StandardBackupJobIds,
) -> SealedStandardBackupJob:
    return SealedStandardBackupJob(
        job_id=ids.job_id,
        job_revision_id=ids.job_revision_id,
        filter_set_id=ids.filter_set_id,
        draft_id=draft.draft_id,
        command_request_id=command.request_id,
        idempotency_key=command.idempotency_key,
        source_name=_required_text(draft.source_name, "SOURCE_REQUIRED"),
        source_path_label=_required_text(draft.source_path_label, "SOURCE_LABEL_REQUIRED"),
        targets=tuple(_sealed_target(target) for target in draft.targets),
        defaults=draft.defaults,
    )


def _sealed_target(target: DraftTarget) -> SealedStandardBackupTarget:
    return SealedStandardBackupTarget(
        name=target.name,
        path_label=target.path_label,
        independent_device_id=target.independent_device_id,
    )


def _missing_draft(draft_id: str) -> JobCreationReadiness:
    return JobCreationReadiness(
        draft_id=draft_id,
        draft_found=False,
        draft_valid=False,
        validation_codes=("DRAFT_NOT_FOUND",),
        next_action="Select source and targets before creating a backup job.",
    )


def _ready_for_creation(draft_id: str) -> JobCreationReadiness:
    return JobCreationReadiness(
        draft_id=draft_id,
        draft_found=True,
        draft_valid=True,
        validation_codes=(),
        next_action="Standard backup job revision is persisted; plan sealing is pending.",
    )


def _required_text(value: str | None, reason: str) -> str:
    if value is None or not value.strip():
        raise JobCreationPayloadError(reason)
    return value
