from __future__ import annotations

import json
from typing import Protocol

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
)
from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetCompletionOutcome,
    RunTargetState,
    RunWarningCompletionStore,
    StartedRun,
    StartedRunTarget,
    complete_run_target_cancelled,
    complete_run_target_recovery_required,
    complete_run_target_success,
    complete_run_target_with_warnings,
)
from mediasync_home.domain.capabilities import MutationPermit


RESTORED_OLD_TARGET_CANCEL_CODE = "RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED"
USER_DECISION_REQUIRED_CODE = "RUN_TARGET_USER_DECISION_REQUIRED"


class RunTargetCompletionOperationStore(Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


def complete_run_target_after_catalog_handoffs(
    *,
    permit: MutationPermit,
    runs: RunStore,
    recovery_operations: RunTargetCompletionOperationStore,
) -> RunTargetCompletionOutcome:
    run = runs.load_started_run(permit.run_id)
    if run is None:
        return _failed(
            permit=permit,
            run=None,
            target=None,
            validation_code="RUN_NOT_FOUND",
            next_action="Create and execute a run before completing target work.",
        )
    if run.state is not RunState.EXECUTING:
        return _failed(
            permit=permit,
            run=run,
            target=None,
            validation_code="RUN_NOT_EXECUTING",
            next_action="Only executing runs can complete target work.",
        )

    target = _target_by_id(run, permit.run_target_id)
    if target is None:
        return _failed(
            permit=permit,
            run=run,
            target=None,
            validation_code="RUN_TARGET_NOT_FOUND",
            next_action="Reload run targets before completing target work.",
        )
    if target.state is not RunTargetState.EXECUTING:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_NOT_EXECUTING",
            next_action="Only executing targets can be marked succeeded.",
        )
    if _target_permit_mismatch(target=target, permit=permit):
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_COMPLETION_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before completing target work.",
        )

    cataloged_operations = _cataloged_operations(
        permit=permit,
        target=target,
        recovery_operations=recovery_operations,
    )
    mismatch = next(
        (
            operation
            for operation in cataloged_operations
            if not _operation_matches_permit(operation=operation, permit=permit)
        ),
        None,
    )
    if mismatch is not None:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_COMPLETION_OPERATION_PERMIT_MISMATCH",
            next_action="Reconcile recovery operations before completing target work.",
        )

    completed_bytes = sum(
        _operation_byte_count(operation) for operation in cataloged_operations
    )
    return complete_run_target_success(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        runs=runs,
        completed_operations=len(cataloged_operations),
        completed_bytes=completed_bytes,
    )


