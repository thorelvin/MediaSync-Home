from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mediasync_home.adapters.sqlite.connection_policy import SqliteStore, StateStoreLayout


STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION = 2
STATE_BACKUP_SET_INTENT_SCHEMA_VERSION = 1
STATE_RESTORE_EPOCH_SCHEMA_VERSION = 1
STATE_BACKUP_SET_STORES = (SqliteStore.CATALOG, SqliteStore.RECOVERY)
STATE_BACKUP_SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
BACKUP_SET_INTENT_FILENAME = "backup-set.intent.json"
BACKUP_SET_MANIFEST_FILENAME = "backup-set.manifest.json"
STATE_RESTORE_EPOCHS_DIR_NAME = "state-restore-epochs"
STATE_RESTORE_INTENT_FILENAME = "state-restore.intent.json"
STATE_RESTORE_COMMITTED_FILENAME = "state-restore.committed.json"
STATE_RESTORE_ROLLED_BACK_FILENAME = "state-restore.rolled-back.json"


class SqliteStateBackupViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SqliteStateStoreBackup:
    store: SqliteStore
    file_name: str
    size_bytes: int
    sha256: str
    schema_version: int
    migration_count: int
    latest_migration_utc: str | None
    page_count: int
    quick_check: str
    foreign_key_violations: int
    unresolved_target_intent_count: int = 0
    target_intent_high_water_utc: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "migration_count": self.migration_count,
            "latest_migration_utc": self.latest_migration_utc,
            "page_count": self.page_count,
            "quick_check": self.quick_check,
            "foreign_key_violations": self.foreign_key_violations,
            "unresolved_target_intent_count": self.unresolved_target_intent_count,
            "target_intent_high_water_utc": self.target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateBackupManifest:
    backup_set_id: str
    created_utc: str
    state_set_hash: str
    stores: tuple[SqliteStateStoreBackup, ...]
    schema_version: int = STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backup_set_id": self.backup_set_id,
            "created_utc": self.created_utc,
            "state_set_hash": self.state_set_hash,
            "stores": [entry.to_payload() for entry in self.stores],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreFile:
    store: SqliteStore
    backup_path: Path
    target_path: Path
    size_bytes: int
    sha256: str
    schema_version: int
    migration_count: int
    latest_migration_utc: str | None
    page_count: int
    quick_check: str
    foreign_key_violations: int
    unresolved_target_intent_count: int = 0
    target_intent_high_water_utc: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "backup_path": str(self.backup_path),
            "target_path": str(self.target_path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "migration_count": self.migration_count,
            "latest_migration_utc": self.latest_migration_utc,
            "page_count": self.page_count,
            "quick_check": self.quick_check,
            "foreign_key_violations": self.foreign_key_violations,
            "unresolved_target_intent_count": self.unresolved_target_intent_count,
            "target_intent_high_water_utc": self.target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestorePlan:
    backup_set_id: str
    state_set_hash: str
    target_layout: StateStoreLayout
    restore_files: tuple[SqliteStateRestoreFile, ...]
    backup_unresolved_target_intent_count: int
    backup_target_intent_high_water_utc: str | None
    current_unresolved_target_intent_count: int
    current_target_intent_high_water_utc: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "backup_set_id": self.backup_set_id,
            "state_set_hash": self.state_set_hash,
            "target_layout": {
                "root": str(self.target_layout.root),
                "catalog": str(self.target_layout.catalog),
                "recovery": str(self.target_layout.recovery),
            },
            "restore_files": [entry.to_payload() for entry in self.restore_files],
            "backup_unresolved_target_intent_count": (
                self.backup_unresolved_target_intent_count
            ),
            "backup_target_intent_high_water_utc": self.backup_target_intent_high_water_utc,
            "current_unresolved_target_intent_count": (
                self.current_unresolved_target_intent_count
            ),
            "current_target_intent_high_water_utc": self.current_target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateSidecarRollback:
    path: Path
    rollback_path: Path

    def to_payload(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "rollback_path": str(self.rollback_path),
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoredFile:
    store: SqliteStore
    target_path: Path
    rollback_path: Path | None
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]
    size_bytes: int
    sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "target_path": str(self.target_path),
            "rollback_path": None if self.rollback_path is None else str(self.rollback_path),
            "sidecar_rollbacks": [sidecar.to_payload() for sidecar in self.sidecar_rollbacks],
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreReceipt:
    restore_epoch_id: str
    backup_set_id: str
    state_set_hash: str
    intent_path: Path
    committed_path: Path
    restored_files: tuple[SqliteStateRestoredFile, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "restore_epoch_id": self.restore_epoch_id,
            "backup_set_id": self.backup_set_id,
            "state_set_hash": self.state_set_hash,
            "intent_path": str(self.intent_path),
            "committed_path": str(self.committed_path),
            "restored_files": [entry.to_payload() for entry in self.restored_files],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreEpochRecovery:
    restore_epoch_id: str
    intent_path: Path
    rolled_back_path: Path
    rolled_back_store_count: int
    removed_temp_file_count: int
    restored_sidecar_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "restore_epoch_id": self.restore_epoch_id,
            "intent_path": str(self.intent_path),
            "rolled_back_path": str(self.rolled_back_path),
            "rolled_back_store_count": self.rolled_back_store_count,
            "removed_temp_file_count": self.removed_temp_file_count,
            "restored_sidecar_count": self.restored_sidecar_count,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreEpochRecoveryReport:
    scanned_epoch_count: int
    committed_epoch_count: int
    previously_rolled_back_epoch_count: int
    recovered_epochs: tuple[SqliteStateRestoreEpochRecovery, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "scanned_epoch_count": self.scanned_epoch_count,
            "committed_epoch_count": self.committed_epoch_count,
            "previously_rolled_back_epoch_count": self.previously_rolled_back_epoch_count,
            "recovered_epochs": [entry.to_payload() for entry in self.recovered_epochs],
        }


@dataclass(frozen=True, slots=True)
class _PreparedRestoreFile:
    restore_file: SqliteStateRestoreFile
    temp_path: Path
    rollback_path: Path
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]


@dataclass(frozen=True, slots=True)
class _RestoreEpochIntentFile:
    store: SqliteStore
    target_path: Path
    temp_path: Path
    rollback_path: Path
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]


@dataclass(frozen=True, slots=True)
class _RestoreEpochIntent:
    restore_epoch_id: str
    backup_set_id: str
    state_set_hash: str
    intent_path: Path
    restore_files: tuple[_RestoreEpochIntentFile, ...]


def create_sqlite_state_backup_set(
    layout: StateStoreLayout,
    backup_root: Path,
    *,
    backup_set_id: str,
    created_utc: str,
) -> SqliteStateBackupManifest:
    _validate_backup_set_id(backup_set_id)
    _validate_local_absolute_path(backup_root, "STATE_BACKUP_ROOT")
    backup_dir = backup_root / backup_set_id
    if backup_dir.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_SET_ALREADY_EXISTS")
    backup_dir.mkdir(parents=True)
    _write_json_no_overwrite(
        backup_dir / BACKUP_SET_INTENT_FILENAME,
        _intent_payload(backup_set_id=backup_set_id, created_utc=created_utc),
    )

    entries: list[SqliteStateStoreBackup] = []
    for store in STATE_BACKUP_SET_STORES:
        source_path = _source_path(layout, store)
        backup_path = backup_dir / _backup_file_name(store)
        _backup_sqlite_database(source_path, backup_path)
        entries.append(_inspect_backup_file(store=store, backup_path=backup_path))

    manifest = SqliteStateBackupManifest(
        backup_set_id=backup_set_id,
        created_utc=created_utc,
        state_set_hash=_state_set_hash(
            backup_set_id=backup_set_id,
            created_utc=created_utc,
            stores=tuple(entries),
        ),
        stores=tuple(entries),
    )
    _write_json_no_overwrite(backup_dir / BACKUP_SET_MANIFEST_FILENAME, manifest.to_payload())
    return manifest


def load_sqlite_state_backup_manifest(backup_dir: Path) -> SqliteStateBackupManifest:
    manifest_path = backup_dir / BACKUP_SET_MANIFEST_FILENAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SqliteStateBackupViolation("STATE_BACKUP_MANIFEST_MISSING") from exc
    return sqlite_state_backup_manifest_from_payload(payload)


def verify_sqlite_state_backup_set(
    backup_dir: Path,
    *,
    manifest: SqliteStateBackupManifest | None = None,
) -> SqliteStateBackupManifest:
    loaded = manifest or load_sqlite_state_backup_manifest(backup_dir)
    _validate_manifest_store_set(loaded.stores)
    expected_hash = _state_set_hash(
        backup_set_id=loaded.backup_set_id,
        created_utc=loaded.created_utc,
        stores=loaded.stores,
    )
    if loaded.state_set_hash != expected_hash:
        raise SqliteStateBackupViolation("STATE_BACKUP_STATE_SET_HASH_MISMATCH")

    verified_entries: list[SqliteStateStoreBackup] = []
    for entry in loaded.stores:
        backup_path = backup_dir / entry.file_name
        if not backup_path.is_file():
            raise SqliteStateBackupViolation("STATE_BACKUP_FILE_MISSING")
        if backup_path.stat().st_size != entry.size_bytes:
            raise SqliteStateBackupViolation("STATE_BACKUP_SIZE_MISMATCH")
        if _sha256_file(backup_path) != entry.sha256:
            raise SqliteStateBackupViolation("STATE_BACKUP_CHECKSUM_MISMATCH")
        inspected = _inspect_backup_file(store=entry.store, backup_path=backup_path)
        if inspected != entry:
            raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_MISMATCH")
        verified_entries.append(inspected)

    verified = SqliteStateBackupManifest(
        backup_set_id=loaded.backup_set_id,
        created_utc=loaded.created_utc,
        state_set_hash=loaded.state_set_hash,
        stores=tuple(verified_entries),
        schema_version=loaded.schema_version,
    )
    return verified


def plan_sqlite_state_restore(
    backup_dir: Path,
    target_layout: StateStoreLayout,
    *,
    current_layout: StateStoreLayout | None = None,
    manifest: SqliteStateBackupManifest | None = None,
) -> SqliteStateRestorePlan:
    _validate_state_store_layout(target_layout, field_name="STATE_RESTORE_TARGET")
    if current_layout is not None:
        _validate_state_store_layout(current_layout, field_name="STATE_RESTORE_CURRENT")
    verified = verify_sqlite_state_backup_set(backup_dir, manifest=manifest)
    backup_intent_count, backup_high_water = _target_intent_evidence_from_manifest(verified)
    current_intent_count, current_high_water = _current_target_intent_evidence(
        (current_layout or target_layout).recovery
    )
    if _has_newer_unresolved_target_intents(
        backup_count=backup_intent_count,
        backup_high_water=backup_high_water,
        current_count=current_intent_count,
        current_high_water=current_high_water,
    ):
        raise SqliteStateBackupViolation("STATE_RESTORE_BLOCKED_BY_NEWER_TARGET_INTENTS")

    restore_files = tuple(
        SqliteStateRestoreFile(
            store=entry.store,
            backup_path=backup_dir / entry.file_name,
            target_path=_source_path(target_layout, entry.store),
            size_bytes=entry.size_bytes,
            sha256=entry.sha256,
            schema_version=entry.schema_version,
            migration_count=entry.migration_count,
            latest_migration_utc=entry.latest_migration_utc,
            page_count=entry.page_count,
            quick_check=entry.quick_check,
            foreign_key_violations=entry.foreign_key_violations,
            unresolved_target_intent_count=entry.unresolved_target_intent_count,
            target_intent_high_water_utc=entry.target_intent_high_water_utc,
        )
        for entry in verified.stores
    )
    return SqliteStateRestorePlan(
        backup_set_id=verified.backup_set_id,
        state_set_hash=verified.state_set_hash,
        target_layout=target_layout,
        restore_files=restore_files,
        backup_unresolved_target_intent_count=backup_intent_count,
        backup_target_intent_high_water_utc=backup_high_water,
        current_unresolved_target_intent_count=current_intent_count,
        current_target_intent_high_water_utc=current_high_water,
    )


