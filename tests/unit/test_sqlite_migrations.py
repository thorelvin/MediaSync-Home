from __future__ import annotations

import pytest

from mediasync_home.adapters.sqlite.connection_policy import SqliteStore
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigration,
    SqliteMigrationPlan,
    SqliteMigrationViolation,
    catalog_migration_plan,
    recovery_migration_plan,
    validate_migration_plan,
)


def test_catalog_and_recovery_migration_plans_are_separate() -> None:
    catalog = catalog_migration_plan()
    recovery = recovery_migration_plan()

    assert catalog.store is SqliteStore.CATALOG
    assert recovery.store is SqliteStore.RECOVERY
    assert [migration.version for migration in catalog.migrations] == [1, 2]
    assert [migration.version for migration in recovery.migrations] == [1]
    assert catalog.migrations[0].name == "catalog_core_contract_skeleton"
    assert catalog.migrations[1].name == "catalog_standard_backup_drafts"
    assert recovery.migrations[0].name == "recovery_journal_skeleton"


def test_migration_plan_requires_contiguous_versions() -> None:
    plan = SqliteMigrationPlan(
        store=SqliteStore.CATALOG,
        migrations=(
            SqliteMigration(version=1, name="one", statements=("SELECT 1",)),
            SqliteMigration(version=3, name="three", statements=("SELECT 3",)),
        ),
    )

    with pytest.raises(SqliteMigrationViolation, match="MIGRATION_VERSIONS_MUST_BE_CONTIGUOUS"):
        validate_migration_plan(plan)


def test_migration_plan_rejects_duplicate_names() -> None:
    plan = SqliteMigrationPlan(
        store=SqliteStore.CATALOG,
        migrations=(
            SqliteMigration(version=1, name="same", statements=("SELECT 1",)),
            SqliteMigration(version=2, name="same", statements=("SELECT 2",)),
        ),
    )

    with pytest.raises(SqliteMigrationViolation, match="MIGRATION_NAMES_MUST_BE_UNIQUE"):
        validate_migration_plan(plan)


def test_migration_plan_rejects_empty_statements() -> None:
    plan = SqliteMigrationPlan(
        store=SqliteStore.RECOVERY,
        migrations=(SqliteMigration(version=1, name="empty", statements=()),),
    )

    with pytest.raises(SqliteMigrationViolation, match="MIGRATION_REQUIRES_STATEMENTS"):
        validate_migration_plan(plan)
