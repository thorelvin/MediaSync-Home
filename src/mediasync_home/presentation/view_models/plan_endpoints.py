from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class PlanEndpointPreviewRow:
    endpoint_id: str
    snapshot_id: str
    display_line: str


@dataclass(frozen=True)
class PlanEndpointPreviewState:
    plan_id: str | None
    source_snapshot_id: str | None
    title: str
    summary_label: str
    read_model_available: bool
    has_more_endpoints: bool
    rows: tuple[PlanEndpointPreviewRow, ...]


def empty_plan_endpoint_preview_state() -> PlanEndpointPreviewState:
    return PlanEndpointPreviewState(
        plan_id=None,
        source_snapshot_id=None,
        title="Plan endpoints",
        summary_label="No sealed plan endpoints to show.",
        read_model_available=False,
        has_more_endpoints=False,
        rows=(),
    )


def plan_endpoint_preview_from_response(response: IpcResponse | None) -> PlanEndpointPreviewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_plan_endpoint_preview_state()
    page = response.payload.get("plan_endpoints")
    if not isinstance(page, dict):
        return empty_plan_endpoint_preview_state()

    plan_id = _required_text(page.get("plan_id"))
    read_model_available = bool(page.get("read_model_available", False))
    has_more = bool(page.get("has_more", False))
    if not read_model_available:
        return PlanEndpointPreviewState(
            plan_id=plan_id,
            source_snapshot_id=None,
            title="Plan endpoints",
            summary_label="Plan endpoint read model is not available.",
            read_model_available=False,
            has_more_endpoints=False,
            rows=(),
        )

    endpoints_payload = page.get("endpoints")
    endpoints = tuple(
        item for item in endpoints_payload if isinstance(item, dict)
    ) if isinstance(endpoints_payload, list) else ()
    rows = tuple(_preview_row(endpoint) for endpoint in endpoints)
    return PlanEndpointPreviewState(
        plan_id=plan_id,
        source_snapshot_id=_source_snapshot_id(endpoints),
        title="Plan endpoints",
        summary_label=_summary_label(plan_id=plan_id, row_count=len(rows), has_more=has_more),
        read_model_available=True,
        has_more_endpoints=has_more,
        rows=rows,
    )


def _preview_row(payload: dict[object, object]) -> PlanEndpointPreviewRow:
    endpoint_id = _required_text(payload.get("endpoint_id")) or "endpoint"
    snapshot_id = _required_text(payload.get("snapshot_id")) or "snapshot"
    role_label = _role_label(payload.get("role"), payload.get("target_ordinal"))
    return PlanEndpointPreviewRow(
        endpoint_id=endpoint_id,
        snapshot_id=snapshot_id,
        display_line=f"{role_label}: {endpoint_id} · snapshot {snapshot_id}",
    )


def _summary_label(*, plan_id: str | None, row_count: int, has_more: bool) -> str:
    if row_count < 1:
        return f"{plan_id or 'Plan'} has no visible endpoints."
    more = " More endpoints exist." if has_more else ""
    return f"{row_count} endpoint{'s' if row_count != 1 else ''} from {plan_id or 'plan'}.{more}"


def _role_label(value: object, target_ordinal: object) -> str:
    role = _required_text(value)
    if role == "SOURCE":
        return "Source endpoint"
    if role == "TARGET_READONLY":
        return "Read-only target endpoint"
    if role == "TARGET_WRITABLE":
        ordinal = _non_negative_int(target_ordinal)
        if ordinal is not None:
            return f"Target endpoint {ordinal + 1}"
        return "Target endpoint"
    return "Endpoint"


def _source_snapshot_id(endpoints: tuple[dict[object, object], ...]) -> str | None:
    for endpoint in endpoints:
        if _required_text(endpoint.get("role")) == "SOURCE":
            return _required_text(endpoint.get("snapshot_id"))
    return None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
