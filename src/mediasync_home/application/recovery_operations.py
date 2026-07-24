from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class RecoveryOperationViolation(ValueError):
    pass


class RecoveryOperationPhase(str, Enum):
    PLANNED = "PLANNED"
    SOURCE_VALIDATED = "SOURCE_VALIDATED"
    SOURCE_STABILITY_BOUND = "SOURCE_STABILITY_BOUND"
    TARGET_PRECONDITION_VALIDATED = "TARGET_PRECONDITION_VALIDATED"
    STAGING_ALLOCATED = "STAGING_ALLOCATED"
    TRANSFERRED = "TRANSFERRED"
    STAGING_DURABLE = "STAGING_DURABLE"
    STAGING_VERIFIED = "STAGING_VERIFIED"
    COMMIT_INTENT_RECORDED = "COMMIT_INTENT_RECORDED"
    COMMIT_PRECONDITIONS_REVALIDATED = "COMMIT_PRECONDITIONS_REVALIDATED"
    OLD_TARGET_PRESERVED = "OLD_TARGET_PRESERVED"
    FILESYSTEM_APPLIED = "FILESYSTEM_APPLIED"
    FINAL_DURABLE = "FINAL_DURABLE"
    FINAL_VERIFIED = "FINAL_VERIFIED"
    CATALOG_RECORDED = "CATALOG_RECORDED"
    CLEANED = "CLEANED"
    SKIPPED = "SKIPPED"
    CONFLICT = "CONFLICT"
    DEFERRED = "DEFERRED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_BLOCKED = "FAILED_BLOCKED"
    CANCELLED = "CANCELLED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"


class RecoveryTargetPreconditionKind(str, Enum):
    ABSENT = "ABSENT"
    MATCH_FINGERPRINT = "MATCH_FINGERPRINT"
    DIRECTORY_EMPTY = "DIRECTORY_EMPTY"
    NONE = "NONE"


@dataclass(frozen=True)
class RecoveryOperation:
    run_id: str
    run_target_id: str
    operation_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    endpoint_generation: int
    owner_installation_id: str
    ownership_epoch: int
    lease_id: str
    lease_resource_key: str
    fencing_token: int
    phase: RecoveryOperationPhase
    final_relative_path: str
    target_precondition_kind: RecoveryTargetPreconditionKind
    source_endpoint_id: str | None = None
    source_endpoint_revision_id: str | None = None
    source_relative_path: str | None = None
    source_guard_kind: str | None = None
    source_guard_evidence_hash: str | None = None
    source_hash_evidence_kind: str | None = None
    source_path_chain_hash: str | None = None
    source_case_context_hash: str | None = None
    staging_object_id: str | None = None
    version_object_id: str | None = None
    quarantine_object_id: str | None = None
    intent_segment_id: str | None = None
    intent_ordinal: int | None = None
    expected_source_fingerprint_json: str | None = None
    expected_target_fingerprint_json: str | None = None
    expected_source_parent_identity_json: str | None = None
    expected_target_parent_identity_json: str | None = None
    expected_target_path_chain_hash: str | None = None
    expected_staging_fingerprint_json: str | None = None
    expected_final_fingerprint_json: str | None = None
    observed_target_file_id: str | None = None
    transfer_state: str | None = None
    assurance_level: str | None = None
    staging_durability_state: str | None = None
    final_durability_state: str | None = None
    catalog_handoff_id: str | None = None
    last_error_code: str | None = None


@dataclass(frozen=True)
class RecoveryOperationMetadata:
    source_guard_kind: str | None = None
    source_guard_evidence_hash: str | None = None
    source_hash_evidence_kind: str | None = None
    staging_object_id: str | None = None
    expected_source_fingerprint_json: str | None = None
    expected_target_fingerprint_json: str | None = None
    expected_staging_fingerprint_json: str | None = None
    expected_final_fingerprint_json: str | None = None
    transfer_state: str | None = None
    assurance_level: str | None = None
    staging_durability_state: str | None = None
    last_error_code: str | None = None


