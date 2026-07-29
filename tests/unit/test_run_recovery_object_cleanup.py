from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from mediasync_home.application.ports import RecoveryObjectCleanupReceipt, RelativePath
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_recovery_object_cleanup import (
    RunTargetRecoveryObjectCleanupOperationStore,
    cleanup_next_run_target_recovery_object,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_cleanup_next_run_target_recovery_object_records_cleaned_for_quarantine() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    cleanup_port = _FakeRecoveryObjectCleanupPort()

    outcome = cleanup_next_run_target_recovery_object(
        permit=_permit(),
        recovery_operations=recovery_operations,
        cleanup_port=cleanup_port,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.idle is False
    assert outcome.cleaned is True
    assert outcome.validation_codes == ()
    assert outcome.receipt == RecoveryObjectCleanupReceipt(
        operation_id="op-a",
        final_relative_path=RelativePath("Pictures/A.jpg"),
        cleaned_object_ids=("op-a",),
    )
    assert operation.phase is RecoveryOperationPhase.CLEANED
    assert cleanup_port.calls == (("lease-a", "op-a"),)
    assert recovery_operations.transitions == (
        ("op-a", RecoveryOperationPhase.CATALOG_RECORDED, RecoveryOperationPhase.CLEANED),
    )


def test_cleanup_next_run_target_recovery_object_reports_idle_without_quarantine() -> None:
    recovery_operations = _FakeRecoveryOperationStore(
        (
            replace(
                _operation(),
                target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
                quarantine_object_id=None,
            ),
        )
    )
    cleanup_port = _FakeRecoveryObjectCleanupPort()

    outcome = cleanup_next_run_target_recovery_object(
        permit=_permit(),
        recovery_operations=recovery_operations,
        cleanup_port=cleanup_port,
        process_instance_id="host-a",
    )

    assert outcome.idle is True
    assert outcome.cleaned is False
    assert outcome.validation_codes == ()
    assert cleanup_port.calls == ()
    assert recovery_operations.transitions == ()


def test_cleanup_next_run_target_recovery_object_rejects_permit_mismatch() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(lease_id="other-lease"),))
    cleanup_port = _FakeRecoveryObjectCleanupPort()

    outcome = cleanup_next_run_target_recovery_object(
        permit=_permit(),
        recovery_operations=recovery_operations,
        cleanup_port=cleanup_port,
        process_instance_id="host-a",
    )

    assert outcome.cleaned is False
    assert outcome.validation_codes == ("RUN_TARGET_RECOVERY_OBJECT_CLEANUP_PERMIT_MISMATCH",)
    assert cleanup_port.calls == ()
    assert recovery_operations.transitions == ()


def test_cleanup_next_run_target_recovery_object_reports_cleanup_failure_without_transition() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    cleanup_port = _FakeRecoveryObjectCleanupPort(
        failure=_CleanupFailure("LOCAL_DIRECTORY_QUARANTINE_CLEANUP_PAYLOAD_NOT_EMPTY", "Inspect.")
    )

    outcome = cleanup_next_run_target_recovery_object(
        permit=_permit(),
        recovery_operations=recovery_operations,
        cleanup_port=cleanup_port,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.cleaned is False
    assert outcome.validation_codes == ("LOCAL_DIRECTORY_QUARANTINE_CLEANUP_PAYLOAD_NOT_EMPTY",)
    assert outcome.next_action == "Inspect."
    assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert recovery_operations.transitions == ()


class _FakeRecoveryOperationStore(RunTargetRecoveryObjectCleanupOperationStore):
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self.operations = {
            (operation.run_id, operation.operation_id): operation for operation in operations
        }
        self.transitions: tuple[
            tuple[str, RecoveryOperationPhase, RecoveryOperationPhase],
            ...,
        ] = ()

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        raise AssertionError("cleanup bridge should not plan operations")

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: RecoveryOperationMetadata | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if operation is None or operation.phase is not expected_phase:
            return None
        updated = replace(operation, phase=next_phase)
        self.operations[(run_id, operation_id)] = updated
        self.transitions = (*self.transitions, (operation_id, expected_phase, next_phase))
        return updated

    def record_operation_lease_rebound(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        raise AssertionError("cleanup bridge should not rebind leases")

    def record_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        raise AssertionError("cleanup bridge should not refresh commit intent")

    def record_old_target_preserved_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        raise AssertionError("cleanup bridge should not refresh preserved commit intent")

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation:
        operation = self.operations.get((run_id, operation_id))
        if operation is None:
            raise AssertionError("operation should exist")
        return operation

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in sorted(
                self.operations.values(),
                key=lambda item: item.operation_id,
            )
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _FakeRecoveryObjectCleanupPort:
    def __init__(self, *, failure: RuntimeError | None = None) -> None:
        self.calls: tuple[tuple[str, str], ...] = ()
        self._failure = failure

    def cleanup_recovery_objects(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> RecoveryObjectCleanupReceipt:
        self.calls = (*self.calls, (permit.lease_id, operation.operation_id))
        if self._failure is not None:
            raise self._failure
        return RecoveryObjectCleanupReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            cleaned_object_ids=(operation.quarantine_object_id or "",),
        )


class _CleanupFailure(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


def _operation(*, lease_id: str = "lease-a") -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id="op-a",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id=lease_id,
            lease_resource_key="endpoint:target-a",
            fencing_token=1,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        ),
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        staging_object_id="op-a",
        quarantine_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        catalog_handoff_id="final-file:run-a:op-a",
        expected_target_fingerprint_json='{"entry_count":0,"kind":"DIRECTORY_EMPTY"}',
        expected_final_fingerprint_json='{"byte_count":9,"content_hash":"' + ("a" * 64) + '"}',
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