def complete_run_target_after_terminal_recovery(
    *,
    permit: MutationPermit,
    runs: RunWarningCompletionStore,
    recovery_operations: RunTargetCompletionOperationStore,
) -> RunTargetCompletionOutcome:
    run = runs.load_started_run(permit.run_id)
    if run is None:
        return _failed(
            permit=permit,
            run=None,
            target=None,
            validation_code="RUN_NOT_FOUND",
            next_action="Create and execute a run before completing target work.",
        )
    if run.state is not RunState.EXECUTING:
        return _failed(
            permit=permit,
            run=run,
            target=None,
            validation_code="RUN_NOT_EXECUTING",
            next_action="Only executing runs can complete target work.",
        )

    target = _target_by_id(run, permit.run_target_id)
    if target is None:
        return _failed(
            permit=permit,
            run=run,
            target=None,
            validation_code="RUN_TARGET_NOT_FOUND",
            next_action="Reload run targets before completing target work.",
        )
    if target.state is not RunTargetState.EXECUTING:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_NOT_EXECUTING",
            next_action="Only executing targets can be completed from terminal recovery.",
        )
    if _target_permit_mismatch(target=target, permit=permit):
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_COMPLETION_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before completing target work.",
        )

    user_decision_operations = _terminal_operations(
        permit=permit,
        target=target,
        recovery_operations=recovery_operations,
        phase=RecoveryOperationPhase.USER_DECISION_REQUIRED,
    )
    mismatch = _terminal_operation_mismatch(
        operations=user_decision_operations,
        permit=permit,
        phase=RecoveryOperationPhase.USER_DECISION_REQUIRED,
    )
    if mismatch is not None:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_TERMINAL_RECOVERY_OPERATION_PERMIT_MISMATCH",
            next_action="Reconcile terminal recovery operations before completing target work.",
        )
    if user_decision_operations:
        return complete_run_target_recovery_required(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            runs=runs,
            last_error_code=_first_error_code(
                user_decision_operations,
                fallback=USER_DECISION_REQUIRED_CODE,
            ),
        )

    skipped_operations = _terminal_operations(
        permit=permit,
        target=target,
        recovery_operations=recovery_operations,
        phase=RecoveryOperationPhase.SKIPPED,
    )
    mismatch = _terminal_operation_mismatch(
        operations=skipped_operations,
        permit=permit,
        phase=RecoveryOperationPhase.SKIPPED,
    )
    if mismatch is not None:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_TERMINAL_RECOVERY_OPERATION_PERMIT_MISMATCH",
            next_action="Reconcile skipped recovery operations before completing target work.",
        )
    cataloged_operations = _cataloged_operations(
        permit=permit,
        target=target,
        recovery_operations=recovery_operations,
    )
    catalog_mismatch = next(
        (
            operation
            for operation in cataloged_operations
            if not _operation_matches_permit(operation=operation, permit=permit)
        ),
        None,
    )
    if catalog_mismatch is not None:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_COMPLETION_OPERATION_PERMIT_MISMATCH",
            next_action="Reconcile completed recovery operations before completing target work.",
        )
    if skipped_operations and (
        len(skipped_operations) + len(cataloged_operations) >= target.planned_operations
    ):
        return complete_run_target_with_warnings(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            runs=runs,
            completed_operations=len(cataloged_operations),
            completed_bytes=sum(
                _operation_byte_count(operation) for operation in cataloged_operations
            ),
            skipped_operations=len(skipped_operations),
            skipped_bytes=sum(
                operation.planned_bytes for operation in skipped_operations
            ),
            last_error_code=_first_error_code(
                skipped_operations,
                fallback="RUN_TARGET_STAGING_ATTEMPTS_EXHAUSTED",
            ),
        )

    cancelled_operations = _terminal_operations(
        permit=permit,
        target=target,
        recovery_operations=recovery_operations,
        phase=RecoveryOperationPhase.CANCELLED,
    )
    mismatch = _terminal_operation_mismatch(
        operations=cancelled_operations,
        permit=permit,
        phase=RecoveryOperationPhase.CANCELLED,
    )
    if mismatch is not None:
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_TERMINAL_RECOVERY_OPERATION_PERMIT_MISMATCH",
            next_action="Reconcile terminal recovery operations before completing target work.",
        )
    restored_old_target_cancellations = tuple(
        operation
        for operation in cancelled_operations
        if operation.last_error_code == RESTORED_OLD_TARGET_CANCEL_CODE
    )
    if len(restored_old_target_cancellations) != len(cancelled_operations):
        return _failed(
            permit=permit,
            run=run,
            target=target,
            validation_code="RUN_TARGET_TERMINAL_RECOVERY_CANCEL_REASON_UNSUPPORTED",
            next_action="Review the cancelled recovery operations before terminalizing the target.",
        )
    if restored_old_target_cancellations and (
        len(restored_old_target_cancellations) + len(cataloged_operations)
        >= target.planned_operations
    ):
        return complete_run_target_cancelled(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            runs=runs,
            last_error_code=RESTORED_OLD_TARGET_CANCEL_CODE,
        )

    return _failed(
        permit=permit,
        run=run,
        target=target,
        validation_code="RUN_TARGET_TERMINAL_RECOVERY_NOT_READY",
        next_action="Continue recovery operations before terminalizing the target.",
    )


def _cataloged_operations(
    *,
    permit: MutationPermit,
    target: StartedRunTarget,
    recovery_operations: RunTargetCompletionOperationStore,
) -> tuple[RecoveryOperation, ...]:
    if target.planned_operations == 0:
        return ()
    cataloged = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        limit=target.planned_operations + 1,
    )
    cleaned = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.CLEANED,
        limit=target.planned_operations + 1,
    )
    return tuple(
        sorted((*cataloged, *cleaned), key=lambda operation: operation.operation_id)
    )


