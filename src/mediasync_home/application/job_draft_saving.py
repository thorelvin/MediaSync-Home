from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from mediasync_home.application.job_creation import (
    JobCreationPayloadError,
    parse_inline_standard_backup_draft,
)
from mediasync_home.application.job_drafts import (
    JobDraftStore,
    StandardBackupJobDraft,
)


class JobDraftCommandName(str, Enum):
    SAVE_STANDARD_BACKUP_DRAFT = "SAVE_STANDARD_BACKUP_DRAFT"


class JobDraftPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class SaveStandardBackupDraftCommand:
    request_id: str
    idempotency_key: str
    draft: StandardBackupJobDraft


def parse_save_standard_backup_draft_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> SaveStandardBackupDraftCommand:
    if set(payload) != {"draft_id", "draft"}:
        raise JobDraftPayloadError("SAVE_STANDARD_BACKUP_DRAFT_PAYLOAD_INVALID")
    draft_id = payload.get("draft_id")
    if (
        not isinstance(draft_id, str)
        or not draft_id.strip()
        or len(draft_id) > 128
    ):
        raise JobDraftPayloadError("SAVE_STANDARD_BACKUP_DRAFT_ID_INVALID")
    normalized_draft_id = draft_id.strip()
    raw_draft = payload.get("draft")
    if not isinstance(raw_draft, dict):
        raise JobDraftPayloadError("SAVE_STANDARD_BACKUP_DRAFT_INVALID")
    if raw_draft.get("draft_id") != normalized_draft_id:
        raise JobDraftPayloadError("SAVE_STANDARD_BACKUP_DRAFT_ID_MISMATCH")
    if raw_draft.get("schema_version", 1) != 1:
        raise JobDraftPayloadError("SAVE_STANDARD_BACKUP_DRAFT_SCHEMA_UNSUPPORTED")

    source_name = raw_draft.get("source_name")
    source_path_label = raw_draft.get("source_path_label")
    targets = raw_draft.get("targets")
    if source_name is None and source_path_label is None and targets == []:
        draft = StandardBackupJobDraft.new(normalized_draft_id)
    else:
        try:
            parsed_draft = parse_inline_standard_backup_draft(
                raw_draft,
                draft_id=normalized_draft_id,
            )
        except JobCreationPayloadError as exc:
            raise JobDraftPayloadError(str(exc)) from exc
        if parsed_draft is None:
            raise JobDraftPayloadError("SAVE_STANDARD_BACKUP_DRAFT_INVALID")
        draft = parsed_draft
    return SaveStandardBackupDraftCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        draft=draft,
    )


def save_standard_backup_draft(
    *,
    command: SaveStandardBackupDraftCommand,
    drafts: JobDraftStore,
) -> StandardBackupJobDraft:
    drafts.save_standard_backup_draft(command.draft)
    return command.draft
