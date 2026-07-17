from __future__ import annotations

import sqlite3

from mediasync_home.application.outbox import (
    OutboxMessage,
    OutboxMessageState,
    OutboxStore,
)


class SqliteOutboxStoreError(ValueError):
    pass


class SqliteOutboxStore(OutboxStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def enqueue_outbox_message(self, message: OutboxMessage) -> OutboxMessage:
        existing = self._load_by_idempotency_key(message.idempotency_key)
        if existing is not None:
            if existing.payload_hash != message.payload_hash:
                raise SqliteOutboxStoreError("OUTBOX_IDEMPOTENCY_CONFLICT")
            return existing

        outer_transaction = self._connection.in_transaction
        try:
            self._connection.execute(
                """
                INSERT INTO outbox_messages (
                    id,
                    message_type,
                    aggregate_type,
                    aggregate_id,
                    idempotency_key,
                    payload_json,
                    payload_hash,
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.message_type,
                    message.aggregate_type,
                    message.aggregate_id,
                    message.idempotency_key,
                    message.payload_json,
                    message.payload_hash,
                    message.state.value,
                ),
            )
            if not outer_transaction:
                self._connection.commit()
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteOutboxStoreError("OUTBOX_ENQUEUE_FAILED") from exc
        return message

    def load_outbox_message(self, message_id: str) -> OutboxMessage | None:
        return self._load_one("WHERE id = ?", (message_id,))

    def claim_next_pending(
        self,
        *,
        owner_instance_id: str,
        claim_token: str,
    ) -> OutboxMessage | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT id, claim_generation
                FROM outbox_messages
                WHERE state = 'PENDING'
                    AND next_attempt_utc <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ORDER BY next_attempt_utc, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return None

            message_id = str(row[0])
            current_generation = int(row[1])
            cursor = self._connection.execute(
                """
                UPDATE outbox_messages
                SET
                    state = 'CLAIMED',
                    claim_owner_instance_id = ?,
                    claim_generation = claim_generation + 1,
                    claim_token = ?,
                    claim_started_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    attempt_count = attempt_count + 1,
                    last_attempt_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'PENDING'
                    AND claim_generation = ?
                """,
                (owner_instance_id, claim_token, message_id, current_generation),
            )
            if cursor.rowcount != 1:
                raise SqliteOutboxStoreError("OUTBOX_CLAIM_CONFLICT")
            claimed = self.load_outbox_message(message_id)
            if claimed is None:
                raise SqliteOutboxStoreError("OUTBOX_CLAIM_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return claimed
        except (sqlite3.Error, SqliteOutboxStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteOutboxStoreError):
                raise
            raise SqliteOutboxStoreError("OUTBOX_CLAIM_FAILED") from exc

    def mark_delivered(
        self,
        *,
        message_id: str,
        claim_token: str,
        terminal_effect_hash: str,
    ) -> OutboxMessage:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            message = self.load_outbox_message(message_id)
            if message is None:
                raise SqliteOutboxStoreError("OUTBOX_MESSAGE_NOT_FOUND")
            cursor = self._connection.execute(
                """
                UPDATE outbox_messages
                SET
                    state = 'DELIVERED',
                    delivered_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    terminal_effect_hash = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'CLAIMED'
                    AND claim_token = ?
                    AND payload_hash = ?
                """,
                (terminal_effect_hash, message_id, claim_token, message.payload_hash),
            )
            if cursor.rowcount != 1:
                raise SqliteOutboxStoreError("OUTBOX_DELIVERY_CLAIM_MISMATCH")
            self._connection.execute(
                """
                INSERT INTO effect_dedup_tombstones (
                    deduplication_key,
                    effect_kind,
                    payload_hash,
                    terminal_state,
                    effect_entity_type,
                    effect_entity_id,
                    terminal_effect_hash
                )
                VALUES (?, 'outbox', ?, 'DELIVERED', ?, ?, ?)
                ON CONFLICT(deduplication_key) DO UPDATE SET
                    payload_hash = excluded.payload_hash,
                    terminal_state = excluded.terminal_state,
                    effect_entity_type = excluded.effect_entity_type,
                    effect_entity_id = excluded.effect_entity_id,
                    terminal_effect_hash = excluded.terminal_effect_hash,
                    compacted_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    message.idempotency_key,
                    message.payload_hash,
                    message.aggregate_type,
                    message.aggregate_id,
                    terminal_effect_hash,
                ),
            )
            delivered = self.load_outbox_message(message_id)
            if delivered is None:
                raise SqliteOutboxStoreError("OUTBOX_DELIVERY_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return delivered
        except (sqlite3.Error, SqliteOutboxStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteOutboxStoreError):
                raise
            raise SqliteOutboxStoreError("OUTBOX_DELIVERY_FAILED") from exc

    def _load_by_idempotency_key(self, idempotency_key: str) -> OutboxMessage | None:
        return self._load_one("WHERE idempotency_key = ?", (idempotency_key,))

    def _load_one(
        self,
        where_clause: str,
        parameters: tuple[object, ...],
    ) -> OutboxMessage | None:
        row = self._connection.execute(
            f"""
            SELECT
                id,
                message_type,
                aggregate_type,
                aggregate_id,
                idempotency_key,
                payload_json,
                payload_hash,
                state,
                claim_owner_instance_id,
                claim_generation,
                claim_token,
                attempt_count,
                terminal_effect_hash,
                last_error_code
            FROM outbox_messages
            {where_clause}
            """,
            parameters,
        ).fetchone()
        if row is None:
            return None
        return OutboxMessage(
            message_id=str(row[0]),
            message_type=str(row[1]),
            aggregate_type=str(row[2]),
            aggregate_id=str(row[3]),
            idempotency_key=str(row[4]),
            payload_json=str(row[5]),
            payload_hash=str(row[6]),
            state=OutboxMessageState(str(row[7])),
            claim_owner_instance_id=None if row[8] is None else str(row[8]),
            claim_generation=int(row[9]),
            claim_token=None if row[10] is None else str(row[10]),
            attempt_count=int(row[11]),
            terminal_effect_hash=None if row[12] is None else str(row[12]),
            last_error_code=None if row[13] is None else str(row[13]),
        )
