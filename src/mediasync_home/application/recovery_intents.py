from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


MAX_INTENT_SEGMENT_OPERATIONS = 10_000
MAX_INTENT_SEGMENT_BYTES = 16 * 1024 * 1024
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class RecoveryIntentSegmentViolation(ValueError):
    pass


class RecoveryIntentSegmentDurabilityState(str, Enum):
    PENDING = "PENDING"
    DURABLE = "DURABLE"


class RecoveryIntentSegmentState(str, Enum):
    BUILDING = "BUILDING"
    DURABLE = "DURABLE"
    RECONCILED = "RECONCILED"
    CLEANUP_ELIGIBLE = "CLEANUP_ELIGIBLE"
    CLEANED = "CLEANED"


@dataclass(frozen=True)
class RecoveryIntentSegment:
    segment_id: str
    run_id: str
    run_target_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    endpoint_generation: int
    owner_installation_id: str
    ownership_epoch: int
    lease_id: str
    fencing_token: int
    segment_sequence: int
    relative_path: str
    schema_version: int
    operation_count: int
    byte_count: int
    segment_hash: str
    previous_segment_hash: str | None = None
    durability_state: RecoveryIntentSegmentDurabilityState = RecoveryIntentSegmentDurabilityState.DURABLE
    state: RecoveryIntentSegmentState = RecoveryIntentSegmentState.DURABLE


class RecoveryIntentSegmentStore(Protocol):
    def publish_intent_segment(self, segment: RecoveryIntentSegment) -> RecoveryIntentSegment: ...

    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None: ...


def durable_recovery_intent_segment(
    *,
    segment_id: str,
    run_id: str,
    run_target_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    owner_installation_id: str,
    ownership_epoch: int,
    lease_id: str,
    fencing_token: int,
    segment_sequence: int,
    relative_path: str,
    schema_version: int,
    operation_count: int,
    byte_count: int,
    segment_hash: str,
    previous_segment_hash: str | None = None,
) -> RecoveryIntentSegment:
    segment = RecoveryIntentSegment(
        segment_id=segment_id,
        run_id=run_id,
        run_target_id=run_target_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        endpoint_generation=endpoint_generation,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        lease_id=lease_id,
        fencing_token=fencing_token,
        segment_sequence=segment_sequence,
        relative_path=relative_path,
        schema_version=schema_version,
        operation_count=operation_count,
        byte_count=byte_count,
        segment_hash=segment_hash,
        previous_segment_hash=previous_segment_hash,
    )
    validate_recovery_intent_segment(segment)
    return segment


def validate_recovery_intent_segment(segment: RecoveryIntentSegment) -> None:
    if not _non_empty(
        segment.segment_id,
        segment.run_id,
        segment.run_target_id,
        segment.target_endpoint_id,
        segment.target_endpoint_revision_id,
        segment.owner_installation_id,
        segment.lease_id,
    ):
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_IDENTIFIERS")
    if not _valid_relative_path(segment.relative_path):
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_RELATIVE_PATH")
    if (
        segment.endpoint_generation < 1
        or segment.ownership_epoch < 1
        or segment.fencing_token < 1
        or segment.segment_sequence < 0
        or segment.schema_version < 1
    ):
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_POSITIVE_NUMBERS")
    if not 1 <= segment.operation_count <= MAX_INTENT_SEGMENT_OPERATIONS:
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_OPERATION_COUNT_OUT_OF_RANGE")
    if not 0 <= segment.byte_count <= MAX_INTENT_SEGMENT_BYTES:
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_BYTE_COUNT_OUT_OF_RANGE")
    if HASH_PATTERN.fullmatch(segment.segment_hash) is None:
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_HASH")
    if (
        segment.previous_segment_hash is not None
        and HASH_PATTERN.fullmatch(segment.previous_segment_hash) is None
    ):
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_PREVIOUS_HASH")
    if segment.state is not RecoveryIntentSegmentState.DURABLE:
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_DURABLE_STATE")
    if segment.durability_state is not RecoveryIntentSegmentDurabilityState.DURABLE:
        raise RecoveryIntentSegmentViolation("RECOVERY_INTENT_SEGMENT_REQUIRES_DURABLE_STORAGE")


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
