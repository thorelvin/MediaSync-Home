from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteStore,
    apply_sqlite_connection_policy,
    build_state_store_layout,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigration,
    SqliteMigrationPlan,
    apply_sqlite_migrations,
    catalog_migration_plan,
    current_schema_version,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.state_backup import verify_sqlite_state_backup_set
from mediasync_home.adapters.sqlite.state_migration import (
    STATE_MIGRATION_COMMITTED_FILENAME,
    STATE_MIGRATION_EPOCHS_DIR_NAME,
    STATE_MIGRATION_INTENT_FILENAME,
    SqliteStateMigrationViolation,
    migrate_sqlite_state_stores,
)


class _SimulatedMigrationCrash(RuntimeError):
    pass


def test_fresh_state_initialization_commits_one_restartable_epoch(tmp_path: Path) -> None:
    layout = build_state_store_layout(tmp_path / "state")

    report = migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="test",
        started_utc="2026-07-31T03:00:00Z",
        completed_utc="2026-07-31T03:00:01Z",
        epoch_id_factory=lambda: "fresh-a",
    )

    assert report.migration_performed
    assert report.created_epoch_count == 1
    assert report.resumed_epoch_count == 0
    assert report.committed_epoch_ids == ("fresh-a",)
    assert report.latest_backup_set_path is None
    assert [(store.store, store.initial_version, store.final_version) for store in report.stores] == [
        (SqliteStore.CATALOG, 0, 28),
        (SqliteStore.RECOVERY, 0, 5),
    ]
    epoch_dir = layout.root / STATE_MIGRATION_EPOCHS_DIR_NAME / "fresh-a"
    assert (epoch_dir / STATE_MIGRATION_INTENT_FILENAME).is_file()
    assert (epoch_dir / STATE_MIGRATION_COMMITTED_FILENAME).is_file()

    restarted = migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="test",
        started_utc="2026-07-31T03:01:00Z",
        completed_utc="2026-07-31T03:01:01Z",
        epoch_id_factory=lambda: "must-not-be-used",
    )

    assert not restarted.migration_performed
    assert restarted.previously_committed_epoch_count == 1
    assert restarted.created_epoch_count == 0
    assert restarted.resumed_epoch_count == 0


def test_existing_state_upgrade_resumes_after_only_catalog_migrates(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="test",
        started_utc="2026-07-31T03:00:00Z",
        completed_utc="2026-07-31T03:00:01Z",
        epoch_id_factory=lambda: "base-a",
    )
    catalog_plan, recovery_plan = _extended_plans()

    def crash_after_catalog(store: SqliteStore) -> None:
        if store is SqliteStore.CATALOG:
            raise _SimulatedMigrationCrash

    with pytest.raises(_SimulatedMigrationCrash):
        migrate_sqlite_state_stores(
            layout,
            catalog_plan=catalog_plan,
            recovery_plan=recovery_plan,
            app_version="test-upgrade",
            started_utc="2026-07-31T03:02:00Z",
            completed_utc="2026-07-31T03:02:01Z",
            epoch_id_factory=lambda: "upgrade-a",
            after_store_migrated=crash_after_catalog,
        )

    assert _schema_versions(layout) == (29, 5)
    upgrade_dir = layout.root / STATE_MIGRATION_EPOCHS_DIR_NAME / "upgrade-a"
    assert (upgrade_dir / STATE_MIGRATION_INTENT_FILENAME).is_file()
    assert not (upgrade_dir / STATE_MIGRATION_COMMITTED_FILENAME).exists()
    backup_dir = upgrade_dir / "backup-sets" / "pre-migration"
    backup = verify_sqlite_state_backup_set(backup_dir)
    assert [store.schema_version for store in backup.stores] == [28, 5]

    resumed = migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_plan,
        recovery_plan=recovery_plan,
        app_version="test-upgrade",
        started_utc="2026-07-31T03:03:00Z",
        completed_utc="2026-07-31T03:03:01Z",
        epoch_id_factory=lambda: "must-not-be-used",
    )

    assert resumed.migration_performed
    assert resumed.resumed_epoch_count == 1
    assert resumed.created_epoch_count == 0
    assert resumed.committed_epoch_ids == ("upgrade-a",)
    assert resumed.latest_backup_set_path == backup_dir
    assert resumed.latest_backup_state_set_hash == backup.state_set_hash
    assert _schema_versions(layout) == (29, 6)
    assert (upgrade_dir / STATE_MIGRATION_COMMITTED_FILENAME).is_file()
    with sqlite3.connect(layout.catalog) as connection:
        assert connection.execute(
            "SELECT value FROM migration_epoch_catalog_probe WHERE id = 1"
        ).fetchone() == ("catalog",)
    with sqlite3.connect(layout.recovery) as connection:
        assert connection.execute(
            "SELECT value FROM migration_epoch_recovery_probe WHERE id = 1"
        ).fetchone() == ("recovery",)


def test_pending_migration_rejects_tampered_plan_hash_before_second_store(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="test",
        started_utc="2026-07-31T03:00:00Z",
        completed_utc="2026-07-31T03:00:01Z",
        epoch_id_factory=lambda: "base-a",
    )
    catalog_plan, recovery_plan = _extended_plans()

    with pytest.raises(_SimulatedMigrationCrash):
        migrate_sqlite_state_stores(
            layout,
            catalog_plan=catalog_plan,
            recovery_plan=recovery_plan,
            app_version="test-upgrade",
            started_utc="2026-07-31T03:02:00Z",
            completed_utc="2026-07-31T03:02:01Z",
            epoch_id_factory=lambda: "upgrade-a",
            after_store_migrated=lambda store: (
                _raise_simulated_crash() if store is SqliteStore.CATALOG else None
            ),
        )
    intent_path = (
        layout.root
        / STATE_MIGRATION_EPOCHS_DIR_NAME
        / "upgrade-a"
        / STATE_MIGRATION_INTENT_FILENAME
    )
    payload = json.loads(intent_path.read_text(encoding="utf-8"))
    payload["plan_hash"] = "0" * 64
    intent_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SqliteStateMigrationViolation,
        match="STATE_MIGRATION_PLAN_HASH_MISMATCH",
    ):
        migrate_sqlite_state_stores(
            layout,
            catalog_plan=catalog_plan,
            recovery_plan=recovery_plan,
            app_version="test-upgrade",
            started_utc="2026-07-31T03:03:00Z",
            completed_utc="2026-07-31T03:03:01Z",
        )

    assert _schema_versions(layout) == (29, 5)


def test_committed_epoch_rejects_tampered_final_store_version(tmp_path: Path) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="test",
        started_utc="2026-07-31T03:00:00Z",
        completed_utc="2026-07-31T03:00:01Z",
        epoch_id_factory=lambda: "fresh-a",
    )
    committed_path = (
        layout.root
        / STATE_MIGRATION_EPOCHS_DIR_NAME
        / "fresh-a"
        / STATE_MIGRATION_COMMITTED_FILENAME
    )
    payload = json.loads(committed_path.read_text(encoding="utf-8"))
    payload["stores"][0]["final_version"] = 999
    committed_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SqliteStateMigrationViolation,
        match="STATE_MIGRATION_COMMITTED_STORE_VERSION_MISMATCH",
    ):
        migrate_sqlite_state_stores(
            layout,
            catalog_plan=catalog_migration_plan(),
            recovery_plan=recovery_migration_plan(),
            app_version="test",
            started_utc="2026-07-31T03:01:00Z",
            completed_utc="2026-07-31T03:01:01Z",
        )


def test_partial_initialization_without_epoch_is_rejected(tmp_path: Path) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    layout.root.mkdir(parents=True)
    with sqlite3.connect(layout.catalog) as connection:
        apply_sqlite_connection_policy(
            connection,
            catalog_critical_writer_policy(layout.catalog),
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())

    with pytest.raises(
        SqliteStateMigrationViolation,
        match="STATE_MIGRATION_PARTIAL_INITIALIZATION_WITHOUT_EPOCH",
    ):
        migrate_sqlite_state_stores(
            layout,
            catalog_plan=catalog_migration_plan(),
            recovery_plan=recovery_migration_plan(),
            app_version="test",
            started_utc="2026-07-31T03:00:00Z",
            completed_utc="2026-07-31T03:00:01Z",
            epoch_id_factory=lambda: "partial-a",
        )

    assert not (layout.root / STATE_MIGRATION_EPOCHS_DIR_NAME / "partial-a").exists()


@pytest.mark.parametrize("with_partial_temp", [False, True])
def test_unpublished_epoch_is_removed_before_fresh_initialization(
    tmp_path: Path,
    *,
    with_partial_temp: bool,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    orphan = layout.root / STATE_MIGRATION_EPOCHS_DIR_NAME / "orphan-a"
    orphan.mkdir(parents=True)
    if with_partial_temp:
        (orphan / f".{STATE_MIGRATION_INTENT_FILENAME}.tmp").write_text(
            '{"partial":',
            encoding="utf-8",
        )

    report = migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="test",
        started_utc="2026-07-31T03:00:00Z",
        completed_utc="2026-07-31T03:00:01Z",
        epoch_id_factory=lambda: "fresh-a",
    )

    assert report.committed_epoch_ids == ("fresh-a",)
    assert not orphan.exists()
    assert (
        layout.root
        / STATE_MIGRATION_EPOCHS_DIR_NAME
        / "fresh-a"
        / STATE_MIGRATION_COMMITTED_FILENAME
    ).is_file()


def _extended_plans() -> tuple[SqliteMigrationPlan, SqliteMigrationPlan]:
    catalog = catalog_migration_plan()
    recovery = recovery_migration_plan()
    return (
        SqliteMigrationPlan(
            store=catalog.store,
            migrations=(
                *catalog.migrations,
                SqliteMigration(
                    version=29,
                    name="test_catalog_migration_epoch",
                    statements=(
                        """
                        CREATE TABLE migration_epoch_catalog_probe (
                            id INTEGER PRIMARY KEY,
                            value TEXT NOT NULL
                        )
                        """,
                        """
                        INSERT INTO migration_epoch_catalog_probe (id, value)
                        VALUES (1, 'catalog')
                        """,
                    ),
                ),
            ),
        ),
        SqliteMigrationPlan(
            store=recovery.store,
            migrations=(
                *recovery.migrations,
                SqliteMigration(
                    version=6,
                    name="test_recovery_migration_epoch",
                    statements=(
                        """
                        CREATE TABLE migration_epoch_recovery_probe (
                            id INTEGER PRIMARY KEY,
                            value TEXT NOT NULL
                        )
                        """,
                        """
                        INSERT INTO migration_epoch_recovery_probe (id, value)
                        VALUES (1, 'recovery')
                        """,
                    ),
                ),
            ),
        ),
    )


def _schema_versions(layout) -> tuple[int, int]:
    with sqlite3.connect(layout.catalog) as catalog:
        catalog_version = current_schema_version(catalog, SqliteStore.CATALOG)
    with sqlite3.connect(layout.recovery) as recovery:
        recovery_version = current_schema_version(recovery, SqliteStore.RECOVERY)
    return catalog_version, recovery_version


def _raise_simulated_crash() -> None:
    raise _SimulatedMigrationCrash
