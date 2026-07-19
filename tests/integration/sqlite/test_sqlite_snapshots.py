from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.snapshots import (
    SqliteSnapshotEntryStore,
    SqliteSnapshotEntryStoreError,
)
from mediasync_home.application.snapshots import (
    SNAPSHOT_CHECKSUM_ALGORITHM,
    SNAPSHOT_SERIALIZER_VERSION,
    SnapshotBatchSummary,
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotIssue,
    SnapshotSealRequest,
    snapshot_entry_batch,
    verify_snapshot_checksum,
)


def test_sqlite_snapshot_entry_batch_is_idempotent_and_preserves_case_collisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        batch = _case_collision_batch()

        receipt = store.commit_snapshot_entry_batch(batch)
        replay = store.commit_snapshot_entry_batch(batch)

        assert receipt.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert receipt.coverage_update_count == 1
        assert replay.coverage_update_count == 1
        assert receipt.issue_count == 0
        assert store.load_snapshot_entries("snapshot-a") == (
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
        )
        assert store.load_directory_coverage("snapshot-a") == _complete_root_coverage()
        assert store.load_snapshot_issues("snapshot-a") == ()
        assert _row_count(connection, "snapshot_batches") == 1
        assert _row_count(connection, "file_entries") == 2
        assert _row_count(connection, "directory_coverage") == 1
        assert _row_count(connection, "snapshot_issues") == 0
        assert _row_count(connection, "case_collision_groups") == 1
        assert _row_count(connection, "case_collision_members") == 2
        assert _snapshot_counts(connection) == (2, 96)


