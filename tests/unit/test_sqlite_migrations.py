from __future__ import annotations

import pytest

from mediasync_home.adapters.sqlite.connection_policy import SqliteStore
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigration,
    SqliteMigrationPlan,
    SqliteMigrationViolation,
    catalog_migration_plan,
    migration_checksum,
    recovery_migration_plan,
    validate_migration_plan,
)


def test_catalog_and_recovery_migration_plans_are_separate() -> None:
    catalog = catalog_migration_plan()
    recovery = recovery_migration_plan()

    assert catalog.store is SqliteStore.CATALOG
    assert recovery.store is SqliteStore.RECOVERY
    assert [migration.version for migration in catalog.migrations] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        47,
        48,
        49,
        50,
        51,
        52,
        53,
        54,
    ]
    assert [migration.version for migration in recovery.migrations] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
    ]
    assert catalog.migrations[0].name == "catalog_core_contract_skeleton"
    assert catalog.migrations[1].name == "catalog_standard_backup_drafts"
    assert catalog.migrations[2].name == "catalog_standard_backup_job_revisions"
    assert catalog.migrations[3].name == "catalog_command_receipts"
    assert catalog.migrations[4].name == "catalog_sealed_plan_details"
    assert catalog.migrations[5].name == "catalog_run_start_skeleton"
    assert catalog.migrations[6].name == "catalog_plan_endpoint_bindings"
    assert catalog.migrations[7].name == "catalog_transactional_outbox_skeleton"
    assert catalog.migrations[8].name == "catalog_final_file_handoff_skeleton"
    assert catalog.migrations[9].name == "catalog_snapshot_entry_materialization"
    assert catalog.migrations[10].name == "catalog_snapshot_seal_checksum"
    assert (
        catalog.migrations[11].name == "catalog_snapshot_coverage_issue_materialization"
    )
    assert catalog.migrations[12].name == "catalog_snapshot_entry_read_model_indexes"
    assert catalog.migrations[13].name == "catalog_plan_operation_read_model_indexes"
    assert catalog.migrations[14].name == "catalog_outbox_reconciliation_indexes"
    assert (
        catalog.migrations[15].name
        == "catalog_snapshot_coverage_issue_read_model_indexes"
    )
    assert catalog.migrations[16].name == "catalog_command_receipt_tombstones"
    assert catalog.migrations[17].name == "catalog_run_activity_read_model_indexes"
    assert catalog.migrations[18].name == "catalog_trigger_occurrence_dedup"
    assert catalog.migrations[19].name == "catalog_schedule_desired_state"
    assert catalog.migrations[20].name == "catalog_external_resource_state"
    assert catalog.migrations[21].name == "catalog_endpoint_revision_identity"
    assert (
        catalog.migrations[22].name == "catalog_standard_backup_job_endpoint_bindings"
    )
    assert catalog.migrations[23].name == "catalog_installation_state"
    assert catalog.migrations[24].name == "catalog_endpoint_classification_observations"
    assert (
        catalog.migrations[25].name
        == "catalog_standard_backup_job_snapshot_materializations"
    )
    assert catalog.migrations[26].name == "catalog_immutable_revision_guards"
    assert catalog.migrations[27].name == "catalog_filter_set_versions"
    assert catalog.migrations[28].name == "catalog_endpoint_generation_bindings"
    assert catalog.migrations[29].name == "catalog_writable_endpoint_registrations"
    assert catalog.migrations[30].name == "catalog_initial_backup_plan_materializations"
    assert catalog.migrations[31].name == "catalog_directory_effect_handoffs"
    assert catalog.migrations[32].name == "catalog_plan_operation_target_bindings"
    assert catalog.migrations[33].name == "catalog_run_stop_requests"
    assert catalog.migrations[34].name == "catalog_backup_analysis_requests"
    assert catalog.migrations[35].name == "catalog_current_read_hash_evidence"
    assert catalog.migrations[36].name == "catalog_source_file_preconditions"
    assert catalog.migrations[39].name == "catalog_run_operation_audit"
    assert catalog.migrations[40].name == "catalog_history_timeline_keyset_indexes"
    assert catalog.migrations[41].name == "catalog_controlled_endpoint_takeovers"
    assert catalog.migrations[42].name == "catalog_job_lifecycle"
    assert (
        catalog.migrations[46].name
        == "catalog_retained_version_restore_rollback_lifecycle"
    )
    assert (
        catalog.migrations[47].name
        == "catalog_retained_recovery_object_roles"
    )
    assert catalog.migrations[48].name == "catalog_snapshot_birthtime_evidence"
    assert catalog.migrations[49].name == "catalog_endpoint_capability_evidence"
    assert catalog.migrations[50].name == "catalog_operation_verification_axes"
    assert catalog.migrations[51].name == "catalog_snapshot_filter_decisions"
    assert catalog.migrations[52].name == "catalog_deferred_automation_plan_operations"
    assert catalog.migrations[53].name == "catalog_duplicate_relation_materialization"
    assert recovery.migrations[0].name == "recovery_journal_skeleton"
    assert recovery.migrations[1].name == "recovery_lease_counters"
    assert recovery.migrations[2].name == "recovery_resource_leases"
    assert recovery.migrations[3].name == "recovery_intent_segments"
    assert recovery.migrations[4].name == "recovery_operation_journal"
    assert recovery.migrations[5].name == "recovery_operation_kind_and_plan_sequence"
    assert recovery.migrations[6].name == "recovery_operation_planned_bytes"
    assert recovery.migrations[7].name == "recovery_source_file_preconditions"
    assert recovery.migrations[8].name == "recovery_staging_failure_count"
    assert recovery.migrations[9].name == "recovery_staging_retry_timing"


def test_migration_plan_requires_contiguous_versions() -> None:
    plan = SqliteMigrationPlan(
        store=SqliteStore.CATALOG,
        migrations=(
            SqliteMigration(version=1, name="one", statements=("SELECT 1",)),
            SqliteMigration(version=3, name="three", statements=("SELECT 3",)),
        ),
    )

    with pytest.raises(
        SqliteMigrationViolation, match="MIGRATION_VERSIONS_MUST_BE_CONTIGUOUS"
    ):
        validate_migration_plan(plan)


def test_migration_plan_rejects_duplicate_names() -> None:
    plan = SqliteMigrationPlan(
        store=SqliteStore.CATALOG,
        migrations=(
            SqliteMigration(version=1, name="same", statements=("SELECT 1",)),
            SqliteMigration(version=2, name="same", statements=("SELECT 2",)),
        ),
    )

    with pytest.raises(
        SqliteMigrationViolation, match="MIGRATION_NAMES_MUST_BE_UNIQUE"
    ):
        validate_migration_plan(plan)


def test_migration_plan_rejects_empty_statements() -> None:
    plan = SqliteMigrationPlan(
        store=SqliteStore.RECOVERY,
        migrations=(SqliteMigration(version=1, name="empty", statements=()),),
    )

    with pytest.raises(SqliteMigrationViolation, match="MIGRATION_REQUIRES_STATEMENTS"):
        validate_migration_plan(plan)


def test_migration_checksum_is_deterministic_and_statement_sensitive() -> None:
    original = SqliteMigration(
        version=1,
        name="one",
        statements=("CREATE TABLE example (id INTEGER PRIMARY KEY)",),
    )
    changed = SqliteMigration(
        version=1,
        name="one",
        statements=("CREATE TABLE example (id TEXT PRIMARY KEY)",),
    )

    assert migration_checksum(original) == migration_checksum(original)
    assert len(migration_checksum(original)) == 64
    assert migration_checksum(original) != migration_checksum(changed)
