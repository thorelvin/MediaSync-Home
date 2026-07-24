from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.recovery_operations import (
    PRE_COMMIT_LEASE_REBIND_PHASES,
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.domain.capabilities import MutationPermit


MAX_RUN_TARGET_OPERATION_LEASE_REBIND_SCAN = 1000


class RunTargetOperationLeaseRebindStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetOperationLeaseRebindOutcome:
    idle: bool
    rebound: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    operation: RecoveryOperation | None
    phase: RecoveryOperationPhase | None
    validation_codes: tuple[str, ...]
    next_action: str


def rebind_next_run_target_recovery_operation_lease(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetOperationLeaseRebindStore,
    process_instance_id: str,
    max_operations: int,
) -> RunTargetOperationLeaseRebindOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation=None,
            validation_code="RUN_TARGET_OPERATION_LEASE_REBIND_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind operation lease rebind to the Engine Host process instance.",
        )
    if max_operations < 1:
        return _failed(
            permit=permit,
            operation=None,
            validation_code="RUN_TARGET_OPERATION_LEASE_REBIND_REQUIRES_POSITIVE_LIMIT",
            next_action="Retry operation lease rebind with a positive bounded limit.",
        )
    if max_operations > MAX_RUN_TARGET_OPERATION_LEASE_REBIND_SCAN:
        return _failed(
            permit=permit,
            operation=None,
            validation_code="RUN_TARGET_OPERATION_LEASE_REBIND_LIMIT_TOO_LARGE",
            next_action="Retry operation lease rebind with a smaller bounded limit.",
        )

    operation = _next_stale_lease_operation(
        permit=permit,
        recovery_operations=recovery_operations,
        max_operations=max_operations,
    )
    if operation is None:
        return RunTargetOperationLeaseRebindOutcome(
            idle=True,
            rebound=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            operation=None,
            phase=None,
            validation_codes=(),
            next_action="No pre-commit recovery operation needs lease rebind.",
        )
    if not _operation_static_binding_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation=operation,
            validation_code="RUN_TARGET_OPERATION_LEASE_REBIND_PERMIT_MISMATCH",
            next_action="Reconcile recovery operation ownership before rebinding its lease.",
        )

    try:
        rebound = recovery_operations.record_operation_lease_rebound(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            expected_lease_id=operation.lease_id,
            expected_ownership_epoch=operation.ownership_epoch,
            expected_fencing_token=operation.fencing_token,
            lease_id=permit.lease_id,
            owner_installation_id=permit.owner_installation_id,
            ownership_epoch=permit.ownership_epoch,
            fencing_token=permit.fencing_token,
            process_instance_id=process_instance_id,
            payload={
                "new_fencing_token": permit.fencing_token,
                "new_lease_id": permit.lease_id,
                "old_fencing_token": operation.fencing_token,
                "old_lease_id": operation.lease_id,
            },
        )
    except ValueError as exc:
        return _failed(
            permit=permit,
            operation=operation,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reload recovery operation state before retrying operation lease rebind.",
            ),
        )
    if rebound is None:
        return _failed(
            permit=permit,
            operation=operation,
            validation_code="RUN_TARGET_OPERATION_LEASE_REBIND_CONFLICT",
            next_action="Reload recovery operation state before retrying operation lease rebind.",
        )

    return RunTargetOperationLeaseRebindOutcome(
        idle=False,
        rebound=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=rebound.operation_id,
        operation=rebound,
        phase=rebound.phase,
        validation_codes=(),
        next_action="Pre-commit recovery operation is rebound to the fresh endpoint lease.",
    )


def _next_stale_lease_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetOperationLeaseRebindStore,
    max_operations: int,
) -> RecoveryOperation | None:
    scanned = 0
    for phase in PRE_COMMIT_LEASE_REBIND_PHASES:
        remaining = max_operations - scanned
        if remaining < 1:
            return None
        operations = recovery_operations.list_operations_for_run_target_in_phase(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            phase=phase,
            limit=remaining,
        )
        scanned += len(operations)
        for operation in operations:
            if not _operation_static_binding_matches_permit(
                operation=operation,
                permit=permit,
            ) or not _operation_lease_matches_permit(operation=operation, permit=permit):
                return operation
    return None


def _operation_static_binding_matches_permit(
    *,
    operation: RecoveryOperation,
    permit: MutationPermit,
) -> bool:
    return (
        operation.phase in PRE_COMMIT_LEASE_REBIND_PHASES
        and operation.run_id == permit.run_id
        and operation.run_target_id == permit.run_target_id
        and operation.target_endpoint_id == permit.endpoint_id
        and operation.target_endpoint_revision_id == permit.endpoint_revision_id
        and operation.owner_installation_id == permit.owner_installation_id
        and operation.ownership_epoch == permit.ownership_epoch
        and operation.lease_resource_key == permit.resource_key
    )


def _operation_lease_matches_permit(
    *,
    operation: RecoveryOperation,
    permit: MutationPermit,
) -> bool:
    return operation.lease_id == permit.lease_id and operation.fencing_token == permit.fencing_token


def _failed(
    *,
    permit: MutationPermit,
    operation: RecoveryOperation | None,
    validation_code: str,
    next_action: str,
) -> RunTargetOperationLeaseRebindOutcome:
    return RunTargetOperationLeaseRebindOutcome(
        idle=False,
        rebound=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=None if operation is None else operation.operation_id,
        operation=operation,
        phase=None if operation is None else operation.phase,
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
