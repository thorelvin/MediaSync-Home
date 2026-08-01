from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.ports import (
    RecoveryObjectCleanupPort,
    RecoveryObjectCleanupReceipt,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.domain.capabilities import MutationPermit


class RunTargetRecoveryObjectCleanupOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetRecoveryObjectCleanupOutcome:
    idle: bool
    cleaned: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    receipt: RecoveryObjectCleanupReceipt | None
    validation_codes: tuple[str, ...]
    next_action: str


def cleanup_next_run_target_recovery_object(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetRecoveryObjectCleanupOperationStore,
    cleanup_port: RecoveryObjectCleanupPort,
    process_instance_id: str,
    max_operations: int = 100,
) -> RunTargetRecoveryObjectCleanupOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation=None,
            receipt=None,
            validation_code="RUN_TARGET_RECOVERY_OBJECT_CLEANUP_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind recovery-object cleanup to the Engine Host process instance.",
        )
    if max_operations < 1:
        return _failed(
            permit=permit,
            operation=None,
            receipt=None,
            validation_code="RUN_TARGET_RECOVERY_OBJECT_CLEANUP_REQUIRES_OPERATION_LIMIT",
            next_action="Run cleanup with a positive bounded operation limit.",
        )

    operation = _next_cleanup_operation(
        permit=permit,
        recovery_operations=recovery_operations,
        limit=max_operations,
    )
    if operation is None:
        return RunTargetRecoveryObjectCleanupOutcome(
            idle=True,
            cleaned=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            receipt=None,
            validation_codes=(),
            next_action="No catalog-recorded recovery object is eligible for cleanup.",
        )
    if not _operation_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code="RUN_TARGET_RECOVERY_OBJECT_CLEANUP_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before cleaning recovery objects.",
        )

    try:
        receipt = cleanup_port.cleanup_recovery_objects(permit, operation)
        _validate_cleanup_receipt(operation=operation, receipt=receipt)
    except RuntimeError as exc:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reconcile recovery objects before retrying cleanup.",
            ),
        )

    cleaned = recovery_operations.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=RecoveryOperationPhase.CATALOG_RECORDED,
        next_phase=RecoveryOperationPhase.CLEANED,
        process_instance_id=process_instance_id,
        payload={
            "cleaned_object_ids": list(receipt.cleaned_object_ids),
            "final_relative_path": receipt.final_relative_path.value,
        },
    )
    if cleaned is None:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=receipt,
            validation_code="RUN_TARGET_RECOVERY_OBJECT_CLEANUP_PHASE_CONFLICT",
            next_action="Reload recovery state before retrying recovery-object cleanup.",
        )
    return RunTargetRecoveryObjectCleanupOutcome(
        idle=False,
        cleaned=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=cleaned.operation_id,
        receipt=receipt,
        validation_codes=(),
        next_action="Catalog-recorded recovery object cleanup is journaled.",
    )


def _next_cleanup_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetRecoveryObjectCleanupOperationStore,
    limit: int,
) -> RecoveryOperation | None:
    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        limit=limit,
    )
    return next((_operation for _operation in operations if _requires_cleanup(_operation)), None)


def _requires_cleanup(operation: RecoveryOperation) -> bool:
    if operation.staging_object_id is not None and operation.staging_object_id.strip():
        return True
    if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
        return True
    return False


def _operation_matches_permit(*, operation: RecoveryOperation, permit: MutationPermit) -> bool:
    return (
        operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        and operation.run_id == permit.run_id
        and operation.run_target_id == permit.run_target_id
        and operation.target_endpoint_id == permit.endpoint_id
        and operation.target_endpoint_revision_id == permit.endpoint_revision_id
        and operation.owner_installation_id == permit.owner_installation_id
        and operation.ownership_epoch == permit.ownership_epoch
        and operation.lease_id == permit.lease_id
        and operation.lease_resource_key == permit.resource_key
        and operation.fencing_token == permit.fencing_token
    )


def _validate_cleanup_receipt(
    *,
    operation: RecoveryOperation,
    receipt: RecoveryObjectCleanupReceipt,
) -> None:
    if receipt.operation_id != operation.operation_id:
        raise RuntimeError("RUN_TARGET_RECOVERY_OBJECT_CLEANUP_RECEIPT_OPERATION_MISMATCH")
    if _relative_path(receipt.final_relative_path.value) != _relative_path(operation.final_relative_path):
        raise RuntimeError("RUN_TARGET_RECOVERY_OBJECT_CLEANUP_RECEIPT_PATH_MISMATCH")
    expected_object_ids = {
        object_id
        for object_id in (
            operation.staging_object_id,
            operation.operation_id
            if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY
            else None,
        )
        if object_id is not None and object_id.strip()
    }
    if not expected_object_ids.issubset(receipt.cleaned_object_ids):
        raise RuntimeError("RUN_TARGET_RECOVERY_OBJECT_CLEANUP_RECEIPT_OBJECT_MISMATCH")


def _failed(
    *,
    permit: MutationPermit,
    operation: RecoveryOperation | None,
    receipt: RecoveryObjectCleanupReceipt | None,
    validation_code: str,
    next_action: str,
) -> RunTargetRecoveryObjectCleanupOutcome:
    return RunTargetRecoveryObjectCleanupOutcome(
        idle=False,
        cleaned=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=None if operation is None else operation.operation_id,
        receipt=receipt,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _error_code(exc: RuntimeError) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc)
    if message.strip():
        return message
    return type(exc).__name__


def _error_next_action(exc: RuntimeError, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback


def _relative_path(value: str) -> str:
    return value.replace("\\", "/")
