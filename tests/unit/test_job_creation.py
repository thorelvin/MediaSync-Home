from __future__ import annotations

import pytest

from mediasync_home.application.job_creation import (
    JobCreationPayloadError,
    evaluate_standard_backup_job_creation,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_drafts import JobDraftStore, StandardBackupJobDraft


class InMemoryJobDraftStore(JobDraftStore):
    def __init__(self) -> None:
        self._drafts: dict[str, StandardBackupJobDraft] = {}

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        self._drafts[draft.draft_id] = draft

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None:
        return self._drafts.get(draft_id)


def test_parse_create_standard_backup_job_command_requires_draft_id() -> None:
    with pytest.raises(JobCreationPayloadError, match="CREATE_STANDARD_BACKUP_JOB_REQUIRES_DRAFT_ID"):
        parse_create_standard_backup_job_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={},
        )


def test_job_creation_readiness_reports_missing_draft() -> None:
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-missing"},
    )

    readiness = evaluate_standard_backup_job_creation(
        command=command,
        drafts=InMemoryJobDraftStore(),
    )

    assert readiness.draft_found is False
    assert readiness.draft_valid is False
    assert readiness.validation_codes == ("DRAFT_NOT_FOUND",)


def test_job_creation_readiness_reports_incomplete_draft() -> None:
    store = InMemoryJobDraftStore()
    store.save_standard_backup_draft(StandardBackupJobDraft.new("draft-a"))
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-a"},
    )

    readiness = evaluate_standard_backup_job_creation(command=command, drafts=store)

    assert readiness.draft_found is True
    assert readiness.draft_valid is False
    assert readiness.validation_codes == (
        "SOURCE_REQUIRED",
        "SOURCE_LABEL_REQUIRED",
        "TARGET_REQUIRED",
    )


def test_job_creation_readiness_accepts_complete_draft_but_keeps_creation_disabled() -> None:
    store = InMemoryJobDraftStore()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    store.save_standard_backup_draft(draft)
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-a"},
    )

    readiness = evaluate_standard_backup_job_creation(command=command, drafts=store)

    assert readiness.draft_found is True
    assert readiness.draft_valid is True
    assert readiness.validation_codes == ()
    assert readiness.next_action == "Backup job creation is recognized but disabled in the 0B local preview."
