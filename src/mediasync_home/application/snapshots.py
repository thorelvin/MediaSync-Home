from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


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


class SnapshotEntryMaterializationStore(Protocol):
    def commit_snapshot_entry_batch(self, batch: SnapshotEntryBatch) -> SnapshotBatchCommitReceipt: ...

    def load_snapshot_entries(self, snapshot_id: str) -> tuple[SnapshotFileEntry, ...]: ...


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
