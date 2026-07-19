from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_CHECKSUM_ALGORITHM = "SHA-256"
SNAPSHOT_SERIALIZER_VERSION = "0B-SNAPSHOT-CANONICAL-JSON-V1"


class SnapshotMaterializationError(ValueError):
    pass


@dataclass(frozen=True)
class SnapshotFileEntry:
    entry_id: str
    relative_path: str
    comparison_key: str
    object_type: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class SnapshotEntryBatch:
    snapshot_id: str
    sequence_no: int
    payload_hash: str
    entries: tuple[SnapshotFileEntry, ...]
    approximate_bytes: int


@dataclass(frozen=True)
class SnapshotBatchCommitReceipt:
    snapshot_id: str
    sequence_no: int
    payload_hash: str
    entry_count: int
    approximate_bytes: int
    idempotent_replay: bool


@dataclass(frozen=True)
class SnapshotBatchSummary:
    sequence_no: int
    payload_hash: str
    entry_count: int
    approximate_bytes: int


@dataclass(frozen=True)
class SnapshotSealRequest:
    snapshot_id: str
    expected_entry_count: int
    expected_total_bytes: int
    expected_batch_count: int
    expected_case_collision_group_count: int = 0


@dataclass(frozen=True)
class SealedSnapshot:
    snapshot_id: str
    snapshot_schema_version: int
    checksum_algorithm: str
    serializer_version: str
    snapshot_checksum: str
    entry_count: int
    total_bytes: int
    batch_count: int
    case_collision_group_count: int
    complete: bool = True
    immutable: bool = True


class SnapshotEntryMaterializationStore(Protocol):
    def commit_snapshot_entry_batch(self, batch: SnapshotEntryBatch) -> SnapshotBatchCommitReceipt: ...

    def load_snapshot_entries(self, snapshot_id: str) -> tuple[SnapshotFileEntry, ...]: ...


class SnapshotSealStore(Protocol):
    def seal_snapshot(self, request: SnapshotSealRequest) -> SealedSnapshot: ...

    def load_sealed_snapshot(self, snapshot_id: str) -> SealedSnapshot | None: ...


def snapshot_entry_batch(
    *,
    snapshot_id: str,
    sequence_no: int,
    entries: tuple[SnapshotFileEntry, ...],
    approximate_bytes: int | None = None,
    payload_hash: str | None = None,
) -> SnapshotEntryBatch:
    measured_bytes = sum(entry.size_bytes or 0 for entry in entries)
    batch = SnapshotEntryBatch(
        snapshot_id=snapshot_id,
        sequence_no=sequence_no,
        payload_hash=payload_hash
        or _payload_hash(
            snapshot_id=snapshot_id,
            sequence_no=sequence_no,
            entries=entries,
            approximate_bytes=measured_bytes if approximate_bytes is None else approximate_bytes,
        ),
        entries=entries,
        approximate_bytes=measured_bytes if approximate_bytes is None else approximate_bytes,
    )
    validate_snapshot_entry_batch(batch)
    return batch


def snapshot_seal(
    *,
    snapshot_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    batches: tuple[SnapshotBatchSummary, ...],
    case_collision_group_count: int,
) -> SealedSnapshot:
    ordered_entries = tuple(sorted(entries, key=lambda entry: (entry.relative_path, entry.entry_id)))
    ordered_batches = tuple(sorted(batches, key=lambda batch: batch.sequence_no))
    _validate_snapshot_seal_inputs(
        snapshot_id=snapshot_id,
        entries=ordered_entries,
        batches=ordered_batches,
        case_collision_group_count=case_collision_group_count,
    )
    entry_count = len(ordered_entries)
    total_bytes = sum(entry.size_bytes or 0 for entry in ordered_entries)
    checksum = _snapshot_checksum(
        snapshot_id=snapshot_id,
        entries=ordered_entries,
        batches=ordered_batches,
        entry_count=entry_count,
        total_bytes=total_bytes,
        case_collision_group_count=case_collision_group_count,
    )
    sealed = SealedSnapshot(
        snapshot_id=snapshot_id,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        checksum_algorithm=SNAPSHOT_CHECKSUM_ALGORITHM,
        serializer_version=SNAPSHOT_SERIALIZER_VERSION,
        snapshot_checksum=checksum,
        entry_count=entry_count,
        total_bytes=total_bytes,
        batch_count=len(ordered_batches),
        case_collision_group_count=case_collision_group_count,
    )
    validate_sealed_snapshot(sealed)
    return sealed


