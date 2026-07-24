from __future__ import annotations

import sqlite3

from mediasync_home.adapters.endpoint_leases import (
    FencingTokenAllocationError,
    FencingTokenStore,
    ResourceLeaseRegistrationError,
    ResourceLeaseStore,
)


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
        return _allocate_token(self._connection, resource_key=resource_key, ownership_epoch=ownership_epoch)


class SqliteResourceLeaseStore(ResourceLeaseStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def reconcile_stale_active_resource_lease_after_lock_acquired(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
    ) -> tuple[str, ...]:
        if not resource_key.strip() or not endpoint_id.strip():
            raise ResourceLeaseRegistrationError(
                "ENDPOINT_RESOURCE_LEASE_INVALID_REQUEST",
                "Reconcile resource leases with a non-empty resource key and endpoint id.",
            )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                """
                SELECT lease_id, endpoint_id, os_lock_kind
                FROM resource_leases
                WHERE resource_key = ?
                    AND lease_mode = 'EXCLUSIVE'
                    AND state = 'ACQUIRED'
                ORDER BY acquired_utc, lease_id
                """,
                (resource_key,),
            ).fetchall()
            if not rows:
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return ()

            mismatched = next(
                (
                    row
                    for row in rows
                    if str(row[1]) != endpoint_id or str(row[2]) != "LOCAL_OS_HANDLE"
                ),
                None,
            )
            if mismatched is not None:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                raise ResourceLeaseRegistrationError(
                    "ENDPOINT_RESOURCE_LEASE_ACTIVE_CONFLICT",
                    "Review the active endpoint lease before reconciling stale local lock state.",
                )

            lease_ids = tuple(str(row[0]) for row in rows)
            placeholders = ", ".join("?" for _ in lease_ids)
            cursor = self._connection.execute(
                f"""
                UPDATE resource_leases
                SET
                    state = 'RELEASED',
                    heartbeat_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    released_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE lease_id IN ({placeholders})
                    AND resource_key = ?
                    AND state = 'ACQUIRED'
                    AND lease_mode = 'EXCLUSIVE'
                    AND os_lock_kind = 'LOCAL_OS_HANDLE'
                """,
                (*lease_ids, resource_key),
            )
            if cursor.rowcount != len(lease_ids):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                raise ResourceLeaseRegistrationError(
                    "ENDPOINT_RESOURCE_LEASE_RECONCILIATION_CONFLICT",
                    "Reload recovery lease state before retrying local lease reconciliation.",
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return lease_ids
        except ResourceLeaseRegistrationError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ResourceLeaseRegistrationError(
                "ENDPOINT_RESOURCE_LEASE_PERSISTENCE_FAILED",
                "Retry after recovery storage is writable and migrated.",
            ) from exc

    def register_acquired_resource_lease(
        self,
        *,
        lease_id: str,
        resource_key: str,
        owner_instance_id: str,
        ownership_epoch: int,
        run_id: str,
        run_target_id: str,
        endpoint_id: str,
        endpoint_generation: int | None,
        lease_mode: str,
        os_lock_kind: str,
    ) -> int:
        _validate_resource_lease_request(
            lease_id=lease_id,
            resource_key=resource_key,
            owner_instance_id=owner_instance_id,
            ownership_epoch=ownership_epoch,
            run_id=run_id,
            run_target_id=run_target_id,
            endpoint_id=endpoint_id,
            endpoint_generation=endpoint_generation,
            lease_mode=lease_mode,
            os_lock_kind=os_lock_kind,
        )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._reject_active_exclusive_resource_lease(resource_key)
            fencing_token = _allocate_token(
                self._connection,
                resource_key=resource_key,
                ownership_epoch=ownership_epoch,
            )
            self._connection.execute(
                """
                INSERT INTO resource_leases (
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
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACQUIRED')
                """,
                (
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
                ),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return fencing_token
        except FencingTokenAllocationError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except ResourceLeaseRegistrationError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ResourceLeaseRegistrationError(
                "ENDPOINT_RESOURCE_LEASE_PERSISTENCE_FAILED",
                "Retry after recovery storage is writable and migrated.",
            ) from exc

    def release_resource_lease(self, *, lease_id: str) -> None:
        if not lease_id.strip():
            raise ResourceLeaseRegistrationError(
                "ENDPOINT_RESOURCE_LEASE_INVALID_REQUEST",
                "Release resource leases with a non-empty lease id.",
            )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE resource_leases
                SET
                    state = 'RELEASED',
                    heartbeat_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    released_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE lease_id = ?
                    AND state = 'ACQUIRED'
                """,
                (lease_id,),
            )
            if cursor.rowcount != 1:
                raise ResourceLeaseRegistrationError(
                    "ENDPOINT_RESOURCE_LEASE_RELEASE_CONFLICT",
                    "Reload recovery lease state before retrying release reconciliation.",
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except ResourceLeaseRegistrationError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ResourceLeaseRegistrationError(
                "ENDPOINT_RESOURCE_LEASE_PERSISTENCE_FAILED",
                "Retry after recovery storage is writable and migrated.",
            ) from exc

    def _reject_active_exclusive_resource_lease(self, resource_key: str) -> None:
        row = self._connection.execute(
            """
            SELECT lease_id
            FROM resource_leases
            WHERE resource_key = ?
                AND lease_mode = 'EXCLUSIVE'
                AND state = 'ACQUIRED'
            """,
            (resource_key,),
        ).fetchone()
        if row is not None:
            raise ResourceLeaseRegistrationError(
                "ENDPOINT_RESOURCE_LEASE_ACTIVE_CONFLICT",
                "Release or reconcile the active endpoint lease before acquiring another one.",
            )


def _allocate_token(
    connection: sqlite3.Connection,
    *,
    resource_key: str,
    ownership_epoch: int,
) -> int:
    row = connection.execute(
        """
        SELECT ownership_epoch, last_fencing_token
        FROM lease_counters
        WHERE resource_key = ?
        """,
        (resource_key,),
    ).fetchone()
    if row is None:
        token = 1
        connection.execute(
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
        cursor = connection.execute(
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
        cursor = connection.execute(
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


def _validate_resource_lease_request(
    *,
    lease_id: str,
    resource_key: str,
    owner_instance_id: str,
    ownership_epoch: int,
    run_id: str,
    run_target_id: str,
    endpoint_id: str,
    endpoint_generation: int | None,
    lease_mode: str,
    os_lock_kind: str,
) -> None:
    if (
        not lease_id.strip()
        or not resource_key.strip()
        or not owner_instance_id.strip()
        or ownership_epoch < 1
        or not run_id.strip()
        or not run_target_id.strip()
        or not endpoint_id.strip()
        or (endpoint_generation is not None and endpoint_generation < 1)
        or lease_mode != "EXCLUSIVE"
        or os_lock_kind != "LOCAL_OS_HANDLE"
    ):
        raise ResourceLeaseRegistrationError(
            "ENDPOINT_RESOURCE_LEASE_INVALID_REQUEST",
            "Register resource leases with validated owner, run, endpoint, lock and epoch data.",
        )