def _terminal_operations(
    *,
    permit: MutationPermit,
    target: StartedRunTarget,
    recovery_operations: RunTargetCompletionOperationStore,
    phase: RecoveryOperationPhase,
) -> tuple[RecoveryOperation, ...]:
    if target.planned_operations == 0:
        return ()
    return recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=phase,
        limit=target.planned_operations + 1,
    )


def _target_permit_mismatch(
    *, target: StartedRunTarget, permit: MutationPermit
) -> bool:
    return (
        target.endpoint_id != permit.endpoint_id
        or target.endpoint_revision_id != permit.endpoint_revision_id
        or target.lease_resource_key != permit.resource_key
        or target.last_lease_id != permit.lease_id
        or target.last_ownership_epoch != permit.ownership_epoch
        or target.last_fencing_token != permit.fencing_token
        or target.required_owner_installation_id
        not in (None, permit.owner_installation_id)
        or target.required_ownership_epoch not in (None, permit.ownership_epoch)
    )


def _terminal_operation_mismatch(
    *,
    operations: tuple[RecoveryOperation, ...],
    permit: MutationPermit,
    phase: RecoveryOperationPhase,
) -> RecoveryOperation | None:
    return next(
        (
            operation
            for operation in operations
            if not _operation_matches_terminal_permit(
                operation=operation,
                permit=permit,
                phase=phase,
            )
        ),
        None,
    )


def _operation_matches_permit(
    *, operation: RecoveryOperation, permit: MutationPermit
) -> bool:
    return (
        operation.phase
        in {RecoveryOperationPhase.CATALOG_RECORDED, RecoveryOperationPhase.CLEANED}
        and operation.run_id == permit.run_id
        and operation.run_target_id == permit.run_target_id
        and operation.target_endpoint_id == permit.endpoint_id
        and operation.target_endpoint_revision_id == permit.endpoint_revision_id
        and operation.owner_installation_id == permit.owner_installation_id
        and operation.ownership_epoch == permit.ownership_epoch
        and operation.lease_resource_key == permit.resource_key
    )


def _operation_matches_terminal_permit(
    *,
    operation: RecoveryOperation,
    permit: MutationPermit,
    phase: RecoveryOperationPhase,
) -> bool:
    return (
        operation.phase is phase
        and operation.run_id == permit.run_id
        and operation.run_target_id == permit.run_target_id
        and operation.target_endpoint_id == permit.endpoint_id
        and operation.target_endpoint_revision_id == permit.endpoint_revision_id
        and operation.owner_installation_id == permit.owner_installation_id
        and operation.ownership_epoch == permit.ownership_epoch
        and operation.lease_resource_key == permit.resource_key
    )


def _first_error_code(
    operations: tuple[RecoveryOperation, ...],
    *,
    fallback: str,
) -> str:
    for operation in operations:
        if operation.last_error_code is not None and operation.last_error_code.strip():
            return operation.last_error_code.strip()
    return fallback


def _operation_byte_count(operation: RecoveryOperation) -> int:
    for raw_payload in (
        operation.expected_final_fingerprint_json,
        operation.expected_staging_fingerprint_json,
    ):
        if raw_payload is None:
            continue
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        byte_count = payload.get("byte_count")
        if isinstance(byte_count, int) and byte_count >= 0:
            return byte_count
    return 0


def _target_by_id(run: StartedRun, run_target_id: str) -> StartedRunTarget | None:
    return next(
        (target for target in run.targets if target.run_target_id == run_target_id),
        None,
    )


def _failed(
    *,
    permit: MutationPermit,
    run: StartedRun | None,
    target: StartedRunTarget | None,
    validation_code: str,
    next_action: str,
) -> RunTargetCompletionOutcome:
    return RunTargetCompletionOutcome(
        completed=False,
        run_completed=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        run=run,
        target=target,
        validation_codes=(validation_code,),
        next_action=next_action,
    )
