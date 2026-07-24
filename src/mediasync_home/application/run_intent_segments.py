from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.recovery_intents import (
    MAX_INTENT_SEGMENT_OPERATIONS,
    RecoveryIntentSegment,
    RecoveryIntentSegmentStore,
    durable_recovery_intent_segment,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.domain.capabilities import MutationPermit


SAFE_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class RunTargetIntentOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetIntentSegmentOutcome:
    published: bool
    run_id: str
    run_target_id: str
    segment_id: str | None
    segment: RecoveryIntentSegment | None
    operations_bound: int
    validation_codes: tuple[str, ...]
    next_action: str


def publish_run_target_recovery_intent_segment(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetIntentOperationStore,
    intent_segments: RecoveryIntentSegmentStore,
    process_instance_id: str,
    segment_sequence: int = 0,
    previous_segment_hash: str | None = None,
) -> RunTargetIntentSegmentOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_INTENT_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind intent publishing to the Engine Host process instance.",
        )
    if segment_sequence < 0:
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_INTENT_REQUIRES_NON_NEGATIVE_SEQUENCE",
            next_action="Retry with the next non-negative recovery intent segment sequence.",
        )

    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.STAGING_VERIFIED,
        limit=MAX_INTENT_SEGMENT_OPERATIONS,
    )
    if not operations:
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_NO_STAGING_VERIFIED_OPERATIONS",
            next_action="Stage and verify at least one operation before publishing commit intent.",
        )

    ordered_operations = tuple(sorted(operations, key=lambda operation: operation.operation_id))
    mismatch = next(
        (
            operation
            for operation in ordered_operations
            if not _operation_matches_permit(operation=operation, permit=permit)
        ),
        None,
    )
    if mismatch is not None:
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_INTENT_OPERATION_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before publishing commit intent.",
        )

    segment = build_run_target_recovery_intent_segment(
        permit=permit,
        operations=ordered_operations,
        segment_sequence=segment_sequence,
        previous_segment_hash=previous_segment_hash,
    )
    try:
        published = intent_segments.publish_intent_segment(segment)
    except ValueError as exc:
        return _failed(
            permit=permit,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reload recovery intent state before retrying segment publication.",
            ),
        )

    for ordinal, operation in enumerate(ordered_operations):
        try:
            updated = recovery_operations.record_operation_phase_transition(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
                expected_phase=RecoveryOperationPhase.STAGING_VERIFIED,
                next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
                process_instance_id=process_instance_id,
                payload={
                    "intent_ordinal": ordinal,
                    "intent_segment_id": published.segment_id,
                    "segment_hash": published.segment_hash,
                },
                intent_segment_id=published.segment_id,
                intent_ordinal=ordinal,
            )
        except ValueError as exc:
            return RunTargetIntentSegmentOutcome(
                published=False,
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                segment_id=published.segment_id,
                segment=published,
                operations_bound=ordinal,
                validation_codes=(_error_code(exc),),
                next_action=_error_next_action(
                    exc,
                    "Reload recovery operation state before retrying commit intent binding.",
                ),
            )
        if updated is None:
            return RunTargetIntentSegmentOutcome(
                published=False,
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                segment_id=published.segment_id,
                segment=published,
                operations_bound=ordinal,
                validation_codes=("RUN_TARGET_INTENT_OPERATION_PHASE_CONFLICT",),
                next_action="Reload recovery operation state before retrying commit intent binding.",
            )

    return RunTargetIntentSegmentOutcome(
        published=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        segment_id=published.segment_id,
        segment=published,
        operations_bound=len(ordered_operations),
        validation_codes=(),
        next_action="Durable commit intent is recorded for staged run-target operations.",
    )


def build_run_target_recovery_intent_segment(
    *,
    permit: MutationPermit,
    operations: tuple[RecoveryOperation, ...],
    segment_sequence: int,
    previous_segment_hash: str | None,
) -> RecoveryIntentSegment:
    return durable_recovery_intent_segment(
        segment_id=_segment_id(permit=permit, segment_sequence=segment_sequence),
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        target_endpoint_id=permit.endpoint_id,
        target_endpoint_revision_id=permit.endpoint_revision_id,
        endpoint_generation=operations[0].endpoint_generation,
        owner_installation_id=permit.owner_installation_id,
        ownership_epoch=permit.ownership_epoch,
        lease_id=permit.lease_id,
        fencing_token=permit.fencing_token,
        segment_sequence=segment_sequence,
        relative_path=_segment_relative_path(permit=permit, segment_sequence=segment_sequence),
        schema_version=1,
        operation_count=len(operations),
        byte_count=sum(_operation_byte_count(operation) for operation in operations),
        segment_hash=_segment_hash(permit=permit, operations=operations),
        previous_segment_hash=previous_segment_hash,
    )


def _operation_matches_permit(*, operation: RecoveryOperation, permit: MutationPermit) -> bool:
    return (
        operation.phase is RecoveryOperationPhase.STAGING_VERIFIED
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


def _segment_id(*, permit: MutationPermit, segment_sequence: int) -> str:
    return f"{permit.run_target_id}-intent-{segment_sequence:06d}"


def _segment_relative_path(*, permit: MutationPermit, segment_sequence: int) -> str:
    owner = _safe_path_component(permit.owner_installation_id)
    run = _safe_path_component(permit.run_id)
    return f"installations/{owner}/recovery/{run}/segment-{segment_sequence:06d}.intent.jsonl"


def _segment_hash(*, permit: MutationPermit, operations: tuple[RecoveryOperation, ...]) -> str:
    payload = {
        "fencing_token": permit.fencing_token,
        "lease_id": permit.lease_id,
        "operations": [
            {
                "final_relative_path": operation.final_relative_path,
                "operation_id": operation.operation_id,
                "target_precondition_kind": operation.target_precondition_kind.value,
            }
            for operation in operations
        ],
        "run_id": permit.run_id,
        "run_target_id": permit.run_target_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_byte_count(operation: RecoveryOperation) -> int:
    if operation.expected_staging_fingerprint_json is None:
        return 0
    try:
        payload = json.loads(operation.expected_staging_fingerprint_json)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    byte_count = payload.get("byte_count")
    if not isinstance(byte_count, int) or byte_count < 0:
        return 0
    return byte_count


def _safe_path_component(value: str) -> str:
    if SAFE_PATH_COMPONENT_PATTERN.fullmatch(value) is not None:
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _failed(
    *,
    permit: MutationPermit,
    validation_code: str,
    next_action: str,
) -> RunTargetIntentSegmentOutcome:
    return RunTargetIntentSegmentOutcome(
        published=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        segment_id=None,
        segment=None,
        operations_bound=0,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _error_code(exc: ValueError) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc)
    if message.strip():
        return message
    return type(exc).__name__


def _error_next_action(exc: ValueError, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback
