from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.catalog_handoff import FinalFileCatalogHandoffStore
from mediasync_home.application.plans import PlanStore
from mediasync_home.application.ports import (
    FinalCommitPort,
    OldTargetPreservationPort,
    RecoveryObjectCleanupPort,
)
from mediasync_home.application.recovery_intents import RecoveryIntentSegmentStore
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
)
from mediasync_home.application.run_catalog_handoffs import (
    record_next_run_target_catalog_handoff,
)
from mediasync_home.application.run_completion import (
    complete_run_target_after_catalog_handoffs,
    complete_run_target_after_terminal_recovery,
)
from mediasync_home.application.run_commit_intent_refresh import (
    refresh_next_run_target_commit_intent_for_fresh_lease,
)
from mediasync_home.application.run_executor import (
    MAX_RUN_EXECUTOR_PUMP_STEPS,
    RunExecutorPumpStopReason,
    RunExecutorQueueStore,
    RunExecutorViolation,
    RunTargetLeaseRegistry,
    execute_bounded_run_executor_preflight_pump,
    execute_one_executing_run_target_lease_reacquire_step,
    execute_one_run_target_execution_start_step,
)
from mediasync_home.application.run_final_commits import (
    commit_next_run_target_verified_artifact,
)
from mediasync_home.application.run_intent_segments import (
    publish_run_target_recovery_intent_segment,
)
from mediasync_home.application.run_operation_lease_rebind import (
    rebind_next_run_target_recovery_operation_lease,
)
from mediasync_home.application.run_operation_planning import (
    plan_run_target_recovery_operations,
)
from mediasync_home.application.run_preserved_commit_refresh import (
    refresh_next_run_target_preserved_commit_intent_for_fresh_lease,
)
from mediasync_home.application.run_recovery_object_cleanup import (
    cleanup_next_run_target_recovery_object,
)
from mediasync_home.application.run_staging import (
    RunTargetStagingOperationStore,
    RunTargetStagingPort,
    execute_next_run_target_staging_step,
)
from mediasync_home.application.run_stop import (
    RunStopRecoveryOperationStore,
    prepare_next_requested_run_stop,
)
from mediasync_home.application.runs import (
    EndpointLeaseAuthority,
    RunState,
    RunTargetState,
    RunWarningCompletionStore,
    StartedRun,
    StartedRunTarget,
)
from mediasync_home.domain.capabilities import MutationPermit


class RunExecutorCycleAction(str, Enum):
    RUN_STOP_BOUNDARY_BOUND = "RUN_STOP_BOUNDARY_BOUND"
    RUN_STOP_CANCELLATION_ADVANCED = "RUN_STOP_CANCELLATION_ADVANCED"
    RUN_STOPPED = "RUN_STOPPED"
    RUN_PAUSED = "RUN_PAUSED"
    PREFLIGHT_LEASE_ACQUIRED = "PREFLIGHT_LEASE_ACQUIRED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    OPERATIONS_PLANNED = "OPERATIONS_PLANNED"
    INTENT_PUBLISHED = "INTENT_PUBLISHED"
    FINAL_COMMITTED = "FINAL_COMMITTED"
    CATALOG_HANDOFF_RECORDED = "CATALOG_HANDOFF_RECORDED"
    RECOVERY_OBJECT_CLEANED = "RECOVERY_OBJECT_CLEANED"
    TARGET_COMPLETED = "TARGET_COMPLETED"
    TARGET_CANCELLED = "TARGET_CANCELLED"
    TARGET_RECOVERY_REQUIRED = "TARGET_RECOVERY_REQUIRED"
    TARGET_WAITING_FOR_ENDPOINT = "TARGET_WAITING_FOR_ENDPOINT"
    EXECUTING_LEASE_REACQUIRED = "EXECUTING_LEASE_REACQUIRED"
    OPERATION_LEASE_REBOUND = "OPERATION_LEASE_REBOUND"
    COMMIT_INTENT_REFRESHED = "COMMIT_INTENT_REFRESHED"
    STAGING_ADVANCED = "STAGING_ADVANCED"
    WAITING_FOR_STAGING = "WAITING_FOR_STAGING"
    IDLE = "IDLE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RunExecutorCycleOutcome:
    action: RunExecutorCycleAction
    advanced: bool
    idle: bool
    run_id: str | None
    run_target_id: str | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class RunExecutorCyclePumpOutcome:
    steps_attempted: int
    stopped_reason: RunExecutorPumpStopReason
    last_step: RunExecutorCycleOutcome | None
    validation_codes: tuple[str, ...]
    next_action: str


