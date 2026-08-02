from __future__ import annotations

import pytest

from mediasync_home.application.job_draft_saving import (
    JobDraftPayloadError,
    parse_save_standard_backup_draft_command,
    save_standard_backup_draft,
)
from mediasync_home.application.job_drafts import StandardBackupJobDraft


class _MemoryDraftStore:
    def __init__(self) -> None:
        self.saved: StandardBackupJobDraft | None = None

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        self.saved = draft

    def load_standard_backup_draft(
        self,
        draft_id: str,
    ) -> StandardBackupJobDraft | None:
        if self.saved is None or self.saved.draft_id != draft_id:
            return None
        return self.saved


def test_save_draft_command_accepts_incomplete_target_selection() -> None:
    command = parse_save_standard_backup_draft_command(
        request_id="request-a",
        idempotency_key="save-a",
        payload={
            "draft_id": "setup-autosave",
            "draft": {
                "draft_id": "setup-autosave",
                "schema_version": 1,
                "source_name": "Pictures",
                "source_path_label": "C:/Users/Ada/Pictures",
                "targets": [],
                "defaults": {
                    "behavior": "UPDATE_BACKUP",
                    "file_selection": "ALL_USER_FILES",
                    "verification": "STANDARD",
                    "retention": "THIRTY_DAYS",
                    "extra_files": "KEEP_ON_TARGET",
                    "performance": "AUTO",
                },
            },
        },
    )
    store = _MemoryDraftStore()

    saved = save_standard_backup_draft(command=command, drafts=store)

    assert saved.source_path_label == "C:/Users/Ada/Pictures"
    assert saved.targets == ()
    assert store.saved == saved


def test_save_draft_command_accepts_explicit_empty_reset() -> None:
    command = parse_save_standard_backup_draft_command(
        request_id="request-a",
        idempotency_key="save-a",
        payload={
            "draft_id": "setup-autosave",
            "draft": {
                "draft_id": "setup-autosave",
                "schema_version": 1,
                "source_name": None,
                "source_path_label": None,
                "targets": [],
                "defaults": {
                    "behavior": "UPDATE_BACKUP",
                    "file_selection": "ALL_USER_FILES",
                    "verification": "STANDARD",
                    "retention": "THIRTY_DAYS",
                    "extra_files": "KEEP_ON_TARGET",
                    "performance": "AUTO",
                },
            },
        },
    )

    assert command.draft == StandardBackupJobDraft.new("setup-autosave")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"draft_id": "setup-autosave", "draft": None},
        {
            "draft_id": "setup-autosave",
            "draft": {
                "draft_id": "different",
                "source_name": None,
                "source_path_label": None,
                "targets": [],
            },
        },
        {
            "draft_id": "setup-autosave",
            "draft": {
                "draft_id": "setup-autosave",
                "schema_version": 2,
                "source_name": None,
                "source_path_label": None,
                "targets": [],
            },
        },
        {
            "draft_id": "setup-autosave",
            "draft": {
                "draft_id": "setup-autosave",
                "source_name": None,
                "source_path_label": "C:/Pictures",
                "targets": [],
            },
        },
    ],
)
def test_save_draft_command_rejects_malformed_partial_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(JobDraftPayloadError):
        parse_save_standard_backup_draft_command(
            request_id="request-a",
            idempotency_key="save-a",
            payload=payload,
        )
