from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigrationViolation,
    apply_sqlite_migrations,
    catalog_migration_plan,
    current_schema_version,
    recovery_migration_plan,
)


def test_catalog_migration_creates_contract_skeleton_and_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
        plan = catalog_migration_plan()

        apply_sqlite_migrations(connection, plan)
        apply_sqlite_migrations(connection, plan)

        assert current_schema_version(connection, plan.store) == 6
        assert _table_names(connection) >= {
            "endpoint_heads",
            "job_heads",
            "file_entries",
            "case_collision_members",
            "operation_dependencies",
            "standard_backup_job_drafts",
            "standard_backup_job_revision_details",
            "command_receipts",
            "plan_seal_details",
            "plan_operation_seal_details",
            "runs",
            "run_targets",
            "schema_migrations",
            "store_identity",
        }
        assert _row_count(connection, "schema_migrations") == 6
        assert _foreign_key(
            connection,
            "endpoint_heads",
            "endpoint_revisions",
            ("endpoint_id", "active_revision_id"),
            ("endpoint_id", "id"),
        )
        assert _foreign_key(
            connection,
            "job_heads",
            "job_revisions",
            ("job_id", "active_revision_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "operation_dependencies",
            "planned_operations",
            ("plan_id", "before_operation_id"),
            ("plan_id", "id"),
        )
        assert _foreign_key(
            connection,
            "standard_backup_job_revision_details",
            "job_revisions",
            ("job_id", "job_revision_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "runs",
            "plan_seal_details",
            ("plan_id", "job_id", "job_revision_id"),
            ("plan_id", "job_id", "job_revision_id"),
        )
        assert _foreign_key(
            connection,
            "runs",
            "command_receipts",
            ("command_receipt_id",),
            ("idempotency_key",),
        )
        assert _index_is_unique(connection, "file_entries", ("snapshot_id", "comparison_key")) is False
        assert _index_is_unique(connection, "command_receipts", ("state",)) is False
        assert _index_is_unique(connection, "runs", ("state",)) is False
        assert _trigger_names(connection) >= {
            "trg_plans_no_update_after_seal",
            "trg_planned_operations_no_insert_after_seal",
            "trg_plan_operation_seal_details_no_insert",
            "trg_plan_seal_details_no_update",
        }


def test_catalog_migration_preserves_case_collision_entries(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
        apply_sqlite_migrations(connection, catalog_migration_plan())

        _insert_catalog_parent_rows(connection)
        connection.execute(
            """
            INSERT INTO file_entries
                (snapshot_id, endpoint_id, id, relative_path, comparison_key, object_type)
                VALUES ('snapshot-a', 'endpoint-a', 'file-a', 'Readme.txt', 'readme.txt', 'file')
            """
        )
        connection.execute(
            """
            INSERT INTO file_entries
                (snapshot_id, endpoint_id, id, relative_path, comparison_key, object_type)
                VALUES ('snapshot-a', 'endpoint-a', 'file-b', 'README.TXT', 'readme.txt', 'file')
            """
        )
        connection.execute(
            """
            INSERT INTO case_collision_groups (snapshot_id, id, comparison_key)
                VALUES ('snapshot-a', 'group-a', 'readme.txt')
            """
        )
        connection.execute(
            """
            INSERT INTO case_collision_members (snapshot_id, group_id, file_entry_id)
                VALUES ('snapshot-a', 'group-a', 'file-a')
            """
        )
        connection.execute(
            """
            INSERT INTO case_collision_members (snapshot_id, group_id, file_entry_id)
                VALUES ('snapshot-a', 'group-a', 'file-b')
            """
        )

        assert _row_count(connection, "file_entries") == 2
        assert _row_count(connection, "case_collision_members") == 2


def test_catalog_migration_enforces_composite_head_foreign_key(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
        apply_sqlite_migrations(connection, catalog_migration_plan())
        connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'missing-revision')"
            )


def test_recovery_migration_creates_journal_skeleton_and_enforces_epoch(tmp_path: Path) -> None:
    database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
        plan = recovery_migration_plan()

        apply_sqlite_migrations(connection, plan)

        assert current_schema_version(connection, plan.store) == 1
        assert _table_names(connection) >= {
            "recovery_epochs",
            "recovery_intents",
            "recovery_intent_steps",
            "schema_migrations",
            "store_identity",
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO recovery_intents (epoch_id, id, correlation_id, state)
                    VALUES ('missing-epoch', 'intent-a', 'corr-a', 'PREPARED')
                """
            )


def test_migration_runner_rejects_wrong_store_identity(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
        apply_sqlite_migrations(connection, catalog_migration_plan())

        with pytest.raises(SqliteMigrationViolation, match="MIGRATION_STORE_IDENTITY_MISMATCH"):
            apply_sqlite_migrations(connection, recovery_migration_plan())


def _insert_catalog_parent_rows(connection: sqlite3.Connection) -> None:
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
        "INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'job-rev-a')"
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


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _foreign_key(
    connection: sqlite3.Connection,
    table: str,
    parent_table: str,
    child_columns: tuple[str, ...],
    parent_columns: tuple[str, ...],
) -> bool:
    grouped: dict[int, list[sqlite3.Row | tuple[object, ...]]] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
        grouped.setdefault(int(row[0]), []).append(row)
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda row: int(row[1]))
        if str(ordered[0][2]) != parent_table:
            continue
        if tuple(str(row[3]) for row in ordered) == child_columns and tuple(
            str(row[4]) for row in ordered
        ) == parent_columns:
            return True
    return False


def _index_is_unique(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> bool | None:
    for index_row in connection.execute(f"PRAGMA index_list({table})"):
        index_name = str(index_row[1])
        index_columns = tuple(
            str(column_row[2]) for column_row in connection.execute(f"PRAGMA index_info({index_name})")
        )
        if index_columns == columns:
            return bool(index_row[2])
    return None


def _trigger_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND name NOT LIKE 'sqlite_%'
            """
        )
    }