def restore_sqlite_state_backup_set(
    backup_dir: Path,
    target_layout: StateStoreLayout,
    *,
    restore_epoch_id: str,
    started_utc: str,
    current_layout: StateStoreLayout | None = None,
    manifest: SqliteStateBackupManifest | None = None,
) -> SqliteStateRestoreReceipt:
    plan = plan_sqlite_state_restore(
        backup_dir,
        target_layout,
        current_layout=current_layout,
        manifest=manifest,
    )
    return apply_sqlite_state_restore_plan(
        plan,
        restore_epoch_id=restore_epoch_id,
        started_utc=started_utc,
    )


def apply_sqlite_state_restore_plan(
    plan: SqliteStateRestorePlan,
    *,
    restore_epoch_id: str,
    started_utc: str,
) -> SqliteStateRestoreReceipt:
    _validate_restore_epoch_id(restore_epoch_id)
    _validate_restore_plan(plan)
    epoch_dir = _restore_epoch_dir(plan.target_layout, restore_epoch_id)
    if epoch_dir.exists():
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ALREADY_EXISTS")
    epoch_dir.parent.mkdir(parents=True, exist_ok=True)
    epoch_dir.mkdir()

    prepared_files = _prepare_restore_files(plan, restore_epoch_id=restore_epoch_id)
    intent_path = epoch_dir / STATE_RESTORE_INTENT_FILENAME
    committed_path = epoch_dir / STATE_RESTORE_COMMITTED_FILENAME
    _write_json_no_overwrite(
        intent_path,
        _restore_intent_payload(
            plan=plan,
            restore_epoch_id=restore_epoch_id,
            started_utc=started_utc,
            prepared_files=prepared_files,
        ),
    )

    restored_files: list[SqliteStateRestoredFile] = []
    try:
        for prepared in prepared_files:
            restored_files.append(_swap_prepared_restore_file(prepared))
        for prepared in prepared_files:
            _verify_restored_file(prepared.restore_file, prepared.restore_file.target_path)
        _write_json_no_overwrite(
            committed_path,
            _restore_committed_payload(
                plan=plan,
                restore_epoch_id=restore_epoch_id,
                started_utc=started_utc,
                restored_files=tuple(restored_files),
            ),
        )
    except Exception as exc:
        try:
            _rollback_restored_files(tuple(reversed(restored_files)))
        except Exception as rollback_exc:
            raise SqliteStateBackupViolation("STATE_RESTORE_ROLLBACK_FAILED") from rollback_exc
        raise SqliteStateBackupViolation("STATE_RESTORE_SWAP_FAILED") from exc

    return SqliteStateRestoreReceipt(
        restore_epoch_id=restore_epoch_id,
        backup_set_id=plan.backup_set_id,
        state_set_hash=plan.state_set_hash,
        intent_path=intent_path,
        committed_path=committed_path,
        restored_files=tuple(restored_files),
    )


def recover_incomplete_sqlite_state_restore_epochs(
    layout: StateStoreLayout,
    *,
    recovered_utc: str,
) -> SqliteStateRestoreEpochRecoveryReport:
    _validate_state_store_layout(layout, field_name="STATE_RESTORE_RECOVERY")
    epochs_dir = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return SqliteStateRestoreEpochRecoveryReport(
            scanned_epoch_count=0,
            committed_epoch_count=0,
            previously_rolled_back_epoch_count=0,
            recovered_epochs=(),
        )
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCHS_PATH_NOT_DIRECTORY")

    scanned = 0
    committed = 0
    previously_rolled_back = 0
    recovered_epochs: list[SqliteStateRestoreEpochRecovery] = []
    for epoch_dir in sorted(path for path in epochs_dir.iterdir() if path.is_dir()):
        scanned += 1
        committed_path = epoch_dir / STATE_RESTORE_COMMITTED_FILENAME
        rolled_back_path = epoch_dir / STATE_RESTORE_ROLLED_BACK_FILENAME
        if committed_path.exists():
            committed += 1
            continue
        if rolled_back_path.exists():
            previously_rolled_back += 1
            continue
        intent = _load_restore_epoch_intent(epoch_dir=epoch_dir, layout=layout)
        rollback_counts = _rollback_incomplete_restore_epoch(intent)
        _write_json_no_overwrite(
            rolled_back_path,
            _restore_rolled_back_payload(
                intent=intent,
                recovered_utc=recovered_utc,
                rolled_back_store_count=rollback_counts[0],
                removed_temp_file_count=rollback_counts[1],
                restored_sidecar_count=rollback_counts[2],
            ),
        )
        recovered_epochs.append(
            SqliteStateRestoreEpochRecovery(
                restore_epoch_id=intent.restore_epoch_id,
                intent_path=intent.intent_path,
                rolled_back_path=rolled_back_path,
                rolled_back_store_count=rollback_counts[0],
                removed_temp_file_count=rollback_counts[1],
                restored_sidecar_count=rollback_counts[2],
            )
        )

    return SqliteStateRestoreEpochRecoveryReport(
        scanned_epoch_count=scanned,
        committed_epoch_count=committed,
        previously_rolled_back_epoch_count=previously_rolled_back,
        recovered_epochs=tuple(recovered_epochs),
    )


