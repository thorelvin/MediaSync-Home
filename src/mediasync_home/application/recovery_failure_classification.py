from __future__ import annotations

from mediasync_home.application.recovery_operations import RecoveryOperation, RecoveryOperationPhase


AMBIGUOUS_PRESERVED_REPLACEMENT_CODES = frozenset(
    {
        "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",
        "LOCAL_REPLACE_FINAL_COMMIT_TARGET_TYPE_CHANGED_AFTER_PRESERVE",
        "LOCAL_REPLACE_FINAL_COMMIT_TARGET_REAPPEARED",
    }
)


def recovery_phase_for_commit_failure(
    *,
    operation: RecoveryOperation,
    validation_code: str,
) -> RecoveryOperationPhase:
    if (
        operation.phase is RecoveryOperationPhase.OLD_TARGET_PRESERVED
        and validation_code in AMBIGUOUS_PRESERVED_REPLACEMENT_CODES
    ):
        return RecoveryOperationPhase.USER_DECISION_REQUIRED
    return RecoveryOperationPhase.FAILED_RETRYABLE
