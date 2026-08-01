from __future__ import annotations

import sqlite3
from pathlib import Path
from time import perf_counter

from mediasync_home.adapters.sqlite.history import SqliteHistoryReadModelStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.application.history_read_models import (
    HistoryActivityFilter,
    query_history_timeline,
)


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
            after=None,
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
            after=None,
            offset=0,
            activity_filter=HistoryActivityFilter.CONTROLS,
            job_id="job-a",
        )
        assert [row.activity_id for row in controls] == ["analysis-a"]


def test_history_keyset_uses_total_order_for_timestamp_ties(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_control(connection)
        _insert_control_activity(
            connection,
            analysis_id="analysis-z",
            started_utc="2026-07-20T12:00:00.000Z",
        )
        _insert_run(connection)
        _insert_run(
            connection,
            run_id="run-z",
            started_utc="2026-07-20T12:00:00.000Z",
            include_target=False,
        )
        store = SqliteHistoryReadModelStore(connection)

        first = query_history_timeline(history_store=store, limit=2)
        assert [row.activity_id for row in first.activities] == [
            "analysis-z",
            "run-z",
        ]
        assert first.next_cursor is not None

        second = query_history_timeline(
            history_store=store,
            limit=2,
            after=first.next_cursor.to_dict(),
        )

        assert [row.activity_id for row in second.activities] == [
            "run-a",
            "analysis-a",
        ]
        assert second.has_more is False


def test_history_keyset_does_not_drift_when_newer_run_is_inserted(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_job_detail(connection)
        for run_id, started_utc in (
            ("run-d", "2026-07-20T14:00:00.000Z"),
            ("run-c", "2026-07-20T13:00:00.000Z"),
            ("run-b", "2026-07-20T12:00:00.000Z"),
            ("run-a", "2026-07-20T11:00:00.000Z"),
        ):
            _insert_run(
                connection,
                run_id=run_id,
                started_utc=started_utc,
                include_target=False,
            )
        store = SqliteHistoryReadModelStore(connection)

        first = query_history_timeline(
            history_store=store,
            limit=2,
            activity_filter="BACKUPS",
        )
        assert [row.activity_id for row in first.activities] == ["run-d", "run-c"]
        assert first.next_cursor is not None

        _insert_run(
            connection,
            run_id="run-new",
            started_utc="2026-07-20T15:00:00.000Z",
            include_target=False,
        )
        second = query_history_timeline(
            history_store=store,
            limit=2,
            activity_filter="BACKUPS",
            after=first.next_cursor.to_dict(),
        )

        assert [row.activity_id for row in second.activities] == ["run-b", "run-a"]


def test_history_keyset_seeks_index_in_100k_run_catalog(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_job_detail(connection)
        _insert_many_runs(connection, count=100_000)
        connection.commit()
        store = SqliteHistoryReadModelStore(connection)
        cursor = {
            "cursor_version": 1,
            "started_utc": "2026-07-20T12:00:00.000Z",
            "activity_kind": "BACKUP",
            "activity_id": "run-050000",
        }
        traced_sql: list[str] = []
        connection.set_trace_callback(traced_sql.append)

        warm = query_history_timeline(
            history_store=store,
            limit=25,
            activity_filter="BACKUPS",
            after=cursor,
        )
        started = perf_counter()
        page = query_history_timeline(
            history_store=store,
            limit=25,
            activity_filter="BACKUPS",
            after=cursor,
        )
        elapsed = perf_counter() - started
        connection.set_trace_callback(None)

        assert warm.activities[0].activity_id == "run-049999"
        assert page.activities[-1].activity_id == "run-049975"
        assert elapsed < 1.0
        run_query = next(
            sql
            for sql in reversed(traced_sql)
            if "FROM runs" in sql and "ORDER BY runs.started_utc" in sql
        )
        plan = connection.execute(f"EXPLAIN QUERY PLAN {run_query}").fetchall()
        details = [str(row[3]) for row in plan]
        assert any(
            "SEARCH runs USING INDEX idx_runs_started_id" in detail
            for detail in details
        )
        assert not any(detail == "SCAN runs" for detail in details)


def test_history_control_keyset_seeks_expression_indexes(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_control(connection)
        store = SqliteHistoryReadModelStore(connection)
        traced_sql: list[str] = []
        connection.set_trace_callback(traced_sql.append)

        query_history_timeline(
            history_store=store,
            limit=25,
            activity_filter="CONTROLS",
            after={
                "cursor_version": 1,
                "started_utc": "2026-07-20T13:00:00.000Z",
                "activity_kind": "CONTROL",
                "activity_id": "analysis-z",
            },
        )
        connection.set_trace_callback(None)

        initial_query = next(
            sql
            for sql in traced_sql
            if "FROM initial_backup_plan_materializations" in sql
        )
        requested_query = next(
            sql
            for sql in traced_sql
            if "'MANUAL_BACKUP_CHECK' AS trigger_type" in sql
        )
        initial_plan = connection.execute(
            f"EXPLAIN QUERY PLAN {initial_query}"
        ).fetchall()
        requested_plan = connection.execute(
            f"EXPLAIN QUERY PLAN {requested_query}"
        ).fetchall()
        initial_details = [str(row[3]) for row in initial_plan]
        requested_details = [str(row[3]) for row in requested_plan]

        assert any(
            "SEARCH materializations USING INDEX "
            "idx_initial_backup_materializations_history" in detail
            for detail in initial_details
        )
        assert any(
            "SEARCH requests USING INDEX idx_backup_analysis_requests_history"
            in detail
            for detail in requested_details
        ), requested_details


def _insert_control(connection: sqlite3.Connection) -> None:
    _insert_job_detail(connection)
    _insert_control_activity(
        connection,
        analysis_id="analysis-a",
        started_utc="2026-07-20T11:00:00.000Z",
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


def _insert_control_activity(
    connection: sqlite3.Connection,
    *,
    analysis_id: str,
    started_utc: str,
) -> None:
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
            ?,
            'NO_CHANGES',
            'INITIAL_BACKUP_PLAN_NO_CHANGES',
            0,
            0,
            0,
            'Nothing to copy.',
            ?,
            '2026-07-20T11:01:00.000Z'
        )
        """,
        (analysis_id, started_utc),
    )


def _insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str = "run-a",
    started_utc: str = "2026-07-20T12:00:00.000Z",
    include_target: bool = True,
) -> None:
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
            ?,
            'job-a',
            'job-rev-a',
            ?,
            ?,
            ?,
            'MANUAL_LOCAL_PREVIEW',
            'COMPLETED',
            '{}',
            '0B-dev',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            ?,
            2,
            1024,
            ?,
            '2026-07-20T12:01:00.000Z'
        )
        """,
        (
            run_id,
            f"plan-{run_id}",
            f"request-{run_id}",
            f"group-{run_id}",
            f"idempotency-{run_id}",
            started_utc,
        ),
    )
    if not include_target:
        return
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
            ?,
            ?,
            'target-a',
            'target-rev-a',
            'SUCCEEDED',
            2,
            2,
            1024,
            1024
        )
        """,
        (f"{run_id}-target-0000", run_id),
    )


def _insert_many_runs(connection: sqlite3.Connection, *, count: int) -> None:
    assert count == 100_000
    connection.execute(
        """
        WITH digits(value) AS (
            VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)
        ),
        sequence(value) AS (
            SELECT
                ones.value
                + tens.value * 10
                + hundreds.value * 100
                + thousands.value * 1000
                + ten_thousands.value * 10000
            FROM digits AS ones
            CROSS JOIN digits AS tens
            CROSS JOIN digits AS hundreds
            CROSS JOIN digits AS thousands
            CROSS JOIN digits AS ten_thousands
        )
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
        SELECT
            printf('run-%06d', value),
            'job-a',
            'job-rev-a',
            printf('plan-%06d', value),
            printf('request-%06d', value),
            printf('group-%06d', value),
            'MANUAL_LOCAL_PREVIEW',
            'COMPLETED',
            '{}',
            '0B-dev',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            printf('idempotency-%06d', value),
            0,
            0,
            '2026-07-20T12:00:00.000Z',
            '2026-07-20T12:01:00.000Z'
        FROM sequence
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
