from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from mediasync_home.adapters.sqlite.connection_policy import SqliteStore


class SqliteMigrationViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SqliteMigration:
    version: int
    name: str
    statements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SqliteMigrationPlan:
    store: SqliteStore
    migrations: tuple[SqliteMigration, ...]


def catalog_migration_plan() -> SqliteMigrationPlan:
    return SqliteMigrationPlan(
        store=SqliteStore.CATALOG,
        migrations=(
            SqliteMigration(
                version=1,
                name="catalog_core_contract_skeleton",
                statements=CATALOG_CORE_CONTRACT_SKELETON,
            ),
        ),
    )


def recovery_migration_plan() -> SqliteMigrationPlan:
    return SqliteMigrationPlan(
        store=SqliteStore.RECOVERY,
        migrations=(
            SqliteMigration(
                version=1,
                name="recovery_journal_skeleton",
                statements=RECOVERY_JOURNAL_SKELETON,
            ),
        ),
    )


def validate_migration_plan(plan: SqliteMigrationPlan) -> None:
    if not plan.migrations:
        raise SqliteMigrationViolation("MIGRATION_PLAN_REQUIRES_MIGRATIONS")

    expected_version = 1
    seen_names: set[str] = set()
    for migration in plan.migrations:
        if migration.version != expected_version:
            raise SqliteMigrationViolation("MIGRATION_VERSIONS_MUST_BE_CONTIGUOUS")
        if not migration.name or migration.name in seen_names:
            raise SqliteMigrationViolation("MIGRATION_NAMES_MUST_BE_UNIQUE")
        if not migration.statements:
            raise SqliteMigrationViolation("MIGRATION_REQUIRES_STATEMENTS")
        if any(not statement.strip() for statement in migration.statements):
            raise SqliteMigrationViolation("MIGRATION_STATEMENT_MUST_NOT_BE_EMPTY")
        seen_names.add(migration.name)
        expected_version += 1


def apply_sqlite_migrations(connection: sqlite3.Connection, plan: SqliteMigrationPlan) -> None:
    validate_migration_plan(plan)
    _ensure_migration_metadata(connection, plan.store)
    applied_versions = _applied_versions(connection, plan.store)
    for migration in plan.migrations:
        if migration.version in applied_versions:
            continue
        _apply_migration(connection, plan.store, migration)
        applied_versions.add(migration.version)


def current_schema_version(connection: sqlite3.Connection, store: SqliteStore) -> int:
    _ensure_migration_metadata(connection, store)
    row = connection.execute(
        "SELECT max(version) FROM schema_migrations WHERE store = ?",
        (store.value,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def _ensure_migration_metadata(connection: sqlite3.Connection, store: SqliteStore) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS store_identity (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            store TEXT NOT NULL CHECK (store IN ('catalog', 'recovery'))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            store TEXT NOT NULL CHECK (store IN ('catalog', 'recovery')),
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            applied_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            PRIMARY KEY (store, version),
            UNIQUE (store, name)
        )
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO store_identity (singleton, store) VALUES (1, ?)",
        (store.value,),
    )
    row = connection.execute("SELECT store FROM store_identity WHERE singleton = 1").fetchone()
    if row is None or row[0] != store.value:
        raise SqliteMigrationViolation("MIGRATION_STORE_IDENTITY_MISMATCH")
    connection.commit()


def _applied_versions(connection: sqlite3.Connection, store: SqliteStore) -> set[int]:
    return {
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations WHERE store = ?",
            (store.value,),
        )
    }


def _apply_migration(
    connection: sqlite3.Connection,
    store: SqliteStore,
    migration: SqliteMigration,
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations (store, version, name) VALUES (?, ?, ?)",
            (store.value, migration.version, migration.name),
        )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


CATALOG_CORE_CONTRACT_SKELETON = (
    """
    CREATE TABLE endpoints (
        id TEXT PRIMARY KEY,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE endpoint_revisions (
        endpoint_id TEXT NOT NULL,
        id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        root_uri TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (endpoint_id, id),
        FOREIGN KEY (endpoint_id) REFERENCES endpoints (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE endpoint_heads (
        endpoint_id TEXT PRIMARY KEY,
        active_revision_id TEXT NOT NULL,
        FOREIGN KEY (endpoint_id, active_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE filter_sets (
        job_id TEXT NOT NULL,
        id TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (job_id, id),
        FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE job_revisions (
        job_id TEXT NOT NULL,
        id TEXT NOT NULL,
        filter_set_id TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (job_id, id),
        FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE RESTRICT,
        FOREIGN KEY (job_id, filter_set_id)
            REFERENCES filter_sets (job_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE job_heads (
        job_id TEXT PRIMARY KEY,
        active_revision_id TEXT NOT NULL,
        FOREIGN KEY (job_id, active_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE analyses (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE analysis_targets (
        analysis_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        PRIMARY KEY (analysis_id, endpoint_id),
        FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE snapshots (
        id TEXT NOT NULL,
        analysis_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (id, endpoint_id),
        UNIQUE (id),
        FOREIGN KEY (analysis_id, endpoint_id)
            REFERENCES analysis_targets (analysis_id, endpoint_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE file_entries (
        snapshot_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        comparison_key TEXT NOT NULL,
        object_type TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, id),
        UNIQUE (snapshot_id, relative_path),
        FOREIGN KEY (snapshot_id, endpoint_id)
            REFERENCES snapshots (id, endpoint_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_file_entries_snapshot_comparison_key
        ON file_entries (snapshot_id, comparison_key)
    """,
    """
    CREATE TABLE case_collision_groups (
        snapshot_id TEXT NOT NULL,
        id TEXT NOT NULL,
        comparison_key TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, id),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE case_collision_members (
        snapshot_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        file_entry_id TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, group_id, file_entry_id),
        FOREIGN KEY (snapshot_id, file_entry_id)
            REFERENCES file_entries (snapshot_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (snapshot_id, group_id)
            REFERENCES case_collision_groups (snapshot_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE plans (
        id TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE planned_operations (
        plan_id TEXT NOT NULL,
        id TEXT NOT NULL,
        operation_type TEXT NOT NULL,
        PRIMARY KEY (plan_id, id),
        FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE operation_dependencies (
        plan_id TEXT NOT NULL,
        before_operation_id TEXT NOT NULL,
        after_operation_id TEXT NOT NULL,
        PRIMARY KEY (plan_id, before_operation_id, after_operation_id),
        FOREIGN KEY (plan_id, before_operation_id)
            REFERENCES planned_operations (plan_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id, after_operation_id)
            REFERENCES planned_operations (plan_id, id)
            ON DELETE RESTRICT
    )
    """,
)

RECOVERY_JOURNAL_SKELETON = (
    """
    CREATE TABLE recovery_epochs (
        id TEXT PRIMARY KEY,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        state TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE recovery_intents (
        epoch_id TEXT NOT NULL,
        id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        state TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (epoch_id, id),
        FOREIGN KEY (epoch_id) REFERENCES recovery_epochs (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE recovery_intent_steps (
        epoch_id TEXT NOT NULL,
        intent_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        state TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        PRIMARY KEY (epoch_id, intent_id, step_id),
        FOREIGN KEY (epoch_id, intent_id)
            REFERENCES recovery_intents (epoch_id, id)
            ON DELETE RESTRICT
    )
    """,
)
