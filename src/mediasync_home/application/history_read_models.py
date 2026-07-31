from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


DEFAULT_HISTORY_TIMELINE_LIMIT = 10
MAX_HISTORY_TIMELINE_LIMIT = 25
MAX_HISTORY_FILTER_ID_LENGTH = 256


class HistoryTimelineQueryError(ValueError):
    pass


class HistoryActivityKind(str, Enum):
    CONTROL = "CONTROL"
    BACKUP = "BACKUP"


class HistoryActivityFilter(str, Enum):
    ALL = "ALL"
    CONTROLS = "CONTROLS"
    BACKUPS = "BACKUPS"


@dataclass(frozen=True)
class HistoryTargetSummary:
    endpoint_id: str
    endpoint_revision_id: str
    state: str
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "endpoint_id": self.endpoint_id,
            "endpoint_revision_id": self.endpoint_revision_id,
            "state": self.state,
            "planned_operations": self.planned_operations,
            "completed_operations": self.completed_operations,
            "planned_bytes": self.planned_bytes,
            "completed_bytes": self.completed_bytes,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
        }


@dataclass(frozen=True)
class HistoryActivitySummary:
    activity_id: str
    activity_kind: HistoryActivityKind
    job_id: str
    job_revision_id: str
    job_title: str
    state: str
    started_utc: str
    finished_utc: str | None
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int
    trigger_type: str
    targets: tuple[HistoryTargetSummary, ...]
    run_id: str | None = None
    analysis_id: str | None = None
    plan_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "activity_id": self.activity_id,
            "activity_kind": self.activity_kind.value,
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "job_title": self.job_title,
            "run_id": self.run_id,
            "analysis_id": self.analysis_id,
            "plan_id": self.plan_id,
            "state": self.state,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "planned_operations": self.planned_operations,
            "completed_operations": self.completed_operations,
            "planned_bytes": self.planned_bytes,
            "completed_bytes": self.completed_bytes,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "trigger_type": self.trigger_type,
            "targets": [target.to_dict() for target in self.targets],
        }


@dataclass(frozen=True)
class HistoryTimelinePage:
    limit: int
    offset: int
    has_more: bool
    read_model_available: bool
    activity_filter: HistoryActivityFilter
    job_id: str | None
    activities: tuple[HistoryActivitySummary, ...] = ()

    @classmethod
    def unavailable(
        cls,
        *,
        limit: int,
        offset: int,
        activity_filter: HistoryActivityFilter,
        job_id: str | None,
    ) -> "HistoryTimelinePage":
        return cls(
            limit=limit,
            offset=offset,
            has_more=False,
            read_model_available=False,
            activity_filter=activity_filter,
            job_id=job_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "activity_filter": self.activity_filter.value,
            "job_id": self.job_id,
            "activities": [activity.to_dict() for activity in self.activities],
        }


class HistoryTimelineReadModelStore(Protocol):
    def list_recent_history_activities(
        self,
        *,
        limit: int,
        offset: int,
        activity_filter: HistoryActivityFilter,
        job_id: str | None,
    ) -> tuple[HistoryActivitySummary, ...]: ...


def query_history_timeline(
    *,
    history_store: HistoryTimelineReadModelStore | None,
    limit: int | None = None,
    offset: int | None = None,
    activity_filter: str | None = None,
    job_id: str | None = None,
) -> HistoryTimelinePage:
    page_limit, page_offset = _normalize_bounds(limit=limit, offset=offset)
    normalized_filter = _normalize_activity_filter(activity_filter)
    normalized_job_id = _normalize_job_id(job_id)
    if history_store is None:
        return HistoryTimelinePage.unavailable(
            limit=page_limit,
            offset=page_offset,
            activity_filter=normalized_filter,
            job_id=normalized_job_id,
        )
    rows = history_store.list_recent_history_activities(
        limit=page_limit + 1,
        offset=page_offset,
        activity_filter=normalized_filter,
        job_id=normalized_job_id,
    )
    return HistoryTimelinePage(
        limit=page_limit,
        offset=page_offset,
        has_more=len(rows) > page_limit,
        read_model_available=True,
        activity_filter=normalized_filter,
        job_id=normalized_job_id,
        activities=rows[:page_limit],
    )


def _normalize_bounds(*, limit: int | None, offset: int | None) -> tuple[int, int]:
    page_limit = DEFAULT_HISTORY_TIMELINE_LIMIT if limit is None else int(limit)
    page_offset = 0 if offset is None else int(offset)
    if page_limit < 1 or page_limit > MAX_HISTORY_TIMELINE_LIMIT:
        raise HistoryTimelineQueryError("HISTORY_TIMELINE_LIMIT_OUT_OF_RANGE")
    if page_offset < 0:
        raise HistoryTimelineQueryError("HISTORY_TIMELINE_OFFSET_OUT_OF_RANGE")
    return page_limit, page_offset


def _normalize_activity_filter(value: str | None) -> HistoryActivityFilter:
    if value is None:
        return HistoryActivityFilter.ALL
    try:
        return HistoryActivityFilter(value.strip().upper())
    except ValueError as exc:
        raise HistoryTimelineQueryError("HISTORY_TIMELINE_FILTER_INVALID") from exc


def _normalize_job_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_HISTORY_FILTER_ID_LENGTH:
        raise HistoryTimelineQueryError("HISTORY_TIMELINE_JOB_FILTER_INVALID")
    return normalized
