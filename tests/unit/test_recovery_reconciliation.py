from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.recovery_operations import (
    PHASES_REQUIRING_CATALOG_HANDOFF,
    PHASES_REQUIRING_INTENT,
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.recovery_reconciliation import (
    RecoveryOperationReconciliationViolation,
    RecoveryOperationStartupClassification,
    RecoveryOperationStartupReconciliationRequest,
    reconcile_recovery_operations_after_startup,
    validate_recovery_operation_startup_reconciliation_request,
)


def test_recovery_operation_startup_reconciliation_classifies_non_terminal_operations() -> None:
    store = _RecoveryOperationStore(
        (
            _operation("op-planned", RecoveryOperationPhase.PLANNED),
            _operation("op-staged", RecoveryOperationPhase.STAGING_VERIFIED),
            _operation("op-applied", RecoveryOperationPhase.FILESYSTEM_APPLIED),
            _operation("op-final", RecoveryOperationPhase.FINAL_VERIFIED),
            _operation("op-catalog", RecoveryOperationPhase.CATALOG_RECORDED),
        )
    )

    report = reconcile_recovery_operations_after_startup(
        RecoveryOperationStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            limit=10,
        ),
        recovery_operations=store,
    )

    assert report.reconciler_instance_id == "host-b"
    assert report.scanned == 5
    assert report.requires_recovery_mode is True
    assert report.manual_decision_operation_ids == ()
    assert [(finding.operation_id, finding.classification) for finding in report.findings] == [
        ("op-planned", RecoveryOperationStartupClassification.DISCARD_UNVERIFIED_INBOX),
        ("op-staged", RecoveryOperationStartupClassification.CONTINUE_FROM_VERIFIED_OBJECT),
        ("op-applied", RecoveryOperationStartupClassification.REVERIFY_FINAL),
        ("op-final", RecoveryOperationStartupClassification.FILESYSTEM_APPLIED_NEEDS_CATALOG),
        ("op-catalog", RecoveryOperationStartupClassification.CATALOG_RECORDED_NEEDS_RUN_COMPLETION),
    ]


def test_recovery_operation_startup_reconciliation_is_bounded() -> None:
    store = _RecoveryOperationStore(
        (
            _operation("op-a", RecoveryOperationPhase.PLANNED),
            _operation("op-b", RecoveryOperationPhase.PLANNED),
        )
    )

    report = reconcile_recovery_operations_after_startup(
        RecoveryOperationStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            limit=1,
        ),
        recovery_operations=store,
    )

    assert report.scanned == 1
    assert [finding.operation_id for finding in report.findings] == ["op-a"]


@pytest.mark.parametrize(
    ("startup_request", "error_code"),
    [
        (
            RecoveryOperationStartupReconciliationRequest(
                reconciler_instance_id=" ",
                limit=1,
            ),
            "RECOVERY_OPERATION_RECONCILIATION_REQUIRES_RECONCILER",
        ),
        (
            RecoveryOperationStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                limit=0,
            ),
            "RECOVERY_OPERATION_RECONCILIATION_LIMIT_MUST_BE_POSITIVE",
        ),
    ],
)
def test_recovery_operation_startup_reconciliation_validates_request(
    startup_request: RecoveryOperationStartupReconciliationRequest,
    error_code: str,
) -> None:
    with pytest.raises(RecoveryOperationReconciliationViolation, match=error_code):
        validate_recovery_operation_startup_reconciliation_request(startup_request)


class _RecoveryOperationStore:
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self._operations = operations

    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in self._operations
            if operation.phase is phase
        )[:limit]


def _operation(operation_id: str, phase: RecoveryOperationPhase) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id=operation_id,
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id="lease-a",
            lease_resource_key="endpoint:target-a",
            fencing_token=1,
            final_relative_path=f"Pictures/{operation_id}.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        ),
        phase=phase,
        intent_segment_id="segment-a" if phase in PHASES_REQUIRING_INTENT else None,
        intent_ordinal=0 if phase in PHASES_REQUIRING_INTENT else None,
        catalog_handoff_id="final-file:run-a:" + operation_id
        if phase in PHASES_REQUIRING_CATALOG_HANDOFF
        else None,
    )
