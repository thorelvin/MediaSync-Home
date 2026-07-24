from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentStore,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.application.run_intent_segments import (
    build_run_target_recovery_intent_segment,
)
from mediasync_home.domain.capabilities import MutationPermit


MAX_RUN_TARGET_COMMIT_INTENT_REFRESH_SCAN = 1000


class RunTargetCommitIntentRefreshOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetCommitIntentRefreshOutcome:
    idle: bool
    refreshed: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    operation: RecoveryOperation | None
    segment: RecoveryIntentSegment | None
    validation_codes: tuple[str, ...]
    next_action: str


def refresh_next_run_target_commit_intent_for_fresh_lease(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetCommitIntentRefreshOperationStore,
    intent_segments: RecoveryIntentSegmentStore,
    process_instance_id: str,
    max_operations: int,
) -> RunTargetCommitIntentRefreshOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation=None,
            segment=None,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind commit-intent refresh to the Engine Host process instance.",
        )
    if max_operations < 1:
        return _failed(
            permit=permit,
            operation=None,
            segment=None,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_REQUIRES_POSITIVE_LIMIT",
            next_action="Retry commit-intent refresh with a positive bounded limit.",
        )
    if max_operations > MAX_RUN_TARGET_COMMIT_INTENT_REFRESH_SCAN:
        return _failed(
            permit=permit,
            operation=None,
            segment=None,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_LIMIT_TOO_LARGE",
            next_action="Retry commit-intent refresh with a smaller bounded limit.",
        )

    operation = _next_stale_commit_intent_operation(
        permit=permit,
        recovery_operations=recovery_operations,
        max_operations=max_operations,
    )
    if operation is None:
        return RunTargetCommitIntentRefreshOutcome(
            idle=True,
            refreshed=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            operation=None,
            segment=None,
            validation_codes=(),
            next_action="No commit-intent operation needs fresh lease intent refresh.",
        )
    if not _operation_static_binding_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation=operation,
            segment=None,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_PERMIT_MISMATCH",
            next_action="Reconcile recovery operation ownership before refreshing commit intent.",
        )
    if operation.intent_segment_id is None or operation.intent_ordinal is None:
        return _failed(
            permit=permit,
            operation=operation,
            segment=None,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_REQUIRES_EXISTING_INTENT",
            next_action="Recover or reconcile the existing commit intent segment before refresh.",
        )

    current_segment = intent_segments.load_intent_segment(operation.intent_segment_id)
    if current_segment is None or not _segment_matches_operation(
        segment=current_segment,
        operation=operation,
    ):
        return _failed(
            permit=permit,
            operation=operation,
            segment=current_segment,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_SEGMENT_MISMATCH",
            next_action="Recover or reconcile the existing commit intent segment before refresh.",
        )
    latest_segment = intent_segments.load_latest_intent_segment_for_run_target(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
    )
    if latest_segment is None:
        return _failed(
            permit=permit,
            operation=operation,
            segment=current_segment,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_SEGMENT_CHAIN_MISSING",
            next_action="Recover or reconcile recovery intent segment history before refresh.",
        )

    segment = build_run_target_recovery_intent_segment(
        permit=permit,
        operations=(operation,),
        segment_sequence=latest_segment.segment_sequence + 1,
        previous_segment_hash=latest_segment.segment_hash,
    )
    try:
        published = intent_segments.publish_intent_segment(segment)
        refreshed = recovery_operations.record_commit_intent_refreshed(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_lease_id=operation.lease_id,
            expected_ownership_epoch=operation.ownership_epoch,
            expected_fencing_token=operation.fencing_token,
            lease_id=permit.lease_id,
            owner_installation_id=permit.owner_installation_id,
            ownership_epoch=permit.ownership_epoch,
            fencing_token=permit.fencing_token,
            intent_segment_id=published.segment_id,
            intent_ordinal=0,
            process_instance_id=process_instance_id,
            payload={
                "new_fencing_token": permit.fencing_token,
                "new_intent_segment_id": published.segment_id,
                "new_lease_id": permit.lease_id,
                "old_fencing_token": operation.fencing_token,
                "old_intent_segment_id": operation.intent_segment_id,
                "old_lease_id": operation.lease_id,
            },
        )
    except ValueError as exc:
        return _failed(
            permit=permit,
            operation=operation,
            segment=segment,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reload recovery intent state before retrying commit-intent refresh.",
            ),
        )
    if refreshed is None:
        return _failed(
            permit=permit,
            operation=operation,
            segment=segment,
            validation_code="RUN_TARGET_COMMIT_INTENT_REFRESH_CONFLICT",
            next_action="Reload recovery operation state before retrying commit-intent refresh.",
        )

    return RunTargetCommitIntentRefreshOutcome(
        idle=False,
        refreshed=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=refreshed.operation_id,
        operation=refreshed,
        segment=published,
        validation_codes=(),
        next_action="Commit intent is refreshed for the fresh endpoint lease.",
    )


def _next_stale_commit_intent_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetCommitIntentRefreshOperationStore,
    max_operations: int,
) -> RecoveryOperation | None:
    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        limit=max_operations,
    )
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
        operation.phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED
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


def _segment_matches_operation(
    *,
    segment: RecoveryIntentSegment,
    operation: RecoveryOperation,
) -> bool:
    return (
        segment.run_id == operation.run_id
        and segment.run_target_id == operation.run_target_id
        and segment.target_endpoint_id == operation.target_endpoint_id
        and segment.target_endpoint_revision_id == operation.target_endpoint_revision_id
        and segment.owner_installation_id == operation.owner_installation_id
        and segment.ownership_epoch == operation.ownership_epoch
        and segment.lease_id == operation.lease_id
        and segment.fencing_token == operation.fencing_token
    )


def _failed(
    *,
    permit: MutationPermit,
    operation: RecoveryOperation | None,
    segment: RecoveryIntentSegment | None,
    validation_code: str,
    next_action: str,
) -> RunTargetCommitIntentRefreshOutcome:
    return RunTargetCommitIntentRefreshOutcome(
        idle=False,
        refreshed=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=None if operation is None else operation.operation_id,
        operation=operation,
        segment=segment,
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
