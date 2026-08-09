from __future__ import annotations

import sqlite3
from pathlib import Path

from mediasync_home.adapters.sqlite.backup_analysis import (
    SqliteBackupAnalysisRequestStore,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.job_lifecycle import SqliteJobLifecycleStore
from mediasync_home.application.backup_analysis import (
    BackupAnalysisRequest,
    BackupAnalysisRequestState,
)
from mediasync_home.application.job_lifecycle import ChangeJobLifecycleCommand
from tests.support.sqlite_catalog import insert_default_filter_set_version


def test_backup_analysis_request_lifecycle_and_interrupted_requeue(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection,
            catalog_critical_writer_policy(database),
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_job(connection)
        store = SqliteBackupAnalysisRequestStore(connection)
        queued = store.enqueue_backup_analysis(
            BackupAnalysisRequest(
                request_id="request-a",
                command_idempotency_key="key-a",
                job_id="job-a",
                job_revision_id="revision-a",
                state=BackupAnalysisRequestState.QUEUED,
                requested_utc="2026-07-31T10:00:00Z",
                start_when_safe=True,
            )
        )

        claimed = store.claim_next_backup_analysis(
            started_utc="2026-07-31T10:00:01Z"
        )
        assert claimed is not None
        assert claimed.state is BackupAnalysisRequestState.RUNNING
        assert store.requeue_interrupted_backup_analyses() == 1

        reclaimed = store.claim_next_backup_analysis(
            started_utc="2026-07-31T10:00:02Z"
        )
        assert reclaimed is not None
        completed = store.complete_backup_analysis(
            request_id=queued.request_id,
            state=BackupAnalysisRequestState.NO_CHANGES,
            completed_utc="2026-07-31T10:00:03Z",
            analysis_id=None,
            plan_id=None,
            reason_code="INITIAL_BACKUP_PLAN_NO_CHANGES",
            operation_count=0,
            planned_bytes=0,
            started_run_id=None,
        )

        assert completed.state is BackupAnalysisRequestState.NO_CHANGES
        assert completed.start_when_safe is True
        assert completed.row_version == 5
        assert (
            store.claim_next_backup_analysis(
                started_utc="2026-07-31T10:00:04Z"
            )
            is None
        )


def test_deleted_job_analysis_request_is_not_claimed(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection,
            catalog_critical_writer_policy(database),
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_job(connection)
        analyses = SqliteBackupAnalysisRequestStore(connection)
        deleted = SqliteJobLifecycleStore(
            connection,
            installation_id="installation-a",
        ).delete_standard_backup_job(
            command=ChangeJobLifecycleCommand(
                request_id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
                idempotency_key="bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb",
                job_id="job-a",
                expected_job_revision_id="revision-a",
                expected_lifecycle_row_version=1,
                explicit_confirmation=True,
            ),
            occurred_utc="2026-07-31T10:00:01Z",
        )

        assert deleted.applied
        connection.commit()
        analyses.enqueue_backup_analysis(
            BackupAnalysisRequest(
                request_id="request-a",
                command_idempotency_key="key-a",
                job_id="job-a",
                job_revision_id="revision-a",
                state=BackupAnalysisRequestState.QUEUED,
                requested_utc="2026-07-31T10:00:01Z",
            )
        )
        assert analyses.claim_next_backup_analysis(
            started_utc="2026-07-31T10:00:02Z"
        ) is None


def _insert_job(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')"
    )
    insert_default_filter_set_version(
        connection,
        job_id="job-a",
        filter_set_id="filter-a",
    )
    connection.execute(
        """
        INSERT INTO job_revisions (
            job_id,
            id,
            filter_set_id,
            filter_set_version
        )
        VALUES ('job-a', 'revision-a', 'filter-a', 1)
        """
    )
    connection.execute(
        """
        INSERT INTO job_heads (job_id, active_revision_id)
        VALUES ('job-a', 'revision-a')
        """
    )
    connection.commit()
