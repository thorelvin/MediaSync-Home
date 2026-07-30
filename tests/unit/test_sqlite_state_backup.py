from __future__ import annotations

import pytest

from mediasync_home.adapters.sqlite.state_backup import (
    STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION,
    SqliteStateBackupViolation,
    sqlite_state_backup_manifest_from_payload,
)


def test_state_backup_manifest_payload_roundtrips_complete_store_pair() -> None:
    payload = _manifest_payload()

    manifest = sqlite_state_backup_manifest_from_payload(payload)

    assert manifest.schema_version == STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION
    assert manifest.backup_set_id == "set-a"
    assert manifest.stores[0].store.value == "catalog"
    assert manifest.stores[1].store.value == "recovery"
    assert manifest.to_payload() == payload


def test_state_backup_manifest_rejects_incomplete_store_pair() -> None:
    payload = _manifest_payload()
    payload["stores"] = payload["stores"][:1]

    with pytest.raises(SqliteStateBackupViolation, match="STATE_BACKUP_INCOMPLETE_STORE_SET"):
        sqlite_state_backup_manifest_from_payload(payload)


def test_state_backup_manifest_rejects_path_like_file_names() -> None:
    payload = _manifest_payload()
    stores = payload["stores"]
    assert isinstance(stores, list)
    first = dict(stores[0])
    first["file_name"] = "../catalog.sqlite.backup"
    stores[0] = first

    with pytest.raises(SqliteStateBackupViolation, match="STATE_BACKUP_FILE_NAME_INVALID"):
        sqlite_state_backup_manifest_from_payload(payload)


def test_state_backup_manifest_rejects_unsafe_backup_set_id() -> None:
    payload = _manifest_payload()
    payload["backup_set_id"] = "../set-a"

    with pytest.raises(SqliteStateBackupViolation, match="STATE_BACKUP_SET_ID_INVALID"):
        sqlite_state_backup_manifest_from_payload(payload)


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "backup_set_id": "set-a",
        "created_utc": "2026-07-30T12:00:00Z",
        "state_set_hash": "0" * 64,
        "stores": [
            {
                "store": "catalog",
                "file_name": "catalog.sqlite.backup",
                "size_bytes": 4096,
                "sha256": "1" * 64,
                "schema_version": 22,
                "migration_count": 22,
                "latest_migration_utc": "2026-07-30T12:00:00.000Z",
                "page_count": 1,
                "quick_check": "ok",
                "foreign_key_violations": 0,
                "unresolved_target_intent_count": 0,
                "target_intent_high_water_utc": None,
            },
            {
                "store": "recovery",
                "file_name": "recovery.sqlite.backup",
                "size_bytes": 4096,
                "sha256": "2" * 64,
                "schema_version": 5,
                "migration_count": 5,
                "latest_migration_utc": "2026-07-30T12:00:00.000Z",
                "page_count": 1,
                "quick_check": "ok",
                "foreign_key_violations": 0,
                "unresolved_target_intent_count": 1,
                "target_intent_high_water_utc": "2026-07-30T12:00:00.000Z",
            },
        ],
    }
