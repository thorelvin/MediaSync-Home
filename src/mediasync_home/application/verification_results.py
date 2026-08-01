from __future__ import annotations

from enum import Enum


class TransferState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    TRANSFERRED = "TRANSFERRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AssuranceLevel(str, Enum):
    NONE = "NONE"
    MANIFEST_VERIFIED = "MANIFEST_VERIFIED"
    METADATA_VERIFIED = "METADATA_VERIFIED"
    PRIMARY_STREAM_HASH_VERIFIED = "PRIMARY_STREAM_HASH_VERIFIED"
    NAMED_STREAMS_VERIFIED = "NAMED_STREAMS_VERIFIED"
    FULL_OBJECT_VERIFIED = "FULL_OBJECT_VERIFIED"


class DurabilityState(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    LOCAL_FILE_FLUSH_CONFIRMED = "LOCAL_FILE_FLUSH_CONFIRMED"
    WRITE_THROUGH_REQUEST_CONFIRMED = "WRITE_THROUGH_REQUEST_CONFIRMED"
    REMOTE_ACK_ONLY = "REMOTE_ACK_ONLY"
    UNKNOWN = "UNKNOWN"


def canonical_transfer_state(
    raw_state: str | None,
    *,
    fallback: TransferState,
) -> TransferState:
    normalized = _normalized(raw_state)
    if normalized in TransferState._value2member_map_:
        return TransferState(normalized)
    if normalized is not None and "NOT_TRANSFERRED" in normalized:
        return TransferState.FAILED
    if normalized is not None and "TRANSFERRED" in normalized:
        return TransferState.TRANSFERRED
    return fallback


def canonical_assurance_level(raw_level: str | None) -> AssuranceLevel:
    normalized = _normalized(raw_level)
    if normalized in AssuranceLevel._value2member_map_:
        return AssuranceLevel(normalized)
    if normalized in {
        "FULL_HASH",
        "STAGING_HASH_MATCHES_POST_TRANSFER_SOURCE_HASH",
    }:
        return AssuranceLevel.PRIMARY_STREAM_HASH_VERIFIED
    if normalized == "STAGING_DIRECTORY_MARKER_VERIFIED":
        return AssuranceLevel.MANIFEST_VERIFIED
    return AssuranceLevel.NONE


def canonical_durability_state(
    raw_state: str | None,
    *,
    fallback: DurabilityState,
) -> DurabilityState:
    normalized = _normalized(raw_state)
    if normalized in DurabilityState._value2member_map_:
        return DurabilityState(normalized)
    if normalized in {
        "LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED",
        "LOCAL_DIRECTORY_MARKER_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED",
    }:
        return DurabilityState.WRITE_THROUGH_REQUEST_CONFIRMED
    if normalized in {
        "LOCAL_FILE_FLUSH_CONFIRMED",
        "LOCAL_DIRECTORY_MARKER_FLUSH_CONFIRMED_ENTRY_UNCONFIRMED",
        "FILE_FSYNC_COMPLETED",
        "DIRECTORY_MARKER_FILE_FSYNC_COMPLETED",
    }:
        return DurabilityState.LOCAL_FILE_FLUSH_CONFIRMED
    if normalized is not None and normalized.startswith("REMOTE_ACK"):
        return DurabilityState.REMOTE_ACK_ONLY
    if normalized is not None:
        return DurabilityState.UNKNOWN
    return fallback


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None
