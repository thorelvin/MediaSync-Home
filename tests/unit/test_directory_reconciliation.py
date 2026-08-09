from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.directory_reconciliation import (
    DirectoryRecoveryEvidenceState,
    DirectoryRecoveryObservation,
    DirectoryRecoveryReconciliationOutcome,
    reconcile_directory_recovery_after_startup,
)
from mediasync_home.application.directory_recovery import (
    CONFLICT_STATE_BY_KIND,
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryTransition,
    planned_directory_recovery_operation,
    validate_directory_recovery_transition,
)


@pytest.mark.parametrize("kind", tuple(DirectoryRecoveryKind))
def test_startup_reconciliation_advances_proven_applied_intent_to_verified(
    kind: DirectoryRecoveryKind,
) -> None:
    operation = _operation_at(kind, 3 if kind is DirectoryRecoveryKind.METADATA else 2)
    store = _Store(operation)
    observer = _Observer(
        DirectoryRecoveryObservation(
            evidence_state=DirectoryRecoveryEvidenceState.APPLIED,
            validation_code="PROVEN_APPLIED",
            managed_object_id=(
                "operation-a"
                if kind in {DirectoryRecoveryKind.QUARANTINE, DirectoryRecoveryKind.RESTORE}
                else None
            ),
        )
    )

    report = reconcile_directory_recovery_after_startup(
        store=store,
        observer=observer,
        process_instance_id="host-a",
    )

    assert report.mutation_safe is True
    assert report.findings[0].outcome is DirectoryRecoveryReconciliationOutcome.ADVANCED
    assert store.operation.state is SUCCESS_PATH_BY_KIND[kind][-2]


def test_startup_reconciliation_keeps_not_applied_intent_retryable() -> None:
    operation = _operation_at(DirectoryRecoveryKind.CREATE, 2)
    store = _Store(operation)

    report = reconcile_directory_recovery_after_startup(
        store=store,
        observer=_Observer(
            DirectoryRecoveryObservation(
                evidence_state=DirectoryRecoveryEvidenceState.NOT_APPLIED,
                validation_code="TARGET_ABSENT",
            )
        ),
        process_instance_id="host-a",
    )

    assert report.mutation_safe is True
    assert report.findings[0].outcome is DirectoryRecoveryReconciliationOutcome.PENDING
    assert store.operation.state is operation.state


def test_startup_reconciliation_records_missing_applied_evidence_as_conflict() -> None:
    operation = _operation_at(DirectoryRecoveryKind.RESTORE, 3)
    store = _Store(operation)

    report = reconcile_directory_recovery_after_startup(
        store=store,
        observer=_Observer(
            DirectoryRecoveryObservation(
                evidence_state=DirectoryRecoveryEvidenceState.NOT_APPLIED,
                validation_code="TARGET_ABSENT",
            )
        ),
        process_instance_id="host-a",
    )

    assert report.mutation_safe is False
    assert report.findings[0].outcome is DirectoryRecoveryReconciliationOutcome.CONFLICT
    assert store.operation.state is CONFLICT_STATE_BY_KIND[DirectoryRecoveryKind.RESTORE]
    assert store.operation.last_error_code == "DIRECTORY_RECOVERY_APPLIED_EVIDENCE_MISSING"


def test_startup_reconciliation_finishes_catalog_terminal_before_filesystem_probe() -> None:
    operation = _operation_at(DirectoryRecoveryKind.CREATE, 4)
    store = _Store(operation)

    report = reconcile_directory_recovery_after_startup(
        store=store,
        observer=_Observer(
            DirectoryRecoveryObservation(
                evidence_state=DirectoryRecoveryEvidenceState.CONFLICT,
                validation_code="MARKER_ALREADY_CLEANED",
                catalog_terminal_recorded=True,
            )
        ),
        process_instance_id="host-a",
    )

    assert report.mutation_safe is True
    assert store.operation.state is SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.CREATE][-1]


