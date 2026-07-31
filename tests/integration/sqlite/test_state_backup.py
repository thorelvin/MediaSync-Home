from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    StateStoreLayout,
    apply_sqlite_connection_policy,
    build_state_store_layout,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.state_backup import (
    BACKUP_SET_INTENT_FILENAME,
    BACKUP_SET_MANIFEST_FILENAME,
    STATE_COMPACTION_COMMITTED_FILENAME,
    STATE_COMPACTION_EPOCHS_DIR_NAME,
    STATE_COMPACTION_INTENT_FILENAME,
    STATE_COMPACTION_ROLLED_BACK_FILENAME,
    STATE_RESTORE_COMMITTED_FILENAME,
    STATE_RESTORE_EPOCHS_DIR_NAME,
    STATE_RESTORE_INTENT_FILENAME,
    STATE_RESTORE_ROLLED_BACK_FILENAME,
    SqliteStateBackupViolation,
    SqliteStateMaintenanceRetentionPolicy,
    admit_sqlite_state_restore_maintenance,
    apply_sqlite_state_maintenance_retention,
    apply_sqlite_state_restore_plan,
    compact_sqlite_state_stores,
    create_sqlite_state_backup_set,
    load_sqlite_state_backup_manifest,
    plan_sqlite_state_maintenance_retention,
    plan_sqlite_state_restore,
    recover_incomplete_sqlite_state_compaction_epochs,
    recover_incomplete_sqlite_state_restore_epochs,
    reconcile_committed_sqlite_state_restore_epochs,
    restore_sqlite_state_backup_set,
    verify_sqlite_state_backup_set,
)


def test_sqlite_state_backup_set_captures_verified_catalog_recovery_pair(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)

    manifest = create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    backup_dir = tmp_path / "state-backups" / "set-a"

    assert (backup_dir / BACKUP_SET_INTENT_FILENAME).is_file()
    assert (backup_dir / BACKUP_SET_MANIFEST_FILENAME).is_file()
    assert [entry.store.value for entry in manifest.stores] == ["catalog", "recovery"]
    assert [entry.schema_version for entry in manifest.stores] == [38, 9]
    assert [entry.migration_count for entry in manifest.stores] == [38, 9]
    assert all(entry.quick_check == "ok" for entry in manifest.stores)
    assert all(entry.foreign_key_violations == 0 for entry in manifest.stores)
    assert all(len(entry.sha256) == 64 for entry in manifest.stores)

    loaded = load_sqlite_state_backup_manifest(backup_dir)
    verified = verify_sqlite_state_backup_set(backup_dir)

    assert loaded == manifest
    assert verified == manifest


def test_sqlite_state_restore_plan_accepts_verified_backup_set(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:00:00.000Z",
    )
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )

    plan = plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)

    assert plan.backup_set_id == "set-a"
    assert [entry.store.value for entry in plan.restore_files] == [
        "catalog",
        "recovery",
    ]
    assert [entry.target_path for entry in plan.restore_files] == [
        layout.catalog,
        layout.recovery,
    ]
    assert plan.backup_unresolved_target_intent_count == 1
    assert plan.current_unresolved_target_intent_count == 1
    assert plan.backup_target_intent_high_water_utc == "2026-07-30T12:00:00.000Z"
    assert plan.current_target_intent_high_water_utc == "2026-07-30T12:00:00.000Z"


def test_sqlite_state_restore_plan_blocks_newer_unresolved_target_intents(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:00:00.000Z",
    )
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-b",
        sequence=1,
        updated_utc="2026-07-30T12:01:00.000Z",
    )

    with pytest.raises(
        SqliteStateBackupViolation,
        match="STATE_RESTORE_BLOCKED_BY_NEWER_TARGET_INTENTS",
    ):
        plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)


def test_sqlite_state_restore_plan_blocks_same_timestamp_extra_target_intent(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:00:00.000Z",
    )
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-b",
        sequence=1,
        updated_utc="2026-07-30T12:00:00.000Z",
    )

    with pytest.raises(
        SqliteStateBackupViolation,
        match="STATE_RESTORE_BLOCKED_BY_NEWER_TARGET_INTENTS",
    ):
        plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)


