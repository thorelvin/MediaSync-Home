from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    PlanStore,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.run_operation_planning import plan_run_target_recovery_operations
from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_plan_run_target_recovery_operations_records_mutating_plan_operations() -> None:
    recovery_operations = _FakeRecoveryOperationStore()

    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(_executing_run()),
        plans=_SinglePlanStore(_sealed_plan()),
        recovery_operations=recovery_operations,
        process_instance_id="host-a",
    )

    assert outcome.planned is True
    assert outcome.validation_codes == ()
    assert outcome.operations_planned == 1
    assert len(outcome.operations) == 1
    operation = outcome.operations[0]
    assert operation.operation_id == "op-copy"
    assert operation.phase is RecoveryOperationPhase.PLANNED
    assert operation.run_target_id == "run-a-target-0000"
    assert operation.target_endpoint_id == "target-a"
    assert operation.endpoint_generation == 1
    assert operation.lease_id == "lease-a"
    assert operation.fencing_token == 42
    assert operation.final_relative_path == "Pictures/A.jpg"
    assert operation.target_precondition_kind is RecoveryTargetPreconditionKind.ABSENT
    assert operation.source_endpoint_id == "source-a"
    assert operation.source_endpoint_revision_id == "source-rev-a"
    assert operation.source_relative_path == "Pictures/A.jpg"
    assert recovery_operations.payloads == (
        {
            "operation_type": "COPY_NEW",
            "plan_checksum": _sealed_plan().plan_checksum,
            "plan_id": "plan-a",
            "sequence_no": 10,
            "target_endpoint_id": "target-a",
        },
    )


def test_plan_run_target_recovery_operations_preserves_match_fingerprint_precondition() -> None:
    plan = _sealed_plan(
        reason_code="REPLACE_CHANGED",
        target_precondition_kind=TargetPreconditionKind.MATCH_FINGERPRINT,
    )
    run = replace(_executing_run(), plan_checksum=plan.plan_checksum)

    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(run),
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore(),
        process_instance_id="host-a",
    )

    assert outcome.planned is True
    assert outcome.validation_codes == ()
    assert len(outcome.operations) == 1
    operation = outcome.operations[0]
    assert operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT
    assert operation.source_endpoint_id == "source-a"
    assert operation.source_endpoint_revision_id == "source-rev-a"
    assert operation.source_relative_path == "Pictures/A.jpg"


def test_plan_run_target_recovery_operations_is_idempotent() -> None:
    recovery_operations = _FakeRecoveryOperationStore()
    plan = _sealed_plan()
    runs = _SingleRunStore(_executing_run())

    first = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=runs,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        process_instance_id="host-a",
    )
    second = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=runs,
        plans=_SinglePlanStore(plan),
        recovery_operations=recovery_operations,
        process_instance_id="host-a",
    )

    assert first.planned is True
    assert second.planned is True
    assert second.operations == first.operations
    assert len(recovery_operations.operations) == 1


def test_plan_run_target_recovery_operations_rejects_generation_mismatch() -> None:
    plan = _sealed_plan(endpoint_generation=2)
    run = replace(_executing_run(), plan_checksum=plan.plan_checksum)

    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(run),
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore(),
        process_instance_id="host-a",
    )

    assert outcome.planned is False
    assert outcome.validation_codes == ("PLAN_TARGET_ENDPOINT_GENERATION_MISMATCH",)


def test_plan_run_target_recovery_operations_requires_executing_run() -> None:
    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(replace(_executing_run(), state=RunState.PREFLIGHT)),
        plans=_SinglePlanStore(_sealed_plan()),
        recovery_operations=_FakeRecoveryOperationStore(),
        process_instance_id="host-a",
    )

    assert outcome.planned is False
    assert outcome.validation_codes == ("RUN_NOT_EXECUTING",)
    assert outcome.operations_planned == 0


def test_plan_run_target_recovery_operations_rejects_permit_mismatch() -> None:
    run = replace(
        _executing_run(),
        targets=(replace(_target(), last_fencing_token=41),),
    )

    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(run),
        plans=_SinglePlanStore(_sealed_plan()),
        recovery_operations=_FakeRecoveryOperationStore(),
        process_instance_id="host-a",
    )

    assert outcome.planned is False
    assert outcome.validation_codes == ("RUN_TARGET_PERMIT_MISMATCH",)


def test_plan_run_target_recovery_operations_reports_store_failure() -> None:
    recovery_operations = _FakeRecoveryOperationStore(
        failure=ValueError("RECOVERY_OPERATION_LEASE_MISMATCH")
    )

    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(_executing_run()),
        plans=_SinglePlanStore(_sealed_plan()),
        recovery_operations=recovery_operations,
        process_instance_id="host-a",
    )

    assert outcome.planned is False
    assert outcome.validation_codes == ("RECOVERY_OPERATION_LEASE_MISMATCH",)
    assert outcome.operations_planned == 0


