from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.application.plans import (
    MUTATING_OPERATION_TYPES,
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanStore,
    SealedPlan,
    verify_plan_checksum,
)
from mediasync_home.application.job_drafts import RetentionPreset
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.runs import (
    MAX_RUN_OPERATION_ID_LENGTH,
    MAX_RUN_OPERATION_SCOPE,
    RunState,
    RunStore,
    RunTargetState,
    StartedRun,
    StartedRunTarget,
)
from mediasync_home.application.source_preconditions import (
    SourceFilePrecondition,
    SourceFilePreconditionError,
)
from mediasync_home.domain.capabilities import MutationPermit


class RunTargetOperationPlanningError(ValueError):
    pass


@dataclass(frozen=True)
class RunTargetOperationPlanningOutcome:
    planned: bool
    run_id: str
    run_target_id: str
    operations_planned: int
    operations: tuple[RecoveryOperation, ...]
    validation_codes: tuple[str, ...]
    next_action: str


def plan_run_target_recovery_operations(
    *,
    permit: MutationPermit,
    runs: RunStore,
    plans: PlanStore,
    recovery_operations: RecoveryOperationStore,
    process_instance_id: str,
) -> RunTargetOperationPlanningOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_OPERATION_PLANNING_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind operation planning to the Engine Host process instance.",
        )

    run = runs.load_started_run(permit.run_id)
    if run is None:
        return _failed(
            permit=permit,
            validation_code="RUN_NOT_FOUND",
            next_action="Create and start a run before planning recovery operations.",
        )
    if run.state is not RunState.EXECUTING:
        return _failed(
            permit=permit,
            validation_code="RUN_NOT_EXECUTING",
            next_action="Start target execution before planning recovery operations.",
        )

    target = next(
        (item for item in run.targets if item.run_target_id == permit.run_target_id),
        None,
    )
    if target is None:
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_NOT_FOUND",
            next_action="Reload run targets before planning recovery operations.",
        )
    target_error = _target_validation_code(target=target, permit=permit)
    if target_error is not None:
        return _failed(
            permit=permit,
            validation_code=target_error,
            next_action="Reacquire the endpoint lease before planning recovery operations.",
        )

    plan = plans.load_sealed_plan(run.plan_id)
    if plan is None:
        return _failed(
            permit=permit,
            validation_code="PLAN_NOT_FOUND",
            next_action="Reload the sealed plan before planning recovery operations.",
        )
    plan_error = _plan_validation_code(plan=plan, expected_checksum=run.plan_checksum)
    if plan_error is not None:
        return _failed(
            permit=permit,
            validation_code=plan_error,
            next_action="Refresh analysis and approve a new sealed plan before execution.",
        )

    operation_binding_error = _operation_binding_validation_code(plan)
    if operation_binding_error is not None:
        return _failed(
            permit=permit,
            validation_code=operation_binding_error,
            next_action="Refresh analysis to create explicit operation-to-target bindings.",
        )
    endpoint = _target_endpoint(plan=plan, target=target)
    if endpoint is None:
        return _failed(
            permit=permit,
            validation_code="PLAN_TARGET_ENDPOINT_NOT_FOUND",
            next_action="Reload the sealed plan endpoint binding before planning operations.",
        )
    if endpoint.endpoint_generation != permit.endpoint_generation:
        return _failed(
            permit=permit,
            validation_code="PLAN_TARGET_ENDPOINT_GENERATION_MISMATCH",
            next_action="Refresh analysis and reacquire the endpoint lease before planning operations.",
        )

    operation_scope, operation_scope_error = _operation_scope_from_run(
        run=run,
        plan=plan,
    )
    if operation_scope_error is not None:
        return _failed(
            permit=permit,
            validation_code=operation_scope_error,
            next_action="Reload the scoped run before planning recovery operations.",
        )
    target_operations = tuple(
        operation
        for operation in plan.operations
        if operation.operation_type in MUTATING_OPERATION_TYPES
        and operation.target_endpoint_id == endpoint.endpoint_id
        and (operation_scope is None or operation.operation_id in operation_scope)
    )
    if len(target_operations) != target.planned_operations:
        return _failed(
            permit=permit,
            validation_code="RUN_OPERATION_SCOPE_COUNT_MISMATCH",
            next_action="Reload the scoped run before planning recovery operations.",
        )
    source_endpoint = _single_source_endpoint(plan)
    if _has_copy_operation(target_operations) and source_endpoint is None:
        return _failed(
            permit=permit,
            validation_code="PLAN_OPERATION_PLANNING_REQUIRES_SINGLE_SOURCE",
            next_action="Add explicit source endpoint binding before executing copy operations.",
        )

    planned: list[RecoveryOperation] = []
    for operation in target_operations:
        if operation.target_relative_path is None:
            return _failed(
                permit=permit,
                validation_code="PLAN_OPERATION_MISSING_TARGET_PATH",
                next_action="Refresh analysis before planning mutating recovery operations.",
            )
        recovery_operation = _planned_recovery_operation(
            permit=permit,
            run=run,
            target=target,
            endpoint=endpoint,
            operation=operation,
            source_endpoint=source_endpoint,
        )
        try:
            recorded = recovery_operations.record_planned_operation(
                recovery_operation,
                process_instance_id=process_instance_id,
                payload={
                    "operation_type": operation.operation_type.value,
                    "plan_checksum": plan.plan_checksum,
                    "plan_id": plan.plan_id,
                    "sequence_no": operation.sequence_no,
                    "target_endpoint_id": endpoint.endpoint_id,
                },
            )
        except ValueError as exc:
            return RunTargetOperationPlanningOutcome(
                planned=False,
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                operations_planned=len(planned),
                operations=tuple(planned),
                validation_codes=(_error_code(exc),),
                next_action=_error_next_action(
                    exc,
                    "Reload recovery state and retry operation planning.",
                ),
            )
        planned.append(recorded)

    return RunTargetOperationPlanningOutcome(
        planned=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operations_planned=len(planned),
        operations=tuple(planned),
        validation_codes=(),
        next_action="Recovery operations are planned for the executing run target.",
    )


