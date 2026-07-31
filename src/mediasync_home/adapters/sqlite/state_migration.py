from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteConnectionPolicy,
    SqliteStore,
    StateStoreLayout,
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    catalog_reader_policy,
    recovery_reader_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigrationPlan,
    SqliteMigrationState,
    apply_sqlite_migrations,
    inspect_sqlite_migration_state,
    migration_checksum,
)
from mediasync_home.adapters.sqlite.state_backup import (
    SqliteStateBackupManifest,
    SqliteStateBackupViolation,
    create_sqlite_state_backup_set,
    verify_sqlite_state_backup_set,
)


STATE_MIGRATION_EPOCH_SCHEMA_VERSION = 1
STATE_MIGRATION_EPOCHS_DIR_NAME = "state-migration-epochs"
STATE_MIGRATION_INTENT_FILENAME = "state-migration.intent.json"
STATE_MIGRATION_COMMITTED_FILENAME = "state-migration.committed.json"
STATE_MIGRATION_BACKUP_ROOT_NAME = "backup-sets"
STATE_MIGRATION_BACKUP_SET_ID = "pre-migration"
STATE_MIGRATION_CONTROL_FILE_LIMIT_BYTES = 256 * 1024
STATE_MIGRATION_EPOCH_SCAN_LIMIT = 1_000
STATE_MIGRATION_CHAIN_LIMIT = 16
STATE_MIGRATION_EPOCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_STORE_ORDER = (SqliteStore.CATALOG, SqliteStore.RECOVERY)
_STORE_PHASES = ("PENDING", "VERIFIED")
_BACKUP_STATUSES = ("NOT_REQUIRED", "PENDING", "VERIFIED")


class SqliteStateMigrationViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SqliteStoreMigrationResult:
    store: SqliteStore
    initial_version: int
    final_version: int
    target_version: int

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "initial_version": self.initial_version,
            "final_version": self.final_version,
            "target_version": self.target_version,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateMigrationReport:
    migration_performed: bool
    previously_committed_epoch_count: int
    resumed_epoch_count: int
    created_epoch_count: int
    committed_epoch_ids: tuple[str, ...]
    latest_backup_set_path: Path | None
    latest_backup_state_set_hash: str | None
    stores: tuple[SqliteStoreMigrationResult, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "migration_performed": self.migration_performed,
            "previously_committed_epoch_count": self.previously_committed_epoch_count,
            "resumed_epoch_count": self.resumed_epoch_count,
            "created_epoch_count": self.created_epoch_count,
            "committed_epoch_ids": list(self.committed_epoch_ids),
            "latest_backup_set_path": (
                None
                if self.latest_backup_set_path is None
                else str(self.latest_backup_set_path)
            ),
            "latest_backup_state_set_hash": self.latest_backup_state_set_hash,
            "stores": [store.to_payload() for store in self.stores],
        }


@dataclass(frozen=True, slots=True)
class _StoreIntent:
    store: SqliteStore
    before_version: int
    target_version: int
    before_initialized: bool
    phase: str

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "before_version": self.before_version,
            "target_version": self.target_version,
            "before_initialized": self.before_initialized,
            "phase": self.phase,
        }


@dataclass(frozen=True, slots=True)
class _MigrationIntent:
    epoch_id: str
    started_utc: str
    app_version: str
    plan_hash: str
    intent_revision: int
    root_path: Path
    catalog_path: Path
    recovery_path: Path
    backup_required: bool
    backup_status: str
    backup_root: Path
    backup_set_path: Path
    backup_state_set_hash: str | None
    stores: tuple[_StoreIntent, ...]
    intent_path: Path

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": STATE_MIGRATION_EPOCH_SCHEMA_VERSION,
            "epoch_id": self.epoch_id,
            "started_utc": self.started_utc,
            "app_version": self.app_version,
            "plan_hash": self.plan_hash,
            "intent_revision": self.intent_revision,
            "layout": {
                "root": str(self.root_path),
                "catalog": str(self.catalog_path),
                "recovery": str(self.recovery_path),
            },
            "backup": {
                "required": self.backup_required,
                "status": self.backup_status,
                "root": str(self.backup_root),
                "set_id": STATE_MIGRATION_BACKUP_SET_ID,
                "set_path": str(self.backup_set_path),
                "state_set_hash": self.backup_state_set_hash,
            },
            "stores": [store.to_payload() for store in self.stores],
        }


@dataclass(frozen=True, slots=True)
class _EpochScan:
    scanned_epoch_count: int
    committed_epoch_count: int
    pending_intent: _MigrationIntent | None


@dataclass(frozen=True, slots=True)
class _EpochExecution:
    epoch_id: str
    backup_set_path: Path | None
    backup_state_set_hash: str | None


def migrate_sqlite_state_stores(
    layout: StateStoreLayout,
    *,
    catalog_plan: SqliteMigrationPlan,
    recovery_plan: SqliteMigrationPlan,
    app_version: str,
    started_utc: str,
    completed_utc: str,
    epoch_id_factory: Callable[[], str] | None = None,
    after_store_migrated: Callable[[SqliteStore], None] | None = None,
) -> SqliteStateMigrationReport:
    plans = _validated_plans(catalog_plan, recovery_plan)
    _validate_layout(layout)
    layout.root.mkdir(parents=True, exist_ok=True)
    initial_states = _inspect_all(layout, plans)
    previous_committed_count: int | None = None
    resumed_count = 0
    created_count = 0
    committed_ids: list[str] = []
    latest_backup_set_path: Path | None = None
    latest_backup_state_set_hash: str | None = None
    make_epoch_id = epoch_id_factory or (lambda: str(uuid4()))

    for _ in range(STATE_MIGRATION_CHAIN_LIMIT):
        scan = _scan_epochs(layout, plans)
        if previous_committed_count is None:
            previous_committed_count = scan.committed_epoch_count
        intent = scan.pending_intent
        resumed = intent is not None
        if intent is None:
            states = _inspect_all(layout, plans)
            if _all_at_target(states):
                return _migration_report(
                    initial_states=initial_states,
                    final_states=states,
                    previous_committed_count=previous_committed_count,
                    resumed_count=resumed_count,
                    created_count=created_count,
                    committed_ids=tuple(committed_ids),
                    latest_backup_set_path=latest_backup_set_path,
                    latest_backup_state_set_hash=latest_backup_state_set_hash,
                )
            intent = _create_epoch_intent(
                layout,
                plans,
                states,
                epoch_id=make_epoch_id(),
                app_version=app_version,
                started_utc=started_utc,
            )
            created_count += 1
        else:
            resumed_count += 1

        execution = _execute_epoch(
            layout,
            plans,
            intent,
            completed_utc=completed_utc,
            after_store_migrated=after_store_migrated,
        )
        committed_ids.append(execution.epoch_id)
        if execution.backup_set_path is not None:
            latest_backup_set_path = execution.backup_set_path
            latest_backup_state_set_hash = execution.backup_state_set_hash
        if resumed:
            after_store_migrated = None

    raise SqliteStateMigrationViolation("STATE_MIGRATION_CHAIN_LIMIT_EXCEEDED")


def _validated_plans(
    catalog_plan: SqliteMigrationPlan,
    recovery_plan: SqliteMigrationPlan,
) -> dict[SqliteStore, SqliteMigrationPlan]:
    if catalog_plan.store is not SqliteStore.CATALOG:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_CATALOG_PLAN_INVALID")
    if recovery_plan.store is not SqliteStore.RECOVERY:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_RECOVERY_PLAN_INVALID")
    return {
        SqliteStore.CATALOG: catalog_plan,
        SqliteStore.RECOVERY: recovery_plan,
    }


def _validate_layout(layout: StateStoreLayout) -> None:
    for path in (layout.root, layout.catalog, layout.recovery):
        if not path.is_absolute() or str(path).startswith("\\\\"):
            raise SqliteStateMigrationViolation("STATE_MIGRATION_LAYOUT_MUST_BE_LOCAL_ABSOLUTE")
    if layout.catalog != layout.root / "catalog.sqlite":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_CATALOG_PATH_INVALID")
    if layout.recovery != layout.root / "recovery.sqlite":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_RECOVERY_PATH_INVALID")
    if layout.catalog == layout.recovery:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORES_MUST_BE_SEPARATE")


def _inspect_all(
    layout: StateStoreLayout,
    plans: dict[SqliteStore, SqliteMigrationPlan],
) -> dict[SqliteStore, SqliteMigrationState]:
    return {
        store: _inspect_store(_store_path(layout, store), plans[store])
        for store in _STORE_ORDER
    }


