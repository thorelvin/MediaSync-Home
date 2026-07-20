from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class SnapshotHealthPreviewRow:
    snapshot_id: str
    severity_label: str
    display_line: str


@dataclass(frozen=True)
class SnapshotHealthPreviewState:
    snapshot_id: str | None
    title: str
    summary_label: str
    read_model_available: bool
    has_more_health_rows: bool
    rows: tuple[SnapshotHealthPreviewRow, ...]


def empty_snapshot_health_preview_state() -> SnapshotHealthPreviewState:
    return SnapshotHealthPreviewState(
        snapshot_id=None,
        title="Snapshot health",
        summary_label="No source snapshot to inspect.",
        read_model_available=False,
        has_more_health_rows=False,
        rows=(),
    )


def snapshot_health_preview_from_responses(
    *,
    snapshot_id: str | None,
    blocking_issues_response: IpcResponse | None,
    coverage_response: IpcResponse | None,
) -> SnapshotHealthPreviewState:
    normalized_snapshot_id = _required_text(snapshot_id)
    if normalized_snapshot_id is None:
        return empty_snapshot_health_preview_state()
    issue_page = _response_page(blocking_issues_response, "snapshot_issues")
    coverage_page = _response_page(coverage_response, "snapshot_coverage")
    if issue_page is None or coverage_page is None:
        return _unavailable(normalized_snapshot_id)
    if not bool(issue_page.get("read_model_available", False)):
        return _unavailable(normalized_snapshot_id)
    if not bool(coverage_page.get("read_model_available", False)):
        return _unavailable(normalized_snapshot_id)

    issue_rows = tuple(_issue_row(normalized_snapshot_id, issue) for issue in _dict_items(issue_page, "issues"))
    coverage_rows = tuple(
        _coverage_row(normalized_snapshot_id, coverage)
        for coverage in _dict_items(coverage_page, "coverage")
        if _required_text(coverage.get("coverage_state")) != "COMPLETE"
    )
    rows = (*issue_rows, *coverage_rows)
    has_more = bool(issue_page.get("has_more", False)) or bool(coverage_page.get("has_more", False))
    return SnapshotHealthPreviewState(
        snapshot_id=normalized_snapshot_id,
        title="Snapshot health",
        summary_label=_summary_label(
            snapshot_id=normalized_snapshot_id,
            blocking_issue_count=len(issue_rows),
            coverage_warning_count=len(coverage_rows),
            has_more=has_more,
        ),
        read_model_available=True,
        has_more_health_rows=has_more,
        rows=rows,
    )


def _unavailable(snapshot_id: str) -> SnapshotHealthPreviewState:
    return SnapshotHealthPreviewState(
        snapshot_id=snapshot_id,
        title="Snapshot health",
        summary_label="Snapshot health read model is not available.",
        read_model_available=False,
        has_more_health_rows=False,
        rows=(),
    )


def _response_page(response: IpcResponse | None, key: str) -> dict[object, object] | None:
    if response is None or response.status is IpcStatus.REJECTED:
        return None
    page = response.payload.get(key)
    return page if isinstance(page, dict) else None


def _dict_items(page: dict[object, object], key: str) -> tuple[dict[object, object], ...]:
    items = page.get(key)
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _issue_row(snapshot_id: str, payload: dict[object, object]) -> SnapshotHealthPreviewRow:
    relative_path = _required_text(payload.get("relative_path")) or "."
    issue_type = _required_text(payload.get("issue_type")) or "ISSUE"
    severity_label = "Blocking issue"
    return SnapshotHealthPreviewRow(
        snapshot_id=snapshot_id,
        severity_label=severity_label,
        display_line=f"{severity_label}: {relative_path} · {issue_type}",
    )


def _coverage_row(snapshot_id: str, payload: dict[object, object]) -> SnapshotHealthPreviewRow:
    relative_path = _required_text(payload.get("relative_path")) or "."
    coverage_state = _required_text(payload.get("coverage_state")) or "UNKNOWN"
    severity_label = "Coverage warning"
    return SnapshotHealthPreviewRow(
        snapshot_id=snapshot_id,
        severity_label=severity_label,
        display_line=f"{severity_label}: {relative_path} · {coverage_state}",
    )


def _summary_label(
    *,
    snapshot_id: str,
    blocking_issue_count: int,
    coverage_warning_count: int,
    has_more: bool,
) -> str:
    more = " More snapshot rows exist." if has_more else ""
    if blocking_issue_count > 0:
        issue_word = "issue" if blocking_issue_count == 1 else "issues"
        return f"{blocking_issue_count} blocking {issue_word} in {snapshot_id}.{more}"
    if coverage_warning_count > 0:
        warning_word = "warning" if coverage_warning_count == 1 else "warnings"
        return f"{coverage_warning_count} coverage {warning_word} in {snapshot_id}.{more}"
    return f"No blocking snapshot issues in {snapshot_id}."


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
