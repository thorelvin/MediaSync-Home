from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol
from uuid import UUID

from mediasync_home.application.recovery_intents import (
    MAX_INTENT_SEGMENT_BYTES,
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
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TARGET_RECOVERY_INTENT_SCHEMA_VERSION = 1


class RunTargetIntentOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


class TargetRecoveryIntentSegmentPublisher(Protocol):
    def publish_target_intent_segment(
        self,
        *,
        segment: RecoveryIntentSegment,
        operations: tuple[RecoveryOperation, ...],
        plan_checksum: str,
    ) -> None: ...


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
    target_intent_segments: TargetRecoveryIntentSegmentPublisher | None = None,
    plan_checksum: str | None = None,
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

    ordered_operations = tuple(
        sorted(operations, key=lambda operation: operation.operation_id)
    )
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

    bounded_operations = _bounded_intent_operations(ordered_operations)
    if not bounded_operations:
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_INTENT_OPERATION_RECORD_TOO_LARGE",
            next_action="Reduce recovery operation metadata before retrying intent publication.",
        )
    if target_intent_segments is not None and (
        plan_checksum is None or HASH_PATTERN.fullmatch(plan_checksum) is None
    ):
        return _failed(
            permit=permit,
            validation_code="RUN_TARGET_INTENT_REQUIRES_PLAN_CHECKSUM",
            next_action="Reload the sealed plan before publishing target recovery evidence.",
        )

    segment = build_run_target_recovery_intent_segment(
        permit=permit,
        operations=bounded_operations,
        segment_sequence=segment_sequence,
        previous_segment_hash=previous_segment_hash,
        plan_checksum=plan_checksum,
    )
    try:
        if target_intent_segments is not None:
            target_intent_segments.publish_target_intent_segment(
                segment=segment,
                operations=bounded_operations,
                plan_checksum=plan_checksum or "",
            )
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

    for ordinal, operation in enumerate(bounded_operations):
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
        operations_bound=len(bounded_operations),
        validation_codes=(),
        next_action="Durable commit intent is recorded for staged run-target operations.",
    )


def build_run_target_recovery_intent_segment(
    *,
    permit: MutationPermit,
    operations: tuple[RecoveryOperation, ...],
    segment_sequence: int,
    previous_segment_hash: str | None,
    plan_checksum: str | None = None,
) -> RecoveryIntentSegment:
    operation_payloads = tuple(
        canonical_recovery_intent_operation_payload(
            operation=operation, ordinal=ordinal
        )
        for ordinal, operation in enumerate(operations)
    )
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
        relative_path=_segment_relative_path(
            permit=permit, segment_sequence=segment_sequence
        ),
        schema_version=TARGET_RECOVERY_INTENT_SCHEMA_VERSION,
        operation_count=len(operations),
        byte_count=sum(
            _canonical_json_line_size(payload) for payload in operation_payloads
        ),
        segment_hash=recovery_intent_segment_hash(
            permit=permit,
            operation_payloads=operation_payloads,
            plan_checksum=plan_checksum,
        ),
        previous_segment_hash=previous_segment_hash,
    )


def _operation_matches_permit(
    *, operation: RecoveryOperation, permit: MutationPermit
) -> bool:
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
    return recovery_intent_segment_relative_path(
        owner_installation_id=permit.owner_installation_id,
        run_id=permit.run_id,
        segment_sequence=segment_sequence,
    )


def recovery_intent_segment_relative_path(
    *,
    owner_installation_id: str,
    run_id: str,
    segment_sequence: int,
) -> str:
    owner = recovery_intent_installation_namespace(owner_installation_id)
    run = _safe_path_component(run_id)
    return f"installations/{owner}/recovery/{run}/segment-{segment_sequence:06d}.intent.jsonl"


def recovery_intent_installation_namespace(owner_installation_id: str) -> str:
    try:
        return UUID(owner_installation_id).hex[:12]
    except ValueError:
        return _safe_path_component(owner_installation_id)


def canonical_recovery_intent_operation_payload(
    *,
    operation: RecoveryOperation,
    ordinal: int,
) -> dict[str, object]:
    return {
        "assurance_level": operation.assurance_level,
        "expected_final_fingerprint_json": operation.expected_final_fingerprint_json,
        "expected_source_fingerprint_json": operation.expected_source_fingerprint_json,
        "expected_source_parent_identity_json": (
            operation.expected_source_parent_identity_json
        ),
        "expected_staging_fingerprint_json": operation.expected_staging_fingerprint_json,
        "expected_target_fingerprint_json": operation.expected_target_fingerprint_json,
        "expected_target_parent_identity_json": (
            operation.expected_target_parent_identity_json
        ),
        "expected_target_path_chain_hash": operation.expected_target_path_chain_hash,
        "final_relative_path": operation.final_relative_path,
        "operation_id": operation.operation_id,
        "operation_kind": operation.operation_kind.value,
        "ordinal": ordinal,
        "plan_sequence_no": operation.plan_sequence_no,
        "planned_bytes": operation.planned_bytes,
        "record_type": "OPERATION",
        "source_case_context_hash": operation.source_case_context_hash,
        "source_endpoint_id": operation.source_endpoint_id,
        "source_endpoint_revision_id": operation.source_endpoint_revision_id,
        "source_guard_evidence_hash": operation.source_guard_evidence_hash,
        "source_guard_kind": operation.source_guard_kind,
        "source_hash_evidence_kind": operation.source_hash_evidence_kind,
        "source_path_chain_hash": operation.source_path_chain_hash,
        "source_precondition_json": operation.source_precondition_json,
        "source_relative_path": operation.source_relative_path,
        "staging_durability_state": operation.staging_durability_state,
        "staging_object_id": operation.staging_object_id,
        "target_precondition_kind": operation.target_precondition_kind.value,
    }


def recovery_intent_segment_hash(
    *,
    permit: MutationPermit,
    operation_payloads: tuple[Mapping[str, object], ...],
    plan_checksum: str | None,
) -> str:
    return recovery_intent_segment_hash_for_binding(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        endpoint_id=permit.endpoint_id,
        endpoint_revision_id=permit.endpoint_revision_id,
        owner_installation_id=permit.owner_installation_id,
        ownership_epoch=permit.ownership_epoch,
        lease_id=permit.lease_id,
        fencing_token=permit.fencing_token,
        operation_payloads=operation_payloads,
        plan_checksum=plan_checksum,
    )


def recovery_intent_segment_hash_for_binding(
    *,
    run_id: str,
    run_target_id: str,
    endpoint_id: str,
    endpoint_revision_id: str,
    owner_installation_id: str,
    ownership_epoch: int,
    lease_id: str,
    fencing_token: int,
    operation_payloads: tuple[Mapping[str, object], ...],
    plan_checksum: str | None,
) -> str:
    payload = {
        "endpoint_id": endpoint_id,
        "endpoint_revision_id": endpoint_revision_id,
        "fencing_token": fencing_token,
        "lease_id": lease_id,
        "operations": list(operation_payloads),
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
        "plan_checksum": plan_checksum,
        "run_id": run_id,
        "run_target_id": run_target_id,
        "schema_version": TARGET_RECOVERY_INTENT_SCHEMA_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_intent_operations(
    operations: tuple[RecoveryOperation, ...],
) -> tuple[RecoveryOperation, ...]:
    bounded: list[RecoveryOperation] = []
    byte_count = 0
    for operation in operations:
        payload = canonical_recovery_intent_operation_payload(
            operation=operation,
            ordinal=len(bounded),
        )
        operation_bytes = _canonical_json_line_size(payload)
        if byte_count + operation_bytes > MAX_INTENT_SEGMENT_BYTES:
            break
        bounded.append(operation)
        byte_count += operation_bytes
    return tuple(bounded)


def _canonical_json_line_size(payload: Mapping[str, object]) -> int:
    return (
        len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        + 1
    )


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
