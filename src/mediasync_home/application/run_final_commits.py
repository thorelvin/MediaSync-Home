from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol

from mediasync_home.application.journaled_commit import JournaledFinalCommitPort
from mediasync_home.application.file_object_fingerprints import (
    FileObjectFingerprintError,
    file_object_fingerprint_from_json,
)
from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    OldTargetPreservationPort,
    RelativePath,
    VerifiedStagingArtifact,
)
from mediasync_home.application.recovery_failure_classification import (
    recovery_phase_for_commit_failure,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.domain.capabilities import MutationPermit


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RunTargetFinalCommitOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetFinalCommitOutcome:
    idle: bool
    committed: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    receipt: CommitReceipt | None
    validation_codes: tuple[str, ...]
    next_action: str


class RunTargetFinalCommitError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


def commit_next_run_target_verified_artifact(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetFinalCommitOperationStore,
    final_commit_port: FinalCommitPort,
    old_target_preservation_port: OldTargetPreservationPort | None = None,
    process_instance_id: str,
) -> RunTargetFinalCommitOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation_id=None,
            validation_code="RUN_TARGET_FINAL_COMMIT_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind final commit execution to the Engine Host process instance.",
        )

    operation = _next_commit_ready_operation(
        permit=permit,
        recovery_operations=recovery_operations,
    )
    if operation is None:
        return RunTargetFinalCommitOutcome(
            idle=True,
            committed=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            receipt=None,
            validation_codes=(),
            next_action="No run-target operation is waiting for final commit.",
        )
    if not _operation_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            validation_code="RUN_TARGET_FINAL_COMMIT_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before applying final filesystem changes.",
        )

    artifact = _verified_artifact(operation)
    if artifact is None:
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            validation_code="RUN_TARGET_FINAL_COMMIT_REQUIRES_VERIFIED_STAGING_ARTIFACT",
            next_action="Stage and verify the operation before applying final filesystem changes.",
        )

    try:
        if operation.phase is RecoveryOperationPhase.OLD_TARGET_PRESERVED:
            receipt = _commit_preserved_target_replacement(
                permit=permit,
                recovery_operations=recovery_operations,
                final_commit_port=final_commit_port,
                operation=operation,
                artifact=artifact,
                process_instance_id=process_instance_id,
            )
        else:
            journaled_commit = JournaledFinalCommitPort(
                recovery_operations=recovery_operations,
                final_commit_port=final_commit_port,
                old_target_preservation_port=old_target_preservation_port,
                process_instance_id=process_instance_id,
            )
            receipt = journaled_commit.commit_verified_artifact(permit, artifact)
    except RuntimeError as exc:
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reload recovery state before retrying final commit.",
            ),
        )

    return RunTargetFinalCommitOutcome(
        idle=False,
        committed=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation.operation_id,
        receipt=receipt,
        validation_codes=(),
        next_action="Final filesystem changes are applied and verified for the operation.",
    )


def _next_commit_ready_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetFinalCommitOperationStore,
) -> RecoveryOperation | None:
    for phase in (
        RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
    ):
        operations = recovery_operations.list_operations_for_run_target_in_phase(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            phase=phase,
            limit=1,
        )
        if operations:
            return operations[0]
    return None


def _operation_matches_permit(*, operation: RecoveryOperation, permit: MutationPermit) -> bool:
    return (
        operation.phase
        in {
            RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        }
        and operation.run_id == permit.run_id
        and operation.run_target_id == permit.run_target_id
        and operation.target_endpoint_id == permit.endpoint_id
        and operation.target_endpoint_revision_id == permit.endpoint_revision_id
        and operation.owner_installation_id == permit.owner_installation_id
        and operation.ownership_epoch == permit.ownership_epoch
        and operation.lease_id == permit.lease_id
        and operation.lease_resource_key == permit.resource_key
        and operation.fencing_token == permit.fencing_token
    )


def _commit_preserved_target_replacement(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetFinalCommitOperationStore,
    final_commit_port: FinalCommitPort,
    operation: RecoveryOperation,
    artifact: VerifiedStagingArtifact,
    process_instance_id: str,
) -> CommitReceipt:
    _validate_preserved_target_replacement(operation)
    try:
        receipt = final_commit_port.commit_verified_artifact(permit, artifact)
    except Exception as exc:
        _record_failure(
            recovery_operations=recovery_operations,
            operation=operation,
            process_instance_id=process_instance_id,
            exc=exc,
        )
        raise

    applied = _transition(
        recovery_operations=recovery_operations,
        operation=operation,
        next_phase=RecoveryOperationPhase.FILESYSTEM_APPLIED,
        process_instance_id=process_instance_id,
        payload={
            "receipt_final_relative_path": receipt.final_relative_path.value,
            "receipt_operation_id": receipt.operation_id,
            "resume_from_phase": RecoveryOperationPhase.OLD_TARGET_PRESERVED.value,
        },
    )
    try:
        _validate_receipt(operation=operation, artifact=artifact, receipt=receipt)
    except RunTargetFinalCommitError as exc:
        _record_failure(
            recovery_operations=recovery_operations,
            operation=applied,
            process_instance_id=process_instance_id,
            exc=exc,
        )
        raise

    durable = _transition(
        recovery_operations=recovery_operations,
        operation=applied,
        next_phase=RecoveryOperationPhase.FINAL_DURABLE,
        process_instance_id=process_instance_id,
        payload={
            "durability_state": receipt.durability_state,
            "file_flush_succeeded": receipt.file_flush_succeeded,
            "write_through_move_used": receipt.write_through_move_used,
        },
        operation_metadata=RecoveryOperationMetadata(
            final_durability_state=receipt.durability_state,
        ),
    )
    _transition(
        recovery_operations=recovery_operations,
        operation=durable,
        next_phase=RecoveryOperationPhase.FINAL_VERIFIED,
        process_instance_id=process_instance_id,
        payload={
            "content_hash": artifact.content_hash,
            "final_relative_path": artifact.relative_path.value,
        },
    )
    return receipt


