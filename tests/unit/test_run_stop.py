from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.recovery_operations import (
    TERMINAL_PHASES,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_stop import prepare_next_requested_run_stop
from mediasync_home.application.runs import RunStopRequest, RunTargetStopProgress


class _RunStops:
    def __init__(self, request: RunStopRequest | None) -> None:
        self.request = request
        self.activated = 0

    def load_next_requested_run_stop(self) -> RunStopRequest | None:
        return self.request

    def bind_requested_run_stop_boundary(
        self,
        *,
        run_id: str,
        run_target_id: str,
        operation_id: str,
    ) -> RunStopRequest | None:
        if self.request is None or self.request.run_id != run_id:
            return None
        self.request = replace(
            self.request,
            boundary_run_target_id=run_target_id,
            boundary_operation_id=operation_id,
        )
        return self.request

    def activate_requested_run_stop(self, run_id: str) -> object | None:
        if self.request is None or self.request.run_id != run_id:
            return None
        self.activated += 1
        return object()


class _RecoveryOperations:
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self.operations = {operation.operation_id: operation for operation in operations}

    def list_started_operations_for_run(
        self,
        *,
        run_id: str,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in self.operations.values()
            if operation.run_id == run_id
            and operation.phase is not RecoveryOperationPhase.PLANNED
            and operation.phase not in TERMINAL_PHASES
        )[:limit]

    def list_planned_operations_for_run(
        self,
        *,
        run_id: str,
        exclude_operation_id: str | None,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in self.operations.values()
            if operation.run_id == run_id
            and operation.phase is RecoveryOperationPhase.PLANNED
            and operation.operation_id != exclude_operation_id
        )[:limit]

    def summarize_successful_operations_for_run(
        self,
        run_id: str,
    ) -> tuple[RunTargetStopProgress, ...]:
        successful = tuple(
            operation
            for operation in self.operations.values()
            if operation.run_id == run_id
            and operation.phase in {
                RecoveryOperationPhase.CATALOG_RECORDED,
                RecoveryOperationPhase.CLEANED,
            }
        )
        if not successful:
            return ()
        return (
            RunTargetStopProgress(
                run_target_id="target-run-a",
                completed_operations=len(successful),
                completed_bytes=sum(operation.planned_bytes for operation in successful),
            ),
        )

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        operation = self.operations.get(operation_id)
        return operation if operation is not None and operation.run_id == run_id else None

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: object = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: RecoveryOperationMetadata | None = None,
    ) -> RecoveryOperation | None:
        operation = self.load_operation(run_id=run_id, operation_id=operation_id)
        if operation is None or operation.phase is not expected_phase:
            return None
        updated = replace(
            operation,
            phase=next_phase,
            last_error_code=(
                operation.last_error_code
                if operation_metadata is None or operation_metadata.last_error_code is None
                else operation_metadata.last_error_code
            ),
        )
        self.operations[operation_id] = updated
        return updated


def test_graceful_stop_binds_and_waits_for_the_started_file() -> None:
    stops = _RunStops(RunStopRequest(run_id="run-a"))
    recovery = _RecoveryOperations(
        (_operation("operation-a", phase=RecoveryOperationPhase.TRANSFERRED),)
    )

    bound = prepare_next_requested_run_stop(
        runs=stops,
        recovery_operations=recovery,
        process_instance_id="host-a",
    )
    waiting = prepare_next_requested_run_stop(
        runs=stops,
        recovery_operations=recovery,
        process_instance_id="host-a",
    )

    assert bound.advanced
    assert bound.boundary_operation_id == "operation-a"
    assert waiting.allow_execution
    assert not waiting.ready_to_finalize
    assert stops.activated == 2


def test_graceful_stop_cancels_only_untouched_operations_in_bounded_batches() -> None:
    stops = _RunStops(RunStopRequest(run_id="run-a"))
    recovery = _RecoveryOperations(
        tuple(_operation(f"operation-{index}") for index in range(3))
    )

    first = prepare_next_requested_run_stop(
        runs=stops,
        recovery_operations=recovery,
        process_instance_id="host-a",
        cancellation_batch_size=2,
    )
    second = prepare_next_requested_run_stop(
        runs=stops,
        recovery_operations=recovery,
        process_instance_id="host-a",
        cancellation_batch_size=2,
    )
    ready = prepare_next_requested_run_stop(
        runs=stops,
        recovery_operations=recovery,
        process_instance_id="host-a",
        cancellation_batch_size=2,
    )

    assert first.cancelled_operations == 2
    assert second.cancelled_operations == 1
    assert ready.ready_to_finalize
    assert all(
        operation.phase is RecoveryOperationPhase.CANCELLED
        for operation in recovery.operations.values()
    )


def test_graceful_stop_finalizes_after_the_bound_file_is_cataloged() -> None:
    stops = _RunStops(
        RunStopRequest(
            run_id="run-a",
            boundary_run_target_id="target-run-a",
            boundary_operation_id="operation-a",
        )
    )
    recovery = _RecoveryOperations(
        (_operation("operation-a", phase=RecoveryOperationPhase.CATALOG_RECORDED),)
    )

    outcome = prepare_next_requested_run_stop(
        runs=stops,
        recovery_operations=recovery,
        process_instance_id="host-a",
    )

    assert outcome.ready_to_finalize
    assert outcome.target_progress == (
        RunTargetStopProgress(
            run_target_id="target-run-a",
            completed_operations=1,
            completed_bytes=128,
        ),
    )


def _operation(
    operation_id: str,
    *,
    phase: RecoveryOperationPhase = RecoveryOperationPhase.PLANNED,
) -> RecoveryOperation:
    operation = planned_recovery_operation(
        run_id="run-a",
        run_target_id="target-run-a",
        operation_id=operation_id,
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path=f"{operation_id}.bin",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        operation_kind=RecoveryOperationKind.COPY_NEW,
        planned_bytes=128,
    )
    return replace(operation, phase=phase)