class RecoveryOperationStore(Protocol):
    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation: ...

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
    ) -> RecoveryOperation | None: ...

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
    ) -> RecoveryOperation | None: ...

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None: ...


SUCCESS_TRANSITIONS: Mapping[RecoveryOperationPhase, tuple[RecoveryOperationPhase, ...]] = {
    RecoveryOperationPhase.PLANNED: (RecoveryOperationPhase.SOURCE_VALIDATED,),
    RecoveryOperationPhase.SOURCE_VALIDATED: (RecoveryOperationPhase.SOURCE_STABILITY_BOUND,),
    RecoveryOperationPhase.SOURCE_STABILITY_BOUND: (
        RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
    ),
    RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED: (RecoveryOperationPhase.STAGING_ALLOCATED,),
    RecoveryOperationPhase.STAGING_ALLOCATED: (RecoveryOperationPhase.TRANSFERRED,),
    RecoveryOperationPhase.TRANSFERRED: (RecoveryOperationPhase.STAGING_DURABLE,),
    RecoveryOperationPhase.STAGING_DURABLE: (RecoveryOperationPhase.STAGING_VERIFIED,),
    RecoveryOperationPhase.STAGING_VERIFIED: (RecoveryOperationPhase.COMMIT_INTENT_RECORDED,),
    RecoveryOperationPhase.COMMIT_INTENT_RECORDED: (
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
    ),
    RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED: (
        RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
    ),
    RecoveryOperationPhase.OLD_TARGET_PRESERVED: (RecoveryOperationPhase.FILESYSTEM_APPLIED,),
    RecoveryOperationPhase.FILESYSTEM_APPLIED: (RecoveryOperationPhase.FINAL_DURABLE,),
    RecoveryOperationPhase.FINAL_DURABLE: (RecoveryOperationPhase.FINAL_VERIFIED,),
    RecoveryOperationPhase.FINAL_VERIFIED: (RecoveryOperationPhase.CATALOG_RECORDED,),
    RecoveryOperationPhase.CATALOG_RECORDED: (RecoveryOperationPhase.CLEANED,),
}
TERMINAL_PHASES = {
    RecoveryOperationPhase.CLEANED,
    RecoveryOperationPhase.SKIPPED,
    RecoveryOperationPhase.CONFLICT,
    RecoveryOperationPhase.DEFERRED,
    RecoveryOperationPhase.FAILED_RETRYABLE,
    RecoveryOperationPhase.FAILED_BLOCKED,
    RecoveryOperationPhase.CANCELLED,
    RecoveryOperationPhase.ROLLBACK_REQUIRED,
    RecoveryOperationPhase.USER_DECISION_REQUIRED,
}
PHASES_REQUIRING_INTENT = {
    RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
    RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
    RecoveryOperationPhase.OLD_TARGET_PRESERVED,
    RecoveryOperationPhase.FILESYSTEM_APPLIED,
    RecoveryOperationPhase.FINAL_DURABLE,
    RecoveryOperationPhase.FINAL_VERIFIED,
    RecoveryOperationPhase.CATALOG_RECORDED,
    RecoveryOperationPhase.CLEANED,
}
PHASES_REQUIRING_CATALOG_HANDOFF = {
    RecoveryOperationPhase.CATALOG_RECORDED,
    RecoveryOperationPhase.CLEANED,
}
PRE_COMMIT_LEASE_REBIND_PHASES = (
    RecoveryOperationPhase.PLANNED,
    RecoveryOperationPhase.SOURCE_VALIDATED,
    RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
    RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
    RecoveryOperationPhase.STAGING_ALLOCATED,
    RecoveryOperationPhase.TRANSFERRED,
    RecoveryOperationPhase.STAGING_DURABLE,
    RecoveryOperationPhase.STAGING_VERIFIED,
)


