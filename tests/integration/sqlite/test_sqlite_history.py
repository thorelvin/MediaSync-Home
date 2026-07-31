from __future__ import annotations

import sqlite3
from pathlib import Path

from mediasync_home.adapters.sqlite.history import SqliteHistoryReadModelStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.application.history_read_models import HistoryActivityFilter


def test_history_store_unifies_controls_and_backup_runs_in_time_order(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_control(connection)
        _insert_run(connection)
        store = SqliteHistoryReadModelStore(connection)

        page = store.list_recent_history_activities(
            limit=3,
            offset=0,
            activity_filter=HistoryActivityFilter.ALL,
            job_id=None,
        )

        assert [row.activity_id for row in page] == ["run-a", "analysis-a"]
        assert page[0].completed_operations == 2
        assert page[0].completed_bytes == 1024
        assert page[0].targets[0].state == "SUCCEEDED"
        assert page[1].analysis_id == "analysis-a"
        assert page[1].targets[0].state == "WRITABLE_READY"

        controls = store.list_recent_history_activities(
            limit=3,
            offset=0,
            activity_filter=HistoryActivityFilter.CONTROLS,
            job_id="job-a",
        )
        assert [row.activity_id for row in controls] == ["analysis-a"]


def _insert_control(connection: sqlite3.Connection) -> None:
    _insert_job_detail(connection)
    connection.execute(
        """
        INSERT INTO initial_backup_plan_materializations (
            job_id,
            job_revision_id,
            analysis_id,
            state,
            reason_code,
            operation_count,
            planned_bytes,
            plan_runnable,
            next_action,
            started_utc,
            completed_utc
        )
        VALUES (
            'job-a',
            'job-rev-a',
            'analysis-a',
            'NO_CHANGES',
            'INITIAL_BACKUP_PLAN_NO_CHANGES',
            0,
            0,
            0,
            'Nothing to copy.',
            '2026-07-20T11:00:00.000Z',
            '2026-07-20T11:01:00.000Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_endpoint_bindings (
            job_id,
            job_revision_id,
            role,
            ordinal,
            endpoint_id,
            endpoint_revision_id,
            registration_state,
            registration_reason_code
        )
        VALUES (
            'job-a',
            'job-rev-a',
            'TARGET',
            1,
            'target-a',
            'target-rev-a',
            'WRITABLE_READY',
            'ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED'
        )
        """
    )


def _insert_run(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO runs (
            id,
            job_id,
            job_revision_id,
            plan_id,
            command_request_id,
            logical_run_group_id,
            trigger_type,
            state,
            summary_json,
            app_version,
            plan_checksum,
            idempotency_key,
            planned_operations,
            planned_bytes,
            started_utc,
            finished_utc
        )
        VALUES (
            'run-a',
            'job-a',
            'job-rev-a',
            'plan-a',
            'request-a',
            'run-group-a',
            'MANUAL_LOCAL_PREVIEW',
            'COMPLETED',
            '{}',
            '0B-dev',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'idempotency-a',
            2,
            1024,
            '2026-07-20T12:00:00.000Z',
            '2026-07-20T12:01:00.000Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO run_targets (
            id,
            run_id,
            endpoint_id,
            endpoint_revision_id,
            state,
            planned_operations,
            completed_operations,
            planned_bytes,
            completed_bytes
        )
        VALUES (
            'run-a-target-0000',
            'run-a',
            'target-a',
            'target-rev-a',
            'SUCCEEDED',
            2,
            2,
            1024,
            1024
        )
        """
    )


def _insert_job_detail(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO standard_backup_job_revision_details (
            job_id,
            job_revision_id,
            draft_id,
            command_request_id,
            idempotency_key,
            source_name,
            source_path_label,
            defaults_json,
            targets_json
        )
        VALUES (
            'job-a',
            'job-rev-a',
            'draft-a',
            'request-job-a',
            'idempotency-job-a',
            'Pictures',
            'C:/Users/Ada/Pictures',
            '{}',
            '[]'
        )
        """
    )
