from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mediasync_home.application.plans import (
    PlanOperationCursor,
    PlanOperationPageQuery,
    PlanOperationReadModel,
    PlanOperationReadModelStore,
    PlanSealViolation,
    validate_plan_operation_page_query,
)


DEFAULT_PLAN_OPERATION_PAGE_LIMIT = 100


class PlanOperationsQueryError(ValueError):
    pass


@dataclass(frozen=True)
class PlanOperationsReadPage:
    plan_id: str
    limit: int
    has_more: bool
    read_model_available: bool
    operations: tuple[PlanOperationReadModel, ...] = ()
    next_cursor: PlanOperationCursor | None = None

    @classmethod
    def unavailable(cls, *, query: PlanOperationPageQuery) -> "PlanOperationsReadPage":
        return cls(
            plan_id=query.plan_id,
            limit=query.limit,
            has_more=False,
            read_model_available=False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "limit": self.limit,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "next_cursor": _cursor_to_dict(self.next_cursor),
            "operations": [_operation_to_dict(operation) for operation in self.operations],
        }


def query_plan_operations(
    *,
    plan_read_store: PlanOperationReadModelStore | None,
    plan_id: str,
    limit: int | None = None,
    after: PlanOperationCursor | Mapping[str, object] | None = None,
) -> PlanOperationsReadPage:
    query = normalize_plan_operation_page_query(plan_id=plan_id, limit=limit, after=after)
    if plan_read_store is None:
        return PlanOperationsReadPage.unavailable(query=query)

    try:
        page = plan_read_store.page_plan_operations(query)
    except PlanSealViolation as exc:
        raise PlanOperationsQueryError(str(exc)) from exc
    return PlanOperationsReadPage(
        plan_id=page.plan_id,
        limit=query.limit,
        has_more=page.has_more,
        read_model_available=True,
        operations=page.operations,
        next_cursor=page.next_cursor,
    )


def normalize_plan_operation_page_query(
    *,
    plan_id: str,
    limit: int | None,
    after: PlanOperationCursor | Mapping[str, object] | None,
) -> PlanOperationPageQuery:
    try:
        query = PlanOperationPageQuery(
            plan_id=str(plan_id).strip(),
            limit=DEFAULT_PLAN_OPERATION_PAGE_LIMIT if limit is None else int(limit),
            after=_normalize_cursor(after),
        )
        validate_plan_operation_page_query(query)
    except (KeyError, TypeError, ValueError, PlanSealViolation) as exc:
        raise PlanOperationsQueryError("PLAN_OPERATIONS_QUERY_INVALID") from exc
    return query


def _normalize_cursor(
    value: PlanOperationCursor | Mapping[str, object] | None,
) -> PlanOperationCursor | None:
    if value is None or isinstance(value, PlanOperationCursor):
        return value
    return PlanOperationCursor(
        execution_phase=_cursor_int(value["execution_phase"]),
        stable_order_key=str(value["stable_order_key"]),
        operation_id=str(value["operation_id"]),
    )


def _cursor_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("cursor integer must not be a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("cursor integer must be an integer or string")


def _operation_to_dict(operation: PlanOperationReadModel) -> dict[str, object]:
    return {
        "operation_id": operation.operation_id,
        "operation_type": operation.operation_type.value,
        "sequence_no": operation.sequence_no,
        "execution_phase": operation.execution_phase,
        "stable_order_key": operation.stable_order_key,
        "target_precondition_kind": operation.target_precondition_kind.value,
        "reason_code": operation.reason_code,
        "risk_level": operation.risk_level.value,
        "target_relative_path": operation.target_relative_path,
        "planned_bytes": operation.planned_bytes,
    }


def _cursor_to_dict(cursor: PlanOperationCursor | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    return {
        "execution_phase": cursor.execution_phase,
        "stable_order_key": cursor.stable_order_key,
        "operation_id": cursor.operation_id,
    }
