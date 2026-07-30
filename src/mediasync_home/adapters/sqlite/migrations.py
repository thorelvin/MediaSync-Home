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
