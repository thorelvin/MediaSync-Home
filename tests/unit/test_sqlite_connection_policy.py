from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteConnectionPurpose,
    SqliteFailureKind,
    SqlitePolicyViolation,
    SqlitePragma,
    catalog_bulk_writer_policy,
    catalog_critical_writer_policy,
    catalog_reader_policy,
    build_state_store_layout,
    classify_sqlite_error_message,
    classify_sqlite_error_name,
    recovery_reader_policy,
    recovery_writer_policy,
    validate_sqlite_connection_policy,
)


ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = ROOT / ".local-state-test"


def test_state_store_layout_uses_separate_local_sqlite_files() -> None:
    layout = build_state_store_layout(STATE_ROOT)

    assert layout.root == STATE_ROOT
    assert layout.catalog == STATE_ROOT / "catalog.sqlite"
    assert layout.recovery == STATE_ROOT / "recovery.sqlite"
    assert layout.catalog != layout.recovery


def test_state_store_layout_rejects_relative_root() -> None:
    with pytest.raises(SqlitePolicyViolation, match="STATE_ROOT_MUST_BE_ABSOLUTE"):
        build_state_store_layout(Path("relative-state"))


def test_catalog_bulk_writer_matches_documented_normal_sync_policy() -> None:
    policy = catalog_bulk_writer_policy(STATE_ROOT / "catalog.sqlite")

    assert policy.store.value == "catalog"
    assert policy.purpose is SqliteConnectionPurpose.CATALOG_BULK_WRITER
    assert policy.writable is True
    assert policy.pragma_map() == {
        "foreign_keys": "ON",
        "trusted_schema": "OFF",
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "busy_timeout": 5000,
        "cache_size": -65536,
        "wal_autocheckpoint": 1000,
    }


def test_catalog_critical_writer_requires_full_sync_without_cross_store_escape() -> None:
    policy = catalog_critical_writer_policy(STATE_ROOT / "catalog.sqlite")

    assert policy.purpose is SqliteConnectionPurpose.CATALOG_CRITICAL_WRITER
    assert policy.pragma_map()["synchronous"] == "FULL"
    assert policy.allow_attach is False
    assert policy.allow_shared_cache is False
    assert policy.allow_cross_store_transaction is False


def test_recovery_writer_uses_full_sync_and_shorter_wal_checkpoint() -> None:
    policy = recovery_writer_policy(STATE_ROOT / "recovery.sqlite")

    assert policy.purpose is SqliteConnectionPurpose.RECOVERY_WRITER
    assert policy.pragma_map() == {
        "foreign_keys": "ON",
        "trusted_schema": "OFF",
        "journal_mode": "WAL",
        "synchronous": "FULL",
        "busy_timeout": 5000,
        "wal_autocheckpoint": 100,
    }


def test_read_connections_are_query_only() -> None:
    assert catalog_reader_policy(STATE_ROOT / "catalog.sqlite").pragma_map()["query_only"] == "ON"
    assert recovery_reader_policy(STATE_ROOT / "recovery.sqlite").pragma_map()["query_only"] == "ON"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"database_path": Path("catalog.sqlite")}, "DATABASE_PATH_MUST_BE_ABSOLUTE"),
        ({"allow_attach": True}, "SQLITE_ATTACH_FORBIDDEN"),
        ({"allow_shared_cache": True}, "SQLITE_SHARED_CACHE_FORBIDDEN"),
        ({"allow_extension_loading": True}, "SQLITE_EXTENSION_LOADING_FORBIDDEN"),
        ({"allow_cross_store_transaction": True}, "CROSS_STORE_TRANSACTION_FORBIDDEN"),
        (
            {"pragmas": (SqlitePragma("foreign_keys", "OFF"),)},
            "SQLITE_FOREIGN_KEYS_REQUIRED",
        ),
    ],
)
def test_connection_policy_rejects_unsafe_database_modes(
    mutation: dict[str, object],
    reason: str,
) -> None:
    baseline = recovery_writer_policy(STATE_ROOT / "recovery.sqlite")
    policy = replace(baseline, **mutation)

    with pytest.raises(SqlitePolicyViolation, match=reason):
        validate_sqlite_connection_policy(policy)


def test_classifies_documented_sqlite_error_names() -> None:
    assert classify_sqlite_error_name("SQLITE_BUSY") is SqliteFailureKind.BUSY
    assert classify_sqlite_error_name("SQLITE_BUSY_SNAPSHOT") is SqliteFailureKind.BUSY_SNAPSHOT
    assert classify_sqlite_error_name("SQLITE_FULL") is SqliteFailureKind.FULL
    assert classify_sqlite_error_name("SQLITE_CORRUPT") is SqliteFailureKind.CORRUPT
    assert classify_sqlite_error_name("SQLITE_NOTADB") is SqliteFailureKind.NOT_A_DATABASE
    assert classify_sqlite_error_name("SQLITE_READONLY") is SqliteFailureKind.READ_ONLY
    assert classify_sqlite_error_name("SQLITE_IOERR_WRITE") is SqliteFailureKind.IO


def test_classifies_documented_sqlite_error_messages() -> None:
    assert classify_sqlite_error_message("database is locked") is SqliteFailureKind.BUSY
    assert classify_sqlite_error_message("database or disk is full") is SqliteFailureKind.FULL
    assert (
        classify_sqlite_error_message("database disk image is malformed")
        is SqliteFailureKind.CORRUPT
    )
    assert classify_sqlite_error_message("file is not a database") is SqliteFailureKind.NOT_A_DATABASE
    assert classify_sqlite_error_message("attempt to write a readonly database") is SqliteFailureKind.READ_ONLY
    assert classify_sqlite_error_message("disk I/O error") is SqliteFailureKind.IO
