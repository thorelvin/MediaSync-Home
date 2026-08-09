from __future__ import annotations

from typing import Mapping

from mediasync_home.application.ports import (
    CommitReceipt,
    DirectoryMutationPreparationPort,
    DirectoryMutationPreparationReceipt,
    FinalCommitPort,
    OldTargetPreservationPort,
    OldTargetPreservationReceipt,
    VerifiedStagingArtifact,
)
from mediasync_home.application.directory_recovery import (
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryStore,
    DirectoryRecoveryTransition,
    directory_recovery_id,
)
from mediasync_home.application.recovery_failure_classification import (
    recovery_phase_for_commit_failure,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
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
        directory_recovery_operations: DirectoryRecoveryStore | None = None,
        directory_mutation_preparation_port: (
            DirectoryMutationPreparationPort | None
        ) = None,
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
        self._directory_recovery = directory_recovery_operations
        self._directory_preparation = directory_mutation_preparation_port
        self._process_instance_id = process_instance_id

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        operation = self._load_and_validate_operation(permit=permit, artifact=artifact)
        directory_operation: DirectoryRecoveryOperation | None = None
        directory_kind = _directory_kind(operation)
        if directory_kind is not None and self._directory_recovery is not None:
            try:
                directory_operation = self._prepare_directory_mutation(
                    permit=permit,
                    operation=operation,
                    artifact=artifact,
                    kind=directory_kind,
                )
            except Exception as exc:
                self._record_failure(operation=operation, exc=exc)
                raise
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
                    "version_created_utc": preservation_receipt.version_created_utc,
                    "version_retention_until_utc": (
                        preservation_receipt.version_retention_until_utc
                    ),
                    "version_manifest_hash": preservation_receipt.version_manifest_hash,
                },
                operation_metadata=RecoveryOperationMetadata(
                    quarantine_object_id=preservation_receipt.quarantine_object_id,
                    version_object_id=preservation_receipt.version_object_id,
                    version_created_utc=preservation_receipt.version_created_utc,
                    version_retention_until_utc=(
                        preservation_receipt.version_retention_until_utc
                    ),
                    version_manifest_hash=preservation_receipt.version_manifest_hash,
                ),
            )
            if (
                directory_kind is DirectoryRecoveryKind.QUARANTINE
                and self._directory_recovery is not None
            ):
                if directory_operation is None:
                    raise JournaledFinalCommitError(
                        "RECOVERY_DIRECTORY_QUARANTINE_JOURNAL_MISSING",
                        "Reload the dedicated directory recovery state before replacement.",
                    )
                directory_operation = self._advance_directory_to(
                    directory_operation,
                    target_index=4,
                    payload={
                        "final_relative_path": preservation_receipt.final_relative_path.value,
                        "quarantine_object_id": preservation_receipt.quarantine_object_id,
                        "version_manifest_hash": preservation_receipt.version_manifest_hash,
                    },
                    managed_object_id=preservation_receipt.quarantine_object_id,
                )
        try:
            receipt = self._final_commit_port.commit_verified_artifact(permit, artifact)
        except Exception as exc:
            self._record_failure(operation=commit_source, exc=exc)
            raise

        if (
            directory_kind is DirectoryRecoveryKind.CREATE
            and self._directory_recovery is not None
        ):
            if directory_operation is None:
                raise JournaledFinalCommitError(
                    "RECOVERY_DIRECTORY_CREATE_JOURNAL_MISSING",
                    "Reload the dedicated directory recovery state before catalog handoff.",
                )
            directory_operation = self._advance_directory_to(
                directory_operation,
                target_index=3,
                payload={
                    "filesystem_apply_method": receipt.filesystem_apply_method,
                    "final_relative_path": receipt.final_relative_path.value,
                },
            )

        applied = self._transition(
            commit_source,
            RecoveryOperationPhase.FILESYSTEM_APPLIED,
            payload={
                "receipt_final_relative_path": receipt.final_relative_path.value,
                "receipt_operation_id": receipt.operation_id,
                "filesystem_apply_method": receipt.filesystem_apply_method,
            },
        )
        try:
            _validate_receipt(operation=operation, artifact=artifact, receipt=receipt)
        except JournaledFinalCommitError as exc:
            self._record_failure(operation=applied, exc=exc)
            raise

        if (
            directory_kind is DirectoryRecoveryKind.CREATE
            and self._directory_recovery is not None
        ):
            assert directory_operation is not None
            directory_operation = self._advance_directory_to(
                directory_operation,
                target_index=4,
                payload={
                    "artifact_content_hash": artifact.content_hash,
                    "receipt_operation_id": receipt.operation_id,
                },
            )

        durable = self._transition(
            applied,
            RecoveryOperationPhase.FINAL_DURABLE,
            payload={
                "durability_state": receipt.durability_state,
                "file_flush_succeeded": receipt.file_flush_succeeded,
                "write_through_move_used": receipt.write_through_move_used,
                "filesystem_apply_method": receipt.filesystem_apply_method,
            },
            operation_metadata=RecoveryOperationMetadata(
                final_durability_state=receipt.durability_state,
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

    def _prepare_directory_mutation(
        self,
        *,
        permit: MutationPermit,
        operation: RecoveryOperation,
        artifact: VerifiedStagingArtifact,
        kind: DirectoryRecoveryKind,
    ) -> DirectoryRecoveryOperation:
        store = self._directory_recovery
        preparation = self._directory_preparation
        if store is None or preparation is None:
            raise JournaledFinalCommitError(
                "RECOVERY_DIRECTORY_PREPARATION_PORT_MISSING",
                "Configure the directory recovery journal and preparation adapter together.",
            )
        recovery_id = directory_recovery_id(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            kind=kind,
        )
        directory_operation = store.load_directory_recovery_operation(recovery_id)
        if directory_operation is None:
            raise JournaledFinalCommitError(
                "RECOVERY_DIRECTORY_OPERATION_NOT_FOUND",
                "Record the dedicated directory recovery operation before mutation.",
            )
        receipt = (
            preparation.prepare_directory_create(permit, operation, artifact)
            if kind is DirectoryRecoveryKind.CREATE
            else preparation.prepare_directory_quarantine(permit, operation)
        )
        _validate_directory_preparation_receipt(
            operation=operation,
            receipt=receipt,
        )
        return self._advance_directory_to(
            directory_operation,
            target_index=2,
            payload={
                "already_applied": receipt.already_applied,
                "final_relative_path": receipt.final_relative_path.value,
                "observed_state": receipt.observed_state,
            },
        )

    def _advance_directory_to(
        self,
        operation: DirectoryRecoveryOperation,
        *,
        target_index: int,
        payload: Mapping[str, object],
        managed_object_id: str | None = None,
    ) -> DirectoryRecoveryOperation:
        store = self._directory_recovery
        if store is None:
            raise JournaledFinalCommitError(
                "RECOVERY_DIRECTORY_JOURNAL_MISSING",
                "Reload the dedicated directory recovery state before mutation.",
            )
        success_path = SUCCESS_PATH_BY_KIND[operation.kind]
        try:
            current_index = success_path.index(operation.state)
        except ValueError as exc:
            raise JournaledFinalCommitError(
                "RECOVERY_DIRECTORY_OPERATION_CONFLICTED",
                "Resolve the directory recovery conflict before retrying mutation.",
            ) from exc
        if current_index > target_index:
            return operation
        while current_index < target_index:
            next_state = success_path[current_index + 1]
            updated = store.transition_directory_recovery_operation(
                DirectoryRecoveryTransition(
                    recovery_id=operation.recovery_id,
                    expected_state=operation.state,
                    next_state=next_state,
                    process_instance_id=self._process_instance_id,
                    payload={
                        **dict(payload),
                        "directory_state": next_state.value,
                    },
                    managed_object_id=managed_object_id,
                )
            )
            if updated is None:
                raise JournaledFinalCommitError(
                    "RECOVERY_DIRECTORY_PHASE_CONFLICT",
                    "Reload the dedicated directory recovery state before retrying mutation.",
                )
            operation = updated
            current_index += 1
        return operation

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


def _directory_kind(operation: RecoveryOperation) -> DirectoryRecoveryKind | None:
    if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
        return DirectoryRecoveryKind.CREATE
    if (
        operation.target_precondition_kind
        is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY
    ):
        return DirectoryRecoveryKind.QUARANTINE
    return None


def _validate_directory_preparation_receipt(
    *,
    operation: RecoveryOperation,
    receipt: DirectoryMutationPreparationReceipt,
) -> None:
    if receipt.operation_id != operation.operation_id or _relative_path(
        receipt.final_relative_path.value
    ) != _relative_path(operation.final_relative_path):
        raise JournaledFinalCommitError(
            "RECOVERY_DIRECTORY_PREPARATION_RECEIPT_MISMATCH",
            "Reload recovery state because directory preparation evidence is inconsistent.",
        )


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
    if (
        receipt.version_object_id is not None
        or receipt.quarantine_object_id is not None
    ) and not all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            receipt.version_created_utc,
            receipt.version_retention_until_utc,
            receipt.version_manifest_hash,
        )
    ):
        raise JournaledFinalCommitError(
            "RECOVERY_COMMIT_PRESERVATION_REQUIRES_RETENTION_METADATA",
            "Record creation time, retention boundary and manifest hash before replacement.",
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
