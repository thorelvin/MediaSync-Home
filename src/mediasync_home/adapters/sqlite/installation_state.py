from __future__ import annotations

import sqlite3

from mediasync_home.application.installation_state import (
    InstallationIdFactory,
    InstallationState,
    InstallationStateViolation,
    validate_installation_id,
)


class SqliteInstallationStateStoreError(ValueError):
    pass


class SqliteInstallationStateStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        id_factory: InstallationIdFactory,
    ) -> None:
        self._connection = connection
        self._id_factory = id_factory

    def load_or_create(
        self,
        *,
        product_channel: str,
        app_version: str,
        catalog_schema_version: int,
        recovery_schema_version: int,
        ipc_protocol_major: int,
    ) -> InstallationState:
        _validate_startup_metadata(
            product_channel=product_channel,
            app_version=app_version,
            catalog_schema_version=catalog_schema_version,
            recovery_schema_version=recovery_schema_version,
            ipc_protocol_major=ipc_protocol_major,
        )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load()
            if existing is None:
                installation_id = self._id_factory.new_installation_id()
                validate_installation_id(installation_id)
                self._connection.execute(
                    """
                    INSERT INTO installation_state (
                        installation_id,
                        product_channel,
                        last_started_app_version,
                        catalog_schema_version,
                        recovery_schema_version,
                        ipc_protocol_major
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        installation_id,
                        product_channel,
                        app_version,
                        catalog_schema_version,
                        recovery_schema_version,
                        ipc_protocol_major,
                    ),
                )
            else:
                if existing.product_channel != product_channel:
                    raise SqliteInstallationStateStoreError(
                        "INSTALLATION_PRODUCT_CHANNEL_MISMATCH"
                    )
                expected_metadata = (
                    app_version,
                    catalog_schema_version,
                    recovery_schema_version,
                    ipc_protocol_major,
                )
                existing_metadata = (
                    existing.last_started_app_version,
                    existing.catalog_schema_version,
                    existing.recovery_schema_version,
                    existing.ipc_protocol_major,
                )
                if existing_metadata != expected_metadata:
                    cursor = self._connection.execute(
                        """
                        UPDATE installation_state
                        SET
                            last_started_app_version = ?,
                            catalog_schema_version = ?,
                            recovery_schema_version = ?,
                            ipc_protocol_major = ?,
                            row_version = row_version + 1
                        WHERE installation_id = ? AND row_version = ?
                        """,
                        (
                            *expected_metadata,
                            existing.installation_id,
                            existing.row_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SqliteInstallationStateStoreError(
                            "INSTALLATION_STATE_UPDATE_CONFLICT"
                        )
            loaded = self.load()
            if loaded is None:
                raise SqliteInstallationStateStoreError("INSTALLATION_STATE_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except (
            sqlite3.Error,
            InstallationStateViolation,
            SqliteInstallationStateStoreError,
        ) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteInstallationStateStoreError):
                raise
            if isinstance(exc, InstallationStateViolation):
                raise SqliteInstallationStateStoreError(str(exc)) from exc
            raise SqliteInstallationStateStoreError("INSTALLATION_STATE_PERSIST_FAILED") from exc

    def load(self) -> InstallationState | None:
        rows = self._connection.execute(
            """
            SELECT
                installation_id,
                product_channel,
                created_utc,
                last_started_app_version,
                catalog_schema_version,
                recovery_schema_version,
                ipc_protocol_major,
                row_version
            FROM installation_state
            ORDER BY installation_id
            LIMIT 2
            """
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise SqliteInstallationStateStoreError("INSTALLATION_STATE_NOT_SINGLETON")
        row = rows[0]
        try:
            return InstallationState(
                installation_id=str(row[0]),
                product_channel=str(row[1]),
                created_utc=str(row[2]),
                last_started_app_version=str(row[3]),
                catalog_schema_version=int(row[4]),
                recovery_schema_version=int(row[5]),
                ipc_protocol_major=int(row[6]),
                row_version=int(row[7]),
            )
        except (TypeError, ValueError, InstallationStateViolation) as exc:
            raise SqliteInstallationStateStoreError("INSTALLATION_STATE_CORRUPT") from exc


def _validate_startup_metadata(
    *,
    product_channel: str,
    app_version: str,
    catalog_schema_version: int,
    recovery_schema_version: int,
    ipc_protocol_major: int,
) -> None:
    if not product_channel.strip():
        raise SqliteInstallationStateStoreError("INSTALLATION_PRODUCT_CHANNEL_REQUIRED")
    if not app_version.strip():
        raise SqliteInstallationStateStoreError("INSTALLATION_APP_VERSION_REQUIRED")
    if catalog_schema_version < 1:
        raise SqliteInstallationStateStoreError("INSTALLATION_CATALOG_SCHEMA_VERSION_INVALID")
    if recovery_schema_version < 1:
        raise SqliteInstallationStateStoreError("INSTALLATION_RECOVERY_SCHEMA_VERSION_INVALID")
    if ipc_protocol_major < 1:
        raise SqliteInstallationStateStoreError("INSTALLATION_IPC_PROTOCOL_MAJOR_INVALID")
