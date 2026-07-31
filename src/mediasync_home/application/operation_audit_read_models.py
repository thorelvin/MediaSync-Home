from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


DEFAULT_OPERATION_AUDIT_LIMIT = 25
MAX_OPERATION_AUDIT_LIMIT = 100
MAX_OPERATION_AUDIT_ID_LENGTH = 256


class OperationAuditQueryError(ValueError):
    pass


@dataclass(frozen=True)
class OperationAuditIdentity:
    run_id: str
    run_target_id: str
    operation_id: str
    target_relative_path: str | None


@dataclass(frozen=True)
class OperationAttemptSummary:
    attempt_number: int
    state: str
    process_instance_id: str
    finished_utc: str
    bytes_transferred: int
    batch_id: str | None
    lease_id: str | None
    ownership_epoch: int | None
    fencing_token: int | None
    source_guard_kind: str | None
    source_guard_evidence_hash: str | None
    transfer_state: str | None
    assurance_level: str | None
    durability_level: str | None
    verification_json: str | None
    error_code: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_number": self.attempt_number,
            "state": self.state,
            "process_instance_id": self.process_instance_id,
            "finished_utc": self.finished_utc,
            "bytes_transferred": self.bytes_transferred,
            "batch_id": self.batch_id,
            "lease_id": self.lease_id,
            "ownership_epoch": self.ownership_epoch,
            "fencing_token": self.fencing_token,
            "source_guard_kind": self.source_guard_kind,
            "source_guard_evidence_hash": self.source_guard_evidence_hash,
            "transfer_state": self.transfer_state,
            "assurance_level": self.assurance_level,
            "durability_level": self.durability_level,
            "verification_json": self.verification_json,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class OperationOutcomeSummary:
    final_state: str
    completed_utc: str
    bytes_transferred: int
    transfer_state: str
    assurance_level: str
    hash_evidence_kind: str | None
    durability_level: str
    verification_json: str | None
    error_code: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "final_state": self.final_state,
            "completed_utc": self.completed_utc,
            "bytes_transferred": self.bytes_transferred,
            "transfer_state": self.transfer_state,
            "assurance_level": self.assurance_level,
            "hash_evidence_kind": self.hash_evidence_kind,
            "durability_level": self.durability_level,
            "verification_json": self.verification_json,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class OperationAuditDetail:
    run_id: str
    operation_id: str
    limit: int
    read_model_available: bool
    found: bool
    run_target_id: str | None = None
    target_relative_path: str | None = None
    attempts: tuple[OperationAttemptSummary, ...] = ()
    outcome: OperationOutcomeSummary | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "run_target_id": self.run_target_id,
            "operation_id": self.operation_id,
            "target_relative_path": self.target_relative_path,
            "limit": self.limit,
            "read_model_available": self.read_model_available,
            "found": self.found,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "outcome": None if self.outcome is None else self.outcome.to_dict(),
        }


class OperationAuditReadModelStore(Protocol):
    def load_operation_audit_identity(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationAuditIdentity | None: ...

    def list_operation_attempt_summaries(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[OperationAttemptSummary, ...]: ...

    def load_operation_outcome_summary(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationOutcomeSummary | None: ...


def query_operation_audit(
    *,
    operation_audit_store: OperationAuditReadModelStore | None,
    run_id: str,
    operation_id: str,
    limit: int | None = None,
) -> OperationAuditDetail:
    normalized_run_id = _normalize_id(run_id)
    normalized_operation_id = _normalize_id(operation_id)
    normalized_limit = DEFAULT_OPERATION_AUDIT_LIMIT if limit is None else int(limit)
    if normalized_limit < 1 or normalized_limit > MAX_OPERATION_AUDIT_LIMIT:
        raise OperationAuditQueryError("OPERATION_AUDIT_QUERY_LIMIT_OUT_OF_RANGE")
    if operation_audit_store is None:
        return OperationAuditDetail(
            run_id=normalized_run_id,
            operation_id=normalized_operation_id,
            limit=normalized_limit,
            read_model_available=False,
            found=False,
        )
    identity = operation_audit_store.load_operation_audit_identity(
        run_id=normalized_run_id,
        operation_id=normalized_operation_id,
    )
    if identity is None:
        return OperationAuditDetail(
            run_id=normalized_run_id,
            operation_id=normalized_operation_id,
            limit=normalized_limit,
            read_model_available=True,
            found=False,
        )
    return OperationAuditDetail(
        run_id=identity.run_id,
        run_target_id=identity.run_target_id,
        operation_id=identity.operation_id,
        target_relative_path=identity.target_relative_path,
        limit=normalized_limit,
        read_model_available=True,
        found=True,
        attempts=operation_audit_store.list_operation_attempt_summaries(
            run_id=normalized_run_id,
            operation_id=normalized_operation_id,
            limit=normalized_limit,
        ),
        outcome=operation_audit_store.load_operation_outcome_summary(
            run_id=normalized_run_id,
            operation_id=normalized_operation_id,
        ),
    )


def _normalize_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_OPERATION_AUDIT_ID_LENGTH:
        raise OperationAuditQueryError("OPERATION_AUDIT_QUERY_ID_INVALID")
    return normalized