def test_sqlite_state_restore_plan_blocks_newer_target_side_intent_marker(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    target_root = tmp_path / "target"
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    _insert_endpoint_revision(
        layout, target_root=target_root, owner_installation_id="owner-a"
    )
    _write_target_side_intent_marker(
        target_root,
        owner_installation_id="owner-a",
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:01:00.000Z",
    )

    with pytest.raises(
        SqliteStateBackupViolation,
        match="STATE_RESTORE_BLOCKED_BY_NEWER_TARGET_INTENTS",
    ):
        plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)


def test_sqlite_state_restore_plan_dedupes_matching_target_side_intent_marker(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    target_root = tmp_path / "target"
    _initialize_state_stores(layout)
    _insert_endpoint_revision(
        layout, target_root=target_root, owner_installation_id="owner-a"
    )
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:00:00.000Z",
    )
    _write_target_side_intent_marker(
        target_root,
        owner_installation_id="owner-a",
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:00:00.000Z",
    )
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )

    plan = plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)

    assert plan.backup_unresolved_target_intent_count == 1
    assert plan.current_unresolved_target_intent_count == 1
    assert plan.current_target_side_intent_marker_count == 1
    assert plan.current_target_side_intent_marker_high_water_utc == (
        "2026-07-30T12:00:00.000Z"
    )


def test_sqlite_state_restore_swap_restores_verified_pair_and_preserves_rollback(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    backup_versions = _read_user_versions(layout)
    _set_user_version(layout.catalog, 77)
    _set_user_version(layout.recovery, 88)
    assert _read_user_versions(layout) == (77, 88)
    plan = plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)
    _sqlite_sidecar_path(layout.recovery, "wal").write_bytes(b"stale-wal")
    _sqlite_sidecar_path(layout.recovery, "shm").write_bytes(b"stale-shm")
    assert _sqlite_sidecar_path(layout.recovery, "wal").is_file()

    receipt = apply_sqlite_state_restore_plan(
        plan,
        restore_epoch_id="restore-a",
        started_utc="2026-07-30T12:05:00Z",
    )

    epoch_dir = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-a"
    assert receipt.backup_set_id == "set-a"
    assert receipt.intent_path == epoch_dir / STATE_RESTORE_INTENT_FILENAME
    assert receipt.committed_path == epoch_dir / STATE_RESTORE_COMMITTED_FILENAME
    assert receipt.intent_path.is_file()
    assert receipt.committed_path.is_file()
    assert _read_user_versions(layout) == backup_versions
    assert [entry.store.value for entry in receipt.restored_files] == [
        "catalog",
        "recovery",
    ]
    assert all(entry.rollback_path is not None for entry in receipt.restored_files)
    assert [entry.target_path for entry in receipt.restored_files] == [
        layout.catalog,
        layout.recovery,
    ]
    assert not _sqlite_sidecar_path(layout.recovery, "wal").exists()
    assert not _sqlite_sidecar_path(layout.recovery, "shm").exists()
    assert (
        layout.recovery.with_name(".recovery.sqlite.restore-a.restore-rollback")
    ).is_file()
    assert (
        layout.recovery.with_name(".recovery.sqlite-wal.restore-a.restore-rollback")
    ).is_file()


def test_sqlite_state_restore_startup_reconciliation_reports_committed_epoch(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    plan = plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)

    receipt = apply_sqlite_state_restore_plan(
        plan,
        restore_epoch_id="restore-a",
        started_utc="2026-07-30T12:05:00Z",
    )

    report = reconcile_committed_sqlite_state_restore_epochs(layout)

    assert report.scanned_epoch_count == 1
    assert report.committed_epoch_count == 1
    assert report.previously_rolled_back_epoch_count == 0
    assert len(report.committed_epochs) == 1
    committed = report.committed_epochs[0]
    assert report.latest_committed_epoch == committed
    assert committed.restore_epoch_id == "restore-a"
    assert committed.backup_set_id == "set-a"
    assert committed.state_set_hash == receipt.state_set_hash
    assert committed.started_utc == "2026-07-30T12:05:00Z"
    assert committed.committed_path == receipt.committed_path
    assert report.to_payload()["latest_committed_epoch"] == committed.to_payload()