def verify_snapshot_checksum(
    snapshot: SealedSnapshot,
    *,
    entries: tuple[SnapshotFileEntry, ...],
    batches: tuple[SnapshotBatchSummary, ...],
) -> bool:
    if not snapshot.complete or not snapshot.immutable:
        return False
    try:
        expected = snapshot_seal(
            snapshot_id=snapshot.snapshot_id,
            entries=entries,
            batches=batches,
            case_collision_group_count=snapshot.case_collision_group_count,
        )
    except SnapshotMaterializationError:
        return False
    return (
        snapshot.snapshot_schema_version == expected.snapshot_schema_version
        and snapshot.checksum_algorithm == expected.checksum_algorithm
        and snapshot.serializer_version == expected.serializer_version
        and snapshot.snapshot_checksum == expected.snapshot_checksum
        and snapshot.entry_count == expected.entry_count
        and snapshot.total_bytes == expected.total_bytes
        and snapshot.batch_count == expected.batch_count
    )


def validate_snapshot_seal_request(request: SnapshotSealRequest) -> None:
    if not request.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SNAPSHOT_ID")
    if request.expected_entry_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ENTRY_COUNT_MUST_BE_NON_NEGATIVE")
    if request.expected_total_bytes < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BYTES_MUST_BE_NON_NEGATIVE")
    if request.expected_batch_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_COUNT_MUST_BE_NON_NEGATIVE")
    if request.expected_case_collision_group_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MUST_BE_NON_NEGATIVE")


def validate_sealed_snapshot(snapshot: SealedSnapshot) -> None:
    if not snapshot.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SNAPSHOT_ID")
    if snapshot.snapshot_schema_version < 1:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SCHEMA_VERSION")
    if snapshot.checksum_algorithm != SNAPSHOT_CHECKSUM_ALGORITHM:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_CHECKSUM_ALGORITHM")
    if snapshot.serializer_version != SNAPSHOT_SERIALIZER_VERSION:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SERIALIZER_VERSION")
    if HASH_PATTERN.fullmatch(snapshot.snapshot_checksum) is None:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_CHECKSUM")
    if snapshot.entry_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ENTRY_COUNT_MUST_BE_NON_NEGATIVE")
    if snapshot.total_bytes < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BYTES_MUST_BE_NON_NEGATIVE")
    if snapshot.batch_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_COUNT_MUST_BE_NON_NEGATIVE")
    if snapshot.case_collision_group_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MUST_BE_NON_NEGATIVE")
    if not snapshot.complete or not snapshot.immutable:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_MUST_BE_COMPLETE_AND_IMMUTABLE")


def validate_snapshot_entry_batch(batch: SnapshotEntryBatch) -> None:
    if not batch.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_REQUIRES_SNAPSHOT_ID")
    if batch.sequence_no < 0:
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_SEQUENCE_MUST_BE_NON_NEGATIVE")
    if HASH_PATTERN.fullmatch(batch.payload_hash) is None:
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_REQUIRES_PAYLOAD_HASH")
    if batch.approximate_bytes < 0:
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_BYTES_MUST_BE_NON_NEGATIVE")

    entry_ids: set[str] = set()
    relative_paths: set[str] = set()
    for entry in batch.entries:
        if not entry.entry_id.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_ID")
        if entry.entry_id in entry_ids:
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_IDS_MUST_BE_UNIQUE_IN_BATCH")
        entry_ids.add(entry.entry_id)
        if not _valid_relative_path(entry.relative_path):
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_RELATIVE_PATH")
        if entry.relative_path in relative_paths:
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_PATHS_MUST_BE_UNIQUE_IN_BATCH")
        relative_paths.add(entry.relative_path)
        if not entry.comparison_key.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_COMPARISON_KEY")
        if not entry.object_type.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_OBJECT_TYPE")
        if entry.size_bytes is not None and entry.size_bytes < 0:
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_SIZE_MUST_BE_NON_NEGATIVE")