def _inspect_store(path: Path, plan: SqliteMigrationPlan) -> SqliteMigrationState:
    if not path.exists():
        return SqliteMigrationState(
            store=plan.store,
            current_version=0,
            target_version=len(plan.migrations),
            initialized=False,
            checksum_backfill_required=False,
        )
    if path.is_symlink() or not path.is_file():
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_PATH_INVALID")
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            apply_sqlite_connection_policy(connection, _reader_policy(plan.store, path))
            return inspect_sqlite_migration_state(connection, plan)
    except (sqlite3.Error, ValueError) as exc:
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{plan.store.value.upper()}_PREFLIGHT_FAILED"
        ) from exc


def _all_at_target(states: dict[SqliteStore, SqliteMigrationState]) -> bool:
    return all(
        state.current_version == state.target_version
        and state.initialized
        and not state.checksum_backfill_required
        for state in states.values()
    )


def _scan_epochs(
    layout: StateStoreLayout,
    plans: dict[SqliteStore, SqliteMigrationPlan],
) -> _EpochScan:
    root = layout.root / STATE_MIGRATION_EPOCHS_DIR_NAME
    if not root.exists():
        return _EpochScan(0, 0, None)
    if root.is_symlink() or not root.is_dir():
        raise SqliteStateMigrationViolation("STATE_MIGRATION_EPOCH_ROOT_INVALID")
    entries = sorted(root.iterdir(), key=lambda path: path.name)
    if len(entries) > STATE_MIGRATION_EPOCH_SCAN_LIMIT:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_EPOCH_SCAN_LIMIT_EXCEEDED")
    committed_count = 0
    pending: list[_MigrationIntent] = []
    for epoch_dir in entries:
        _validate_epoch_dir(epoch_dir, expected_parent=root)
        intent_path = epoch_dir / STATE_MIGRATION_INTENT_FILENAME
        if not intent_path.exists():
            _remove_unpublished_epoch(epoch_dir)
            continue
        intent = _load_intent(
            intent_path,
            layout=layout,
            plans=plans,
        )
        committed_path = epoch_dir / STATE_MIGRATION_COMMITTED_FILENAME
        if committed_path.exists():
            _validate_committed_marker(committed_path, intent)
            committed_count += 1
        else:
            pending.append(intent)
    if len(pending) > 1:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_MULTIPLE_PENDING_EPOCHS")
    return _EpochScan(
        scanned_epoch_count=committed_count + len(pending),
        committed_epoch_count=committed_count,
        pending_intent=None if not pending else pending[0],
    )


def _remove_unpublished_epoch(epoch_dir: Path) -> None:
    temp_path = epoch_dir / f".{STATE_MIGRATION_INTENT_FILENAME}.tmp"
    children = list(epoch_dir.iterdir())
    if any(child != temp_path for child in children):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_INTENT_MISSING")
    if temp_path.exists():
        if temp_path.is_symlink() or not temp_path.is_file():
            raise SqliteStateMigrationViolation("STATE_MIGRATION_CONTROL_TEMP_INVALID")
        try:
            temp_path.unlink()
        except OSError as exc:
            raise SqliteStateMigrationViolation(
                "STATE_MIGRATION_UNPUBLISHED_EPOCH_CLEANUP_FAILED"
            ) from exc
    try:
        epoch_dir.rmdir()
        _fsync_directory(epoch_dir.parent)
    except OSError as exc:
        raise SqliteStateMigrationViolation(
            "STATE_MIGRATION_UNPUBLISHED_EPOCH_CLEANUP_FAILED"
        ) from exc


def _validate_epoch_dir(epoch_dir: Path, *, expected_parent: Path) -> None:
    if epoch_dir.parent != expected_parent:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_EPOCH_PATH_INVALID")
    _validate_epoch_id(epoch_dir.name)
    if epoch_dir.is_symlink() or not epoch_dir.is_dir():
        raise SqliteStateMigrationViolation("STATE_MIGRATION_EPOCH_DIRECTORY_INVALID")


