from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentState,
)
from mediasync_home.domain.capabilities import MutationPermit


class RunIntentCleanupAction(str, Enum):
    IDLE = "IDLE"
    RECONCILED = "RECONCILED"
    CLEANUP_ELIGIBLE = "CLEANUP_ELIGIBLE"
    TARGET_REMOVED = "TARGET_REMOVED"
    CLEANED = "CLEANED"
    BLOCKED = "BLOCKED"


class RecoveryIntentSegmentLifecycleStore(Protocol):
    def load_next_intent_segment_lifecycle_candidate(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> RecoveryIntentSegment | None: ...

    def intent_segment_reconciliation_ready(self, *, segment_id: str) -> bool: ...

    def transition_intent_segment_state(
        self,
        *,
        segment_id: str,
        expected_state: RecoveryIntentSegmentState,
        next_state: RecoveryIntentSegmentState,
    ) -> RecoveryIntentSegment | None: ...


class TargetRecoveryIntentSegmentCleanupPort(Protocol):
    def ensure_target_intent_segment_absent(
        self,
        *,
        permit: MutationPermit,
        segment: RecoveryIntentSegment,
    ) -> bool: ...


@dataclass(frozen=True)
class RunIntentCleanupOutcome:
    action: RunIntentCleanupAction
    advanced: bool
    all_cleaned: bool
    segment_id: str | None
    validation_codes: tuple[str, ...]
    next_action: str


def advance_next_run_target_intent_cleanup(
    *,
    permit: MutationPermit,
    intent_segments: RecoveryIntentSegmentLifecycleStore,
    target_cleanup: TargetRecoveryIntentSegmentCleanupPort,
) -> RunIntentCleanupOutcome:
    segment = intent_segments.load_next_intent_segment_lifecycle_candidate(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
    )
    if segment is None:
        return RunIntentCleanupOutcome(
            action=RunIntentCleanupAction.IDLE,
            advanced=False,
            all_cleaned=True,
            segment_id=None,
            validation_codes=(),
            next_action="All recovery intent segments are cleaned.",
        )
    if not _segment_matches_permit(segment=segment, permit=permit):
        return _blocked(
            segment=segment,
            validation_code="RUN_INTENT_CLEANUP_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before cleaning recovery intent evidence.",
        )

    if segment.state is RecoveryIntentSegmentState.DURABLE:
        if not intent_segments.intent_segment_reconciliation_ready(
            segment_id=segment.segment_id
        ):
            return RunIntentCleanupOutcome(
                action=RunIntentCleanupAction.IDLE,
                advanced=False,
                all_cleaned=False,
                segment_id=segment.segment_id,
                validation_codes=(),
                next_action="Continue recovery operations before reconciling intent evidence.",
            )
        return _transition(
            segment=segment,
            intent_segments=intent_segments,
            next_state=RecoveryIntentSegmentState.RECONCILED,
            action=RunIntentCleanupAction.RECONCILED,
            next_action="Recovery intent evidence is reconciled with terminal operations.",
        )

    if segment.state is RecoveryIntentSegmentState.RECONCILED:
        return _transition(
            segment=segment,
            intent_segments=intent_segments,
            next_state=RecoveryIntentSegmentState.CLEANUP_ELIGIBLE,
            action=RunIntentCleanupAction.CLEANUP_ELIGIBLE,
            next_action="Recovery intent evidence is eligible for target cleanup.",
        )

    if segment.state is RecoveryIntentSegmentState.CLEANUP_ELIGIBLE:
        try:
            already_absent = target_cleanup.ensure_target_intent_segment_absent(
                permit=permit,
                segment=segment,
            )
        except (RuntimeError, ValueError) as exc:
            return _blocked(
                segment=segment,
                validation_code=_error_code(exc),
                next_action=_error_next_action(
                    exc,
                    "Inspect target recovery intent evidence before retrying cleanup.",
                ),
            )
        if not already_absent:
            return RunIntentCleanupOutcome(
                action=RunIntentCleanupAction.TARGET_REMOVED,
                advanced=True,
                all_cleaned=False,
                segment_id=segment.segment_id,
                validation_codes=(),
                next_action="Target recovery intent evidence was removed; verify absence next cycle.",
            )
        return _transition(
            segment=segment,
            intent_segments=intent_segments,
            next_state=RecoveryIntentSegmentState.CLEANED,
            action=RunIntentCleanupAction.CLEANED,
            next_action="Target recovery intent absence is verified and journaled.",
        )

    return _blocked(
        segment=segment,
        validation_code="RUN_INTENT_CLEANUP_STATE_INVALID",
        next_action="Inspect the recovery intent lifecycle state before retrying cleanup.",
    )


def _transition(
    *,
    segment: RecoveryIntentSegment,
    intent_segments: RecoveryIntentSegmentLifecycleStore,
    next_state: RecoveryIntentSegmentState,
    action: RunIntentCleanupAction,
    next_action: str,
) -> RunIntentCleanupOutcome:
    try:
        transitioned = intent_segments.transition_intent_segment_state(
            segment_id=segment.segment_id,
            expected_state=segment.state,
            next_state=next_state,
        )
    except (RuntimeError, ValueError) as exc:
        return _blocked(
            segment=segment,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reload recovery intent state before retrying cleanup.",
            ),
        )
    if transitioned is None:
        return _blocked(
            segment=segment,
            validation_code="RUN_INTENT_CLEANUP_STATE_CONFLICT",
            next_action="Reload recovery intent state before retrying cleanup.",
        )
    return RunIntentCleanupOutcome(
        action=action,
        advanced=True,
        all_cleaned=(
            next_state is RecoveryIntentSegmentState.CLEANED
            and intent_segments.load_next_intent_segment_lifecycle_candidate(
                run_id=segment.run_id,
                run_target_id=segment.run_target_id,
            )
            is None
        ),
        segment_id=segment.segment_id,
        validation_codes=(),
        next_action=next_action,
    )


def _segment_matches_permit(
    *,
    segment: RecoveryIntentSegment,
    permit: MutationPermit,
) -> bool:
    return (
        segment.run_id == permit.run_id
        and segment.run_target_id == permit.run_target_id
        and segment.target_endpoint_id == permit.endpoint_id
        and segment.target_endpoint_revision_id == permit.endpoint_revision_id
        and segment.owner_installation_id == permit.owner_installation_id
        and segment.ownership_epoch == permit.ownership_epoch
    )


def _blocked(
    *,
    segment: RecoveryIntentSegment,
    validation_code: str,
    next_action: str,
) -> RunIntentCleanupOutcome:
    return RunIntentCleanupOutcome(
        action=RunIntentCleanupAction.BLOCKED,
        advanced=False,
        all_cleaned=False,
        segment_id=segment.segment_id,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _error_code(exc: BaseException) -> str:
    validation_code = getattr(exc, "validation_code", None)
    if isinstance(validation_code, str) and validation_code.strip():
        return validation_code
    return str(exc) or type(exc).__name__


def _error_next_action(exc: BaseException, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback
