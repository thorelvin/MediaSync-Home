from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from mediasync_home.application.job_drafts import JobDraftStore


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
