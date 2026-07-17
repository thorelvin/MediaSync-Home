from __future__ import annotations

import sqlite3

from mediasync_home.adapters.endpoint_leases import FencingTokenAllocationError, FencingTokenStore


class SqliteFencingTokenStore(FencingTokenStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def allocate_next_fencing_token(self, *, resource_key: str, ownership_epoch: int) -> int:
        if not resource_key.strip() or ownership_epoch < 1:
            raise FencingTokenAllocationError(
                "ENDPOINT_FENCING_TOKEN_INVALID_REQUEST",
                "Acquire fencing tokens with a non-empty resource key and positive ownership epoch.",
            )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            token = self._allocate_token(resource_key=resource_key, ownership_epoch=ownership_epoch)
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return token
        except FencingTokenAllocationError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise FencingTokenAllocationError(
                "ENDPOINT_FENCING_TOKEN_PERSISTENCE_FAILED",
                "Retry after recovery storage is writable and migrated.",
            ) from exc

    def _allocate_token(self, *, resource_key: str, ownership_epoch: int) -> int:
        row = self._connection.execute(
            """
            SELECT ownership_epoch, last_fencing_token
            FROM lease_counters
            WHERE resource_key = ?
            """,
            (resource_key,),
        ).fetchone()
        if row is None:
            token = 1
            self._connection.execute(
                """
                INSERT INTO lease_counters (
                    resource_key,
                    ownership_epoch,
                    last_fencing_token
                )
                VALUES (?, ?, ?)
                """,
                (resource_key, ownership_epoch, token),
            )
            return token

        stored_epoch = int(row[0])
        last_token = int(row[1])
        if stored_epoch > ownership_epoch:
            raise FencingTokenAllocationError(
                "ENDPOINT_FENCING_TOKEN_STALE_EPOCH",
                "Refresh ownership before acquiring a lease for an older endpoint epoch.",
            )
        if stored_epoch < ownership_epoch:
            token = 1
            cursor = self._connection.execute(
                """
                UPDATE lease_counters
                SET
                    ownership_epoch = ?,
                    last_fencing_token = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE resource_key = ?
                    AND ownership_epoch = ?
                """,
                (ownership_epoch, token, resource_key, stored_epoch),
            )
        else:
            token = last_token + 1
            cursor = self._connection.execute(
                """
                UPDATE lease_counters
                SET
                    last_fencing_token = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE resource_key = ?
                    AND ownership_epoch = ?
                    AND last_fencing_token = ?
                """,
                (token, resource_key, ownership_epoch, last_token),
            )
        if cursor.rowcount != 1:
            raise FencingTokenAllocationError(
                "ENDPOINT_FENCING_TOKEN_ALLOCATE_CONFLICT",
                "Retry token allocation after reloading recovery lease counters.",
            )
        return token
