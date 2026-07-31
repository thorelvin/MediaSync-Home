from __future__ import annotations

import pytest

from mediasync_home.application.history_read_models import (
    HistoryActivityFilter,
    HistoryActivityKind,
    HistoryActivitySummary,
    HistoryTargetSummary,
    HistoryTimelineQueryError,
    HistoryTimelineReadModelStore,
    query_history_timeline,
)


class _HistoryStore(HistoryTimelineReadModelStore):
    def __init__(self, rows: tuple[HistoryActivitySummary, ...]) -> None:
        self.rows = rows
        self.calls: list[dict[str, object]] = []

    def list_recent_history_activities(
        self,
        *,
        limit: int,
        offset: int,
        activity_filter: HistoryActivityFilter,
        job_id: str | None,
    ) -> tuple[HistoryActivitySummary, ...]:
        self.calls.append(
            {
                "limit": limit,
                "offset": offset,
                "activity_filter": activity_filter,
                "job_id": job_id,
            }
        )
        rows = tuple(
            row
            for row in self.rows
            if (
                activity_filter is HistoryActivityFilter.ALL
                or (
                    activity_filter is HistoryActivityFilter.CONTROLS
                    and row.activity_kind is HistoryActivityKind.CONTROL
                )
                or (
                    activity_filter is HistoryActivityFilter.BACKUPS
                    and row.activity_kind is HistoryActivityKind.BACKUP
                )
            )
            and (job_id is None or row.job_id == job_id)
        )
        return rows[offset : offset + limit]


def test_history_timeline_returns_bounded_filtered_page() -> None:
    store = _HistoryStore(
        (
            _activity("run-b", HistoryActivityKind.BACKUP, job_id="job-b"),
            _activity("analysis-a", HistoryActivityKind.CONTROL, job_id="job-a"),
        )
    )

    page = query_history_timeline(
        history_store=store,
        limit=1,
        offset=0,
        activity_filter=" controls ",
        job_id=" job-a ",
    )

    assert store.calls == [
        {
            "limit": 2,
            "offset": 0,
            "activity_filter": HistoryActivityFilter.CONTROLS,
            "job_id": "job-a",
        }
    ]
    assert page.read_model_available is True
    assert page.has_more is False
    assert [row.activity_id for row in page.activities] == ["analysis-a"]
    assert page.to_dict()["activity_filter"] == "CONTROLS"


def test_history_timeline_reports_unavailable_without_store() -> None:
    page = query_history_timeline(
        history_store=None,
        limit=5,
        offset=25,
        activity_filter="BACKUPS",
    )

    assert page.to_dict() == {
        "limit": 5,
        "offset": 25,
        "has_more": False,
        "read_model_available": False,
        "activity_filter": "BACKUPS",
        "job_id": None,
        "activities": [],
    }


@pytest.mark.parametrize(
    ("limit", "offset", "activity_filter", "job_id"),
    (
        (0, 0, "ALL", None),
        (26, 0, "ALL", None),
        (1, -1, "ALL", None),
        (1, 0, "RUNS", None),
        (1, 0, "ALL", " "),
    ),
)
def test_history_timeline_rejects_invalid_query(
    limit: int,
    offset: int,
    activity_filter: str,
    job_id: str | None,
) -> None:
    with pytest.raises(HistoryTimelineQueryError):
        query_history_timeline(
            history_store=None,
            limit=limit,
            offset=offset,
            activity_filter=activity_filter,
            job_id=job_id,
        )


def _activity(
    activity_id: str,
    kind: HistoryActivityKind,
    *,
    job_id: str,
) -> HistoryActivitySummary:
    target = HistoryTargetSummary(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state="SUCCEEDED",
        planned_operations=1,
        completed_operations=1,
        planned_bytes=128,
        completed_bytes=128,
        warning_count=0,
        error_count=0,
    )
    return HistoryActivitySummary(
        activity_id=activity_id,
        activity_kind=kind,
        job_id=job_id,
        job_revision_id=f"{job_id}-rev",
        job_title=job_id,
        state="COMPLETED" if kind is HistoryActivityKind.BACKUP else "SEALED",
        started_utc="2026-07-20T12:00:00.000Z",
        finished_utc="2026-07-20T12:01:00.000Z",
        planned_operations=1,
        completed_operations=1,
        planned_bytes=128,
        completed_bytes=128,
        warning_count=0,
        error_count=0,
        trigger_type="MANUAL_LOCAL_PREVIEW",
        targets=(target,),
        run_id=activity_id if kind is HistoryActivityKind.BACKUP else None,
        analysis_id=activity_id if kind is HistoryActivityKind.CONTROL else None,
    )