def _planned_recovery_operation(
    *,
    permit: MutationPermit,
    run: StartedRun,
    target: StartedRunTarget,
    endpoint: PlanEndpoint,
    operation: PlanOperation,
    source_endpoint: PlanEndpoint | None,
) -> RecoveryOperation:
    if operation.target_relative_path is None:
        raise RunTargetOperationPlanningError("PLAN_OPERATION_MISSING_TARGET_PATH")
    source_endpoint_id: str | None = None
    source_endpoint_revision_id: str | None = None
    source_relative_path: str | None = None
    source_precondition_json: str | None = None
    if operation.operation_type is PlanOperationType.COPY_NEW:
        if source_endpoint is None:
            raise RunTargetOperationPlanningError("PLAN_OPERATION_PLANNING_REQUIRES_SINGLE_SOURCE")
        source_endpoint_id = source_endpoint.endpoint_id
        source_endpoint_revision_id = source_endpoint.endpoint_revision_id
        source_relative_path = operation.source_relative_path
        source_precondition_json = operation.source_precondition_json
        try:
            precondition = SourceFilePrecondition.from_json(source_precondition_json)
        except SourceFilePreconditionError as exc:
            raise RunTargetOperationPlanningError(exc.validation_code) from exc
        if (
            source_relative_path is None
            or precondition.snapshot_id != source_endpoint.snapshot_id
            or precondition.relative_path != source_relative_path
            or precondition.size_bytes != operation.planned_bytes
        ):
            raise RunTargetOperationPlanningError(
                "PLAN_OPERATION_SOURCE_PRECONDITION_MISMATCH"
            )
    return planned_recovery_operation(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation.operation_id,
        target_endpoint_id=target.endpoint_id,
        target_endpoint_revision_id=target.endpoint_revision_id,
        endpoint_generation=permit.endpoint_generation,
        owner_installation_id=permit.owner_installation_id,
        ownership_epoch=permit.ownership_epoch,
        lease_id=permit.lease_id,
        lease_resource_key=permit.resource_key,
        fencing_token=permit.fencing_token,
        final_relative_path=operation.target_relative_path,
        target_precondition_kind=RecoveryTargetPreconditionKind(
            operation.target_precondition_kind.value
        ),
        operation_kind=RecoveryOperationKind(operation.operation_type.value),
        plan_sequence_no=operation.sequence_no,
        planned_bytes=operation.planned_bytes,
        job_id=run.job_id,
        job_revision_id=run.job_revision_id,
        retention_policy=RetentionPreset.THIRTY_DAYS.value,
        source_endpoint_id=source_endpoint_id,
        source_endpoint_revision_id=source_endpoint_revision_id,
        source_relative_path=source_relative_path,
        source_precondition_json=source_precondition_json,
    )


def _target_validation_code(*, target: StartedRunTarget, permit: MutationPermit) -> str | None:
    if target.state is not RunTargetState.EXECUTING:
        return "RUN_TARGET_NOT_EXECUTING"
    if (
        target.endpoint_id != permit.endpoint_id
        or target.endpoint_revision_id != permit.endpoint_revision_id
        or target.lease_resource_key != permit.resource_key
        or target.last_lease_id != permit.lease_id
        or target.last_ownership_epoch != permit.ownership_epoch
        or target.last_fencing_token != permit.fencing_token
    ):
        return "RUN_TARGET_PERMIT_MISMATCH"
    if target.required_owner_installation_id not in (None, permit.owner_installation_id):
        return "RUN_TARGET_PERMIT_MISMATCH"
    if target.required_ownership_epoch not in (None, permit.ownership_epoch):
        return "RUN_TARGET_PERMIT_MISMATCH"
    return None


