from __future__ import annotations

from pathlib import Path

import pytest

from mediasync_home.adapters.quick_signature import (
    LocalQuickSignatureHasher,
    QuickSignatureRequest,
)
from mediasync_home.application.hash_cache import (
    QUICK_SIGNATURE_MIDDLE_THRESHOLD_BYTES,
    QUICK_SIGNATURE_SCHEMA_VERSION,
    QUICK_SIGNATURE_SEGMENT_BYTES,
    HashCacheEvidenceError,
    quick_signature_segments,
)


def test_quick_signature_reads_small_file_with_stable_fingerprint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    payload = b"candidate contents"
    (root / "A.bin").write_bytes(payload)

    evidence = LocalQuickSignatureHasher(chunk_bytes=3).hash_file(
        _request(root, "A.bin", len(payload))
    )

    assert evidence.signature_schema_version == QUICK_SIGNATURE_SCHEMA_VERSION
    assert evidence.segments == quick_signature_segments(len(payload))
    assert evidence.read_started_fingerprint_hash == (
        evidence.read_completed_fingerprint_hash
    )
    assert evidence.mtime_ns > 0
    assert evidence.link_count == 1


def test_quick_signature_detects_sampled_large_file_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "Large.bin"
    size = QUICK_SIGNATURE_MIDDLE_THRESHOLD_BYTES + QUICK_SIGNATURE_SEGMENT_BYTES
    with path.open("wb") as stream:
        stream.seek(size - 1)
        stream.write(b"\0")
    hasher = LocalQuickSignatureHasher()

    original = hasher.hash_file(_request(root, path.name, size))
    middle = original.segments[1]
    with path.open("r+b", buffering=0) as stream:
        stream.seek(middle.offset)
        stream.write(b"changed")
    changed = hasher.hash_file(_request(root, path.name, size))

    assert len(original.segments) == 3
    assert original.signature_hash != changed.signature_hash


def test_quick_signature_remains_a_hint_for_unsampled_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "Large.bin"
    size = QUICK_SIGNATURE_SEGMENT_BYTES * 4
    with path.open("wb") as stream:
        stream.seek(size - 1)
        stream.write(b"\0")
    hasher = LocalQuickSignatureHasher()
    original = hasher.hash_file(_request(root, path.name, size))

    with path.open("r+b", buffering=0) as stream:
        stream.seek(QUICK_SIGNATURE_SEGMENT_BYTES * 2)
        stream.write(b"not sampled")
    changed = hasher.hash_file(_request(root, path.name, size))

    assert original.signature_hash == changed.signature_hash


def test_quick_signature_rejects_snapshot_size_drift(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "A.bin").write_bytes(b"changed")

    with pytest.raises(
        HashCacheEvidenceError,
        match="QUICK_SIGNATURE_SNAPSHOT_DRIFT",
    ):
        LocalQuickSignatureHasher().hash_file(_request(root, "A.bin", 2))


def _request(root: Path, relative_path: str, size_bytes: int) -> QuickSignatureRequest:
    return QuickSignatureRequest(
        snapshot_id="snapshot-a",
        entry_id="entry-a",
        endpoint_id="source-a",
        root=root,
        relative_path=relative_path,
        expected_size_bytes=size_bytes,
        computed_utc="2026-08-02T10:00:00Z",
    )
