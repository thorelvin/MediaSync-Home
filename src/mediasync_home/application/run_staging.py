from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.domain.capabilities import MutationPermit


class RunTargetEndpointWaitRequired(RuntimeError):
    def __init__(self, *, reason_code: str, next_action: str) -> None:
        normalized_reason = reason_code.strip()
        normalized_action = next_action.strip()
        if not normalized_reason or not normalized_action:
            raise ValueError("ENDPOINT_WAIT_SIGNAL_REQUIRES_REASON_AND_ACTION")
        super().__init__(normalized_reason)
        self.reason_code = normalized_reason
        self.next_action = normalized_action


class RunTargetStagingOperationStore(RecoveryOperationStore, Protocol):
    def record_staging_failure(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        expected_failure_count: int,
        next_phase: RecoveryOperationPhase,
        error_code: str,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None: ...

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class SourceValidationEvidence:
    fingerprint_json: str
    hash_evidence_kind: str


@dataclass(frozen=True)
class SourceStabilityEvidence:
    guard_kind: str
    guard_evidence_hash: str


@dataclass(frozen=True)
class TargetPreconditionEvidence:
    fingerprint_json: str


@dataclass(frozen=True)
class StagingAllocation:
    staging_object_id: str


@dataclass(frozen=True)
class StagingTransferEvidence:
    transfer_state: str


@dataclass(frozen=True)
class StagingDurabilityEvidence:
    durability_state: str


@dataclass(frozen=True)
class StagingVerificationEvidence:
    fingerprint_json: str
    final_fingerprint_json: str
    assurance_level: str


class RunTargetStagingPort(Protocol):
    def validate_source_file(
        self, operation: RecoveryOperation
    ) -> SourceValidationEvidence: ...

    def bind_source_stability(
        self, operation: RecoveryOperation
    ) -> SourceStabilityEvidence: ...

    def validate_target_precondition(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence: ...

    def allocate_staging_object(
        self, operation: RecoveryOperation
    ) -> StagingAllocation: ...

    def transfer_to_staging(
        self, operation: RecoveryOperation
    ) -> StagingTransferEvidence: ...

    def ensure_staging_durable(
        self, operation: RecoveryOperation
    ) -> StagingDurabilityEvidence: ...

    def verify_staging_artifact(
        self, operation: RecoveryOperation
    ) -> StagingVerificationEvidence: ...


@dataclass(frozen=True)
class RunTargetStagingOutcome:
    idle: bool
    advanced: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    phase: RecoveryOperationPhase | None
    validation_codes: tuple[str, ...]
    next_action: str
    endpoint_wait_reason_code: str | None = None


STAGING_EXECUTION_PHASES = (
    RecoveryOperationPhase.PLANNED,
    RecoveryOperationPhase.SOURCE_VALIDATED,
    RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
    RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
    RecoveryOperationPhase.STAGING_ALLOCATED,
    RecoveryOperationPhase.TRANSFERRED,
    RecoveryOperationPhase.STAGING_DURABLE,
)
MAX_STAGING_ATTEMPTS = 3
RETRYABLE_STAGING_FAILURE_CODES = frozenset(
    {
        "LOCAL_STAGING_ALLOCATION_FAILED",
        "LOCAL_STAGING_DURABILITY_FAILED",
        "LOCAL_STAGING_SOURCE_FILE_UNREADABLE",
        "LOCAL_STAGING_TARGET_DIRECTORY_EMPTY_READ_FAILED",
        "LOCAL_STAGING_TRANSFER_FAILED",
        "LOCAL_STAGING_VERIFICATION_FAILED",
        "ROBOCOPY_PROCESS_WAIT_FAILED",
        "ROBOCOPY_TRANSFER_FAILED",
        "ROBOCOPY_TRANSFER_TIMED_OUT",
    }
)


def execute_next_run_target_staging_step(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetStagingOperationStore,
    staging_port: RunTargetStagingPort,
    process_instance_id: str,
) -> RunTargetStagingOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation_id=None,
            phase=None,
            validation_code="RUN_TARGET_STAGING_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind staging execution to the Engine Host process instance.",
        )

    operation = _next_staging_operation(
        permit=permit, recovery_operations=recovery_operations
    )
    if operation is None:
        return RunTargetStagingOutcome(
            idle=True,
            advanced=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            phase=None,
            validation_codes=(),
            next_action="No run-target operation is waiting for staging.",
        )
    if not _operation_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            phase=operation.phase,
            validation_code="RUN_TARGET_STAGING_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before staging this operation.",
        )

    try:
        next_phase, metadata, payload = _execute_phase(
            permit=permit,
            operation=operation,
            staging_port=staging_port,
        )
    except RunTargetEndpointWaitRequired as exc:
        return RunTargetStagingOutcome(
            idle=False,
            advanced=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=operation.operation_id,
            phase=operation.phase,
            validation_codes=(),
            next_action=exc.next_action,
            endpoint_wait_reason_code=exc.reason_code,
        )
    except RuntimeError as exc:
        validation_code = _error_code(exc)
        if validation_code in RETRYABLE_STAGING_FAILURE_CODES:
            attempt_number = operation.staging_failure_count + 1
            exhausted = attempt_number >= MAX_STAGING_ATTEMPTS
            updated = recovery_operations.record_staging_failure(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
                expected_phase=operation.phase,
                expected_failure_count=operation.staging_failure_count,
                next_phase=(
                    RecoveryOperationPhase.SKIPPED if exhausted else operation.phase
                ),
                error_code=validation_code,
                process_instance_id=process_instance_id,
                payload={"staging_phase": operation.phase.value},
            )
            if updated is None:
                return _failed(
                    permit=permit,
                    operation_id=operation.operation_id,
                    phase=operation.phase,
                    validation_code="RUN_TARGET_STAGING_FAILURE_RECORD_CONFLICT",
                    next_action="Reload recovery state before retrying staging execution.",
                )
            return RunTargetStagingOutcome(
                idle=False,
                advanced=True,
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                operation_id=operation.operation_id,
                phase=updated.phase,
                validation_codes=(),
                next_action=(
                    f"Skipped file after {attempt_number} failed staging attempts."
                    if exhausted
                    else f"Staging attempt {attempt_number} failed; retry scheduled."
                ),
            )
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            phase=operation.phase,
            validation_code=validation_code,
            next_action=_error_next_action(
                exc, "Reload staging state and retry this operation."
            ),
        )

    updated = recovery_operations.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=next_phase,
        process_instance_id=process_instance_id,
        payload=payload,
        operation_metadata=metadata,
    )
    if updated is None:
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            phase=operation.phase,
            validation_code="RUN_TARGET_STAGING_PHASE_CONFLICT",
            next_action="Reload recovery state before retrying staging execution.",
        )

    return RunTargetStagingOutcome(
        idle=False,
        advanced=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation.operation_id,
        phase=updated.phase,
        validation_codes=(),
        next_action=f"Operation advanced to {updated.phase.value}.",
    )