def sqlite_state_backup_manifest_from_payload(payload: object) -> SqliteStateBackupManifest:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_BACKUP_MANIFEST_NOT_OBJECT")
    schema_version = _int_field(payload, "schema_version")
    if schema_version != STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION:
        raise SqliteStateBackupViolation("STATE_BACKUP_MANIFEST_SCHEMA_UNSUPPORTED")
    backup_set_id = _str_field(payload, "backup_set_id")
    _validate_backup_set_id(backup_set_id)
    created_utc = _str_field(payload, "created_utc")
    state_set_hash = _hex_field(payload, "state_set_hash")
    stores_payload = payload.get("stores")
    if not isinstance(stores_payload, list):
        raise SqliteStateBackupViolation("STATE_BACKUP_STORES_NOT_ARRAY")
    stores = tuple(_store_backup_from_payload(entry) for entry in stores_payload)
    _validate_manifest_store_set(stores)
    return SqliteStateBackupManifest(
        backup_set_id=backup_set_id,
        created_utc=created_utc,
        state_set_hash=state_set_hash,
        stores=stores,
        schema_version=schema_version,
    )


def _store_backup_from_payload(payload: object) -> SqliteStateStoreBackup:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_BACKUP_STORE_NOT_OBJECT")
    try:
        store = SqliteStore(_str_field(payload, "store"))
    except ValueError as exc:
        raise SqliteStateBackupViolation("STATE_BACKUP_STORE_UNSUPPORTED") from exc
    latest_migration_utc = payload.get("latest_migration_utc")
    if latest_migration_utc is not None and not isinstance(latest_migration_utc, str):
        raise SqliteStateBackupViolation("STATE_BACKUP_LATEST_MIGRATION_UTC_INVALID")
    return SqliteStateStoreBackup(
        store=store,
        file_name=_safe_file_name(_str_field(payload, "file_name")),
        size_bytes=_non_negative_int_field(payload, "size_bytes"),
        sha256=_hex_field(payload, "sha256"),
        schema_version=_non_negative_int_field(payload, "schema_version"),
        migration_count=_non_negative_int_field(payload, "migration_count"),
        latest_migration_utc=latest_migration_utc,
        page_count=_non_negative_int_field(payload, "page_count"),
        quick_check=_str_field(payload, "quick_check"),
        foreign_key_violations=_non_negative_int_field(payload, "foreign_key_violations"),
        unresolved_target_intent_count=_non_negative_int_field(
            payload,
            "unresolved_target_intent_count",
        ),
        target_intent_high_water_utc=_optional_str_field(
            payload,
            "target_intent_high_water_utc",
        ),
    )


def _backup_sqlite_database(source_path: Path, backup_path: Path) -> None:
    if not source_path.is_file():
        raise SqliteStateBackupViolation("STATE_BACKUP_SOURCE_MISSING")
    if backup_path.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_FILE_ALREADY_EXISTS")
    with sqlite3.connect(source_path) as source:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)


def _inspect_backup_file(*, store: SqliteStore, backup_path: Path) -> SqliteStateStoreBackup:
    try:
        with sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True) as connection:
            identity = _optional_scalar(
                connection,
                "SELECT store FROM store_identity WHERE singleton = 1",
            )
            if identity != store.value:
                raise SqliteStateBackupViolation("STATE_BACKUP_STORE_IDENTITY_MISMATCH")
            quick_check = str(_required_scalar(connection, "PRAGMA quick_check"))
            if quick_check.lower() != "ok":
                raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_QUICK_CHECK_FAILED")
            foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            if foreign_key_violations:
                raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_FOREIGN_KEY_CHECK_FAILED")
            schema_row = connection.execute(
                """
                SELECT
                    coalesce(max(version), 0),
                    count(*),
                    max(applied_utc)
                FROM schema_migrations
                WHERE store = ?
                """,
                (store.value,),
            ).fetchone()
            if schema_row is None:
                raise SqliteStateBackupViolation("STATE_BACKUP_SCHEMA_MIGRATIONS_MISSING")
            page_count = _required_int_scalar(connection, "PRAGMA page_count")
            unresolved_count, target_intent_high_water = _target_intent_evidence(
                connection,
                store=store,
            )
    except sqlite3.Error as exc:
        raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_UNREADABLE") from exc

    return SqliteStateStoreBackup(
        store=store,
        file_name=backup_path.name,
        size_bytes=backup_path.stat().st_size,
        sha256=_sha256_file(backup_path),
        schema_version=int(schema_row[0]),
        migration_count=int(schema_row[1]),
        latest_migration_utc=None if schema_row[2] is None else str(schema_row[2]),
        page_count=page_count,
        quick_check=quick_check,
        foreign_key_violations=foreign_key_violations,
        unresolved_target_intent_count=unresolved_count,
        target_intent_high_water_utc=target_intent_high_water,
    )


