from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum


HASH_CACHE_ALGORITHM = "BLAKE3-256"
HASH_CACHE_HASH_SCHEMA_VERSION = 1
QUICK_SIGNATURE_SCHEMA_VERSION = 1
QUICK_SIGNATURE_SEGMENT_BYTES = 1024 * 1024
QUICK_SIGNATURE_MIDDLE_THRESHOLD_BYTES = 64 * 1024 * 1024
QUICK_SIGNATURE_MAX_SEGMENTS = 3
HASH_CACHE_MAX_ACTIVE_ROWS = 1_000_000
HASH_REQUEST_MAX_CLAIM_BATCH = 64

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class HashCacheEvidenceKind(str, Enum):
    CURRENT_READ_HASH = "CURRENT_READ_HASH"
    USN_CONTINUITY_VALIDATED_HASH = "USN_CONTINUITY_VALIDATED_HASH"
    METADATA_REVALIDATED_CACHED_HASH = "METADATA_REVALIDATED_CACHED_HASH"
    STALE_HASH_HINT = "STALE_HASH_HINT"
    QUICK_SIGNATURE_ONLY = "QUICK_SIGNATURE_ONLY"


class HashCacheEvidenceError(ValueError):
    pass


class HashCacheWriteState(str, Enum):
    INSERTED = "INSERTED"
    PROMOTED = "PROMOTED"
    REPLAYED = "REPLAYED"
    RETAINED_STRONGER = "RETAINED_STRONGER"
    CAPACITY_REJECTED = "CAPACITY_REJECTED"


@dataclass(frozen=True, slots=True)
class QuickSignatureSegment:
    offset: int
    length: int

    def __post_init__(self) -> None:
        if self.offset < 0 or self.length < 0:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SEGMENT_INVALID")


@dataclass(frozen=True, slots=True)
class QuickSignatureEvidence:
    snapshot_id: str
    entry_id: str
    endpoint_id: str
    signature_hash: str
    size_bytes: int
    signature_schema_version: int
    segments: tuple[QuickSignatureSegment, ...]
    read_started_fingerprint_hash: str
    read_completed_fingerprint_hash: str
    volume_identity: str | None
    file_id: str | None
    mtime_ns: int
    birthtime_ns: int | None
    attributes: int | None
    reparse_tag: int | None
    link_count: int | None
    computed_utc: str

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip() or not self.entry_id.strip():
            raise HashCacheEvidenceError("QUICK_SIGNATURE_IDENTITY_INVALID")
        if not self.endpoint_id.strip() or not self.computed_utc.strip():
            raise HashCacheEvidenceError("QUICK_SIGNATURE_IDENTITY_INVALID")
        if self.size_bytes < 0:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SIZE_INVALID")
        if self.signature_schema_version != QUICK_SIGNATURE_SCHEMA_VERSION:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SCHEMA_INVALID")
        if _DIGEST_PATTERN.fullmatch(self.signature_hash) is None:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_HASH_INVALID")
        if not 1 <= len(self.segments) <= QUICK_SIGNATURE_MAX_SEGMENTS:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SEGMENTS_INVALID")
        if any(
            segment.offset + segment.length > self.size_bytes
            for segment in self.segments
        ):
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SEGMENTS_INVALID")
        if tuple(sorted(self.segments, key=lambda item: item.offset)) != self.segments:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SEGMENTS_INVALID")
        if any(
            left.offset + left.length > right.offset
            for left, right in zip(self.segments, self.segments[1:])
        ):
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SEGMENTS_INVALID")
        if (
            _DIGEST_PATTERN.fullmatch(self.read_started_fingerprint_hash) is None
            or self.read_started_fingerprint_hash
            != self.read_completed_fingerprint_hash
        ):
            raise HashCacheEvidenceError("QUICK_SIGNATURE_FINGERPRINT_INVALID")
        if self.mtime_ns < 0:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_METADATA_INVALID")
        if self.birthtime_ns is not None and self.birthtime_ns < 0:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_METADATA_INVALID")
        if self.link_count is not None and self.link_count < 1:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_METADATA_INVALID")


@dataclass(frozen=True, slots=True)
class HashCacheIdentity:
    endpoint_id: str
    endpoint_generation: int
    volume_identity: str | None
    relative_path: str
    comparison_key: str
    comparison_key_version: int
    parent_case_context_hash: str
    entry_type: str
    size_bytes: int
    mtime_ns: int
    birthtime_ns: int | None
    attributes: int | None
    reparse_tag: int | None
    file_id: str | None
    file_id_reliability: str
    link_count: int | None

    def __post_init__(self) -> None:
        if not self.endpoint_id.strip() or not self.relative_path.strip():
            raise HashCacheEvidenceError("HASH_CACHE_IDENTITY_INVALID")
        if not self.comparison_key.strip() or self.endpoint_generation < 1:
            raise HashCacheEvidenceError("HASH_CACHE_IDENTITY_INVALID")
        if self.comparison_key_version < 1 or self.entry_type != "file":
            raise HashCacheEvidenceError("HASH_CACHE_IDENTITY_INVALID")
        if _DIGEST_PATTERN.fullmatch(self.parent_case_context_hash) is None:
            raise HashCacheEvidenceError("HASH_CACHE_CASE_CONTEXT_INVALID")
        if self.size_bytes < 0 or self.mtime_ns < 0:
            raise HashCacheEvidenceError("HASH_CACHE_METADATA_INVALID")
        if self.birthtime_ns is not None and self.birthtime_ns < 0:
            raise HashCacheEvidenceError("HASH_CACHE_METADATA_INVALID")
        if self.file_id_reliability not in {"stable", "hint", "unavailable"}:
            raise HashCacheEvidenceError("HASH_CACHE_FILE_ID_RELIABILITY_INVALID")
        if self.link_count is not None and self.link_count < 1:
            raise HashCacheEvidenceError("HASH_CACHE_LINK_COUNT_INVALID")

    @property
    def identity_hash(self) -> str:
        return _canonical_digest(
            {
                "attributes": self.attributes,
                "birthtime_ns": self.birthtime_ns,
                "comparison_key": self.comparison_key,
                "comparison_key_version": self.comparison_key_version,
                "endpoint_generation": self.endpoint_generation,
                "endpoint_id": self.endpoint_id,
                "entry_type": self.entry_type,
                "file_id": self.file_id,
                "file_id_reliability": self.file_id_reliability,
                "link_count": self.link_count,
                "mtime_ns": self.mtime_ns,
                "parent_case_context_hash": self.parent_case_context_hash,
                "relative_path": self.relative_path,
                "reparse_tag": self.reparse_tag,
                "size_bytes": self.size_bytes,
                "volume_identity": self.volume_identity,
            }
        )


@dataclass(frozen=True, slots=True)
class HashCacheRecord:
    identity: HashCacheIdentity
    evidence_kind: HashCacheEvidenceKind
    evidence_generation: int
    computed_utc: str
    quick_hash: str | None = None
    full_hash: str | None = None
    algorithm: str = HASH_CACHE_ALGORITHM
    hash_schema_version: int = HASH_CACHE_HASH_SCHEMA_VERSION
    signature_schema_version: int | None = None
    read_started_fingerprint_hash: str | None = None
    read_completed_fingerprint_hash: str | None = None
    usn_journal_id: str | None = None
    usn_first_record: str | None = None
    usn_last_record: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_generation < 1 or not self.computed_utc.strip():
            raise HashCacheEvidenceError("HASH_CACHE_EVIDENCE_IDENTITY_INVALID")
        if self.algorithm != HASH_CACHE_ALGORITHM:
            raise HashCacheEvidenceError("HASH_CACHE_ALGORITHM_INVALID")
        if self.hash_schema_version != HASH_CACHE_HASH_SCHEMA_VERSION:
            raise HashCacheEvidenceError("HASH_CACHE_HASH_SCHEMA_INVALID")
        if self.quick_hash is not None and _DIGEST_PATTERN.fullmatch(self.quick_hash) is None:
            raise HashCacheEvidenceError("HASH_CACHE_QUICK_HASH_INVALID")
        if self.full_hash is not None and _DIGEST_PATTERN.fullmatch(self.full_hash) is None:
            raise HashCacheEvidenceError("HASH_CACHE_FULL_HASH_INVALID")
        if self.evidence_kind is HashCacheEvidenceKind.QUICK_SIGNATURE_ONLY:
            if (
                self.quick_hash is None
                or self.full_hash is not None
                or self.signature_schema_version != QUICK_SIGNATURE_SCHEMA_VERSION
                or not _matching_fingerprints(self)
            ):
                raise HashCacheEvidenceError("HASH_CACHE_QUICK_EVIDENCE_INVALID")
        elif self.full_hash is None:
            raise HashCacheEvidenceError("HASH_CACHE_FULL_EVIDENCE_INVALID")
        if self.evidence_kind is not HashCacheEvidenceKind.QUICK_SIGNATURE_ONLY:
            if self.signature_schema_version not in {None, QUICK_SIGNATURE_SCHEMA_VERSION}:
                raise HashCacheEvidenceError("HASH_CACHE_SIGNATURE_SCHEMA_INVALID")
        if self.evidence_kind is HashCacheEvidenceKind.CURRENT_READ_HASH:
            if not _matching_fingerprints(self):
                raise HashCacheEvidenceError("HASH_CACHE_CURRENT_READ_INVALID")
        if self.evidence_kind is HashCacheEvidenceKind.USN_CONTINUITY_VALIDATED_HASH:
            if not all(
                value is not None and value.strip()
                for value in (
                    self.usn_journal_id,
                    self.usn_first_record,
                    self.usn_last_record,
                )
            ):
                raise HashCacheEvidenceError("HASH_CACHE_USN_EVIDENCE_INVALID")

    @property
    def can_drive_skip_identical(self) -> bool:
        return self.evidence_kind in {
            HashCacheEvidenceKind.CURRENT_READ_HASH,
            HashCacheEvidenceKind.USN_CONTINUITY_VALIDATED_HASH,
        }

    @property
    def evidence_hash(self) -> str:
        return _canonical_digest(
            {
                "algorithm": self.algorithm,
                "cache_identity_hash": self.identity.identity_hash,
                "evidence_kind": self.evidence_kind.value,
                "full_hash": self.full_hash,
                "hash_schema_version": self.hash_schema_version,
                "quick_hash": self.quick_hash,
                "read_completed_fingerprint_hash": (
                    self.read_completed_fingerprint_hash
                ),
                "read_started_fingerprint_hash": self.read_started_fingerprint_hash,
                "signature_schema_version": self.signature_schema_version,
                "usn_first_record": self.usn_first_record,
                "usn_journal_id": self.usn_journal_id,
                "usn_last_record": self.usn_last_record,
            }
        )


@dataclass(frozen=True, slots=True)
class HashCacheWriteReport:
    state: HashCacheWriteState
    cache_identity_hash: str
    evidence_hash: str
    record_id: int | None
    evidence_generation: int | None
    active_evidence_kind: HashCacheEvidenceKind | None


def compatible_strong_cache_evidence(
    left: HashCacheRecord | None,
    right: HashCacheRecord | None,
    *,
    expected_size_bytes: int,
) -> bool:
    if left is None or right is None:
        return False
    return (
        expected_size_bytes >= 0
        and left.can_drive_skip_identical
        and right.can_drive_skip_identical
        and left.identity.size_bytes == right.identity.size_bytes == expected_size_bytes
        and left.algorithm == right.algorithm == HASH_CACHE_ALGORITHM
        and left.hash_schema_version
        == right.hash_schema_version
        == HASH_CACHE_HASH_SCHEMA_VERSION
        and left.full_hash is not None
        and left.full_hash == right.full_hash
    )


def quick_signature_segments(size_bytes: int) -> tuple[QuickSignatureSegment, ...]:
    if size_bytes < 0:
        raise HashCacheEvidenceError("QUICK_SIGNATURE_SIZE_INVALID")
    if size_bytes <= QUICK_SIGNATURE_SEGMENT_BYTES:
        return (QuickSignatureSegment(offset=0, length=size_bytes),)
    if size_bytes <= QUICK_SIGNATURE_SEGMENT_BYTES * 2:
        return (
            QuickSignatureSegment(offset=0, length=QUICK_SIGNATURE_SEGMENT_BYTES),
            QuickSignatureSegment(
                offset=QUICK_SIGNATURE_SEGMENT_BYTES,
                length=size_bytes - QUICK_SIGNATURE_SEGMENT_BYTES,
            ),
        )
    segments = [
        QuickSignatureSegment(offset=0, length=QUICK_SIGNATURE_SEGMENT_BYTES),
    ]
    if size_bytes >= QUICK_SIGNATURE_MIDDLE_THRESHOLD_BYTES:
        segments.append(
            QuickSignatureSegment(
                offset=(size_bytes - QUICK_SIGNATURE_SEGMENT_BYTES) // 2,
                length=QUICK_SIGNATURE_SEGMENT_BYTES,
            )
        )
    segments.append(
        QuickSignatureSegment(
            offset=size_bytes - QUICK_SIGNATURE_SEGMENT_BYTES,
            length=QUICK_SIGNATURE_SEGMENT_BYTES,
        )
    )
    return tuple(segments)


def _matching_fingerprints(record: HashCacheRecord) -> bool:
    return (
        record.read_started_fingerprint_hash is not None
        and _DIGEST_PATTERN.fullmatch(record.read_started_fingerprint_hash) is not None
        and record.read_started_fingerprint_hash
        == record.read_completed_fingerprint_hash
    )


def _canonical_digest(payload: dict[str, object]) -> str:
    material = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
