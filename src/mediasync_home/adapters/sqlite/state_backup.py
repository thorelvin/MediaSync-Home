from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mediasync_home.adapters.sqlite.connection_policy import SqliteStore, StateStoreLayout


STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION = 1
STATE_BACKUP_SET_INTENT_SCHEMA_VERSION = 1
STATE_BACKUP_SET_STORES = (SqliteStore.CATALOG, SqliteStore.RECOVERY)
STATE_BACKUP_SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
BACKUP_SET_INTENT_FILENAME = "backup-set.intent.json"
BACKUP_SET_MANIFEST_FILENAME = "backup-set.manifest.json"


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
    )


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


def _backup_file_name(store: SqliteStore) -> str:
    return f"{store.value}.sqlite.backup"


def _write_json_no_overwrite(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_CONTROL_FILE_ALREADY_EXISTS")
    path.write_bytes(_canonical_json_bytes(payload) + b"\n")


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
