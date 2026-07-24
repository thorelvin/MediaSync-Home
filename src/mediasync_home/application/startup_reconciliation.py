from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.application.command_receipts import (
    MAX_COMMAND_RECEIPT_STARTUP_RECONCILIATION_LIMIT,
    CommandReceiptStartupReconciliationReport,
    CommandReceiptStartupReconciliationRequest,
    CommandReceiptStartupReconciliationStore,
)
from mediasync_home.application.outbox import (
    MAX_OUTBOX_STARTUP_RECONCILIATION_LIMIT,
    OutboxStartupReconciliationReport,
    OutboxStartupReconciliationRequest,
    OutboxStartupReconciliationStore,
)
from mediasync_home.application.recovery_reconciliation import (
    MAX_RECOVERY_OPERATION_STARTUP_RECONCILIATION_LIMIT,
    RecoveryOperationStartupReconciliationReport,
    RecoveryOperationStartupReconciliationRequest,
    RecoveryOperationStartupReconciliationStore,
    reconcile_recovery_operations_after_startup,
)
from mediasync_home.application.recovery_resume import (
    MAX_RECOVERY_RESUME_STARTUP_LIMIT,
    RecoveryResumeOperationStore,
    RecoveryResumeStartupReport,
    RecoveryResumeStartupRequest,
    resume_catalog_recorded_run_targets_after_startup,
)
from mediasync_home.application.runs import RunStore


class EngineHostStartupReconciliationViolation(ValueError):
    pass


@dataclass(frozen=True)
class EngineHostStartupReconciliationRequest:
    reconciler_instance_id: str
    command_receipt_limit: int = MAX_COMMAND_RECEIPT_STARTUP_RECONCILIATION_LIMIT
    outbox_limit: int = MAX_OUTBOX_STARTUP_RECONCILIATION_LIMIT
    recovery_operation_limit: int = MAX_RECOVERY_OPERATION_STARTUP_RECONCILIATION_LIMIT
    recovery_resume_limit: int = MAX_RECOVERY_RESUME_STARTUP_LIMIT
    inactive_outbox_owner_instance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineHostStartupReconciliationReport:
    reconciler_instance_id: str
    command_receipts: CommandReceiptStartupReconciliationReport | None
    outbox: OutboxStartupReconciliationReport | None
    recovery_operations: RecoveryOperationStartupReconciliationReport | None
    recovery_resume: RecoveryResumeStartupReport | None
    skipped_outbox_requeue_reason: str | None = None


def reconcile_engine_host_after_startup(
    request: EngineHostStartupReconciliationRequest,
    *,
    command_receipts: CommandReceiptStartupReconciliationStore | None = None,
    outbox: OutboxStartupReconciliationStore | None = None,
    recovery_operations: RecoveryOperationStartupReconciliationStore | None = None,
    recovery_resume_operations: RecoveryResumeOperationStore | None = None,
    runs: RunStore | None = None,
) -> EngineHostStartupReconciliationReport:
    validate_engine_host_startup_reconciliation_request(request)

    command_report = None
    if command_receipts is not None:
        command_report = command_receipts.reconcile_non_terminal_after_startup(
            CommandReceiptStartupReconciliationRequest(
                reconciler_instance_id=request.reconciler_instance_id,
                limit=request.command_receipt_limit,
            )
        )

    outbox_report = None
    skipped_outbox_requeue_reason = None
    if outbox is not None:
        if request.inactive_outbox_owner_instance_ids:
            outbox_report = outbox.requeue_claimed_after_startup(
                OutboxStartupReconciliationRequest(
                    reconciler_instance_id=request.reconciler_instance_id,
                    inactive_owner_instance_ids=request.inactive_outbox_owner_instance_ids,
                    limit=request.outbox_limit,
                )
            )
        else:
            skipped_outbox_requeue_reason = "OUTBOX_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF"

    recovery_report = None
    if recovery_operations is not None:
        recovery_report = reconcile_recovery_operations_after_startup(
            RecoveryOperationStartupReconciliationRequest(
                reconciler_instance_id=request.reconciler_instance_id,
                limit=request.recovery_operation_limit,
            ),
            recovery_operations=recovery_operations,
        )

    recovery_resume_report = None
    if recovery_resume_operations is not None and runs is not None:
        recovery_resume_report = resume_catalog_recorded_run_targets_after_startup(
            RecoveryResumeStartupRequest(
                reconciler_instance_id=request.reconciler_instance_id,
                limit=request.recovery_resume_limit,
            ),
            runs=runs,
            recovery_operations=recovery_resume_operations,
        )

    return EngineHostStartupReconciliationReport(
        reconciler_instance_id=request.reconciler_instance_id,
        command_receipts=command_report,
        outbox=outbox_report,
        recovery_operations=recovery_report,
        recovery_resume=recovery_resume_report,
        skipped_outbox_requeue_reason=skipped_outbox_requeue_reason,
    )


def validate_engine_host_startup_reconciliation_request(
    request: EngineHostStartupReconciliationRequest,
) -> None:
    if not request.reconciler_instance_id.strip():
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_REQUIRES_RECONCILER"
        )
    if request.command_receipt_limit < 1:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_COMMAND_RECEIPT_LIMIT_MUST_BE_POSITIVE"
        )
    if request.command_receipt_limit > MAX_COMMAND_RECEIPT_STARTUP_RECONCILIATION_LIMIT:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_COMMAND_RECEIPT_LIMIT_TOO_LARGE"
        )
    if request.outbox_limit < 1:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_OUTBOX_LIMIT_MUST_BE_POSITIVE"
        )
    if request.outbox_limit > MAX_OUTBOX_STARTUP_RECONCILIATION_LIMIT:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_OUTBOX_LIMIT_TOO_LARGE"
        )
    if request.recovery_operation_limit < 1:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_RECOVERY_OPERATION_LIMIT_MUST_BE_POSITIVE"
        )
    if request.recovery_operation_limit > MAX_RECOVERY_OPERATION_STARTUP_RECONCILIATION_LIMIT:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_RECOVERY_OPERATION_LIMIT_TOO_LARGE"
        )
    if request.recovery_resume_limit < 1:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_RECOVERY_RESUME_LIMIT_MUST_BE_POSITIVE"
        )
    if request.recovery_resume_limit > MAX_RECOVERY_RESUME_STARTUP_LIMIT:
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_RECOVERY_RESUME_LIMIT_TOO_LARGE"
        )

    owners = set()
    for owner_instance_id in request.inactive_outbox_owner_instance_ids:
        if not owner_instance_id.strip():
            raise EngineHostStartupReconciliationViolation(
                "ENGINE_HOST_RECONCILIATION_REQUIRES_INACTIVE_OWNER_PROOF"
            )
        if owner_instance_id == request.reconciler_instance_id:
            raise EngineHostStartupReconciliationViolation(
                "ENGINE_HOST_RECONCILIATION_CANNOT_REQUEUE_CURRENT_OWNER"
            )
        owners.add(owner_instance_id)
    if len(owners) != len(request.inactive_outbox_owner_instance_ids):
        raise EngineHostStartupReconciliationViolation(
            "ENGINE_HOST_RECONCILIATION_OWNERS_MUST_BE_UNIQUE"
        )
