from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    TERMINAL_PHASES,
)
from mediasync_home.application.verification_results import (
    AssuranceLevel,
    DurabilityState,
    TransferState,
    canonical_assurance_level,
    canonical_durability_state,
    canonical_transfer_state,
)


MAX_OPERATION_AUDIT_TARGET_OPERATIONS = 10_000
MAX_OPERATION_AUDIT_EVENTS_PER_OPERATION = 128
MAX_OPERATION_AUDIT_RUN_PROCESSES = 128


class OperationAuditViolation(ValueError):
    pass


class OperationAttemptState(str, Enum):
    FAILED = "FAILED"
    SUCCEEDED = "SUCCEEDED"


class OperationOutcomeState(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class RecoveryOperationAuditEvent:
    run_id: str
    run_sequence: int
    operation_id: str
    from_phase: RecoveryOperationPhase | None
    to_phase: RecoveryOperationPhase
    event_utc: str
    process_instance_id: str
    payload: Mapping[str, object]
    event_hash: str


@dataclass(frozen=True)
class RunProcessAuditEvidence:
    process_instance_id: str
    first_run_sequence: int
    first_event_utc: str
    last_event_utc: str


@dataclass(frozen=True)
class RunAttemptAudit:
    id: str
    run_id: str
    attempt_number: int
    process_instance_id: str
    started_utc: str
    finished_utc: str | None = None
    termination_reason: str | None = None


@dataclass(frozen=True)
class OperationAttemptAudit:
    id: str
    run_attempt_id: str
    run_id: str
    run_target_id: str
    operation_id: str
    attempt_number: int
    state: OperationAttemptState
    process_instance_id: str
    finished_utc: str
    batch_id: str | None
    lease_id: str | None
    ownership_epoch: int | None
    fencing_token: int | None
    source_guard_kind: str | None
    source_guard_evidence_hash: str | None
    transfer_state: str | None
    assurance_level: str | None
    durability_level: str | None
    bytes_transferred: int
    verification_json: str | None
    error_code: str | None
    started_utc: str | None = None
    duration_ms: int | None = None
    robocopy_exit_code: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class OperationOutcomeAudit:
    run_id: str
    run_target_id: str
    operation_id: str
    final_state: OperationOutcomeState
    bytes_transferred: int
    transfer_state: str
    assurance_level: str
    hash_evidence_kind: str | None
    durability_level: str
    verification_json: str | None
    error_code: str | None
    completed_utc: str
    error_message: str | None = None


@dataclass(frozen=True)
class OperationAuditWriteResult:
    changed: bool
    run_attempts_inserted: int = 0
    operation_attempts_inserted: int = 0
    operation_outcome_inserted: bool = False


@dataclass(frozen=True)
class RunTargetOperationAuditOutcome:
    changed: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    terminal_outcomes_complete: bool
    next_action: str


class OperationAuditRecoveryStore(Protocol):
    def list_operations_for_run_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...

    def list_operation_audit_events(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[RecoveryOperationAuditEvent, ...]: ...

    def list_run_process_audit_evidence(
        self,
        *,
        run_id: str,
        limit: int,
    ) -> tuple[RunProcessAuditEvidence, ...]: ...


class OperationAuditCatalogStore(Protocol):
    def reconcile_operation_audit(
        self,
        *,
        run_attempts: tuple[RunAttemptAudit, ...],
        operation_attempts: tuple[OperationAttemptAudit, ...],
        operation_outcome: OperationOutcomeAudit | None,
    ) -> OperationAuditWriteResult: ...


def reconcile_next_run_target_operation_audit(
    *,
    run_id: str,
    run_target_id: str,
    recovery_operations: OperationAuditRecoveryStore,
    operation_audits: OperationAuditCatalogStore,
    max_operations: int,
) -> RunTargetOperationAuditOutcome:
    if not run_id.strip() or not run_target_id.strip():
        raise OperationAuditViolation("OPERATION_AUDIT_REQUIRES_TARGET_IDENTIFIERS")
    if max_operations < 1:
        raise OperationAuditViolation("OPERATION_AUDIT_REQUIRES_POSITIVE_LIMIT")
    if max_operations > MAX_OPERATION_AUDIT_TARGET_OPERATIONS:
        raise OperationAuditViolation("OPERATION_AUDIT_LIMIT_TOO_LARGE")

    operations = recovery_operations.list_operations_for_run_target(
        run_id=run_id,
        run_target_id=run_target_id,
        limit=max_operations,
    )
    if len(operations) >= max_operations:
        raise OperationAuditViolation("OPERATION_AUDIT_OPERATION_LIMIT_REACHED")
    process_evidence = recovery_operations.list_run_process_audit_evidence(
        run_id=run_id,
        limit=MAX_OPERATION_AUDIT_RUN_PROCESSES + 1,
    )
    if len(process_evidence) > MAX_OPERATION_AUDIT_RUN_PROCESSES:
        raise OperationAuditViolation("OPERATION_AUDIT_PROCESS_LIMIT_REACHED")
    run_attempts = _run_attempts(run_id, process_evidence)
    run_attempt_ids = {
        attempt.process_instance_id: attempt.id for attempt in run_attempts
    }

    terminal_outcomes_complete = True
    for operation in operations:
        events = recovery_operations.list_operation_audit_events(
            run_id=run_id,
            operation_id=operation.operation_id,
            limit=MAX_OPERATION_AUDIT_EVENTS_PER_OPERATION + 1,
        )
        if len(events) > MAX_OPERATION_AUDIT_EVENTS_PER_OPERATION:
            raise OperationAuditViolation("OPERATION_AUDIT_EVENT_LIMIT_REACHED")
        attempts = _operation_attempts(
            operation,
            events=events,
            run_attempt_ids=run_attempt_ids,
        )
        outcome = _operation_outcome(operation, events=events)
        if operation.phase in TERMINAL_PHASES and outcome is None:
            terminal_outcomes_complete = False
        write = operation_audits.reconcile_operation_audit(
            run_attempts=run_attempts,
            operation_attempts=attempts,
            operation_outcome=outcome,
        )
        if write.changed:
            return RunTargetOperationAuditOutcome(
                changed=True,
                run_id=run_id,
                run_target_id=run_target_id,
                operation_id=operation.operation_id,
                terminal_outcomes_complete=terminal_outcomes_complete,
                next_action="Durable operation-attempt evidence was reconciled to the catalog.",
            )

    return RunTargetOperationAuditOutcome(
        changed=False,
        run_id=run_id,
        run_target_id=run_target_id,
        operation_id=None,
        terminal_outcomes_complete=terminal_outcomes_complete,
        next_action="Operation-attempt evidence is current.",
    )


def _run_attempts(
    run_id: str,
    evidence: tuple[RunProcessAuditEvidence, ...],
) -> tuple[RunAttemptAudit, ...]:
    ordered = sorted(
        evidence,
        key=lambda item: (item.first_run_sequence, item.process_instance_id),
    )
    return tuple(
        RunAttemptAudit(
            id=_audit_id("run-attempt", run_id, item.process_instance_id),
            run_id=run_id,
            attempt_number=index,
            process_instance_id=item.process_instance_id,
            started_utc=item.first_event_utc,
        )
        for index, item in enumerate(ordered, start=1)
    )


def _operation_attempts(
    operation: RecoveryOperation,
    *,
    events: tuple[RecoveryOperationAuditEvent, ...],
    run_attempt_ids: Mapping[str, str],
) -> tuple[OperationAttemptAudit, ...]:
    attempts: list[OperationAttemptAudit] = []
    highest_failure = 0
    for event in events:
        if event.payload.get("event_kind") != "STAGING_ATTEMPT_FAILED":
            continue
        attempt_number = _positive_int(event.payload.get("attempt_number"))
        if attempt_number is None:
            raise OperationAuditViolation("OPERATION_AUDIT_FAILURE_ATTEMPT_INVALID")
        highest_failure = max(highest_failure, attempt_number)
        attempts.append(
            _attempt_from_event(
                operation,
                event=event,
                run_attempt_ids=run_attempt_ids,
                attempt_number=attempt_number,
                state=OperationAttemptState.FAILED,
                error_code=_optional_text(event.payload.get("error_code")),
                bytes_transferred=0,
            )
        )

    verified = next(
        (
            event
            for event in events
            if event.from_phase is RecoveryOperationPhase.STAGING_DURABLE
            and event.to_phase is RecoveryOperationPhase.STAGING_VERIFIED
        ),
        None,
    )
    if verified is not None:
        attempts.append(
            _attempt_from_event(
                operation,
                event=verified,
                run_attempt_ids=run_attempt_ids,
                attempt_number=highest_failure + 1,
                state=OperationAttemptState.SUCCEEDED,
                error_code=None,
                bytes_transferred=operation.planned_bytes,
            )
        )
    return tuple(sorted(attempts, key=lambda item: item.attempt_number))


def _attempt_from_event(
    operation: RecoveryOperation,
    *,
    event: RecoveryOperationAuditEvent,
    run_attempt_ids: Mapping[str, str],
    attempt_number: int,
    state: OperationAttemptState,
    error_code: str | None,
    bytes_transferred: int,
) -> OperationAttemptAudit:
    run_attempt_id = run_attempt_ids.get(event.process_instance_id)
    if run_attempt_id is None:
        raise OperationAuditViolation("OPERATION_AUDIT_RUN_ATTEMPT_MISSING")
    evidence = _audit_evidence(event.payload)
    raw_transfer_state = _evidence_text(
        evidence, "transfer_state", operation.transfer_state
    )
    raw_assurance_level = _evidence_text(
        evidence, "assurance_level", operation.assurance_level
    )
    raw_durability_state = _evidence_text(
        evidence,
        "durability_level",
        operation.staging_durability_state,
    )
    transfer_state = canonical_transfer_state(
        raw_transfer_state,
        fallback=(
            TransferState.TRANSFERRED
            if state is OperationAttemptState.SUCCEEDED
            else TransferState.FAILED
        ),
    )
    assurance_level = canonical_assurance_level(raw_assurance_level)
    durability_level = canonical_durability_state(
        raw_durability_state,
        fallback=(
            DurabilityState.UNKNOWN
            if state is OperationAttemptState.SUCCEEDED
            else DurabilityState.NOT_REQUESTED
        ),
    )
    verification_json = _verification_json(
        operation,
        evidence=evidence,
        raw_transfer_state=raw_transfer_state,
        raw_assurance_level=raw_assurance_level,
        raw_staging_durability_state=raw_durability_state,
    )
    return OperationAttemptAudit(
        id=_audit_id(
            "operation-attempt",
            operation.run_id,
            operation.operation_id,
            str(attempt_number),
        ),
        run_attempt_id=run_attempt_id,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        attempt_number=attempt_number,
        state=state,
        process_instance_id=event.process_instance_id,
        finished_utc=event.event_utc,
        batch_id=_evidence_text(evidence, "staging_object_id", operation.staging_object_id),
        lease_id=_evidence_text(evidence, "lease_id", operation.lease_id),
        ownership_epoch=_evidence_int(
            evidence, "ownership_epoch", operation.ownership_epoch
        ),
        fencing_token=_evidence_int(evidence, "fencing_token", operation.fencing_token),
        source_guard_kind=_evidence_text(
            evidence, "source_guard_kind", operation.source_guard_kind
        ),
        source_guard_evidence_hash=_evidence_text(
            evidence,
            "source_guard_evidence_hash",
            operation.source_guard_evidence_hash,
        ),
        transfer_state=transfer_state.value,
        assurance_level=assurance_level.value,
        durability_level=durability_level.value,
        bytes_transferred=bytes_transferred,
        verification_json=verification_json,
        error_code=error_code,
    )


def _operation_outcome(
    operation: RecoveryOperation,
    *,
    events: tuple[RecoveryOperationAuditEvent, ...],
) -> OperationOutcomeAudit | None:
    state = _outcome_state(operation.phase)
    if state is None:
        return None
    completed_event = _terminal_event(operation.phase, events)
    if completed_event is None:
        return None
    evidence = _audit_evidence(completed_event.payload)
    succeeded = state is OperationOutcomeState.SUCCEEDED
    raw_transfer_state = _evidence_text(
        evidence, "transfer_state", operation.transfer_state
    )
    raw_assurance_level = _evidence_text(
        evidence, "assurance_level", operation.assurance_level
    )
    final_durability_event = _last_event_to_phase(
        events,
        RecoveryOperationPhase.FINAL_DURABLE,
    )
    final_durability_evidence = (
        {} if final_durability_event is None else final_durability_event.payload
    )
    raw_final_durability_state = (
        _optional_text(final_durability_evidence.get("durability_state"))
        or operation.final_durability_state
    )
    transfer_state = canonical_transfer_state(
        raw_transfer_state,
        fallback=(
            TransferState.NOT_STARTED
            if succeeded
            else (
                TransferState.CANCELLED
                if state is OperationOutcomeState.CANCELLED
                else TransferState.FAILED
            )
        ),
    )
    assurance_level = canonical_assurance_level(raw_assurance_level)
    durability_level = canonical_durability_state(
        raw_final_durability_state,
        fallback=(
            DurabilityState.UNKNOWN
            if succeeded
            else DurabilityState.NOT_REQUESTED
        ),
    )
    if succeeded and (
        transfer_state is not TransferState.TRANSFERRED
        or assurance_level is AssuranceLevel.NONE
    ):
        return None
    return OperationOutcomeAudit(
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        final_state=state,
        bytes_transferred=operation.planned_bytes if succeeded else 0,
        transfer_state=transfer_state.value,
        assurance_level=assurance_level.value,
        hash_evidence_kind=operation.source_hash_evidence_kind,
        durability_level=durability_level.value,
        verification_json=_verification_json(
            operation,
            evidence=evidence,
            raw_transfer_state=raw_transfer_state,
            raw_assurance_level=raw_assurance_level,
            raw_staging_durability_state=operation.staging_durability_state,
            raw_final_durability_state=raw_final_durability_state,
            final_durability_evidence=final_durability_evidence,
        ),
        error_code=None if succeeded else operation.last_error_code,
        completed_utc=completed_event.event_utc,
    )


def _outcome_state(phase: RecoveryOperationPhase) -> OperationOutcomeState | None:
    if phase in {
        RecoveryOperationPhase.CATALOG_RECORDED,
        RecoveryOperationPhase.CLEANED,
    }:
        return OperationOutcomeState.SUCCEEDED
    if phase is RecoveryOperationPhase.SKIPPED:
        return OperationOutcomeState.SKIPPED
    if phase is RecoveryOperationPhase.CANCELLED:
        return OperationOutcomeState.CANCELLED
    if phase in {
        RecoveryOperationPhase.CONFLICT,
        RecoveryOperationPhase.DEFERRED,
        RecoveryOperationPhase.FAILED_RETRYABLE,
        RecoveryOperationPhase.FAILED_BLOCKED,
        RecoveryOperationPhase.ROLLBACK_REQUIRED,
        RecoveryOperationPhase.USER_DECISION_REQUIRED,
    }:
        return OperationOutcomeState.RECOVERY_REQUIRED
    return None


def _terminal_event(
    phase: RecoveryOperationPhase,
    events: tuple[RecoveryOperationAuditEvent, ...],
) -> RecoveryOperationAuditEvent | None:
    preferred = (
        RecoveryOperationPhase.CATALOG_RECORDED
        if phase is RecoveryOperationPhase.CLEANED
        else phase
    )
    return next(
        (event for event in reversed(events) if event.to_phase is preferred),
        None,
    )


def _last_event_to_phase(
    events: tuple[RecoveryOperationAuditEvent, ...],
    phase: RecoveryOperationPhase,
) -> RecoveryOperationAuditEvent | None:
    return next(
        (event for event in reversed(events) if event.to_phase is phase),
        None,
    )


def _audit_evidence(payload: Mapping[str, object]) -> Mapping[str, object]:
    evidence = payload.get("operation_audit")
    return evidence if isinstance(evidence, Mapping) else {}


def _verification_json(
    operation: RecoveryOperation,
    *,
    evidence: Mapping[str, object],
    raw_transfer_state: str | None,
    raw_assurance_level: str | None,
    raw_staging_durability_state: str | None,
    raw_final_durability_state: str | None = None,
    final_durability_evidence: Mapping[str, object] | None = None,
) -> str | None:
    final_evidence = (
        {} if final_durability_evidence is None else final_durability_evidence
    )
    values = {
        "expected_final_fingerprint_json": operation.expected_final_fingerprint_json,
        "expected_staging_fingerprint_json": (
            operation.expected_staging_fingerprint_json
        ),
        "source_guard_evidence_hash": _evidence_text(
            evidence,
            "source_guard_evidence_hash",
            operation.source_guard_evidence_hash,
        ),
        "source_guard_kind": _evidence_text(
            evidence, "source_guard_kind", operation.source_guard_kind
        ),
        "raw_assurance_level": raw_assurance_level,
        "raw_final_durability_state": raw_final_durability_state,
        "raw_staging_durability_state": raw_staging_durability_state,
        "raw_transfer_state": raw_transfer_state,
        "final_file_flush_succeeded": _optional_bool(
            final_evidence.get("file_flush_succeeded")
        ),
        "final_write_through_move_used": _optional_bool(
            final_evidence.get("write_through_move_used")
        ),
    }
    populated = {key: value for key, value in values.items() if value is not None}
    if not populated:
        return None
    return json.dumps(populated, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _evidence_text(
    evidence: Mapping[str, object],
    key: str,
    fallback: str | None,
) -> str | None:
    return _optional_text(evidence.get(key)) or fallback


def _evidence_int(
    evidence: Mapping[str, object],
    key: str,
    fallback: int | None,
) -> int | None:
    value = _positive_int(evidence.get(key))
    return fallback if value is None else value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _audit_id(kind: str, *identity: str) -> str:
    payload = "\x1f".join((kind, *identity)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
