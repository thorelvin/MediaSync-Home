from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from mediasync_home.application.job_drafts import (
    AutomationPolicy,
    BackupBehavior,
    DraftValidationError,
    DraftTarget,
    ExtraFilesPreset,
    FileSelectionPreset,
    JobDraftStore,
    PerformancePreset,
    RetentionPreset,
    StandardBackupDefaults,
    StandardBackupJobDraft,
    VerificationPreset,
    draft_path_labels_overlap,
)


class JobCreationCommandName(str, Enum):
    CREATE_STANDARD_BACKUP_JOB = "CREATE_STANDARD_BACKUP_JOB"


class JobCreationPayloadError(ValueError):
    pass


STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB = "STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB"


@dataclass(frozen=True)
class CreateStandardBackupJobCommand:
    request_id: str
    idempotency_key: str
    draft_id: str
    inline_draft: StandardBackupJobDraft | None = None
    autosave_draft_id: str | None = None


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
    filter_set_version: int = 1


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

    def list_active_standard_backup_jobs(self) -> tuple[SealedStandardBackupJob, ...]: ...

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
    inline_draft = parse_inline_standard_backup_draft(
        payload.get("draft"),
        draft_id=draft_id,
    )
    autosave_draft_id = payload.get("autosave_draft_id")
    if autosave_draft_id is not None:
        if (
            not isinstance(autosave_draft_id, str)
            or not autosave_draft_id.strip()
            or len(autosave_draft_id) > 128
        ):
            raise JobCreationPayloadError(
                "CREATE_STANDARD_BACKUP_JOB_AUTOSAVE_DRAFT_ID_INVALID"
            )
        autosave_draft_id = autosave_draft_id.strip()
        if autosave_draft_id == draft_id:
            raise JobCreationPayloadError(
                "CREATE_STANDARD_BACKUP_JOB_AUTOSAVE_DRAFT_ID_REUSED"
            )
    return CreateStandardBackupJobCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        draft_id=draft_id,
        inline_draft=inline_draft,
        autosave_draft_id=autosave_draft_id,
    )


def parse_inline_standard_backup_draft(
    payload: object,
    *,
    draft_id: str,
) -> StandardBackupJobDraft | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_DRAFT_INVALID")
    if payload.get("draft_id") != draft_id:
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_DRAFT_ID_MISMATCH")
    if payload.get("schema_version", 1) != 1:
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_DRAFT_SCHEMA_UNSUPPORTED")

    source_name = _required_payload_text(payload.get("source_name"), "SOURCE_REQUIRED")
    source_path_label = _required_payload_text(
        payload.get("source_path_label"),
        "SOURCE_LABEL_REQUIRED",
    )
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_TARGETS_INVALID")

    try:
        draft = StandardBackupJobDraft(
            draft_id=draft_id,
            source_name=source_name,
            source_path_label=source_path_label,
            defaults=_parse_inline_defaults(payload.get("defaults")),
        )
        for target_payload in targets:
            if not isinstance(target_payload, dict):
                raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_TARGET_INVALID")
            independent_device_id = target_payload.get("independent_device_id")
            if independent_device_id is not None:
                independent_device_id = _required_payload_text(
                    independent_device_id,
                    "TARGET_DEVICE_ID_INVALID",
                )
            draft = draft.with_added_target(
                name=_required_payload_text(
                    target_payload.get("name"),
                    "TARGET_NAME_REQUIRED",
                ),
                path_label=_required_payload_text(
                    target_payload.get("path_label"),
                    "TARGET_LABEL_REQUIRED",
                ),
                independent_device_id=independent_device_id,
            )
    except DraftValidationError as exc:
        raise JobCreationPayloadError(str(exc)) from exc
    return draft


def _parse_inline_defaults(payload: object) -> StandardBackupDefaults:
    if payload is None:
        return StandardBackupDefaults()
    if not isinstance(payload, dict):
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_DEFAULTS_INVALID")
    try:
        return StandardBackupDefaults(
            behavior=BackupBehavior(str(payload["behavior"])),
            file_selection=FileSelectionPreset(str(payload["file_selection"])),
            verification=VerificationPreset(str(payload["verification"])),
            retention=RetentionPreset(str(payload["retention"])),
            extra_files=ExtraFilesPreset(str(payload["extra_files"])),
            performance=PerformancePreset(str(payload["performance"])),
            automation_policy=AutomationPolicy(
                str(payload.get("automation_policy", AutomationPolicy.NEW_FILES_ONLY.value))
            ),
        )
    except (KeyError, ValueError) as exc:
        raise JobCreationPayloadError("CREATE_STANDARD_BACKUP_JOB_DEFAULTS_INVALID") from exc


def _required_payload_text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JobCreationPayloadError(reason)
    return value.strip()


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

    existing_overlap_codes = _existing_job_overlap_validation_codes(
        draft,
        catalog.list_active_standard_backup_jobs(),
    )
    if existing_overlap_codes:
        return JobCreationOutcome(
            created=False,
            idempotent_replay=False,
            readiness=JobCreationReadiness(
                draft_id=command.draft_id,
                draft_found=True,
                draft_valid=False,
                validation_codes=existing_overlap_codes,
                next_action="Choose source and target roots that do not overlap existing backup jobs.",
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


def _existing_job_overlap_validation_codes(
    draft: StandardBackupJobDraft,
    existing_jobs: tuple[SealedStandardBackupJob, ...],
) -> tuple[str, ...]:
    new_roots = _draft_job_roots(draft)
    for existing_job in existing_jobs:
        existing_roots = _sealed_job_roots(existing_job)
        for new_root in new_roots:
            for existing_root in existing_roots:
                if not new_root[1] and not existing_root[1]:
                    continue
                if draft_path_labels_overlap(new_root[0], existing_root[0]):
                    return (STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB,)
    return ()


def _draft_job_roots(draft: StandardBackupJobDraft) -> tuple[tuple[str, bool], ...]:
    roots: list[tuple[str, bool]] = []
    if draft.source_path_label is not None:
        roots.append((draft.source_path_label, False))
    roots.extend((target.path_label, True) for target in draft.targets)
    return tuple(roots)


def _sealed_job_roots(job: SealedStandardBackupJob) -> tuple[tuple[str, bool], ...]:
    roots: list[tuple[str, bool]] = [(job.source_path_label, False)]
    roots.extend((target.path_label, True) for target in job.targets)
    return tuple(roots)


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