def _target_intent_evidence(
    connection: sqlite3.Connection,
    *,
    store: SqliteStore,
) -> tuple[int, str | None]:
    if store is not SqliteStore.RECOVERY:
        return (0, None)
    row = connection.execute(
        """
        SELECT count(*), max(updated_utc)
        FROM recovery_intent_segments
        WHERE state IN ('BUILDING', 'DURABLE')
        """
    ).fetchone()
    if row is None:
        raise SqliteStateBackupViolation("STATE_BACKUP_TARGET_INTENT_EVIDENCE_MISSING")
    count = int(row[0])
    high_water = None if row[1] is None else str(row[1])
    return (count, high_water)


def _current_target_intent_evidence(recovery_path: Path) -> tuple[int, str | None]:
    if not recovery_path.exists():
        return (0, None)
    try:
        with sqlite3.connect(f"file:{recovery_path.as_posix()}?mode=ro", uri=True) as connection:
            identity = _optional_scalar(
                connection,
                "SELECT store FROM store_identity WHERE singleton = 1",
            )
            if identity != SqliteStore.RECOVERY.value:
                raise SqliteStateBackupViolation("STATE_RESTORE_CURRENT_RECOVERY_MISMATCH")
            return _target_intent_evidence(connection, store=SqliteStore.RECOVERY)
    except sqlite3.Error as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_CURRENT_RECOVERY_UNREADABLE") from exc


def _target_intent_evidence_from_manifest(
    manifest: SqliteStateBackupManifest,
) -> tuple[int, str | None]:
    for entry in manifest.stores:
        if entry.store is SqliteStore.RECOVERY:
            return (entry.unresolved_target_intent_count, entry.target_intent_high_water_utc)
    raise SqliteStateBackupViolation("STATE_BACKUP_RECOVERY_STORE_MISSING")


def _has_newer_unresolved_target_intents(
    *,
    backup_count: int,
    backup_high_water: str | None,
    current_count: int,
    current_high_water: str | None,
) -> bool:
    if current_count == 0 or current_high_water is None:
        return False
    if backup_count == 0 or backup_high_water is None:
        return True
    return current_high_water > backup_high_water or (
        current_high_water == backup_high_water and current_count > backup_count
    )


def _validate_restore_plan(plan: SqliteStateRestorePlan) -> None:
    _validate_state_store_layout(plan.target_layout, field_name="STATE_RESTORE_TARGET")
    if tuple(entry.store for entry in plan.restore_files) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_RESTORE_INCOMPLETE_STORE_SET")
    for entry in plan.restore_files:
        expected_target = _source_path(plan.target_layout, entry.store)
        if entry.target_path != expected_target:
            raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_PATH_MISMATCH")
        if entry.target_path.parent != plan.target_layout.root:
            raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_STORES_MUST_BE_IN_ROOT")


def _prepare_restore_files(
    plan: SqliteStateRestorePlan,
    *,
    restore_epoch_id: str,
) -> tuple[_PreparedRestoreFile, ...]:
    prepared_files: list[_PreparedRestoreFile] = []
    temp_paths: list[Path] = []
    try:
        for entry in plan.restore_files:
            temp_path = _restore_temp_path(entry.target_path, restore_epoch_id)
            rollback_path = _restore_rollback_path(entry.target_path, restore_epoch_id)
            _require_absent(temp_path, "STATE_RESTORE_TEMP_FILE_ALREADY_EXISTS")
            _require_absent(rollback_path, "STATE_RESTORE_ROLLBACK_FILE_ALREADY_EXISTS")
            sidecar_rollbacks = tuple(
                SqliteStateSidecarRollback(
                    path=sidecar_path,
                    rollback_path=_restore_rollback_path(sidecar_path, restore_epoch_id),
                )
                for sidecar_path in _sqlite_sidecar_paths(entry.target_path)
                if sidecar_path.exists()
            )
            for sidecar in sidecar_rollbacks:
                if not sidecar.path.is_file():
                    raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_SIDECAR_NOT_FILE")
                _require_absent(
                    sidecar.rollback_path,
                    "STATE_RESTORE_SIDECAR_ROLLBACK_ALREADY_EXISTS",
                )
            _copy_file_no_overwrite(source=entry.backup_path, destination=temp_path)
            temp_paths.append(temp_path)
            _verify_restored_file(entry, temp_path)
            prepared_files.append(
                _PreparedRestoreFile(
                    restore_file=entry,
                    temp_path=temp_path,
                    rollback_path=rollback_path,
                    sidecar_rollbacks=sidecar_rollbacks,
                )
            )
    except Exception:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        raise
    return tuple(prepared_files)


def _swap_prepared_restore_file(prepared: _PreparedRestoreFile) -> SqliteStateRestoredFile:
    target_path = prepared.restore_file.target_path
    main_moved = False
    moved_sidecars: list[SqliteStateSidecarRollback] = []
    try:
        if target_path.exists():
            if not target_path.is_file():
                raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_NOT_FILE")
            target_path.replace(prepared.rollback_path)
            main_moved = True
        for sidecar in prepared.sidecar_rollbacks:
            if sidecar.path.exists():
                sidecar.path.replace(sidecar.rollback_path)
                moved_sidecars.append(sidecar)
        prepared.temp_path.replace(target_path)
    except Exception:
        _rollback_prepared_file(
            prepared,
            main_moved=main_moved,
            moved_sidecars=tuple(reversed(moved_sidecars)),
        )
        raise
    return SqliteStateRestoredFile(
        store=prepared.restore_file.store,
        target_path=target_path,
        rollback_path=prepared.rollback_path if main_moved else None,
        sidecar_rollbacks=tuple(moved_sidecars),
        size_bytes=prepared.restore_file.size_bytes,
        sha256=prepared.restore_file.sha256,
    )