class RunExecutorCycleRecoveryOperationStore(
    RunStopRecoveryOperationStore,
    RunTargetStagingOperationStore,
    Protocol,
):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


class RunExecutorCycleRunStore(
    RunExecutorQueueStore, RunWarningCompletionStore, Protocol
):
    pass


EARLY_OPERATION_PHASES = (
    RecoveryOperationPhase.PLANNED,
    RecoveryOperationPhase.SOURCE_VALIDATED,
    RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
    RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
    RecoveryOperationPhase.STAGING_ALLOCATED,
    RecoveryOperationPhase.TRANSFERRED,
    RecoveryOperationPhase.STAGING_DURABLE,
)


def execute_bounded_run_executor_cycle(
    *,
    runs: RunExecutorCycleRunStore,
    leases: EndpointLeaseAuthority,
    lease_registry: RunTargetLeaseRegistry,
    plans: PlanStore,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    intent_segments: RecoveryIntentSegmentStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
    max_steps: int,
    final_commit_port: FinalCommitPort | None = None,
    old_target_preservation_port: OldTargetPreservationPort | None = None,
    recovery_object_cleanup_port: RecoveryObjectCleanupPort | None = None,
    staging_transfer_port: RunTargetStagingPort | None = None,
) -> RunExecutorCyclePumpOutcome:
    if max_steps < 1:
        raise RunExecutorViolation("RUN_EXECUTOR_CYCLE_REQUIRES_POSITIVE_STEP_LIMIT")
    if max_steps > MAX_RUN_EXECUTOR_PUMP_STEPS:
        raise RunExecutorViolation("RUN_EXECUTOR_CYCLE_STEP_LIMIT_TOO_LARGE")

    runs.requeue_next_due_waiting_run_target()
    last_step: RunExecutorCycleOutcome | None = None
    for step_index in range(1, max_steps + 1):
        last_step = execute_one_run_executor_cycle(
            runs=runs,
            leases=leases,
            lease_registry=lease_registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            process_instance_id=process_instance_id,
            final_commit_port=final_commit_port,
            old_target_preservation_port=old_target_preservation_port,
            recovery_object_cleanup_port=recovery_object_cleanup_port,
            staging_transfer_port=staging_transfer_port,
        )
        if last_step.idle:
            return RunExecutorCyclePumpOutcome(
                steps_attempted=step_index,
                stopped_reason=RunExecutorPumpStopReason.IDLE,
                last_step=last_step,
                validation_codes=(),
                next_action=last_step.next_action,
            )
        if not last_step.advanced:
            return RunExecutorCyclePumpOutcome(
                steps_attempted=step_index,
                stopped_reason=RunExecutorPumpStopReason.BLOCKED,
                last_step=last_step,
                validation_codes=last_step.validation_codes,
                next_action=last_step.next_action,
            )

    return RunExecutorCyclePumpOutcome(
        steps_attempted=max_steps,
        stopped_reason=RunExecutorPumpStopReason.STEP_LIMIT_REACHED,
        last_step=last_step,
        validation_codes=(),
        next_action="Run executor cycle reached its configured step limit.",
    )


