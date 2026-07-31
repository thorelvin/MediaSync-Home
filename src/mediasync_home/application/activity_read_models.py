from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.runs import RunState, RunTargetState, RunTriggerType


DEFAULT_ACTIVITY_OVERVIEW_LIMIT = 10
MAX_ACTIVITY_OVERVIEW_LIMIT = 25


class ActivityOverviewQueryError(ValueError):
    pass


@dataclass(frozen=True)
class RunTargetActivitySummary:
    run_target_id: str
    endpoint_id: str
    endpoint_revision_id: str
    state: RunTargetState
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int = 0
    error_count: int = 0
    last_success_utc: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_target_id": self.run_target_id,
            "endpoint_id": self.endpoint_id,
            "endpoint_revision_id": self.endpoint_revision_id,
            "state": self.state.value,
            "planned_operations": self.planned_operations,
            "completed_operations": self.completed_operations,
            "planned_bytes": self.planned_bytes,
            "completed_bytes": self.completed_bytes,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "last_success_utc": self.last_success_utc,
        }


@dataclass(frozen=True)
class RunActivitySummary:
    run_id: str
    job_id: str
    job_revision_id: str
    plan_id: str
    state: RunState
    trigger_type: RunTriggerType
    started_utc: str
    finished_utc: str | None
    planned_operations: int
    planned_bytes: int
    warning_count: int
    error_count: int
    targets: tuple[RunTargetActivitySummary, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "plan_id": self.plan_id,
            "state": self.state.value,
            "trigger_type": self.trigger_type.value,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "planned_operations": self.planned_operations,
            "planned_bytes": self.planned_bytes,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "targets": [target.to_dict() for target in self.targets],
        }


@dataclass(frozen=True)
class ActivityOverviewPage:
    limit: int
    offset: int
    has_more: bool
    read_model_available: bool
    runs: tuple[RunActivitySummary, ...] = ()
    job_id: str | None = None

    @classmethod
    def unavailable(cls, *, limit: int, offset: int, job_id: str | None) -> "ActivityOverviewPage":
        return cls(
            limit=limit,
            offset=offset,
            has_more=False,
            read_model_available=False,
            job_id=job_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "job_id": self.job_id,
            "runs": [run.to_dict() for run in self.runs],
        }


class RunActivityReadModelStore(Protocol):
    def list_recent_run_activity_summaries(
        self,
        *,
        limit: int,
        offset: int,
        job_id: str | None = None,
    ) -> tuple[RunActivitySummary, ...]: ...


def query_activity_overview(
    *,
    run_read_store: RunActivityReadModelStore | None,
    limit: int | None = None,
    offset: int | None = None,
    job_id: str | None = None,
) -> ActivityOverviewPage:
    page_limit, page_offset = normalize_activity_overview_bounds(limit=limit, offset=offset)
    normalized_job_id = _normalized_optional_text(job_id)
    if run_read_store is None:
        return ActivityOverviewPage.unavailable(
            limit=page_limit,
            offset=page_offset,
            job_id=normalized_job_id,
        )

    rows = run_read_store.list_recent_run_activity_summaries(
        limit=page_limit + 1,
        offset=page_offset,
        job_id=normalized_job_id,
    )
    return ActivityOverviewPage(
        limit=page_limit,
        offset=page_offset,
        has_more=len(rows) > page_limit,
        read_model_available=True,
        runs=rows[:page_limit],
        job_id=normalized_job_id,
    )


def normalize_activity_overview_bounds(
    *,
    limit: int | None,
    offset: int | None,
) -> tuple[int, int]:
    page_limit = DEFAULT_ACTIVITY_OVERVIEW_LIMIT if limit is None else int(limit)
    page_offset = 0 if offset is None else int(offset)
    if page_limit < 1 or page_limit > MAX_ACTIVITY_OVERVIEW_LIMIT:
        raise ActivityOverviewQueryError("ACTIVITY_OVERVIEW_LIMIT_OUT_OF_RANGE")
    if page_offset < 0:
        raise ActivityOverviewQueryError("ACTIVITY_OVERVIEW_OFFSET_OUT_OF_RANGE")
    return page_limit, page_offset


def _normalized_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ActivityOverviewQueryError("ACTIVITY_OVERVIEW_FILTER_EMPTY")
    return normalized
