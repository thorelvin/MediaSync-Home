from __future__ import annotations

import sqlite3
from pathlib import Path

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_bulk_writer_policy,
    catalog_critical_writer_policy,
    catalog_reader_policy,
    recovery_writer_policy,
)


def test_catalog_bulk_writer_pragmas_apply_to_real_sqlite_file(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_bulk_writer_policy(database))

        assert _pragma(connection, "foreign_keys") == 1
        assert _pragma(connection, "trusted_schema") == 0
        assert _pragma(connection, "journal_mode") == "wal"
        assert _pragma(connection, "synchronous") == 1
        assert _pragma(connection, "busy_timeout") == 5000
        assert _pragma(connection, "cache_size") == -65536
        assert _pragma(connection, "wal_autocheckpoint") == 1000


def test_catalog_critical_writer_pragmas_apply_full_sync(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))

        assert _pragma(connection, "journal_mode") == "wal"
        assert _pragma(connection, "synchronous") == 2
        assert _pragma(connection, "wal_autocheckpoint") == 1000


def test_recovery_writer_pragmas_apply_full_sync_and_short_wal(tmp_path: Path) -> None:
    database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, recovery_writer_policy(database))

        assert _pragma(connection, "foreign_keys") == 1
        assert _pragma(connection, "trusted_schema") == 0
        assert _pragma(connection, "journal_mode") == "wal"
        assert _pragma(connection, "synchronous") == 2
        assert _pragma(connection, "busy_timeout") == 5000
        assert _pragma(connection, "wal_autocheckpoint") == 100


def test_reader_policy_applies_query_only_guard(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, catalog_reader_policy(database))

        assert _pragma(connection, "foreign_keys") == 1
        assert _pragma(connection, "trusted_schema") == 0
        assert _pragma(connection, "query_only") == 1


def _pragma(connection: sqlite3.Connection, name: str) -> object:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None:
        raise AssertionError(f"PRAGMA {name} returned no row")
    return row[0]
