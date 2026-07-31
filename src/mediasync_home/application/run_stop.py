from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.recovery_operations import (
    TERMINAL_PHASES,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.runs import (
    RunStopRequest,
    RunTargetStopProgress,
    StartedRun,
)


STOP_CANCELLATION_BATCH_SIZE = 100
USER_STOP_AFTER_ACTIVE_FILE_CODE = "USER_STOP_AFTER_ACTIVE_FILE"


class RunStopStore(Protocol):
    def load_next_requested_run_stop(self) -> RunStopRequest | None: ...

    def bind_requested_run_stop_boundary(
        self,
        *,
        run_id: str,
        run_target_id: str,
        operation_id: str,
    ) -> RunStopRequest | None: ...

    def activate_requested_run_stop(self, run_id: str) -> StartedRun | None: ...


class RunStopRecoveryOperationStore(RecoveryOperationStore, Protocol):
    def list_started_operations_for_run(
        self,
        *,
        run_id: str,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...

    def list_planned_operations_for_run(
        self,
        *,
        run_id: str,
        exclude_operation_id: str | None,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...

    def summarize_successful_operations_for_run(
        self,
        run_id: str,
    ) -> tuple[RunTargetStopProgress, ...]: ...


@dataclass(frozen=True)
class RunStopPreparationOutcome:
    run_id: str | None
    advanced: bool
    idle: bool
    allow_execution: bool
    ready_to_finalize: bool
    boundary_run_target_id: str | None
    boundary_operation_id: str | None
    cancelled_operations: int
    target_progress: tuple[RunTargetStopProgress, ...]
    validation_codes: tuple[str, ...]
    next_action: str


def prepare_next_requested_run_stop(
    *,
    runs: RunStopStore,
    recovery_operations: RunStopRecoveryOperationStore,
    process_instance_id: str,
    cancellation_batch_size: int = STOP_CANCELLATION_BATCH_SIZE,
) -> RunStopPreparationOutcome:
    request = runs.load_next_requested_run_stop()
    if request is None:
        return _outcome(
            run_id=None,
            idle=True,
            next_action="No graceful run stop is pending.",
        )
    if not process_instance_id.strip():
        return _blocked(
            request,
            "RUN_STOP_REQUIRES_PROCESS_INSTANCE",
            "Bind graceful stop processing to the Engine Host process instance.",
        )
    if cancellation_batch_size < 1:
        return _blocked(
            request,
            "RUN_STOP_REQUIRES_POSITIVE_CANCELLATION_BATCH",
            "Use a positive bounded cancellation batch.",
        )

    if request.boundary_operation_id is None:
        started = recovery_operations.list_started_operations_for_run(
            run_id=request.run_id,
            limit=2,
        )
        if len(started) > 1:
            return _blocked(
                request,
                "RUN_STOP_MULTIPLE_ACTIVE_OPERATIONS",
                "Reconcile concurrent active operations before stopping this run.",
            )
        if started:
            operation = started[0]
            bound = runs.bind_requested_run_stop_boundary(
                run_id=request.run_id,
                run_target_id=operation.run_target_id,
                operation_id=operation.operation_id,
            )
            if bound is None:
                return _blocked(
                    request,
                    "RUN_STOP_BOUNDARY_STATE_CONFLICT",
                    "Reload the graceful stop request before binding its active file.",
                )
            if runs.activate_requested_run_stop(request.run_id) is None:
                return _blocked(
                    bound,
                    "RUN_STOP_ACTIVATION_STATE_CONFLICT",
                    "Reload run state before continuing the active file.",
                )
            return _outcome(
                run_id=request.run_id,
                advanced=True,
                boundary_run_target_id=operation.run_target_id,
                boundary_operation_id=operation.operation_id,
                next_action="Graceful stop is bound to the active file.",
            )
        return _cancel_or_finalize(
            request=request,
            recovery_operations=recovery_operations,
            process_instance_id=process_instance_id,
            cancellation_batch_size=cancellation_batch_size,
        )

    boundary_operation = recovery_operations.load_operation(
        run_id=request.run_id,
        operation_id=request.boundary_operation_id,
    )
    if (
        boundary_operation is None
        or boundary_operation.run_target_id != request.boundary_run_target_id
    ):
        return _blocked(
            request,
            "RUN_STOP_BOUNDARY_OPERATION_MISSING",
            "Reconcile recovery state before continuing graceful stop.",
        )
    if not _boundary_is_safe(boundary_operation):
        if runs.activate_requested_run_stop(request.run_id) is None:
            return _blocked(
                request,
                "RUN_STOP_ACTIVATION_STATE_CONFLICT",
                "Reload run state before continuing the active file.",
            )
        return _outcome(
            run_id=request.run_id,
            allow_execution=True,
            boundary_run_target_id=request.boundary_run_target_id,
            boundary_operation_id=request.boundary_operation_id,
            next_action="Continue only the bound active file to its safe boundary.",
        )
    return _cancel_or_finalize(
        request=request,
        recovery_operations=recovery_operations,
        process_instance_id=process_instance_id,
        cancellation_batch_size=cancellation_batch_size,
    )


def _cancel_or_finalize(
    *,
    request: RunStopRequest,
    recovery_operations: RunStopRecoveryOperationStore,
    process_instance_id: str,
    cancellation_batch_size: int,
) -> RunStopPreparationOutcome:
    started = tuple(
        operation
        for operation in recovery_operations.list_started_operations_for_run(
            run_id=request.run_id,
            limit=2,
        )
        if operation.operation_id != request.boundary_operation_id
    )
    if started:
        return _blocked(
            request,
            "RUN_STOP_UNEXPECTED_ACTIVE_OPERATION",
            "Reconcile the additional active operation before finalizing graceful stop.",
        )

    planned = recovery_operations.list_planned_operations_for_run(
        run_id=request.run_id,
        exclude_operation_id=request.boundary_operation_id,
        limit=cancellation_batch_size,
    )
    for operation in planned:
        updated = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.PLANNED,
            next_phase=RecoveryOperationPhase.CANCELLED,
            process_instance_id=process_instance_id,
            payload={
                "reason": USER_STOP_AFTER_ACTIVE_FILE_CODE,
                "stop_mode": "AFTER_ACTIVE_FILE",
            },
            operation_metadata=RecoveryOperationMetadata(
                last_error_code=USER_STOP_AFTER_ACTIVE_FILE_CODE,
            ),
        )
        if updated is None:
            return _blocked(
                request,
                "RUN_STOP_CANCELLATION_STATE_CONFLICT",
                "Reload recovery state before continuing graceful stop.",
            )
    if planned:
        return _outcome(
            run_id=request.run_id,
            advanced=True,
            boundary_run_target_id=request.boundary_run_target_id,
            boundary_operation_id=request.boundary_operation_id,
            cancelled_operations=len(planned),
            next_action="Untouched planned files were cancelled in a bounded batch.",
        )

    return _outcome(
        run_id=request.run_id,
        ready_to_finalize=True,
        boundary_run_target_id=request.boundary_run_target_id,
        boundary_operation_id=request.boundary_operation_id,
        target_progress=recovery_operations.summarize_successful_operations_for_run(
            request.run_id
        ),
        next_action="The active file is safe and no untouched operation remains.",
    )


def _boundary_is_safe(operation: RecoveryOperation) -> bool:
    if operation.phase in TERMINAL_PHASES:
        return True
    if operation.phase is not RecoveryOperationPhase.CATALOG_RECORDED:
        return False
    if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
        return False
    return not (
        operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY
        and operation.quarantine_object_id is not None
        and bool(operation.quarantine_object_id.strip())
    )


def _blocked(
    request: RunStopRequest,
    validation_code: str,
    next_action: str,
) -> RunStopPreparationOutcome:
    return _outcome(
        run_id=request.run_id,
        boundary_run_target_id=request.boundary_run_target_id,
        boundary_operation_id=request.boundary_operation_id,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _outcome(
    *,
    run_id: str | None,
    advanced: bool = False,
    idle: bool = False,
    allow_execution: bool = False,
    ready_to_finalize: bool = False,
    boundary_run_target_id: str | None = None,
    boundary_operation_id: str | None = None,
    cancelled_operations: int = 0,
    target_progress: tuple[RunTargetStopProgress, ...] = (),
    validation_codes: tuple[str, ...] = (),
    next_action: str,
) -> RunStopPreparationOutcome:
    return RunStopPreparationOutcome(
        run_id=run_id,
        advanced=advanced,
        idle=idle,
        allow_execution=allow_execution,
        ready_to_finalize=ready_to_finalize,
        boundary_run_target_id=boundary_run_target_id,
        boundary_operation_id=boundary_operation_id,
        cancelled_operations=cancelled_operations,
        target_progress=target_progress,
        validation_codes=validation_codes,
        next_action=next_action,
    )
