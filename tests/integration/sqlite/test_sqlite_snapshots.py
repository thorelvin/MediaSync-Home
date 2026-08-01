from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

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
    SnapshotCoveragePageQuery,
    SnapshotBatchSummary,
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotFilterDecision,
    SnapshotFilterDecisionPageQuery,
    SnapshotEntryPageQuery,
    SnapshotIssuePageQuery,
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
                birthtime_ns=2_000,
                identity_fingerprint_hash="b" * 64,
            ),
            SnapshotFileEntry(
                entry_id="file-a",
                relative_path="Readme.txt",
                comparison_key="readme.txt",
                object_type="file",
                size_bytes=32,
                birthtime_ns=1_000,
                identity_fingerprint_hash="a" * 64,
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


def test_sqlite_snapshot_entry_read_model_pages_by_comparison_key(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(_case_collision_batch())
        store.commit_snapshot_entry_batch(_alpha_batch())

        first_page = store.page_snapshot_entries(
            SnapshotEntryPageQuery(snapshot_id="snapshot-a", limit=2)
        )
        assert [entry.relative_path for entry in first_page.entries] == [
            "Alpha.txt",
            "README.TXT",
        ]
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert first_page.entries[0].case_collision_group_id is None
        assert first_page.entries[0].birthtime_ns == 3_000
        assert first_page.entries[1].case_collision_group_id is not None
        assert first_page.entries[1].birthtime_ns == 2_000

        second_page = store.page_snapshot_entries(
            SnapshotEntryPageQuery(
                snapshot_id="snapshot-a",
                limit=2,
                after=first_page.next_cursor,
            )
        )
        assert [entry.relative_path for entry in second_page.entries] == ["Readme.txt"]
        assert second_page.has_more is False
        assert second_page.next_cursor is None


def test_sqlite_snapshot_coverage_read_model_pages_and_filters_by_state(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(_case_collision_batch())
        store.commit_snapshot_entry_batch(
            snapshot_entry_batch(
                snapshot_id="snapshot-a",
                sequence_no=1,
                entries=(),
                coverage_updates=(
                    _coverage("Photos", "photos", "COMPLETE"),
                    _coverage("Videos", "videos", "VOLATILE"),
                ),
            )
        )

        first_page = store.page_snapshot_directory_coverage(
            SnapshotCoveragePageQuery(snapshot_id="snapshot-a", limit=2)
        )

        assert [coverage.relative_path for coverage in first_page.coverage] == [".", "Photos"]
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert first_page.next_cursor.comparison_key == "photos"

        second_page = store.page_snapshot_directory_coverage(
            SnapshotCoveragePageQuery(
                snapshot_id="snapshot-a",
                limit=2,
                after=first_page.next_cursor,
            )
        )
        assert [coverage.relative_path for coverage in second_page.coverage] == ["Videos"]
        assert second_page.has_more is False
        assert second_page.next_cursor is None

        volatile_page = store.page_snapshot_directory_coverage(
            SnapshotCoveragePageQuery(
                snapshot_id="snapshot-a",
                limit=10,
                coverage_states=("VOLATILE",),
            )
        )
        assert [coverage.relative_path for coverage in volatile_page.coverage] == ["Videos"]
        assert volatile_page.coverage[0].coverage_state == "VOLATILE"


def test_sqlite_snapshot_issue_read_model_pages_and_filters_blocking_issues(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(
            _case_collision_batch(
                issues=(
                    SnapshotIssue(
                        relative_path="Archive",
                        issue_type="UNREADABLE_DIRECTORY",
                        blocks_destructive_actions=True,
                        error_code="ERROR_ACCESS_DENIED",
                        sanitized_message="access denied",
                    ),
                    SnapshotIssue(
                        relative_path="Photos",
                        issue_type="VOLATILE_DIRECTORY",
                        blocks_destructive_actions=False,
                    ),
                    SnapshotIssue(
                        relative_path="Videos",
                        issue_type="REPARSE_BLOCKED",
                        blocks_destructive_actions=True,
                        error_code="IO_REPARSE_TAG_SYMLINK",
                        sanitized_message="blocked reparse point",
                    ),
                )
            )
        )

        first_page = store.page_snapshot_issues(
            SnapshotIssuePageQuery(snapshot_id="snapshot-a", limit=2)
        )

        assert [issue.relative_path for issue in first_page.issues] == ["Archive", "Photos"]
        assert all(issue.issue_id > 0 for issue in first_page.issues)
        assert first_page.has_more is True
        assert first_page.next_cursor is not None

        second_page = store.page_snapshot_issues(
            SnapshotIssuePageQuery(
                snapshot_id="snapshot-a",
                limit=2,
                after=first_page.next_cursor,
            )
        )
        assert [issue.relative_path for issue in second_page.issues] == ["Videos"]
        assert second_page.has_more is False

        blocking_first = store.page_snapshot_issues(
            SnapshotIssuePageQuery(
                snapshot_id="snapshot-a",
                limit=1,
                blocking_only=True,
            )
        )
        assert [issue.relative_path for issue in blocking_first.issues] == ["Archive"]
        assert blocking_first.has_more is True
        assert blocking_first.next_cursor is not None

        blocking_second = store.page_snapshot_issues(
            SnapshotIssuePageQuery(
                snapshot_id="snapshot-a",
                limit=1,
                after=blocking_first.next_cursor,
                blocking_only=True,
            )
        )
        assert [issue.relative_path for issue in blocking_second.issues] == ["Videos"]
        assert blocking_second.has_more is False


def test_sqlite_snapshot_filter_decisions_are_paged_sealed_and_immutable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    decisions = (
        SnapshotFilterDecision(
            relative_path="$RECYCLE.BIN",
            object_type="directory",
            decision_state="EXCLUDED",
            reason_code="FILTER_RULE_EXCLUDED",
            matched_rule_id="default-recycle-bin",
            evaluation_stage="PRE_METADATA",
        ),
        SnapshotFilterDecision(
            relative_path="Readme.txt",
            object_type="file",
            decision_state="INCLUDED",
            reason_code="FILTER_RULE_INCLUDED",
            matched_rule_id="include-readme",
            evaluation_stage="PRE_METADATA",
        ),
    )
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        receipt = store.commit_snapshot_entry_batch(
            _case_collision_batch(filter_decisions=decisions)
        )

        assert receipt.filter_decision_count == 2
        assert store.load_snapshot_filter_decisions("snapshot-a") == decisions
        excluded = store.page_snapshot_filter_decisions(
            SnapshotFilterDecisionPageQuery(
                snapshot_id="snapshot-a",
                limit=1,
                decision_states=("EXCLUDED",),
            )
        )
        assert [item.relative_path for item in excluded.decisions] == [
            "$RECYCLE.BIN"
        ]
        assert excluded.has_more is False

        sealed = store.seal_snapshot(
            _seal_request(expected_filter_decision_count=2)
        )

        assert sealed.filter_decision_count == 2
        assert store.load_sealed_snapshot("snapshot-a") == sealed
        with pytest.raises(sqlite3.IntegrityError, match="SNAPSHOT_IMMUTABLE"):
            connection.execute(
                """
                UPDATE snapshot_filter_decisions
                SET reason_code = 'CHANGED'
                WHERE snapshot_id = 'snapshot-a'
                """
            )


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


def test_sqlite_named_stream_finding_is_persisted_without_blocking_seal(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_snapshot_parent_rows(connection)
        store = SqliteSnapshotEntryStore(connection)
        store.commit_snapshot_entry_batch(
            _case_collision_batch(
                issues=(
                    SnapshotIssue(
                        relative_path="Readme.txt",
                        issue_type="NAMED_STREAM_PRESENT",
                        blocks_destructive_actions=False,
                        error_code="SNAPSHOT_NAMED_STREAM_PRESENT",
                        sanitized_message=(
                            "The item contains Windows named streams that must "
                            "be preserved during transfer."
                        ),
                    ),
                )
            )
        )

        page = store.page_snapshot_issues(
            SnapshotIssuePageQuery(
                snapshot_id="snapshot-a",
                limit=10,
                blocking_only=False,
            )
        )

        assert len(page.issues) == 1
        assert page.issues[0].issue_type == "NAMED_STREAM_PRESENT"
        assert page.issues[0].error_code == "SNAPSHOT_NAMED_STREAM_PRESENT"
        assert page.issues[0].blocks_destructive_actions is False
        blocking_page = store.page_snapshot_issues(
            SnapshotIssuePageQuery(
                snapshot_id="snapshot-a",
                limit=10,
                blocking_only=True,
            )
        )
        assert blocking_page.issues == ()

        sealed = store.seal_snapshot(_seal_request(expected_issue_count=1))

        assert sealed.snapshot_id == "snapshot-a"
        assert store.load_sealed_snapshot("snapshot-a") == sealed


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
    insert_default_filter_set_version(
        connection,
        job_id="job-a",
        filter_set_id="filter-a",
    )
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
    filter_decisions: tuple[SnapshotFilterDecision, ...] = (),
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
                birthtime_ns=1_000,
                identity_fingerprint_hash="a" * 64,
            ),
            SnapshotFileEntry(
                entry_id="file-b",
                relative_path="README.TXT",
                comparison_key="readme.txt",
                object_type="file",
                size_bytes=64,
                birthtime_ns=2_000,
                identity_fingerprint_hash="b" * 64,
            ),
        ),
        coverage_updates=_complete_root_coverage(coverage_state),
        issues=issues,
        filter_decisions=filter_decisions,
    )


def _alpha_batch():
    return snapshot_entry_batch(
        snapshot_id="snapshot-a",
        sequence_no=1,
        entries=(
            SnapshotFileEntry(
                entry_id="file-c",
                relative_path="Alpha.txt",
                comparison_key="alpha.txt",
                object_type="file",
                size_bytes=16,
                birthtime_ns=3_000,
                identity_fingerprint_hash="c" * 64,
            ),
        ),
    )


def _seal_request(
    *,
    expected_issue_count: int = 0,
    expected_blocking_issue_count: int = 0,
    expected_filter_decision_count: int = 0,
) -> SnapshotSealRequest:
    return SnapshotSealRequest(
        snapshot_id="snapshot-a",
        expected_entry_count=2,
        expected_total_bytes=96,
        expected_batch_count=1,
        expected_directory_coverage_count=1,
        expected_issue_count=expected_issue_count,
        expected_blocking_issue_count=expected_blocking_issue_count,
        expected_case_collision_group_count=1,
        expected_filter_decision_count=expected_filter_decision_count,
    )


def _summary(batch) -> SnapshotBatchSummary:
    return SnapshotBatchSummary(
        sequence_no=batch.sequence_no,
        payload_hash=batch.payload_hash,
        entry_count=len(batch.entries),
        coverage_update_count=len(batch.coverage_updates),
        issue_count=len(batch.issues),
        approximate_bytes=batch.approximate_bytes,
        filter_decision_count=len(batch.filter_decisions),
    )


def _complete_root_coverage(coverage_state: str = "COMPLETE") -> tuple[SnapshotDirectoryCoverage, ...]:
    return (
        _coverage(".", ".", coverage_state),
    )


def _coverage(
    relative_path: str,
    comparison_key: str,
    coverage_state: str = "COMPLETE",
) -> SnapshotDirectoryCoverage:
    return SnapshotDirectoryCoverage(
        relative_path=relative_path,
        comparison_key=comparison_key,
        coverage_state=coverage_state,
        case_mode="CASE_INSENSITIVE",
        case_mode_evidence="probe",
        case_context_hash="1" * 64,
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
