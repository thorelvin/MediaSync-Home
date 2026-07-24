from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from mediasync_home.application.catalog_handoff import (
    FinalFileCatalogHandoff,
    FinalFileCatalogHandoffStore,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_catalog_handoffs import (
    RunTargetCatalogHandoffOperationStore,
    record_next_run_target_catalog_handoff,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_record_next_run_target_catalog_handoff_records_catalog_and_recovery() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    catalog_handoffs = _FakeCatalogHandoffStore()

    outcome = record_next_run_target_catalog_handoff(
        permit=_permit(),
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.idle is False
    assert outcome.recorded is True
    assert outcome.validation_codes == ()
    assert outcome.operation_id == "op-a"
    assert outcome.handoff_id == "final-file:run-a:op-a"
    assert outcome.handoff_outcome is not None
    assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert operation.catalog_handoff_id == "final-file:run-a:op-a"
    assert catalog_handoffs.records["final-file:run-a:op-a"].content_hash == "a" * 64
    assert recovery_operations.transitions == (
        ("op-a", RecoveryOperationPhase.FINAL_VERIFIED, RecoveryOperationPhase.CATALOG_RECORDED),
    )


def test_record_next_run_target_catalog_handoff_reports_idle_without_final_verified_operation() -> None:
    outcome = record_next_run_target_catalog_handoff(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore(()),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
    )

    assert outcome.idle is True
    assert outcome.recorded is False
    assert outcome.validation_codes == ()


def test_record_next_run_target_catalog_handoff_rejects_permit_mismatch() -> None:
    catalog_handoffs = _FakeCatalogHandoffStore()

    outcome = record_next_run_target_catalog_handoff(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((_operation(lease_id="other-lease"),)),
        catalog_handoffs=catalog_handoffs,
        process_instance_id="host-a",
    )

    assert outcome.recorded is False
    assert outcome.validation_codes == ("RUN_TARGET_CATALOG_HANDOFF_PERMIT_MISMATCH",)
    assert catalog_handoffs.records == {}


def test_record_next_run_target_catalog_handoff_requires_content_hash() -> None:
    operation = replace(
        _operation(),
        expected_final_fingerprint_json=None,
        expected_staging_fingerprint_json='{"byte_count":128}',
    )
    catalog_handoffs = _FakeCatalogHandoffStore()

    outcome = record_next_run_target_catalog_handoff(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((operation,)),
        catalog_handoffs=catalog_handoffs,
        process_instance_id="host-a",
    )

    assert outcome.recorded is False
    assert outcome.validation_codes == ("RUN_TARGET_CATALOG_HANDOFF_REQUIRES_CONTENT_HASH",)
    assert catalog_handoffs.records == {}


def test_record_next_run_target_catalog_handoff_reports_persistence_failure() -> None:
    outcome = record_next_run_target_catalog_handoff(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((_operation(),)),
        catalog_handoffs=_FakeCatalogHandoffStore(failure=ValueError("CATALOG_HANDOFF_PERSISTENCE_FAILED")),
        process_instance_id="host-a",
    )

    assert outcome.recorded is False
    assert outcome.validation_codes == ("CATALOG_HANDOFF_PERSISTENCE_FAILED",)


class _FakeRecoveryOperationStore(RunTargetCatalogHandoffOperationStore):
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
        raise AssertionError("catalog handoff bridge should not plan operations")

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
        operation_metadata: object | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if operation is None or operation.phase is not expected_phase:
            return None
        updated = replace(
            operation,
            phase=next_phase,
            catalog_handoff_id=catalog_handoff_id,
        )
        self.operations[(run_id, operation_id)] = updated
        self.transitions = (*self.transitions, (operation_id, expected_phase, next_phase))
        return updated

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


class _FakeCatalogHandoffStore(FinalFileCatalogHandoffStore):
    def __init__(self, *, failure: ValueError | None = None) -> None:
        self.records: dict[str, FinalFileCatalogHandoff] = {}
        self._failure = failure

    def record_final_file_handoff(
        self,
        handoff: FinalFileCatalogHandoff,
    ) -> FinalFileCatalogHandoff:
        if self._failure is not None:
            raise self._failure
        existing = self.records.get(handoff.handoff_id)
        if existing is not None:
            return existing
        self.records[handoff.handoff_id] = handoff
        return handoff

    def load_final_file_handoff(self, handoff_id: str) -> FinalFileCatalogHandoff | None:
        return self.records.get(handoff_id)


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
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        ),
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        staging_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        expected_final_fingerprint_json='{"content_hash":"' + ("a" * 64) + '"}',
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
