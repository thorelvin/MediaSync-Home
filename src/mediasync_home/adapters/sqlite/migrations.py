from __future__ import annotations

import hashlib
import json
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


@dataclass(frozen=True, slots=True)
class SqliteMigrationState:
    store: SqliteStore
    current_version: int
    target_version: int
    initialized: bool
    checksum_backfill_required: bool


def catalog_migration_plan() -> SqliteMigrationPlan:
    return SqliteMigrationPlan(
        store=SqliteStore.CATALOG,
        migrations=(
            SqliteMigration(
                version=1,
                name="catalog_core_contract_skeleton",
                statements=CATALOG_CORE_CONTRACT_SKELETON,
            ),
            SqliteMigration(
                version=2,
                name="catalog_standard_backup_drafts",
                statements=CATALOG_STANDARD_BACKUP_DRAFTS,
            ),
            SqliteMigration(
                version=3,
                name="catalog_standard_backup_job_revisions",
                statements=CATALOG_STANDARD_BACKUP_JOB_REVISIONS,
            ),
            SqliteMigration(
                version=4,
                name="catalog_command_receipts",
                statements=CATALOG_COMMAND_RECEIPTS,
            ),
            SqliteMigration(
                version=5,
                name="catalog_sealed_plan_details",
                statements=CATALOG_SEALED_PLAN_DETAILS,
            ),
            SqliteMigration(
                version=6,
                name="catalog_run_start_skeleton",
                statements=CATALOG_RUN_START_SKELETON,
            ),
            SqliteMigration(
                version=7,
                name="catalog_plan_endpoint_bindings",
                statements=CATALOG_PLAN_ENDPOINT_BINDINGS,
            ),
            SqliteMigration(
                version=8,
                name="catalog_transactional_outbox_skeleton",
                statements=CATALOG_TRANSACTIONAL_OUTBOX_SKELETON,
            ),
            SqliteMigration(
                version=9,
                name="catalog_final_file_handoff_skeleton",
                statements=CATALOG_FINAL_FILE_HANDOFF_SKELETON,
            ),
            SqliteMigration(
                version=10,
                name="catalog_snapshot_entry_materialization",
                statements=CATALOG_SNAPSHOT_ENTRY_MATERIALIZATION,
            ),
            SqliteMigration(
                version=11,
                name="catalog_snapshot_seal_checksum",
                statements=CATALOG_SNAPSHOT_SEAL_CHECKSUM,
            ),
            SqliteMigration(
                version=12,
                name="catalog_snapshot_coverage_issue_materialization",
                statements=CATALOG_SNAPSHOT_COVERAGE_ISSUE_MATERIALIZATION,
            ),
            SqliteMigration(
                version=13,
                name="catalog_snapshot_entry_read_model_indexes",
                statements=CATALOG_SNAPSHOT_ENTRY_READ_MODEL_INDEXES,
            ),
            SqliteMigration(
                version=14,
                name="catalog_plan_operation_read_model_indexes",
                statements=CATALOG_PLAN_OPERATION_READ_MODEL_INDEXES,
            ),
            SqliteMigration(
                version=15,
                name="catalog_outbox_reconciliation_indexes",
                statements=CATALOG_OUTBOX_RECONCILIATION_INDEXES,
            ),
            SqliteMigration(
                version=16,
                name="catalog_snapshot_coverage_issue_read_model_indexes",
                statements=CATALOG_SNAPSHOT_COVERAGE_ISSUE_READ_MODEL_INDEXES,
            ),
            SqliteMigration(
                version=17,
                name="catalog_command_receipt_tombstones",
                statements=CATALOG_COMMAND_RECEIPT_TOMBSTONES,
            ),
            SqliteMigration(
                version=18,
                name="catalog_run_activity_read_model_indexes",
                statements=CATALOG_RUN_ACTIVITY_READ_MODEL_INDEXES,
            ),
            SqliteMigration(
                version=19,
                name="catalog_trigger_occurrence_dedup",
                statements=CATALOG_TRIGGER_OCCURRENCE_DEDUP,
            ),
            SqliteMigration(
                version=20,
                name="catalog_schedule_desired_state",
                statements=CATALOG_SCHEDULE_DESIRED_STATE,
            ),
            SqliteMigration(
                version=21,
                name="catalog_external_resource_state",
                statements=CATALOG_EXTERNAL_RESOURCE_STATE,
            ),
            SqliteMigration(
                version=22,
                name="catalog_endpoint_revision_identity",
                statements=CATALOG_ENDPOINT_REVISION_IDENTITY,
            ),
            SqliteMigration(
                version=23,
                name="catalog_standard_backup_job_endpoint_bindings",
                statements=CATALOG_STANDARD_BACKUP_JOB_ENDPOINT_BINDINGS,
            ),
            SqliteMigration(
                version=24,
                name="catalog_installation_state",
                statements=CATALOG_INSTALLATION_STATE,
            ),
            SqliteMigration(
                version=25,
                name="catalog_endpoint_classification_observations",
                statements=CATALOG_ENDPOINT_CLASSIFICATION_OBSERVATIONS,
            ),
            SqliteMigration(
                version=26,
                name="catalog_standard_backup_job_snapshot_materializations",
                statements=CATALOG_STANDARD_BACKUP_JOB_SNAPSHOT_MATERIALIZATIONS,
            ),
            SqliteMigration(
                version=27,
                name="catalog_immutable_revision_guards",
                statements=CATALOG_IMMUTABLE_REVISION_GUARDS,
            ),
            SqliteMigration(
                version=28,
                name="catalog_filter_set_versions",
                statements=CATALOG_FILTER_SET_VERSIONS,
            ),
            SqliteMigration(
                version=29,
                name="catalog_endpoint_generation_bindings",
                statements=CATALOG_ENDPOINT_GENERATION_BINDINGS,
            ),
            SqliteMigration(
                version=30,
                name="catalog_writable_endpoint_registrations",
                statements=CATALOG_WRITABLE_ENDPOINT_REGISTRATIONS,
            ),
            SqliteMigration(
                version=31,
                name="catalog_initial_backup_plan_materializations",
                statements=CATALOG_INITIAL_BACKUP_PLAN_MATERIALIZATIONS,
            ),
            SqliteMigration(
                version=32,
                name="catalog_directory_effect_handoffs",
                statements=CATALOG_DIRECTORY_EFFECT_HANDOFFS,
            ),
            SqliteMigration(
                version=33,
                name="catalog_plan_operation_target_bindings",
                statements=CATALOG_PLAN_OPERATION_TARGET_BINDINGS,
            ),
            SqliteMigration(
                version=34,
                name="catalog_run_stop_requests",
                statements=CATALOG_RUN_STOP_REQUESTS,
            ),
            SqliteMigration(
                version=35,
                name="catalog_backup_analysis_requests",
                statements=CATALOG_BACKUP_ANALYSIS_REQUESTS,
            ),
            SqliteMigration(
                version=36,
                name="catalog_current_read_hash_evidence",
                statements=CATALOG_CURRENT_READ_HASH_EVIDENCE,
            ),
            SqliteMigration(
                version=37,
                name="catalog_source_file_preconditions",
                statements=CATALOG_SOURCE_FILE_PRECONDITIONS,
            ),
            SqliteMigration(
                version=38,
                name="catalog_run_target_endpoint_waits",
                statements=CATALOG_RUN_TARGET_ENDPOINT_WAITS,
            ),
            SqliteMigration(
                version=39,
                name="catalog_run_target_endpoint_retry_timing",
                statements=CATALOG_RUN_TARGET_ENDPOINT_RETRY_TIMING,
            ),
            SqliteMigration(
                version=40,
                name="catalog_run_operation_audit",
                statements=CATALOG_RUN_OPERATION_AUDIT,
            ),
            SqliteMigration(
                version=41,
                name="catalog_history_timeline_keyset_indexes",
                statements=CATALOG_HISTORY_TIMELINE_KEYSET_INDEXES,
            ),
            SqliteMigration(
                version=42,
                name="catalog_controlled_endpoint_takeovers",
                statements=CATALOG_CONTROLLED_ENDPOINT_TAKEOVERS,
            ),
            SqliteMigration(
                version=43,
                name="catalog_job_lifecycle",
                statements=CATALOG_JOB_LIFECYCLE,
            ),
            SqliteMigration(
                version=44,
                name="catalog_retained_version_objects",
                statements=CATALOG_RETAINED_VERSION_OBJECTS,
            ),
            SqliteMigration(
                version=45,
                name="catalog_version_retention_plans",
                statements=CATALOG_VERSION_RETENTION_PLANS,
            ),
            SqliteMigration(
                version=46,
                name="catalog_retained_version_restore_operations",
                statements=CATALOG_RETAINED_VERSION_RESTORE_OPERATIONS,
            ),
            SqliteMigration(
                version=47,
                name="catalog_retained_version_restore_rollback_lifecycle",
                statements=CATALOG_RETAINED_VERSION_RESTORE_ROLLBACK_LIFECYCLE,
            ),
            SqliteMigration(
                version=48,
                name="catalog_retained_recovery_object_roles",
                statements=CATALOG_RETAINED_RECOVERY_OBJECT_ROLES,
            ),
            SqliteMigration(
                version=49,
                name="catalog_snapshot_birthtime_evidence",
                statements=CATALOG_SNAPSHOT_BIRTHTIME_EVIDENCE,
            ),
            SqliteMigration(
                version=50,
                name="catalog_endpoint_capability_evidence",
                statements=CATALOG_ENDPOINT_CAPABILITY_EVIDENCE,
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
            SqliteMigration(
                version=2,
                name="recovery_lease_counters",
                statements=RECOVERY_LEASE_COUNTERS,
            ),
            SqliteMigration(
                version=3,
                name="recovery_resource_leases",
                statements=RECOVERY_RESOURCE_LEASES,
            ),
            SqliteMigration(
                version=4,
                name="recovery_intent_segments",
                statements=RECOVERY_INTENT_SEGMENTS,
            ),
            SqliteMigration(
                version=5,
                name="recovery_operation_journal",
                statements=RECOVERY_OPERATION_JOURNAL,
            ),
            SqliteMigration(
                version=6,
                name="recovery_operation_kind_and_plan_sequence",
                statements=RECOVERY_OPERATION_KIND_AND_PLAN_SEQUENCE,
            ),
            SqliteMigration(
                version=7,
                name="recovery_operation_planned_bytes",
                statements=RECOVERY_OPERATION_PLANNED_BYTES,
            ),
            SqliteMigration(
                version=8,
                name="recovery_source_file_preconditions",
                statements=RECOVERY_SOURCE_FILE_PRECONDITIONS,
            ),
            SqliteMigration(
                version=9,
                name="recovery_staging_failure_count",
                statements=RECOVERY_STAGING_FAILURE_COUNT,
            ),
            SqliteMigration(
                version=10,
                name="recovery_staging_retry_timing",
                statements=RECOVERY_STAGING_RETRY_TIMING,
            ),
            SqliteMigration(
                version=11,
                name="recovery_version_retention_metadata",
                statements=RECOVERY_VERSION_RETENTION_METADATA,
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


def apply_sqlite_migrations(
    connection: sqlite3.Connection, plan: SqliteMigrationPlan
) -> None:
    validate_migration_plan(plan)
    _ensure_migration_metadata(connection, plan)
    applied_versions = _verified_applied_versions(connection, plan)
    for migration in plan.migrations:
        if migration.version in applied_versions:
            continue
        _apply_migration(connection, plan.store, migration)
        applied_versions.add(migration.version)


def current_schema_version(connection: sqlite3.Connection, store: SqliteStore) -> int:
    metadata_tables = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
                AND name IN ('store_identity', 'schema_migrations')
            """
        ).fetchall()
    }
    if metadata_tables != {"store_identity", "schema_migrations"}:
        return 0
    identity = connection.execute(
        "SELECT store FROM store_identity WHERE singleton = 1"
    ).fetchone()
    if identity is None or identity[0] != store.value:
        raise SqliteMigrationViolation("MIGRATION_STORE_IDENTITY_MISMATCH")
    row = connection.execute(
        "SELECT max(version) FROM schema_migrations WHERE store = ?",
        (store.value,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def inspect_sqlite_migration_state(
    connection: sqlite3.Connection,
    plan: SqliteMigrationPlan,
) -> SqliteMigrationState:
    validate_migration_plan(plan)
    tables = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
                AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }
    metadata_tables = tables & {"store_identity", "schema_migrations"}
    if not metadata_tables:
        if tables:
            raise SqliteMigrationViolation("MIGRATION_UNMANAGED_SCHEMA")
        return SqliteMigrationState(
            store=plan.store,
            current_version=0,
            target_version=len(plan.migrations),
            initialized=False,
            checksum_backfill_required=False,
        )
    if metadata_tables != {"store_identity", "schema_migrations"}:
        raise SqliteMigrationViolation("MIGRATION_METADATA_INCOMPLETE")

    identity = connection.execute(
        "SELECT store FROM store_identity WHERE singleton = 1"
    ).fetchone()
    if identity is None or identity[0] != plan.store.value:
        raise SqliteMigrationViolation("MIGRATION_STORE_IDENTITY_MISMATCH")
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    }
    checksum_column_present = "migration_checksum" in columns
    rows = _migration_history_rows(
        connection,
        plan.store,
        has_checksum_column=checksum_column_present,
    )
    _validate_migration_history(
        plan,
        rows,
        allow_missing_checksums=True,
    )
    return SqliteMigrationState(
        store=plan.store,
        current_version=0 if not rows else rows[-1][0],
        target_version=len(plan.migrations),
        initialized=True,
        checksum_backfill_required=(
            not checksum_column_present or any(row[2] is None for row in rows)
        ),
    )


def migration_checksum(migration: SqliteMigration) -> str:
    payload = json.dumps(
        {
            "name": migration.name,
            "statements": list(migration.statements),
            "version": migration.version,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ensure_migration_metadata(
    connection: sqlite3.Connection,
    plan: SqliteMigrationPlan,
) -> None:
    store = plan.store
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
            migration_checksum TEXT NOT NULL CHECK (
                length(migration_checksum) = 64
                AND migration_checksum NOT GLOB '*[^0-9a-f]*'
            ),
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
    row = connection.execute(
        "SELECT store FROM store_identity WHERE singleton = 1"
    ).fetchone()
    if row is None or row[0] != store.value:
        raise SqliteMigrationViolation("MIGRATION_STORE_IDENTITY_MISMATCH")
    connection.commit()
    _upgrade_and_verify_migration_history(connection, plan)


def _upgrade_and_verify_migration_history(
    connection: sqlite3.Connection,
    plan: SqliteMigrationPlan,
) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    }
    has_checksum_column = "migration_checksum" in columns
    rows = _migration_history_rows(
        connection,
        plan.store,
        has_checksum_column=has_checksum_column,
    )
    _validate_migration_history(
        plan,
        rows,
        allow_missing_checksums=True,
    )

    needs_backfill = not has_checksum_column or any(row[2] is None for row in rows)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not has_checksum_column:
            connection.execute(
                "ALTER TABLE schema_migrations ADD COLUMN migration_checksum TEXT"
            )
        if needs_backfill:
            migrations_by_version = {
                migration.version: migration for migration in plan.migrations
            }
            for version, _name, checksum in rows:
                if checksum is not None:
                    continue
                connection.execute(
                    """
                    UPDATE schema_migrations
                    SET migration_checksum = ?
                    WHERE store = ?
                        AND version = ?
                        AND migration_checksum IS NULL
                    """,
                    (
                        migration_checksum(migrations_by_version[version]),
                        plan.store.value,
                        version,
                    ),
                )
        _create_migration_history_guards(connection)
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise

    verified_rows = _migration_history_rows(
        connection,
        plan.store,
        has_checksum_column=True,
    )
    _validate_migration_history(
        plan,
        verified_rows,
        allow_missing_checksums=False,
    )


def _migration_history_rows(
    connection: sqlite3.Connection,
    store: SqliteStore,
    *,
    has_checksum_column: bool,
) -> tuple[tuple[int, str, str | None], ...]:
    foreign_store_row = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE store != ? LIMIT 1",
        (store.value,),
    ).fetchone()
    if foreign_store_row is not None:
        raise SqliteMigrationViolation("MIGRATION_HISTORY_FOREIGN_STORE_ROW")
    checksum_projection = "migration_checksum" if has_checksum_column else "NULL"
    rows = connection.execute(
        f"""
        SELECT version, name, {checksum_projection}
        FROM schema_migrations
        WHERE store = ?
        ORDER BY version
        """,
        (store.value,),
    ).fetchall()
    return tuple(
        (
            int(row[0]),
            str(row[1]),
            None if row[2] is None else str(row[2]),
        )
        for row in rows
    )


def _validate_migration_history(
    plan: SqliteMigrationPlan,
    rows: tuple[tuple[int, str, str | None], ...],
    *,
    allow_missing_checksums: bool,
) -> None:
    if rows and rows[-1][0] > len(plan.migrations):
        raise SqliteMigrationViolation("MIGRATION_SCHEMA_NEWER_THAN_RUNTIME")
    expected_versions = tuple(range(1, len(rows) + 1))
    if tuple(row[0] for row in rows) != expected_versions:
        raise SqliteMigrationViolation("MIGRATION_HISTORY_GAP")

    migrations_by_version = {
        migration.version: migration for migration in plan.migrations
    }
    for version, name, checksum in rows:
        expected = migrations_by_version[version]
        if name != expected.name:
            raise SqliteMigrationViolation("MIGRATION_HISTORY_NAME_MISMATCH")
        if checksum is None:
            if allow_missing_checksums:
                continue
            raise SqliteMigrationViolation("MIGRATION_HISTORY_CHECKSUM_MISSING")
        if checksum != migration_checksum(expected):
            raise SqliteMigrationViolation("MIGRATION_HISTORY_CHECKSUM_MISMATCH")


def _verified_applied_versions(
    connection: sqlite3.Connection,
    plan: SqliteMigrationPlan,
) -> set[int]:
    rows = _migration_history_rows(
        connection,
        plan.store,
        has_checksum_column=True,
    )
    _validate_migration_history(
        plan,
        rows,
        allow_missing_checksums=False,
    )
    return {row[0] for row in rows}


def _create_migration_history_guards(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_schema_migrations_valid_insert
        BEFORE INSERT ON schema_migrations
        WHEN
            NEW.store != (SELECT store FROM store_identity WHERE singleton = 1)
            OR NEW.migration_checksum IS NULL
            OR length(NEW.migration_checksum) != 64
            OR NEW.migration_checksum GLOB '*[^0-9a-f]*'
        BEGIN
            SELECT RAISE(ABORT, 'schema migration history entry is invalid');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_schema_migrations_immutable_update
        BEFORE UPDATE ON schema_migrations
        BEGIN
            SELECT RAISE(ABORT, 'schema migration history is immutable');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_schema_migrations_immutable_delete
        BEFORE DELETE ON schema_migrations
        BEGIN
            SELECT RAISE(ABORT, 'schema migration history is immutable');
        END
        """
    )


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
            """
            INSERT INTO schema_migrations (
                store,
                version,
                name,
                migration_checksum
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                store.value,
                migration.version,
                migration.name,
                migration_checksum(migration),
            ),
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

CATALOG_STANDARD_BACKUP_DRAFTS = (
    """
    CREATE TABLE standard_backup_job_drafts (
        draft_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        source_name TEXT,
        source_path_label TEXT,
        defaults_json TEXT NOT NULL,
        targets_json TEXT NOT NULL,
        updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
)

CATALOG_STANDARD_BACKUP_JOB_REVISIONS = (
    """
    CREATE TABLE standard_backup_job_revision_details (
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        draft_id TEXT NOT NULL,
        command_request_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_path_label TEXT NOT NULL,
        defaults_json TEXT NOT NULL,
        targets_json TEXT NOT NULL,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (job_id, job_revision_id),
        UNIQUE (idempotency_key),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (draft_id)
            REFERENCES standard_backup_job_drafts (draft_id)
            ON DELETE RESTRICT
    )
    """,
)

CATALOG_COMMAND_RECEIPTS = (
    """
    CREATE TABLE command_receipts (
        idempotency_key TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        client_instance_id TEXT NOT NULL,
        principal_fingerprint TEXT NOT NULL,
        command_name TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        protocol_version INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN (
                'RECEIVED',
                'VALIDATED',
                'EFFECT_PREPARED',
                'ACCEPTED',
                'RUNNING',
                'SUCCEEDED',
                'REJECTED',
                'FAILED',
                'CANCELLED'
            )
        ),
        expected_entity_revision INTEGER,
        payload_hash_scope TEXT NOT NULL,
        payload_canonicalization_algorithm TEXT NOT NULL,
        payload_hash_algorithm TEXT NOT NULL,
        result_entity_type TEXT,
        result_entity_id TEXT,
        rejection_reason TEXT,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE INDEX idx_command_receipts_state
        ON command_receipts (state)
    """,
)

CATALOG_COMMAND_RECEIPT_TOMBSTONES = (
    """
    CREATE TABLE command_dedup_tombstones (
        idempotency_key TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        client_instance_id TEXT NOT NULL,
        principal_fingerprint TEXT NOT NULL,
        command_name TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        protocol_version INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        terminal_state TEXT NOT NULL CHECK (
            terminal_state IN ('SUCCEEDED', 'REJECTED', 'FAILED', 'CANCELLED')
        ),
        expected_entity_revision INTEGER,
        payload_hash_scope TEXT NOT NULL,
        payload_canonicalization_algorithm TEXT NOT NULL,
        payload_hash_algorithm TEXT NOT NULL,
        result_entity_type TEXT,
        result_entity_id TEXT,
        rejection_reason TEXT,
        terminal_effect_hash TEXT NOT NULL CHECK (
            length(terminal_effect_hash) = 64
            AND terminal_effect_hash NOT GLOB '*[^0-9a-f]*'
        ),
        first_seen_utc TEXT NOT NULL,
        compacted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
)

CATALOG_SEALED_PLAN_DETAILS = (
    """
    CREATE TABLE plan_seal_details (
        plan_id TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        parent_plan_id TEXT,
        planner_version TEXT NOT NULL,
        plan_schema_version INTEGER NOT NULL,
        operation_schema_version INTEGER NOT NULL,
        execution_policy TEXT NOT NULL,
        checksum_algorithm TEXT NOT NULL,
        serializer_version TEXT NOT NULL,
        plan_checksum TEXT NOT NULL CHECK (length(plan_checksum) = 64),
        risk_summary_json TEXT NOT NULL,
        operation_count INTEGER NOT NULL CHECK (operation_count > 0),
        planned_bytes INTEGER NOT NULL CHECK (planned_bytes >= 0),
        immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable = 1),
        sealed_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (plan_id, analysis_id),
        UNIQUE (plan_id, job_id, job_revision_id),
        FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE RESTRICT,
        FOREIGN KEY (parent_plan_id) REFERENCES plans (id) ON DELETE RESTRICT,
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE plan_operation_seal_details (
        plan_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
        execution_phase INTEGER NOT NULL CHECK (execution_phase >= 0),
        stable_order_key TEXT NOT NULL,
        target_precondition_kind TEXT NOT NULL CHECK (
            target_precondition_kind IN ('ABSENT', 'MATCH_FINGERPRINT', 'DIRECTORY_EMPTY', 'NONE')
        ),
        reason_code TEXT NOT NULL,
        risk_level TEXT NOT NULL CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'BLOCKED')),
        target_relative_path TEXT,
        planned_bytes INTEGER NOT NULL DEFAULT 0 CHECK (planned_bytes >= 0),
        PRIMARY KEY (plan_id, operation_id),
        UNIQUE (plan_id, sequence_no),
        FOREIGN KEY (plan_id, operation_id)
            REFERENCES planned_operations (plan_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER trg_plans_no_update_after_seal
    BEFORE UPDATE ON plans
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plans_no_delete_after_seal
    BEFORE DELETE ON plans
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_planned_operations_no_insert_after_seal
    BEFORE INSERT ON planned_operations
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = NEW.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_planned_operations_no_update_after_seal
    BEFORE UPDATE ON planned_operations
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_planned_operations_no_delete_after_seal
    BEFORE DELETE ON planned_operations
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_operation_dependencies_no_insert_after_seal
    BEFORE INSERT ON operation_dependencies
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = NEW.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_operation_dependencies_no_update_after_seal
    BEFORE UPDATE ON operation_dependencies
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_operation_dependencies_no_delete_after_seal
    BEFORE DELETE ON operation_dependencies
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_seal_details_no_update
    BEFORE UPDATE ON plan_seal_details
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_seal_details_no_delete
    BEFORE DELETE ON plan_seal_details
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_operation_seal_details_no_insert
    BEFORE INSERT ON plan_operation_seal_details
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = NEW.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_operation_seal_details_no_update
    BEFORE UPDATE ON plan_operation_seal_details
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_operation_seal_details_no_delete
    BEFORE DELETE ON plan_operation_seal_details
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
)

CATALOG_RUN_START_SKELETON = (
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        command_request_id TEXT NOT NULL,
        command_receipt_id TEXT,
        trigger_occurrence_id TEXT,
        logical_run_group_id TEXT NOT NULL,
        resumed_from_run_id TEXT,
        trigger_type TEXT NOT NULL CHECK (trigger_type IN ('MANUAL_LOCAL_PREVIEW')),
        state TEXT NOT NULL CHECK (
            state IN (
                'CREATED',
                'QUEUED',
                'PREFLIGHT',
                'EXECUTING',
                'PAUSING',
                'PAUSED',
                'COMPLETED',
                'COMPLETED_WITH_WARNINGS',
                'PARTIAL_FAILURE',
                'FAILED',
                'CANCELLED',
                'BLOCKED_BY_SAFETY',
                'RECOVERY_REQUIRED'
            )
        ),
        started_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        finished_utc TEXT,
        summary_json TEXT NOT NULL,
        warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
        error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
        app_version TEXT NOT NULL,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        plan_checksum TEXT NOT NULL CHECK (length(plan_checksum) = 64),
        idempotency_key TEXT NOT NULL UNIQUE,
        planned_operations INTEGER NOT NULL CHECK (planned_operations >= 0),
        planned_bytes INTEGER NOT NULL CHECK (planned_bytes >= 0),
        UNIQUE (id, plan_id),
        UNIQUE (id, job_id, job_revision_id),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id, job_id, job_revision_id)
            REFERENCES plan_seal_details (plan_id, job_id, job_revision_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (command_receipt_id)
            REFERENCES command_receipts (idempotency_key)
            ON DELETE RESTRICT,
        FOREIGN KEY (resumed_from_run_id)
            REFERENCES runs (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_runs_state
        ON runs (state)
    """,
    """
    CREATE TABLE run_targets (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        required_owner_installation_id TEXT,
        required_ownership_epoch INTEGER,
        state TEXT NOT NULL CHECK (
            state IN (
                'PENDING',
                'ACQUIRING_LEASE',
                'REVALIDATING',
                'EXECUTING',
                'PAUSED',
                'WAITING_FOR_ENDPOINT',
                'NEEDS_REVIEW',
                'SUCCEEDED',
                'SUCCEEDED_WITH_WARNINGS',
                'FAILED',
                'CANCELLED',
                'BLOCKED',
                'RECOVERY_REQUIRED'
            )
        ),
        lease_resource_key TEXT,
        last_lease_id TEXT,
        last_ownership_epoch INTEGER,
        last_fencing_token INTEGER,
        started_utc TEXT,
        finished_utc TEXT,
        planned_operations INTEGER NOT NULL DEFAULT 0 CHECK (planned_operations >= 0),
        completed_operations INTEGER NOT NULL DEFAULT 0 CHECK (completed_operations >= 0),
        planned_bytes INTEGER NOT NULL DEFAULT 0 CHECK (planned_bytes >= 0),
        completed_bytes INTEGER NOT NULL DEFAULT 0 CHECK (completed_bytes >= 0),
        warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
        error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
        result_json TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        UNIQUE (run_id, endpoint_id),
        UNIQUE (run_id, id),
        FOREIGN KEY (run_id)
            REFERENCES runs (id)
            ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_run_targets_state
        ON run_targets (state)
    """,
)

CATALOG_RUN_STOP_REQUESTS = (
    """
    CREATE TABLE run_stop_requests (
        run_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL CHECK (mode IN ('AFTER_ACTIVE_FILE')),
        state TEXT NOT NULL CHECK (state IN ('PENDING', 'APPLIED')),
        boundary_run_target_id TEXT,
        boundary_operation_id TEXT,
        requested_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        applied_utc TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE RESTRICT,
        FOREIGN KEY (run_id, boundary_run_target_id)
            REFERENCES run_targets (run_id, id)
            ON DELETE RESTRICT,
        CHECK (
            (boundary_run_target_id IS NULL AND boundary_operation_id IS NULL)
            OR (
                boundary_run_target_id IS NOT NULL
                AND boundary_operation_id IS NOT NULL
                AND length(trim(boundary_operation_id)) > 0
            )
        ),
        CHECK (
            (state = 'PENDING' AND applied_utc IS NULL)
            OR (state = 'APPLIED' AND applied_utc IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_run_stop_requests_state
        ON run_stop_requests (state, requested_utc, run_id)
    """,
)

CATALOG_BACKUP_ANALYSIS_REQUESTS = (
    """
    DROP TRIGGER trg_initial_backup_plan_materializations_terminal_immutable
    """,
    """
    DROP TRIGGER trg_initial_backup_plan_materializations_no_delete
    """,
    """
    ALTER TABLE initial_backup_plan_materializations
        RENAME TO initial_backup_plan_materializations_v34
    """,
    """
    CREATE TABLE initial_backup_plan_materializations (
        materialization_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        analysis_id TEXT,
        plan_id TEXT UNIQUE,
        state TEXT NOT NULL CHECK (
            state IN ('SEALED', 'NO_CHANGES', 'BLOCKED', 'FAILED')
        ),
        reason_code TEXT NOT NULL CHECK (length(trim(reason_code)) > 0),
        operation_count INTEGER NOT NULL DEFAULT 0 CHECK (operation_count >= 0),
        planned_bytes INTEGER NOT NULL DEFAULT 0 CHECK (planned_bytes >= 0),
        plan_runnable INTEGER NOT NULL DEFAULT 0 CHECK (plan_runnable IN (0, 1)),
        next_action TEXT NOT NULL CHECK (length(trim(next_action)) > 0),
        started_utc TEXT NOT NULL,
        completed_utc TEXT NOT NULL,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (analysis_id)
            REFERENCES analyses (id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id)
            REFERENCES plan_seal_details (plan_id)
            ON DELETE RESTRICT,
        CHECK (
            (state = 'SEALED' AND analysis_id IS NOT NULL AND plan_id IS NOT NULL)
            OR (
                state = 'NO_CHANGES'
                AND analysis_id IS NOT NULL
                AND plan_id IS NULL
                AND operation_count = 0
                AND planned_bytes = 0
                AND plan_runnable = 0
            )
            OR (state IN ('BLOCKED', 'FAILED') AND plan_id IS NULL)
        )
    )
    """,
    """
    INSERT INTO initial_backup_plan_materializations (
        materialization_id,
        job_id,
        job_revision_id,
        analysis_id,
        plan_id,
        state,
        reason_code,
        operation_count,
        planned_bytes,
        plan_runnable,
        next_action,
        started_utc,
        completed_utc,
        row_version
    )
    SELECT
        'legacy:' || job_id || ':' || job_revision_id,
        job_id,
        job_revision_id,
        analysis_id,
        plan_id,
        state,
        reason_code,
        operation_count,
        planned_bytes,
        plan_runnable,
        next_action,
        started_utc,
        completed_utc,
        row_version
    FROM initial_backup_plan_materializations_v34
    """,
    """
    DROP TABLE initial_backup_plan_materializations_v34
    """,
    """
    CREATE INDEX idx_initial_backup_plan_materializations_state
        ON initial_backup_plan_materializations (
            state,
            completed_utc,
            job_id,
            job_revision_id
        )
    """,
    """
    CREATE INDEX idx_initial_backup_plan_materializations_job_latest
        ON initial_backup_plan_materializations (
            job_id,
            job_revision_id,
            completed_utc DESC,
            materialization_id DESC
        )
    """,
    """
    CREATE TRIGGER trg_initial_backup_plan_materializations_no_update
    BEFORE UPDATE ON initial_backup_plan_materializations
    BEGIN
        SELECT RAISE(ABORT, 'INITIAL_BACKUP_PLAN_MATERIALIZATION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_initial_backup_plan_materializations_no_delete
    BEFORE DELETE ON initial_backup_plan_materializations
    BEGIN
        SELECT RAISE(ABORT, 'INITIAL_BACKUP_PLAN_MATERIALIZATION_IMMUTABLE');
    END
    """,
    """
    CREATE TABLE backup_analysis_requests (
        request_id TEXT PRIMARY KEY,
        command_idempotency_key TEXT NOT NULL UNIQUE,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN (
                'QUEUED',
                'RUNNING',
                'SUCCEEDED',
                'NO_CHANGES',
                'BLOCKED',
                'FAILED'
            )
        ),
        requested_utc TEXT NOT NULL,
        started_utc TEXT,
        completed_utc TEXT,
        analysis_id TEXT,
        plan_id TEXT,
        reason_code TEXT,
        operation_count INTEGER NOT NULL DEFAULT 0 CHECK (operation_count >= 0),
        planned_bytes INTEGER NOT NULL DEFAULT 0 CHECK (planned_bytes >= 0),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE RESTRICT,
        FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE RESTRICT,
        CHECK (
            (state = 'QUEUED' AND started_utc IS NULL AND completed_utc IS NULL)
            OR (state = 'RUNNING' AND started_utc IS NOT NULL AND completed_utc IS NULL)
            OR (
                state IN ('SUCCEEDED', 'NO_CHANGES', 'BLOCKED', 'FAILED')
                AND started_utc IS NOT NULL
                AND completed_utc IS NOT NULL
                AND reason_code IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE INDEX idx_backup_analysis_requests_queue
        ON backup_analysis_requests (state, requested_utc, request_id)
    """,
    """
    CREATE INDEX idx_backup_analysis_requests_job
        ON backup_analysis_requests (job_id, requested_utc DESC, request_id DESC)
    """,
)

CATALOG_CURRENT_READ_HASH_EVIDENCE = (
    """
    CREATE TABLE current_read_hash_evidence (
        snapshot_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        algorithm TEXT NOT NULL CHECK (algorithm = 'BLAKE3-256'),
        hash_schema_version INTEGER NOT NULL CHECK (hash_schema_version = 1),
        evidence_kind TEXT NOT NULL CHECK (evidence_kind = 'CURRENT_READ_HASH'),
        read_started_fingerprint_hash TEXT NOT NULL CHECK (
            length(read_started_fingerprint_hash) = 64
        ),
        read_completed_fingerprint_hash TEXT NOT NULL CHECK (
            length(read_completed_fingerprint_hash) = 64
        ),
        computed_utc TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, entry_id),
        FOREIGN KEY (snapshot_id, entry_id)
            REFERENCES file_entries (snapshot_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (snapshot_id, endpoint_id)
            REFERENCES snapshots (id, endpoint_id)
            ON DELETE RESTRICT,
        CHECK (
            read_started_fingerprint_hash = read_completed_fingerprint_hash
        )
    )
    """,
    """
    CREATE INDEX idx_current_read_hash_evidence_content
        ON current_read_hash_evidence (
            content_hash,
            size_bytes,
            algorithm,
            hash_schema_version
        )
    """,
    """
    CREATE TRIGGER trg_current_read_hash_evidence_no_update
    BEFORE UPDATE ON current_read_hash_evidence
    BEGIN
        SELECT RAISE(ABORT, 'CURRENT_READ_HASH_EVIDENCE_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_current_read_hash_evidence_no_delete
    BEFORE DELETE ON current_read_hash_evidence
    BEGIN
        SELECT RAISE(ABORT, 'CURRENT_READ_HASH_EVIDENCE_IMMUTABLE');
    END
    """,
    """
    ALTER TABLE backup_analysis_requests
        ADD COLUMN start_when_safe INTEGER NOT NULL DEFAULT 0
        CHECK (start_when_safe IN (0, 1))
    """,
    """
    ALTER TABLE backup_analysis_requests
        ADD COLUMN started_run_id TEXT
        REFERENCES runs (id) ON DELETE RESTRICT
    """,
    """
    CREATE INDEX idx_backup_analysis_requests_started_run
        ON backup_analysis_requests (started_run_id)
        WHERE started_run_id IS NOT NULL
    """,
)

CATALOG_SOURCE_FILE_PRECONDITIONS = (
    """
    ALTER TABLE file_entries
        ADD COLUMN identity_fingerprint_hash TEXT
        CHECK (
            identity_fingerprint_hash IS NULL
            OR (
                length(identity_fingerprint_hash) = 64
                AND identity_fingerprint_hash NOT GLOB '*[^0-9a-f]*'
            )
        )
    """,
    """
    ALTER TABLE plan_operation_seal_details
        ADD COLUMN source_relative_path TEXT
    """,
    """
    ALTER TABLE plan_operation_seal_details
        ADD COLUMN source_precondition_json TEXT
    """,
    """
    CREATE TRIGGER trg_snapshot_v2_requires_file_identity
    BEFORE UPDATE OF immutable, snapshot_schema_version ON snapshots
    WHEN NEW.immutable = 1
        AND NEW.snapshot_schema_version >= 2
        AND EXISTS (
            SELECT 1
            FROM file_entries AS entries
            WHERE entries.snapshot_id = NEW.id
                AND entries.object_type = 'file'
                AND entries.identity_fingerprint_hash IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_V2_REQUIRES_FILE_IDENTITY');
    END
    """,
    """
    CREATE TRIGGER trg_plan_operation_v3_requires_source_precondition
    BEFORE INSERT ON plan_seal_details
    WHEN NEW.operation_schema_version >= 3
        AND EXISTS (
            SELECT 1
            FROM plan_operation_seal_details AS details
            INNER JOIN planned_operations AS operations
                ON operations.plan_id = details.plan_id
                AND operations.id = details.operation_id
            WHERE details.plan_id = NEW.plan_id
                AND operations.operation_type = 'COPY_NEW'
                AND (
                    details.source_relative_path IS NULL
                    OR details.source_precondition_json IS NULL
                )
        )
    BEGIN
        SELECT RAISE(ABORT, 'COPY_PLAN_OPERATION_REQUIRES_SOURCE_PRECONDITION');
    END
    """,
)

CATALOG_RUN_TARGET_ENDPOINT_WAITS = (
    """
    CREATE TABLE run_target_endpoint_wait_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
        reason_code TEXT NOT NULL CHECK (length(trim(reason_code)) > 0),
        observed_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (run_id, run_target_id, attempt_no),
        FOREIGN KEY (run_id, run_target_id)
            REFERENCES run_targets (run_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_run_target_endpoint_wait_events_observed
        ON run_target_endpoint_wait_events (observed_utc, id)
    """,
    """
    CREATE TRIGGER trg_run_target_endpoint_wait_events_no_update
    BEFORE UPDATE ON run_target_endpoint_wait_events
    BEGIN
        SELECT RAISE(ABORT, 'RUN_TARGET_ENDPOINT_WAIT_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_run_target_endpoint_wait_events_no_delete
    BEFORE DELETE ON run_target_endpoint_wait_events
    BEGIN
        SELECT RAISE(ABORT, 'RUN_TARGET_ENDPOINT_WAIT_EVENT_IMMUTABLE');
    END
    """,
)

CATALOG_RUN_TARGET_ENDPOINT_RETRY_TIMING = (
    """
    DROP TRIGGER trg_run_target_endpoint_wait_events_no_update
    """,
    """
    ALTER TABLE run_target_endpoint_wait_events
        ADD COLUMN backoff_ms INTEGER NOT NULL DEFAULT 5000
            CHECK (backoff_ms >= 1 AND backoff_ms <= 300000)
    """,
    """
    ALTER TABLE run_target_endpoint_wait_events
        ADD COLUMN retry_not_before_utc TEXT
    """,
    """
    UPDATE run_target_endpoint_wait_events
    SET retry_not_before_utc = strftime(
        '%Y-%m-%dT%H:%M:%fZ',
        observed_utc,
        '+5 seconds'
    )
    """,
    """
    CREATE INDEX idx_run_target_endpoint_wait_events_retry
        ON run_target_endpoint_wait_events (retry_not_before_utc, id)
    """,
    """
    CREATE TRIGGER trg_run_target_endpoint_wait_events_retry_timing_required
    BEFORE INSERT ON run_target_endpoint_wait_events
    WHEN NEW.retry_not_before_utc IS NULL
        OR length(trim(NEW.retry_not_before_utc)) = 0
        OR substr(trim(NEW.retry_not_before_utc), -1) <> 'Z'
    BEGIN
        SELECT RAISE(ABORT, 'RUN_TARGET_ENDPOINT_RETRY_TIMING_REQUIRED');
    END
    """,
    """
    CREATE TRIGGER trg_run_target_endpoint_wait_events_no_update
    BEFORE UPDATE ON run_target_endpoint_wait_events
    BEGIN
        SELECT RAISE(ABORT, 'RUN_TARGET_ENDPOINT_WAIT_EVENT_IMMUTABLE');
    END
    """,
)

CATALOG_RUN_OPERATION_AUDIT = (
    """
    CREATE TABLE run_attempts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
        process_instance_id TEXT NOT NULL CHECK (
            length(trim(process_instance_id)) > 0
        ),
        started_utc TEXT NOT NULL,
        finished_utc TEXT,
        termination_reason TEXT,
        UNIQUE (run_id, attempt_number),
        UNIQUE (run_id, process_instance_id),
        UNIQUE (id, run_id),
        FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_run_attempts_run_started
        ON run_attempts (run_id, started_utc, attempt_number)
    """,
    """
    CREATE TABLE operation_outcomes (
        run_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        final_state TEXT NOT NULL CHECK (
            final_state IN ('SUCCEEDED', 'SKIPPED', 'CANCELLED', 'RECOVERY_REQUIRED')
        ),
        bytes_transferred INTEGER NOT NULL DEFAULT 0 CHECK (bytes_transferred >= 0),
        transfer_state TEXT NOT NULL,
        assurance_level TEXT NOT NULL,
        hash_evidence_kind TEXT,
        durability_level TEXT NOT NULL,
        verification_json TEXT,
        error_code TEXT,
        error_message TEXT,
        completed_utc TEXT NOT NULL,
        PRIMARY KEY (run_id, operation_id),
        FOREIGN KEY (run_id, plan_id)
            REFERENCES runs (id, plan_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (run_id, run_target_id)
            REFERENCES run_targets (run_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id, operation_id)
            REFERENCES planned_operations (plan_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_operation_outcomes_run_target_completed
        ON operation_outcomes (run_id, run_target_id, completed_utc, operation_id)
    """,
    """
    CREATE TABLE operation_attempts (
        id TEXT PRIMARY KEY,
        run_attempt_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
        state TEXT NOT NULL CHECK (state IN ('FAILED', 'SUCCEEDED')),
        batch_id TEXT,
        lease_id TEXT,
        ownership_epoch INTEGER CHECK (
            ownership_epoch IS NULL OR ownership_epoch >= 1
        ),
        fencing_token INTEGER CHECK (fencing_token IS NULL OR fencing_token >= 1),
        source_guard_kind TEXT,
        source_guard_evidence_hash TEXT CHECK (
            source_guard_evidence_hash IS NULL
            OR length(source_guard_evidence_hash) = 64
        ),
        transfer_state TEXT,
        assurance_level TEXT,
        durability_level TEXT,
        started_utc TEXT,
        finished_utc TEXT NOT NULL,
        bytes_transferred INTEGER NOT NULL DEFAULT 0 CHECK (bytes_transferred >= 0),
        duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
        robocopy_exit_code INTEGER,
        verification_json TEXT,
        error_code TEXT,
        error_message TEXT,
        UNIQUE (run_id, operation_id, attempt_number),
        FOREIGN KEY (run_attempt_id, run_id)
            REFERENCES run_attempts (id, run_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (run_id, plan_id)
            REFERENCES runs (id, plan_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (run_id, run_target_id)
            REFERENCES run_targets (run_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id, operation_id)
            REFERENCES planned_operations (plan_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_operation_attempts_operation
        ON operation_attempts (run_id, operation_id, attempt_number)
    """,
    """
    CREATE INDEX idx_operation_attempts_run_target_finished
        ON operation_attempts (run_id, run_target_id, finished_utc, operation_id)
    """,
    """
    CREATE TRIGGER trg_operation_outcomes_no_update
    BEFORE UPDATE ON operation_outcomes
    BEGIN
        SELECT RAISE(ABORT, 'OPERATION_OUTCOME_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_operation_outcomes_no_delete
    BEFORE DELETE ON operation_outcomes
    BEGIN
        SELECT RAISE(ABORT, 'OPERATION_OUTCOME_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_operation_attempts_no_update
    BEFORE UPDATE ON operation_attempts
    BEGIN
        SELECT RAISE(ABORT, 'OPERATION_ATTEMPT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_operation_attempts_no_delete
    BEFORE DELETE ON operation_attempts
    BEGIN
        SELECT RAISE(ABORT, 'OPERATION_ATTEMPT_IMMUTABLE');
    END
    """,
)

CATALOG_PLAN_ENDPOINT_BINDINGS = (
    """
    CREATE TABLE plan_endpoints (
        plan_id TEXT NOT NULL,
        analysis_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('SOURCE', 'TARGET_WRITABLE', 'TARGET_READONLY')),
        target_ordinal INTEGER CHECK (target_ordinal IS NULL OR target_ordinal >= 0),
        capabilities_hash TEXT NOT NULL,
        root_case_context_hash TEXT NOT NULL,
        required_owner_installation_id TEXT,
        required_ownership_epoch INTEGER CHECK (
            required_ownership_epoch IS NULL OR required_ownership_epoch >= 1
        ),
        control_schema_version INTEGER CHECK (
            control_schema_version IS NULL OR control_schema_version >= 1
        ),
        planned_operations INTEGER NOT NULL DEFAULT 0 CHECK (planned_operations >= 0),
        planned_bytes INTEGER NOT NULL DEFAULT 0 CHECK (planned_bytes >= 0),
        PRIMARY KEY (plan_id, endpoint_id, role),
        UNIQUE (plan_id, endpoint_id),
        UNIQUE (plan_id, snapshot_id),
        FOREIGN KEY (plan_id)
            REFERENCES plans (id)
            ON DELETE RESTRICT,
        FOREIGN KEY (analysis_id, endpoint_id)
            REFERENCES analysis_targets (analysis_id, endpoint_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (snapshot_id, endpoint_id)
            REFERENCES snapshots (id, endpoint_id)
            ON DELETE RESTRICT,
        CHECK (
            role <> 'TARGET_WRITABLE'
            OR (
                target_ordinal IS NOT NULL
                AND required_owner_installation_id IS NOT NULL
                AND required_ownership_epoch IS NOT NULL
                AND control_schema_version IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE INDEX idx_plan_endpoints_read_page
        ON plan_endpoints (plan_id, role, target_ordinal, endpoint_id)
    """,
    """
    CREATE TRIGGER trg_plan_endpoints_no_insert_after_seal
    BEFORE INSERT ON plan_endpoints
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = NEW.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_endpoints_no_update_after_seal
    BEFORE UPDATE ON plan_endpoints
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_endpoints_no_delete_after_seal
    BEFORE DELETE ON plan_endpoints
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
)

CATALOG_TRANSACTIONAL_OUTBOX_SKELETON = (
    """
    CREATE TABLE outbox_messages (
        id TEXT PRIMARY KEY,
        message_type TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        state TEXT NOT NULL CHECK (state IN ('PENDING', 'CLAIMED', 'DELIVERED', 'DEAD_LETTER')),
        available_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        next_attempt_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        claim_owner_instance_id TEXT,
        claim_generation INTEGER NOT NULL DEFAULT 0 CHECK (claim_generation >= 0),
        claim_token TEXT,
        claim_started_utc TEXT,
        claim_ttl_ms INTEGER CHECK (claim_ttl_ms IS NULL OR claim_ttl_ms > 0),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        last_attempt_utc TEXT,
        delivered_utc TEXT,
        terminal_effect_hash TEXT CHECK (
            terminal_effect_hash IS NULL OR length(terminal_effect_hash) = 64
        ),
        last_error_code TEXT,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        CHECK (
            state <> 'CLAIMED'
            OR (
                claim_owner_instance_id IS NOT NULL
                AND claim_token IS NOT NULL
                AND claim_started_utc IS NOT NULL
            )
        ),
        CHECK (
            state <> 'DELIVERED'
            OR (
                delivered_utc IS NOT NULL
                AND terminal_effect_hash IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE INDEX idx_outbox_messages_state_next_attempt
        ON outbox_messages (state, next_attempt_utc)
    """,
    """
    CREATE TABLE effect_dedup_tombstones (
        deduplication_key TEXT PRIMARY KEY,
        effect_kind TEXT NOT NULL CHECK (effect_kind IN ('outbox', 'trigger')),
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        terminal_state TEXT NOT NULL,
        effect_entity_type TEXT,
        effect_entity_id TEXT,
        terminal_effect_hash TEXT CHECK (
            terminal_effect_hash IS NULL OR length(terminal_effect_hash) = 64
        ),
        first_seen_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        compacted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
)

CATALOG_TRIGGER_OCCURRENCE_DEDUP = (
    """
    CREATE TABLE trigger_occurrences (
        id TEXT PRIMARY KEY,
        schedule_id TEXT NOT NULL,
        schedule_revision_hash TEXT NOT NULL CHECK (length(schedule_revision_hash) = 64),
        job_id TEXT NOT NULL,
        occurrence_key TEXT NOT NULL,
        deduplication_key TEXT NOT NULL UNIQUE CHECK (length(deduplication_key) = 64),
        first_delivery_id TEXT NOT NULL,
        occurrence_slot_utc TEXT,
        source_instance_key TEXT,
        trigger_type TEXT NOT NULL CHECK (
            trigger_type IN (
                'SCHEDULED_TIME',
                'LOGON',
                'STARTUP',
                'EVENT',
                'VOLUME_CONNECTED',
                'MANUAL_LOCAL_PREVIEW'
            )
        ),
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        received_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        state TEXT NOT NULL CHECK (
            state IN (
                'RECEIVED',
                'WAITING_FOR_RECOVERY',
                'RUN_ENQUEUED',
                'SUCCEEDED',
                'REJECTED',
                'FAILED',
                'CANCELLED'
            )
        ),
        run_id TEXT,
        terminal_effect_hash TEXT CHECK (
            terminal_effect_hash IS NULL OR length(terminal_effect_hash) = 64
        ),
        completed_utc TEXT,
        FOREIGN KEY (job_id)
            REFERENCES jobs (id)
            ON DELETE RESTRICT,
        FOREIGN KEY (run_id)
            REFERENCES runs (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_trigger_occurrences_job_received
        ON trigger_occurrences (job_id, received_utc)
    """,
)

CATALOG_SCHEDULE_DESIRED_STATE = (
    """
    CREATE TABLE schedules (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_checksum TEXT NOT NULL CHECK (length(plan_checksum) = 64),
        trigger_type TEXT NOT NULL CHECK (
            trigger_type IN (
                'SCHEDULED_TIME',
                'LOGON',
                'STARTUP',
                'EVENT',
                'VOLUME_CONNECTED',
                'MANUAL_LOCAL_PREVIEW'
            )
        ),
        configuration_json TEXT NOT NULL,
        definition_generation INTEGER NOT NULL CHECK (definition_generation >= 1),
        desired_definition_hash TEXT NOT NULL CHECK (length(desired_definition_hash) = 64),
        time_zone_id TEXT,
        dst_policy TEXT NOT NULL,
        misfire_policy TEXT NOT NULL,
        coalescing_window_seconds INTEGER NOT NULL CHECK (coalescing_window_seconds >= 0),
        task_logon_type TEXT NOT NULL,
        requires_network INTEGER NOT NULL CHECK (requires_network IN (0, 1)),
        run_only_when_logged_on INTEGER NOT NULL CHECK (run_only_when_logged_on IN (0, 1)),
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        last_triggered_utc TEXT,
        FOREIGN KEY (job_id)
            REFERENCES jobs (id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id)
            REFERENCES plan_seal_details (plan_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_schedules_job_enabled
        ON schedules (job_id, enabled)
    """,
)

CATALOG_EXTERNAL_RESOURCE_STATE = (
    """
    CREATE TABLE external_resource_state (
        resource_type TEXT NOT NULL CHECK (
            resource_type IN ('task_scheduler', 'notification_channel', 'control_marker')
        ),
        resource_id TEXT NOT NULL,
        desired_generation INTEGER NOT NULL CHECK (desired_generation >= 1),
        desired_hash TEXT NOT NULL CHECK (length(desired_hash) = 64),
        observed_generation INTEGER CHECK (
            observed_generation IS NULL OR observed_generation >= 1
        ),
        observed_hash TEXT CHECK (observed_hash IS NULL OR length(observed_hash) = 64),
        state TEXT NOT NULL CHECK (state IN ('PENDING', 'CLAIMED', 'IN_SYNC', 'BLOCKED')),
        claim_owner_instance_id TEXT,
        claim_generation INTEGER NOT NULL DEFAULT 0 CHECK (claim_generation >= 0),
        claim_token TEXT,
        claim_started_utc TEXT,
        claim_ttl_ms INTEGER CHECK (claim_ttl_ms IS NULL OR claim_ttl_ms > 0),
        last_attempt_utc TEXT,
        last_success_utc TEXT,
        last_error_code TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        PRIMARY KEY (resource_type, resource_id),
        CHECK (
            state <> 'CLAIMED'
            OR (
                claim_owner_instance_id IS NOT NULL
                AND claim_token IS NOT NULL
                AND claim_started_utc IS NOT NULL
            )
        ),
        CHECK (
            state <> 'IN_SYNC'
            OR (
                observed_generation = desired_generation
                AND observed_hash = desired_hash
                AND last_success_utc IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE INDEX idx_external_resource_state_type_state_id
        ON external_resource_state (resource_type, state, resource_id)
    """,
)

CATALOG_ENDPOINT_REVISION_IDENTITY = (
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN control_area_id TEXT
    """,
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN root_identity_hash_algorithm TEXT
    """,
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN root_identity_hash TEXT CHECK (
            root_identity_hash IS NULL OR length(root_identity_hash) = 64
        )
    """,
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN owner_installation_id TEXT
    """,
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN ownership_epoch INTEGER CHECK (
            ownership_epoch IS NULL OR ownership_epoch >= 1
        )
    """,
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN control_marker_checksum_algorithm TEXT
    """,
    """
    ALTER TABLE endpoint_revisions
        ADD COLUMN control_marker_checksum TEXT CHECK (
            control_marker_checksum IS NULL OR length(control_marker_checksum) = 64
        )
    """,
)

CATALOG_STANDARD_BACKUP_JOB_ENDPOINT_BINDINGS = (
    """
    CREATE TABLE endpoint_root_claims (
        canonical_root_key TEXT PRIMARY KEY,
        endpoint_id TEXT NOT NULL UNIQUE,
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (endpoint_id)
            REFERENCES endpoints (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE standard_backup_job_endpoint_bindings (
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('SOURCE', 'TARGET')),
        ordinal INTEGER NOT NULL CHECK (
            (role = 'SOURCE' AND ordinal = 0)
            OR (role = 'TARGET' AND ordinal >= 1)
        ),
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        registration_state TEXT NOT NULL CHECK (
            registration_state IN (
                'REGISTRATION_PENDING',
                'READ_ONLY_READY',
                'WRITABLE_READY',
                'BLOCKED'
            )
        ),
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (job_id, job_revision_id, role, ordinal),
        UNIQUE (job_id, job_revision_id, endpoint_id),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_standard_backup_job_endpoint_bindings_endpoint
        ON standard_backup_job_endpoint_bindings (endpoint_id, endpoint_revision_id)
    """,
)

CATALOG_INSTALLATION_STATE = (
    """
    CREATE TABLE installation_state (
        installation_id TEXT PRIMARY KEY,
        singleton_guard INTEGER NOT NULL DEFAULT 1 UNIQUE CHECK (singleton_guard = 1),
        product_channel TEXT NOT NULL CHECK (length(trim(product_channel)) > 0),
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        last_started_app_version TEXT NOT NULL CHECK (
            length(trim(last_started_app_version)) > 0
        ),
        catalog_schema_version INTEGER NOT NULL CHECK (catalog_schema_version >= 1),
        recovery_schema_version INTEGER NOT NULL CHECK (recovery_schema_version >= 1),
        ipc_protocol_major INTEGER NOT NULL CHECK (ipc_protocol_major >= 1),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
    )
    """,
    """
    CREATE TRIGGER trg_installation_state_identity_immutable
    BEFORE UPDATE ON installation_state
    WHEN
        NEW.installation_id IS NOT OLD.installation_id
        OR NEW.singleton_guard IS NOT OLD.singleton_guard
        OR NEW.product_channel IS NOT OLD.product_channel
        OR NEW.created_utc IS NOT OLD.created_utc
    BEGIN
        SELECT RAISE(ABORT, 'INSTALLATION_IDENTITY_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_installation_state_no_delete
    BEFORE DELETE ON installation_state
    BEGIN
        SELECT RAISE(ABORT, 'INSTALLATION_STATE_DELETE_FORBIDDEN');
    END
    """,
)

CATALOG_ENDPOINT_CLASSIFICATION_OBSERVATIONS = (
    """
    ALTER TABLE standard_backup_job_endpoint_bindings
        ADD COLUMN registration_reason_code TEXT NOT NULL
            DEFAULT 'ENDPOINT_CLASSIFICATION_PENDING'
            CHECK (length(trim(registration_reason_code)) > 0)
    """,
    """
    CREATE TABLE endpoint_classification_observations (
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        local_installation_id TEXT NOT NULL CHECK (
            length(trim(local_installation_id)) > 0
        ),
        inspection_status TEXT NOT NULL CHECK (
            inspection_status IN ('CLASSIFIED', 'FAILED')
        ),
        classification_state TEXT CHECK (
            classification_state IS NULL
            OR classification_state IN (
                'ABSENT',
                'VALID_OWNED',
                'VALID_FOREIGN',
                'VALID_READ_ONLY_NEWER_SCHEMA',
                'PARTIAL_CONTROL_AREA',
                'UNKNOWN_EMPTY_DIRECTORY',
                'UNKNOWN_NONEMPTY_DIRECTORY',
                'CASE_ALIAS_COLLISION',
                'CORRUPT_MARKER'
            )
        ),
        reason_codes_json TEXT NOT NULL,
        marker_json TEXT,
        error_code TEXT,
        next_action TEXT,
        observed_utc TEXT NOT NULL CHECK (length(trim(observed_utc)) > 0),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        PRIMARY KEY (endpoint_id, endpoint_revision_id),
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT,
        CHECK (
            (
                inspection_status = 'CLASSIFIED'
                AND classification_state IS NOT NULL
                AND error_code IS NULL
                AND next_action IS NULL
            )
            OR (
                inspection_status = 'FAILED'
                AND classification_state IS NULL
                AND marker_json IS NULL
                AND length(trim(error_code)) > 0
                AND length(trim(next_action)) > 0
            )
        )
    )
    """,
    """
    CREATE INDEX idx_endpoint_classification_observations_status
        ON endpoint_classification_observations (
            inspection_status,
            classification_state,
            endpoint_id
        )
    """,
)

CATALOG_STANDARD_BACKUP_JOB_SNAPSHOT_MATERIALIZATIONS = (
    """
    CREATE TABLE standard_backup_job_snapshot_materializations (
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        analysis_id TEXT,
        state TEXT NOT NULL CHECK (state IN ('SEALED', 'BLOCKED')),
        reason_code TEXT NOT NULL CHECK (length(trim(reason_code)) > 0),
        snapshot_count INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_count >= 0),
        sealed_snapshot_count INTEGER NOT NULL DEFAULT 0 CHECK (
            sealed_snapshot_count >= 0
            AND sealed_snapshot_count <= snapshot_count
        ),
        started_utc TEXT NOT NULL CHECK (length(trim(started_utc)) > 0),
        completed_utc TEXT NOT NULL CHECK (length(trim(completed_utc)) > 0),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        PRIMARY KEY (job_id, job_revision_id),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (analysis_id)
            REFERENCES analyses (id)
            ON DELETE RESTRICT,
        CHECK (
            (
                state = 'SEALED'
                AND analysis_id IS NOT NULL
                AND snapshot_count >= 1
                AND sealed_snapshot_count = snapshot_count
            )
            OR (
                state = 'BLOCKED'
                AND sealed_snapshot_count = 0
            )
        )
    )
    """,
    """
    CREATE INDEX idx_standard_backup_job_snapshot_materializations_state
        ON standard_backup_job_snapshot_materializations (
            state,
            completed_utc,
            job_id
        )
    """,
)

CATALOG_IMMUTABLE_REVISION_GUARDS = (
    """
    CREATE TRIGGER trg_endpoint_revisions_no_update
    BEFORE UPDATE ON endpoint_revisions
    BEGIN
        SELECT RAISE(ABORT, 'ENDPOINT_REVISION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_endpoint_revisions_no_delete
    BEFORE DELETE ON endpoint_revisions
    BEGIN
        SELECT RAISE(ABORT, 'ENDPOINT_REVISION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_job_revisions_no_update
    BEFORE UPDATE ON job_revisions
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_job_revisions_no_delete
    BEFORE DELETE ON job_revisions
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_filter_sets_no_update_after_use
    BEFORE UPDATE ON filter_sets
    WHEN EXISTS (
        SELECT 1
        FROM job_revisions
        WHERE job_id = OLD.job_id
            AND filter_set_id = OLD.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'FILTER_SET_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_filter_sets_no_delete_after_use
    BEFORE DELETE ON filter_sets
    WHEN EXISTS (
        SELECT 1
        FROM job_revisions
        WHERE job_id = OLD.job_id
            AND filter_set_id = OLD.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'FILTER_SET_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_standard_backup_job_revision_details_no_update
    BEFORE UPDATE ON standard_backup_job_revision_details
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_standard_backup_job_revision_details_no_delete
    BEFORE DELETE ON standard_backup_job_revision_details
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_standard_backup_job_endpoint_bindings_identity_immutable
    BEFORE UPDATE ON standard_backup_job_endpoint_bindings
    WHEN
        NEW.job_id IS NOT OLD.job_id
        OR NEW.job_revision_id IS NOT OLD.job_revision_id
        OR NEW.role IS NOT OLD.role
        OR NEW.ordinal IS NOT OLD.ordinal
        OR NEW.endpoint_id IS NOT OLD.endpoint_id
        OR NEW.endpoint_revision_id IS NOT OLD.endpoint_revision_id
        OR NEW.created_utc IS NOT OLD.created_utc
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_BINDING_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_standard_backup_job_endpoint_bindings_no_delete
    BEFORE DELETE ON standard_backup_job_endpoint_bindings
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_BINDING_IMMUTABLE');
    END
    """,
)

CATALOG_FILTER_SET_VERSIONS = (
    """
    ALTER TABLE job_revisions
    ADD COLUMN filter_set_version INTEGER NOT NULL DEFAULT 1
        CHECK (filter_set_version >= 1)
    """,
    """
    CREATE TABLE filter_set_versions (
        job_id TEXT NOT NULL,
        filter_set_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version >= 1),
        rules_hash TEXT NOT NULL
            CHECK (
                length(rules_hash) = 64
                AND rules_hash = lower(rules_hash)
                AND rules_hash NOT GLOB '*[^0-9a-f]*'
            ),
        rules_json TEXT NOT NULL CHECK (json_valid(rules_json)),
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (job_id, filter_set_id, version),
        FOREIGN KEY (job_id, filter_set_id)
            REFERENCES filter_sets (job_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    INSERT INTO filter_set_versions (
        job_id,
        filter_set_id,
        version,
        rules_hash,
        rules_json
    )
    SELECT
        job_id,
        id,
        1,
        '5b551f66adfe79a9e025a369c44e76ece00928588f965a93fe6cdcfbdb1e4a9b',
        '{"preset":"ALL_USER_FILES","schema_version":1}'
    FROM filter_sets
    """,
    """
    CREATE TABLE job_revision_filter_bindings (
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        filter_set_id TEXT NOT NULL,
        filter_set_version INTEGER NOT NULL CHECK (filter_set_version >= 1),
        PRIMARY KEY (job_id, job_revision_id),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (job_id, filter_set_id, filter_set_version)
            REFERENCES filter_set_versions (job_id, filter_set_id, version)
            ON DELETE RESTRICT
    )
    """,
    """
    INSERT INTO job_revision_filter_bindings (
        job_id,
        job_revision_id,
        filter_set_id,
        filter_set_version
    )
    SELECT
        job_id,
        id,
        filter_set_id,
        filter_set_version
    FROM job_revisions
    """,
    """
    CREATE TRIGGER trg_job_revisions_filter_version_required
    BEFORE INSERT ON job_revisions
    WHEN NOT EXISTS (
        SELECT 1
        FROM filter_set_versions
        WHERE job_id = NEW.job_id
            AND filter_set_id = NEW.filter_set_id
            AND version = NEW.filter_set_version
    )
    BEGIN
        SELECT RAISE(ABORT, 'FILTER_SET_VERSION_NOT_FOUND');
    END
    """,
    """
    CREATE TRIGGER trg_job_revisions_bind_filter_version
    AFTER INSERT ON job_revisions
    BEGIN
        INSERT INTO job_revision_filter_bindings (
            job_id,
            job_revision_id,
            filter_set_id,
            filter_set_version
        )
        VALUES (
            NEW.job_id,
            NEW.id,
            NEW.filter_set_id,
            NEW.filter_set_version
        );
    END
    """,
    """
    CREATE TRIGGER trg_filter_set_versions_no_update
    BEFORE UPDATE ON filter_set_versions
    BEGIN
        SELECT RAISE(ABORT, 'FILTER_SET_VERSION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_filter_set_versions_no_delete
    BEFORE DELETE ON filter_set_versions
    BEGIN
        SELECT RAISE(ABORT, 'FILTER_SET_VERSION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_job_revision_filter_bindings_no_update
    BEFORE UPDATE ON job_revision_filter_bindings
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_FILTER_BINDING_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_job_revision_filter_bindings_no_delete
    BEFORE DELETE ON job_revision_filter_bindings
    BEGIN
        SELECT RAISE(ABORT, 'JOB_REVISION_FILTER_BINDING_IMMUTABLE');
    END
    """,
)

CATALOG_ENDPOINT_GENERATION_BINDINGS = (
    """
    DROP TRIGGER trg_endpoint_revisions_no_update
    """,
    """
    ALTER TABLE endpoint_revisions
    ADD COLUMN generation INTEGER NOT NULL DEFAULT 1
        CHECK (generation >= 1)
    """,
    """
    UPDATE endpoint_revisions AS current
    SET generation = (
        SELECT COUNT(*)
        FROM endpoint_revisions AS prior
        WHERE prior.endpoint_id = current.endpoint_id
            AND (
                prior.created_utc < current.created_utc
                OR (
                    prior.created_utc = current.created_utc
                    AND prior.id <= current.id
                )
            )
    )
    """,
    """
    CREATE UNIQUE INDEX uq_endpoint_revisions_endpoint_generation
        ON endpoint_revisions (endpoint_id, generation)
    """,
    """
    CREATE TRIGGER trg_endpoint_revisions_generation_must_advance
    BEFORE INSERT ON endpoint_revisions
    WHEN NEW.generation <> COALESCE(
        (
            SELECT MAX(generation) + 1
            FROM endpoint_revisions
            WHERE endpoint_id = NEW.endpoint_id
        ),
        1
    )
    BEGIN
        SELECT RAISE(ABORT, 'ENDPOINT_GENERATION_MUST_ADVANCE');
    END
    """,
    """
    CREATE TRIGGER trg_endpoint_revisions_no_update
    BEFORE UPDATE ON endpoint_revisions
    BEGIN
        SELECT RAISE(ABORT, 'ENDPOINT_REVISION_IMMUTABLE');
    END
    """,
    """
    DROP TRIGGER trg_snapshots_no_update_after_immutable
    """,
    """
    ALTER TABLE snapshots
    ADD COLUMN endpoint_generation INTEGER NOT NULL DEFAULT 1
        CHECK (endpoint_generation >= 1)
    """,
    """
    UPDATE snapshots
    SET endpoint_generation = (
        SELECT revisions.generation
        FROM endpoint_revisions AS revisions
        WHERE revisions.endpoint_id = snapshots.endpoint_id
            AND revisions.id = snapshots.endpoint_revision_id
    )
    """,
    """
    CREATE TRIGGER trg_snapshots_endpoint_generation_required
    BEFORE INSERT ON snapshots
    WHEN NOT EXISTS (
        SELECT 1
        FROM endpoint_revisions
        WHERE endpoint_id = NEW.endpoint_id
            AND id = NEW.endpoint_revision_id
            AND generation = NEW.endpoint_generation
    )
    BEGIN
        SELECT RAISE(ABORT, 'ENDPOINT_GENERATION_MISMATCH');
    END
    """,
    """
    CREATE TRIGGER trg_snapshots_endpoint_identity_immutable
    BEFORE UPDATE ON snapshots
    WHEN
        NEW.endpoint_id IS NOT OLD.endpoint_id
        OR NEW.endpoint_revision_id IS NOT OLD.endpoint_revision_id
        OR NEW.endpoint_generation IS NOT OLD.endpoint_generation
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_ENDPOINT_IDENTITY_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshots_no_update_after_immutable
    BEFORE UPDATE ON snapshots
    WHEN OLD.immutable = 1
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    DROP TRIGGER trg_plan_endpoints_no_update_after_seal
    """,
    """
    ALTER TABLE plan_endpoints
    ADD COLUMN endpoint_generation INTEGER NOT NULL DEFAULT 1
        CHECK (endpoint_generation >= 1)
    """,
    """
    UPDATE plan_endpoints
    SET endpoint_generation = (
        SELECT revisions.generation
        FROM endpoint_revisions AS revisions
        WHERE revisions.endpoint_id = plan_endpoints.endpoint_id
            AND revisions.id = plan_endpoints.endpoint_revision_id
    )
    """,
    """
    CREATE TRIGGER trg_plan_endpoints_endpoint_generation_required
    BEFORE INSERT ON plan_endpoints
    WHEN
        NOT EXISTS (
            SELECT 1
            FROM endpoint_revisions
            WHERE endpoint_id = NEW.endpoint_id
                AND id = NEW.endpoint_revision_id
                AND generation = NEW.endpoint_generation
        )
        OR NOT EXISTS (
            SELECT 1
            FROM snapshots
            WHERE id = NEW.snapshot_id
                AND endpoint_id = NEW.endpoint_id
                AND endpoint_generation = NEW.endpoint_generation
        )
    BEGIN
        SELECT RAISE(ABORT, 'ENDPOINT_GENERATION_MISMATCH');
    END
    """,
    """
    CREATE TRIGGER trg_plan_endpoints_endpoint_identity_immutable
    BEFORE UPDATE ON plan_endpoints
    WHEN
        NEW.endpoint_id IS NOT OLD.endpoint_id
        OR NEW.endpoint_revision_id IS NOT OLD.endpoint_revision_id
        OR NEW.endpoint_generation IS NOT OLD.endpoint_generation
        OR NEW.snapshot_id IS NOT OLD.snapshot_id
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_ENDPOINT_IDENTITY_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_endpoints_no_update_after_seal
    BEFORE UPDATE ON plan_endpoints
    WHEN EXISTS (SELECT 1 FROM plan_seal_details WHERE plan_id = OLD.plan_id)
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
)


CATALOG_WRITABLE_ENDPOINT_REGISTRATIONS = (
    """
    CREATE TABLE writable_endpoint_registration_intents (
        intent_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        source_job_revision_id TEXT NOT NULL,
        resulting_job_revision_id TEXT NOT NULL UNIQUE,
        command_request_id TEXT NOT NULL,
        command_idempotency_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN ('PREPARED', 'FILESYSTEM_APPLIED', 'COMMITTED', 'BLOCKED')
        ),
        prepared_targets_json TEXT NOT NULL,
        last_error_code TEXT,
        last_next_action TEXT,
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        committed_utc TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        UNIQUE (job_id, source_job_revision_id),
        FOREIGN KEY (job_id, source_job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        CHECK (
            (state = 'COMMITTED' AND committed_utc IS NOT NULL)
            OR (state <> 'COMMITTED' AND committed_utc IS NULL)
        ),
        CHECK (
            (last_error_code IS NULL AND last_next_action IS NULL)
            OR (last_error_code IS NOT NULL AND last_next_action IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_writable_endpoint_registration_intents_recovery
        ON writable_endpoint_registration_intents (state, created_utc, intent_id)
    """,
    """
    CREATE TABLE writable_endpoint_registrations (
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        endpoint_generation INTEGER NOT NULL CHECK (endpoint_generation >= 1),
        intent_id TEXT NOT NULL,
        control_area_id TEXT NOT NULL,
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        root_identity_hash_algorithm TEXT NOT NULL,
        root_identity_hash TEXT NOT NULL CHECK (length(root_identity_hash) = 64),
        marker_checksum_algorithm TEXT NOT NULL,
        marker_checksum TEXT NOT NULL CHECK (length(marker_checksum) = 64),
        probe_completed_utc TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        PRIMARY KEY (endpoint_id, endpoint_revision_id),
        UNIQUE (intent_id, endpoint_id),
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (intent_id)
            REFERENCES writable_endpoint_registration_intents (intent_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_registration_intents_identity_immutable
    BEFORE UPDATE ON writable_endpoint_registration_intents
    WHEN
        NEW.intent_id IS NOT OLD.intent_id
        OR NEW.job_id IS NOT OLD.job_id
        OR NEW.source_job_revision_id IS NOT OLD.source_job_revision_id
        OR NEW.resulting_job_revision_id IS NOT OLD.resulting_job_revision_id
        OR NEW.command_request_id IS NOT OLD.command_request_id
        OR NEW.command_idempotency_key IS NOT OLD.command_idempotency_key
        OR NEW.prepared_targets_json IS NOT OLD.prepared_targets_json
        OR NEW.created_utc IS NOT OLD.created_utc
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_REGISTRATION_INTENT_IDENTITY_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_registration_intents_transition
    BEFORE UPDATE OF state ON writable_endpoint_registration_intents
    WHEN NOT (
        NEW.state = OLD.state
        OR (OLD.state = 'PREPARED' AND NEW.state IN ('FILESYSTEM_APPLIED', 'BLOCKED'))
        OR (OLD.state = 'FILESYSTEM_APPLIED' AND NEW.state IN ('COMMITTED', 'BLOCKED'))
    )
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_REGISTRATION_TRANSITION_INVALID');
    END
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_registration_intents_no_delete
    BEFORE DELETE ON writable_endpoint_registration_intents
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_REGISTRATION_INTENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_registrations_no_update
    BEFORE UPDATE ON writable_endpoint_registrations
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_REGISTRATION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_registrations_no_delete
    BEFORE DELETE ON writable_endpoint_registrations
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_REGISTRATION_IMMUTABLE');
    END
    """,
)


CATALOG_CONTROLLED_ENDPOINT_TAKEOVERS = (
    """
    CREATE TABLE controlled_endpoint_takeover_intents (
        intent_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        source_job_revision_id TEXT NOT NULL,
        resulting_job_revision_id TEXT NOT NULL UNIQUE,
        analysis_request_id TEXT NOT NULL UNIQUE,
        target_ordinal INTEGER NOT NULL CHECK (target_ordinal BETWEEN 1 AND 3),
        endpoint_id TEXT NOT NULL,
        source_endpoint_revision_id TEXT NOT NULL,
        resulting_endpoint_revision_id TEXT NOT NULL UNIQUE,
        foreign_owner_installation_id TEXT NOT NULL,
        foreign_ownership_epoch INTEGER NOT NULL CHECK (foreign_ownership_epoch >= 1),
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (
            ownership_epoch = foreign_ownership_epoch + 1
        ),
        command_request_id TEXT NOT NULL,
        command_idempotency_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK (
            state IN ('PREPARED', 'FILESYSTEM_APPLIED', 'COMMITTED', 'BLOCKED')
        ),
        prepared_takeover_json TEXT NOT NULL,
        last_error_code TEXT,
        last_next_action TEXT,
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        committed_utc TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        UNIQUE (job_id, source_job_revision_id, target_ordinal),
        UNIQUE (endpoint_id, source_endpoint_revision_id),
        FOREIGN KEY (job_id, source_job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, source_endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT,
        CHECK (
            (state = 'COMMITTED' AND committed_utc IS NOT NULL)
            OR (state <> 'COMMITTED' AND committed_utc IS NULL)
        ),
        CHECK (
            (last_error_code IS NULL AND last_next_action IS NULL)
            OR (last_error_code IS NOT NULL AND last_next_action IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_controlled_endpoint_takeover_intents_recovery
        ON controlled_endpoint_takeover_intents (state, created_utc, intent_id)
    """,
    """
    CREATE TABLE controlled_endpoint_takeovers (
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        endpoint_generation INTEGER NOT NULL CHECK (endpoint_generation >= 2),
        intent_id TEXT NOT NULL UNIQUE,
        control_area_id TEXT NOT NULL,
        previous_owner_installation_id TEXT NOT NULL,
        previous_ownership_epoch INTEGER NOT NULL CHECK (previous_ownership_epoch >= 1),
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (
            ownership_epoch = previous_ownership_epoch + 1
        ),
        root_identity_hash_algorithm TEXT NOT NULL,
        root_identity_hash TEXT NOT NULL CHECK (length(root_identity_hash) = 64),
        marker_checksum_algorithm TEXT NOT NULL,
        marker_checksum TEXT NOT NULL CHECK (length(marker_checksum) = 64),
        takeover_record_path TEXT NOT NULL,
        ownership_record_path TEXT NOT NULL,
        probe_completed_utc TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        PRIMARY KEY (endpoint_id, endpoint_revision_id),
        FOREIGN KEY (endpoint_id, endpoint_revision_id)
            REFERENCES endpoint_revisions (endpoint_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (intent_id)
            REFERENCES controlled_endpoint_takeover_intents (intent_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER trg_controlled_endpoint_takeover_intents_identity_immutable
    BEFORE UPDATE ON controlled_endpoint_takeover_intents
    WHEN
        NEW.intent_id IS NOT OLD.intent_id
        OR NEW.job_id IS NOT OLD.job_id
        OR NEW.source_job_revision_id IS NOT OLD.source_job_revision_id
        OR NEW.resulting_job_revision_id IS NOT OLD.resulting_job_revision_id
        OR NEW.analysis_request_id IS NOT OLD.analysis_request_id
        OR NEW.target_ordinal IS NOT OLD.target_ordinal
        OR NEW.endpoint_id IS NOT OLD.endpoint_id
        OR NEW.source_endpoint_revision_id IS NOT OLD.source_endpoint_revision_id
        OR NEW.resulting_endpoint_revision_id IS NOT OLD.resulting_endpoint_revision_id
        OR NEW.foreign_owner_installation_id IS NOT OLD.foreign_owner_installation_id
        OR NEW.foreign_ownership_epoch IS NOT OLD.foreign_ownership_epoch
        OR NEW.owner_installation_id IS NOT OLD.owner_installation_id
        OR NEW.ownership_epoch IS NOT OLD.ownership_epoch
        OR NEW.command_request_id IS NOT OLD.command_request_id
        OR NEW.command_idempotency_key IS NOT OLD.command_idempotency_key
        OR NEW.prepared_takeover_json IS NOT OLD.prepared_takeover_json
        OR NEW.created_utc IS NOT OLD.created_utc
    BEGIN
        SELECT RAISE(ABORT, 'CONTROLLED_ENDPOINT_TAKEOVER_INTENT_IDENTITY_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_controlled_endpoint_takeover_intents_transition
    BEFORE UPDATE OF state ON controlled_endpoint_takeover_intents
    WHEN NOT (
        NEW.state = OLD.state
        OR (OLD.state = 'PREPARED' AND NEW.state IN ('FILESYSTEM_APPLIED', 'BLOCKED'))
        OR (OLD.state = 'FILESYSTEM_APPLIED' AND NEW.state IN ('COMMITTED', 'BLOCKED'))
    )
    BEGIN
        SELECT RAISE(ABORT, 'CONTROLLED_ENDPOINT_TAKEOVER_TRANSITION_INVALID');
    END
    """,
    """
    CREATE TRIGGER trg_controlled_endpoint_takeover_intents_no_delete
    BEFORE DELETE ON controlled_endpoint_takeover_intents
    BEGIN
        SELECT RAISE(ABORT, 'CONTROLLED_ENDPOINT_TAKEOVER_INTENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_controlled_endpoint_takeovers_no_update
    BEFORE UPDATE ON controlled_endpoint_takeovers
    BEGIN
        SELECT RAISE(ABORT, 'CONTROLLED_ENDPOINT_TAKEOVER_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_controlled_endpoint_takeovers_no_delete
    BEFORE DELETE ON controlled_endpoint_takeovers
    BEGIN
        SELECT RAISE(ABORT, 'CONTROLLED_ENDPOINT_TAKEOVER_IMMUTABLE');
    END
    """,
)


CATALOG_JOB_LIFECYCLE = (
    """
    ALTER TABLE jobs
        ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (lifecycle_state IN ('ACTIVE', 'ARCHIVED'))
    """,
    """
    ALTER TABLE jobs
        ADD COLUMN archived_utc TEXT
    """,
    """
    ALTER TABLE jobs
        ADD COLUMN lifecycle_row_version INTEGER NOT NULL DEFAULT 1
        CHECK (lifecycle_row_version >= 1)
    """,
    """
    CREATE TABLE job_lifecycle_events (
        event_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        command_request_id TEXT NOT NULL,
        command_idempotency_key TEXT NOT NULL UNIQUE,
        transition_kind TEXT NOT NULL CHECK (
            transition_kind IN ('ARCHIVE', 'REACTIVATE')
        ),
        previous_state TEXT NOT NULL CHECK (
            previous_state IN ('ACTIVE', 'ARCHIVED')
        ),
        next_state TEXT NOT NULL CHECK (
            next_state IN ('ACTIVE', 'ARCHIVED')
        ),
        occurred_utc TEXT NOT NULL,
        lifecycle_row_version INTEGER NOT NULL CHECK (lifecycle_row_version >= 2),
        disabled_schedule_count INTEGER NOT NULL DEFAULT 0
            CHECK (disabled_schedule_count >= 0),
        analysis_request_id TEXT,
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_jobs_lifecycle_state_id
        ON jobs (lifecycle_state, id)
    """,
    """
    CREATE INDEX idx_job_lifecycle_events_job_occurred
        ON job_lifecycle_events (job_id, occurred_utc, event_id)
    """,
    """
    CREATE TRIGGER trg_job_lifecycle_events_no_update
    BEFORE UPDATE ON job_lifecycle_events
    BEGIN
        SELECT RAISE(ABORT, 'JOB_LIFECYCLE_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_job_lifecycle_events_no_delete
    BEFORE DELETE ON job_lifecycle_events
    BEGIN
        SELECT RAISE(ABORT, 'JOB_LIFECYCLE_EVENT_IMMUTABLE');
    END
    """,
)

CATALOG_RETAINED_VERSION_OBJECTS = (
    """
    CREATE TABLE retained_version_objects (
        version_object_id TEXT PRIMARY KEY,
        handoff_id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        target_endpoint_id TEXT NOT NULL,
        target_endpoint_revision_id TEXT NOT NULL,
        endpoint_generation INTEGER NOT NULL CHECK (endpoint_generation >= 1),
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        final_relative_path TEXT NOT NULL,
        original_fingerprint_json TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        retention_policy TEXT NOT NULL CHECK (retention_policy = 'THIRTY_DAYS'),
        retention_until_utc TEXT NOT NULL,
        manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
        state TEXT NOT NULL DEFAULT 'RETAINED' CHECK (
            state IN ('RETAINED', 'DELETE_PENDING', 'DELETED', 'BLOCKED')
        ),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        deletion_plan_id TEXT,
        deleted_utc TEXT,
        last_validation_code TEXT,
        FOREIGN KEY (handoff_id)
            REFERENCES final_file_catalog_handoffs (handoff_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_retained_version_objects_due
        ON retained_version_objects (state, retention_until_utc, version_object_id)
    """,
    """
    CREATE INDEX idx_retained_version_objects_job_state
        ON retained_version_objects (job_id, state, version_object_id)
    """,
    """
    CREATE INDEX idx_retained_version_objects_endpoint_state
        ON retained_version_objects (
            target_endpoint_id,
            target_endpoint_revision_id,
            state,
            version_object_id
        )
    """,
    """
    CREATE TRIGGER trg_retained_version_objects_immutable_binding
    BEFORE UPDATE ON retained_version_objects
    WHEN
        NEW.version_object_id IS NOT OLD.version_object_id
        OR NEW.handoff_id IS NOT OLD.handoff_id
        OR NEW.run_id IS NOT OLD.run_id
        OR NEW.run_target_id IS NOT OLD.run_target_id
        OR NEW.operation_id IS NOT OLD.operation_id
        OR NEW.job_id IS NOT OLD.job_id
        OR NEW.job_revision_id IS NOT OLD.job_revision_id
        OR NEW.target_endpoint_id IS NOT OLD.target_endpoint_id
        OR NEW.target_endpoint_revision_id IS NOT OLD.target_endpoint_revision_id
        OR NEW.endpoint_generation IS NOT OLD.endpoint_generation
        OR NEW.owner_installation_id IS NOT OLD.owner_installation_id
        OR NEW.ownership_epoch IS NOT OLD.ownership_epoch
        OR NEW.final_relative_path IS NOT OLD.final_relative_path
        OR NEW.original_fingerprint_json IS NOT OLD.original_fingerprint_json
        OR NEW.created_utc IS NOT OLD.created_utc
        OR NEW.retention_policy IS NOT OLD.retention_policy
        OR NEW.retention_until_utc IS NOT OLD.retention_until_utc
        OR NEW.manifest_hash IS NOT OLD.manifest_hash
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_BINDING_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_objects_no_delete
    BEFORE DELETE ON retained_version_objects
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RECORD_REQUIRED');
    END
    """,
)

CATALOG_RETAINED_RECOVERY_OBJECT_ROLES = (
    """
    ALTER TABLE retained_version_objects
        ADD COLUMN object_role TEXT NOT NULL DEFAULT 'OLD_TARGET_VERSION'
        CHECK (object_role IN ('OLD_TARGET_VERSION', 'EMPTY_DIRECTORY_QUARANTINE'))
    """,
    """
    CREATE INDEX idx_retained_version_objects_role_state
        ON retained_version_objects (object_role, state, retention_until_utc, version_object_id)
    """,
    """
    CREATE TRIGGER trg_retained_version_objects_role_immutable
    BEFORE UPDATE OF object_role ON retained_version_objects
    WHEN NEW.object_role IS NOT OLD.object_role
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_RECOVERY_OBJECT_ROLE_IMMUTABLE');
    END
    """,
)

CATALOG_SNAPSHOT_BIRTHTIME_EVIDENCE = (
    """
    ALTER TABLE file_entries
        ADD COLUMN birthtime_ns INTEGER
        CHECK (birthtime_ns IS NULL OR birthtime_ns >= 0)
    """,
    """
    CREATE TRIGGER trg_snapshot_v3_requires_birthtime
    BEFORE UPDATE OF immutable, snapshot_schema_version ON snapshots
    WHEN NEW.immutable = 1
        AND NEW.snapshot_schema_version >= 3
        AND EXISTS (
            SELECT 1
            FROM file_entries
            WHERE snapshot_id = NEW.id
                AND object_type IN ('file', 'directory')
                AND birthtime_ns IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_V3_REQUIRES_BIRTHTIME');
    END
    """,
)


CATALOG_ENDPOINT_CAPABILITY_EVIDENCE = (
    """
    ALTER TABLE endpoint_classification_observations
        ADD COLUMN read_capabilities_json TEXT
    """,
    """
    ALTER TABLE endpoint_classification_observations
        ADD COLUMN read_capabilities_hash TEXT
        CHECK (read_capabilities_hash IS NULL OR length(read_capabilities_hash) = 64)
    """,
    """
    ALTER TABLE writable_endpoint_registrations
        ADD COLUMN write_capabilities_json TEXT
    """,
    """
    ALTER TABLE writable_endpoint_registrations
        ADD COLUMN write_capabilities_hash TEXT
        CHECK (write_capabilities_hash IS NULL OR length(write_capabilities_hash) = 64)
    """,
    """
    CREATE TABLE writable_endpoint_capability_observations (
        intent_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        endpoint_revision_id TEXT NOT NULL,
        capabilities_json TEXT NOT NULL CHECK (length(trim(capabilities_json)) > 0),
        capabilities_hash TEXT NOT NULL CHECK (length(capabilities_hash) = 64),
        observed_utc TEXT NOT NULL CHECK (length(trim(observed_utc)) > 0),
        PRIMARY KEY (intent_id, endpoint_id),
        UNIQUE (endpoint_id, endpoint_revision_id),
        FOREIGN KEY (intent_id)
            REFERENCES writable_endpoint_registration_intents (intent_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER trg_classified_endpoint_requires_read_capabilities_insert
    BEFORE INSERT ON endpoint_classification_observations
    WHEN NEW.inspection_status = 'CLASSIFIED'
        AND (
            NEW.read_capabilities_json IS NULL
            OR NEW.read_capabilities_hash IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'CLASSIFIED_ENDPOINT_REQUIRES_READ_CAPABILITIES');
    END
    """,
    """
    CREATE TRIGGER trg_classified_endpoint_requires_read_capabilities_update
    BEFORE UPDATE ON endpoint_classification_observations
    WHEN NEW.inspection_status = 'CLASSIFIED'
        AND (
            NEW.read_capabilities_json IS NULL
            OR NEW.read_capabilities_hash IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'CLASSIFIED_ENDPOINT_REQUIRES_READ_CAPABILITIES');
    END
    """,
    """
    CREATE TRIGGER trg_writable_registration_requires_capabilities
    BEFORE INSERT ON writable_endpoint_registrations
    WHEN NEW.write_capabilities_json IS NULL OR NEW.write_capabilities_hash IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_REGISTRATION_REQUIRES_CAPABILITIES');
    END
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_capability_observations_no_update
    BEFORE UPDATE ON writable_endpoint_capability_observations
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_CAPABILITY_EVIDENCE_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_writable_endpoint_capability_observations_no_delete
    BEFORE DELETE ON writable_endpoint_capability_observations
    BEGIN
        SELECT RAISE(ABORT, 'WRITABLE_ENDPOINT_CAPABILITY_EVIDENCE_IMMUTABLE');
    END
    """,
)

CATALOG_VERSION_RETENTION_PLANS = (
    """
    CREATE TABLE version_retention_holds (
        hold_id TEXT PRIMARY KEY,
        version_object_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        expires_utc TEXT,
        released_utc TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        FOREIGN KEY (version_object_id)
            REFERENCES retained_version_objects (version_object_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE UNIQUE INDEX uq_version_retention_holds_active_object
        ON version_retention_holds (version_object_id)
        WHERE released_utc IS NULL
    """,
    """
    CREATE TABLE version_retention_plans (
        plan_id TEXT PRIMARY KEY,
        cutoff_utc TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        candidate_count INTEGER NOT NULL CHECK (candidate_count >= 1),
        manifest_json TEXT NOT NULL,
        manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
        state TEXT NOT NULL CHECK (
            state IN ('PLANNED', 'APPLYING', 'COMPLETED', 'BLOCKED')
        ),
        completed_utc TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
    )
    """,
    """
    CREATE TABLE version_retention_items (
        plan_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        version_object_id TEXT NOT NULL,
        expected_object_row_version INTEGER NOT NULL CHECK (
            expected_object_row_version >= 2
        ),
        expected_manifest_hash TEXT NOT NULL CHECK (length(expected_manifest_hash) = 64),
        target_endpoint_id TEXT NOT NULL,
        target_endpoint_revision_id TEXT NOT NULL,
        endpoint_generation INTEGER NOT NULL CHECK (endpoint_generation >= 1),
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        final_relative_path TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN (
                'PLANNED',
                'DELETE_INTENT_RECORDED',
                'FILESYSTEM_DELETED',
                'DELETED',
                'BLOCKED'
            )
        ),
        delete_lease_id TEXT,
        delete_fencing_token INTEGER CHECK (
            delete_fencing_token IS NULL OR delete_fencing_token >= 1
        ),
        delete_intent_utc TEXT,
        filesystem_deleted_utc TEXT,
        deleted_utc TEXT,
        last_validation_code TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        PRIMARY KEY (plan_id, ordinal),
        UNIQUE (plan_id, version_object_id),
        FOREIGN KEY (plan_id)
            REFERENCES version_retention_plans (plan_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (version_object_id)
            REFERENCES retained_version_objects (version_object_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_version_retention_items_plan_state
        ON version_retention_items (plan_id, state, ordinal)
    """,
    """
    CREATE TABLE version_retention_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id TEXT NOT NULL,
        plan_sequence INTEGER NOT NULL CHECK (plan_sequence >= 0),
        version_object_id TEXT,
        event_kind TEXT NOT NULL,
        event_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_event_hash TEXT,
        event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
        UNIQUE (plan_id, plan_sequence),
        FOREIGN KEY (plan_id)
            REFERENCES version_retention_plans (plan_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER trg_version_retention_plans_immutable_manifest
    BEFORE UPDATE ON version_retention_plans
    WHEN
        NEW.plan_id IS NOT OLD.plan_id
        OR NEW.cutoff_utc IS NOT OLD.cutoff_utc
        OR NEW.created_utc IS NOT OLD.created_utc
        OR NEW.candidate_count IS NOT OLD.candidate_count
        OR NEW.manifest_json IS NOT OLD.manifest_json
        OR NEW.manifest_hash IS NOT OLD.manifest_hash
    BEGIN
        SELECT RAISE(ABORT, 'VERSION_RETENTION_PLAN_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_version_retention_items_immutable_binding
    BEFORE UPDATE ON version_retention_items
    WHEN
        NEW.plan_id IS NOT OLD.plan_id
        OR NEW.ordinal IS NOT OLD.ordinal
        OR NEW.version_object_id IS NOT OLD.version_object_id
        OR NEW.expected_object_row_version IS NOT OLD.expected_object_row_version
        OR NEW.expected_manifest_hash IS NOT OLD.expected_manifest_hash
        OR NEW.target_endpoint_id IS NOT OLD.target_endpoint_id
        OR NEW.target_endpoint_revision_id IS NOT OLD.target_endpoint_revision_id
        OR NEW.endpoint_generation IS NOT OLD.endpoint_generation
        OR NEW.owner_installation_id IS NOT OLD.owner_installation_id
        OR NEW.ownership_epoch IS NOT OLD.ownership_epoch
        OR NEW.final_relative_path IS NOT OLD.final_relative_path
    BEGIN
        SELECT RAISE(ABORT, 'VERSION_RETENTION_ITEM_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_version_retention_events_no_update
    BEFORE UPDATE ON version_retention_events
    BEGIN
        SELECT RAISE(ABORT, 'VERSION_RETENTION_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_version_retention_events_no_delete
    BEFORE DELETE ON version_retention_events
    BEGIN
        SELECT RAISE(ABORT, 'VERSION_RETENTION_EVENT_IMMUTABLE');
    END
    """,
)

CATALOG_RETAINED_VERSION_RESTORE_OPERATIONS = (
    """
    CREATE TABLE retained_version_restore_operations (
        restore_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        version_object_id TEXT NOT NULL,
        hold_id TEXT NOT NULL,
        expected_source_row_version INTEGER NOT NULL CHECK (
            expected_source_row_version >= 1
        ),
        rollback_object_id TEXT NOT NULL UNIQUE,
        created_utc TEXT NOT NULL,
        rollback_retention_until_utc TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN (
                'REQUESTED',
                'INTENT_RECORDED',
                'CURRENT_FINAL_PRESERVED',
                'HISTORICAL_APPLIED',
                'FINAL_VERIFIED',
                'COMPLETED',
                'FAILED_BLOCKED'
            )
        ),
        current_final_fingerprint_json TEXT,
        rollback_manifest_hash TEXT CHECK (
            rollback_manifest_hash IS NULL OR length(rollback_manifest_hash) = 64
        ),
        lease_id TEXT,
        fencing_token INTEGER CHECK (fencing_token IS NULL OR fencing_token >= 1),
        completed_utc TEXT,
        last_validation_code TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        updated_utc TEXT NOT NULL,
        FOREIGN KEY (version_object_id)
            REFERENCES retained_version_objects (version_object_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (hold_id)
            REFERENCES version_retention_holds (hold_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE UNIQUE INDEX uq_retained_version_restore_active_source
        ON retained_version_restore_operations (version_object_id)
        WHERE state NOT IN ('COMPLETED', 'FAILED_BLOCKED')
    """,
    """
    CREATE INDEX idx_retained_version_restore_work
        ON retained_version_restore_operations (state, created_utc, restore_id)
    """,
    """
    CREATE TABLE retained_version_restore_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        restore_id TEXT NOT NULL,
        restore_sequence INTEGER NOT NULL CHECK (restore_sequence >= 0),
        from_state TEXT,
        to_state TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        event_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_event_hash TEXT CHECK (
            previous_event_hash IS NULL OR length(previous_event_hash) = 64
        ),
        event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
        UNIQUE (restore_id, restore_sequence),
        FOREIGN KEY (restore_id)
            REFERENCES retained_version_restore_operations (restore_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_binding_immutable
    BEFORE UPDATE ON retained_version_restore_operations
    WHEN
        NEW.restore_id IS NOT OLD.restore_id
        OR NEW.request_id IS NOT OLD.request_id
        OR NEW.idempotency_key IS NOT OLD.idempotency_key
        OR NEW.version_object_id IS NOT OLD.version_object_id
        OR NEW.hold_id IS NOT OLD.hold_id
        OR NEW.expected_source_row_version IS NOT OLD.expected_source_row_version
        OR NEW.rollback_object_id IS NOT OLD.rollback_object_id
        OR NEW.created_utc IS NOT OLD.created_utc
        OR NEW.rollback_retention_until_utc IS NOT OLD.rollback_retention_until_utc
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_BINDING_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_no_delete
    BEFORE DELETE ON retained_version_restore_operations
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_RECORD_REQUIRED');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_events_no_update
    BEFORE UPDATE ON retained_version_restore_events
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_events_no_delete
    BEFORE DELETE ON retained_version_restore_events
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_EVENT_IMMUTABLE');
    END
    """,
)

CATALOG_RETAINED_VERSION_RESTORE_ROLLBACK_LIFECYCLE = (
    """
    CREATE TABLE retained_version_restore_rollbacks (
        restore_id TEXT PRIMARY KEY,
        rollback_object_id TEXT NOT NULL UNIQUE,
        expected_restored_final_fingerprint_json TEXT NOT NULL,
        rollback_fingerprint_json TEXT NOT NULL,
        rollback_manifest_hash TEXT NOT NULL CHECK (
            length(rollback_manifest_hash) = 64
        ),
        retention_until_utc TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN (
                'AVAILABLE',
                'UNDO_REQUESTED',
                'UNDO_INTENT_RECORDED',
                'UNDO_APPLIED',
                'UNDO_VERIFIED',
                'UNDONE',
                'EXPIRY_INTENT_RECORDED',
                'EXPIRED',
                'FAILED_BLOCKED'
            )
        ),
        undo_request_id TEXT,
        undo_idempotency_key TEXT UNIQUE,
        lease_id TEXT,
        fencing_token INTEGER CHECK (fencing_token IS NULL OR fencing_token >= 1),
        last_validation_code TEXT,
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        completed_utc TEXT,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        CHECK (
            (undo_request_id IS NULL AND undo_idempotency_key IS NULL)
            OR (undo_request_id IS NOT NULL AND undo_idempotency_key IS NOT NULL)
        ),
        FOREIGN KEY (restore_id)
            REFERENCES retained_version_restore_operations (restore_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_retained_version_restore_rollbacks_work
        ON retained_version_restore_rollbacks (state, retention_until_utc, restore_id)
    """,
    """
    CREATE TABLE retained_version_restore_rollback_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        restore_id TEXT NOT NULL,
        lifecycle_sequence INTEGER NOT NULL CHECK (lifecycle_sequence >= 0),
        from_state TEXT,
        to_state TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        event_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_event_hash TEXT CHECK (
            previous_event_hash IS NULL OR length(previous_event_hash) = 64
        ),
        event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
        UNIQUE (restore_id, lifecycle_sequence),
        FOREIGN KEY (restore_id)
            REFERENCES retained_version_restore_rollbacks (restore_id)
            ON DELETE RESTRICT
    )
    """,
    """
    INSERT INTO retained_version_restore_rollbacks (
        restore_id,
        rollback_object_id,
        expected_restored_final_fingerprint_json,
        rollback_fingerprint_json,
        rollback_manifest_hash,
        retention_until_utc,
        state,
        created_utc,
        updated_utc
    )
    SELECT
        restores.restore_id,
        restores.rollback_object_id,
        versions.original_fingerprint_json,
        restores.current_final_fingerprint_json,
        restores.rollback_manifest_hash,
        restores.rollback_retention_until_utc,
        'AVAILABLE',
        COALESCE(restores.completed_utc, restores.updated_utc),
        restores.updated_utc
    FROM retained_version_restore_operations AS restores
    INNER JOIN retained_version_objects AS versions
        ON versions.version_object_id = restores.version_object_id
    WHERE restores.state = 'COMPLETED'
        AND restores.current_final_fingerprint_json IS NOT NULL
        AND restores.rollback_manifest_hash IS NOT NULL
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_rollback_binding_immutable
    BEFORE UPDATE ON retained_version_restore_rollbacks
    WHEN
        NEW.restore_id IS NOT OLD.restore_id
        OR NEW.rollback_object_id IS NOT OLD.rollback_object_id
        OR NEW.expected_restored_final_fingerprint_json
            IS NOT OLD.expected_restored_final_fingerprint_json
        OR NEW.rollback_fingerprint_json IS NOT OLD.rollback_fingerprint_json
        OR NEW.rollback_manifest_hash IS NOT OLD.rollback_manifest_hash
        OR NEW.retention_until_utc IS NOT OLD.retention_until_utc
        OR NEW.created_utc IS NOT OLD.created_utc
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_ROLLBACK_BINDING_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_rollback_no_delete
    BEFORE DELETE ON retained_version_restore_rollbacks
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_ROLLBACK_RECORD_REQUIRED');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_rollback_events_no_update
    BEFORE UPDATE ON retained_version_restore_rollback_events
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_ROLLBACK_EVENT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_retained_version_restore_rollback_events_no_delete
    BEFORE DELETE ON retained_version_restore_rollback_events
    BEGIN
        SELECT RAISE(ABORT, 'RETAINED_VERSION_RESTORE_ROLLBACK_EVENT_IMMUTABLE');
    END
    """,
)

CATALOG_INITIAL_BACKUP_PLAN_MATERIALIZATIONS = (
    """
    CREATE TABLE initial_backup_plan_materializations (
        job_id TEXT NOT NULL,
        job_revision_id TEXT NOT NULL,
        analysis_id TEXT,
        plan_id TEXT UNIQUE,
        state TEXT NOT NULL CHECK (
            state IN ('SEALED', 'NO_CHANGES', 'BLOCKED', 'FAILED')
        ),
        reason_code TEXT NOT NULL CHECK (length(trim(reason_code)) > 0),
        operation_count INTEGER NOT NULL DEFAULT 0 CHECK (operation_count >= 0),
        planned_bytes INTEGER NOT NULL DEFAULT 0 CHECK (planned_bytes >= 0),
        plan_runnable INTEGER NOT NULL DEFAULT 0 CHECK (plan_runnable IN (0, 1)),
        next_action TEXT NOT NULL CHECK (length(trim(next_action)) > 0),
        started_utc TEXT NOT NULL,
        completed_utc TEXT NOT NULL,
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        PRIMARY KEY (job_id, job_revision_id),
        FOREIGN KEY (job_id, job_revision_id)
            REFERENCES job_revisions (job_id, id)
            ON DELETE RESTRICT,
        FOREIGN KEY (analysis_id)
            REFERENCES analyses (id)
            ON DELETE RESTRICT,
        FOREIGN KEY (plan_id)
            REFERENCES plan_seal_details (plan_id)
            ON DELETE RESTRICT,
        CHECK (
            (state = 'SEALED' AND analysis_id IS NOT NULL AND plan_id IS NOT NULL)
            OR (
                state = 'NO_CHANGES'
                AND analysis_id IS NOT NULL
                AND plan_id IS NULL
                AND operation_count = 0
                AND planned_bytes = 0
                AND plan_runnable = 0
            )
            OR (state IN ('BLOCKED', 'FAILED') AND plan_id IS NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_initial_backup_plan_materializations_state
        ON initial_backup_plan_materializations (
            state,
            completed_utc,
            job_id,
            job_revision_id
        )
    """,
    """
    CREATE TRIGGER trg_initial_backup_plan_materializations_terminal_immutable
    BEFORE UPDATE ON initial_backup_plan_materializations
    WHEN OLD.state IN ('SEALED', 'NO_CHANGES')
    BEGIN
        SELECT RAISE(ABORT, 'INITIAL_BACKUP_PLAN_MATERIALIZATION_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_initial_backup_plan_materializations_no_delete
    BEFORE DELETE ON initial_backup_plan_materializations
    BEGIN
        SELECT RAISE(ABORT, 'INITIAL_BACKUP_PLAN_MATERIALIZATION_IMMUTABLE');
    END
    """,
)

CATALOG_FINAL_FILE_HANDOFF_SKELETON = (
    """
    CREATE TABLE final_file_catalog_handoffs (
        handoff_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        target_endpoint_id TEXT NOT NULL,
        target_endpoint_revision_id TEXT NOT NULL,
        final_relative_path TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        lease_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
        effect_kind TEXT NOT NULL CHECK (effect_kind IN ('COPY_NEW_FINAL_FILE')),
        recorded_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (run_id, operation_id)
    )
    """,
    """
    CREATE INDEX idx_final_file_catalog_handoffs_run_target
        ON final_file_catalog_handoffs (run_id, run_target_id)
    """,
)

CATALOG_DIRECTORY_EFFECT_HANDOFFS = (
    """
    ALTER TABLE final_file_catalog_handoffs
        RENAME TO final_file_catalog_handoffs_v31
    """,
    """
    CREATE TABLE final_file_catalog_handoffs (
        handoff_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        target_endpoint_id TEXT NOT NULL,
        target_endpoint_revision_id TEXT NOT NULL,
        final_relative_path TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        lease_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
        effect_kind TEXT NOT NULL CHECK (
            effect_kind IN ('COPY_NEW_FINAL_FILE', 'CREATE_DIRECTORY')
        ),
        recorded_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (run_id, operation_id)
    )
    """,
    """
    INSERT INTO final_file_catalog_handoffs (
        handoff_id,
        run_id,
        run_target_id,
        operation_id,
        target_endpoint_id,
        target_endpoint_revision_id,
        final_relative_path,
        content_hash,
        lease_id,
        fencing_token,
        effect_kind,
        recorded_utc
    )
    SELECT
        handoff_id,
        run_id,
        run_target_id,
        operation_id,
        target_endpoint_id,
        target_endpoint_revision_id,
        final_relative_path,
        content_hash,
        lease_id,
        fencing_token,
        effect_kind,
        recorded_utc
    FROM final_file_catalog_handoffs_v31
    """,
    """
    DROP TABLE final_file_catalog_handoffs_v31
    """,
    """
    CREATE INDEX idx_final_file_catalog_handoffs_run_target
        ON final_file_catalog_handoffs (run_id, run_target_id)
    """,
)

CATALOG_PLAN_OPERATION_TARGET_BINDINGS = (
    """
    DROP TRIGGER trg_plan_operation_seal_details_no_update
    """,
    """
    ALTER TABLE plan_operation_seal_details
        ADD COLUMN target_endpoint_id TEXT
    """,
    """
    UPDATE plan_operation_seal_details
    SET target_endpoint_id = (
        SELECT endpoints.endpoint_id
        FROM plan_endpoints AS endpoints
        WHERE endpoints.plan_id = plan_operation_seal_details.plan_id
            AND endpoints.role = 'TARGET_WRITABLE'
    )
    WHERE (
        SELECT count(*)
        FROM plan_endpoints AS endpoints
        WHERE endpoints.plan_id = plan_operation_seal_details.plan_id
            AND endpoints.role = 'TARGET_WRITABLE'
    ) = 1
    """,
    """
    CREATE TRIGGER trg_plan_operation_seal_details_no_update
    BEFORE UPDATE ON plan_operation_seal_details
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_SEAL_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_operation_target_binding_valid_insert
    BEFORE INSERT ON plan_operation_seal_details
    WHEN NEW.target_endpoint_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM plan_endpoints AS endpoints
            WHERE endpoints.plan_id = NEW.plan_id
                AND endpoints.endpoint_id = NEW.target_endpoint_id
                AND endpoints.role = 'TARGET_WRITABLE'
        )
    BEGIN
        SELECT RAISE(ABORT, 'PLAN_OPERATION_TARGET_ENDPOINT_NOT_WRITABLE');
    END
    """,
    """
    CREATE TRIGGER trg_plan_operation_v2_requires_target_binding
    BEFORE INSERT ON plan_seal_details
    WHEN NEW.operation_schema_version >= 2
        AND EXISTS (
            SELECT 1
            FROM plan_endpoints AS endpoints
            WHERE endpoints.plan_id = NEW.plan_id
                AND endpoints.role = 'TARGET_WRITABLE'
        )
        AND EXISTS (
            SELECT 1
            FROM plan_operation_seal_details AS details
            INNER JOIN planned_operations AS operations
                ON operations.plan_id = details.plan_id
                AND operations.id = details.operation_id
            WHERE details.plan_id = NEW.plan_id
                AND operations.operation_type IN ('COPY_NEW', 'CREATE_DIRECTORY')
                AND details.target_endpoint_id IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'MUTATING_PLAN_OPERATION_REQUIRES_TARGET_ENDPOINT');
    END
    """,
    """
    CREATE INDEX idx_plan_operation_details_plan_target_phase_key_id
        ON plan_operation_seal_details (
            plan_id,
            target_endpoint_id,
            execution_phase,
            stable_order_key,
            operation_id
        )
    """,
)

CATALOG_SNAPSHOT_ENTRY_MATERIALIZATION = (
    """
    ALTER TABLE snapshots
        ADD COLUMN entry_count INTEGER NOT NULL DEFAULT 0 CHECK (entry_count >= 0)
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN total_bytes INTEGER NOT NULL DEFAULT 0 CHECK (total_bytes >= 0)
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN immutable INTEGER NOT NULL DEFAULT 0 CHECK (immutable IN (0, 1))
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN sealed_utc TEXT
    """,
    """
    ALTER TABLE file_entries
        ADD COLUMN size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0)
    """,
    """
    CREATE TABLE snapshot_batches (
        snapshot_id TEXT NOT NULL,
        sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
        approximate_bytes INTEGER NOT NULL CHECK (approximate_bytes >= 0),
        state TEXT NOT NULL CHECK (state IN ('COMMITTED')),
        committed_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (snapshot_id, sequence_no),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE UNIQUE INDEX uq_case_collision_groups_snapshot_comparison_key
        ON case_collision_groups (snapshot_id, comparison_key)
    """,
    """
    CREATE TRIGGER trg_snapshots_no_update_after_immutable
    BEFORE UPDATE ON snapshots
    WHEN OLD.immutable = 1
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_file_entries_no_insert_after_snapshot_immutable
    BEFORE INSERT ON file_entries
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = NEW.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_file_entries_no_update_after_snapshot_immutable
    BEFORE UPDATE ON file_entries
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_file_entries_no_delete_after_snapshot_immutable
    BEFORE DELETE ON file_entries
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshot_batches_no_insert_after_snapshot_immutable
    BEFORE INSERT ON snapshot_batches
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = NEW.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshot_batches_no_update_after_snapshot_immutable
    BEFORE UPDATE ON snapshot_batches
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshot_batches_no_delete_after_snapshot_immutable
    BEFORE DELETE ON snapshot_batches
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_case_collision_groups_no_insert_after_snapshot_immutable
    BEFORE INSERT ON case_collision_groups
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = NEW.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_case_collision_groups_no_update_after_snapshot_immutable
    BEFORE UPDATE ON case_collision_groups
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_case_collision_groups_no_delete_after_snapshot_immutable
    BEFORE DELETE ON case_collision_groups
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_case_collision_members_no_insert_after_snapshot_immutable
    BEFORE INSERT ON case_collision_members
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = NEW.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_case_collision_members_no_update_after_snapshot_immutable
    BEFORE UPDATE ON case_collision_members
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_case_collision_members_no_delete_after_snapshot_immutable
    BEFORE DELETE ON case_collision_members
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
)

CATALOG_SNAPSHOT_SEAL_CHECKSUM = (
    """
    ALTER TABLE snapshots
        ADD COLUMN complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1))
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN snapshot_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (snapshot_schema_version >= 1)
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN checksum_algorithm TEXT
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN serializer_version TEXT
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN snapshot_checksum TEXT CHECK (
            snapshot_checksum IS NULL
            OR (
                length(snapshot_checksum) = 64
                AND snapshot_checksum NOT GLOB '*[^0-9a-f]*'
            )
        )
    """,
    """
    CREATE TRIGGER trg_snapshots_seal_insert_requires_checksum
    BEFORE INSERT ON snapshots
    WHEN NEW.immutable = 1
        AND (
            NEW.complete != 1
            OR NEW.checksum_algorithm IS NULL
            OR NEW.serializer_version IS NULL
            OR NEW.snapshot_checksum IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_SEAL_INCOMPLETE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshots_seal_update_requires_checksum
    BEFORE UPDATE ON snapshots
    WHEN NEW.immutable = 1
        AND (
            NEW.complete != 1
            OR NEW.checksum_algorithm IS NULL
            OR NEW.serializer_version IS NULL
            OR NEW.snapshot_checksum IS NULL
        )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_SEAL_INCOMPLETE');
    END
    """,
)

CATALOG_SNAPSHOT_COVERAGE_ISSUE_MATERIALIZATION = (
    """
    ALTER TABLE snapshot_batches
        ADD COLUMN coverage_update_count INTEGER NOT NULL DEFAULT 0 CHECK (coverage_update_count >= 0)
    """,
    """
    ALTER TABLE snapshot_batches
        ADD COLUMN issue_count INTEGER NOT NULL DEFAULT 0 CHECK (issue_count >= 0)
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN scan_error_count INTEGER NOT NULL DEFAULT 0 CHECK (scan_error_count >= 0)
    """,
    """
    ALTER TABLE snapshots
        ADD COLUMN volatile_directory_count INTEGER NOT NULL DEFAULT 0 CHECK (volatile_directory_count >= 0)
    """,
    """
    CREATE TABLE directory_coverage (
        snapshot_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        comparison_key TEXT NOT NULL,
        coverage_state TEXT NOT NULL CHECK (
            coverage_state IN (
                'COMPLETE',
                'VOLATILE',
                'UNREADABLE',
                'DISAPPEARED',
                'REPARSE_BLOCKED',
                'CASE_CONTEXT_UNKNOWN',
                'CANCELLED'
            )
        ),
        case_mode TEXT NOT NULL CHECK (case_mode IN ('CASE_SENSITIVE', 'CASE_INSENSITIVE', 'UNKNOWN')),
        case_mode_evidence TEXT NOT NULL,
        case_context_hash TEXT NOT NULL CHECK (
            length(case_context_hash) = 64
            AND case_context_hash NOT GLOB '*[^0-9a-f]*'
        ),
        case_probe_error TEXT,
        identity_before_json TEXT,
        identity_after_json TEXT,
        enumerated_start_utc TEXT,
        enumerated_end_utc TEXT,
        PRIMARY KEY (snapshot_id, relative_path),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_directory_coverage_snapshot_comparison_state
        ON directory_coverage (snapshot_id, comparison_key, coverage_state)
    """,
    """
    CREATE TABLE snapshot_issues (
        id INTEGER PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        error_code TEXT,
        sanitized_message TEXT,
        blocks_destructive_actions INTEGER NOT NULL CHECK (blocks_destructive_actions IN (0, 1)),
        observed_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX idx_snapshot_issues_snapshot_relative_path
        ON snapshot_issues (snapshot_id, relative_path)
    """,
    """
    CREATE TRIGGER trg_directory_coverage_no_insert_after_snapshot_immutable
    BEFORE INSERT ON directory_coverage
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = NEW.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_directory_coverage_no_update_after_snapshot_immutable
    BEFORE UPDATE ON directory_coverage
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_directory_coverage_no_delete_after_snapshot_immutable
    BEFORE DELETE ON directory_coverage
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshot_issues_no_insert_after_snapshot_immutable
    BEFORE INSERT ON snapshot_issues
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = NEW.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshot_issues_no_update_after_snapshot_immutable
    BEFORE UPDATE ON snapshot_issues
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER trg_snapshot_issues_no_delete_after_snapshot_immutable
    BEFORE DELETE ON snapshot_issues
    WHEN EXISTS (
        SELECT 1 FROM snapshots
        WHERE id = OLD.snapshot_id AND immutable = 1
    )
    BEGIN
        SELECT RAISE(ABORT, 'SNAPSHOT_IMMUTABLE');
    END
    """,
)

CATALOG_SNAPSHOT_ENTRY_READ_MODEL_INDEXES = (
    """
    CREATE INDEX idx_file_entries_snapshot_comparison_path_id
        ON file_entries (snapshot_id, comparison_key, relative_path, id)
    """,
)

CATALOG_PLAN_OPERATION_READ_MODEL_INDEXES = (
    """
    CREATE INDEX idx_plan_operation_details_plan_phase_key_id
        ON plan_operation_seal_details (plan_id, execution_phase, stable_order_key, operation_id)
    """,
)

CATALOG_OUTBOX_RECONCILIATION_INDEXES = (
    """
    CREATE INDEX idx_outbox_messages_state_owner_claim_started
        ON outbox_messages (state, claim_owner_instance_id, claim_started_utc, id)
    """,
)

CATALOG_SNAPSHOT_COVERAGE_ISSUE_READ_MODEL_INDEXES = (
    """
    CREATE INDEX idx_directory_coverage_snapshot_comparison_path
        ON directory_coverage (snapshot_id, comparison_key, relative_path)
    """,
    """
    CREATE INDEX idx_directory_coverage_snapshot_state_comparison_path
        ON directory_coverage (snapshot_id, coverage_state, comparison_key, relative_path)
    """,
    """
    CREATE INDEX idx_snapshot_issues_snapshot_path_type_id
        ON snapshot_issues (snapshot_id, relative_path, issue_type, id)
    """,
    """
    CREATE INDEX idx_snapshot_issues_snapshot_blocking_path_type_id
        ON snapshot_issues (snapshot_id, blocks_destructive_actions, relative_path, issue_type, id)
    """,
)

CATALOG_RUN_ACTIVITY_READ_MODEL_INDEXES = (
    """
    CREATE INDEX idx_runs_started_id
        ON runs (started_utc DESC, id DESC)
    """,
    """
    CREATE INDEX idx_runs_job_started_id
        ON runs (job_id, started_utc DESC, id DESC)
    """,
)

CATALOG_HISTORY_TIMELINE_KEYSET_INDEXES = (
    """
    CREATE INDEX idx_initial_backup_materializations_history
        ON initial_backup_plan_materializations (
            started_utc DESC,
            COALESCE(
                analysis_id,
                'control:' || job_id || ':' || job_revision_id
            ) DESC
        )
    """,
    """
    CREATE INDEX idx_initial_backup_materializations_job_history
        ON initial_backup_plan_materializations (
            job_id,
            started_utc DESC,
            COALESCE(
                analysis_id,
                'control:' || job_id || ':' || job_revision_id
            ) DESC
        )
    """,
    """
    CREATE INDEX idx_backup_analysis_requests_history
        ON backup_analysis_requests (
            COALESCE(started_utc, requested_utc) DESC,
            request_id DESC
        )
    """,
    """
    CREATE INDEX idx_backup_analysis_requests_job_history
        ON backup_analysis_requests (
            job_id,
            COALESCE(started_utc, requested_utc) DESC,
            request_id DESC
        )
    """,
    """
    CREATE INDEX idx_backup_analysis_requests_analysis
        ON backup_analysis_requests (analysis_id)
        WHERE analysis_id IS NOT NULL
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

RECOVERY_LEASE_COUNTERS = (
    """
    CREATE TABLE lease_counters (
        resource_key TEXT PRIMARY KEY,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        last_fencing_token INTEGER NOT NULL CHECK (last_fencing_token >= 0),
        updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
)

RECOVERY_RESOURCE_LEASES = (
    """
    CREATE TABLE resource_leases (
        lease_id TEXT PRIMARY KEY,
        resource_key TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
        lease_mode TEXT NOT NULL CHECK (lease_mode IN ('EXCLUSIVE')),
        owner_instance_id TEXT NOT NULL,
        run_id TEXT,
        run_target_id TEXT,
        endpoint_id TEXT,
        endpoint_generation INTEGER CHECK (endpoint_generation IS NULL OR endpoint_generation >= 1),
        os_lock_kind TEXT NOT NULL CHECK (os_lock_kind IN ('LOCAL_OS_HANDLE')),
        state TEXT NOT NULL CHECK (state IN ('ACQUIRED', 'RELEASED')),
        acquired_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        heartbeat_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        released_utc TEXT,
        CHECK (
            (state = 'ACQUIRED' AND released_utc IS NULL)
            OR (state = 'RELEASED' AND released_utc IS NOT NULL)
        ),
        UNIQUE (resource_key, ownership_epoch, fencing_token)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_resource_leases_active_exclusive_resource
        ON resource_leases (resource_key)
        WHERE state = 'ACQUIRED' AND lease_mode = 'EXCLUSIVE'
    """,
    """
    CREATE INDEX idx_resource_leases_state_heartbeat
        ON resource_leases (state, heartbeat_utc)
    """,
)

RECOVERY_INTENT_SEGMENTS = (
    """
    CREATE TABLE recovery_intent_segments (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        target_endpoint_id TEXT NOT NULL,
        target_endpoint_revision_id TEXT NOT NULL,
        endpoint_generation INTEGER NOT NULL CHECK (endpoint_generation >= 1),
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        lease_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
        segment_sequence INTEGER NOT NULL CHECK (segment_sequence >= 0),
        relative_path TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
        operation_count INTEGER NOT NULL CHECK (operation_count BETWEEN 1 AND 10000),
        byte_count INTEGER NOT NULL CHECK (byte_count BETWEEN 0 AND 16777216),
        segment_hash TEXT NOT NULL CHECK (length(segment_hash) = 64),
        previous_segment_hash TEXT CHECK (
            previous_segment_hash IS NULL OR length(previous_segment_hash) = 64
        ),
        durability_state TEXT NOT NULL CHECK (durability_state IN ('PENDING', 'DURABLE')),
        state TEXT NOT NULL CHECK (
            state IN ('BUILDING', 'DURABLE', 'RECONCILED', 'CLEANUP_ELIGIBLE', 'CLEANED')
        ),
        created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (run_target_id, segment_sequence),
        UNIQUE (run_target_id, relative_path),
        FOREIGN KEY (lease_id) REFERENCES resource_leases (lease_id) ON DELETE RESTRICT,
        CHECK (state <> 'DURABLE' OR durability_state = 'DURABLE')
    )
    """,
    """
    CREATE INDEX idx_recovery_intent_segments_state
        ON recovery_intent_segments (state, updated_utc)
    """,
    """
    CREATE TRIGGER trg_recovery_intent_segments_immutable_after_durable
    BEFORE UPDATE ON recovery_intent_segments
    WHEN OLD.state IN ('DURABLE', 'RECONCILED', 'CLEANUP_ELIGIBLE', 'CLEANED')
        AND (
            NEW.run_id IS NOT OLD.run_id
            OR NEW.run_target_id IS NOT OLD.run_target_id
            OR NEW.target_endpoint_id IS NOT OLD.target_endpoint_id
            OR NEW.target_endpoint_revision_id IS NOT OLD.target_endpoint_revision_id
            OR NEW.endpoint_generation IS NOT OLD.endpoint_generation
            OR NEW.owner_installation_id IS NOT OLD.owner_installation_id
            OR NEW.ownership_epoch IS NOT OLD.ownership_epoch
            OR NEW.lease_id IS NOT OLD.lease_id
            OR NEW.fencing_token IS NOT OLD.fencing_token
            OR NEW.segment_sequence IS NOT OLD.segment_sequence
            OR NEW.relative_path IS NOT OLD.relative_path
            OR NEW.schema_version IS NOT OLD.schema_version
            OR NEW.operation_count IS NOT OLD.operation_count
            OR NEW.byte_count IS NOT OLD.byte_count
            OR NEW.segment_hash IS NOT OLD.segment_hash
            OR NEW.previous_segment_hash IS NOT OLD.previous_segment_hash
            OR NEW.durability_state IS NOT OLD.durability_state
        )
    BEGIN
        SELECT RAISE(ABORT, 'INTENT_SEGMENT_IMMUTABLE');
    END
    """,
)

RECOVERY_OPERATION_JOURNAL = (
    """
    CREATE TABLE recovery_operations (
        run_id TEXT NOT NULL,
        run_target_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        source_endpoint_id TEXT,
        source_endpoint_revision_id TEXT,
        target_endpoint_id TEXT NOT NULL,
        target_endpoint_revision_id TEXT NOT NULL,
        endpoint_generation INTEGER NOT NULL CHECK (endpoint_generation >= 1),
        owner_installation_id TEXT NOT NULL,
        ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1),
        lease_id TEXT NOT NULL,
        lease_resource_key TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
        phase TEXT NOT NULL CHECK (
            phase IN (
                'PLANNED',
                'SOURCE_VALIDATED',
                'SOURCE_STABILITY_BOUND',
                'TARGET_PRECONDITION_VALIDATED',
                'STAGING_ALLOCATED',
                'TRANSFERRED',
                'STAGING_DURABLE',
                'STAGING_VERIFIED',
                'COMMIT_INTENT_RECORDED',
                'COMMIT_PRECONDITIONS_REVALIDATED',
                'OLD_TARGET_PRESERVED',
                'FILESYSTEM_APPLIED',
                'FINAL_DURABLE',
                'FINAL_VERIFIED',
                'CATALOG_RECORDED',
                'CLEANED',
                'SKIPPED',
                'CONFLICT',
                'DEFERRED',
                'FAILED_RETRYABLE',
                'FAILED_BLOCKED',
                'CANCELLED',
                'ROLLBACK_REQUIRED',
                'USER_DECISION_REQUIRED'
            )
        ),
        source_relative_path TEXT,
        source_guard_kind TEXT,
        source_guard_evidence_hash TEXT CHECK (
            source_guard_evidence_hash IS NULL OR length(source_guard_evidence_hash) = 64
        ),
        source_hash_evidence_kind TEXT,
        source_path_chain_hash TEXT CHECK (
            source_path_chain_hash IS NULL OR length(source_path_chain_hash) = 64
        ),
        source_case_context_hash TEXT CHECK (
            source_case_context_hash IS NULL OR length(source_case_context_hash) = 64
        ),
        staging_object_id TEXT,
        final_relative_path TEXT NOT NULL,
        version_object_id TEXT,
        quarantine_object_id TEXT,
        intent_segment_id TEXT,
        intent_ordinal INTEGER CHECK (intent_ordinal IS NULL OR intent_ordinal >= 0),
        target_precondition_kind TEXT NOT NULL CHECK (
            target_precondition_kind IN ('ABSENT', 'MATCH_FINGERPRINT', 'DIRECTORY_EMPTY', 'NONE')
        ),
        expected_source_fingerprint_json TEXT,
        expected_target_fingerprint_json TEXT,
        expected_source_parent_identity_json TEXT,
        expected_target_parent_identity_json TEXT,
        expected_target_path_chain_hash TEXT CHECK (
            expected_target_path_chain_hash IS NULL OR length(expected_target_path_chain_hash) = 64
        ),
        expected_staging_fingerprint_json TEXT,
        expected_final_fingerprint_json TEXT,
        observed_target_file_id TEXT,
        transfer_state TEXT,
        assurance_level TEXT,
        staging_durability_state TEXT,
        final_durability_state TEXT,
        catalog_handoff_id TEXT,
        last_error_code TEXT,
        updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (run_id, operation_id),
        FOREIGN KEY (lease_id) REFERENCES resource_leases (lease_id) ON DELETE RESTRICT,
        FOREIGN KEY (intent_segment_id)
            REFERENCES recovery_intent_segments (id)
            ON DELETE RESTRICT,
        CHECK (
            phase NOT IN (
                'COMMIT_INTENT_RECORDED',
                'COMMIT_PRECONDITIONS_REVALIDATED',
                'OLD_TARGET_PRESERVED',
                'FILESYSTEM_APPLIED',
                'FINAL_DURABLE',
                'FINAL_VERIFIED',
                'CATALOG_RECORDED',
                'CLEANED'
            )
            OR (intent_segment_id IS NOT NULL AND intent_ordinal IS NOT NULL)
        )
    )
    """,
    """
    CREATE UNIQUE INDEX uq_recovery_operations_intent_ordinal
        ON recovery_operations (intent_segment_id, intent_ordinal)
        WHERE intent_segment_id IS NOT NULL AND intent_ordinal IS NOT NULL
    """,
    """
    CREATE INDEX idx_recovery_operations_phase
        ON recovery_operations (phase, updated_utc)
    """,
    """
    CREATE TABLE recovery_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 0),
        operation_id TEXT,
        from_phase TEXT,
        to_phase TEXT NOT NULL,
        event_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        process_instance_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_event_hash TEXT CHECK (
            previous_event_hash IS NULL OR length(previous_event_hash) = 64
        ),
        event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
        UNIQUE (run_id, run_sequence),
        FOREIGN KEY (run_id, operation_id)
            REFERENCES recovery_operations (run_id, operation_id)
            ON DELETE RESTRICT
    )
    """,
)

RECOVERY_OPERATION_KIND_AND_PLAN_SEQUENCE = (
    """
    ALTER TABLE recovery_operations
    ADD COLUMN operation_kind TEXT NOT NULL DEFAULT 'COPY_NEW'
        CHECK (operation_kind IN ('COPY_NEW', 'CREATE_DIRECTORY'))
    """,
    """
    ALTER TABLE recovery_operations
    ADD COLUMN plan_sequence_no INTEGER NOT NULL DEFAULT 0
        CHECK (plan_sequence_no >= 0)
    """,
    """
    CREATE INDEX idx_recovery_operations_run_target_sequence
        ON recovery_operations (run_id, run_target_id, plan_sequence_no, operation_id)
    """,
)

RECOVERY_OPERATION_PLANNED_BYTES = (
    """
    ALTER TABLE recovery_operations
    ADD COLUMN planned_bytes INTEGER NOT NULL DEFAULT 0
        CHECK (planned_bytes >= 0)
    """,
)

RECOVERY_SOURCE_FILE_PRECONDITIONS = (
    """
    ALTER TABLE recovery_operations
        ADD COLUMN source_precondition_json TEXT
    """,
)

RECOVERY_STAGING_FAILURE_COUNT = (
    """
    ALTER TABLE recovery_operations
        ADD COLUMN staging_failure_count INTEGER NOT NULL DEFAULT 0
            CHECK (staging_failure_count >= 0)
    """,
)

RECOVERY_STAGING_RETRY_TIMING = (
    """
    ALTER TABLE recovery_operations
        ADD COLUMN staging_retry_backoff_ms INTEGER
            CHECK (
                staging_retry_backoff_ms IS NULL
                OR staging_retry_backoff_ms BETWEEN 1 AND 30000
            )
    """,
    """
    ALTER TABLE recovery_operations
        ADD COLUMN staging_retry_not_before_utc TEXT
            CHECK (
                staging_retry_not_before_utc IS NULL
                OR (
                    length(staging_retry_not_before_utc) BETWEEN 20 AND 64
                    AND substr(staging_retry_not_before_utc, -1, 1) = 'Z'
                )
            )
    """,
)

RECOVERY_VERSION_RETENTION_METADATA = (
    """
    ALTER TABLE recovery_operations
        ADD COLUMN job_id TEXT
    """,
    """
    ALTER TABLE recovery_operations
        ADD COLUMN job_revision_id TEXT
    """,
    """
    ALTER TABLE recovery_operations
        ADD COLUMN retention_policy TEXT
            CHECK (retention_policy IS NULL OR retention_policy = 'THIRTY_DAYS')
    """,
    """
    ALTER TABLE recovery_operations
        ADD COLUMN version_created_utc TEXT
    """,
    """
    ALTER TABLE recovery_operations
        ADD COLUMN version_retention_until_utc TEXT
    """,
    """
    ALTER TABLE recovery_operations
        ADD COLUMN version_manifest_hash TEXT
            CHECK (version_manifest_hash IS NULL OR length(version_manifest_hash) = 64)
    """,
)
