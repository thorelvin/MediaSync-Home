from __future__ import annotations

from pathlib import Path

import pytest
from blake3 import blake3

from mediasync_home.adapters.current_read_hash import (
    CurrentReadHashRequest,
    LocalCurrentReadHasher,
)
from mediasync_home.application.hash_evidence import (
    CURRENT_READ_HASH_ALGORITHM,
    CurrentReadHashEvidenceError,
    HashEvidenceKind,
)


def test_local_current_read_hasher_reads_complete_stable_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    payload = b"current backup contents"
    (root / "A.bin").write_bytes(payload)

    evidence = LocalCurrentReadHasher(chunk_bytes=3).hash_file(
        CurrentReadHashRequest(
            snapshot_id="snapshot-a",
            entry_id="entry-a",
            endpoint_id="source-a",
            root=root,
            relative_path="A.bin",
            expected_size_bytes=len(payload),
            computed_utc="2026-07-31T10:00:00Z",
        )
    )

    assert evidence.content_hash == blake3(payload).hexdigest()
    assert evidence.algorithm == CURRENT_READ_HASH_ALGORITHM
    assert evidence.evidence_kind is HashEvidenceKind.CURRENT_READ_HASH
    assert (
        evidence.read_started_fingerprint_hash
        == evidence.read_completed_fingerprint_hash
    )


def test_local_current_read_hasher_rejects_snapshot_size_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "A.bin").write_bytes(b"changed")

    with pytest.raises(
        CurrentReadHashEvidenceError,
        match="CURRENT_READ_HASH_SNAPSHOT_DRIFT",
    ):
        LocalCurrentReadHasher().hash_file(
            CurrentReadHashRequest(
                snapshot_id="snapshot-a",
                entry_id="entry-a",
                endpoint_id="source-a",
                root=root,
                relative_path="A.bin",
                expected_size_bytes=2,
                computed_utc="2026-07-31T10:00:00Z",
            )
        )
