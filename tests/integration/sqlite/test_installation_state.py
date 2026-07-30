from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.installation_state import (
    SqliteInstallationStateStore,
    SqliteInstallationStateStoreError,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)


@dataclass
class _FixedInstallationIdFactory:
    installation_id: str
    calls: int = 0

    def new_installation_id(self) -> str:
        self.calls += 1
        return self.installation_id


def test_installation_state_is_created_once_and_reused(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    factory = _FixedInstallationIdFactory("11111111-1111-4111-8111-111111111111")
    with _catalog_connection(database) as connection:
        store = SqliteInstallationStateStore(connection, id_factory=factory)

        created = _load_or_create(store)
        reloaded = _load_or_create(store)

        assert UUID(created.installation_id).version == 4
        assert reloaded == created
        assert created.product_channel == "local-preview"
        assert created.catalog_schema_version == 25
        assert created.recovery_schema_version == 5
        assert created.ipc_protocol_major == 1
        assert created.row_version == 1
        assert factory.calls == 1


def test_installation_state_updates_compatible_startup_metadata(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    factory = _FixedInstallationIdFactory("22222222-2222-4222-8222-222222222222")
    with _catalog_connection(database) as connection:
        store = SqliteInstallationStateStore(connection, id_factory=factory)
        created = _load_or_create(store)

        updated = store.load_or_create(
            product_channel="local-preview",
            app_version="1.2.3",
            catalog_schema_version=25,
            recovery_schema_version=6,
            ipc_protocol_major=2,
        )

        assert updated.installation_id == created.installation_id
        assert updated.created_utc == created.created_utc
        assert updated.last_started_app_version == "1.2.3"
        assert updated.catalog_schema_version == 25
        assert updated.recovery_schema_version == 6
        assert updated.ipc_protocol_major == 2
        assert updated.row_version == 2
        assert factory.calls == 1


def test_installation_state_rejects_product_channel_change(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    factory = _FixedInstallationIdFactory("33333333-3333-4333-8333-333333333333")
    with _catalog_connection(database) as connection:
        store = SqliteInstallationStateStore(connection, id_factory=factory)
        _load_or_create(store)

        with pytest.raises(
            SqliteInstallationStateStoreError,
            match="INSTALLATION_PRODUCT_CHANNEL_MISMATCH",
        ):
            store.load_or_create(
                product_channel="stable",
                app_version="0.0.0",
                catalog_schema_version=25,
                recovery_schema_version=5,
                ipc_protocol_major=1,
            )


def test_installation_state_rejects_noncanonical_factory_uuid(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    factory = _FixedInstallationIdFactory("NOT-A-UUID")
    with _catalog_connection(database) as connection:
        store = SqliteInstallationStateStore(connection, id_factory=factory)

        with pytest.raises(SqliteInstallationStateStoreError, match="INSTALLATION_ID_INVALID"):
            _load_or_create(store)

        assert store.load() is None


def test_installation_state_schema_enforces_singleton(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    factory = _FixedInstallationIdFactory("44444444-4444-4444-8444-444444444444")
    with _catalog_connection(database) as connection:
        store = SqliteInstallationStateStore(connection, id_factory=factory)
        _load_or_create(store)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO installation_state (
                    installation_id,
                    product_channel,
                    last_started_app_version,
                    catalog_schema_version,
                    recovery_schema_version,
                    ipc_protocol_major
                )
                VALUES (?, 'local-preview', '0.0.0', 25, 5, 1)
                """,
                ("55555555-5555-4555-8555-555555555555",),
            )


def test_installation_state_schema_keeps_identity_immutable(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    factory = _FixedInstallationIdFactory("66666666-6666-4666-8666-666666666666")
    with _catalog_connection(database) as connection:
        store = SqliteInstallationStateStore(connection, id_factory=factory)
        created = _load_or_create(store)

        with pytest.raises(sqlite3.IntegrityError, match="INSTALLATION_IDENTITY_IMMUTABLE"):
            connection.execute(
                "UPDATE installation_state SET product_channel = 'stable'",
            )
        with pytest.raises(sqlite3.IntegrityError, match="INSTALLATION_STATE_DELETE_FORBIDDEN"):
            connection.execute(
                "DELETE FROM installation_state WHERE installation_id = ?",
                (created.installation_id,),
            )


def _catalog_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
    return connection


def _load_or_create(store: SqliteInstallationStateStore):
    return store.load_or_create(
        product_channel="local-preview",
        app_version="0.0.0",
        catalog_schema_version=25,
        recovery_schema_version=5,
        ipc_protocol_major=1,
    )