def _create_epoch_intent(
    layout: StateStoreLayout,
    plans: dict[SqliteStore, SqliteMigrationPlan],
    states: dict[SqliteStore, SqliteMigrationState],
    *,
    epoch_id: str,
    app_version: str,
    started_utc: str,
) -> _MigrationIntent:
    _validate_epoch_id(epoch_id)
    if not app_version.strip() or not started_utc.strip():
        raise SqliteStateMigrationViolation("STATE_MIGRATION_METADATA_INVALID")
    initialized = {state.initialized for state in states.values()}
    if len(initialized) != 1:
        raise SqliteStateMigrationViolation(
            "STATE_MIGRATION_PARTIAL_INITIALIZATION_WITHOUT_EPOCH"
        )
    epochs_root = layout.root / STATE_MIGRATION_EPOCHS_DIR_NAME
    epochs_root.mkdir(parents=True, exist_ok=True)
    epoch_dir = epochs_root / epoch_id
    try:
        epoch_dir.mkdir()
    except FileExistsError as exc:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_EPOCH_ALREADY_EXISTS") from exc
    backup_required = all(state.initialized for state in states.values())
    backup_root = epoch_dir / STATE_MIGRATION_BACKUP_ROOT_NAME
    backup_set_path = backup_root / STATE_MIGRATION_BACKUP_SET_ID
    stores = tuple(
        _StoreIntent(
            store=store,
            before_version=states[store].current_version,
            target_version=states[store].target_version,
            before_initialized=states[store].initialized,
            phase=(
                "VERIFIED"
                if states[store].current_version == states[store].target_version
                and not states[store].checksum_backfill_required
                else "PENDING"
            ),
        )
        for store in _STORE_ORDER
    )
    intent = _MigrationIntent(
        epoch_id=epoch_id,
        started_utc=started_utc,
        app_version=app_version,
        plan_hash=_plan_hash(plans, stores),
        intent_revision=1,
        root_path=layout.root,
        catalog_path=layout.catalog,
        recovery_path=layout.recovery,
        backup_required=backup_required,
        backup_status="PENDING" if backup_required else "NOT_REQUIRED",
        backup_root=backup_root,
        backup_set_path=backup_set_path,
        backup_state_set_hash=None,
        stores=stores,
        intent_path=epoch_dir / STATE_MIGRATION_INTENT_FILENAME,
    )
    _atomic_write_json(intent.intent_path, intent.to_payload(), replace_existing=False)
    return intent


def _execute_epoch(
    layout: StateStoreLayout,
    plans: dict[SqliteStore, SqliteMigrationPlan],
    intent: _MigrationIntent,
    *,
    completed_utc: str,
    after_store_migrated: Callable[[SqliteStore], None] | None,
) -> _EpochExecution:
    current = _inspect_all(layout, plans)
    _validate_current_versions(intent, current)
    intent = _ensure_backup(layout, intent, current)
    for store in _STORE_ORDER:
        store_intent = _store_intent(intent, store)
        state = _inspect_store(_store_path(layout, store), plans[store])
        _validate_store_resume_state(store_intent, state)
        if store_intent.phase == "VERIFIED":
            continue
        if (
            state.current_version < store_intent.target_version
            or state.checksum_backfill_required
            or not state.initialized
        ):
            _migrate_one_store(
                path=_store_path(layout, store),
                plan=_plan_prefix(plans[store], store_intent.target_version),
            )
        _verify_store_target(
            path=_store_path(layout, store),
            plan=_plan_prefix(plans[store], store_intent.target_version),
            target_version=store_intent.target_version,
        )
        intent = _mark_store_verified(intent, store)
        if after_store_migrated is not None:
            after_store_migrated(store)

    final_states = _inspect_all(layout, plans)
    for store_intent in intent.stores:
        state = final_states[store_intent.store]
        if state.current_version != store_intent.target_version:
            raise SqliteStateMigrationViolation("STATE_MIGRATION_FINAL_VERSION_MISMATCH")
        if state.checksum_backfill_required or not state.initialized:
            raise SqliteStateMigrationViolation("STATE_MIGRATION_FINAL_STATE_INVALID")
    if not completed_utc.strip():
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMPLETED_UTC_INVALID")
    committed_path = intent.intent_path.parent / STATE_MIGRATION_COMMITTED_FILENAME
    _atomic_write_json(
        committed_path,
        {
            "schema_version": STATE_MIGRATION_EPOCH_SCHEMA_VERSION,
            "status": "COMMITTED",
            "epoch_id": intent.epoch_id,
            "completed_utc": completed_utc,
            "intent_hash": _payload_hash(intent.to_payload()),
            "plan_hash": intent.plan_hash,
            "backup_state_set_hash": intent.backup_state_set_hash,
            "stores": [
                {
                    "store": store.value,
                    "final_version": final_states[store].current_version,
                }
                for store in _STORE_ORDER
            ],
        },
        replace_existing=False,
    )
    return _EpochExecution(
        epoch_id=intent.epoch_id,
        backup_set_path=intent.backup_set_path if intent.backup_required else None,
        backup_state_set_hash=intent.backup_state_set_hash,
    )


def _validate_current_versions(
    intent: _MigrationIntent,
    states: dict[SqliteStore, SqliteMigrationState],
) -> None:
    for store_intent in intent.stores:
        state = states[store_intent.store]
        if state.current_version < store_intent.before_version:
            raise SqliteStateMigrationViolation("STATE_MIGRATION_VERSION_REGRESSED")
        if state.current_version > store_intent.target_version:
            raise SqliteStateMigrationViolation("STATE_MIGRATION_VERSION_EXCEEDS_EPOCH_TARGET")


def _ensure_backup(
    layout: StateStoreLayout,
    intent: _MigrationIntent,
    states: dict[SqliteStore, SqliteMigrationState],
) -> _MigrationIntent:
    if not intent.backup_required:
        if intent.backup_status != "NOT_REQUIRED":
            raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_STATUS_INVALID")
        return intent
    if intent.backup_status == "VERIFIED":
        manifest = _verify_epoch_backup(intent)
        if manifest.state_set_hash != intent.backup_state_set_hash:
            raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_HASH_MISMATCH")
        return intent
    if intent.backup_status != "PENDING":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_STATUS_INVALID")
    if any(
        states[store_intent.store].current_version != store_intent.before_version
        for store_intent in intent.stores
    ):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_NOT_VERIFIED")
    if intent.backup_root.exists():
        manifest_path = intent.backup_set_path / "backup-set.manifest.json"
        if manifest_path.exists():
            manifest = _verify_epoch_backup(intent)
        else:
            _remove_incomplete_backup_root(intent)
            manifest = _create_epoch_backup(layout, intent)
    else:
        manifest = _create_epoch_backup(layout, intent)
    updated = replace(
        intent,
        backup_status="VERIFIED",
        backup_state_set_hash=manifest.state_set_hash,
        intent_revision=intent.intent_revision + 1,
    )
    _atomic_write_json(updated.intent_path, updated.to_payload(), replace_existing=True)
    return updated


def _create_epoch_backup(
    layout: StateStoreLayout,
    intent: _MigrationIntent,
) -> SqliteStateBackupManifest:
    try:
        manifest = create_sqlite_state_backup_set(
            layout,
            intent.backup_root,
            backup_set_id=STATE_MIGRATION_BACKUP_SET_ID,
            created_utc=intent.started_utc,
        )
        return verify_sqlite_state_backup_set(intent.backup_set_path, manifest=manifest)
    except SqliteStateBackupViolation as exc:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_FAILED") from exc


def _verify_epoch_backup(intent: _MigrationIntent) -> SqliteStateBackupManifest:
    try:
        return verify_sqlite_state_backup_set(intent.backup_set_path)
    except SqliteStateBackupViolation as exc:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_INVALID") from exc


def _remove_incomplete_backup_root(intent: _MigrationIntent) -> None:
    epoch_dir = intent.intent_path.parent
    if intent.backup_root != epoch_dir / STATE_MIGRATION_BACKUP_ROOT_NAME:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_ROOT_INVALID")
    if intent.backup_root.is_symlink() or not intent.backup_root.is_dir():
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_ROOT_INVALID")
    shutil.rmtree(intent.backup_root)


def _migrate_one_store(*, path: Path, plan: SqliteMigrationPlan) -> None:
    try:
        with sqlite3.connect(path) as connection:
            apply_sqlite_connection_policy(connection, _writer_policy(plan.store, path))
            apply_sqlite_migrations(connection, plan)
            _verify_sqlite_integrity(connection)
    except (sqlite3.Error, ValueError) as exc:
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{plan.store.value.upper()}_APPLY_FAILED"
        ) from exc


def _verify_store_target(
    *,
    path: Path,
    plan: SqliteMigrationPlan,
    target_version: int,
) -> None:
    state = _inspect_store(path, plan)
    if (
        not state.initialized
        or state.current_version != target_version
        or state.checksum_backfill_required
    ):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_VERIFY_FAILED")
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            apply_sqlite_connection_policy(connection, _reader_policy(plan.store, path))
            _verify_sqlite_integrity(connection)
    except (sqlite3.Error, ValueError) as exc:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_VERIFY_FAILED") from exc