def _rollback_prepared_file(
    prepared: _PreparedRestoreFile,
    *,
    main_moved: bool,
    moved_sidecars: tuple[SqliteStateSidecarRollback, ...],
) -> None:
    target_path = prepared.restore_file.target_path
    if target_path.exists() and target_path.is_file():
        target_path.unlink()
    if main_moved and prepared.rollback_path.exists():
        prepared.rollback_path.replace(target_path)
    for sidecar in moved_sidecars:
        if sidecar.path.exists() and sidecar.path.is_file():
            sidecar.path.unlink()
        if sidecar.rollback_path.exists():
            sidecar.rollback_path.replace(sidecar.path)


def _rollback_restored_files(restored_files: tuple[SqliteStateRestoredFile, ...]) -> None:
    for restored in restored_files:
        if restored.target_path.exists() and restored.target_path.is_file():
            restored.target_path.unlink()
        if restored.rollback_path is not None and restored.rollback_path.exists():
            restored.rollback_path.replace(restored.target_path)
        for sidecar in restored.sidecar_rollbacks:
            if sidecar.path.exists() and sidecar.path.is_file():
                sidecar.path.unlink()
            if sidecar.rollback_path.exists():
                sidecar.rollback_path.replace(sidecar.path)


def _load_restore_epoch_intent(
    *,
    epoch_dir: Path,
    layout: StateStoreLayout,
) -> _RestoreEpochIntent:
    intent_path = epoch_dir / STATE_RESTORE_INTENT_FILENAME
    try:
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INTENT_MISSING") from exc
    except json.JSONDecodeError as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INTENT_INVALID_JSON") from exc
    return _restore_epoch_intent_from_payload(
        payload,
        intent_path=intent_path,
        layout=layout,
        expected_epoch_id=epoch_dir.name,
    )


def _restore_epoch_intent_from_payload(
    payload: object,
    *,
    intent_path: Path,
    layout: StateStoreLayout,
    expected_epoch_id: str,
) -> _RestoreEpochIntent:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INTENT_NOT_OBJECT")
    schema_version = _int_field(payload, "schema_version")
    if schema_version != STATE_RESTORE_EPOCH_SCHEMA_VERSION:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SCHEMA_UNSUPPORTED")
    if _str_field(payload, "status") != "PREPARED":
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_STATUS_UNSUPPORTED")
    restore_epoch_id = _str_field(payload, "restore_epoch_id")
    _validate_restore_epoch_id(restore_epoch_id)
    if restore_epoch_id != expected_epoch_id:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ID_MISMATCH")
    backup_set_id = _str_field(payload, "backup_set_id")
    _validate_backup_set_id(backup_set_id)
    state_set_hash = _hex_field(payload, "state_set_hash")
    restore_files_payload = payload.get("restore_files")
    if not isinstance(restore_files_payload, list):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_FILES_NOT_ARRAY")
    restore_files = tuple(
        _restore_epoch_intent_file_from_payload(
            entry,
            layout=layout,
            restore_epoch_id=restore_epoch_id,
        )
        for entry in restore_files_payload
    )
    if tuple(entry.store for entry in restore_files) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INCOMPLETE_STORE_SET")
    return _RestoreEpochIntent(
        restore_epoch_id=restore_epoch_id,
        backup_set_id=backup_set_id,
        state_set_hash=state_set_hash,
        intent_path=intent_path,
        restore_files=restore_files,
    )


def _restore_epoch_intent_file_from_payload(
    payload: object,
    *,
    layout: StateStoreLayout,
    restore_epoch_id: str,
) -> _RestoreEpochIntentFile:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_FILE_NOT_OBJECT")
    try:
        store = SqliteStore(_str_field(payload, "store"))
    except ValueError as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_STORE_UNSUPPORTED") from exc
    target_path = _path_field(payload, "target_path")
    expected_target_path = _source_path(layout, store)
    if target_path != expected_target_path:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_TARGET_PATH_MISMATCH")
    temp_path = _path_field(payload, "temp_path")
    if temp_path != _restore_temp_path(target_path, restore_epoch_id):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_TEMP_PATH_MISMATCH")
    rollback_path = _path_field(payload, "rollback_path")
    if rollback_path != _restore_rollback_path(target_path, restore_epoch_id):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ROLLBACK_PATH_MISMATCH")
    sidecar_payload = payload.get("sidecar_rollbacks")
    if not isinstance(sidecar_payload, list):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECARS_NOT_ARRAY")
    sidecar_rollbacks = tuple(
        _sidecar_rollback_from_payload(
            entry,
            target_path=target_path,
            restore_epoch_id=restore_epoch_id,
        )
        for entry in sidecar_payload
    )
    return _RestoreEpochIntentFile(
        store=store,
        target_path=target_path,
        temp_path=temp_path,
        rollback_path=rollback_path,
        sidecar_rollbacks=sidecar_rollbacks,
    )


def _sidecar_rollback_from_payload(
    payload: object,
    *,
    target_path: Path,
    restore_epoch_id: str,
) -> SqliteStateSidecarRollback:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECAR_NOT_OBJECT")
    sidecar_path = _path_field(payload, "path")
    if sidecar_path not in _sqlite_sidecar_paths(target_path):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECAR_PATH_MISMATCH")
    rollback_path = _path_field(payload, "rollback_path")
    if rollback_path != _restore_rollback_path(sidecar_path, restore_epoch_id):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECAR_ROLLBACK_MISMATCH")
    return SqliteStateSidecarRollback(path=sidecar_path, rollback_path=rollback_path)


