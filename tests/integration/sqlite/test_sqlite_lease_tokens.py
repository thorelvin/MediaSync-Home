from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.endpoint_leases import (
    FencingTokenAllocationError,
    ResourceLeaseRegistrationError,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import (
    SqliteFencingTokenStore,
    SqliteResourceLeaseStore,
)
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


def test_sqlite_resource_lease_store_registers_token_and_active_lease(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteResourceLeaseStore(connection)

        assert _register_resource_lease(store, lease_id="lease-a") == 1

        row = connection.execute(
            """
            SELECT
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
                state,
                released_utc
            FROM resource_leases
            WHERE lease_id = ?
            """,
            ("lease-a",),
        ).fetchone()
        assert row == (
            "lease-a",
            "endpoint:target-a",
            1,
            1,
            "EXCLUSIVE",
            "owner-a",
            "run-a",
            "run-a-target-0000",
            "target-a",
            None,
            "LOCAL_OS_HANDLE",
            "ACQUIRED",
            None,
        )

        counter = connection.execute(
            """
            SELECT ownership_epoch, last_fencing_token
            FROM lease_counters
            WHERE resource_key = ?
            """,
            ("endpoint:target-a",),
        ).fetchone()
        assert counter == (1, 1)
    finally:
        connection.close()


def test_sqlite_resource_lease_store_blocks_second_active_exclusive_lease(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteResourceLeaseStore(connection)
        _register_resource_lease(store, lease_id="lease-a")

        with pytest.raises(ResourceLeaseRegistrationError) as exc_info:
            _register_resource_lease(store, lease_id="lease-b")

        assert exc_info.value.validation_code == "ENDPOINT_RESOURCE_LEASE_ACTIVE_CONFLICT"
    finally:
        connection.close()


def test_sqlite_resource_lease_store_releases_and_allows_next_token(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteResourceLeaseStore(connection)
        assert _register_resource_lease(store, lease_id="lease-a") == 1

        store.release_resource_lease(lease_id="lease-a")

        assert _register_resource_lease(store, lease_id="lease-b") == 2
        rows = connection.execute(
            """
            SELECT lease_id, state, released_utc
            FROM resource_leases
            ORDER BY lease_id
            """
        ).fetchall()
        assert rows[0][:2] == ("lease-a", "RELEASED")
        assert rows[0][2] is not None
        assert rows[1] == ("lease-b", "ACQUIRED", None)
    finally:
        connection.close()


def test_sqlite_resource_lease_store_rejects_stale_epoch(tmp_path: Path) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteResourceLeaseStore(connection)
        _register_resource_lease(store, lease_id="lease-a", ownership_epoch=2)
        store.release_resource_lease(lease_id="lease-a")

        with pytest.raises(FencingTokenAllocationError) as exc_info:
            _register_resource_lease(store, lease_id="lease-b", ownership_epoch=1)

        assert exc_info.value.validation_code == "ENDPOINT_FENCING_TOKEN_STALE_EPOCH"
    finally:
        connection.close()


def test_sqlite_resource_lease_store_rolls_back_counter_when_lease_insert_fails(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        store = SqliteResourceLeaseStore(connection)
        _register_resource_lease(store, lease_id="lease-a")
        store.release_resource_lease(lease_id="lease-a")

        with pytest.raises(ResourceLeaseRegistrationError) as exc_info:
            _register_resource_lease(
                store,
                lease_id="lease-a",
                resource_key="endpoint:target-b",
                endpoint_id="target-b",
            )

        assert exc_info.value.validation_code == "ENDPOINT_RESOURCE_LEASE_PERSISTENCE_FAILED"
        assert connection.in_transaction is False
        counter = connection.execute(
            """
            SELECT last_fencing_token
            FROM lease_counters
            WHERE resource_key = ?
            """,
            ("endpoint:target-b",),
        ).fetchone()
        assert counter is None
    finally:
        connection.close()


def _register_resource_lease(
    store: SqliteResourceLeaseStore,
    *,
    lease_id: str,
    resource_key: str = "endpoint:target-a",
    endpoint_id: str = "target-a",
    ownership_epoch: int = 1,
) -> int:
    return store.register_acquired_resource_lease(
        lease_id=lease_id,
        resource_key=resource_key,
        owner_instance_id="owner-a",
        ownership_epoch=ownership_epoch,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id=endpoint_id,
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    )


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection
