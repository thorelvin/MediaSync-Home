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
