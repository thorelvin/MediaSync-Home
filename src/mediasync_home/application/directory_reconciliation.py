from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.directory_recovery import (
    CONFLICT_STATE_BY_KIND,
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryState,
    DirectoryRecoveryStore,
    DirectoryRecoveryTransition,
)


MAX_DIRECTORY_RECOVERY_STARTUP_LIMIT = 1000


class DirectoryRecoveryReconciliationViolation(ValueError):
    pass


class DirectoryRecoveryEvidenceState(str, Enum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


class DirectoryRecoveryReconciliationOutcome(str, Enum):
    ADVANCED = "ADVANCED"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class DirectoryRecoveryObservation:
    evidence_state: DirectoryRecoveryEvidenceState
    validation_code: str
    catalog_terminal_recorded: bool = False
    managed_object_id: str | None = None


class DirectoryRecoveryObservationPort(Protocol):
    def observe_directory_recovery(
        self,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryRecoveryObservation: ...


@dataclass(frozen=True, slots=True)
class DirectoryRecoveryReconciliationFinding:
    recovery_id: str
    operation_id: str
    kind: DirectoryRecoveryKind
    initial_state: DirectoryRecoveryState
    final_state: DirectoryRecoveryState
    outcome: DirectoryRecoveryReconciliationOutcome
    validation_code: str


@dataclass(frozen=True, slots=True)
class DirectoryRecoveryReconciliationReport:
    process_instance_id: str
    scanned: int
    findings: tuple[DirectoryRecoveryReconciliationFinding, ...]

    @property
    def mutation_safe(self) -> bool:
        return all(
            finding.outcome is not DirectoryRecoveryReconciliationOutcome.CONFLICT
            for finding in self.findings
        )

    @property
    def conflict_recovery_ids(self) -> tuple[str, ...]:
        return tuple(
            finding.recovery_id
            for finding in self.findings
            if finding.outcome is DirectoryRecoveryReconciliationOutcome.CONFLICT
        )


def reconcile_directory_recovery_after_startup(
    *,
    store: DirectoryRecoveryStore,
    observer: DirectoryRecoveryObservationPort,
    process_instance_id: str,
    limit: int = MAX_DIRECTORY_RECOVERY_STARTUP_LIMIT,
) -> DirectoryRecoveryReconciliationReport:
    if not process_instance_id.strip():
        raise DirectoryRecoveryReconciliationViolation(
            "DIRECTORY_RECOVERY_RECONCILIATION_REQUIRES_PROCESS"
        )
    if limit < 1 or limit > MAX_DIRECTORY_RECOVERY_STARTUP_LIMIT:
        raise DirectoryRecoveryReconciliationViolation(
            "DIRECTORY_RECOVERY_RECONCILIATION_LIMIT_INVALID"
        )

    conflicted = store.list_conflicted_directory_recovery_operations(limit=limit)
    remaining = limit - len(conflicted)
    unresolved = (
        store.list_unresolved_directory_recovery_operations(limit=remaining)
        if remaining > 0
        else ()
    )
    findings = [
        _existing_conflict_finding(operation) for operation in conflicted
    ]
    findings.extend(
        _reconcile_one(
            store=store,
            observer=observer,
            operation=operation,
            process_instance_id=process_instance_id,
        )
        for operation in unresolved
    )
    return DirectoryRecoveryReconciliationReport(
        process_instance_id=process_instance_id,
        scanned=len(findings),
        findings=tuple(findings),
    )


def _reconcile_one(
    *,
    store: DirectoryRecoveryStore,
    observer: DirectoryRecoveryObservationPort,
    operation: DirectoryRecoveryOperation,
    process_instance_id: str,
) -> DirectoryRecoveryReconciliationFinding:
    initial_state = operation.state
    path = SUCCESS_PATH_BY_KIND[operation.kind]
    state_index = path.index(operation.state)
    intent_index, verified_index = _reconciliation_indexes(operation.kind)
    if state_index < intent_index:
        return _finding(
            operation=operation,
            initial_state=initial_state,
            outcome=DirectoryRecoveryReconciliationOutcome.PENDING,
            validation_code="DIRECTORY_RECOVERY_PRE_INTENT_PENDING",
        )

    observation = observer.observe_directory_recovery(operation)
    if state_index == len(path) - 2 and observation.catalog_terminal_recorded:
        operation = _advance_to(
            store=store,
            operation=operation,
            target_index=len(path) - 1,
            process_instance_id=process_instance_id,
            validation_code="DIRECTORY_RECOVERY_CATALOG_TERMINAL_RECONCILED",
            managed_object_id=observation.managed_object_id,
        )
        return _finding(
            operation=operation,
            initial_state=initial_state,
            outcome=DirectoryRecoveryReconciliationOutcome.ADVANCED,
            validation_code="DIRECTORY_RECOVERY_CATALOG_TERMINAL_RECONCILED",
        )
    if observation.evidence_state is DirectoryRecoveryEvidenceState.UNAVAILABLE:
        return _finding(
            operation=operation,
            initial_state=initial_state,
            outcome=DirectoryRecoveryReconciliationOutcome.UNAVAILABLE,
            validation_code=observation.validation_code,
        )
    if observation.evidence_state is DirectoryRecoveryEvidenceState.CONFLICT:
        return _record_conflict(
            store=store,
            operation=operation,
            initial_state=initial_state,
            process_instance_id=process_instance_id,
            validation_code=observation.validation_code,
        )
    if observation.evidence_state is DirectoryRecoveryEvidenceState.NOT_APPLIED:
        if state_index == intent_index:
            return _finding(
                operation=operation,
                initial_state=initial_state,
                outcome=DirectoryRecoveryReconciliationOutcome.PENDING,
                validation_code=observation.validation_code,
            )
        return _record_conflict(
            store=store,
            operation=operation,
            initial_state=initial_state,
            process_instance_id=process_instance_id,
            validation_code="DIRECTORY_RECOVERY_APPLIED_EVIDENCE_MISSING",
        )

    target_index = max(state_index, verified_index)
    operation = _advance_to(
        store=store,
        operation=operation,
        target_index=target_index,
        process_instance_id=process_instance_id,
        validation_code=observation.validation_code,
        managed_object_id=observation.managed_object_id,
    )
    return _finding(
        operation=operation,
        initial_state=initial_state,
        outcome=(
            DirectoryRecoveryReconciliationOutcome.ADVANCED
            if operation.state is not initial_state
            else DirectoryRecoveryReconciliationOutcome.PENDING
        ),
        validation_code=observation.validation_code,
    )


def _reconciliation_indexes(kind: DirectoryRecoveryKind) -> tuple[int, int]:
    if kind is DirectoryRecoveryKind.METADATA:
        return 3, 5
    return 2, 4


def _advance_to(
    *,
    store: DirectoryRecoveryStore,
    operation: DirectoryRecoveryOperation,
    target_index: int,
    process_instance_id: str,
    validation_code: str,
    managed_object_id: str | None,
) -> DirectoryRecoveryOperation:
    path = SUCCESS_PATH_BY_KIND[operation.kind]
    current_index = path.index(operation.state)
    while current_index < target_index:
        next_state = path[current_index + 1]
        updated = store.transition_directory_recovery_operation(
            DirectoryRecoveryTransition(
                recovery_id=operation.recovery_id,
                expected_state=operation.state,
                next_state=next_state,
                process_instance_id=process_instance_id,
                payload={
                    "reconciliation_code": validation_code,
                    "reconciled_state": next_state.value,
                },
                managed_object_id=managed_object_id,
            )
        )
        if updated is None:
            raise DirectoryRecoveryReconciliationViolation(
                "DIRECTORY_RECOVERY_RECONCILIATION_STATE_CONFLICT"
            )
        operation = updated
        current_index += 1
    return operation


def _record_conflict(
    *,
    store: DirectoryRecoveryStore,
    operation: DirectoryRecoveryOperation,
    initial_state: DirectoryRecoveryState,
    process_instance_id: str,
    validation_code: str,
) -> DirectoryRecoveryReconciliationFinding:
    conflict = CONFLICT_STATE_BY_KIND[operation.kind]
    updated = store.transition_directory_recovery_operation(
        DirectoryRecoveryTransition(
            recovery_id=operation.recovery_id,
            expected_state=operation.state,
            next_state=conflict,
            process_instance_id=process_instance_id,
            payload={"reconciliation_code": validation_code},
            last_error_code=validation_code,
        )
    )
    if updated is None:
        raise DirectoryRecoveryReconciliationViolation(
            "DIRECTORY_RECOVERY_RECONCILIATION_STATE_CONFLICT"
        )
    return _finding(
        operation=updated,
        initial_state=initial_state,
        outcome=DirectoryRecoveryReconciliationOutcome.CONFLICT,
        validation_code=validation_code,
    )


def _existing_conflict_finding(
    operation: DirectoryRecoveryOperation,
) -> DirectoryRecoveryReconciliationFinding:
    return _finding(
        operation=operation,
        initial_state=operation.state,
        outcome=DirectoryRecoveryReconciliationOutcome.CONFLICT,
        validation_code=(
            operation.last_error_code or "DIRECTORY_RECOVERY_CONFLICT_REQUIRES_REPAIR"
        ),
    )


def _finding(
    *,
    operation: DirectoryRecoveryOperation,
    initial_state: DirectoryRecoveryState,
    outcome: DirectoryRecoveryReconciliationOutcome,
    validation_code: str,
) -> DirectoryRecoveryReconciliationFinding:
    return DirectoryRecoveryReconciliationFinding(
        recovery_id=operation.recovery_id,
        operation_id=operation.operation_id,
        kind=operation.kind,
        initial_state=initial_state,
        final_state=operation.state,
        outcome=outcome,
        validation_code=validation_code,
    )
