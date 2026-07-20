from __future__ import annotations

import pytest

from mediasync_home.application.job_drafts import JobDraftStore, StandardBackupJobDraft
from mediasync_home.application.job_read_models import (
    BackupOverviewQueryError,
    StandardBackupJobReadModelStore,
    StandardBackupJobSummary,
    StandardBackupTargetSummary,
    query_backup_overview,
)


class _Drafts(JobDraftStore):
    def __init__(self, draft: StandardBackupJobDraft | None = None) -> None:
        self._draft = draft

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        self._draft = draft

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None:
        if self._draft is not None and self._draft.draft_id == draft_id:
            return self._draft
        return None


class _ReadStore(StandardBackupJobReadModelStore):
    def __init__(self, jobs: tuple[StandardBackupJobSummary, ...]) -> None:
        self.jobs = jobs
        self.calls: list[dict[str, int]] = []

    def list_active_standard_backup_job_summaries(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[StandardBackupJobSummary, ...]:
        self.calls.append({"limit": limit, "offset": offset})
        return self.jobs[offset : offset + limit]


def test_backup_overview_query_returns_bounded_page_and_requested_draft() -> None:
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )
    read_store = _ReadStore((_job("job-a"), _job("job-b")))

    page = query_backup_overview(
        job_read_store=read_store,
        draft_store=_Drafts(draft),
        draft_id="draft-a",
        limit=1,
        offset=0,
    )

    payload = page.to_dict()
    assert read_store.calls == [{"limit": 2, "offset": 0}]
    assert payload["has_more"] is True
    assert payload["read_model_available"] is True
    assert payload["requested_draft_id"] == "draft-a"
    assert payload["draft"]["can_create"] is True  # type: ignore[index]
    assert [job["job_id"] for job in payload["jobs"]] == ["job-a"]  # type: ignore[index]


def test_backup_overview_query_reports_unavailable_read_model_without_store() -> None:
    page = query_backup_overview(job_read_store=None, limit=5, offset=0)

    assert page.to_dict() == {
        "limit": 5,
        "offset": 0,
        "has_more": False,
        "read_model_available": False,
        "requested_draft_id": None,
        "draft": None,
        "jobs": [],
    }


def test_backup_overview_query_rejects_unbounded_limits() -> None:
    with pytest.raises(BackupOverviewQueryError):
        query_backup_overview(job_read_store=None, limit=26, offset=0)
    with pytest.raises(BackupOverviewQueryError):
        query_backup_overview(job_read_store=None, limit=1, offset=-1)


def _job(job_id: str) -> StandardBackupJobSummary:
    return StandardBackupJobSummary(
        job_id=job_id,
        job_revision_id=f"{job_id}-rev",
        filter_set_id=f"{job_id}-filter",
        source_name=f"{job_id} source",
        source_path_label=f"C:/Data/{job_id}",
        targets=(
            StandardBackupTargetSummary(
                name="USB",
                path_label=f"E:/Backup/{job_id}",
                independent_device_id="disk-a",
            ),
        ),
    )
