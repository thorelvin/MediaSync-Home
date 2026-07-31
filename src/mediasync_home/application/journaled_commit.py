from __future__ import annotations

from typing import Mapping

from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    OldTargetPreservationPort,
    OldTargetPreservationReceipt,
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


class JournaledFinalCommitError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class JournaledFinalCommitPort(FinalCommitPort):
    def __init__(
        self,
        *,
        recovery_operations: RecoveryOperationStore,
        final_commit_port: FinalCommitPort,
        old_target_preservation_port: OldTargetPreservationPort | None = None,
        process_instance_id: str,
    ) -> None:
        if not process_instance_id.strip():
            raise JournaledFinalCommitError(
                "RECOVERY_COMMIT_REQUIRES_PROCESS_INSTANCE",
                "Bind the journaled commit runner to the Engine Host process instance.",
            )
        self._recovery_operations = recovery_operations
        self._final_commit_port = final_commit_port
        self._old_target_preservation_port = old_target_preservation_port
        self._process_instance_id = process_instance_id

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        operation = self._load_and_validate_operation(permit=permit, artifact=artifact)
        preconditions = self._transition(
            operation,
            RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            payload={
                "artifact_object_id": artifact.object_id,
                "content_hash": artifact.content_hash,
                "final_relative_path": artifact.relative_path.value,
            },
        )
        commit_source = preconditions
        if _requires_old_target_preservation(preconditions):
            preservation_port = self._old_target_preservation_port
            if preservation_port is None:
                raise JournaledFinalCommitError(
                    "RECOVERY_COMMIT_REQUIRES_OLD_TARGET_PRESERVATION_PORT",
                    "Use a final commit adapter that preserves the old target before replacement.",
                )
            try:
                preservation_receipt = preservation_port.preserve_old_target(permit, preconditions)
                _validate_preservation_receipt(
                    operation=preconditions,
                    receipt=preservation_receipt,
                )
            except Exception as exc:
                self._record_failure(operation=preconditions, exc=exc)
                raise
            commit_source = self._transition(
                preconditions,
                RecoveryOperationPhase.OLD_TARGET_PRESERVED,
                payload={
                    "final_relative_path": preservation_receipt.final_relative_path.value,
                    "fingerprint_json": preservation_receipt.fingerprint_json,
                    "quarantine_object_id": preservation_receipt.quarantine_object_id,
                    "version_object_id": preservation_receipt.version_object_id,
                },
                operation_metadata=RecoveryOperationMetadata(
                    quarantine_object_id=preservation_receipt.quarantine_object_id,
                    version_object_id=preservation_receipt.version_object_id,
                ),
            )
        try:
            receipt = self._final_commit_port.commit_verified_artifact(permit, artifact)
        except Exception as exc:
            self._record_failure(operation=commit_source, exc=exc)
            raise

        applied = self._transition(
            commit_source,
            RecoveryOperationPhase.FILESYSTEM_APPLIED,
            payload={
                "receipt_final_relative_path": receipt.final_relative_path.value,
                "receipt_operation_id": receipt.operation_id,
            },
        )
        try:
            _validate_receipt(operation=operation, artifact=artifact, receipt=receipt)
        except JournaledFinalCommitError as exc:
            self._record_failure(operation=applied, exc=exc)
            raise

        durable = self._transition(
            applied,
            RecoveryOperationPhase.FINAL_DURABLE,
            payload={"durability_state": "FINAL_COMMIT_ADAPTER_COMPLETED"},
            operation_metadata=RecoveryOperationMetadata(
                final_durability_state="FINAL_COMMIT_ADAPTER_COMPLETED",
            ),
        )
        self._transition(
            durable,
            RecoveryOperationPhase.FINAL_VERIFIED,
            payload={
                "content_hash": artifact.content_hash,
                "final_relative_path": artifact.relative_path.value,
            },
        )
        return receipt

    def _load_and_validate_operation(
        self,
        *,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> RecoveryOperation:
        operation = self._recovery_operations.load_operation(
            run_id=permit.run_id,
            operation_id=artifact.object_id,
        )
        if operation is None:
            raise JournaledFinalCommitError(
                "RECOVERY_COMMIT_OPERATION_NOT_FOUND",
                "Record the recovery operation and durable commit intent before final commit.",
            )
        _validate_operation_binding(operation=operation, permit=permit, artifact=artifact)
        _validate_target_precondition_support(
            operation=operation,
            old_target_preservation_port=self._old_target_preservation_port,
        )
        return operation

    def _transition(
        self,
        operation: RecoveryOperation,
        next_phase: RecoveryOperationPhase,
        *,
        payload: Mapping[str, object],
        operation_metadata: RecoveryOperationMetadata | None = None,
    ) -> RecoveryOperation:
        updated = self._recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=next_phase,
            process_instance_id=self._process_instance_id,
            payload=payload,
            operation_metadata=operation_metadata,
        )
        if updated is None:
            raise JournaledFinalCommitError(
                "RECOVERY_COMMIT_PHASE_CONFLICT",
                "Reload the recovery operation before attempting final commit.",
            )
        return updated

    def _record_failure(self, *, operation: RecoveryOperation, exc: Exception) -> None:
        error_code = _error_code(exc)
        updated = self._recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=recovery_phase_for_commit_failure(
                operation=operation,
                validation_code=error_code,
            ),
            process_instance_id=self._process_instance_id,
            payload={
                "error_code": error_code,
                "error_type": type(exc).__name__,
            },
            operation_metadata=RecoveryOperationMetadata(last_error_code=error_code),
        )
        if updated is None:
            raise JournaledFinalCommitError(
                "RECOVERY_COMMIT_FAILURE_JOURNAL_CONFLICT",
                "Reload recovery state before retrying or resuming the final commit.",
            ) from exc