def test_sqlite_snapshot_entry_batch_rejects_sequence_hash_conflict(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(_case_collision_batch())

        with pytest.raises(SqliteSnapshotEntryStoreError, match="SNAPSHOT_BATCH_CONFLICT"):
            store.commit_snapshot_entry_batch(
                snapshot_entry_batch(
                    snapshot_id="snapshot-a",
                    sequence_no=0,
                    entries=(
                        SnapshotFileEntry(
                            entry_id="file-c",
                            relative_path="Other.txt",
                            comparison_key="other.txt",
                            object_type="file",
                            size_bytes=1,
                        ),
                    ),
                )
            )

        assert _row_count(connection, "file_entries") == 2


def test_sqlite_snapshot_entry_batch_rejects_immutable_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        connection.execute(
            """
            UPDATE snapshots
            SET immutable = 1,
                complete = 1,
                sealed_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                checksum_algorithm = 'SHA-256',
                serializer_version = 'test',
                snapshot_checksum = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            WHERE id = 'snapshot-a'
            """
        )
        store = SqliteSnapshotEntryStore(connection)

        with pytest.raises(SqliteSnapshotEntryStoreError, match="SNAPSHOT_IMMUTABLE"):
            store.commit_snapshot_entry_batch(_case_collision_batch())

        assert _row_count(connection, "snapshot_batches") == 0
        assert _row_count(connection, "file_entries") == 0


def test_sqlite_snapshot_seal_is_idempotent_and_blocks_later_mutation(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        batch = _case_collision_batch()
        store.commit_snapshot_entry_batch(batch)

        sealed = store.seal_snapshot(_seal_request())
        replay = store.seal_snapshot(_seal_request())
        loaded = store.load_sealed_snapshot("snapshot-a")

        assert replay == sealed
        assert loaded == sealed
        assert sealed.complete is True
        assert sealed.immutable is True
        assert sealed.checksum_algorithm == SNAPSHOT_CHECKSUM_ALGORITHM
        assert sealed.serializer_version == SNAPSHOT_SERIALIZER_VERSION
        assert verify_snapshot_checksum(
            sealed,
            entries=store.load_snapshot_entries("snapshot-a"),
            coverage=store.load_directory_coverage("snapshot-a"),
            issues=store.load_snapshot_issues("snapshot-a"),
            batches=(_summary(batch),),
        )
        assert _snapshot_seal_row(connection) == (
            1,
            1,
            0,
            0,
            SNAPSHOT_CHECKSUM_ALGORITHM,
            SNAPSHOT_SERIALIZER_VERSION,
            sealed.snapshot_checksum,
        )

        with pytest.raises(SqliteSnapshotEntryStoreError, match="SNAPSHOT_IMMUTABLE"):
            store.commit_snapshot_entry_batch(
                snapshot_entry_batch(
                    snapshot_id="snapshot-a",
                    sequence_no=1,
                    entries=(
                        SnapshotFileEntry(
                            entry_id="file-c",
                            relative_path="Other.txt",
                            comparison_key="other.txt",
                            object_type="file",
                            size_bytes=1,
                        ),
                    ),
                )
            )
        with pytest.raises(sqlite3.IntegrityError, match="SNAPSHOT_IMMUTABLE"):
            connection.execute(
                """
                UPDATE file_entries
                SET size_bytes = 65
                WHERE snapshot_id = 'snapshot-a' AND id = 'file-b'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="SNAPSHOT_IMMUTABLE"):
            connection.execute(
                """
                INSERT INTO directory_coverage (
                    snapshot_id,
                    relative_path,
                    comparison_key,
                    coverage_state,
                    case_mode,
                    case_mode_evidence,
                    case_context_hash
                )
                VALUES ('snapshot-a', 'Later', 'later', 'COMPLETE', 'CASE_INSENSITIVE', 'probe', ?)
                """,
                ("2" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="SNAPSHOT_IMMUTABLE"):
            connection.execute(
                """
                INSERT INTO snapshot_issues (
                    snapshot_id,
                    relative_path,
                    issue_type,
                    blocks_destructive_actions
                )
                VALUES ('snapshot-a', 'Later', 'LATE_ISSUE', 0)
                """
            )


def test_sqlite_snapshot_seal_rejects_count_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(_case_collision_batch())

        with pytest.raises(
            SqliteSnapshotEntryStoreError,
            match="SNAPSHOT_SEAL_ENTRY_COUNT_MISMATCH",
        ):
            store.seal_snapshot(
                SnapshotSealRequest(
                    snapshot_id="snapshot-a",
                    expected_entry_count=3,
                    expected_total_bytes=96,
                    expected_batch_count=1,
                    expected_directory_coverage_count=1,
                    expected_case_collision_group_count=1,
                )
            )

        assert store.load_sealed_snapshot("snapshot-a") is None
        assert _snapshot_seal_row(connection) == (0, 0, 0, 0, None, None, None)


def test_sqlite_snapshot_seal_rejects_incomplete_coverage(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(_case_collision_batch(coverage_state="VOLATILE"))

        with pytest.raises(
            SqliteSnapshotEntryStoreError,
            match="SNAPSHOT_SEAL_COVERAGE_INCOMPLETE",
        ):
            store.seal_snapshot(_seal_request())

        assert store.load_sealed_snapshot("snapshot-a") is None
        assert _snapshot_seal_row(connection) == (0, 0, 0, 0, None, None, None)


def test_sqlite_snapshot_seal_rejects_blocking_issue(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(
            _case_collision_batch(
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
        )

        with pytest.raises(
            SqliteSnapshotEntryStoreError,
            match="SNAPSHOT_SEAL_BLOCKING_COUNT_MISMATCH",
        ):
            store.seal_snapshot(_seal_request(expected_issue_count=1))

        assert store.load_sealed_snapshot("snapshot-a") is None
        assert _snapshot_seal_row(connection) == (0, 0, 0, 0, None, None, None)


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_snapshot_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES ('endpoint-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('endpoint-a', 'endpoint-rev-a', 'USB', 'file:///E:/Backup')
        """
    )
    connection.execute(
        """
        INSERT INTO endpoint_heads (endpoint_id, active_revision_id)
            VALUES ('endpoint-a', 'endpoint-rev-a')
        """
    )
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
        """
    )
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'endpoint-a', 'endpoint-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('snapshot-a', 'analysis-a', 'endpoint-a', 'endpoint-rev-a')
        """
    )


def _case_collision_batch(
    *,
    coverage_state: str = "COMPLETE",
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
        coverage_updates=_complete_root_coverage(coverage_state),
        issues=issues,
    )


def _seal_request(*, expected_issue_count: int = 0) -> SnapshotSealRequest:
    return SnapshotSealRequest(
        snapshot_id="snapshot-a",
        expected_entry_count=2,
        expected_total_bytes=96,
        expected_batch_count=1,
        expected_directory_coverage_count=1,
        expected_issue_count=expected_issue_count,
        expected_case_collision_group_count=1,
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


def _complete_root_coverage(coverage_state: str = "COMPLETE") -> tuple[SnapshotDirectoryCoverage, ...]:
    return (
        SnapshotDirectoryCoverage(
            relative_path=".",
            comparison_key=".",
            coverage_state=coverage_state,
            case_mode="CASE_INSENSITIVE",
            case_mode_evidence="probe",
            case_context_hash="1" * 64,
        ),
    )


def _snapshot_seal_row(
    connection: sqlite3.Connection,
) -> tuple[int, int, int, int, str | None, str | None, str | None]:
    row = connection.execute(
        """
        SELECT
            complete,
            immutable,
            scan_error_count,
            volatile_directory_count,
            checksum_algorithm,
            serializer_version,
            snapshot_checksum
        FROM snapshots
        WHERE id = 'snapshot-a'
        """
    ).fetchone()
    assert row is not None
    return (
        int(row[0]),
        int(row[1]),
        int(row[2]),
        int(row[3]),
        None if row[4] is None else str(row[4]),
        None if row[5] is None else str(row[5]),
        None if row[6] is None else str(row[6]),
    )


def _snapshot_counts(connection: sqlite3.Connection) -> tuple[int, int]:
    row = connection.execute(
        """
        SELECT entry_count, total_bytes
        FROM snapshots
        WHERE id = 'snapshot-a'
        """
    ).fetchone()
    assert row is not None
    return (int(row[0]), int(row[1]))


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