def _verify_sqlite_integrity(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA quick_check").fetchone()
    if row is None or str(row[0]).lower() != "ok":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_QUICK_CHECK_FAILED")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_FOREIGN_KEY_CHECK_FAILED")


def _validate_store_resume_state(
    store_intent: _StoreIntent,
    state: SqliteMigrationState,
) -> None:
    if state.current_version < store_intent.before_version:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_VERSION_REGRESSED")
    if state.current_version > store_intent.target_version:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_VERSION_TOO_NEW")
    if store_intent.phase == "VERIFIED" and (
        not state.initialized
        or state.current_version != store_intent.target_version
        or state.checksum_backfill_required
    ):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_VERIFIED_STORE_DRIFT")


def _mark_store_verified(
    intent: _MigrationIntent,
    store: SqliteStore,
) -> _MigrationIntent:
    stores = tuple(
        replace(entry, phase="VERIFIED") if entry.store is store else entry
        for entry in intent.stores
    )
    updated = replace(
        intent,
        stores=stores,
        intent_revision=intent.intent_revision + 1,
    )
    _atomic_write_json(updated.intent_path, updated.to_payload(), replace_existing=True)
    return updated


def _load_intent(
    path: Path,
    *,
    layout: StateStoreLayout,
    plans: dict[SqliteStore, SqliteMigrationPlan],
) -> _MigrationIntent:
    payload = _read_json_object(path, "STATE_MIGRATION_INTENT")
    if _int_field(payload, "schema_version") != STATE_MIGRATION_EPOCH_SCHEMA_VERSION:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_INTENT_SCHEMA_UNSUPPORTED")
    epoch_id = _str_field(payload, "epoch_id")
    _validate_epoch_id(epoch_id)
    if path.parent.name != epoch_id:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_INTENT_EPOCH_MISMATCH")
    layout_payload = _mapping_field(payload, "layout")
    root_path = Path(_str_field(layout_payload, "root"))
    catalog_path = Path(_str_field(layout_payload, "catalog"))
    recovery_path = Path(_str_field(layout_payload, "recovery"))
    if (
        root_path != layout.root
        or catalog_path != layout.catalog
        or recovery_path != layout.recovery
    ):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_INTENT_LAYOUT_MISMATCH")
    backup_payload = _mapping_field(payload, "backup")
    backup_required = _bool_field(backup_payload, "required")
    backup_status = _str_field(backup_payload, "status")
    if backup_status not in _BACKUP_STATUSES:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_STATUS_INVALID")
    backup_root = Path(_str_field(backup_payload, "root"))
    backup_set_id = _str_field(backup_payload, "set_id")
    backup_set_path = Path(_str_field(backup_payload, "set_path"))
    expected_backup_root = path.parent / STATE_MIGRATION_BACKUP_ROOT_NAME
    if (
        backup_set_id != STATE_MIGRATION_BACKUP_SET_ID
        or backup_root != expected_backup_root
        or backup_set_path != expected_backup_root / STATE_MIGRATION_BACKUP_SET_ID
    ):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_PATH_MISMATCH")
    backup_hash = _optional_hex_field(backup_payload, "state_set_hash")
    if backup_status == "VERIFIED" and backup_hash is None:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_HASH_MISSING")
    if backup_status != "VERIFIED" and backup_hash is not None:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_HASH_UNEXPECTED")
    stores_payload = payload.get("stores")
    if not isinstance(stores_payload, list):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORES_INVALID")
    stores = tuple(_store_intent_from_payload(item) for item in stores_payload)
    if tuple(store.store for store in stores) != _STORE_ORDER:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_ORDER_INVALID")
    plan_hash = _str_field(payload, "plan_hash")
    if plan_hash != _plan_hash(plans, stores):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_PLAN_HASH_MISMATCH")
    if backup_required != all(store.before_initialized for store in stores):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_REQUIREMENT_INVALID")
    if not backup_required and backup_status != "NOT_REQUIRED":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_BACKUP_STATUS_INVALID")
    return _MigrationIntent(
        epoch_id=epoch_id,
        started_utc=_str_field(payload, "started_utc"),
        app_version=_str_field(payload, "app_version"),
        plan_hash=plan_hash,
        intent_revision=_positive_int_field(payload, "intent_revision"),
        root_path=root_path,
        catalog_path=catalog_path,
        recovery_path=recovery_path,
        backup_required=backup_required,
        backup_status=backup_status,
        backup_root=backup_root,
        backup_set_path=backup_set_path,
        backup_state_set_hash=backup_hash,
        stores=stores,
        intent_path=path,
    )