def _rollback_incomplete_restore_epoch(intent: _RestoreEpochIntent) -> tuple[int, int, int]:
    rolled_back_store_count = 0
    removed_temp_file_count = 0
    restored_sidecar_count = 0
    for restore_file in reversed(intent.restore_files):
        if restore_file.temp_path.exists():
            if not restore_file.temp_path.is_file():
                raise SqliteStateBackupViolation("STATE_RESTORE_TEMP_PATH_NOT_FILE")
            restore_file.temp_path.unlink()
            removed_temp_file_count += 1
        if restore_file.rollback_path.exists():
            if not restore_file.rollback_path.is_file():
                raise SqliteStateBackupViolation("STATE_RESTORE_ROLLBACK_PATH_NOT_FILE")
            if restore_file.target_path.exists():
                if not restore_file.target_path.is_file():
                    raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_NOT_FILE")
                restore_file.target_path.unlink()
            restore_file.rollback_path.replace(restore_file.target_path)
            rolled_back_store_count += 1
        for sidecar in restore_file.sidecar_rollbacks:
            if sidecar.rollback_path.exists():
                if not sidecar.rollback_path.is_file():
                    raise SqliteStateBackupViolation("STATE_RESTORE_SIDECAR_ROLLBACK_NOT_FILE")
                if sidecar.path.exists():
                    if not sidecar.path.is_file():
                        raise SqliteStateBackupViolation("STATE_RESTORE_SIDECAR_PATH_NOT_FILE")
                    sidecar.path.unlink()
                sidecar.rollback_path.replace(sidecar.path)
                restored_sidecar_count += 1
    return (rolled_back_store_count, removed_temp_file_count, restored_sidecar_count)


def _verify_restored_file(entry: SqliteStateRestoreFile, database_path: Path) -> None:
    inspected = _inspect_backup_file(store=entry.store, backup_path=database_path)
    expected = SqliteStateStoreBackup(
        store=entry.store,
        file_name=database_path.name,
        size_bytes=entry.size_bytes,
        sha256=entry.sha256,
        schema_version=entry.schema_version,
        migration_count=entry.migration_count,
        latest_migration_utc=entry.latest_migration_utc,
        page_count=entry.page_count,
        quick_check=entry.quick_check,
        foreign_key_violations=entry.foreign_key_violations,
        unresolved_target_intent_count=entry.unresolved_target_intent_count,
        target_intent_high_water_utc=entry.target_intent_high_water_utc,
    )
    if inspected != expected:
        raise SqliteStateBackupViolation("STATE_RESTORE_SQLITE_EVIDENCE_MISMATCH")


