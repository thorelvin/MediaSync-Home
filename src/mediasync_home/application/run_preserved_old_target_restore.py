from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.ports import (
    DirectoryMutationPreparationPort,
    OldTargetRestorePort,
    OldTargetRestoreReceipt,
)
from mediasync_home.application.directory_recovery import (
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryStore,
    DirectoryRecoveryTransition,
    directory_recovery_id,
    planned_directory_recovery_operation,
)
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
    directory_recovery_operations: DirectoryRecoveryStore | None = None,
    directory_mutation_preparation_port: (
        DirectoryMutationPreparationPort | None
    ) = None,
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
    precondition_error = _validate_restorable_precondition(operation)
    if precondition_error is not None:
        return _failed(
            permit=permit,
            operation=operation,
            receipt=None,
            validation_code=precondition_error[0],
            next_action=precondition_error[1],
        )

    directory_restore: DirectoryRecoveryOperation | None = None
    try:
        if (
            operation.target_precondition_kind
            is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY
            and directory_recovery_operations is not None
        ):
            directory_restore = _prepare_directory_restore(
                permit=permit,
                operation=operation,
                directory_recovery_operations=directory_recovery_operations,
                directory_mutation_preparation_port=(
                    directory_mutation_preparation_port
                ),
                process_instance_id=process_instance_id,
            )
        receipt = old_target_restore_port.restore_old_target(permit, operation)
        _validate_restore_receipt(operation=operation, receipt=receipt)
        if directory_restore is not None:
            directory_restore = _advance_directory_restore_to(
                directory_restore,
                target_index=4,
                directory_recovery_operations=directory_recovery_operations,
                process_instance_id=process_instance_id,
                payload={
                    "final_relative_path": receipt.final_relative_path.value,
                    "fingerprint_json": receipt.fingerprint_json,
                },
            )
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
    if directory_restore is not None:
        try:
            _advance_directory_restore_to(
                directory_restore,
                target_index=5,
                directory_recovery_operations=directory_recovery_operations,
                process_instance_id=process_instance_id,
                payload={
                    "generic_recovery_phase": restored.phase.value,
                    "restore_reason": "EXPLICIT_OLD_TARGET_RESTORE",
                },
            )
        except RuntimeError as exc:
            return _failed(
                permit=permit,
                operation=restored,
                receipt=receipt,
                validation_code=_error_code(exc),
                next_action=_error_next_action(
                    exc,
                    "Reconcile the restored directory catalog state before continuing.",
                ),
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


def _prepare_directory_restore(
    *,
    permit: MutationPermit,
    operation: RecoveryOperation,
    directory_recovery_operations: DirectoryRecoveryStore,
    directory_mutation_preparation_port: DirectoryMutationPreparationPort | None,
    process_instance_id: str,
) -> DirectoryRecoveryOperation:
    preparation = directory_mutation_preparation_port
    if preparation is None:
        raise RuntimeError("RUN_TARGET_DIRECTORY_RESTORE_PREPARATION_PORT_MISSING")
    recovery_id = directory_recovery_id(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        kind=DirectoryRecoveryKind.RESTORE,
    )
    directory_restore = directory_recovery_operations.record_directory_recovery_operation(
        planned_directory_recovery_operation(
            recovery_id=recovery_id,
            operation_id=operation.operation_id,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            target_endpoint_id=operation.target_endpoint_id,
            target_endpoint_revision_id=operation.target_endpoint_revision_id,
            owner_installation_id=operation.owner_installation_id,
            ownership_epoch=operation.ownership_epoch,
            kind=DirectoryRecoveryKind.RESTORE,
            final_relative_path=operation.final_relative_path,
            expected_precondition_json='{"kind":"ABSENT"}',
            desired_metadata_json=operation.expected_target_fingerprint_json,
        ),
        process_instance_id=process_instance_id,
        payload={
            "generic_recovery_phase": operation.phase.value,
            "quarantine_object_id": operation.quarantine_object_id,
        },
    )
    preparation_receipt = preparation.prepare_directory_restore(permit, operation)
    if (
        preparation_receipt.operation_id != operation.operation_id
        or _relative_path(preparation_receipt.final_relative_path.value)
        != _relative_path(operation.final_relative_path)
    ):
        raise RuntimeError("RUN_TARGET_DIRECTORY_RESTORE_PREPARATION_MISMATCH")
    return _advance_directory_restore_to(
        directory_restore,
        target_index=2,
        directory_recovery_operations=directory_recovery_operations,
        process_instance_id=process_instance_id,
        payload={
            "already_applied": preparation_receipt.already_applied,
            "observed_state": preparation_receipt.observed_state,
        },
        managed_object_id=preparation_receipt.managed_object_id,
    )


def _advance_directory_restore_to(
    operation: DirectoryRecoveryOperation,
    *,
    target_index: int,
    directory_recovery_operations: DirectoryRecoveryStore | None,
    process_instance_id: str,
    payload: dict[str, object],
    managed_object_id: str | None = None,
) -> DirectoryRecoveryOperation:
    if directory_recovery_operations is None:
        raise RuntimeError("RUN_TARGET_DIRECTORY_RESTORE_STORE_MISSING")
    success_path = SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.RESTORE]
    try:
        current_index = success_path.index(operation.state)
    except ValueError as exc:
        raise RuntimeError("RUN_TARGET_DIRECTORY_RESTORE_CONFLICTED") from exc
    while current_index < target_index:
        next_state = success_path[current_index + 1]
        updated = directory_recovery_operations.transition_directory_recovery_operation(
            DirectoryRecoveryTransition(
                recovery_id=operation.recovery_id,
                expected_state=operation.state,
                next_state=next_state,
                process_instance_id=process_instance_id,
                payload={**payload, "directory_state": next_state.value},
                managed_object_id=managed_object_id,
            )
        )
        if updated is None:
            raise RuntimeError("RUN_TARGET_DIRECTORY_RESTORE_PHASE_CONFLICT")
        operation = updated
        current_index += 1
    return operation


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


def _validate_restorable_precondition(operation: RecoveryOperation) -> tuple[str, str] | None:
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
        if operation.version_object_id is None or not operation.version_object_id.strip():
            return (
                "RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_REQUIRES_VERSION_OBJECT",
                "Recover or reconcile the preserved old target version object before restore.",
            )
        return None
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
        if operation.quarantine_object_id is None or not operation.quarantine_object_id.strip():
            return (
                "RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_REQUIRES_QUARANTINE_OBJECT",
                "Recover or reconcile the quarantined empty directory before restore.",
            )
        return None
    return (
        "RUN_TARGET_PRESERVED_OLD_TARGET_RESTORE_REQUIRES_RESTORABLE_PRECONDITION",
        "Restore old targets only for preserved versioned replacements or quarantined empty directories.",
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
