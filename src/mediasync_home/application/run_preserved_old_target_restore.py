from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.ports import OldTargetRestorePort, OldTargetRestoreReceipt
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.domain.capabilities import MutationPermit


class RunTargetPreservedOldTargetRestoreOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetPreservedOldTargetRestoreOutcome:
    idle: bool
    restored: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    receipt: OldTargetRestoreReceipt | None
    operation: RecoveryOperation | None
    validation_codes: tuple[str, ...]
    next_action: str


def restore_next_run_target_preserved_old_target(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetPreservedOldTargetRestoreOperationStore,
    old_target_restore_port: OldTargetRestorePort,
    process_instance_id: str,
) -> RunTargetPreservedOldTargetRestoreOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation=None,
            receipt=None,
            validation_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind old-target restore to the Engine Host process instance.",
        )

    operation = _next_preserved_operation(
        permit=permit,
        recovery_operations=recovery_operations,
    )
    if operation is None:
        return RunTargetPreservedOldTargetRestoreOutcome(
            idle=True,
            restored=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            receipt=None,
            operation=None,
            validation_codes=(),
            next_action="No preserved old target is waiting for explicit restore.",
        )
    if not _operation_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before restoring old target bytes.",
        )
    if operation.target_precondition_kind is not RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_REQUIRES_MATCH_FINGERPRINT",
            next_action="Restore old target bytes only for preserved versioned replacements.",
        )
    if operation.version_object_id is None and operation.quarantine_object_id is None:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_REQUIRES_PRESERVED_OBJECT",
            next_action="Recover or reconcile the preserved old target object before restore.",
        )

    try:
        receipt = old_target_restore_port.restore_old_target(permit, operation)
        _validate_restore_receipt(operation=operation, receipt=receipt)
    except RuntimeError as exc:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Recover or reconcile the preserved old target before retrying restore.",
            ),
        )

    restored = recovery_operations.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        next_phase=RecoveryOperationPhase.CANCELLED,
        process_instance_id=process_instance_id,
        payload={
            "final_relative_path": receipt.final_relative_path.value,
            "fingerprint_json": receipt.fingerprint_json,
            "restore_reason": "EXPLICIT_OLD_TARGET_RESTORE",
        },
        operation_metadata=RecoveryOperationMetadata(
            last_error_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED",
        ),
    )
    if restored is None:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=receipt,
            validation_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_PHASE_CONFLICT",
            next_action="Reload recovery state before retrying old-target restore.",
        )
    return RunTargetPreservedOldTargetRestoreOutcome(
        idle=False,
        restored=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=restored.operation_id,
        receipt=receipt,
        operation=restored,
        validation_codes=(),
        next_action="Preserved old target bytes are restored and the replacement operation is cancelled.",
    )


def _next_preserved_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetPreservedOldTargetRestoreOperationStore,
) -> RecoveryOperation | None:
    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        limit=1,
    )
    if not operations:
        return None
    return operations[0]


def _operation_matches_permit(*, operation: RecoveryOperation, permit: MutationPermit) -> bool:
    return (
        operation.phase is RecoveryOperationPhase.OLD_TARGET_PRESERVED
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


def _validate_restore_receipt(
    *,
    operation: RecoveryOperation,
    receipt: OldTargetRestoreReceipt,
) -> None:
    if receipt.operation_id != operation.operation_id:
        raise RuntimeError("RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_RECEIPT_OPERATION_MISMATCH")
    if _relative_path(receipt.final_relative_path.value) != _relative_path(operation.final_relative_path):
        raise RuntimeError("RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_RECEIPT_PATH_MISMATCH")


def _failed(
    *,
    permit: MutationPermit,
    operation: RecoveryOperation | None,
    receipt: OldTargetRestoreReceipt | None,
    validation_code: str,
    next_action: str,
) -> RunTargetPreservedOldTargetRestoreOutcome:
    return RunTargetPreservedOldTargetRestoreOutcome(
        idle=False,
        restored=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=None if operation is None else operation.operation_id,
        receipt=receipt,
        operation=operation,
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