def test_sqlite_state_restore_swap_rolls_back_when_second_store_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    _set_user_version(layout.catalog, 77)
    _set_user_version(layout.recovery, 88)
    original_replace = Path.replace

    def flaky_replace(self: Path, target: str | Path) -> Path:
        target_path = Path(target)
        if self.name.startswith(".recovery.sqlite.restore-b.restore-new") and (
            target_path == layout.recovery
        ):
            raise OSError("simulated restore swap failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(SqliteStateBackupViolation, match="STATE_RESTORE_SWAP_FAILED"):
        restore_sqlite_state_backup_set(
            tmp_path / "state-backups" / "set-a",
            layout,
            restore_epoch_id="restore-b",
            started_utc="2026-07-30T12:05:00Z",
        )

    assert _read_user_versions(layout) == (77, 88)
    assert (
        layout.catalog.with_name(".catalog.sqlite.restore-b.restore-rollback").exists()
        is False
    )
    assert (
        layout.recovery.with_name(
            ".recovery.sqlite.restore-b.restore-rollback"
        ).exists()
        is False
    )


def test_sqlite_state_restore_startup_recovery_rolls_back_interrupted_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    _set_user_version(layout.catalog, 77)
    _set_user_version(layout.recovery, 88)
    plan = plan_sqlite_state_restore(tmp_path / "state-backups" / "set-a", layout)
    original_replace = Path.replace

    def interrupted_replace(self: Path, target: str | Path) -> Path:
        target_path = Path(target)
        if self.name.startswith(".recovery.sqlite.restore-c.restore-new") and (
            target_path == layout.recovery
        ):
            raise KeyboardInterrupt("simulated host crash")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", interrupted_replace)
    with pytest.raises(KeyboardInterrupt):
        apply_sqlite_state_restore_plan(
            plan,
            restore_epoch_id="restore-c",
            started_utc="2026-07-30T12:05:00Z",
        )
    monkeypatch.setattr(Path, "replace", original_replace)

    epoch_dir = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-c"
    assert (epoch_dir / STATE_RESTORE_INTENT_FILENAME).is_file()
    assert not (epoch_dir / STATE_RESTORE_COMMITTED_FILENAME).exists()
    assert layout.catalog.with_name(
        ".catalog.sqlite.restore-c.restore-rollback"
    ).is_file()
    assert layout.recovery.with_name(
        ".recovery.sqlite.restore-c.restore-rollback"
    ).is_file()
    assert layout.recovery.with_name(
        ".recovery.sqlite.restore-c.restore-new.tmp"
    ).is_file()

    report = recover_incomplete_sqlite_state_restore_epochs(
        layout,
        recovered_utc="2026-07-30T12:06:00Z",
    )

    assert report.scanned_epoch_count == 1
    assert report.committed_epoch_count == 0
    assert report.previously_rolled_back_epoch_count == 0
    assert len(report.recovered_epochs) == 1
    assert report.recovered_epochs[0].restore_epoch_id == "restore-c"
    assert report.recovered_epochs[0].rolled_back_store_count == 2
    assert report.recovered_epochs[0].removed_temp_file_count == 1
    assert (epoch_dir / STATE_RESTORE_ROLLED_BACK_FILENAME).is_file()
    assert not layout.catalog.with_name(
        ".catalog.sqlite.restore-c.restore-rollback"
    ).exists()
    assert not layout.recovery.with_name(
        ".recovery.sqlite.restore-c.restore-rollback"
    ).exists()
    assert not layout.recovery.with_name(
        ".recovery.sqlite.restore-c.restore-new.tmp"
    ).exists()
    assert _read_user_versions(layout) == (77, 88)

    startup_report = reconcile_committed_sqlite_state_restore_epochs(layout)

    assert startup_report.scanned_epoch_count == 1
    assert startup_report.committed_epoch_count == 0
    assert startup_report.previously_rolled_back_epoch_count == 1
    assert startup_report.latest_committed_epoch is None
    assert startup_report.committed_epochs == ()


def test_sqlite_state_restore_startup_recovery_ignores_committed_epoch(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    backup_versions = _read_user_versions(layout)
    _set_user_version(layout.catalog, 77)
    _set_user_version(layout.recovery, 88)
    restore_sqlite_state_backup_set(
        tmp_path / "state-backups" / "set-a",
        layout,
        restore_epoch_id="restore-d",
        started_utc="2026-07-30T12:05:00Z",
    )

    report = recover_incomplete_sqlite_state_restore_epochs(
        layout,
        recovered_utc="2026-07-30T12:06:00Z",
    )

    assert report.scanned_epoch_count == 1
    assert report.committed_epoch_count == 1
    assert report.recovered_epochs == ()
    assert _read_user_versions(layout) == backup_versions


def test_sqlite_state_compaction_epoch_swaps_verified_pair_and_preserves_rollback(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    _set_user_version(layout.catalog, 77)
    _set_user_version(layout.recovery, 88)

    receipt = compact_sqlite_state_stores(
        layout,
        compaction_epoch_id="compact-a",
        started_utc="2026-07-30T12:05:00Z",
    )

    epoch_dir = layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-a"
    assert receipt.compaction_epoch_id == "compact-a"
    assert len(receipt.state_set_hash) == 64
    assert receipt.intent_path == epoch_dir / STATE_COMPACTION_INTENT_FILENAME
    assert receipt.committed_path == epoch_dir / STATE_COMPACTION_COMMITTED_FILENAME
    assert receipt.intent_path.is_file()
    assert receipt.committed_path.is_file()
    assert [entry.store.value for entry in receipt.compacted_files] == [
        "catalog",
        "recovery",
    ]
    assert [entry.target_path for entry in receipt.compacted_files] == [
        layout.catalog,
        layout.recovery,
    ]
    assert all(entry.rollback_path is not None for entry in receipt.compacted_files)
    assert _read_user_versions(layout) == (77, 88)
    assert (
        layout.catalog.with_name(".catalog.sqlite.compact-a.compaction-rollback")
    ).is_file()
    assert (
        layout.recovery.with_name(".recovery.sqlite.compact-a.compaction-rollback")
    ).is_file()


def test_sqlite_state_compaction_recovery_rolls_back_interrupted_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    _set_user_version(layout.catalog, 77)
    _set_user_version(layout.recovery, 88)
    original_replace = Path.replace

    def interrupted_replace(self: Path, target: str | Path) -> Path:
        target_path = Path(target)
        if self.name.startswith(".recovery.sqlite.compact-b.compaction-new") and (
            target_path == layout.recovery
        ):
            raise KeyboardInterrupt("simulated host crash")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", interrupted_replace)
    with pytest.raises(KeyboardInterrupt):
        compact_sqlite_state_stores(
            layout,
            compaction_epoch_id="compact-b",
            started_utc="2026-07-30T12:05:00Z",
        )
    monkeypatch.setattr(Path, "replace", original_replace)

    epoch_dir = layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-b"
    assert (epoch_dir / STATE_COMPACTION_INTENT_FILENAME).is_file()
    assert not (epoch_dir / STATE_COMPACTION_COMMITTED_FILENAME).exists()
    assert layout.catalog.with_name(
        ".catalog.sqlite.compact-b.compaction-rollback"
    ).is_file()
    assert layout.recovery.with_name(
        ".recovery.sqlite.compact-b.compaction-rollback"
    ).is_file()
    assert layout.recovery.with_name(
        ".recovery.sqlite.compact-b.compaction-new.tmp"
    ).is_file()

    report = recover_incomplete_sqlite_state_compaction_epochs(
        layout,
        recovered_utc="2026-07-30T12:06:00Z",
    )

    assert report.scanned_epoch_count == 1
    assert report.committed_epoch_count == 0
    assert report.previously_rolled_back_epoch_count == 0
    assert len(report.recovered_epochs) == 1
    assert report.recovered_epochs[0].compaction_epoch_id == "compact-b"
    assert report.recovered_epochs[0].rolled_back_store_count == 2
    assert report.recovered_epochs[0].removed_temp_file_count == 1
    assert (epoch_dir / STATE_COMPACTION_ROLLED_BACK_FILENAME).is_file()
    assert not layout.catalog.with_name(
        ".catalog.sqlite.compact-b.compaction-rollback"
    ).exists()
    assert not layout.recovery.with_name(
        ".recovery.sqlite.compact-b.compaction-rollback"
    ).exists()
    assert not layout.recovery.with_name(
        ".recovery.sqlite.compact-b.compaction-new.tmp"
    ).exists()
    assert _read_user_versions(layout) == (77, 88)


def test_sqlite_state_maintenance_retention_deletes_only_unprotected_terminal_artifacts(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    backup_root = tmp_path / "state-backups"
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        backup_root,
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    _set_user_version(layout.catalog, 11)
    _set_user_version(layout.recovery, 12)
    create_sqlite_state_backup_set(
        layout,
        backup_root,
        backup_set_id="set-b",
        created_utc="2026-07-30T12:01:00Z",
    )
    _set_user_version(layout.catalog, 21)
    _set_user_version(layout.recovery, 22)
    restore_sqlite_state_backup_set(
        backup_root / "set-a",
        layout,
        restore_epoch_id="restore-old",
        started_utc="2026-07-30T12:02:00Z",
    )
    _set_user_version(layout.catalog, 31)
    _set_user_version(layout.recovery, 32)
    create_sqlite_state_backup_set(
        layout,
        backup_root,
        backup_set_id="set-c",
        created_utc="2026-07-30T12:03:00Z",
    )
    restore_sqlite_state_backup_set(
        backup_root / "set-b",
        layout,
        restore_epoch_id="restore-new",
        started_utc="2026-07-30T12:04:00Z",
    )
    compact_sqlite_state_stores(
        layout,
        compaction_epoch_id="compact-old",
        started_utc="2026-07-30T12:05:00Z",
    )
    compact_sqlite_state_stores(
        layout,
        compaction_epoch_id="compact-new",
        started_utc="2026-07-30T12:06:00Z",
    )
    old_restore_rollback = layout.catalog.with_name(
        ".catalog.sqlite.restore-old.restore-rollback"
    )
    new_restore_rollback = layout.catalog.with_name(
        ".catalog.sqlite.restore-new.restore-rollback"
    )
    old_compaction_rollback = layout.catalog.with_name(
        ".catalog.sqlite.compact-old.compaction-rollback"
    )
    new_compaction_rollback = layout.catalog.with_name(
        ".catalog.sqlite.compact-new.compaction-rollback"
    )
    assert old_restore_rollback.is_file()
    assert new_restore_rollback.is_file()
    assert old_compaction_rollback.is_file()
    assert new_compaction_rollback.is_file()

    result = apply_sqlite_state_maintenance_retention(
        layout,
        backup_root,
        policy=SqliteStateMaintenanceRetentionPolicy(
            keep_latest_backup_sets=1,
            keep_latest_restore_epochs=1,
            keep_latest_compaction_epochs=1,
        ),
    )

    deleted = {
        (artifact.artifact_type, artifact.artifact_id)
        for artifact in result.deleted_artifacts
    }
    retained = {
        (artifact.artifact_type, artifact.artifact_id)
        for artifact in result.plan.retained_artifacts
    }
    assert deleted == {
        ("backup_set", "set-a"),
        ("restore_epoch", "restore-old"),
        ("compaction_epoch", "compact-old"),
    }
    assert retained == {
        ("backup_set", "set-b"),
        ("backup_set", "set-c"),
        ("restore_epoch", "restore-new"),
        ("compaction_epoch", "compact-new"),
    }
    assert result.plan.protected_backup_set_ids == ("set-b",)
    assert not (backup_root / "set-a").exists()
    assert (backup_root / "set-b").is_dir()
    assert (backup_root / "set-c").is_dir()
    assert not (layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-old").exists()
    assert (layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-new").is_dir()
    assert not (layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-old").exists()
    assert (layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-new").is_dir()
    assert not old_restore_rollback.exists()
    assert new_restore_rollback.is_file()
    assert not old_compaction_rollback.exists()
    assert new_compaction_rollback.is_file()


def test_sqlite_state_maintenance_retention_skips_incomplete_and_malformed_artifacts(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    backup_root = tmp_path / "state-backups"
    _initialize_state_stores(layout)
    create_sqlite_state_backup_set(
        layout,
        backup_root,
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )
    (backup_root / "bad-set").mkdir()
    (layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-pending").mkdir(
        parents=True
    )
    (layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-pending").mkdir(
        parents=True
    )

    plan = plan_sqlite_state_maintenance_retention(
        layout,
        backup_root,
        policy=SqliteStateMaintenanceRetentionPolicy(
            keep_latest_backup_sets=1,
            keep_latest_restore_epochs=0,
            keep_latest_compaction_epochs=0,
        ),
    )

    skipped = {
        (artifact.artifact_type, artifact.artifact_id, artifact.reason)
        for artifact in plan.skipped_artifacts
    }
    assert skipped == {
        ("backup_set", "bad-set", "STATE_BACKUP_MANIFEST_MISSING"),
        (
            "restore_epoch",
            "restore-pending",
            "STATE_RETENTION_RESTORE_EPOCH_INCOMPLETE",
        ),
        (
            "compaction_epoch",
            "compact-pending",
            "STATE_RETENTION_COMPACTION_EPOCH_INCOMPLETE",
        ),
    }

    result = apply_sqlite_state_maintenance_retention(
        layout,
        backup_root,
        policy=plan.policy,
    )

    assert result.deleted_artifacts == ()
    assert (backup_root / "set-a").is_dir()
    assert (backup_root / "bad-set").is_dir()
    assert (layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-pending").is_dir()
    assert (layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-pending").is_dir()


def test_sqlite_state_restore_maintenance_admits_clean_state(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)

    admission = admit_sqlite_state_restore_maintenance(layout)

    assert admission.admitted is True
    assert admission.blockers == ()
    assert admission.active_run_count == 0
    assert admission.active_run_target_count == 0
    assert admission.non_terminal_command_receipt_count == 0
    assert admission.pending_outbox_message_count == 0
    assert admission.active_resource_lease_count == 0
    assert admission.unresolved_target_intent_segment_count == 0
    assert admission.incomplete_restore_epoch_count == 0
    assert admission.incomplete_compaction_epoch_count == 0
    assert admission.to_payload()["admitted"] is True


def test_sqlite_state_restore_maintenance_blocks_active_mutation_evidence(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    _insert_active_catalog_restore_maintenance_evidence(layout)
    _insert_active_recovery_intent_segment(
        layout,
        segment_id="segment-a",
        sequence=0,
        updated_utc="2026-07-30T12:00:00.000Z",
    )
    (layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / "restore-pending").mkdir(
        parents=True
    )
    (layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / "compact-pending").mkdir(
        parents=True
    )

    admission = admit_sqlite_state_restore_maintenance(layout)

    assert admission.admitted is False
    assert admission.active_run_count == 1
    assert admission.active_run_target_count == 1
    assert admission.non_terminal_command_receipt_count == 1
    assert admission.pending_outbox_message_count == 1
    assert admission.active_resource_lease_count == 1
    assert admission.unresolved_target_intent_segment_count == 1
    assert admission.incomplete_restore_epoch_count == 1
    assert admission.incomplete_compaction_epoch_count == 1
    assert {blocker.code for blocker in admission.blockers} == {
        "STATE_RESTORE_MAINTENANCE_ACTIVE_RUNS",
        "STATE_RESTORE_MAINTENANCE_ACTIVE_RUN_TARGETS",
        "STATE_RESTORE_MAINTENANCE_NON_TERMINAL_COMMAND_RECEIPTS",
        "STATE_RESTORE_MAINTENANCE_PENDING_OUTBOX_MESSAGES",
        "STATE_RESTORE_MAINTENANCE_ACTIVE_RESOURCE_LEASES",
        "STATE_RESTORE_MAINTENANCE_UNRESOLVED_TARGET_INTENTS",
        "STATE_RESTORE_MAINTENANCE_INCOMPLETE_RESTORE_EPOCHS",
        "STATE_RESTORE_MAINTENANCE_INCOMPLETE_COMPACTION_EPOCHS",
    }


def test_sqlite_state_backup_set_rejects_mixed_store_file_from_another_epoch(
    tmp_path: Path,
) -> None:
    layout = build_state_store_layout(tmp_path / "state")
    _initialize_state_stores(layout)
    manifest_a = create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-a",
        created_utc="2026-07-30T12:00:00Z",
    )

    with sqlite3.connect(layout.recovery) as connection:
        connection.execute("PRAGMA user_version = 77")
    create_sqlite_state_backup_set(
        layout,
        tmp_path / "state-backups",
        backup_set_id="set-b",
        created_utc="2026-07-30T12:01:00Z",
    )

    shutil.copyfile(
        tmp_path / "state-backups" / "set-b" / "recovery.sqlite.backup",
        tmp_path / "state-backups" / "set-a" / "recovery.sqlite.backup",
    )

    with pytest.raises(
        SqliteStateBackupViolation, match="STATE_BACKUP_CHECKSUM_MISMATCH"
    ):
        verify_sqlite_state_backup_set(
            tmp_path / "state-backups" / "set-a",
            manifest=manifest_a,
        )


def _initialize_state_stores(layout) -> None:
    layout.root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.catalog) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(layout.catalog)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
    with sqlite3.connect(layout.recovery) as connection:
        apply_sqlite_connection_policy(
            connection, recovery_writer_policy(layout.recovery)
        )
        apply_sqlite_migrations(connection, recovery_migration_plan())


def _set_user_version(database_path: Path, value: int) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA user_version = {value}")


def _read_user_versions(layout: StateStoreLayout) -> tuple[int, int]:
    return (_read_user_version(layout.catalog), _read_user_version(layout.recovery))


def _read_user_version(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def _sqlite_sidecar_path(database_path: Path, suffix: str) -> Path:
    return Path(f"{database_path}-{suffix}")


def _insert_endpoint_revision(
    layout: StateStoreLayout,
    *,
    target_root: Path,
    owner_installation_id: str,
) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.catalog) as connection:
        connection.execute("INSERT OR IGNORE INTO endpoints (id) VALUES ('target-a')")
        connection.execute(
            """
            INSERT INTO endpoint_revisions (
                endpoint_id,
                id,
                display_name,
                root_uri,
                owner_installation_id,
                ownership_epoch
            )
            VALUES (
                'target-a',
                'target-rev-a',
                'Target A',
                ?,
                ?,
                1
            )
            """,
            (target_root.as_uri(), owner_installation_id),
        )


def _write_target_side_intent_marker(
    target_root: Path,
    *,
    owner_installation_id: str,
    segment_id: str,
    sequence: int,
    updated_utc: str,
) -> Path:
    relative_path = (
        f"installations/{owner_installation_id}/recovery/run-a/"
        f"segment-{sequence:06d}.intent.jsonl"
    )
    marker_path = target_root / ".mediasync" / Path(relative_path)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "segment_id": segment_id,
        "run_id": "run-a",
        "run_target_id": "run-target-a",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": 1,
        "lease_id": "lease-a",
        "fencing_token": 1,
        "segment_sequence": sequence,
        "relative_path": relative_path,
        "operation_count": 1,
        "byte_count": 10,
        "segment_hash": f"{sequence + 1:064x}",
        "previous_segment_hash": None if sequence == 0 else f"{sequence:064x}",
        "durability_state": "DURABLE",
        "state": "DURABLE",
        "created_utc": updated_utc,
        "updated_utc": updated_utc,
    }
    marker_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return marker_path


def _insert_active_recovery_intent_segment(
    layout: StateStoreLayout,
    *,
    segment_id: str,
    sequence: int,
    updated_utc: str,
) -> None:
    with sqlite3.connect(layout.recovery) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO resource_leases (
                lease_id,
                resource_key,
                ownership_epoch,
                fencing_token,
                lease_mode,
                owner_instance_id,
                run_id,
                run_target_id,
                endpoint_id,
                endpoint_generation,
                os_lock_kind,
                state
            )
            VALUES (
                'lease-a',
                'endpoint:target-a',
                1,
                1,
                'EXCLUSIVE',
                'owner-a',
                'run-a',
                'run-target-a',
                'target-a',
                1,
                'LOCAL_OS_HANDLE',
                'ACQUIRED'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO recovery_intent_segments (
                id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                segment_sequence,
                relative_path,
                schema_version,
                operation_count,
                byte_count,
                segment_hash,
                previous_segment_hash,
                durability_state,
                state,
                created_utc,
                updated_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment_id,
                "run-a",
                "run-target-a",
                "target-a",
                "target-rev-a",
                1,
                "owner-a",
                1,
                "lease-a",
                1,
                sequence,
                f"installations/owner-a/recovery/run-a/segment-{sequence:06d}.intent.jsonl",
                1,
                1,
                10,
                f"{sequence + 1:064x}",
                None if sequence == 0 else f"{sequence:064x}",
                "DURABLE",
                "DURABLE",
                updated_utc,
                updated_utc,
            ),
        )


def _insert_active_catalog_restore_maintenance_evidence(
    layout: StateStoreLayout,
) -> None:
    with sqlite3.connect(layout.catalog) as connection:
        connection.execute(
            """
            INSERT INTO command_receipts (
                idempotency_key,
                request_id,
                client_instance_id,
                principal_fingerprint,
                command_name,
                payload_hash,
                protocol_version,
                schema_version,
                state,
                payload_hash_scope,
                payload_canonicalization_algorithm,
                payload_hash_algorithm
            )
            VALUES (
                'cmd-a',
                'request-a',
                'client-a',
                'principal-a',
                'START_RUN',
                ?,
                1,
                1,
                'RUNNING',
                'COMMAND_PAYLOAD',
                'RFC8785',
                'SHA-256'
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO outbox_messages (
                id,
                message_type,
                aggregate_type,
                aggregate_id,
                idempotency_key,
                payload_json,
                payload_hash,
                state
            )
            VALUES (
                'outbox-a',
                'COMMAND_EFFECT_ACCEPTED',
                'command',
                'cmd-a',
                'outbox-key-a',
                '{}',
                ?,
                'PENDING'
            )
            """,
            ("b" * 64,),
        )
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
                planned_bytes
            )
            VALUES (
                'run-a',
                'job-a',
                'job-rev-a',
                'plan-a',
                'request-a',
                'group-a',
                'MANUAL_LOCAL_PREVIEW',
                'EXECUTING',
                '{}',
                '0B-dev',
                ?,
                'run-key-a',
                1,
                10
            )
            """,
            ("c" * 64,),
        )
        connection.execute(
            """
            INSERT INTO run_targets (
                id,
                run_id,
                endpoint_id,
                endpoint_revision_id,
                state
            )
            VALUES (
                'run-target-a',
                'run-a',
                'target-a',
                'target-rev-a',
                'EXECUTING'
            )
            """
        )