def _plan_validation_code(*, plan: SealedPlan, expected_checksum: str) -> str | None:
    if plan.plan_checksum != expected_checksum:
        return "PLAN_CHECKSUM_MISMATCH"
    if not verify_plan_checksum(plan):
        return "PLAN_CHECKSUM_INVALID"
    if not plan.immutable:
        return "PLAN_NOT_IMMUTABLE"
    return None


def _target_endpoint(*, plan: SealedPlan, target: StartedRunTarget) -> PlanEndpoint | None:
    return next(
        (
            endpoint
            for endpoint in _writable_target_endpoints(plan)
            if endpoint.endpoint_id == target.endpoint_id
            and endpoint.endpoint_revision_id == target.endpoint_revision_id
        ),
        None,
    )


def _writable_target_endpoints(plan: SealedPlan) -> tuple[PlanEndpoint, ...]:
    return tuple(
        endpoint
        for endpoint in plan.endpoints
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
    )


def _single_source_endpoint(plan: SealedPlan) -> PlanEndpoint | None:
    sources = tuple(endpoint for endpoint in plan.endpoints if endpoint.role is PlanEndpointRole.SOURCE)
    if len(sources) != 1:
        return None
    return sources[0]


def _operation_binding_validation_code(plan: SealedPlan) -> str | None:
    writable_endpoint_ids = {
        endpoint.endpoint_id
        for endpoint in _writable_target_endpoints(plan)
    }
    source_endpoint = _single_source_endpoint(plan)
    for operation in plan.operations:
        if operation.operation_type not in MUTATING_OPERATION_TYPES:
            continue
        if operation.target_endpoint_id is None:
            return "PLAN_OPERATION_MISSING_TARGET_ENDPOINT"
        if operation.target_endpoint_id not in writable_endpoint_ids:
            return "PLAN_OPERATION_TARGET_ENDPOINT_NOT_FOUND"
        if operation.operation_type is PlanOperationType.COPY_NEW:
            if (
                plan.operation_schema_version < 3
                or operation.source_relative_path is None
                or operation.source_precondition_json is None
            ):
                return "PLAN_OPERATION_SOURCE_PRECONDITION_MISSING"
            if source_endpoint is None:
                return "PLAN_OPERATION_PLANNING_REQUIRES_SINGLE_SOURCE"
            try:
                precondition = SourceFilePrecondition.from_json(
                    operation.source_precondition_json
                )
            except SourceFilePreconditionError as exc:
                return exc.validation_code
            if (
                precondition.snapshot_id != source_endpoint.snapshot_id
                or precondition.relative_path != operation.source_relative_path
                or precondition.size_bytes != operation.planned_bytes
            ):
                return "PLAN_OPERATION_SOURCE_PRECONDITION_MISMATCH"
    return None


def _has_copy_operation(operations: tuple[PlanOperation, ...]) -> bool:
    return any(operation.operation_type is PlanOperationType.COPY_NEW for operation in operations)


def _operation_scope_from_run(
    *,
    run: StartedRun,
    plan: SealedPlan,
) -> tuple[set[str] | None, str | None]:
    raw_scope = run.summary.get("operation_ids")
    if raw_scope is None:
        return None, None
    if (
        not isinstance(raw_scope, list)
        or not 1 <= len(raw_scope) <= MAX_RUN_OPERATION_SCOPE
        or any(
            not isinstance(operation_id, str)
            or not operation_id.strip()
            or len(operation_id) > MAX_RUN_OPERATION_ID_LENGTH
            or operation_id != operation_id.strip()
            for operation_id in raw_scope
        )
    ):
        return None, "RUN_OPERATION_SCOPE_INVALID"
    operation_scope = set(raw_scope)
    if len(operation_scope) != len(raw_scope):
        return None, "RUN_OPERATION_SCOPE_INVALID"
    scoped_operations = tuple(
        operation
        for operation in plan.operations
        if operation.operation_id in operation_scope
    )
    if len(scoped_operations) != len(operation_scope) or any(
        operation.operation_type not in MUTATING_OPERATION_TYPES
        for operation in scoped_operations
    ):
        return None, "RUN_OPERATION_SCOPE_PLAN_MISMATCH"
    return operation_scope, None


def _failed(
    *,
    permit: MutationPermit,
    validation_code: str,
    next_action: str,
) -> RunTargetOperationPlanningOutcome:
    return RunTargetOperationPlanningOutcome(
        planned=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operations_planned=0,
        operations=(),
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _error_code(exc: ValueError) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc)
    if message.strip():
        return message
    return type(exc).__name__


def _error_next_action(exc: ValueError, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback
