from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteConnectionPolicy,
    SqlitePolicyViolation,
    apply_sqlite_connection_policy,
    catalog_bulk_writer_policy,
    catalog_critical_writer_policy,
    catalog_reader_policy,
    recovery_reader_policy,
    recovery_writer_policy,
    verify_applied_sqlite_connection_policy,
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


@pytest.mark.parametrize(
    "policy_factory",
    [catalog_critical_writer_policy, recovery_writer_policy],
)
def test_critical_writer_guard_blocks_runtime_policy_weakening(
    tmp_path: Path,
    policy_factory: Callable[[Path], SqliteConnectionPolicy],
) -> None:
    database = tmp_path / "state.sqlite"
    policy = policy_factory(database)
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, policy)

        for statement in (
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = OFF",
            "PRAGMA trusted_schema = ON",
            "PRAGMA journal_mode = DELETE",
            "PRAGMA writable_schema = ON",
            "PRAGMA ignore_check_constraints = ON",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                connection.execute(statement)

        verify_applied_sqlite_connection_policy(connection, policy)
        assert _pragma(connection, "synchronous") == 2
        assert _pragma(connection, "foreign_keys") == 1
        assert _pragma(connection, "trusted_schema") == 0
        assert _pragma(connection, "journal_mode") == "wal"


@pytest.mark.parametrize(
    "policy_factory",
    [catalog_reader_policy, recovery_reader_policy],
)
def test_reader_guard_cannot_be_switched_back_to_writable(
    tmp_path: Path,
    policy_factory: Callable[[Path], SqliteConnectionPolicy],
) -> None:
    database = tmp_path / "state.sqlite"
    policy = policy_factory(database)
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, policy)

        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("PRAGMA query_only = OFF")

        verify_applied_sqlite_connection_policy(connection, policy)
        assert _pragma(connection, "query_only") == 1


def test_policy_guard_blocks_late_attach_and_detects_preexisting_attach(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    peer = tmp_path / "peer.sqlite"
    policy = catalog_critical_writer_policy(database)
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, policy)

        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("ATTACH DATABASE ? AS peer", (str(peer),))

    with sqlite3.connect(database) as connection:
        connection.execute("ATTACH DATABASE ? AS peer", (str(peer),))
        with pytest.raises(
            SqlitePolicyViolation,
            match="SQLITE_ATTACHED_DATABASE_FORBIDDEN",
        ):
            apply_sqlite_connection_policy(connection, policy)


def test_policy_verification_rejects_connection_to_different_file(tmp_path: Path) -> None:
    actual_database = tmp_path / "actual.sqlite"
    claimed_database = tmp_path / "claimed.sqlite"
    with sqlite3.connect(actual_database) as connection:
        with pytest.raises(
            SqlitePolicyViolation,
            match="SQLITE_DATABASE_PATH_MISMATCH",
        ):
            apply_sqlite_connection_policy(
                connection,
                catalog_critical_writer_policy(claimed_database),
            )


def test_guard_allows_repository_writes_and_non_policy_high_water_pragma(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    policy = catalog_critical_writer_policy(database)
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, policy)
        apply_sqlite_connection_policy(connection, policy)

        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY) STRICT")
        connection.execute("INSERT INTO evidence (id) VALUES (1)")
        connection.execute("PRAGMA user_version = 77")

        assert connection.execute("SELECT id FROM evidence").fetchone() == (1,)
        assert _pragma(connection, "user_version") == 77


def test_policy_keeps_sqlite_extension_loading_disabled(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection,
            catalog_critical_writer_policy(database),
        )

        with pytest.raises(sqlite3.OperationalError, match="not authorized"):
            connection.execute("SELECT load_extension('untrusted-extension')")


def _pragma(connection: sqlite3.Connection, name: str) -> object:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None:
        raise AssertionError(f"PRAGMA {name} returned no row")
    return row[0]
