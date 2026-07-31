from __future__ import annotations

import pytest

from mediasync_home.application.plan_read_models import (
    DEFAULT_PLAN_ENDPOINT_PAGE_LIMIT,
    DEFAULT_PLAN_OPERATION_PAGE_LIMIT,
    PlanEndpointsQueryError,
    PlanOperationsQueryError,
    query_plan_endpoints,
    query_plan_operations,
)
from mediasync_home.application.plans import (
    PlanEndpointCursor,
    PlanEndpointPage,
    PlanEndpointPageQuery,
    PlanEndpointReadModel,
    PlanEndpointRole,
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
        "risk_counts": {},
        "highest_risk": None,
        "target_endpoint_ids": [],
        "operations": [],
    }


def test_plan_endpoint_query_reports_unavailable_store_with_normalized_bounds() -> None:
    page = query_plan_endpoints(plan_read_store=None, plan_id=" plan-a ")

    assert page.to_dict() == {
        "plan_id": "plan-a",
        "limit": DEFAULT_PLAN_ENDPOINT_PAGE_LIMIT,
        "has_more": False,
        "read_model_available": False,
        "next_cursor": None,
        "endpoints": [],
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
        target_endpoint_id="target-a",
        risk_levels=("LOW", "MEDIUM"),
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
            target_endpoint_id="target-a",
            risk_levels=(PlanRiskLevel.LOW, PlanRiskLevel.MEDIUM),
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
        "risk_counts": {"LOW": 2},
        "highest_risk": "LOW",
        "target_endpoint_ids": ["target-a"],
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
                    "target_endpoint_id": None,
                    "target_relative_path": "Pictures/op-a.jpg",
                "planned_bytes": 128,
            }
        ],
    }


def test_plan_endpoint_query_returns_bounded_serializable_page() -> None:
    store = _FakePlanEndpointStore((_endpoint("source-a"), _endpoint("target-a")))

    page = query_plan_endpoints(
        plan_read_store=store,
        plan_id="plan-a",
        limit=1,
        after={
            "role": "SOURCE",
            "target_ordinal": None,
            "endpoint_id": "previous-source",
        },
    )

    assert store.queries == (
        PlanEndpointPageQuery(
            plan_id="plan-a",
            limit=1,
            after=PlanEndpointCursor(
                role=PlanEndpointRole.SOURCE,
                target_ordinal=None,
                endpoint_id="previous-source",
            ),
        ),
    )
    assert page.to_dict() == {
        "plan_id": "plan-a",
        "limit": 1,
        "has_more": True,
        "read_model_available": True,
        "next_cursor": {
            "role": "SOURCE",
            "target_ordinal": None,
            "endpoint_id": "source-a",
        },
        "endpoints": [
            {
                    "endpoint_id": "source-a",
                    "endpoint_generation": 1,
                    "endpoint_revision_id": "source-a-rev",
                "snapshot_id": "source-a-snapshot",
                "role": "SOURCE",
                "target_ordinal": None,
                "capabilities_hash": "capabilities-a",
                "root_case_context_hash": "case-a",
                "required_owner_installation_id": None,
                "required_ownership_epoch": None,
                "control_schema_version": None,
                "planned_operations": 0,
                "planned_bytes": 0,
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


@pytest.mark.parametrize(
    ("target_endpoint_id", "risk_levels"),
    [
        (" ", ()),
        (None, ("UNKNOWN",)),
        (None, ("LOW", "LOW")),
    ],
)
def test_plan_operation_query_rejects_invalid_filters(
    target_endpoint_id: str | None,
    risk_levels: tuple[str, ...],
) -> None:
    with pytest.raises(PlanOperationsQueryError):
        query_plan_operations(
            plan_read_store=None,
            plan_id="plan-a",
            target_endpoint_id=target_endpoint_id,
            risk_levels=risk_levels,
        )


@pytest.mark.parametrize(
    ("plan_id", "limit", "after"),
    [
        (" ", None, None),
        ("plan-a", 0, None),
        ("plan-a", 101, None),
        ("plan-a", 10, {"role": "NOT_A_ROLE", "target_ordinal": None, "endpoint_id": "target-a"}),
        ("plan-a", 10, {"role": "SOURCE", "target_ordinal": -1, "endpoint_id": "target-a"}),
        ("plan-a", 10, {"role": "SOURCE", "target_ordinal": None, "endpoint_id": ""}),
    ],
)
def test_plan_endpoint_query_rejects_invalid_bounds_or_cursor(
    plan_id: str,
    limit: int | None,
    after: dict[str, object] | None,
) -> None:
    with pytest.raises(PlanEndpointsQueryError):
        query_plan_endpoints(
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
            risk_counts={"LOW": len(self._operations)},
            highest_risk=PlanRiskLevel.LOW,
            target_endpoint_ids=("target-a",),
        )


class _FakePlanEndpointStore:
    def __init__(self, endpoints: tuple[PlanEndpointReadModel, ...]) -> None:
        self._endpoints = endpoints
        self.queries: tuple[PlanEndpointPageQuery, ...] = ()

    def page_plan_endpoints(self, query: PlanEndpointPageQuery) -> PlanEndpointPage:
        self.queries = (*self.queries, query)
        endpoints = self._endpoints[: query.limit]
        return PlanEndpointPage(
            plan_id=query.plan_id,
            endpoints=endpoints,
            has_more=len(self._endpoints) > query.limit,
            next_cursor=_endpoint_cursor(endpoints[-1]) if len(self._endpoints) > query.limit else None,
        )


def _endpoint(endpoint_id: str) -> PlanEndpointReadModel:
    return PlanEndpointReadModel(
        endpoint_id=endpoint_id,
        endpoint_revision_id=f"{endpoint_id}-rev",
        snapshot_id=f"{endpoint_id}-snapshot",
        role=PlanEndpointRole.SOURCE if endpoint_id.startswith("source") else PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=None if endpoint_id.startswith("source") else 0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        endpoint_generation=1,
        required_owner_installation_id=None,
        required_ownership_epoch=None,
        control_schema_version=None,
        planned_operations=0,
        planned_bytes=0,
    )


def _endpoint_cursor(endpoint: PlanEndpointReadModel) -> PlanEndpointCursor:
    return PlanEndpointCursor(
        role=endpoint.role,
        target_ordinal=endpoint.target_ordinal,
        endpoint_id=endpoint.endpoint_id,
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
