from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class FilterDecisionPreviewRow:
    relative_path: str
    decision_state: str
    reason_code: str
    matched_rule_id: str | None

    @property
    def display_line(self) -> str:
        state_label = {
            "INCLUDED": "Included",
            "EXCLUDED": "Excluded",
            "ERROR": "Rule error",
        }.get(self.decision_state, self.decision_state)
        reason_label = {
            "FILTER_CONTROL_AREA_EXCLUDED": "Protected MediaSync control data",
            "FILTER_RULE_EXCLUDED": "Matched exclusion rule",
            "FILTER_RULE_INCLUDED": "Matched inclusion rule",
            "FILTER_INCLUDE_RULE_NOT_MATCHED": "No inclusion rule matched",
            "FILTER_REGEX_BUDGET_EXCEEDED": "Rule evaluation limit reached",
        }.get(self.reason_code, self.reason_code)
        rule = (
            f" - Rule: {self.matched_rule_id}"
            if self.matched_rule_id is not None
            else ""
        )
        return f"{state_label}: {self.relative_path} - {reason_label}{rule}"


@dataclass(frozen=True)
class FilterDecisionPreviewState:
    snapshot_id: str | None
    title: str
    summary_label: str
    read_model_available: bool
    has_more_rows: bool
    rows: tuple[FilterDecisionPreviewRow, ...]


def empty_filter_decision_preview_state() -> FilterDecisionPreviewState:
    return FilterDecisionPreviewState(
        snapshot_id=None,
        title="File selection",
        summary_label="No source snapshot to inspect.",
        read_model_available=False,
        has_more_rows=False,
        rows=(),
    )


def filter_decision_preview_from_response(
    *,
    snapshot_id: str | None,
    response: IpcResponse | None,
) -> FilterDecisionPreviewState:
    normalized_snapshot_id = _required_text(snapshot_id)
    if normalized_snapshot_id is None:
        return empty_filter_decision_preview_state()
    page = _response_page(response)
    if page is None or not bool(page.get("read_model_available", False)):
        return FilterDecisionPreviewState(
            snapshot_id=normalized_snapshot_id,
            title="File selection",
            summary_label="File-selection details are not available.",
            read_model_available=False,
            has_more_rows=False,
            rows=(),
        )
    rows = tuple(
        FilterDecisionPreviewRow(
            relative_path=_required_text(item.get("relative_path")) or ".",
            decision_state=(
                _required_text(item.get("decision_state")) or "UNKNOWN"
            ),
            reason_code=_required_text(item.get("reason_code")) or "UNKNOWN",
            matched_rule_id=_required_text(item.get("matched_rule_id")),
        )
        for item in _dict_items(page, "decisions")
    )
    has_more = bool(page.get("has_more", False))
    return FilterDecisionPreviewState(
        snapshot_id=normalized_snapshot_id,
        title="File selection",
        summary_label=(
            "More file-selection rows exist."
            if has_more
            else "Exact matched file-selection decisions for this snapshot."
        ),
        read_model_available=True,
        has_more_rows=has_more,
        rows=rows,
    )


def _response_page(response: IpcResponse | None) -> dict[object, object] | None:
    if response is None or response.status is IpcStatus.REJECTED:
        return None
    page = response.payload.get("snapshot_filter_decisions")
    return page if isinstance(page, dict) else None


def _dict_items(
    page: dict[object, object],
    key: str,
) -> tuple[dict[object, object], ...]:
    items = page.get(key)
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
