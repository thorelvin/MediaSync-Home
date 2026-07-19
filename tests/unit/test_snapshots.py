from __future__ import annotations

import pytest

from mediasync_home.application.snapshots import (
    SnapshotFileEntry,
    SnapshotMaterializationError,
    snapshot_entry_batch,
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
