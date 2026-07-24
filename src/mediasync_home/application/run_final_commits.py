from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.journaled_commit import JournaledFinalCommitPort
from mediasync_home.application.ports import CommitReceipt, FinalCommitPort, RelativePath, VerifiedStagingArtifact
from mediasync_home.application.ports import OldTargetPreservationPort
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
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

    operation = _next_commit_intent_operation(
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

    journaled_commit = JournaledFinalCommitPort(
        recovery_operations=recovery_operations,
        final_commit_port=final_commit_port,
        old_target_preservation_port=old_target_preservation_port,
        process_instance_id=process_instance_id,
    )
    try:
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


def _next_commit_intent_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetFinalCommitOperationStore,
) -> RecoveryOperation | None:
    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        limit=1,
    )
    if not operations:
        return None
    return operations[0]


def _operation_matches_permit(*, operation: RecoveryOperation, permit: MutationPermit) -> bool:
    return (
        operation.phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED
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
    )


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


def _error_code(exc: RuntimeError) -> str:
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
