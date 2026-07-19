from __future__ import annotations

import pytest

from mediasync_home.application.snapshots import (
    SnapshotBatchSummary,
    SnapshotFileEntry,
    SnapshotMaterializationError,
    snapshot_entry_batch,
    snapshot_seal,
    verify_snapshot_checksum,
)


def test_snapshot_entry_batch_hash_is_deterministic_and_allows_case_collisions() -> None:
    batch = snapshot_entry_batch(
        snapshot_id="snapshot-a",
        sequence_no=0,
        entries=(
            SnapshotFileEntry(
                entry_id="file-b",
                relative_path="README.TXT",
                comparison_key="readme.txt",
                object_type="file",
                size_bytes=64,
            ),
            SnapshotFileEntry(
                entry_id="file-a",
                relative_path="Readme.txt",
                comparison_key="readme.txt",
                object_type="file",
                size_bytes=32,
            ),
        ),
    )
    replay = snapshot_entry_batch(
        snapshot_id="snapshot-a",
        sequence_no=0,
        entries=tuple(reversed(batch.entries)),
    )

    assert batch.payload_hash == replay.payload_hash
    assert len(batch.payload_hash) == 64
    assert batch.approximate_bytes == 96


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute/file.txt",
        "\\absolute\\file.txt",
        "C:\\absolute\\file.txt",
        "Photos/../secret.txt",
        "Photos//image.jpg",
    ],
)
def test_snapshot_entry_batch_rejects_unsafe_relative_paths(relative_path: str) -> None:
    with pytest.raises(SnapshotMaterializationError, match="SNAPSHOT_ENTRY_REQUIRES_RELATIVE_PATH"):
        snapshot_entry_batch(
            snapshot_id="snapshot-a",
            sequence_no=0,
            entries=(
                SnapshotFileEntry(
                    entry_id="file-a",
                    relative_path=relative_path,
                    comparison_key="image.jpg",
                    object_type="file",
                ),
            ),
        )


def test_snapshot_entry_batch_rejects_duplicate_exact_paths() -> None:
    with pytest.raises(
        SnapshotMaterializationError,
        match="SNAPSHOT_ENTRY_PATHS_MUST_BE_UNIQUE_IN_BATCH",
    ):
        snapshot_entry_batch(
            snapshot_id="snapshot-a",
            sequence_no=0,
            entries=(
                SnapshotFileEntry(
                    entry_id="file-a",
                    relative_path="Readme.txt",
                    comparison_key="readme.txt",
                    object_type="file",
                ),
                SnapshotFileEntry(
                    entry_id="file-b",
                    relative_path="Readme.txt",
                    comparison_key="readme.txt",
                    object_type="file",
                ),
            ),
        )


def test_snapshot_seal_checksum_is_deterministic_and_verifiable() -> None:
    batch = _case_collision_batch()
    summary = SnapshotBatchSummary(
        sequence_no=batch.sequence_no,
        payload_hash=batch.payload_hash,
        entry_count=len(batch.entries),
        approximate_bytes=batch.approximate_bytes,
    )

    sealed = snapshot_seal(
        snapshot_id=batch.snapshot_id,
        entries=batch.entries,
        batches=(summary,),
        case_collision_group_count=1,
    )
    replay = snapshot_seal(
        snapshot_id=batch.snapshot_id,
        entries=tuple(reversed(batch.entries)),
        batches=(summary,),
        case_collision_group_count=1,
    )

    assert sealed.snapshot_checksum == replay.snapshot_checksum
    assert len(sealed.snapshot_checksum) == 64
    assert sealed.entry_count == 2
    assert sealed.total_bytes == 96
    assert sealed.batch_count == 1
    assert verify_snapshot_checksum(sealed, entries=batch.entries, batches=(summary,)) is True
    assert (
        verify_snapshot_checksum(
            sealed,
            entries=(
                SnapshotFileEntry(
                    entry_id="file-a",
                    relative_path="Readme.txt",
                    comparison_key="readme.txt",
                    object_type="file",
                    size_bytes=31,
                ),
                batch.entries[1],
            ),
            batches=(summary,),
        )
        is False
    )


def test_snapshot_seal_rejects_non_contiguous_batch_sequence() -> None:
    batch = _case_collision_batch()

    with pytest.raises(
        SnapshotMaterializationError,
        match="SNAPSHOT_SEAL_REQUIRES_CONTIGUOUS_BATCHES",
    ):
        snapshot_seal(
            snapshot_id=batch.snapshot_id,
            entries=batch.entries,
            batches=(
                SnapshotBatchSummary(
                    sequence_no=1,
                    payload_hash=batch.payload_hash,
                    entry_count=len(batch.entries),
                    approximate_bytes=batch.approximate_bytes,
                ),
            ),
            case_collision_group_count=1,
        )


def _case_collision_batch():
    return snapshot_entry_batch(
        snapshot_id="snapshot-a",
        sequence_no=0,
        entries=(
            SnapshotFileEntry(
                entry_id="file-a",
                relative_path="Readme.txt",
                comparison_key="readme.txt",
                object_type="file",
                size_bytes=32,
            ),
            SnapshotFileEntry(
                entry_id="file-b",
                relative_path="README.TXT",
                comparison_key="readme.txt",
                object_type="file",
                size_bytes=64,
            ),
        ),
    )
