from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class PlanOperationPreviewRow:
    operation_id: str
    display_line: str
    risk_label: str
    target_endpoint_id: str | None = None
    operation_type: str | None = None
    target_relative_path: str | None = None
    planned_bytes: int = 0


@dataclass(frozen=True)
class PlanOperationPreviewState:
    plan_id: str | None
    title: str
    summary_label: str
    read_model_available: bool
    has_more_operations: bool
    rows: tuple[PlanOperationPreviewRow, ...]
    limit: int = 100
    next_cursor: dict[str, object] | None = None


def empty_plan_operation_preview_state() -> PlanOperationPreviewState:
    return PlanOperationPreviewState(
        plan_id=None,
        title="Plan preview",
        summary_label="No sealed plan to show.",
        read_model_available=False,
        has_more_operations=False,
        rows=(),
    )


def plan_operation_preview_from_response(response: IpcResponse | None) -> PlanOperationPreviewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_plan_operation_preview_state()
    page = response.payload.get("plan_operations")
    if not isinstance(page, dict):
        return empty_plan_operation_preview_state()

    plan_id = _required_text(page.get("plan_id"))
    read_model_available = bool(page.get("read_model_available", False))
    has_more = bool(page.get("has_more", False))
    if not read_model_available:
        return PlanOperationPreviewState(
            plan_id=plan_id,
            title="Plan preview",
            summary_label="Plan read model is not available.",
            read_model_available=False,
            has_more_operations=False,
            rows=(),
        )

    operations_payload = page.get("operations")
    operations = tuple(
        item for item in operations_payload if isinstance(item, dict)
    ) if isinstance(operations_payload, list) else ()
    rows = tuple(_preview_row(operation) for operation in operations)
    return PlanOperationPreviewState(
        plan_id=plan_id,
        title="Plan preview",
        summary_label=_summary_label(plan_id=plan_id, row_count=len(rows), has_more=has_more),
        read_model_available=True,
        has_more_operations=has_more,
        rows=rows,
        limit=_positive_int(page.get("limit")) or 100,
        next_cursor=_next_cursor(page.get("next_cursor")),
    )


def _preview_row(payload: dict[object, object]) -> PlanOperationPreviewRow:
    operation_id = _required_text(payload.get("operation_id")) or "operation"
    operation_type_code = _required_text(payload.get("operation_type"))
    operation_type = _operation_label(operation_type_code)
    risk = _risk_label(payload.get("risk_level"))
    path = _required_text(payload.get("target_relative_path")) or _required_text(payload.get("reason_code")) or "item"
    target_endpoint_id = _required_text(payload.get("target_endpoint_id"))
    planned_bytes = _non_negative_int(payload.get("planned_bytes")) or 0
    suffix = f" - {_bytes_label(planned_bytes)}" if planned_bytes else ""
    target_suffix = f" -> {target_endpoint_id}" if target_endpoint_id is not None else ""
    return PlanOperationPreviewRow(
        operation_id=operation_id,
        display_line=f"{operation_type}: {path}{suffix}{target_suffix}",
        risk_label=risk,
        target_endpoint_id=target_endpoint_id,
        operation_type=operation_type_code,
        target_relative_path=_required_text(payload.get("target_relative_path")),
        planned_bytes=planned_bytes,
    )


def _next_cursor(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    execution_phase = _non_negative_int(value.get("execution_phase"))
    stable_order_key = _required_text(value.get("stable_order_key"))
    operation_id = _required_text(value.get("operation_id"))
    if execution_phase is None or stable_order_key is None or operation_id is None:
        return None
    return {
        "execution_phase": execution_phase,
        "stable_order_key": stable_order_key,
        "operation_id": operation_id,
    }


def _positive_int(value: object) -> int | None:
    parsed = _non_negative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _summary_label(*, plan_id: str | None, row_count: int, has_more: bool) -> str:
    if row_count < 1:
        return f"{plan_id or 'Plan'} has no visible operations."
    more = " More operations exist." if has_more else ""
    return f"{row_count} operation{'s' if row_count != 1 else ''} from {plan_id or 'plan'}.{more}"


def _operation_label(value: object) -> str:
    text = _required_text(value)
    if text is None:
        return "Operation"
    return {
        "COPY_NEW": "Copy new",
        "CREATE_DIRECTORY": "Create folder",
        "SKIP_IDENTICAL": "Already current",
        "DEFER_AUTOMATION_POLICY": "Deferred",
        "BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN": "Blocked",
    }.get(text, text)


def _risk_label(value: object) -> str:
    text = _required_text(value)
    if text is None:
        return "Unknown"
    return {
        "LOW": "Low",
        "MEDIUM": "Review",
        "HIGH": "High",
        "BLOCKED": "Blocked",
    }.get(text, text)


def _bytes_label(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


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
