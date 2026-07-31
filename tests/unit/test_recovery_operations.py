from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.recovery_operations import (
    RecoveryOperationPhase,
    RecoveryOperationViolation,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
    validate_recovery_operation,
    validate_recovery_phase_transition,
)


def test_planned_recovery_operation_binds_lease_and_relative_paths() -> None:
    operation = _operation()

    assert operation.phase is RecoveryOperationPhase.PLANNED
    assert operation.lease_id == "lease-a"
    assert operation.fencing_token == 1
    assert operation.final_relative_path == "Photos/2026/image.jpg"
    assert operation.source_relative_path == "Photos/2026/image.jpg"


def test_recovery_phase_transition_accepts_linear_and_terminal_paths() -> None:
    validate_recovery_phase_transition(
        RecoveryOperationPhase.STAGING_VERIFIED,
        RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
    )
    validate_recovery_phase_transition(
        RecoveryOperationPhase.STAGING_VERIFIED,
        RecoveryOperationPhase.FAILED_RETRYABLE,
    )


def test_recovery_phase_transition_rejects_skipped_success_path() -> None:
    with pytest.raises(
        RecoveryOperationViolation,
        match="RECOVERY_OPERATION_INVALID_PHASE_TRANSITION",
    ):
        validate_recovery_phase_transition(
            RecoveryOperationPhase.PLANNED,
            RecoveryOperationPhase.STAGING_VERIFIED,
        )


def test_recovery_phase_transition_rejects_terminal_source() -> None:
    with pytest.raises(
        RecoveryOperationViolation,
        match="RECOVERY_OPERATION_TERMINAL_PHASE_CANNOT_TRANSITION",
    ):
        validate_recovery_phase_transition(
            RecoveryOperationPhase.CLEANED,
            RecoveryOperationPhase.SOURCE_VALIDATED,
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute/file.txt",
        "\\absolute\\file.txt",
        "C:\\absolute\\file.txt",
        "Photos/../secret.txt",
        "Photos//image.jpg",
    ],
)
def test_recovery_operation_rejects_unsafe_final_paths(relative_path: str) -> None:
    with pytest.raises(
        RecoveryOperationViolation,
        match="RECOVERY_OPERATION_REQUIRES_RELATIVE_FINAL_PATH",
    ):
        _operation(final_relative_path=relative_path)


def test_recovery_operation_requires_intent_segment_after_commit_intent() -> None:
    operation = replace(_operation(), phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED)

    with pytest.raises(
        RecoveryOperationViolation,
        match="RECOVERY_OPERATION_REQUIRES_INTENT_SEGMENT",
    ):
        validate_recovery_operation(operation)

    validate_recovery_operation(
        replace(
            operation,
            intent_segment_id="segment-a",
            intent_ordinal=0,
        )
    )


def test_recovery_operation_validates_persisted_staging_retry_pair() -> None:
    retrying = replace(
        _operation(),
        staging_failure_count=1,
        staging_retry_backoff_ms=900,
        staging_retry_not_before_utc="2026-07-31T00:00:00.900Z",
    )

    validate_recovery_operation(retrying)
    with pytest.raises(
        RecoveryOperationViolation,
        match="STAGING_RETRY_TIMING_PAIR_INVALID",
    ):
        validate_recovery_operation(
            replace(retrying, staging_retry_not_before_utc=None)
        )
    with pytest.raises(
        RecoveryOperationViolation,
        match="RECOVERY_OPERATION_STAGING_RETRY_STATE_INVALID",
    ):
        validate_recovery_operation(
            replace(retrying, phase=RecoveryOperationPhase.SKIPPED)
        )


def _operation(**overrides: object):
    values: dict[str, object] = {
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "operation_id": "operation-a",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "owner_installation_id": "owner-a",
        "ownership_epoch": 1,
        "lease_id": "lease-a",
        "lease_resource_key": "endpoint:target-a",
        "fencing_token": 1,
        "final_relative_path": "Photos/2026/image.jpg",
        "source_relative_path": "Photos/2026/image.jpg",
        "source_endpoint_id": "source-a",
        "source_endpoint_revision_id": "source-rev-a",
        "target_precondition_kind": RecoveryTargetPreconditionKind.ABSENT,
    }
    values.update(overrides)
    return planned_recovery_operation(**values)