def planned_recovery_operation(
    *,
    run_id: str,
    run_target_id: str,
    operation_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    owner_installation_id: str,
    ownership_epoch: int,
    lease_id: str,
    lease_resource_key: str,
    fencing_token: int,
    final_relative_path: str,
    target_precondition_kind: RecoveryTargetPreconditionKind,
    source_endpoint_id: str | None = None,
    source_endpoint_revision_id: str | None = None,
    source_relative_path: str | None = None,
) -> RecoveryOperation:
    operation = RecoveryOperation(
        run_id=run_id,
        run_target_id=run_target_id,
        operation_id=operation_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        endpoint_generation=endpoint_generation,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        lease_id=lease_id,
        lease_resource_key=lease_resource_key,
        fencing_token=fencing_token,
        phase=RecoveryOperationPhase.PLANNED,
        final_relative_path=final_relative_path,
        target_precondition_kind=target_precondition_kind,
        source_endpoint_id=source_endpoint_id,
        source_endpoint_revision_id=source_endpoint_revision_id,
        source_relative_path=source_relative_path,
    )
    validate_recovery_operation(operation)
    return operation


def validate_recovery_operation(operation: RecoveryOperation) -> None:
    if not _non_empty(
        operation.run_id,
        operation.run_target_id,
        operation.operation_id,
        operation.target_endpoint_id,
        operation.target_endpoint_revision_id,
        operation.owner_installation_id,
        operation.lease_id,
        operation.lease_resource_key,
    ):
        raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_IDENTIFIERS")
    if (
        operation.endpoint_generation < 1
        or operation.ownership_epoch < 1
        or operation.fencing_token < 1
    ):
        raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_POSITIVE_NUMBERS")
    if not _valid_relative_path(operation.final_relative_path):
        raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_RELATIVE_FINAL_PATH")
    if operation.source_relative_path is not None and not _valid_relative_path(
        operation.source_relative_path
    ):
        raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_RELATIVE_SOURCE_PATH")
    _validate_hash("RECOVERY_OPERATION_REQUIRES_SOURCE_GUARD_HASH", operation.source_guard_evidence_hash)
    _validate_hash("RECOVERY_OPERATION_REQUIRES_SOURCE_PATH_CHAIN_HASH", operation.source_path_chain_hash)
    _validate_hash("RECOVERY_OPERATION_REQUIRES_SOURCE_CASE_CONTEXT_HASH", operation.source_case_context_hash)
    _validate_hash(
        "RECOVERY_OPERATION_REQUIRES_TARGET_PATH_CHAIN_HASH",
        operation.expected_target_path_chain_hash,
    )
    if operation.phase in PHASES_REQUIRING_INTENT:
        if operation.intent_segment_id is None or not operation.intent_segment_id.strip():
            raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_INTENT_SEGMENT")
        if operation.intent_ordinal is None or operation.intent_ordinal < 0:
            raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_INTENT_ORDINAL")
    if operation.phase in PHASES_REQUIRING_CATALOG_HANDOFF and (
        operation.catalog_handoff_id is None or not operation.catalog_handoff_id.strip()
    ):
        raise RecoveryOperationViolation("RECOVERY_OPERATION_REQUIRES_CATALOG_HANDOFF")


def validate_recovery_phase_transition(
    from_phase: RecoveryOperationPhase,
    to_phase: RecoveryOperationPhase,
) -> None:
    if from_phase in TERMINAL_PHASES:
        raise RecoveryOperationViolation("RECOVERY_OPERATION_TERMINAL_PHASE_CANNOT_TRANSITION")
    if to_phase in TERMINAL_PHASES:
        return
    if to_phase not in SUCCESS_TRANSITIONS.get(from_phase, ()):
        raise RecoveryOperationViolation("RECOVERY_OPERATION_INVALID_PHASE_TRANSITION")


def _non_empty(*values: str) -> bool:
    return all(value.strip() for value in values)


def _valid_relative_path(value: str) -> bool:
    if not value.strip():
        return False
    if value.startswith(("/", "\\")) or WINDOWS_DRIVE_PATTERN.match(value):
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("//"):
        return False
    parts = normalized.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _validate_hash(validation_code: str, value: str | None) -> None:
    if value is not None and HASH_PATTERN.fullmatch(value) is None:
        raise RecoveryOperationViolation(validation_code)
