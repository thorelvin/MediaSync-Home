from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.endpoint_leases import FencingTokenAllocationError
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteFencingTokenStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, recovery_migration_plan


def test_sqlite_fencing_token_store_allocates_monotonic_tokens_per_epoch(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteFencingTokenStore(connection)

        assert store.allocate_next_fencing_token(
            resource_key="endpoint:target-a",
            ownership_epoch=1,
        ) == 1
        assert store.allocate_next_fencing_token(
            resource_key="endpoint:target-a",
            ownership_epoch=1,
        ) == 2

        row = connection.execute(
            """
            SELECT ownership_epoch, last_fencing_token
            FROM lease_counters
            WHERE resource_key = ?
            """,
            ("endpoint:target-a",),
        ).fetchone()
        assert row == (1, 2)
    finally:
        connection.close()


def test_sqlite_fencing_token_store_tracks_resources_independently(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteFencingTokenStore(connection)

        assert store.allocate_next_fencing_token(
            resource_key="endpoint:target-a",
            ownership_epoch=1,
        ) == 1
        assert store.allocate_next_fencing_token(
            resource_key="endpoint:target-b",
            ownership_epoch=1,
        ) == 1
        assert store.allocate_next_fencing_token(
            resource_key="endpoint:target-a",
            ownership_epoch=1,
        ) == 2
    finally:
        connection.close()


def test_sqlite_fencing_token_store_resets_counter_for_newer_epoch(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteFencingTokenStore(connection)

        store.allocate_next_fencing_token(resource_key="endpoint:target-a", ownership_epoch=1)
        store.allocate_next_fencing_token(resource_key="endpoint:target-a", ownership_epoch=1)

        assert store.allocate_next_fencing_token(
            resource_key="endpoint:target-a",
            ownership_epoch=2,
        ) == 1
        row = connection.execute(
            """
            SELECT ownership_epoch, last_fencing_token
            FROM lease_counters
            WHERE resource_key = ?
            """,
            ("endpoint:target-a",),
        ).fetchone()
        assert row == (2, 1)
    finally:
        connection.close()


def test_sqlite_fencing_token_store_rejects_stale_epoch(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteFencingTokenStore(connection)
        store.allocate_next_fencing_token(resource_key="endpoint:target-a", ownership_epoch=2)

        with pytest.raises(FencingTokenAllocationError) as exc_info:
            store.allocate_next_fencing_token(resource_key="endpoint:target-a", ownership_epoch=1)

        assert exc_info.value.validation_code == "ENDPOINT_FENCING_TOKEN_STALE_EPOCH"
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("resource_key", "ownership_epoch"),
    [
        ("", 1),
        ("   ", 1),
        ("endpoint:target-a", 0),
    ],
)
def test_sqlite_fencing_token_store_requires_valid_request(
    tmp_path: Path,
    resource_key: str,
    ownership_epoch: int,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteFencingTokenStore(connection)

        with pytest.raises(FencingTokenAllocationError) as exc_info:
            store.allocate_next_fencing_token(
                resource_key=resource_key,
                ownership_epoch=ownership_epoch,
            )

        assert exc_info.value.validation_code == "ENDPOINT_FENCING_TOKEN_INVALID_REQUEST"
    finally:
        connection.close()


def test_sqlite_fencing_token_store_rolls_back_persistence_failure(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteFencingTokenStore(connection)
        connection.execute("DROP TABLE lease_counters")

        with pytest.raises(FencingTokenAllocationError) as exc_info:
            store.allocate_next_fencing_token(
                resource_key="endpoint:target-a",
                ownership_epoch=1,
            )

        assert exc_info.value.validation_code == "ENDPOINT_FENCING_TOKEN_PERSISTENCE_FAILED"
        assert connection.in_transaction is False
    finally:
        connection.close()


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection
