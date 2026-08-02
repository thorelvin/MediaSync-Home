from __future__ import annotations

import pytest

from mediasync_home.application.job_drafts import (
    AutomationPolicy,
    BackupBehavior,
    DraftTarget,
    DraftValidationCode,
    DraftValidationError,
    FileSelectionPreset,
    JobDraftStore,
    StandardBackupJobDraft,
    VerificationPreset,
)


class InMemoryJobDraftStore(JobDraftStore):
    def __init__(self) -> None:
        self._drafts: dict[str, StandardBackupJobDraft] = {}

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        self._drafts[draft.draft_id] = draft

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None:
        return self._drafts.get(draft_id)


def test_standard_backup_draft_starts_with_safe_defaults() -> None:
    draft = StandardBackupJobDraft.new("draft-1")

    assert draft.schema_version == 1
    assert draft.defaults.behavior is BackupBehavior.UPDATE_BACKUP
    assert draft.defaults.file_selection is FileSelectionPreset.ALL_USER_FILES
    assert draft.defaults.verification is VerificationPreset.STANDARD
    assert draft.defaults.automation_policy is AutomationPolicy.NEW_FILES_ONLY
    assert draft.can_create() is False
    assert [issue.code for issue in draft.validation_issues()] == [
        DraftValidationCode.SOURCE_REQUIRED,
        DraftValidationCode.SOURCE_LABEL_REQUIRED,
        DraftValidationCode.TARGET_REQUIRED,
    ]


def test_standard_backup_draft_can_be_created_after_source_and_target() -> None:
    draft = (
        StandardBackupJobDraft.new("draft-2")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )

    assert draft.can_create() is True
    assert draft.validation_issues() == ()
    assert draft.targets == (
        DraftTarget(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a"),
    )


def test_standard_backup_draft_reports_duplicate_and_blank_targets() -> None:
    draft = StandardBackupJobDraft(
        draft_id="draft-3",
        source_name="Pictures",
        source_path_label="C:/Users/Ada/Pictures",
        targets=(
            DraftTarget(name="USB 1", path_label="E:/Backup"),
            DraftTarget(name="usb 1", path_label="F:/Backup"),
            DraftTarget(name=" ", path_label=" "),
        ),
    )

    assert [issue.code for issue in draft.validation_issues()] == [
        DraftValidationCode.DUPLICATE_TARGET_NAME,
        DraftValidationCode.TARGET_NAME_REQUIRED,
        DraftValidationCode.TARGET_LABEL_REQUIRED,
    ]
    assert draft.can_create() is False


def test_standard_backup_draft_blocks_same_or_nested_source_target_roots() -> None:
    nested_target = (
        StandardBackupJobDraft.new("draft-overlap-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="Nested target", path_label="C:/Users/Ada/Pictures/Phone")
    )
    parent_target = (
        StandardBackupJobDraft.new("draft-overlap-b")
        .with_source(name="Pictures", path_label="file:///C:/Users/Ada/Pictures")
        .with_added_target(name="Parent target", path_label="C:\\Users\\Ada")
    )

    assert [issue.code for issue in nested_target.validation_issues()] == [
        DraftValidationCode.TARGET_ROOT_OVERLAPS_SOURCE,
    ]
    assert [issue.field for issue in nested_target.validation_issues()] == [
        "targets[0].path_label",
    ]
    assert nested_target.can_create() is False
    assert [issue.code for issue in parent_target.validation_issues()] == [
        DraftValidationCode.TARGET_ROOT_OVERLAPS_SOURCE,
    ]
    assert parent_target.can_create() is False


def test_standard_backup_draft_blocks_overlapping_target_roots() -> None:
    draft = (
        StandardBackupJobDraft.new("draft-overlap-c")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="//nas/backup/Ada")
        .with_added_target(name="USB 2", path_label="\\\\NAS\\Backup\\Ada\\Pictures")
    )

    assert [issue.code for issue in draft.validation_issues()] == [
        DraftValidationCode.TARGET_ROOT_OVERLAPS_TARGET,
    ]
    assert [issue.field for issue in draft.validation_issues()] == [
        "targets[1].path_label",
    ]
    assert draft.can_create() is False


def test_standard_backup_draft_rejects_more_than_three_targets() -> None:
    draft = (
        StandardBackupJobDraft.new("draft-4")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
        .with_added_target(name="USB 2", path_label="F:/Backup")
        .with_added_target(name="NAS", path_label="//nas/backup")
    )

    with pytest.raises(DraftValidationError, match=DraftValidationCode.TOO_MANY_TARGETS.value):
        draft.with_added_target(name="Archive", path_label="G:/Backup")


def test_job_draft_store_port_supports_local_roundtrip() -> None:
    store = InMemoryJobDraftStore()
    draft = (
        StandardBackupJobDraft.new("draft-5")
        .with_source(name="Documents", path_label="C:/Users/Ada/Documents")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )

    store.save_standard_backup_draft(draft)

    assert store.load_standard_backup_draft("draft-5") == draft
    assert store.load_standard_backup_draft("missing") is None