def test_startup_reconciliation_reports_persisted_conflict_on_every_restart() -> None:
    kind = DirectoryRecoveryKind.QUARANTINE
    operation = replace(
        _operation_at(kind, 2),
        state=CONFLICT_STATE_BY_KIND[kind],
        last_error_code="DIRECTORY_QUARANTINE_BINDING_CONFLICT",
    )
    store = _Store(operation)
    observer = _Observer(
        DirectoryRecoveryObservation(
            evidence_state=DirectoryRecoveryEvidenceState.APPLIED,
            validation_code="IGNORED",
        )
    )

    report = reconcile_directory_recovery_after_startup(
        store=store,
        observer=observer,
        process_instance_id="host-a",
    )

    assert report.mutation_safe is False
    assert report.conflict_recovery_ids == (operation.recovery_id,)
    assert observer.calls == 0


def test_startup_reconciliation_does_not_probe_before_durable_intent() -> None:
    operation = _operation_at(DirectoryRecoveryKind.METADATA, 2)
    store = _Store(operation)
    observer = _Observer(
        DirectoryRecoveryObservation(
            evidence_state=DirectoryRecoveryEvidenceState.APPLIED,
            validation_code="IGNORED",
        )
    )

    report = reconcile_directory_recovery_after_startup(
        store=store,
        observer=observer,
        process_instance_id="host-a",
    )

    assert report.findings[0].validation_code == "DIRECTORY_RECOVERY_PRE_INTENT_PENDING"
    assert observer.calls == 0


class _Store:
    def __init__(self, operation: DirectoryRecoveryOperation) -> None:
        self.operation = operation

    def list_conflicted_directory_recovery_operations(
        self,
        *,
        limit: int,
    ) -> tuple[DirectoryRecoveryOperation, ...]:
        del limit
        return (
            (self.operation,)
            if self.operation.state is CONFLICT_STATE_BY_KIND[self.operation.kind]
            else ()
        )

    def list_unresolved_directory_recovery_operations(
        self,
        *,
        limit: int,
    ) -> tuple[DirectoryRecoveryOperation, ...]:
        del limit
        if self.operation.state in {
            CONFLICT_STATE_BY_KIND[self.operation.kind],
            SUCCESS_PATH_BY_KIND[self.operation.kind][-1],
        }:
            return ()
        return (self.operation,)

    def transition_directory_recovery_operation(
        self,
        transition: DirectoryRecoveryTransition,
    ) -> DirectoryRecoveryOperation | None:
        validate_directory_recovery_transition(self.operation, transition)
        self.operation = replace(
            self.operation,
            state=transition.next_state,
            managed_object_id=(
                transition.managed_object_id or self.operation.managed_object_id
            ),
            last_error_code=transition.last_error_code,
            row_version=self.operation.row_version + 1,
        )
        return self.operation

    def load_directory_recovery_operation(
        self,
        recovery_id: str,
    ) -> DirectoryRecoveryOperation | None:
        return self.operation if recovery_id == self.operation.recovery_id else None

    def record_directory_recovery_operation(
        self,
        operation: DirectoryRecoveryOperation,
        *,
        process_instance_id: str,
        payload: object | None = None,
    ) -> DirectoryRecoveryOperation:
        del process_instance_id, payload
        self.operation = operation
        return operation


class _Observer:
    def __init__(self, observation: DirectoryRecoveryObservation) -> None:
        self.observation = observation
        self.calls = 0

    def observe_directory_recovery(
        self,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryRecoveryObservation:
        del operation
        self.calls += 1
        return self.observation


def _operation_at(
    kind: DirectoryRecoveryKind,
    state_index: int,
) -> DirectoryRecoveryOperation:
    operation = planned_directory_recovery_operation(
        recovery_id=f"directory-{kind.value.lower()}",
        operation_id="operation-a",
        run_id="run-a",
        run_target_id="target-a",
        target_endpoint_id="endpoint-a",
        target_endpoint_revision_id="revision-a",
        owner_installation_id="installation-a",
        ownership_epoch=1,
        kind=kind,
        final_relative_path="Parent/Folder",
        expected_precondition_json=(
            '{"entry_count":0,"kind":"DIRECTORY_EMPTY"}'
            if kind in {DirectoryRecoveryKind.QUARANTINE, DirectoryRecoveryKind.RESTORE}
            else '{"kind":"ABSENT"}'
        ),
        desired_metadata_json=(
            '{"modified_ns":123456789}'
            if kind is DirectoryRecoveryKind.METADATA
            else None
        ),
    )
    return replace(operation, state=SUCCESS_PATH_BY_KIND[kind][state_index])
