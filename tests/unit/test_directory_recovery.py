from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.directory_recovery import (
    CONFLICT_STATE_BY_KIND,
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryTransition,
    DirectoryRecoveryViolation,
    directory_recovery_is_terminal,
    planned_directory_recovery_operation,
    validate_directory_recovery_transition,
)


@pytest.mark.parametrize("kind", list(DirectoryRecoveryKind))
def test_directory_recovery_kind_follows_its_canonical_success_path(
    kind: DirectoryRecoveryKind,
) -> None:
    operation = _operation(kind)

    for next_state in SUCCESS_PATH_BY_KIND[kind][1:]:
        transition = DirectoryRecoveryTransition(
            recovery_id=operation.recovery_id,
            expected_state=operation.state,
            next_state=next_state,
            process_instance_id="host-a",
            payload={"proof": next_state.value},
        )
        validate_directory_recovery_transition(operation, transition)
        operation = replace(operation, state=next_state)

    assert directory_recovery_is_terminal(operation)
    with pytest.raises(
        DirectoryRecoveryViolation,
        match="DIRECTORY_RECOVERY_TERMINAL_STATE_CANNOT_TRANSITION",
    ):
        validate_directory_recovery_transition(
            operation,
            replace(
                transition,
                expected_state=operation.state,
                next_state=CONFLICT_STATE_BY_KIND[kind],
                last_error_code="LATE_CONFLICT",
            ),
        )


def test_directory_recovery_rejects_skipped_state_and_unexplained_conflict() -> None:
    operation = _operation(DirectoryRecoveryKind.CREATE)
    success_path = SUCCESS_PATH_BY_KIND[operation.kind]

    with pytest.raises(
        DirectoryRecoveryViolation,
        match="DIRECTORY_RECOVERY_INVALID_STATE_TRANSITION",
    ):
        validate_directory_recovery_transition(
            operation,
            DirectoryRecoveryTransition(
                recovery_id=operation.recovery_id,
                expected_state=operation.state,
                next_state=success_path[2],
                process_instance_id="host-a",
                payload={},
            ),
        )
    with pytest.raises(
        DirectoryRecoveryViolation,
        match="DIRECTORY_RECOVERY_CONFLICT_REQUIRES_ERROR",
    ):
        validate_directory_recovery_transition(
            operation,
            DirectoryRecoveryTransition(
                recovery_id=operation.recovery_id,
                expected_state=operation.state,
                next_state=CONFLICT_STATE_BY_KIND[operation.kind],
                process_instance_id="host-a",
                payload={},
            ),
        )


def _operation(kind: DirectoryRecoveryKind):
    return planned_directory_recovery_operation(
        recovery_id=f"directory-{kind.value.lower()}-a",
        operation_id=f"operation-{kind.value.lower()}-a",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        kind=kind,
        final_relative_path="Photos/2026",
        expected_precondition_json='{"object_type":"directory"}',
        desired_metadata_json='{"modified_ns":123}',
    )
