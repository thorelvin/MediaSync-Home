from __future__ import annotations

import pytest

from mediasync_home.application.activity_read_models import (
    ActivityOverviewQueryError,
    RunActivityReadModelStore,
    RunActivitySummary,
    RunTargetActivitySummary,
    query_activity_overview,
)
from mediasync_home.application.runs import RunState, RunTargetState, RunTriggerType


class _ReadStore(RunActivityReadModelStore):
    def __init__(self, runs: tuple[RunActivitySummary, ...]) -> None:
        self.runs = runs
        self.calls: list[dict[str, int | str | None]] = []

    def list_recent_run_activity_summaries(
        self,
        *,
        limit: int,
        offset: int,
        job_id: str | None = None,
    ) -> tuple[RunActivitySummary, ...]:
        self.calls.append({"limit": limit, "offset": offset, "job_id": job_id})
        filtered = tuple(run for run in self.runs if job_id is None or run.job_id == job_id)
        return filtered[offset : offset + limit]


def test_activity_overview_query_returns_bounded_recent_runs() -> None:
    read_store = _ReadStore((_run("run-a", job_id="job-a"), _run("run-b", job_id="job-b")))

    page = query_activity_overview(
        run_read_store=read_store,
        limit=1,
        offset=0,
        job_id=" job-a ",
    )

    assert read_store.calls == [{"limit": 2, "offset": 0, "job_id": "job-a"}]
    assert page.has_more is False
    assert page.read_model_available is True
    assert page.job_id == "job-a"
    assert [run["run_id"] for run in page.to_dict()["runs"]] == ["run-a"]  # type: ignore[index]
    assert page.to_dict()["runs"][0]["targets"][0]["last_success_utc"] == (  # type: ignore[index]
        "2026-07-19T12:05:00.000Z"
    )


def test_activity_overview_query_reports_unavailable_without_store() -> None:
    page = query_activity_overview(run_read_store=None, limit=5, offset=0)

    assert page.to_dict() == {
        "limit": 5,
        "offset": 0,
        "has_more": False,
        "read_model_available": False,
        "job_id": None,
        "runs": [],
    }


def test_activity_overview_query_rejects_invalid_bounds_and_filters() -> None:
    with pytest.raises(ActivityOverviewQueryError):
        query_activity_overview(run_read_store=None, limit=26, offset=0)
    with pytest.raises(ActivityOverviewQueryError):
        query_activity_overview(run_read_store=None, limit=1, offset=-1)
    with pytest.raises(ActivityOverviewQueryError):
        query_activity_overview(run_read_store=None, job_id=" ")


def _run(run_id: str, *, job_id: str) -> RunActivitySummary:
    return RunActivitySummary(
        run_id=run_id,
        job_id=job_id,
        job_revision_id=f"{job_id}-rev",
        plan_id=f"{job_id}-plan",
        state=RunState.QUEUED,
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        started_utc="2026-07-20T12:00:00.000Z",
        finished_utc=None,
        planned_operations=1,
        planned_bytes=128,
        warning_count=0,
        error_count=0,
        targets=(
            RunTargetActivitySummary(
                run_target_id=f"{run_id}-target-0000",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                state=RunTargetState.PENDING,
                planned_operations=1,
                completed_operations=0,
                planned_bytes=128,
                completed_bytes=0,
                last_success_utc="2026-07-19T12:05:00.000Z",
            ),
        ),
    )