def _validate_operation_binding(
    *,
    operation: RecoveryOperation,
    permit: MutationPermit,
    artifact: VerifiedStagingArtifact,
) -> None:
    if operation.phase is not RecoveryOperationPhase.COMMIT_INTENT_RECORDED:
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_REQUIRES_COMMIT_INTENT",
            "Record durable commit intent before applying final filesystem changes.",
        )
    if operation.staging_object_id is not None and operation.staging_object_id != artifact.object_id:
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_STAGING_OBJECT_MISMATCH",
            "Restage or reload the operation before final commit.",
        )
    if operation.operation_kind is not artifact.operation_kind:
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_OPERATION_KIND_MISMATCH",
            "Reload the verified staging artifact before final commit.",
        )
    if _relative_path(operation.final_relative_path) != _relative_path(artifact.relative_path.value):
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_FINAL_PATH_MISMATCH",
            "Reload the sealed operation before final commit.",
        )
    if (
        operation.run_id != permit.run_id
        or operation.run_target_id != permit.run_target_id
        or operation.target_endpoint_id != permit.endpoint_id
        or operation.target_endpoint_revision_id != permit.endpoint_revision_id
        or operation.owner_installation_id != permit.owner_installation_id
        or operation.ownership_epoch != permit.ownership_epoch
        or operation.lease_id != permit.lease_id
        or operation.lease_resource_key != permit.resource_key
        or operation.fencing_token != permit.fencing_token
    ):
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_PERMIT_MISMATCH",
            "Reject the permit and reacquire the endpoint lease for this operation.",
        )


def _validate_target_precondition_support(
    *,
    operation: RecoveryOperation,
    old_target_preservation_port: OldTargetPreservationPort | None,
) -> None:
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.ABSENT:
        return
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
        if old_target_preservation_port is None:
            raise JournaledFinalCommitError(
                "RECOVERY_COMMIT_REQUIRES_OLD_TARGET_PRESERVATION_PORT",
                "Use a final commit adapter that preserves the old target before replacement.",
            )
        return
    if operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
        if old_target_preservation_port is None:
            raise JournaledFinalCommitError(
                "RECOVERY_COMMIT_REQUIRES_DIRECTORY_QUARANTINE_PORT",
                "Use a final commit adapter that quarantines empty directories before replacement.",
            )
        return
    raise JournaledFinalCommitError(
        "RECOVERY_COMMIT_REQUIRES_TARGET_PRECONDITION",
        "Refresh the sealed operation with an explicit mutating target precondition.",
    )


def _requires_old_target_preservation(operation: RecoveryOperation) -> bool:
    return operation.target_precondition_kind in {
        RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
    }


def _validate_preservation_receipt(
    *,
    operation: RecoveryOperation,
    receipt: OldTargetPreservationReceipt,
) -> None:
    if receipt.operation_id != operation.operation_id:
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_PRESERVATION_OPERATION_MISMATCH",
            "Enter recovery because the old-target preservation receipt is inconsistent.",
        )
    if _relative_path(receipt.final_relative_path.value) != _relative_path(operation.final_relative_path):
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_PRESERVATION_PATH_MISMATCH",
            "Enter recovery because the old-target preservation receipt path is inconsistent.",
        )
    if receipt.version_object_id is None and receipt.quarantine_object_id is None:
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_PRESERVATION_REQUIRES_OBJECT_ID",
            "Record the preserved old target as a version or quarantine object before replacement.",
        )


def _validate_receipt(
    *,
    operation: RecoveryOperation,
    artifact: VerifiedStagingArtifact,
    receipt: CommitReceipt,
) -> None:
    if receipt.operation_id != operation.operation_id:
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_RECEIPT_OPERATION_MISMATCH",
            "Enter recovery before catalog handoff because the final commit receipt is inconsistent.",
        )
    if _relative_path(receipt.final_relative_path.value) != _relative_path(artifact.relative_path.value):
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_RECEIPT_PATH_MISMATCH",
            "Enter recovery before catalog handoff because the final commit receipt path is inconsistent.",
        )


def _relative_path(value: str) -> str:
    return value.replace("\\", "/")


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    return type(exc).__name__
