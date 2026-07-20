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
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationRequest,
    EngineHostStartupReconciliationViolation,
    reconcile_engine_host_after_startup,
    validate_engine_host_startup_reconciliation_request,
)


def test_engine_host_startup_reconciliation_runs_command_receipts_and_outbox() -> None:
    command_receipts = _CommandReceiptReconciler()
    outbox = _OutboxReconciler()

    report = reconcile_engine_host_after_startup(
        EngineHostStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            command_receipt_limit=17,
            outbox_limit=19,
            inactive_outbox_owner_instance_ids=("host-a",),
        ),
        command_receipts=command_receipts,
        outbox=outbox,
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