def _validate_preserved_target_replacement(operation: RecoveryOperation) -> None:
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
        if operation.version_object_id is None and operation.quarantine_object_id is None:
            raise RunTargetFinalCommitError(
                "RUN_TARGET_FINAL_COMMIT_REQUIRES_PRESERVED_OLD_TARGET",
                "Recover or reconcile the preserved old target before applying replacement bytes.",
            )
        return
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
        if operation.quarantine_object_id is None or not operation.quarantine_object_id.strip():
            raise RunTargetFinalCommitError(
                "RUN_TARGET_FINAL_COMMIT_REQUIRES_QUARANTINED_DIRECTORY",
                "Recover or reconcile the quarantined empty directory before applying replacement bytes.",
            )
        return
    raise RunTargetFinalCommitError(
        "RUN_TARGET_FINAL_COMMIT_REQUIRES_PRESERVED_REPLACE_PRECONDITION",
        "Reload recovery state before resuming preserved final-path work.",
    )


def _transition(
    *,
    recovery_operations: RunTargetFinalCommitOperationStore,
    operation: RecoveryOperation,
    next_phase: RecoveryOperationPhase,
    process_instance_id: str,
    payload: Mapping[str, object],
    operation_metadata: RecoveryOperationMetadata | None = None,
) -> RecoveryOperation:
    updated = recovery_operations.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=next_phase,
        process_instance_id=process_instance_id,
        payload=payload,
        operation_metadata=operation_metadata,
    )
    if updated is None:
        raise RunTargetFinalCommitError(
            "RUN_TARGET_FINAL_COMMIT_PHASE_CONFLICT",
            "Reload recovery state or run startup final reverify before retrying final commit.",
        )
    return updated


def _validate_receipt(
    *,
    operation: RecoveryOperation,
    artifact: VerifiedStagingArtifact,
    receipt: CommitReceipt,
) -> None:
    if receipt.operation_id != operation.operation_id:
        raise RunTargetFinalCommitError(
            "RUN_TARGET_FINAL_COMMIT_RECEIPT_OPERATION_MISMATCH",
            "Enter recovery before catalog handoff because the final commit receipt is inconsistent.",
        )
    if _relative_path(receipt.final_relative_path.value) != _relative_path(artifact.relative_path.value):
        raise RunTargetFinalCommitError(
            "RUN_TARGET_FINAL_COMMIT_RECEIPT_PATH_MISMATCH",
            "Enter recovery before catalog handoff because the final commit receipt path is inconsistent.",
        )


def _record_failure(
    *,
    recovery_operations: RunTargetFinalCommitOperationStore,
    operation: RecoveryOperation,
    process_instance_id: str,
    exc: Exception,
) -> None:
    error_code = _error_code(exc)
    updated = recovery_operations.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=recovery_phase_for_commit_failure(
            operation=operation,
            validation_code=error_code,
        ),
        process_instance_id=process_instance_id,
        payload={
            "error_code": error_code,
            "error_type": type(exc).__name__,
        },
        operation_metadata=RecoveryOperationMetadata(last_error_code=error_code),
    )
    if updated is None:
        raise RunTargetFinalCommitError(
            "RUN_TARGET_FINAL_COMMIT_FAILURE_JOURNAL_CONFLICT",
            "Reload recovery state before retrying or resuming the final commit.",
        ) from exc


def _verified_artifact(operation: RecoveryOperation) -> VerifiedStagingArtifact | None:
    if operation.staging_object_id is None or not operation.staging_object_id.strip():
        return None
    content_hash = _content_hash(operation)
    if content_hash is None:
        return None
    return VerifiedStagingArtifact(
        object_id=operation.staging_object_id,
        relative_path=RelativePath(operation.final_relative_path),
        content_hash=content_hash,
        operation_kind=operation.operation_kind,
        fingerprint_json=_complete_fingerprint_json(operation),
    )


def _complete_fingerprint_json(operation: RecoveryOperation) -> str | None:
    for raw_payload in (
        operation.expected_staging_fingerprint_json,
        operation.expected_final_fingerprint_json,
    ):
        if raw_payload is None:
            continue
        try:
            file_object_fingerprint_from_json(
                raw_payload,
                require_named_stream_inventory=True,
            )
        except FileObjectFingerprintError:
            continue
        return raw_payload
    return None


def _content_hash(operation: RecoveryOperation) -> str | None:
    for raw_payload in (
        operation.expected_staging_fingerprint_json,
        operation.expected_final_fingerprint_json,
    ):
        if raw_payload is None:
            continue
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        content_hash = payload.get("content_hash")
        if isinstance(content_hash, str) and HASH_PATTERN.fullmatch(content_hash) is not None:
            return content_hash
    return None


def _relative_path(value: str) -> str:
    return value.replace("\\", "/")


def _failed(
    *,
    permit: MutationPermit,
    operation_id: str | None,
    validation_code: str,
    next_action: str,
) -> RunTargetFinalCommitOutcome:
    return RunTargetFinalCommitOutcome(
        idle=False,
        committed=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation_id,
        receipt=None,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc)
    if message.strip():
        return message
    return type(exc).__name__


def _error_next_action(exc: RuntimeError, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback
