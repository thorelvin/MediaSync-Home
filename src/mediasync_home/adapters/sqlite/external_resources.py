from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from mediasync_home.application.external_resources import (
    ExternalResourceRecord,
    ExternalResourceState,
    ExternalResourceStateStore,
    ExternalResourceStartupReconciliationReport,
    ExternalResourceStartupReconciliationRequest,
    ExternalResourceType,
    ExternalResourceViolation,
    validate_desired_external_resource_state,
    validate_external_resource_blocked,
    validate_external_resource_claim,
    validate_external_resource_completion,
    validate_external_resource_startup_reconciliation_request,
    validate_expired_external_resource_claim_requeue,
)


class SqliteExternalResourceStateStoreError(ValueError):
    pass


class SqliteExternalResourceStateStore(ExternalResourceStateStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def upsert_desired_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        desired_hash: str,
    ) -> ExternalResourceRecord:
        outer_transaction = self._connection.in_transaction
        try:
            validate_desired_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
                desired_generation=desired_generation,
                desired_hash=desired_hash,
            )
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO external_resource_state (
                        resource_type,
                        resource_id,
                        desired_generation,
                        desired_hash,
                        state
                    )
                    VALUES (?, ?, ?, ?, 'PENDING')
                    """,
                    (resource_type.value, resource_id, desired_generation, desired_hash),
                )
            else:
                if desired_generation < existing.desired_generation:
                    raise SqliteExternalResourceStateStoreError(
                        "EXTERNAL_RESOURCE_DESIRED_GENERATION_REGRESSION"
                    )
                if (
                    desired_generation == existing.desired_generation
                    and desired_hash != existing.desired_hash
                ):
                    raise SqliteExternalResourceStateStoreError(
                        "EXTERNAL_RESOURCE_DESIRED_HASH_CONFLICT"
                    )
                if desired_generation > existing.desired_generation:
                    self._connection.execute(
                        """
                        UPDATE external_resource_state
                        SET
                            desired_generation = ?,
                            desired_hash = ?,
                            state = 'PENDING',
                            claim_owner_instance_id = NULL,
                            claim_generation = claim_generation + 1,
                            claim_token = NULL,
                            claim_started_utc = NULL,
                            claim_ttl_ms = NULL,
                            last_error_code = NULL,
                            row_version = row_version + 1
                        WHERE resource_type = ? AND resource_id = ?
                        """,
                        (desired_generation, desired_hash, resource_type.value, resource_id),
                    )
            loaded = self.load_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if loaded is None:
                raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except (sqlite3.Error, ExternalResourceViolation, SqliteExternalResourceStateStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteExternalResourceStateStoreError):
                raise
            if isinstance(exc, ExternalResourceViolation):
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_VALIDATION_FAILED"
                ) from exc
            raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_UPSERT_FAILED") from exc

    def load_external_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
    ) -> ExternalResourceRecord | None:
        row = self._connection.execute(
            """
            SELECT
                resource_type,
                resource_id,
                desired_generation,
                desired_hash,
                observed_generation,
                observed_hash,
                state,
                claim_owner_instance_id,
                claim_generation,
                claim_token,
                claim_started_utc,
                claim_ttl_ms,
                last_attempt_utc,
                last_success_utc,
                last_error_code,
                attempt_count,
                row_version
            FROM external_resource_state
            WHERE resource_type = ? AND resource_id = ?
            """,
            (resource_type.value, resource_id),
        ).fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    def claim_next_pending_external_resource(
        self,
        *,
        resource_type: ExternalResourceType,
        owner_instance_id: str,
        claim_token: str,
        claim_started_utc: str,
        claim_ttl_ms: int,
    ) -> ExternalResourceRecord | None:
        try:
            validate_external_resource_claim(
                resource_type=resource_type,
                owner_instance_id=owner_instance_id,
                claim_token=claim_token,
                claim_started_utc=claim_started_utc,
                claim_ttl_ms=claim_ttl_ms,
            )
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT resource_id, claim_generation
                FROM external_resource_state
                WHERE resource_type = ? AND state = 'PENDING'
                ORDER BY resource_id
                LIMIT 1
                """,
                (resource_type.value,),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            resource_id = str(row[0])
            claim_generation = int(row[1])
            cursor = self._connection.execute(
                """
                UPDATE external_resource_state
                SET
                    state = 'CLAIMED',
                    claim_owner_instance_id = ?,
                    claim_generation = claim_generation + 1,
                    claim_token = ?,
                    claim_started_utc = ?,
                    claim_ttl_ms = ?,
                    last_attempt_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    attempt_count = attempt_count + 1,
                    row_version = row_version + 1
                WHERE resource_type = ?
                    AND resource_id = ?
                    AND state = 'PENDING'
                    AND claim_generation = ?
                """,
                (
                    owner_instance_id,
                    claim_token,
                    claim_started_utc,
                    claim_ttl_ms,
                    resource_type.value,
                    resource_id,
                    claim_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_CLAIM_CONFLICT")
            claimed = self.load_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if claimed is None:
                raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_LOAD_FAILED")
            self._connection.execute("COMMIT")
            return claimed
        except (sqlite3.Error, ExternalResourceViolation, SqliteExternalResourceStateStoreError) as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteExternalResourceStateStoreError):
                raise
            if isinstance(exc, ExternalResourceViolation):
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_VALIDATION_FAILED"
                ) from exc
            raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_CLAIM_FAILED") from exc

    def requeue_expired_external_resource_claim(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        owner_instance_id: str,
        claim_generation: int,
        claim_token: str,
        requeued_utc: str,
    ) -> ExternalResourceRecord:
        try:
            validate_expired_external_resource_claim_requeue(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_instance_id=owner_instance_id,
                claim_generation=claim_generation,
                claim_token=claim_token,
                requeued_utc=requeued_utc,
            )
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE external_resource_state
                SET
                    state = 'PENDING',
                    claim_owner_instance_id = NULL,
                    claim_generation = claim_generation + 1,
                    claim_token = NULL,
                    claim_started_utc = NULL,
                    claim_ttl_ms = NULL,
                    last_error_code =
                        'EXTERNAL_RESOURCE_CLAIM_REQUEUED_AFTER_MONOTONIC_EXPIRY',
                    row_version = row_version + 1
                WHERE resource_type = ?
                    AND resource_id = ?
                    AND state = 'CLAIMED'
                    AND claim_owner_instance_id = ?
                    AND claim_generation = ?
                    AND claim_token = ?
                """,
                (
                    resource_type.value,
                    resource_id,
                    owner_instance_id,
                    claim_generation,
                    claim_token,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_EXPIRED_CLAIM_MISMATCH"
                )
            loaded = self.load_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if loaded is None:
                raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_LOAD_FAILED")
            self._connection.execute("COMMIT")
            return loaded
        except (
            sqlite3.Error,
            ExternalResourceViolation,
            SqliteExternalResourceStateStoreError,
        ) as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteExternalResourceStateStoreError):
                raise
            if isinstance(exc, ExternalResourceViolation):
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_VALIDATION_FAILED"
                ) from exc
            raise SqliteExternalResourceStateStoreError(
                "EXTERNAL_RESOURCE_EXPIRED_CLAIM_REQUEUE_FAILED"
            ) from exc

    def mark_external_resource_in_sync(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        claim_token: str,
        observed_hash: str,
    ) -> ExternalResourceRecord:
        try:
            validate_external_resource_completion(
                resource_type=resource_type,
                resource_id=resource_id,
                desired_generation=desired_generation,
                claim_token=claim_token,
                observed_hash=observed_hash,
            )
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE external_resource_state
                SET
                    observed_generation = desired_generation,
                    observed_hash = ?,
                    state = 'IN_SYNC',
                    claim_owner_instance_id = NULL,
                    claim_token = NULL,
                    claim_started_utc = NULL,
                    claim_ttl_ms = NULL,
                    last_success_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    last_error_code = NULL,
                    row_version = row_version + 1
                WHERE resource_type = ?
                    AND resource_id = ?
                    AND desired_generation = ?
                    AND desired_hash = ?
                    AND state = 'CLAIMED'
                    AND claim_token = ?
                """,
                (
                    observed_hash,
                    resource_type.value,
                    resource_id,
                    desired_generation,
                    observed_hash,
                    claim_token,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_COMPLETION_CLAIM_MISMATCH"
                )
            loaded = self.load_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if loaded is None:
                raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_LOAD_FAILED")
            self._connection.execute("COMMIT")
            return loaded
        except (sqlite3.Error, ExternalResourceViolation, SqliteExternalResourceStateStoreError) as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteExternalResourceStateStoreError):
                raise
            if isinstance(exc, ExternalResourceViolation):
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_VALIDATION_FAILED"
                ) from exc
            raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_COMPLETE_FAILED") from exc

    def mark_external_resource_blocked(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        claim_token: str,
        error_code: str,
    ) -> ExternalResourceRecord:
        try:
            validate_external_resource_blocked(
                resource_type=resource_type,
                resource_id=resource_id,
                claim_token=claim_token,
                error_code=error_code,
            )
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE external_resource_state
                SET
                    state = 'BLOCKED',
                    claim_owner_instance_id = NULL,
                    claim_token = NULL,
                    claim_started_utc = NULL,
                    claim_ttl_ms = NULL,
                    last_error_code = ?,
                    row_version = row_version + 1
                WHERE resource_type = ?
                    AND resource_id = ?
                    AND state = 'CLAIMED'
                    AND claim_token = ?
                """,
                (error_code, resource_type.value, resource_id, claim_token),
            )
            if cursor.rowcount != 1:
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_BLOCK_CLAIM_MISMATCH"
                )
            loaded = self.load_external_resource_state(
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if loaded is None:
                raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_LOAD_FAILED")
            self._connection.execute("COMMIT")
            return loaded
        except (sqlite3.Error, ExternalResourceViolation, SqliteExternalResourceStateStoreError) as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteExternalResourceStateStoreError):
                raise
            if isinstance(exc, ExternalResourceViolation):
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_VALIDATION_FAILED"
                ) from exc
            raise SqliteExternalResourceStateStoreError("EXTERNAL_RESOURCE_BLOCK_FAILED") from exc

    def requeue_claimed_after_startup(
        self,
        request: ExternalResourceStartupReconciliationRequest,
    ) -> ExternalResourceStartupReconciliationReport:
        try:
            validate_external_resource_startup_reconciliation_request(request)
            owner_placeholders = ", ".join("?" for _ in request.inactive_owner_instance_ids)
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                f"""
                SELECT resource_id, claim_generation
                FROM external_resource_state
                WHERE resource_type = ?
                    AND state = 'CLAIMED'
                    AND claim_owner_instance_id IN ({owner_placeholders})
                ORDER BY claim_started_utc, resource_id
                LIMIT ?
                """,
                (
                    request.resource_type.value,
                    *request.inactive_owner_instance_ids,
                    request.limit,
                ),
            ).fetchall()

            requeued: list[str] = []
            for row in rows:
                resource_id = str(row[0])
                claim_generation = int(row[1])
                cursor = self._connection.execute(
                    f"""
                    UPDATE external_resource_state
                    SET
                        state = 'PENDING',
                        claim_owner_instance_id = NULL,
                        claim_generation = claim_generation + 1,
                        claim_token = NULL,
                        claim_started_utc = NULL,
                        claim_ttl_ms = NULL,
                        last_error_code = 'EXTERNAL_RESOURCE_CLAIM_REQUEUED_AFTER_STARTUP',
                        row_version = row_version + 1
                    WHERE resource_type = ?
                        AND resource_id = ?
                        AND state = 'CLAIMED'
                        AND claim_generation = ?
                        AND claim_owner_instance_id IN ({owner_placeholders})
                    """,
                    (
                        request.resource_type.value,
                        resource_id,
                        claim_generation,
                        *request.inactive_owner_instance_ids,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SqliteExternalResourceStateStoreError(
                        "EXTERNAL_RESOURCE_RECONCILIATION_CLAIM_CONFLICT"
                    )
                requeued.append(resource_id)

            self._connection.execute("COMMIT")
            return ExternalResourceStartupReconciliationReport(
                reconciler_instance_id=request.reconciler_instance_id,
                resource_type=request.resource_type,
                scanned=len(rows),
                requeued_resource_ids=tuple(requeued),
            )
        except (sqlite3.Error, ExternalResourceViolation, SqliteExternalResourceStateStoreError) as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteExternalResourceStateStoreError):
                raise
            if isinstance(exc, ExternalResourceViolation):
                raise SqliteExternalResourceStateStoreError(
                    "EXTERNAL_RESOURCE_VALIDATION_FAILED"
                ) from exc
            raise SqliteExternalResourceStateStoreError(
                "EXTERNAL_RESOURCE_RECONCILIATION_FAILED"
            ) from exc


def _record_from_row(row: Sequence[object]) -> ExternalResourceRecord:
    return ExternalResourceRecord(
        resource_type=ExternalResourceType(str(row[0])),
        resource_id=str(row[1]),
        desired_generation=_int_field(row[2]),
        desired_hash=str(row[3]),
        observed_generation=None if row[4] is None else _int_field(row[4]),
        observed_hash=None if row[5] is None else str(row[5]),
        state=ExternalResourceState(str(row[6])),
        claim_owner_instance_id=None if row[7] is None else str(row[7]),
        claim_generation=_int_field(row[8]),
        claim_token=None if row[9] is None else str(row[9]),
        claim_started_utc=None if row[10] is None else str(row[10]),
        claim_ttl_ms=None if row[11] is None else _int_field(row[11]),
        last_attempt_utc=None if row[12] is None else str(row[12]),
        last_success_utc=None if row[13] is None else str(row[13]),
        last_error_code=None if row[14] is None else str(row[14]),
        attempt_count=_int_field(row[15]),
        row_version=_int_field(row[16]),
    )


def _int_field(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError("SQLite integer field must be int-compatible")
