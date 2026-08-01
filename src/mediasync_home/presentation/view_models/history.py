from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class HistoryTargetViewState:
    endpoint_id: str
    state: str
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int


@dataclass(frozen=True)
class HistoryActivityViewState:
    activity_id: str
    activity_kind: str
    job_id: str
    job_title: str
    job_revision_id: str
    state: str
    started_utc: str
    finished_utc: str | None
    duration_seconds: int | None
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int
    trigger_type: str
    targets: tuple[HistoryTargetViewState, ...]
    run_id: str | None = None
    analysis_id: str | None = None
    plan_id: str | None = None

    @property
    def selection_key(self) -> str:
        return f"{self.activity_kind}:{self.activity_id}"


@dataclass(frozen=True)
class HistoryTimelineViewState:
    read_model_available: bool
    has_more: bool
    activity_filter: str
    job_id: str | None
    activities: tuple[HistoryActivityViewState, ...]
    limit: int = 10
    offset: int = 0
    next_cursor: dict[str, object] | None = None
    keyset_paging_available: bool = False
    selected_activity_id: str | None = None


def empty_history_timeline_state() -> HistoryTimelineViewState:
    return HistoryTimelineViewState(
        read_model_available=False,
        has_more=False,
        activity_filter="ALL",
        job_id=None,
        activities=(),
    )


def history_timeline_from_response(
    response: IpcResponse | None,
) -> HistoryTimelineViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_history_timeline_state()
    timeline = response.payload.get("history_timeline")
    if not isinstance(timeline, dict):
        return empty_history_timeline_state()
    activity_filter = _history_filter(timeline.get("activity_filter"))
    job_id = _optional_text(timeline.get("job_id"))
    keyset_paging_available = "next_cursor" in timeline
    next_cursor = _history_cursor(timeline.get("next_cursor"))
    if not bool(timeline.get("read_model_available", False)):
        return HistoryTimelineViewState(
            read_model_available=False,
            has_more=False,
            activity_filter=activity_filter,
            job_id=job_id,
            activities=(),
            limit=_positive_int(timeline.get("limit")) or 10,
            offset=_non_negative_int(timeline.get("offset")) or 0,
            keyset_paging_available=keyset_paging_available,
        )
    payloads = timeline.get("activities")
    activities = tuple(
        activity
        for payload in payloads
        if isinstance(payload, dict)
        and (activity := _activity_from_payload(payload)) is not None
    ) if isinstance(payloads, list) else ()
    has_more = bool(timeline.get("has_more", False))
    if keyset_paging_available and next_cursor is None:
        has_more = False
    return HistoryTimelineViewState(
        read_model_available=True,
        has_more=has_more,
        activity_filter=activity_filter,
        job_id=job_id,
        activities=activities,
        limit=_positive_int(timeline.get("limit")) or 10,
        offset=_non_negative_int(timeline.get("offset")) or 0,
        next_cursor=next_cursor,
        keyset_paging_available=keyset_paging_available,
        selected_activity_id=activities[0].selection_key if activities else None,
    )


def _activity_from_payload(
    payload: dict[object, object],
) -> HistoryActivityViewState | None:
    activity_id = _required_text(payload.get("activity_id"))
    activity_kind = _required_text(payload.get("activity_kind"))
    job_id = _required_text(payload.get("job_id"))
    job_title = _required_text(payload.get("job_title"))
    job_revision_id = _required_text(payload.get("job_revision_id"))
    state = _required_text(payload.get("state"))
    started_utc = _required_text(payload.get("started_utc"))
    trigger_type = _required_text(payload.get("trigger_type"))
    if (
        activity_id is None
        or activity_kind not in {"CONTROL", "BACKUP"}
        or job_id is None
        or job_title is None
        or job_revision_id is None
        or state is None
        or started_utc is None
        or trigger_type is None
    ):
        return None
    targets_payload = payload.get("targets")
    targets = tuple(
        target
        for target_payload in targets_payload
        if isinstance(target_payload, dict)
        and (target := _target_from_payload(target_payload)) is not None
    ) if isinstance(targets_payload, list) else ()
    finished_utc = _optional_text(payload.get("finished_utc"))
    return HistoryActivityViewState(
        activity_id=activity_id,
        activity_kind=activity_kind,
        job_id=job_id,
        job_title=job_title,
        job_revision_id=job_revision_id,
        run_id=_optional_text(payload.get("run_id")),
        analysis_id=_optional_text(payload.get("analysis_id")),
        plan_id=_optional_text(payload.get("plan_id")),
        state=state,
        started_utc=started_utc,
        finished_utc=finished_utc,
        duration_seconds=_duration_seconds(started_utc, finished_utc),
        planned_operations=_non_negative_int(payload.get("planned_operations")) or 0,
        completed_operations=_non_negative_int(payload.get("completed_operations")) or 0,
        planned_bytes=_non_negative_int(payload.get("planned_bytes")) or 0,
        completed_bytes=_non_negative_int(payload.get("completed_bytes")) or 0,
        warning_count=_non_negative_int(payload.get("warning_count")) or 0,
        error_count=_non_negative_int(payload.get("error_count")) or 0,
        trigger_type=trigger_type,
        targets=targets,
    )


def _target_from_payload(
    payload: dict[object, object],
) -> HistoryTargetViewState | None:
    endpoint_id = _required_text(payload.get("endpoint_id"))
    state = _required_text(payload.get("state"))
    if endpoint_id is None or state is None:
        return None
    return HistoryTargetViewState(
        endpoint_id=endpoint_id,
        state=state,
        planned_operations=_non_negative_int(payload.get("planned_operations")) or 0,
        completed_operations=_non_negative_int(payload.get("completed_operations")) or 0,
        planned_bytes=_non_negative_int(payload.get("planned_bytes")) or 0,
        completed_bytes=_non_negative_int(payload.get("completed_bytes")) or 0,
        warning_count=_non_negative_int(payload.get("warning_count")) or 0,
        error_count=_non_negative_int(payload.get("error_count")) or 0,
    )


def _duration_seconds(started_utc: str, finished_utc: str | None) -> int | None:
    if finished_utc is None:
        return None
    try:
        started = datetime.fromisoformat(started_utc.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((finished - started).total_seconds()))


def _history_filter(value: object) -> str:
    normalized = _required_text(value)
    return normalized if normalized in {"ALL", "CONTROLS", "BACKUPS"} else "ALL"


def _history_cursor(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if set(value) != {
        "cursor_version",
        "started_utc",
        "activity_kind",
        "activity_id",
    }:
        return None
    version = value.get("cursor_version")
    started_utc = _required_text(value.get("started_utc"))
    activity_kind = _required_text(value.get("activity_kind"))
    activity_id = _required_text(value.get("activity_id"))
    if (
        isinstance(version, bool)
        or version != 1
        or started_utc is None
        or activity_kind not in {"CONTROL", "BACKUP"}
        or activity_id is None
    ):
        return None
    return {
        "cursor_version": 1,
        "started_utc": started_utc,
        "activity_kind": activity_kind,
        "activity_id": activity_id,
    }


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_text(value: object) -> str | None:
    return _required_text(value)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
