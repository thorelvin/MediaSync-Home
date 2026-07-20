from __future__ import annotations

import pytest

from mediasync_home.application.plan_read_models import (
    DEFAULT_PLAN_OPERATION_PAGE_LIMIT,
    PlanOperationsQueryError,
    query_plan_operations,
)
from mediasync_home.application.plans import (
    PlanOperationCursor,
    PlanOperationPage,
    PlanOperationPageQuery,
    PlanOperationReadModel,
    PlanOperationType,
    PlanRiskLevel,
    TargetPreconditionKind,
)


def test_plan_operation_query_reports_unavailable_store_with_normalized_bounds() -> None:
    page = query_plan_operations(plan_read_store=None, plan_id=" plan-a ")

    assert page.to_dict() == {
        "plan_id": "plan-a",
        "limit": DEFAULT_PLAN_OPERATION_PAGE_LIMIT,
        "has_more": False,
        "read_model_available": False,
        "next_cursor": None,
        "operations": [],
    }


def test_plan_operation_query_returns_bounded_serializable_page() -> None:
    store = _FakePlanOperationStore((_operation("op-a"), _operation("op-b")))

    page = query_plan_operations(
        plan_read_store=store,
        plan_id="plan-a",
        limit=1,
        after={
            "execution_phase": 0,
            "stable_order_key": "000:start",
            "operation_id": "op-start",
        },
    )

    assert store.queries == (
        PlanOperationPageQuery(
            plan_id="plan-a",
            limit=1,
            after=PlanOperationCursor(
                execution_phase=0,
                stable_order_key="000:start",
                operation_id="op-start",
            ),
        ),
    )
    assert page.to_dict() == {
        "plan_id": "plan-a",
        "limit": 1,
        "has_more": True,
        "read_model_available": True,
        "next_cursor": {
            "execution_phase": 10,
            "stable_order_key": "010:Pictures/op-a.jpg",
            "operation_id": "op-a",
        },
        "operations": [
            {
                "operation_id": "op-a",
                "operation_type": "COPY_NEW",
                "sequence_no": 10,
                "execution_phase": 10,
                "stable_order_key": "010:Pictures/op-a.jpg",
                "target_precondition_kind": "ABSENT",
                "reason_code": "COPY_NEW",
                "risk_level": "LOW",
                "target_relative_path": "Pictures/op-a.jpg",
                "planned_bytes": 128,
            }
        ],
    }


@pytest.mark.parametrize(
    ("plan_id", "limit", "after"),
    [
        (" ", None, None),
        ("plan-a", 0, None),
        ("plan-a", 1001, None),
        (
            "plan-a",
            10,
            {"execution_phase": -1, "stable_order_key": "010:path", "operation_id": "op-a"},
        ),
        (
            "plan-a",
            10,
            {"execution_phase": 1, "stable_order_key": "", "operation_id": "op-a"},
        ),
    ],
)
def test_plan_operation_query_rejects_invalid_bounds_or_cursor(
    plan_id: str,
    limit: int | None,
    after: dict[str, object] | None,
) -> None:
    with pytest.raises(PlanOperationsQueryError):
        query_plan_operations(
            plan_read_store=None,
            plan_id=plan_id,
            limit=limit,
            after=after,
        )


class _FakePlanOperationStore:
    def __init__(self, operations: tuple[PlanOperationReadModel, ...]) -> None:
        self._operations = operations
        self.queries: tuple[PlanOperationPageQuery, ...] = ()

    def page_plan_operations(self, query: PlanOperationPageQuery) -> PlanOperationPage:
        self.queries = (*self.queries, query)
        operations = self._operations[: query.limit]
        return PlanOperationPage(
            plan_id=query.plan_id,
            operations=operations,
            has_more=len(self._operations) > query.limit,
            next_cursor=_cursor(operations[-1]) if len(self._operations) > query.limit else None,
        )


def _operation(operation_id: str) -> PlanOperationReadModel:
    return PlanOperationReadModel(
        operation_id=operation_id,
        operation_type=PlanOperationType.COPY_NEW,
        sequence_no=10,
        execution_phase=10,
        stable_order_key=f"010:Pictures/{operation_id}.jpg",
        target_precondition_kind=TargetPreconditionKind.ABSENT,
        reason_code="COPY_NEW",
        risk_level=PlanRiskLevel.LOW,
        target_relative_path=f"Pictures/{operation_id}.jpg",
        planned_bytes=128,
    )


def _cursor(operation: PlanOperationReadModel) -> PlanOperationCursor:
    return PlanOperationCursor(
        execution_phase=operation.execution_phase,
        stable_order_key=operation.stable_order_key,
        operation_id=operation.operation_id,
    )