def test_plan_run_target_recovery_operations_requires_matching_plan_checksum() -> None:
    run = replace(_executing_run(), plan_checksum="b" * 64)

    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(run),
        plans=_SinglePlanStore(_sealed_plan()),
        recovery_operations=_FakeRecoveryOperationStore(),
        process_instance_id="host-a",
    )

    assert outcome.planned is False
    assert outcome.validation_codes == ("PLAN_CHECKSUM_MISMATCH",)


def test_plan_run_target_recovery_operations_selects_bound_multi_target_operations() -> None:
    plan = _multi_target_plan()
    outcome = plan_run_target_recovery_operations(
        permit=_permit(),
        runs=_SingleRunStore(replace(_executing_run(), plan_checksum=plan.plan_checksum)),
        plans=_SinglePlanStore(plan),
        recovery_operations=_FakeRecoveryOperationStore(),
        process_instance_id="host-a",
    )

    assert outcome.planned is True
    assert outcome.validation_codes == ()
    assert [operation.operation_id for operation in outcome.operations] == ["op-copy-a"]
    assert all(
        operation.target_endpoint_id == "target-a"
        for operation in outcome.operations
    )


class _SingleRunStore(RunStore):
    def __init__(self, run: StartedRun | None) -> None:
        self._run = run

    def save_started_run(self, run: StartedRun) -> None:
        self._run = run

    def load_started_run(self, run_id: str) -> StartedRun | None:
        if self._run is not None and self._run.run_id == run_id:
            return self._run
        return None

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
        if self._run is not None and self._run.idempotency_key == idempotency_key:
            return self._run
        return None

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        return None

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        return None

    def record_run_target_lease_acquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        return None

    def record_run_target_execution_started(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        return None


class _SinglePlanStore(PlanStore):
    def __init__(self, plan: SealedPlan | None) -> None:
        self._plan = plan

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self._plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        if self._plan is not None and self._plan.plan_id == plan_id:
            return self._plan
        return None


class _FakeRecoveryOperationStore(RecoveryOperationStore):
    def __init__(self, *, failure: ValueError | None = None) -> None:
        self.operations: dict[tuple[str, str], RecoveryOperation] = {}
        self.payloads: tuple[Mapping[str, object] | None, ...] = ()
        self._failure = failure

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        if self._failure is not None:
            raise self._failure
        key = (operation.run_id, operation.operation_id)
        existing = self.operations.get(key)
        if existing is not None:
            if existing != operation:
                raise ValueError("RECOVERY_OPERATION_IDEMPOTENCY_CONFLICT")
            return existing
        self.operations[key] = operation
        self.payloads = (*self.payloads, payload)
        return operation

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: object | None = None,
    ) -> RecoveryOperation | None:
        return None

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        return self.operations.get((run_id, operation_id))


def _executing_run() -> StartedRun:
    return StartedRun(
        run_id="run-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        plan_id="plan-a",
        command_request_id="request-a",
        idempotency_key="idempotency-a",
        command_receipt_id="idempotency-a",
        logical_run_group_id="run-group-a",
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=RunState.EXECUTING,
        app_version="0B-dev",
        plan_checksum=_sealed_plan().plan_checksum,
        planned_operations=1,
        planned_bytes=128,
        targets=(_target(),),
    )


def _target() -> StartedRunTarget:
    return StartedRunTarget(
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state=RunTargetState.EXECUTING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        last_lease_id="lease-a",
        last_ownership_epoch=1,
        last_fencing_token=42,
        planned_operations=1,
        planned_bytes=128,
    )


def _sealed_plan(
    *,
    reason_code: str = "COPY_NEW",
    target_precondition_kind: TargetPreconditionKind = TargetPreconditionKind.ABSENT,
    endpoint_generation: int = 1,
) -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _source_endpoint(),
            replace(_target_endpoint(), endpoint_generation=endpoint_generation),
        ),
        operations=(
            PlanOperation(
                operation_id="op-copy",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=target_precondition_kind,
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code=reason_code,
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-skip",
                operation_type=PlanOperationType.SKIP_IDENTICAL,
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:Pictures/B.jpg",
                target_precondition_kind=TargetPreconditionKind.NONE,
                target_relative_path="Pictures/B.jpg",
                planned_bytes=0,
                reason_code="SKIP_IDENTICAL",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
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


def _source_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="source-a",
        endpoint_revision_id="source-rev-a",
        snapshot_id="source-snapshot-a",
        role=PlanEndpointRole.SOURCE,
        target_ordinal=None,
        capabilities_hash="capabilities-source-a",
        root_case_context_hash="case-source-a",
        endpoint_generation=1,
        control_schema_version=1,
        planned_operations=0,
        planned_bytes=0,
    )


def _multi_target_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _source_endpoint(),
            _target_endpoint(),
            replace(
                _target_endpoint(),
                endpoint_id="target-b",
                endpoint_revision_id="target-rev-b",
                snapshot_id="target-snapshot-b",
                target_ordinal=1,
            ),
        ),
        operations=(
            PlanOperation(
                operation_id="op-copy-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:0000:target-a:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_endpoint_id="target-a",
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-copy-b",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:0001:target-b:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_endpoint_id="target-b",
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=42,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
