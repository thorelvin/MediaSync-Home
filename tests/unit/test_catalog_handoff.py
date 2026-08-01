from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import pytest

from mediasync_home.application.catalog_handoff import (
    CatalogHandoffError,
    CatalogHandoffReconciliationStatus,
    FinalFileCatalogHandoff,
    FinalFileCatalogHandoffStore,
    reconcile_catalog_handoffs_after_startup,
    record_catalog_handoff_after_final_verification,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


def test_catalog_handoff_records_catalog_before_recovery_catalog_recorded() -> None:
    actions: list[str] = []
    recovery = _FakeRecoveryOperationStore(_final_verified_operation(), actions=actions)
    catalog = _FakeCatalogHandoffStore(actions=actions)

    outcome = record_catalog_handoff_after_final_verification(
        run_id="run-a",
        operation_id="operation-a",
        content_hash="a" * 64,
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )

    assert actions == ["catalog", "transition:CATALOG_RECORDED"]
    assert outcome.idempotent_replay is False
    assert outcome.handoff.handoff_id == "final-file:run-a:operation-a"
    assert outcome.recovery_operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert outcome.recovery_operation.catalog_handoff_id == "final-file:run-a:operation-a"
    assert catalog.load_final_file_handoff("final-file:run-a:operation-a") == outcome.handoff
    assert recovery.transitions == [
        _Transition(
            expected_phase=RecoveryOperationPhase.FINAL_VERIFIED,
            next_phase=RecoveryOperationPhase.CATALOG_RECORDED,
            catalog_handoff_id="final-file:run-a:operation-a",
        )
    ]


def test_catalog_handoff_carries_journaled_retained_version_root() -> None:
    operation = replace(
        _final_verified_operation(),
        job_id="job-a",
        job_revision_id="job-rev-a",
        retention_policy="THIRTY_DAYS",
        version_object_id="operation-a",
        version_created_utc="2026-08-01T10:00:00.000Z",
        version_retention_until_utc="2026-08-31T10:00:00.000Z",
        version_manifest_hash="d" * 64,
        expected_target_fingerprint_json=(
            '{"byte_count":9,"content_hash":"' + ("b" * 64) + '"}'
        ),
    )
    recovery = _FakeRecoveryOperationStore(operation)
    catalog = _FakeCatalogHandoffStore()

    outcome = record_catalog_handoff_after_final_verification(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        content_hash="a" * 64,
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )

    retained = outcome.handoff.retained_version
    assert retained is not None
    assert retained.version_object_id == "operation-a"
    assert retained.job_revision_id == "job-rev-a"
    assert retained.retention_until_utc == "2026-08-31T10:00:00.000Z"
    assert retained.manifest_hash == "d" * 64


def test_catalog_handoff_carries_journaled_empty_directory_quarantine() -> None:
    operation = replace(
        _final_verified_operation(),
        target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        job_id="job-a",
        job_revision_id="job-rev-a",
        retention_policy="THIRTY_DAYS",
        quarantine_object_id="operation-a",
        version_created_utc="2026-08-01T10:00:00.000Z",
        version_retention_until_utc="2026-08-31T10:00:00.000Z",
        version_manifest_hash="d" * 64,
        expected_target_fingerprint_json=(
            '{"entry_count":0,"kind":"DIRECTORY_EMPTY"}'
        ),
    )

    outcome = record_catalog_handoff_after_final_verification(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        content_hash="a" * 64,
        recovery_operations=_FakeRecoveryOperationStore(operation),
        catalog_handoffs=_FakeCatalogHandoffStore(),
        process_instance_id="host-a",
    )

    retained = outcome.handoff.retained_version
    assert retained is not None
    assert retained.version_object_id == "operation-a"
    assert retained.object_role == "EMPTY_DIRECTORY_QUARANTINE"
    assert retained.original_fingerprint_json == (
        '{"entry_count":0,"kind":"DIRECTORY_EMPTY"}'
    )


def test_catalog_handoff_refuses_version_without_manifest_evidence() -> None:
    operation = replace(
        _final_verified_operation(),
        version_object_id="operation-a",
    )
    recovery = _FakeRecoveryOperationStore(operation)

    with pytest.raises(CatalogHandoffError) as exc_info:
        record_catalog_handoff_after_final_verification(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            content_hash="a" * 64,
            recovery_operations=recovery,
            catalog_handoffs=_FakeCatalogHandoffStore(),
            process_instance_id="host-a",
        )

    assert exc_info.value.validation_code == "CATALOG_HANDOFF_RETAINED_VERSION_METADATA_MISSING"


def test_catalog_handoff_requires_final_verified_operation() -> None:
    recovery = _FakeRecoveryOperationStore(
        replace(_final_verified_operation(), phase=RecoveryOperationPhase.FILESYSTEM_APPLIED)
    )
    catalog = _FakeCatalogHandoffStore()

    with pytest.raises(CatalogHandoffError) as exc_info:
        record_catalog_handoff_after_final_verification(
            run_id="run-a",
            operation_id="operation-a",
            content_hash="a" * 64,
            recovery_operations=recovery,
            catalog_handoffs=catalog,
            process_instance_id="host-a",
        )

    assert exc_info.value.validation_code == "CATALOG_HANDOFF_REQUIRES_FINAL_VERIFIED"
    assert catalog.records == {}
    assert recovery.transitions == []


def test_catalog_handoff_does_not_advance_recovery_when_catalog_write_fails() -> None:
    recovery = _FakeRecoveryOperationStore(_final_verified_operation())
    catalog = _FakeCatalogHandoffStore(failure=_CatalogFailure("CATALOG_HANDOFF_PERSISTENCE_FAILED"))

    with pytest.raises(_CatalogFailure):
        record_catalog_handoff_after_final_verification(
            run_id="run-a",
            operation_id="operation-a",
            content_hash="a" * 64,
            recovery_operations=recovery,
            catalog_handoffs=catalog,
            process_instance_id="host-a",
        )

    assert recovery.transitions == []


def test_catalog_handoff_reports_recovery_conflict_after_catalog_write() -> None:
    recovery = _FakeRecoveryOperationStore(
        _final_verified_operation(),
        conflict_on=RecoveryOperationPhase.CATALOG_RECORDED,
    )
    catalog = _FakeCatalogHandoffStore()

    with pytest.raises(CatalogHandoffError) as exc_info:
        record_catalog_handoff_after_final_verification(
            run_id="run-a",
            operation_id="operation-a",
            content_hash="a" * 64,
            recovery_operations=recovery,
            catalog_handoffs=catalog,
            process_instance_id="host-a",
        )

    assert exc_info.value.validation_code == "CATALOG_HANDOFF_RECOVERY_PHASE_CONFLICT"
    assert catalog.load_final_file_handoff("final-file:run-a:operation-a") is not None


def test_catalog_handoff_replays_catalog_recorded_operation_without_transition() -> None:
    recovery = _FakeRecoveryOperationStore(
        replace(
            _final_verified_operation(),
            phase=RecoveryOperationPhase.CATALOG_RECORDED,
            catalog_handoff_id="final-file:run-a:operation-a",
        )
    )
    catalog = _FakeCatalogHandoffStore()

    first = record_catalog_handoff_after_final_verification(
        run_id="run-a",
        operation_id="operation-a",
        content_hash="a" * 64,
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )
    second = record_catalog_handoff_after_final_verification(
        run_id="run-a",
        operation_id="operation-a",
        content_hash="a" * 64,
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )

    assert first.idempotent_replay is True
    assert second.idempotent_replay is True
    assert second.handoff == first.handoff
    assert recovery.transitions == []
    assert len(catalog.records) == 1


def test_startup_reconciliation_advances_matching_catalog_handoff_without_catalog_write() -> None:
    actions: list[str] = []
    operation = _final_verified_operation()
    recovery = _FakeRecoveryOperationStore(operation, actions=actions)
    catalog = _FakeCatalogHandoffStore(actions=actions)
    catalog.records["final-file:run-a:operation-a"] = _handoff(operation=operation)

    report = reconcile_catalog_handoffs_after_startup(
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )

    assert actions == ["transition:CATALOG_RECORDED"]
    assert report.scanned == 1
    assert report.recovered[0].status is CatalogHandoffReconciliationStatus.RECOVERED
    assert report.pending == ()
    assert report.ambiguous == ()
    assert report.should_block_mutating_readiness is False
    assert recovery.operation is not None
    assert recovery.operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert recovery.operation.catalog_handoff_id == "final-file:run-a:operation-a"


def test_startup_reconciliation_leaves_final_verified_operation_pending_without_catalog_row() -> None:
    recovery = _FakeRecoveryOperationStore(_final_verified_operation())
    catalog = _FakeCatalogHandoffStore()

    report = reconcile_catalog_handoffs_after_startup(
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )

    assert report.scanned == 1
    assert report.recovered == ()
    assert report.pending[0].status is CatalogHandoffReconciliationStatus.PENDING_CATALOG
    assert report.pending[0].validation_code == "CATALOG_HANDOFF_NOT_COMMITTED"
    assert report.ambiguous == ()
    assert report.should_block_mutating_readiness is False
    assert recovery.transitions == []


def test_startup_reconciliation_blocks_ambiguous_catalog_handoff_mismatch() -> None:
    operation = _final_verified_operation()
    recovery = _FakeRecoveryOperationStore(operation)
    catalog = _FakeCatalogHandoffStore()
    catalog.records["final-file:run-a:operation-a"] = replace(
        _handoff(operation=operation),
        final_relative_path="Photos/other.jpg",
    )

    report = reconcile_catalog_handoffs_after_startup(
        recovery_operations=recovery,
        catalog_handoffs=catalog,
        process_instance_id="host-a",
    )

    assert report.scanned == 1
    assert report.recovered == ()
    assert report.pending == ()
    assert report.ambiguous[0].status is CatalogHandoffReconciliationStatus.AMBIGUOUS
    assert (
        report.ambiguous[0].validation_code
        == "CATALOG_HANDOFF_RECONCILIATION_PAYLOAD_MISMATCH"
    )
    assert report.should_block_mutating_readiness is True
    assert recovery.transitions == []


class _Transition:
    def __init__(
        self,
        *,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        catalog_handoff_id: str | None,
    ) -> None:
        self.expected_phase = expected_phase
        self.next_phase = next_phase
        self.catalog_handoff_id = catalog_handoff_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Transition):
            return NotImplemented
        return (
            self.expected_phase == other.expected_phase
            and self.next_phase == other.next_phase
            and self.catalog_handoff_id == other.catalog_handoff_id
        )


