from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.recovery_operations import (
    PRE_COMMIT_LEASE_REBIND_PHASES,
    RecoveryOperation,
    RecoveryOperationPhase,
    TERMINAL_PHASES,
)


MAX_RECOVERY_OPERATION_STARTUP_RECONCILIATION_LIMIT = 1000


class RecoveryOperationReconciliationViolation(ValueError):
    pass


class RecoveryOperationStartupClassification(str, Enum):
    DISCARD_UNVERIFIED_INBOX = "DISCARD_UNVERIFIED_INBOX"
    REACQUIRE_AND_REBIND_PRE_COMMIT = "REACQUIRE_AND_REBIND_PRE_COMMIT"
    REFRESH_COMMIT_INTENT = "REFRESH_COMMIT_INTENT"
    CONTINUE_FROM_VERIFIED_OBJECT = "CONTINUE_FROM_VERIFIED_OBJECT"
    REVERIFY_FINAL = "REVERIFY_FINAL"
    FILESYSTEM_APPLIED_NEEDS_CATALOG = "FILESYSTEM_APPLIED_NEEDS_CATALOG"
    CATALOG_RECORDED_NEEDS_RUN_COMPLETION = "CATALOG_RECORDED_NEEDS_RUN_COMPLETION"
    USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"


@dataclass(frozen=True)
class RecoveryOperationStartupReconciliationRequest:
    reconciler_instance_id: str
    limit: int


@dataclass(frozen=True)
class RecoveryOperationStartupFinding:
    run_id: str
    run_target_id: str
    operation_id: str
    phase: RecoveryOperationPhase
    classification: RecoveryOperationStartupClassification
    requires_manual_decision: bool
    next_action: str


@dataclass(frozen=True)
class RecoveryOperationStartupReconciliationReport:
    reconciler_instance_id: str
    scanned: int
    findings: tuple[RecoveryOperationStartupFinding, ...]

    @property
    def requires_recovery_mode(self) -> bool:
        return bool(self.findings)

    @property
    def manual_decision_operation_ids(self) -> tuple[str, ...]:
        return tuple(
            finding.operation_id
            for finding in self.findings
            if finding.requires_manual_decision
        )


class RecoveryOperationStartupReconciliationStore(Protocol):
    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


NON_TERMINAL_RECOVERY_OPERATION_PHASES = tuple(
    phase for phase in RecoveryOperationPhase if phase not in TERMINAL_PHASES
)


def reconcile_recovery_operations_after_startup(
    request: RecoveryOperationStartupReconciliationRequest,
    *,
    recovery_operations: RecoveryOperationStartupReconciliationStore,
) -> RecoveryOperationStartupReconciliationReport:
    validate_recovery_operation_startup_reconciliation_request(request)

    findings: list[RecoveryOperationStartupFinding] = []
    remaining = request.limit
    for phase in NON_TERMINAL_RECOVERY_OPERATION_PHASES:
        if remaining < 1:
            break
        operations = recovery_operations.list_operations_in_phase(
            phase=phase,
            limit=remaining,
        )
        findings.extend(_finding_for_operation(operation) for operation in operations)
        remaining -= len(operations)

    return RecoveryOperationStartupReconciliationReport(
        reconciler_instance_id=request.reconciler_instance_id,
        scanned=len(findings),
        findings=tuple(findings),
    )


def validate_recovery_operation_startup_reconciliation_request(
    request: RecoveryOperationStartupReconciliationRequest,
) -> None:
    if not request.reconciler_instance_id.strip():
        raise RecoveryOperationReconciliationViolation(
            "RECOVERY_OPERATION_RECONCILIATION_REQUIRES_RECONCILER"
        )
    if request.limit < 1:
        raise RecoveryOperationReconciliationViolation(
            "RECOVERY_OPERATION_RECONCILIATION_LIMIT_MUST_BE_POSITIVE"
        )
    if request.limit > MAX_RECOVERY_OPERATION_STARTUP_RECONCILIATION_LIMIT:
        raise RecoveryOperationReconciliationViolation(
            "RECOVERY_OPERATION_RECONCILIATION_LIMIT_TOO_LARGE"
        )


def _finding_for_operation(operation: RecoveryOperation) -> RecoveryOperationStartupFinding:
    classification = _classification_for_phase(operation.phase)
    return RecoveryOperationStartupFinding(
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        phase=operation.phase,
        classification=classification,
        requires_manual_decision=classification
        is RecoveryOperationStartupClassification.USER_DECISION_REQUIRED,
        next_action=_next_action_for_classification(classification),
    )


def _classification_for_phase(
    phase: RecoveryOperationPhase,
) -> RecoveryOperationStartupClassification:
    if phase in PRE_COMMIT_LEASE_REBIND_PHASES:
        return RecoveryOperationStartupClassification.REACQUIRE_AND_REBIND_PRE_COMMIT
    if phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED:
        return RecoveryOperationStartupClassification.REFRESH_COMMIT_INTENT
    if phase in {
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
    }:
        return RecoveryOperationStartupClassification.REVERIFY_FINAL
    if phase is RecoveryOperationPhase.FINAL_VERIFIED:
        return RecoveryOperationStartupClassification.FILESYSTEM_APPLIED_NEEDS_CATALOG
    if phase is RecoveryOperationPhase.CATALOG_RECORDED:
        return RecoveryOperationStartupClassification.CATALOG_RECORDED_NEEDS_RUN_COMPLETION
    return RecoveryOperationStartupClassification.USER_DECISION_REQUIRED


def _next_action_for_classification(
    classification: RecoveryOperationStartupClassification,
) -> str:
    if classification is RecoveryOperationStartupClassification.DISCARD_UNVERIFIED_INBOX:
        return "Reacquire the endpoint lease, discard unverified staging evidence and rerun staging."
    if classification is RecoveryOperationStartupClassification.REACQUIRE_AND_REBIND_PRE_COMMIT:
        return "Reacquire the endpoint lease and rebind pre-commit recovery operations before continuing."
    if classification is RecoveryOperationStartupClassification.REFRESH_COMMIT_INTENT:
        return "Reacquire the endpoint lease and refresh commit intent before final commit."
    if classification is RecoveryOperationStartupClassification.CONTINUE_FROM_VERIFIED_OBJECT:
        return "Reacquire the endpoint lease and reverify the staged object before commit."
    if classification is RecoveryOperationStartupClassification.REVERIFY_FINAL:
        return "Reacquire the endpoint lease and reverify the final filesystem state."
    if classification is RecoveryOperationStartupClassification.FILESYSTEM_APPLIED_NEEDS_CATALOG:
        return "Record or reconcile the catalog handoff before completing the run target."
    if classification is RecoveryOperationStartupClassification.CATALOG_RECORDED_NEEDS_RUN_COMPLETION:
        return "Complete the catalog-recorded run target before starting new work for the endpoint."
    return "Require guided recovery before any new mutation for the affected endpoint."
