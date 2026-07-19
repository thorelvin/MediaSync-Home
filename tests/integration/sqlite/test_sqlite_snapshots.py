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
from mediasync_home.application.snapshots import SnapshotFileEntry, snapshot_entry_batch


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
        assert _row_count(connection, "snapshot_batches") == 1
        assert _row_count(connection, "file_entries") == 2
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
                sealed_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = 'snapshot-a'
            """
        )
        store = SqliteSnapshotEntryStore(connection)

        with pytest.raises(SqliteSnapshotEntryStoreError, match="SNAPSHOT_IMMUTABLE"):
            store.commit_snapshot_entry_batch(_case_collision_batch())

        assert _row_count(connection, "snapshot_batches") == 0
        assert _row_count(connection, "file_entries") == 0


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
