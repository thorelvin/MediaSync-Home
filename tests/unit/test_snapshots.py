from __future__ import annotations

import pytest

from mediasync_home.application.snapshots import (
    SnapshotBatchSummary,
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotIssue,
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
        coverage_update_count=len(batch.coverage_updates),
        issue_count=len(batch.issues),
        approximate_bytes=batch.approximate_bytes,
    )

    sealed = snapshot_seal(
        snapshot_id=batch.snapshot_id,
        entries=batch.entries,
        coverage=batch.coverage_updates,
        issues=batch.issues,
        batches=(summary,),
        case_collision_group_count=1,
    )
    replay = snapshot_seal(
        snapshot_id=batch.snapshot_id,
        entries=tuple(reversed(batch.entries)),
        coverage=batch.coverage_updates,
        issues=batch.issues,
        batches=(summary,),
        case_collision_group_count=1,
    )

    assert sealed.snapshot_checksum == replay.snapshot_checksum
    assert len(sealed.snapshot_checksum) == 64
    assert sealed.entry_count == 2
    assert sealed.total_bytes == 96
    assert sealed.batch_count == 1
    assert sealed.directory_coverage_count == 1
    assert sealed.issue_count == 0
    assert verify_snapshot_checksum(
        sealed,
        entries=batch.entries,
        coverage=batch.coverage_updates,
        issues=batch.issues,
        batches=(summary,),
    ) is True
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
            coverage=batch.coverage_updates,
            issues=batch.issues,
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
            coverage=batch.coverage_updates,
            issues=batch.issues,
            batches=(
                SnapshotBatchSummary(
                    sequence_no=1,
                    payload_hash=batch.payload_hash,
                    entry_count=len(batch.entries),
                    coverage_update_count=len(batch.coverage_updates),
                    issue_count=len(batch.issues),
                    approximate_bytes=batch.approximate_bytes,
                ),
            ),
            case_collision_group_count=1,
        )


def test_snapshot_seal_rejects_incomplete_directory_coverage() -> None:
    batch = _case_collision_batch(
        coverage=(SnapshotDirectoryCoverage(
            relative_path=".",
            comparison_key=".",
            coverage_state="VOLATILE",
            case_mode="CASE_INSENSITIVE",
            case_mode_evidence="probe",
            case_context_hash="1" * 64,
        ),)
    )

    with pytest.raises(
        SnapshotMaterializationError,
        match="SNAPSHOT_SEAL_COVERAGE_INCOMPLETE",
    ):
        snapshot_seal(
            snapshot_id=batch.snapshot_id,
            entries=batch.entries,
            coverage=batch.coverage_updates,
            issues=batch.issues,
            batches=(_summary(batch),),
            case_collision_group_count=1,
        )


def test_snapshot_seal_rejects_blocking_issue() -> None:
    batch = _case_collision_batch(
        issues=(
            SnapshotIssue(
                relative_path="Photos",
                issue_type="UNREADABLE_DIRECTORY",
                blocks_destructive_actions=True,
                error_code="ERROR_ACCESS_DENIED",
                sanitized_message="access denied",
            ),
        )
    )

    with pytest.raises(
        SnapshotMaterializationError,
        match="SNAPSHOT_SEAL_BLOCKING_ISSUES",
    ):
        snapshot_seal(
            snapshot_id=batch.snapshot_id,
            entries=batch.entries,
            coverage=batch.coverage_updates,
            issues=batch.issues,
            batches=(_summary(batch),),
            case_collision_group_count=1,
        )


def _case_collision_batch(
    *,
    coverage: tuple[SnapshotDirectoryCoverage, ...] | None = None,
    issues: tuple[SnapshotIssue, ...] = (),
):
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
        coverage_updates=_complete_root_coverage() if coverage is None else coverage,
        issues=issues,
    )


def _summary(batch) -> SnapshotBatchSummary:
    return SnapshotBatchSummary(
        sequence_no=batch.sequence_no,
        payload_hash=batch.payload_hash,
        entry_count=len(batch.entries),
        coverage_update_count=len(batch.coverage_updates),
        issue_count=len(batch.issues),
        approximate_bytes=batch.approximate_bytes,
    )


def _complete_root_coverage() -> tuple[SnapshotDirectoryCoverage, ...]:
    return (
        SnapshotDirectoryCoverage(
            relative_path=".",
            comparison_key=".",
            coverage_state="COMPLETE",
            case_mode="CASE_INSENSITIVE",
            case_mode_evidence="probe",
            case_context_hash="1" * 64,
        ),
    )
