from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.plans import (
    MAX_PLAN_OPERATION_PAGE_LIMIT,
    PlanDependency,
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationCursor,
    PlanOperationPageQuery,
    PlanOperationType,
    PlanRiskLevel,
    PlanSealViolation,
    TargetPreconditionKind,
    seal_plan,
    validate_plan_operation_page_query,
    verify_plan_checksum,
)


def test_seal_plan_creates_deterministic_checksum_and_summary() -> None:
    operations = (_skip_operation(), _copy_operation())
    dependencies = (PlanDependency(before_operation_id="op-copy", after_operation_id="op-skip"),)

    plan = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=operations,
        dependencies=dependencies,
    )
    same = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=tuple(reversed(operations)),
        dependencies=dependencies,
    )

    assert plan.plan_checksum == same.plan_checksum
    assert len(plan.plan_checksum) == 64
    assert plan.operation_count == 2
    assert plan.planned_bytes == 128
    assert plan.operations[0].operation_id == "op-copy"
    assert plan.risk_summary["highest"] == "LOW"
    assert verify_plan_checksum(plan) is True


def test_plan_checksum_changes_when_operation_payload_changes() -> None:
    base = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=(_copy_operation(),),
    )
    changed = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=(replace(_copy_operation(), target_relative_path="Pictures/Other.jpg"),),
    )

    assert changed.plan_checksum != base.plan_checksum


def test_plan_checksum_changes_when_endpoint_binding_changes() -> None:
    base = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(_copy_operation(),),
    )
    changed = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(replace(_target_endpoint(), required_ownership_epoch=2),),
        operations=(_copy_operation(),),
    )

    assert changed.plan_checksum != base.plan_checksum


def test_seal_plan_binds_operations_to_the_only_writable_target() -> None:
    plan = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(_copy_operation(),),
    )

    assert plan.operations[0].target_endpoint_id == "target-a"
    assert verify_plan_checksum(plan) is True


def test_multi_target_mutating_operation_requires_explicit_target_binding() -> None:
    with pytest.raises(
        PlanSealViolation,
        match="MUTATING_PLAN_OPERATION_REQUIRES_TARGET_ENDPOINT",
    ):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            endpoints=(
                _target_endpoint(),
                replace(
                    _target_endpoint(),
                    endpoint_id="target-b",
                    endpoint_revision_id="target-rev-b",
                    snapshot_id="target-snapshot-b",
                    target_ordinal=1,
                ),
            ),
            operations=(_copy_operation(),),
        )


def test_plan_checksum_covers_seal_metadata() -> None:
    plan = seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=(_copy_operation(),),
    )

    assert verify_plan_checksum(replace(plan, planner_version="changed")) is False


def test_mutating_plan_operation_requires_target_precondition() -> None:
    with pytest.raises(
        PlanSealViolation,
        match="MUTATING_PLAN_OPERATION_REQUIRES_TARGET_PRECONDITION",
    ):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            operations=(
                replace(
                    _copy_operation(),
                    target_precondition_kind=TargetPreconditionKind.NONE,
                ),
            ),
        )


def test_plan_operation_target_path_must_be_relative() -> None:
    with pytest.raises(PlanSealViolation, match="PLAN_OPERATION_TARGET_PATH_MUST_BE_RELATIVE"):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            operations=(replace(_copy_operation(), target_relative_path="C:/Pictures/A.jpg"),),
        )


def test_plan_dependencies_must_reference_existing_operations() -> None:
    with pytest.raises(PlanSealViolation, match="PLAN_DEPENDENCY_REQUIRES_EXISTING_OPERATIONS"):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            operations=(_copy_operation(),),
            dependencies=(PlanDependency(before_operation_id="op-copy", after_operation_id="missing"),),
        )


def test_plan_dependencies_must_be_acyclic() -> None:
    with pytest.raises(PlanSealViolation, match="PLAN_DEPENDENCIES_MUST_BE_ACYCLIC"):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            operations=(_copy_operation(), _skip_operation()),
            dependencies=(
                PlanDependency(before_operation_id="op-copy", after_operation_id="op-skip"),
                PlanDependency(before_operation_id="op-skip", after_operation_id="op-copy"),
            ),
        )


