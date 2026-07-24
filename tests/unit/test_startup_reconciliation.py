from __future__ import annotations

import pytest

from mediasync_home.application.command_receipts import (
    CommandReceiptStartupReconciliationReport,
    CommandReceiptStartupReconciliationRequest,
)
from mediasync_home.application.outbox import (
    OutboxStartupReconciliationReport,
    OutboxStartupReconciliationRequest,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.recovery_reconciliation import (
    RecoveryOperationStartupClassification,
)
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationRequest,
    EngineHostStartupReconciliationViolation,
    reconcile_engine_host_after_startup,
    validate_engine_host_startup_reconciliation_request,
)


def test_engine_host_startup_reconciliation_runs_command_receipts_and_outbox() -> None:
    command_receipts = _CommandReceiptReconciler()
    outbox = _OutboxReconciler()
    recovery_operations = _RecoveryOperationReconciler()

    report = reconcile_engine_host_after_startup(
        EngineHostStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            command_receipt_limit=17,
            outbox_limit=19,
            recovery_operation_limit=23,
            inactive_outbox_owner_instance_ids=("host-a",),
        ),
        command_receipts=command_receipts,
        outbox=outbox,
        recovery_operations=recovery_operations,
    )

    assert command_receipts.requests == (
        CommandReceiptStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            limit=17,
        ),
    )
    assert outbox.requests == (
        OutboxStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            inactive_owner_instance_ids=("host-a",),
            limit=19,
        ),
    )
    assert report.reconciler_instance_id == "host-b"
    assert report.command_receipts is not None
    assert report.command_receipts.rejected_idempotency_keys == ("idempotency-a",)
    assert report.outbox is not None
    assert report.outbox.requeued_message_ids == ("message-a",)
    assert recovery_operations.requests == (
        (RecoveryOperationPhase.PLANNED, 23),
        (RecoveryOperationPhase.SOURCE_VALIDATED, 23),
        (RecoveryOperationPhase.SOURCE_STABILITY_BOUND, 23),
        (RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED, 23),
        (RecoveryOperationPhase.STAGING_ALLOCATED, 23),
        (RecoveryOperationPhase.TRANSFERRED, 23),
        (RecoveryOperationPhase.STAGING_DURABLE, 23),
        (RecoveryOperationPhase.STAGING_VERIFIED, 23),
        (RecoveryOperationPhase.COMMIT_INTENT_RECORDED, 22),
        (RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED, 22),
        (RecoveryOperationPhase.OLD_TARGET_PRESERVED, 22),
        (RecoveryOperationPhase.FILESYSTEM_APPLIED, 22),
        (RecoveryOperationPhase.FINAL_DURABLE, 22),
        (RecoveryOperationPhase.FINAL_VERIFIED, 22),
        (RecoveryOperationPhase.CATALOG_RECORDED, 22),
    )
    assert report.recovery_operations is not None
    assert report.recovery_operations.scanned == 1
    assert report.recovery_operations.findings[0].classification is (
        RecoveryOperationStartupClassification.CONTINUE_FROM_VERIFIED_OBJECT
    )
    assert report.recovery_resume is None
    assert report.skipped_outbox_requeue_reason is None


def test_engine_host_startup_reconciliation_skips_outbox_without_inactive_owner_proof() -> None:
    outbox = _OutboxReconciler()

    report = reconcile_engine_host_after_startup(
        EngineHostStartupReconciliationRequest(reconciler_instance_id="host-b"),
        outbox=outbox,
    )

    assert outbox.requests == ()
    assert report.command_receipts is None
    assert report.outbox is None
    assert report.recovery_operations is None
    assert report.recovery_resume is None
    assert report.skipped_outbox_requeue_reason == (
        "OUTBOX_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF"
    )


def test_engine_host_startup_reconciliation_allows_no_optional_stores() -> None:
    report = reconcile_engine_host_after_startup(
        EngineHostStartupReconciliationRequest(reconciler_instance_id="host-b")
    )

    assert report.reconciler_instance_id == "host-b"
    assert report.command_receipts is None
    assert report.outbox is None
    assert report.recovery_operations is None
    assert report.recovery_resume is None
    assert report.skipped_outbox_requeue_reason is None


@pytest.mark.parametrize(
    ("startup_request", "error_code"),
    [
        (
            EngineHostStartupReconciliationRequest(reconciler_instance_id=" "),
            "ENGINE_HOST_RECONCILIATION_REQUIRES_RECONCILER",
        ),
        (
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                command_receipt_limit=0,
            ),
            "ENGINE_HOST_RECONCILIATION_COMMAND_RECEIPT_LIMIT_MUST_BE_POSITIVE",
        ),
        (
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                outbox_limit=0,
            ),
            "ENGINE_HOST_RECONCILIATION_OUTBOX_LIMIT_MUST_BE_POSITIVE",
        ),
        (
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                recovery_operation_limit=0,
            ),
            "ENGINE_HOST_RECONCILIATION_RECOVERY_OPERATION_LIMIT_MUST_BE_POSITIVE",
        ),
        (
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                recovery_resume_limit=0,
            ),
            "ENGINE_HOST_RECONCILIATION_RECOVERY_RESUME_LIMIT_MUST_BE_POSITIVE",
        ),
        (
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                inactive_outbox_owner_instance_ids=("host-a", "host-a"),
            ),
            "ENGINE_HOST_RECONCILIATION_OWNERS_MUST_BE_UNIQUE",
        ),
        (
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                inactive_outbox_owner_instance_ids=("host-b",),
            ),
            "ENGINE_HOST_RECONCILIATION_CANNOT_REQUEUE_CURRENT_OWNER",
        ),
    ],
)
def test_engine_host_startup_reconciliation_validates_request(
    startup_request: EngineHostStartupReconciliationRequest,
    error_code: str,
) -> None:
    with pytest.raises(EngineHostStartupReconciliationViolation, match=error_code):
        validate_engine_host_startup_reconciliation_request(startup_request)


class _CommandReceiptReconciler:
    def __init__(self) -> None:
        self.requests: tuple[CommandReceiptStartupReconciliationRequest, ...] = ()

    def reconcile_non_terminal_after_startup(
        self,
        request: CommandReceiptStartupReconciliationRequest,
    ) -> CommandReceiptStartupReconciliationReport:
        self.requests = (*self.requests, request)
        return CommandReceiptStartupReconciliationReport(
            reconciler_instance_id=request.reconciler_instance_id,
            scanned=2,
            rejected_idempotency_keys=("idempotency-a",),
            pending_effect_reconciliation_keys=("idempotency-b",),
        )


class _OutboxReconciler:
    def __init__(self) -> None:
        self.requests: tuple[OutboxStartupReconciliationRequest, ...] = ()

    def requeue_claimed_after_startup(
        self,
        request: OutboxStartupReconciliationRequest,
    ) -> OutboxStartupReconciliationReport:
        self.requests = (*self.requests, request)
        return OutboxStartupReconciliationReport(
            reconciler_instance_id=request.reconciler_instance_id,
            scanned=1,
            requeued_message_ids=("message-a",),
        )


class _RecoveryOperationReconciler:
    def __init__(self) -> None:
        self.requests: tuple[tuple[RecoveryOperationPhase, int], ...] = ()

    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        self.requests = (*self.requests, (phase, limit))
        if phase is RecoveryOperationPhase.STAGING_VERIFIED:
            return (_operation_in_phase(phase),)
        return ()


def _operation_in_phase(phase: RecoveryOperationPhase) -> RecoveryOperation:
    return RecoveryOperation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="op-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        phase=phase,
        final_relative_path="Pictures/A.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
    )