def _copy_file_no_overwrite(*, source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SqliteStateBackupViolation("STATE_RESTORE_BACKUP_FILE_MISSING")
    if destination.exists():
        raise SqliteStateBackupViolation("STATE_RESTORE_TEMP_FILE_ALREADY_EXISTS")
    try:
        with source.open("rb") as source_handle:
            with destination.open("xb") as destination_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    destination_handle.write(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise SqliteStateBackupViolation("STATE_RESTORE_TEMP_COPY_FAILED") from exc


def _restore_epoch_dir(layout: StateStoreLayout, restore_epoch_id: str) -> Path:
    return layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / restore_epoch_id


def _restore_temp_path(target_path: Path, restore_epoch_id: str) -> Path:
    return target_path.with_name(f".{target_path.name}.{restore_epoch_id}.restore-new.tmp")


def _restore_rollback_path(target_path: Path, restore_epoch_id: str) -> Path:
    return target_path.with_name(f".{target_path.name}.{restore_epoch_id}.restore-rollback")


def _sqlite_sidecar_paths(database_path: Path) -> tuple[Path, ...]:
    raw = str(database_path)
    return (Path(f"{raw}-wal"), Path(f"{raw}-shm"), Path(f"{raw}-journal"))


def _require_absent(path: Path, violation: str) -> None:
    if path.exists():
        raise SqliteStateBackupViolation(violation)


def _restore_intent_payload(
    *,
    plan: SqliteStateRestorePlan,
    restore_epoch_id: str,
    started_utc: str,
    prepared_files: tuple[_PreparedRestoreFile, ...],
) -> dict[str, object]:
    return {
        "schema_version": STATE_RESTORE_EPOCH_SCHEMA_VERSION,
        "status": "PREPARED",
        "restore_epoch_id": restore_epoch_id,
        "started_utc": started_utc,
        "backup_set_id": plan.backup_set_id,
        "state_set_hash": plan.state_set_hash,
        "backup_unresolved_target_intent_count": (
            plan.backup_unresolved_target_intent_count
        ),
        "backup_target_intent_high_water_utc": plan.backup_target_intent_high_water_utc,
        "current_unresolved_target_intent_count": (
            plan.current_unresolved_target_intent_count
        ),
        "current_target_intent_high_water_utc": plan.current_target_intent_high_water_utc,
        "restore_files": [
            {
                **prepared.restore_file.to_payload(),
                "temp_path": str(prepared.temp_path),
                "rollback_path": str(prepared.rollback_path),
                "sidecar_rollbacks": [
                    {
                        "path": str(sidecar.path),
                        "rollback_path": str(sidecar.rollback_path),
                    }
                    for sidecar in prepared.sidecar_rollbacks
                ],
            }
            for prepared in prepared_files
        ],
    }


def _restore_committed_payload(
    *,
    plan: SqliteStateRestorePlan,
    restore_epoch_id: str,
    started_utc: str,
    restored_files: tuple[SqliteStateRestoredFile, ...],
) -> dict[str, object]:
    return {
        "schema_version": STATE_RESTORE_EPOCH_SCHEMA_VERSION,
        "status": "COMMITTED",
        "restore_epoch_id": restore_epoch_id,
        "started_utc": started_utc,
        "backup_set_id": plan.backup_set_id,
        "state_set_hash": plan.state_set_hash,
        "restored_files": [entry.to_payload() for entry in restored_files],
    }


def _restore_rolled_back_payload(
    *,
    intent: _RestoreEpochIntent,
    recovered_utc: str,
    rolled_back_store_count: int,
    removed_temp_file_count: int,
    restored_sidecar_count: int,
) -> dict[str, object]:
    return {
        "schema_version": STATE_RESTORE_EPOCH_SCHEMA_VERSION,
        "status": "ROLLED_BACK",
        "restore_epoch_id": intent.restore_epoch_id,
        "recovered_utc": recovered_utc,
        "backup_set_id": intent.backup_set_id,
        "state_set_hash": intent.state_set_hash,
        "rolled_back_store_count": rolled_back_store_count,
        "removed_temp_file_count": removed_temp_file_count,
        "restored_sidecar_count": restored_sidecar_count,
    }


def _validate_manifest_store_set(stores: tuple[SqliteStateStoreBackup, ...]) -> None:
    seen = tuple(entry.store for entry in stores)
    if seen != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_BACKUP_INCOMPLETE_STORE_SET")
    if len(set(seen)) != len(seen):
        raise SqliteStateBackupViolation("STATE_BACKUP_DUPLICATE_STORE")
    for entry in stores:
        if entry.file_name != _backup_file_name(entry.store):
            raise SqliteStateBackupViolation("STATE_BACKUP_FILE_NAME_MISMATCH")


def _state_set_hash(
    *,
    backup_set_id: str,
    created_utc: str,
    stores: tuple[SqliteStateStoreBackup, ...],
) -> str:
    return _sha256_bytes(
        _canonical_json_bytes(
            {
                "backup_set_id": backup_set_id,
                "created_utc": created_utc,
                "stores": [entry.to_payload() for entry in stores],
            }
        )
    )


def _intent_payload(*, backup_set_id: str, created_utc: str) -> dict[str, object]:
    return {
        "schema_version": STATE_BACKUP_SET_INTENT_SCHEMA_VERSION,
        "backup_set_id": backup_set_id,
        "created_utc": created_utc,
        "expected_stores": [store.value for store in STATE_BACKUP_SET_STORES],
        "expected_files": [_backup_file_name(store) for store in STATE_BACKUP_SET_STORES],
    }


def _source_path(layout: StateStoreLayout, store: SqliteStore) -> Path:
    if store is SqliteStore.CATALOG:
        return layout.catalog
    if store is SqliteStore.RECOVERY:
        return layout.recovery
    raise SqliteStateBackupViolation("STATE_BACKUP_STORE_UNSUPPORTED")


def _validate_state_store_layout(layout: StateStoreLayout, *, field_name: str) -> None:
    _validate_local_absolute_path(layout.root, f"{field_name}_ROOT")
    _validate_local_absolute_path(layout.catalog, f"{field_name}_CATALOG")
    _validate_local_absolute_path(layout.recovery, f"{field_name}_RECOVERY")
    if layout.catalog == layout.recovery:
        raise SqliteStateBackupViolation(f"{field_name}_STORES_MUST_BE_SEPARATE_FILES")


def _backup_file_name(store: SqliteStore) -> str:
    return f"{store.value}.sqlite.backup"


def _write_json_no_overwrite(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_CONTROL_FILE_ALREADY_EXISTS")
    with path.open("xb") as handle:
        handle.write(_canonical_json_bytes(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _canonical_json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_backup_set_id(value: str) -> None:
    if STATE_BACKUP_SET_ID_PATTERN.fullmatch(value) is None:
        raise SqliteStateBackupViolation("STATE_BACKUP_SET_ID_INVALID")


def _validate_restore_epoch_id(value: str) -> None:
    if STATE_BACKUP_SET_ID_PATTERN.fullmatch(value) is None:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ID_INVALID")


def _validate_local_absolute_path(path: Path, field_name: str) -> None:
    if not path.is_absolute():
        raise SqliteStateBackupViolation(f"{field_name}_MUST_BE_ABSOLUTE")
    if str(path).startswith("\\\\"):
        raise SqliteStateBackupViolation(f"{field_name}_MUST_BE_LOCAL")


def _safe_file_name(value: str) -> str:
    if "/" in value or "\\" in value or value in {"", ".", ".."}:
        raise SqliteStateBackupViolation("STATE_BACKUP_FILE_NAME_INVALID")
    return value


def _required_scalar(connection: sqlite3.Connection, query: str) -> object:
    row = connection.execute(query).fetchone()
    if row is None:
        raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_MISSING")
    return cast(object, row[0])


def _required_int_scalar(connection: sqlite3.Connection, query: str) -> int:
    value = _required_scalar(connection, query)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_INVALID")
    return value


def _optional_scalar(connection: sqlite3.Connection, query: str) -> object | None:
    row = connection.execute(query).fetchone()
    if row is None:
        return None
    return cast(object | None, row[0])


def _str_field(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _optional_str_field(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _path_field(payload: dict[str, Any], field_name: str) -> Path:
    value = _str_field(payload, field_name)
    path = Path(value)
    if not path.is_absolute():
        raise SqliteStateBackupViolation(f"STATE_RESTORE_{field_name.upper()}_MUST_BE_ABSOLUTE")
    return path


def _int_field(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _non_negative_int_field(payload: dict[str, Any], field_name: str) -> int:
    value = _int_field(payload, field_name)
    if value < 0:
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _hex_field(payload: dict[str, Any], field_name: str) -> str:
    value = _str_field(payload, field_name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value