def execute_one_run_executor_cycle(
    *,
    runs: RunExecutorCycleRunStore,
    leases: EndpointLeaseAuthority,
    lease_registry: RunTargetLeaseRegistry,
    plans: PlanStore,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    intent_segments: RecoveryIntentSegmentStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
    final_commit_port: FinalCommitPort | None = None,
    old_target_preservation_port: OldTargetPreservationPort | None = None,
    recovery_object_cleanup_port: RecoveryObjectCleanupPort | None = None,
    staging_transfer_port: RunTargetStagingPort | None = None,
) -> RunExecutorCycleOutcome:
    stopping = (
        prepare_next_requested_run_stop(
            runs=runs,
            recovery_operations=recovery_operations,
            process_instance_id=process_instance_id,
        )
        if hasattr(runs, "load_next_requested_run_stop")
        and hasattr(recovery_operations, "list_started_operations_for_run")
        else None
    )
    if stopping is not None and not stopping.idle:
        if stopping.ready_to_finalize and stopping.run_id is not None:
            for run_id, run_target_id in lease_registry.retained_run_target_keys():
                if run_id == stopping.run_id:
                    lease_registry.release_retained_run_target_lease(
                        run_id=run_id,
                        run_target_id=run_target_id,
                    )
            stopped = runs.finalize_requested_run_stop(
                run_id=stopping.run_id,
                target_progress=stopping.target_progress,
            )
            if stopped is None:
                return _blocked(
                    run_id=stopping.run_id,
                    run_target_id=stopping.boundary_run_target_id,
                    validation_codes=("RUN_STOP_FINALIZE_STATE_CONFLICT",),
                    next_action="Reload run state before finalizing graceful stop.",
                )
            return _advanced(
                action=RunExecutorCycleAction.RUN_STOPPED,
                run_id=stopped.run_id,
                run_target_id=stopping.boundary_run_target_id,
                next_action="Run stopped after the active file reached a safe boundary.",
            )
        if stopping.advanced:
            return _advanced(
                action=(
                    RunExecutorCycleAction.RUN_STOP_CANCELLATION_ADVANCED
                    if stopping.cancelled_operations
                    else RunExecutorCycleAction.RUN_STOP_BOUNDARY_BOUND
                ),
                run_id=stopping.run_id,
                run_target_id=stopping.boundary_run_target_id,
                next_action=stopping.next_action,
            )
        if stopping.validation_codes:
            return _blocked(
                run_id=stopping.run_id,
                run_target_id=stopping.boundary_run_target_id,
                validation_codes=stopping.validation_codes,
                next_action=stopping.next_action,
            )

    pausing = runs.load_next_pausing_run()
    if pausing is not None:
        for run_id, run_target_id in lease_registry.retained_run_target_keys():
            if run_id == pausing.run_id:
                lease_registry.release_retained_run_target_lease(
                    run_id=run_id,
                    run_target_id=run_target_id,
                )
        paused = runs.finalize_requested_run_pause(pausing.run_id)
        if paused is None:
            return _blocked(
                run_id=pausing.run_id,
                run_target_id=None,
                validation_codes=("RUN_PAUSE_BOUNDARY_STATE_CONFLICT",),
                next_action="Reload run state before retrying the pause boundary.",
            )
        return _advanced(
            action=RunExecutorCycleAction.RUN_PAUSED,
            run_id=paused.run_id,
            run_target_id=None,
            next_action="Run paused at a safe operation boundary and released endpoint leases.",
        )

    retained = _next_retained_executing_target(
        runs=runs,
        lease_registry=lease_registry,
    )
    if retained is not None:
        return _advance_retained_target(
            runs=runs,
            lease_registry=lease_registry,
            plans=plans,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            catalog_handoffs=catalog_handoffs,
            process_instance_id=process_instance_id,
            final_commit_port=final_commit_port,
            old_target_preservation_port=old_target_preservation_port,
            recovery_object_cleanup_port=recovery_object_cleanup_port,
            staging_transfer_port=staging_transfer_port,
            retained=retained,
        )

    reacquired = execute_one_executing_run_target_lease_reacquire_step(
        runs=runs,
        leases=leases,
        lease_registry=lease_registry,
    )
    if not reacquired.idle:
        if reacquired.reacquired:
            return _advanced(
                action=RunExecutorCycleAction.EXECUTING_LEASE_REACQUIRED,
                run_id=reacquired.run_id,
                run_target_id=reacquired.run_target_id,
                next_action=reacquired.next_action,
            )
        if _target_is_waiting_for_endpoint(reacquired.target):
            return _advanced(
                action=RunExecutorCycleAction.TARGET_WAITING_FOR_ENDPOINT,
                run_id=reacquired.run_id,
                run_target_id=reacquired.run_target_id,
                next_action=reacquired.next_action,
            )
        return _blocked(
            run_id=reacquired.run_id,
            run_target_id=reacquired.run_target_id,
            validation_codes=reacquired.validation_codes,
            next_action=reacquired.next_action,
        )

    execution = execute_one_run_target_execution_start_step(
        runs=runs,
        lease_registry=lease_registry,
        leases=leases,
    )
    if not execution.idle:
        if execution.execution_started:
            return RunExecutorCycleOutcome(
                action=RunExecutorCycleAction.EXECUTION_STARTED,
                advanced=True,
                idle=False,
                run_id=execution.run_id,
                run_target_id=execution.run_target_id,
                validation_codes=(),
                next_action=execution.next_action,
            )
        if _target_is_waiting_for_endpoint(execution.target):
            return _advanced(
                action=RunExecutorCycleAction.TARGET_WAITING_FOR_ENDPOINT,
                run_id=execution.run_id,
                run_target_id=execution.run_target_id,
                next_action=execution.next_action,
            )
        return _blocked(
            run_id=execution.run_id,
            run_target_id=execution.run_target_id,
            validation_codes=execution.validation_codes,
            next_action=execution.next_action,
        )

    preflight = execute_bounded_run_executor_preflight_pump(
        runs=runs,
        leases=leases,
        lease_registry=lease_registry,
        max_steps=1,
    )
    if preflight.last_step is not None and preflight.last_step.lease_acquired:
        return RunExecutorCycleOutcome(
            action=RunExecutorCycleAction.PREFLIGHT_LEASE_ACQUIRED,
            advanced=True,
            idle=False,
            run_id=preflight.last_step.run_id,
            run_target_id=preflight.last_step.run_target_id,
            validation_codes=(),
            next_action=preflight.last_step.next_action,
        )
    if preflight.last_step is not None and _target_is_waiting_for_endpoint(
        preflight.last_step.target
    ):
        return _advanced(
            action=RunExecutorCycleAction.TARGET_WAITING_FOR_ENDPOINT,
            run_id=preflight.last_step.run_id,
            run_target_id=preflight.last_step.run_target_id,
            next_action=preflight.last_step.next_action,
        )
    if preflight.stopped_reason is RunExecutorPumpStopReason.IDLE:
        return RunExecutorCycleOutcome(
            action=RunExecutorCycleAction.IDLE,
            advanced=False,
            idle=True,
            run_id=None,
            run_target_id=None,
            validation_codes=(),
            next_action=preflight.next_action,
        )
    return _blocked(
        run_id=None if preflight.last_step is None else preflight.last_step.run_id,
        run_target_id=None
        if preflight.last_step is None
        else preflight.last_step.run_target_id,
        validation_codes=preflight.validation_codes,
        next_action=preflight.next_action,
    )


def _target_is_waiting_for_endpoint(target: StartedRunTarget | None) -> bool:
    return target is not None and target.state is RunTargetState.WAITING_FOR_ENDPOINT


@dataclass(frozen=True)
class _RetainedTarget:
    run: StartedRun
    target: StartedRunTarget
    permit: MutationPermit


def _advance_retained_target(
    *,
    runs: RunExecutorCycleRunStore,
    lease_registry: RunTargetLeaseRegistry,
    plans: PlanStore,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    intent_segments: RecoveryIntentSegmentStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
    final_commit_port: FinalCommitPort | None,
    old_target_preservation_port: OldTargetPreservationPort | None,
    recovery_object_cleanup_port: RecoveryObjectCleanupPort | None,
    staging_transfer_port: RunTargetStagingPort | None,
    retained: _RetainedTarget,
) -> RunExecutorCycleOutcome:
    permit = retained.permit
    target = retained.target

    if _terminal_recovery_ready(
        recovery_operations=recovery_operations,
        permit=permit,
        target=target,
    ):
        completion_outcome = complete_run_target_after_terminal_recovery(
            permit=permit,
            runs=runs,
            recovery_operations=recovery_operations,
        )
        if completion_outcome.completed:
            lease_registry.release_retained_run_target_lease(
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
            )
            return _advanced(
                action=_terminal_completion_action(completion_outcome.target),
                run_id=completion_outcome.run_id,
                run_target_id=completion_outcome.run_target_id,
                next_action=completion_outcome.next_action,
            )
        return _blocked(
            run_id=completion_outcome.run_id,
            run_target_id=completion_outcome.run_target_id,
            validation_codes=completion_outcome.validation_codes,
            next_action=completion_outcome.next_action,
        )

    if _has_phase(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        limit=target.planned_operations + 1,
    ):
        catalog_outcome = record_next_run_target_catalog_handoff(
            permit=permit,
            recovery_operations=recovery_operations,
            catalog_handoffs=catalog_handoffs,
            process_instance_id=process_instance_id,
        )
        if catalog_outcome.recorded:
            return _advanced(
                action=RunExecutorCycleAction.CATALOG_HANDOFF_RECORDED,
                run_id=catalog_outcome.run_id,
                run_target_id=catalog_outcome.run_target_id,
                next_action=catalog_outcome.next_action,
            )
        return _blocked(
            run_id=catalog_outcome.run_id,
            run_target_id=catalog_outcome.run_target_id,
            validation_codes=catalog_outcome.validation_codes,
            next_action=catalog_outcome.next_action,
        )

    if _has_phase(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        limit=target.planned_operations + 1,
    ):
        preserved_refresh_outcome = (
            refresh_next_run_target_preserved_commit_intent_for_fresh_lease(
                permit=permit,
                recovery_operations=recovery_operations,
                intent_segments=intent_segments,
                process_instance_id=process_instance_id,
                max_operations=target.planned_operations + 1,
            )
        )
        if not preserved_refresh_outcome.idle:
            if preserved_refresh_outcome.refreshed:
                return _advanced(
                    action=RunExecutorCycleAction.COMMIT_INTENT_REFRESHED,
                    run_id=preserved_refresh_outcome.run_id,
                    run_target_id=preserved_refresh_outcome.run_target_id,
                    next_action=preserved_refresh_outcome.next_action,
                )
            return _blocked(
                run_id=preserved_refresh_outcome.run_id,
                run_target_id=preserved_refresh_outcome.run_target_id,
                validation_codes=preserved_refresh_outcome.validation_codes,
                next_action=preserved_refresh_outcome.next_action,
            )
        if final_commit_port is None:
            return _blocked(
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                validation_codes=("RUN_EXECUTOR_FINAL_COMMIT_PORT_NOT_CONFIGURED",),
                next_action="Configure a final commit port before applying verified artifacts.",
            )
        final_commit_outcome = commit_next_run_target_verified_artifact(
            permit=permit,
            recovery_operations=recovery_operations,
            final_commit_port=final_commit_port,
            old_target_preservation_port=old_target_preservation_port,
            process_instance_id=process_instance_id,
        )
        if final_commit_outcome.committed:
            return _advanced(
                action=RunExecutorCycleAction.FINAL_COMMITTED,
                run_id=final_commit_outcome.run_id,
                run_target_id=final_commit_outcome.run_target_id,
                next_action=final_commit_outcome.next_action,
            )
        return _blocked(
            run_id=final_commit_outcome.run_id,
            run_target_id=final_commit_outcome.run_target_id,
            validation_codes=final_commit_outcome.validation_codes,
            next_action=final_commit_outcome.next_action,
        )

    if _has_phase(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        limit=target.planned_operations + 1,
    ):
        commit_refresh_outcome = refresh_next_run_target_commit_intent_for_fresh_lease(
            permit=permit,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id=process_instance_id,
            max_operations=target.planned_operations + 1,
        )
        if not commit_refresh_outcome.idle:
            if commit_refresh_outcome.refreshed:
                return _advanced(
                    action=RunExecutorCycleAction.COMMIT_INTENT_REFRESHED,
                    run_id=commit_refresh_outcome.run_id,
                    run_target_id=commit_refresh_outcome.run_target_id,
                    next_action=commit_refresh_outcome.next_action,
                )
            return _blocked(
                run_id=commit_refresh_outcome.run_id,
                run_target_id=commit_refresh_outcome.run_target_id,
                validation_codes=commit_refresh_outcome.validation_codes,
                next_action=commit_refresh_outcome.next_action,
            )
        if final_commit_port is None:
            return _blocked(
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                validation_codes=("RUN_EXECUTOR_FINAL_COMMIT_PORT_NOT_CONFIGURED",),
                next_action="Configure a final commit port before applying verified artifacts.",
            )
        final_commit_outcome = commit_next_run_target_verified_artifact(
            permit=permit,
            recovery_operations=recovery_operations,
            final_commit_port=final_commit_port,
            old_target_preservation_port=old_target_preservation_port,
            process_instance_id=process_instance_id,
        )
        if final_commit_outcome.committed:
            return _advanced(
                action=RunExecutorCycleAction.FINAL_COMMITTED,
                run_id=final_commit_outcome.run_id,
                run_target_id=final_commit_outcome.run_target_id,
                next_action=final_commit_outcome.next_action,
            )
        return _blocked(
            run_id=final_commit_outcome.run_id,
            run_target_id=final_commit_outcome.run_target_id,
            validation_codes=final_commit_outcome.validation_codes,
            next_action=final_commit_outcome.next_action,
        )

    rebind_outcome = rebind_next_run_target_recovery_operation_lease(
        permit=permit,
        recovery_operations=recovery_operations,
        process_instance_id=process_instance_id,
        max_operations=target.planned_operations + 1,
    )
    if not rebind_outcome.idle:
        if rebind_outcome.rebound:
            return _advanced(
                action=RunExecutorCycleAction.OPERATION_LEASE_REBOUND,
                run_id=rebind_outcome.run_id,
                run_target_id=rebind_outcome.run_target_id,
                next_action=rebind_outcome.next_action,
            )
        return _blocked(
            run_id=rebind_outcome.run_id,
            run_target_id=rebind_outcome.run_target_id,
            validation_codes=rebind_outcome.validation_codes,
            next_action=rebind_outcome.next_action,
        )

    if _has_phase(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.STAGING_VERIFIED,
        limit=target.planned_operations + 1,
    ):
        latest_segment = intent_segments.load_latest_intent_segment_for_run_target(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
        )
        intent_outcome = publish_run_target_recovery_intent_segment(
            permit=permit,
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id=process_instance_id,
            segment_sequence=(
                0 if latest_segment is None else latest_segment.segment_sequence + 1
            ),
            previous_segment_hash=(
                None if latest_segment is None else latest_segment.segment_hash
            ),
        )
        if intent_outcome.published:
            return _advanced(
                action=RunExecutorCycleAction.INTENT_PUBLISHED,
                run_id=intent_outcome.run_id,
                run_target_id=intent_outcome.run_target_id,
                next_action=intent_outcome.next_action,
            )
        return _blocked(
            run_id=intent_outcome.run_id,
            run_target_id=intent_outcome.run_target_id,
            validation_codes=intent_outcome.validation_codes,
            next_action=intent_outcome.next_action,
        )

    if target.planned_operations == 0:
        completion_outcome = complete_run_target_after_catalog_handoffs(
            permit=permit,
            runs=runs,
            recovery_operations=recovery_operations,
        )
        if completion_outcome.completed:
            lease_registry.release_retained_run_target_lease(
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
            )
            return _advanced(
                action=RunExecutorCycleAction.TARGET_COMPLETED,
                run_id=completion_outcome.run_id,
                run_target_id=completion_outcome.run_target_id,
                next_action=completion_outcome.next_action,
            )
        return _blocked(
            run_id=completion_outcome.run_id,
            run_target_id=completion_outcome.run_target_id,
            validation_codes=completion_outcome.validation_codes,
            next_action=completion_outcome.next_action,
        )

    if not _has_any_operation(
        recovery_operations=recovery_operations,
        permit=permit,
        target=target,
    ):
        planning_outcome = plan_run_target_recovery_operations(
            permit=permit,
            runs=runs,
            plans=plans,
            recovery_operations=recovery_operations,
            process_instance_id=process_instance_id,
        )
        if planning_outcome.planned:
            return _advanced(
                action=RunExecutorCycleAction.OPERATIONS_PLANNED,
                run_id=planning_outcome.run_id,
                run_target_id=planning_outcome.run_target_id,
                next_action=planning_outcome.next_action,
            )
        return _blocked(
            run_id=planning_outcome.run_id,
            run_target_id=planning_outcome.run_target_id,
            validation_codes=planning_outcome.validation_codes,
            next_action=planning_outcome.next_action,
        )

    if recovery_object_cleanup_port is not None:
        cleanup_outcome = cleanup_next_run_target_recovery_object(
            permit=permit,
            recovery_operations=recovery_operations,
            cleanup_port=recovery_object_cleanup_port,
            process_instance_id=process_instance_id,
            max_operations=target.planned_operations + 1,
        )
        if not cleanup_outcome.idle:
            if cleanup_outcome.cleaned:
                return _advanced(
                    action=RunExecutorCycleAction.RECOVERY_OBJECT_CLEANED,
                    run_id=cleanup_outcome.run_id,
                    run_target_id=cleanup_outcome.run_target_id,
                    next_action=cleanup_outcome.next_action,
                )
            return _blocked(
                run_id=cleanup_outcome.run_id,
                run_target_id=cleanup_outcome.run_target_id,
                validation_codes=cleanup_outcome.validation_codes,
                next_action=cleanup_outcome.next_action,
            )

    if _skipped_recovery_ready(
        recovery_operations=recovery_operations,
        permit=permit,
        target=target,
    ):
        completion_outcome = complete_run_target_after_terminal_recovery(
            permit=permit,
            runs=runs,
            recovery_operations=recovery_operations,
        )
        if completion_outcome.completed:
            lease_registry.release_retained_run_target_lease(
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
            )
            return _advanced(
                action=RunExecutorCycleAction.TARGET_COMPLETED,
                run_id=completion_outcome.run_id,
                run_target_id=completion_outcome.run_target_id,
                next_action=completion_outcome.next_action,
            )
        return _blocked(
            run_id=completion_outcome.run_id,
            run_target_id=completion_outcome.run_target_id,
            validation_codes=completion_outcome.validation_codes,
            next_action=completion_outcome.next_action,
        )

    if (
        _catalog_recorded_count(
            recovery_operations=recovery_operations,
            permit=permit,
            target=target,
        )
        >= target.planned_operations
    ):
        completion_outcome = complete_run_target_after_catalog_handoffs(
            permit=permit,
            runs=runs,
            recovery_operations=recovery_operations,
        )
        if completion_outcome.completed:
            lease_registry.release_retained_run_target_lease(
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
            )
            return _advanced(
                action=RunExecutorCycleAction.TARGET_COMPLETED,
                run_id=completion_outcome.run_id,
                run_target_id=completion_outcome.run_target_id,
                next_action=completion_outcome.next_action,
            )
        return _blocked(
            run_id=completion_outcome.run_id,
            run_target_id=completion_outcome.run_target_id,
            validation_codes=completion_outcome.validation_codes,
            next_action=completion_outcome.next_action,
        )

    if _has_early_operation(
        recovery_operations=recovery_operations,
        permit=permit,
        target=target,
    ):
        if staging_transfer_port is None:
            return RunExecutorCycleOutcome(
                action=RunExecutorCycleAction.WAITING_FOR_STAGING,
                advanced=False,
                idle=False,
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                validation_codes=("RUN_EXECUTOR_STAGING_PORT_NOT_CONFIGURED",),
                next_action="Configure a staging transfer port before executing planned operations.",
            )
        staging_outcome = execute_next_run_target_staging_step(
            permit=permit,
            recovery_operations=recovery_operations,
            staging_port=staging_transfer_port,
            process_instance_id=process_instance_id,
        )
        if staging_outcome.advanced:
            return _advanced(
                action=RunExecutorCycleAction.STAGING_ADVANCED,
                run_id=staging_outcome.run_id,
                run_target_id=staging_outcome.run_target_id,
                next_action=staging_outcome.next_action,
            )
        return _blocked(
            run_id=staging_outcome.run_id,
            run_target_id=staging_outcome.run_target_id,
            validation_codes=staging_outcome.validation_codes,
            next_action=staging_outcome.next_action,
        )

    return RunExecutorCycleOutcome(
        action=RunExecutorCycleAction.WAITING_FOR_STAGING,
        advanced=False,
        idle=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        validation_codes=("RUN_EXECUTOR_STAGING_STEP_NOT_IMPLEMENTED",),
        next_action="Run target has planned or partially committed work; add staging/transfer execution before completion.",
    )


def _next_retained_executing_target(
    *,
    runs: RunExecutorQueueStore,
    lease_registry: RunTargetLeaseRegistry,
) -> _RetainedTarget | None:
    for run_id, run_target_id in lease_registry.retained_run_target_keys():
        run = runs.load_started_run(run_id)
        if run is None:
            lease_registry.release_retained_run_target_lease(
                run_id=run_id,
                run_target_id=run_target_id,
            )
            continue
        target = next(
            (item for item in run.targets if item.run_target_id == run_target_id), None
        )
        if target is None or target.state is not RunTargetState.EXECUTING:
            if target is not None and target.state is RunTargetState.SUCCEEDED:
                lease_registry.release_retained_run_target_lease(
                    run_id=run_id,
                    run_target_id=run_target_id,
                )
            continue
        if run.state is not RunState.EXECUTING:
            continue
        lease = lease_registry.load_retained_run_target_lease(
            run_id=run_id,
            run_target_id=run_target_id,
        )
        if lease is None:
            continue
        permit = lease.issue_mutation_permit()
        return _RetainedTarget(run=run, target=target, permit=permit)
    return None


def _has_phase(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    phase: RecoveryOperationPhase,
    limit: int,
) -> bool:
    return bool(
        recovery_operations.list_operations_for_run_target_in_phase(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            phase=phase,
            limit=max(limit, 1),
        )
    )


def _has_any_operation(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    target: StartedRunTarget,
) -> bool:
    for phase in (*EARLY_OPERATION_PHASES, RecoveryOperationPhase.STAGING_VERIFIED):
        if _has_phase(
            recovery_operations=recovery_operations,
            permit=permit,
            phase=phase,
            limit=target.planned_operations + 1,
        ):
            return True
    for phase in (
        RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
        RecoveryOperationPhase.CATALOG_RECORDED,
        RecoveryOperationPhase.CLEANED,
        RecoveryOperationPhase.SKIPPED,
    ):
        if _has_phase(
            recovery_operations=recovery_operations,
            permit=permit,
            phase=phase,
            limit=target.planned_operations + 1,
        ):
            return True
    return False


def _terminal_recovery_ready(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    target: StartedRunTarget,
) -> bool:
    if _has_phase(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.USER_DECISION_REQUIRED,
        limit=target.planned_operations + 1,
    ):
        return True
    cancelled_count = _phase_count(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.CANCELLED,
        limit=target.planned_operations + 1,
    )
    if cancelled_count == 0:
        return False
    return (
        cancelled_count
        + _catalog_recorded_count(
            recovery_operations=recovery_operations,
            permit=permit,
            target=target,
        )
        >= target.planned_operations
    )


def _skipped_recovery_ready(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    target: StartedRunTarget,
) -> bool:
    skipped_count = _phase_count(
        recovery_operations=recovery_operations,
        permit=permit,
        phase=RecoveryOperationPhase.SKIPPED,
        limit=target.planned_operations + 1,
    )
    if skipped_count == 0:
        return False
    return (
        skipped_count
        + _catalog_recorded_count(
            recovery_operations=recovery_operations,
            permit=permit,
            target=target,
        )
        >= target.planned_operations
    )


