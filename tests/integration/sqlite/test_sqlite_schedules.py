from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.schedules import SqliteScheduleStore, SqliteScheduleStoreError
from mediasync_home.application.schedules import (
    ScheduleDefinition,
    ScheduleTriggerResolutionKind,
    resolve_schedule_for_trigger,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


def test_sqlite_schedule_store_roundtrips_and_resolves_trigger_definition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqliteScheduleStore(connection)
        schedule = _schedule()

        store.save_schedule(schedule)
        loaded = store.load_schedule("schedule-a")
        resolution = resolve_schedule_for_trigger(
            schedules=store,
            schedule_id="schedule-a",
            schedule_revision_hash="b" * 64,
        )

        assert loaded == schedule
        assert resolution.kind is ScheduleTriggerResolutionKind.READY
        assert resolution.schedule == schedule


def test_sqlite_schedule_store_upserts_current_desired_definition(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqliteScheduleStore(connection)
        store.save_schedule(_schedule())

        updated = replace(
            _schedule(),
            desired_definition_hash="c" * 64,
            definition_generation=2,
            row_version=2,
            enabled=False,
        )
        store.save_schedule(updated)

        assert store.load_schedule("schedule-a") == updated
        assert _row_count(connection, "schedules") == 1


def test_sqlite_schedule_store_enforces_existing_job_and_plan(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteScheduleStore(connection)

        with pytest.raises(SqliteScheduleStoreError, match="SCHEDULE_SAVE_FAILED"):
            store.save_schedule(_schedule())


def _schedule() -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id="job-a",
        plan_id="plan-a",
        plan_checksum="a" * 64,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash="b" * 64,
        time_zone_id="Europe/Oslo",
        dst_policy="PRESERVE_WALL_TIME",
        misfire_policy="QUEUE_ONCE",
        coalescing_window_seconds=60,
        task_logon_type="INTERACTIVE_TOKEN",
        requires_network=False,
        run_only_when_logged_on=True,
        enabled=True,
        row_version=1,
    )


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_plan_parent_rows(connection: sqlite3.Connection) -> None:
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
    connection.execute("INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')")
    connection.execute(
        """
        INSERT INTO plan_seal_details (
            plan_id,
            analysis_id,
            job_id,
            job_revision_id,
            planner_version,
            plan_schema_version,
            operation_schema_version,
            execution_policy,
            checksum_algorithm,
            serializer_version,
            plan_checksum,
            risk_summary_json,
            operation_count,
            planned_bytes
        )
        VALUES (
            'plan-a',
            'analysis-a',
            'job-a',
            'job-rev-a',
            'planner',
            1,
            1,
            'dry-run',
            'SHA-256',
            'canonical-json',
            ?,
            '{}',
            1,
            0
        )
        """,
        ("a" * 64,),
    )


def _row_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()
    assert row is not None
    return int(row[0])