def _payload_hash(
    *,
    snapshot_id: str,
    sequence_no: int,
    entries: tuple[SnapshotFileEntry, ...],
    approximate_bytes: int,
) -> str:
    payload = {
        "approximate_bytes": approximate_bytes,
        "entries": [
            {
                "comparison_key": entry.comparison_key,
                "entry_id": entry.entry_id,
                "object_type": entry.object_type,
                "relative_path": entry.relative_path,
                "size_bytes": entry.size_bytes,
            }
            for entry in sorted(entries, key=lambda item: (item.entry_id, item.relative_path))
        ],
        "sequence_no": sequence_no,
        "snapshot_id": snapshot_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _snapshot_checksum(
    *,
    snapshot_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    batches: tuple[SnapshotBatchSummary, ...],
    entry_count: int,
    total_bytes: int,
    case_collision_group_count: int,
) -> str:
    payload = {
        "batch_count": len(batches),
        "batches": [
            {
                "approximate_bytes": batch.approximate_bytes,
                "entry_count": batch.entry_count,
                "payload_hash": batch.payload_hash,
                "sequence_no": batch.sequence_no,
            }
            for batch in batches
        ],
        "case_collision_group_count": case_collision_group_count,
        "checksum_algorithm": SNAPSHOT_CHECKSUM_ALGORITHM,
        "complete": True,
        "entries": [
            {
                "comparison_key": entry.comparison_key,
                "entry_id": entry.entry_id,
                "object_type": entry.object_type,
                "relative_path": entry.relative_path,
                "size_bytes": entry.size_bytes,
            }
            for entry in entries
        ],
        "entry_count": entry_count,
        "immutable": True,
        "serializer_version": SNAPSHOT_SERIALIZER_VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "total_bytes": total_bytes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_snapshot_seal_inputs(
    *,
    snapshot_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    batches: tuple[SnapshotBatchSummary, ...],
    case_collision_group_count: int,
) -> None:
    if not snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SNAPSHOT_ID")
    if case_collision_group_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MUST_BE_NON_NEGATIVE")
    validate_snapshot_entry_batch(
        SnapshotEntryBatch(
            snapshot_id=snapshot_id,
            sequence_no=0,
            payload_hash="0" * 64,
            entries=entries,
            approximate_bytes=sum(entry.size_bytes or 0 for entry in entries),
        )
    )
    _validate_snapshot_batch_summaries(batches)
    if sum(batch.entry_count for batch in batches) != len(entries):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ENTRY_COUNT_MISMATCH")


def _validate_snapshot_batch_summaries(batches: tuple[SnapshotBatchSummary, ...]) -> None:
    ordered_sequences = [batch.sequence_no for batch in batches]
    if ordered_sequences != list(range(len(batches))):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_CONTIGUOUS_BATCHES")
    for batch in batches:
        if HASH_PATTERN.fullmatch(batch.payload_hash) is None:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_BATCH_PAYLOAD_HASH")
        if batch.entry_count < 0:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_ENTRY_COUNT_MUST_BE_NON_NEGATIVE")
        if batch.approximate_bytes < 0:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_BYTES_MUST_BE_NON_NEGATIVE")


def _valid_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or normalized.startswith("//")
        or WINDOWS_DRIVE_PATTERN.match(normalized)
    ):
        return False
    parts = tuple(normalized.split("/"))
    return all(part not in {"", ".", ".."} and ":" not in part for part in parts)
