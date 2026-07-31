from __future__ import annotations

from pathlib import Path

from mediasync_home.adapters.file_identity import stable_file_identity_hash
from mediasync_home.application.source_preconditions import SourceFilePrecondition


def source_precondition_json(
    *,
    relative_path: str = "Pictures/A.jpg",
    size_bytes: int = 128,
    snapshot_id: str = "source-snapshot-a",
    snapshot_entry_id: str = "source-entry-a",
    identity_fingerprint_hash: str = "a" * 64,
) -> str:
    return SourceFilePrecondition(
        snapshot_id=snapshot_id,
        snapshot_entry_id=snapshot_entry_id,
        relative_path=relative_path,
        size_bytes=size_bytes,
        identity_fingerprint_hash=identity_fingerprint_hash,
    ).to_json()


def source_precondition_for_file(
    path: Path,
    *,
    relative_path: str,
    snapshot_id: str = "source-snapshot-a",
    snapshot_entry_id: str = "source-entry-a",
) -> str:
    stat_result = path.stat()
    return source_precondition_json(
        relative_path=relative_path,
        size_bytes=int(stat_result.st_size),
        snapshot_id=snapshot_id,
        snapshot_entry_id=snapshot_entry_id,
        identity_fingerprint_hash=stable_file_identity_hash(stat_result),
    )