def _terminal_completion_action(
    target: StartedRunTarget | None,
) -> RunExecutorCycleAction:
    if target is not None and target.state is RunTargetState.RECOVERY_REQUIRED:
        return RunExecutorCycleAction.TARGET_RECOVERY_REQUIRED
    if target is not None and target.state is RunTargetState.CANCELLED:
        return RunExecutorCycleAction.TARGET_CANCELLED
    return RunExecutorCycleAction.TARGET_COMPLETED


def _has_early_operation(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    target: StartedRunTarget,
) -> bool:
    for phase in EARLY_OPERATION_PHASES:
        if _has_phase(
            recovery_operations=recovery_operations,
            permit=permit,
            phase=phase,
            limit=target.planned_operations + 1,
        ):
            return True
    return False


def _phase_count(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    phase: RecoveryOperationPhase,
    limit: int,
) -> int:
    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=phase,
        limit=max(limit, 1),
    )
    return len(operations)


def _catalog_recorded_count(
    *,
    recovery_operations: RunExecutorCycleRecoveryOperationStore,
    permit: MutationPermit,
    target: StartedRunTarget,
) -> int:
    cataloged = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        limit=max(target.planned_operations + 1, 1),
    )
    cleaned = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.CLEANED,
        limit=max(target.planned_operations + 1, 1),
    )
    return len(cataloged) + len(cleaned)


def _advanced(
    *,
    action: RunExecutorCycleAction,
    run_id: str | None,
    run_target_id: str | None,
    next_action: str,
) -> RunExecutorCycleOutcome:
    return RunExecutorCycleOutcome(
        action=action,
        advanced=True,
        idle=False,
        run_id=run_id,
        run_target_id=run_target_id,
        validation_codes=(),
        next_action=next_action,
    )


def _blocked(
    *,
    run_id: str | None,
    run_target_id: str | None,
    validation_codes: tuple[str, ...],
    next_action: str,
) -> RunExecutorCycleOutcome:
    return RunExecutorCycleOutcome(
        action=RunExecutorCycleAction.BLOCKED,
        advanced=False,
        idle=False,
        run_id=run_id,
        run_target_id=run_target_id,
        validation_codes=validation_codes,
        next_action=next_action,
    )
