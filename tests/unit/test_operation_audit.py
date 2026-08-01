from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mediasync_home.application.operation_audit import (
    OperationAttemptState,
    OperationAuditViolation,
    OperationAuditWriteResult,
    OperationOutcomeState,
    RecoveryOperationAuditEvent,
    RunProcessAuditEvidence,
    reconcile_next_run_target_operation_audit,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
)


class _RecoveryAuditStore:
    def __init__(
        self,
        operation: RecoveryOperation,
        events: tuple[RecoveryOperationAuditEvent, ...],
        processes: tuple[RunProcessAuditEvidence, ...],
    ) -> None:
        self.operation = operation
        self.events = events
        self.processes = processes

    def list_operations_for_run_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        del run_id, run_target_id
        return (self.operation,)[:limit]

    def list_operation_audit_events(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[RecoveryOperationAuditEvent, ...]:
        del run_id, operation_id
        return self.events[:limit]

    def list_run_process_audit_evidence(
        self,
        *,
        run_id: str,
        limit: int,
    ) -> tuple[RunProcessAuditEvidence, ...]:
        del run_id
        return self.processes[:limit]


class _CatalogAuditStore:
    def __init__(self) -> None:
        self.changed = True
        self.calls: list[tuple[object, object, object]] = []

    def reconcile_operation_audit(
        self,
        *,
        run_attempts: tuple[object, ...],
        operation_attempts: tuple[object, ...],
        operation_outcome: object | None,
    ) -> OperationAuditWriteResult:
        self.calls.append((run_attempts, operation_attempts, operation_outcome))
        changed = self.changed
        self.changed = False
        return OperationAuditWriteResult(changed=changed)


def test_reconcile_derives_failed_and_successful_attempts_before_outcome() -> None:
    operation = replace(
        _operation(),
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        staging_object_id="staging-a",
        transfer_state="TRANSFERRED",
        assurance_level="FULL_HASH",
        staging_durability_state="DURABLE",
        final_durability_state="DURABLE",
        source_guard_kind="FILE_ID",
        source_guard_evidence_hash="a" * 64,
        source_hash_evidence_kind="CURRENT_READ_HASH",
        last_error_code="NETWORK_INTERRUPTED",
        expected_staging_fingerprint_json='{"size":7}',
        expected_final_fingerprint_json='{"size":7}',
    )
    events = (
        _event(
            sequence=0,
            process="process-a",
            from_phase=RecoveryOperationPhase.STAGING_ALLOCATED,
            to_phase=RecoveryOperationPhase.STAGING_ALLOCATED,
            payload={
                "attempt_number": 1,
                "error_code": "NETWORK_INTERRUPTED",
                "event_kind": "STAGING_ATTEMPT_FAILED",
                "operation_audit": {
                    "lease_id": "lease-old",
                    "ownership_epoch": 1,
                    "fencing_token": 3,
                    "staging_object_id": "staging-old",
                },
            },
        ),
        _event(
            sequence=1,
            process="process-b",
            from_phase=RecoveryOperationPhase.STAGING_DURABLE,
            to_phase=RecoveryOperationPhase.STAGING_VERIFIED,
            payload={
                "operation_audit": {
                    "assurance_level": "FULL_HASH",
                    "durability_level": "DURABLE",
                    "lease_id": "lease-a",
                    "ownership_epoch": 2,
                    "fencing_token": 4,
                    "staging_object_id": "staging-a",
                    "transfer_state": "TRANSFERRED",
                }
            },
        ),
        _event(
            sequence=2,
            process="process-b",
            from_phase=RecoveryOperationPhase.FILESYSTEM_APPLIED,
            to_phase=RecoveryOperationPhase.FINAL_DURABLE,
            payload={
                "durability_state": (
                    "LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED"
                ),
                "file_flush_succeeded": True,
                "write_through_move_used": True,
            },
        ),
        _event(
            sequence=3,
            process="process-b",
            from_phase=RecoveryOperationPhase.FINAL_VERIFIED,
            to_phase=RecoveryOperationPhase.CATALOG_RECORDED,
        ),
    )
    recovery = _RecoveryAuditStore(operation, events, _processes())
    catalog = _CatalogAuditStore()

    outcome = reconcile_next_run_target_operation_audit(
        run_id="run-a",
        run_target_id="target-a",
        recovery_operations=recovery,
        operation_audits=catalog,
        max_operations=2,
    )

    assert outcome.changed is True
    assert outcome.terminal_outcomes_complete is True
    run_attempts, attempts, final = catalog.calls[0]
    assert [attempt.attempt_number for attempt in run_attempts] == [1, 2]
    assert [attempt.state for attempt in attempts] == [
        OperationAttemptState.FAILED,
        OperationAttemptState.SUCCEEDED,
    ]
    assert attempts[0].lease_id == "lease-old"
    assert attempts[1].lease_id == "lease-a"
    assert attempts[1].bytes_transferred == 7
    assert attempts[1].transfer_state == "TRANSFERRED"
    assert attempts[1].assurance_level == "PRIMARY_STREAM_HASH_VERIFIED"
    assert attempts[1].durability_level == "UNKNOWN"
    assert final.final_state is OperationOutcomeState.SUCCEEDED
    assert final.bytes_transferred == 7
    assert final.transfer_state == "TRANSFERRED"
    assert final.assurance_level == "PRIMARY_STREAM_HASH_VERIFIED"
    assert final.durability_level == "WRITE_THROUGH_REQUEST_CONFIRMED"
    assert final.hash_evidence_kind == "CURRENT_READ_HASH"
    assert final.error_code is None
    verification = json.loads(final.verification_json)
    assert verification["raw_assurance_level"] == "FULL_HASH"
    assert verification["raw_transfer_state"] == "TRANSFERRED"
    assert verification["final_file_flush_succeeded"] is True
    assert verification["final_write_through_move_used"] is True

    second = reconcile_next_run_target_operation_audit(
        run_id="run-a",
        run_target_id="target-a",
        recovery_operations=recovery,
        operation_audits=catalog,
        max_operations=2,
    )
    assert second.changed is False


def test_reconcile_records_exhausted_retry_as_skipped_outcome() -> None:
    operation = replace(
        _operation(),
        phase=RecoveryOperationPhase.SKIPPED,
        last_error_code="SOURCE_CHANGED_DURING_COPY",
        staging_failure_count=1,
    )
    events = (
        _event(
            sequence=0,
            process="process-a",
            from_phase=RecoveryOperationPhase.SOURCE_VALIDATED,
            to_phase=RecoveryOperationPhase.SKIPPED,
            payload={
                "attempt_number": 1,
                "error_code": "SOURCE_CHANGED_DURING_COPY",
                "event_kind": "STAGING_ATTEMPT_FAILED",
            },
        ),
    )
    catalog = _CatalogAuditStore()

    outcome = reconcile_next_run_target_operation_audit(
        run_id="run-a",
        run_target_id="target-a",
        recovery_operations=_RecoveryAuditStore(
            operation,
            events,
            (_processes()[0],),
        ),
        operation_audits=catalog,
        max_operations=2,
    )

    assert outcome.terminal_outcomes_complete is True
    _, attempts, final = catalog.calls[0]
    assert len(attempts) == 1
    assert attempts[0].state is OperationAttemptState.FAILED
    assert final.final_state is OperationOutcomeState.SKIPPED
    assert final.error_code == "SOURCE_CHANGED_DURING_COPY"


def test_reconcile_does_not_materialize_success_without_transfer_assurance() -> None:
    operation = replace(
        _operation(),
        phase=RecoveryOperationPhase.CLEANED,
        final_durability_state="FINAL_DURABILITY_UNCONFIRMED",
    )
    events = (
        _event(
            sequence=0,
            process="process-a",
            from_phase=RecoveryOperationPhase.FINAL_VERIFIED,
            to_phase=RecoveryOperationPhase.CATALOG_RECORDED,
        ),
    )
    catalog = _CatalogAuditStore()

    outcome = reconcile_next_run_target_operation_audit(
        run_id="run-a",
        run_target_id="target-a",
        recovery_operations=_RecoveryAuditStore(
            operation,
            events,
            (_processes()[0],),
        ),
        operation_audits=catalog,
        max_operations=2,
    )

    assert outcome.terminal_outcomes_complete is False
    assert catalog.calls[0][2] is None


def test_reconcile_rejects_unbounded_operation_page() -> None:
    with pytest.raises(
        OperationAuditViolation,
        match="OPERATION_AUDIT_OPERATION_LIMIT_REACHED",
    ):
        reconcile_next_run_target_operation_audit(
            run_id="run-a",
            run_target_id="target-a",
            recovery_operations=_RecoveryAuditStore(
                _operation(),
                (),
                _processes(),
            ),
            operation_audits=_CatalogAuditStore(),
            max_operations=1,
        )


def _operation() -> RecoveryOperation:
    return RecoveryOperation(
        run_id="run-a",
        run_target_id="target-a",
        operation_id="operation-a",
        target_endpoint_id="endpoint-a",
        target_endpoint_revision_id="endpoint-revision-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=2,
        lease_id="lease-a",
        lease_resource_key="endpoint:endpoint-a",
        fencing_token=4,
        phase=RecoveryOperationPhase.PLANNED,
        final_relative_path="A.txt",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        planned_bytes=7,
    )


def _event(
    *,
    sequence: int,
    process: str,
    from_phase: RecoveryOperationPhase,
    to_phase: RecoveryOperationPhase,
    payload: dict[str, object] | None = None,
) -> RecoveryOperationAuditEvent:
    return RecoveryOperationAuditEvent(
        run_id="run-a",
        run_sequence=sequence,
        operation_id="operation-a",
        from_phase=from_phase,
        to_phase=to_phase,
        event_utc=f"2026-07-31T10:00:0{sequence}.000Z",
        process_instance_id=process,
        payload={} if payload is None else payload,
        event_hash=str(sequence) * 64,
    )


def _processes() -> tuple[RunProcessAuditEvidence, ...]:
    return (
        RunProcessAuditEvidence(
            process_instance_id="process-a",
            first_run_sequence=0,
            first_event_utc="2026-07-31T10:00:00.000Z",
            last_event_utc="2026-07-31T10:00:00.000Z",
        ),
        RunProcessAuditEvidence(
            process_instance_id="process-b",
            first_run_sequence=1,
            first_event_utc="2026-07-31T10:00:01.000Z",
            last_event_utc="2026-07-31T10:00:02.000Z",
        ),
    )