def _next_staging_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetStagingOperationStore,
) -> RecoveryOperation | None:
    candidates: list[RecoveryOperation] = []
    for phase in STAGING_EXECUTION_PHASES:
        operations = recovery_operations.list_operations_for_run_target_in_phase(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            phase=phase,
            limit=1,
        )
        if operations:
            candidates.append(operations[0])
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda operation: (operation.plan_sequence_no, operation.operation_id),
    )


def _execute_phase(
    *,
    permit: MutationPermit,
    operation: RecoveryOperation,
    staging_port: RunTargetStagingPort,
) -> tuple[RecoveryOperationPhase, RecoveryOperationMetadata, Mapping[str, object]]:
    if operation.phase is RecoveryOperationPhase.PLANNED:
        source_evidence = staging_port.validate_source_file(operation)
        return (
            RecoveryOperationPhase.SOURCE_VALIDATED,
            RecoveryOperationMetadata(
                expected_source_fingerprint_json=source_evidence.fingerprint_json,
                source_hash_evidence_kind=source_evidence.hash_evidence_kind,
            ),
            {
                "source_hash_evidence_kind": source_evidence.hash_evidence_kind,
                "source_fingerprint_json": source_evidence.fingerprint_json,
            },
        )

    if operation.phase is RecoveryOperationPhase.SOURCE_VALIDATED:
        stability_evidence = staging_port.bind_source_stability(operation)
        return (
            RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
            RecoveryOperationMetadata(
                source_guard_kind=stability_evidence.guard_kind,
                source_guard_evidence_hash=stability_evidence.guard_evidence_hash,
            ),
            {
                "source_guard_kind": stability_evidence.guard_kind,
                "source_guard_evidence_hash": stability_evidence.guard_evidence_hash,
            },
        )

    if operation.phase is RecoveryOperationPhase.SOURCE_STABILITY_BOUND:
        target_evidence = staging_port.validate_target_precondition(permit, operation)
        return (
            RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
            RecoveryOperationMetadata(
                expected_target_fingerprint_json=target_evidence.fingerprint_json
            ),
            {"target_precondition_fingerprint_json": target_evidence.fingerprint_json},
        )

    if operation.phase is RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED:
        allocation = staging_port.allocate_staging_object(operation)
        return (
            RecoveryOperationPhase.STAGING_ALLOCATED,
            RecoveryOperationMetadata(staging_object_id=allocation.staging_object_id),
            {"staging_object_id": allocation.staging_object_id},
        )

    if operation.phase is RecoveryOperationPhase.STAGING_ALLOCATED:
        transfer_evidence = staging_port.transfer_to_staging(operation)
        return (
            RecoveryOperationPhase.TRANSFERRED,
            RecoveryOperationMetadata(transfer_state=transfer_evidence.transfer_state),
            {"transfer_state": transfer_evidence.transfer_state},
        )

    if operation.phase is RecoveryOperationPhase.TRANSFERRED:
        durability_evidence = staging_port.ensure_staging_durable(operation)
        return (
            RecoveryOperationPhase.STAGING_DURABLE,
            RecoveryOperationMetadata(
                staging_durability_state=durability_evidence.durability_state
            ),
            {"staging_durability_state": durability_evidence.durability_state},
        )

    if operation.phase is RecoveryOperationPhase.STAGING_DURABLE:
        verification_evidence = staging_port.verify_staging_artifact(operation)
        return (
            RecoveryOperationPhase.STAGING_VERIFIED,
            RecoveryOperationMetadata(
                expected_staging_fingerprint_json=verification_evidence.fingerprint_json,
                expected_final_fingerprint_json=verification_evidence.final_fingerprint_json,
                assurance_level=verification_evidence.assurance_level,
            ),
            {
                "assurance_level": verification_evidence.assurance_level,
                "staging_fingerprint_json": verification_evidence.fingerprint_json,
            },
        )

    raise RuntimeError("RUN_TARGET_STAGING_PHASE_UNSUPPORTED")


def _operation_matches_permit(
    *, operation: RecoveryOperation, permit: MutationPermit
) -> bool:
    return (
        operation.phase in STAGING_EXECUTION_PHASES
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


def _failed(
    *,
    permit: MutationPermit,
    operation_id: str | None,
    phase: RecoveryOperationPhase | None,
    validation_code: str,
    next_action: str,
) -> RunTargetStagingOutcome:
    return RunTargetStagingOutcome(
        idle=False,
        advanced=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation_id,
        phase=phase,
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