def _store_intent_from_payload(payload: object) -> _StoreIntent:
    if not isinstance(payload, dict):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_INTENT_INVALID")
    try:
        store = SqliteStore(_str_field(payload, "store"))
    except ValueError as exc:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_INVALID") from exc
    before_version = _non_negative_int_field(payload, "before_version")
    target_version = _positive_int_field(payload, "target_version")
    if before_version > target_version:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_VERSION_RANGE_INVALID")
    phase = _str_field(payload, "phase")
    if phase not in _STORE_PHASES:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_PHASE_INVALID")
    return _StoreIntent(
        store=store,
        before_version=before_version,
        target_version=target_version,
        before_initialized=_bool_field(payload, "before_initialized"),
        phase=phase,
    )


def _validate_committed_marker(path: Path, intent: _MigrationIntent) -> None:
    payload = _read_json_object(path, "STATE_MIGRATION_COMMITTED")
    if _int_field(payload, "schema_version") != STATE_MIGRATION_EPOCH_SCHEMA_VERSION:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_SCHEMA_UNSUPPORTED")
    if _str_field(payload, "status") != "COMMITTED":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_STATUS_INVALID")
    if _str_field(payload, "epoch_id") != intent.epoch_id:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_EPOCH_MISMATCH")
    if _hex_field(payload, "intent_hash") != _payload_hash(intent.to_payload()):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_INTENT_HASH_MISMATCH")
    if _hex_field(payload, "plan_hash") != intent.plan_hash:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_PLAN_HASH_MISMATCH")
    _str_field(payload, "completed_utc")
    if any(store.phase != "VERIFIED" for store in intent.stores):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_STORE_NOT_VERIFIED")
    if intent.backup_required and intent.backup_status != "VERIFIED":
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_BACKUP_NOT_VERIFIED")
    if (
        _optional_hex_field(payload, "backup_state_set_hash")
        != intent.backup_state_set_hash
    ):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_BACKUP_HASH_MISMATCH")
    stores_payload = payload.get("stores")
    if not isinstance(stores_payload, list):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_STORES_INVALID")
    final_versions: list[tuple[SqliteStore, int]] = []
    for store_payload in stores_payload:
        if not isinstance(store_payload, dict):
            raise SqliteStateMigrationViolation("STATE_MIGRATION_COMMITTED_STORE_INVALID")
        try:
            store = SqliteStore(_str_field(store_payload, "store"))
        except ValueError as exc:
            raise SqliteStateMigrationViolation(
                "STATE_MIGRATION_COMMITTED_STORE_INVALID"
            ) from exc
        final_versions.append(
            (store, _positive_int_field(store_payload, "final_version"))
        )
    expected_versions = [
        (store.store, store.target_version)
        for store in intent.stores
    ]
    if final_versions != expected_versions:
        raise SqliteStateMigrationViolation(
            "STATE_MIGRATION_COMMITTED_STORE_VERSION_MISMATCH"
        )


def _plan_prefix(plan: SqliteMigrationPlan, target_version: int) -> SqliteMigrationPlan:
    if target_version < 1 or target_version > len(plan.migrations):
        raise SqliteStateMigrationViolation("STATE_MIGRATION_TARGET_VERSION_UNSUPPORTED")
    return SqliteMigrationPlan(
        store=plan.store,
        migrations=plan.migrations[:target_version],
    )


def _plan_hash(
    plans: dict[SqliteStore, SqliteMigrationPlan],
    stores: tuple[_StoreIntent, ...],
) -> str:
    payload: dict[str, object] = {
        "stores": [
            {
                "store": store_intent.store.value,
                "target_version": store_intent.target_version,
                "migrations": [
                    {
                        "version": migration.version,
                        "name": migration.name,
                        "checksum": migration_checksum(migration),
                    }
                    for migration in _plan_prefix(
                        plans[store_intent.store],
                        store_intent.target_version,
                    ).migrations
                ],
            }
            for store_intent in stores
        ]
    }
    return _payload_hash(payload)


def _migration_report(
    *,
    initial_states: dict[SqliteStore, SqliteMigrationState],
    final_states: dict[SqliteStore, SqliteMigrationState],
    previous_committed_count: int,
    resumed_count: int,
    created_count: int,
    committed_ids: tuple[str, ...],
    latest_backup_set_path: Path | None,
    latest_backup_state_set_hash: str | None,
) -> SqliteStateMigrationReport:
    return SqliteStateMigrationReport(
        migration_performed=bool(committed_ids),
        previously_committed_epoch_count=previous_committed_count,
        resumed_epoch_count=resumed_count,
        created_epoch_count=created_count,
        committed_epoch_ids=committed_ids,
        latest_backup_set_path=latest_backup_set_path,
        latest_backup_state_set_hash=latest_backup_state_set_hash,
        stores=tuple(
            SqliteStoreMigrationResult(
                store=store,
                initial_version=initial_states[store].current_version,
                final_version=final_states[store].current_version,
                target_version=final_states[store].target_version,
            )
            for store in _STORE_ORDER
        ),
    )


def _store_intent(intent: _MigrationIntent, store: SqliteStore) -> _StoreIntent:
    for entry in intent.stores:
        if entry.store is store:
            return entry
    raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_INTENT_MISSING")


def _store_path(layout: StateStoreLayout, store: SqliteStore) -> Path:
    if store is SqliteStore.CATALOG:
        return layout.catalog
    if store is SqliteStore.RECOVERY:
        return layout.recovery
    raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_UNSUPPORTED")


def _reader_policy(store: SqliteStore, path: Path) -> SqliteConnectionPolicy:
    if store is SqliteStore.CATALOG:
        return catalog_reader_policy(path)
    if store is SqliteStore.RECOVERY:
        return recovery_reader_policy(path)
    raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_UNSUPPORTED")


def _writer_policy(store: SqliteStore, path: Path) -> SqliteConnectionPolicy:
    if store is SqliteStore.CATALOG:
        return catalog_critical_writer_policy(path)
    if store is SqliteStore.RECOVERY:
        return recovery_writer_policy(path)
    raise SqliteStateMigrationViolation("STATE_MIGRATION_STORE_UNSUPPORTED")


def _read_json_object(path: Path, code_prefix: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SqliteStateMigrationViolation(f"{code_prefix}_MISSING")
    if path.stat().st_size > STATE_MIGRATION_CONTROL_FILE_LIMIT_BYTES:
        raise SqliteStateMigrationViolation(f"{code_prefix}_TOO_LARGE")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SqliteStateMigrationViolation(f"{code_prefix}_INVALID") from exc
    if not isinstance(payload, dict):
        raise SqliteStateMigrationViolation(f"{code_prefix}_NOT_OBJECT")
    return payload


def _atomic_write_json(
    path: Path,
    payload: dict[str, object],
    *,
    replace_existing: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    if temp_path.exists():
        if temp_path.is_symlink() or not temp_path.is_file():
            raise SqliteStateMigrationViolation("STATE_MIGRATION_CONTROL_TEMP_INVALID")
        temp_path.unlink()
    if path.exists() and not replace_existing:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_CONTROL_FILE_ALREADY_EXISTS")
    try:
        with temp_path.open("xb") as handle:
            handle.write(_canonical_json_bytes(payload) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise SqliteStateMigrationViolation("STATE_MIGRATION_CONTROL_WRITE_FAILED") from exc


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _payload_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_epoch_id(value: str) -> None:
    if STATE_MIGRATION_EPOCH_ID_PATTERN.fullmatch(value) is None:
        raise SqliteStateMigrationViolation("STATE_MIGRATION_EPOCH_ID_INVALID")


def _mapping_field(payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_OBJECT"
        )
    return value


def _str_field(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_STRING"
        )
    return value


def _int_field(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_INTEGER"
        )
    return value


def _positive_int_field(payload: dict[str, Any], field: str) -> int:
    value = _int_field(payload, field)
    if value < 1:
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_POSITIVE"
        )
    return value


def _non_negative_int_field(payload: dict[str, Any], field: str) -> int:
    value = _int_field(payload, field)
    if value < 0:
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_NON_NEGATIVE"
        )
    return value


def _bool_field(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_BOOLEAN"
        )
    return value


def _hex_field(payload: dict[str, Any], field: str) -> str:
    value = _str_field(payload, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SqliteStateMigrationViolation(
            f"STATE_MIGRATION_{field.upper()}_MUST_BE_SHA256"
        )
    return value


def _optional_hex_field(payload: dict[str, Any], field: str) -> str | None:
    if payload.get(field) is None:
        return None
    return _hex_field(payload, field)