def test_writable_plan_target_requires_owner_epoch_and_control_schema() -> None:
    with pytest.raises(PlanSealViolation, match="WRITABLE_PLAN_TARGET_REQUIRES_OWNER"):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            endpoints=(replace(_target_endpoint(), required_owner_installation_id=None),),
            operations=(_copy_operation(),),
        )
    with pytest.raises(PlanSealViolation, match="WRITABLE_PLAN_TARGET_REQUIRES_OWNERSHIP_EPOCH"):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            endpoints=(replace(_target_endpoint(), required_ownership_epoch=0),),
            operations=(_copy_operation(),),
        )
    with pytest.raises(PlanSealViolation, match="WRITABLE_PLAN_TARGET_REQUIRES_CONTROL_SCHEMA_VERSION"):
        seal_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            endpoints=(replace(_target_endpoint(), control_schema_version=None),),
            operations=(_copy_operation(),),
        )


def test_plan_operation_page_query_rejects_unbounded_limits() -> None:
    validate_plan_operation_page_query(
        PlanOperationPageQuery(
            plan_id="plan-a",
            limit=MAX_PLAN_OPERATION_PAGE_LIMIT,
            after=PlanOperationCursor(
                execution_phase=1,
                stable_order_key="001:Pictures/A.jpg",
                operation_id="op-a",
            ),
        )
    )

    with pytest.raises(PlanSealViolation, match="PLAN_OPERATION_READ_LIMIT_TOO_LARGE"):
        validate_plan_operation_page_query(
            PlanOperationPageQuery(
                plan_id="plan-a",
                limit=MAX_PLAN_OPERATION_PAGE_LIMIT + 1,
            )
        )


def test_plan_operation_page_query_rejects_invalid_cursor() -> None:
    with pytest.raises(
        PlanSealViolation,
        match="PLAN_OPERATION_READ_CURSOR_PHASE_MUST_BE_NON_NEGATIVE",
    ):
        validate_plan_operation_page_query(
            PlanOperationPageQuery(
                plan_id="plan-a",
                limit=10,
                after=PlanOperationCursor(
                    execution_phase=-1,
                    stable_order_key="001:Pictures/A.jpg",
                    operation_id="op-a",
                ),
            )
        )

    with pytest.raises(
        PlanSealViolation,
        match="PLAN_OPERATION_READ_CURSOR_REQUIRES_OPERATION_ID",
    ):
        validate_plan_operation_page_query(
            PlanOperationPageQuery(
                plan_id="plan-a",
                limit=10,
                after=PlanOperationCursor(
                    execution_phase=1,
                    stable_order_key="001:Pictures/A.jpg",
                    operation_id=" ",
                ),
            )
        )


def _copy_operation() -> PlanOperation:
    return PlanOperation(
        operation_id="op-copy",
        operation_type=PlanOperationType.COPY_NEW,
        sequence_no=10,
        execution_phase=20,
        stable_order_key="020:Pictures/A.jpg",
        target_precondition_kind=TargetPreconditionKind.ABSENT,
        target_relative_path="Pictures/A.jpg",
        planned_bytes=128,
        reason_code="COPY_NEW",
        risk_level=PlanRiskLevel.LOW,
    )


def _target_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        snapshot_id="target-snapshot-a",
        role=PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        endpoint_generation=1,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )


def _skip_operation() -> PlanOperation:
    return PlanOperation(
        operation_id="op-skip",
        operation_type=PlanOperationType.SKIP_IDENTICAL,
        sequence_no=20,
        execution_phase=70,
        stable_order_key="070:Pictures/B.jpg",
        target_precondition_kind=TargetPreconditionKind.NONE,
        target_relative_path="Pictures/B.jpg",
        reason_code="SKIP_IDENTICAL",
        risk_level=PlanRiskLevel.LOW,
    )
