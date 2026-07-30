from __future__ import annotations

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
    SqliteStateBackupViolation,
    create_sqlite_state_backup_set,
    load_sqlite_state_backup_manifest,
    plan_sqlite_state_restore,
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
    assert [entry.schema_version for entry in manifest.stores] == [22, 5]
    assert [entry.migration_count for entry in manifest.stores] == [22, 5]
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
    assert [entry.store.value for entry in plan.restore_files] == ["catalog", "recovery"]
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

    with pytest.raises(SqliteStateBackupViolation, match="STATE_BACKUP_CHECKSUM_MISMATCH"):
        verify_sqlite_state_backup_set(
            tmp_path / "state-backups" / "set-a",
            manifest=manifest_a,
        )


def _initialize_state_stores(layout) -> None:
    layout.root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.catalog) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(layout.catalog))
        apply_sqlite_migrations(connection, catalog_migration_plan())
    with sqlite3.connect(layout.recovery) as connection:
        apply_sqlite_connection_policy(connection, recovery_writer_policy(layout.recovery))
        apply_sqlite_migrations(connection, recovery_migration_plan())


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