class _FakeRecoveryOperationStore(RecoveryOperationStore):
    def __init__(
        self,
        operation: RecoveryOperation | None,
        *,
        actions: list[str] | None = None,
        conflict_on: RecoveryOperationPhase | None = None,
    ) -> None:
        self.operation = operation
        self._actions = actions
        self._conflict_on = conflict_on
        self.transitions: list[_Transition] = []

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        raise AssertionError("catalog handoff should not plan operations")

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
        if self._actions is not None:
            self._actions.append(f"transition:{next_phase.value}")
        if next_phase is self._conflict_on:
            return None
        if self.operation is None:
            return None
        if (
            self.operation.run_id != run_id
            or self.operation.operation_id != operation_id
            or self.operation.phase is not expected_phase
            or process_instance_id != "host-a"
            or intent_segment_id is not None
            or intent_ordinal is not None
            or next_phase is not RecoveryOperationPhase.CATALOG_RECORDED
            or catalog_handoff_id is None
        ):
            return None
        self.transitions.append(
            _Transition(
                expected_phase=expected_phase,
                next_phase=next_phase,
                catalog_handoff_id=catalog_handoff_id,
            )
        )
        self.operation = replace(
            self.operation,
            phase=next_phase,
            catalog_handoff_id=catalog_handoff_id,
        )
        return self.operation

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        if self.operation is None:
            return None
        if self.operation.run_id == run_id and self.operation.operation_id == operation_id:
            return self.operation
        return None

    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        if self.operation is None or limit < 1 or self.operation.phase is not phase:
            return ()
        return (self.operation,)


class _FakeCatalogHandoffStore(FinalFileCatalogHandoffStore):
    def __init__(
        self,
        *,
        actions: list[str] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self._actions = actions
        self._failure = failure
        self.records: dict[str, FinalFileCatalogHandoff] = {}

    def record_final_file_handoff(
        self,
        handoff: FinalFileCatalogHandoff,
    ) -> FinalFileCatalogHandoff:
        if self._actions is not None:
            self._actions.append("catalog")
        if self._failure is not None:
            raise self._failure
        existing = self.records.get(handoff.handoff_id)
        if existing is not None:
            if existing != handoff:
                raise _CatalogFailure("CATALOG_HANDOFF_IDEMPOTENCY_CONFLICT")
            return existing
        self.records[handoff.handoff_id] = handoff
        return handoff

    def load_final_file_handoff(self, handoff_id: str) -> FinalFileCatalogHandoff | None:
        return self.records.get(handoff_id)


class _CatalogFailure(RuntimeError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


def _final_verified_operation() -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id="operation-a",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id="lease-a",
            lease_resource_key="endpoint:target-a",
            fencing_token=42,
            final_relative_path="Photos/image.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        ),
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        intent_segment_id="segment-a",
        intent_ordinal=0,
    )


def _handoff(*, operation: RecoveryOperation) -> FinalFileCatalogHandoff:
    return FinalFileCatalogHandoff(
        handoff_id=f"final-file:{operation.run_id}:{operation.operation_id}",
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        final_relative_path=operation.final_relative_path,
        content_hash="a" * 64,
        lease_id=operation.lease_id,
        fencing_token=operation.fencing_token,
    )
