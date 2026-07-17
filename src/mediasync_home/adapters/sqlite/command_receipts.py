from __future__ import annotations

import sqlite3

from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptState,
    CommandReceiptStore,
    ensure_idempotency_compatible,
)


class SqliteCommandReceiptStoreError(ValueError):
    pass


class SqliteCommandReceiptStore(CommandReceiptStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_received(self, receipt: CommandReceipt) -> CommandReceipt:
        existing = self.load_command_receipt(receipt.idempotency_key)
        if existing is not None:
            return ensure_idempotency_compatible(existing, receipt)
        try:
            self._connection.execute(
                """
                INSERT INTO command_receipts (
                    idempotency_key,
                    request_id,
                    client_instance_id,
                    principal_fingerprint,
                    command_name,
                    payload_hash,
                    protocol_version,
                    schema_version,
                    state,
                    expected_entity_revision,
                    payload_hash_scope,
                    payload_canonicalization_algorithm,
                    payload_hash_algorithm,
                    result_entity_type,
                    result_entity_id,
                    rejection_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _receipt_parameters(receipt),
            )
            self._connection.commit()
        except sqlite3.Error as exc:
            raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_RECORD_FAILED") from exc
        return receipt

    def load_command_receipt(self, idempotency_key: str) -> CommandReceipt | None:
        row = self._connection.execute(
            """
            SELECT
                request_id,
                client_instance_id,
                principal_fingerprint,
                idempotency_key,
                command_name,
                payload_hash,
                protocol_version,
                schema_version,
                state,
                expected_entity_revision,
                payload_hash_scope,
                payload_canonicalization_algorithm,
                payload_hash_algorithm,
                result_entity_type,
                result_entity_id,
                rejection_reason
            FROM command_receipts
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return CommandReceipt(
            request_id=str(row[0]),
            client_instance_id=str(row[1]),
            principal_fingerprint=str(row[2]),
            idempotency_key=str(row[3]),
            command_name=str(row[4]),
            payload_hash=str(row[5]),
            protocol_version=int(row[6]),
            schema_version=int(row[7]),
            state=CommandReceiptState(str(row[8])),
            expected_entity_revision=None if row[9] is None else int(row[9]),
            payload_hash_scope=str(row[10]),
            payload_canonicalization_algorithm=str(row[11]),
            payload_hash_algorithm=str(row[12]),
            result_entity_type=None if row[13] is None else str(row[13]),
            result_entity_id=None if row[14] is None else str(row[14]),
            rejection_reason=None if row[15] is None else str(row[15]),
        )

    def update_command_receipt(self, receipt: CommandReceipt) -> None:
        try:
            cursor = self._connection.execute(
                """
                UPDATE command_receipts
                SET
                    request_id = ?,
                    client_instance_id = ?,
                    principal_fingerprint = ?,
                    command_name = ?,
                    payload_hash = ?,
                    protocol_version = ?,
                    schema_version = ?,
                    state = ?,
                    expected_entity_revision = ?,
                    payload_hash_scope = ?,
                    payload_canonicalization_algorithm = ?,
                    payload_hash_algorithm = ?,
                    result_entity_type = ?,
                    result_entity_id = ?,
                    rejection_reason = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE idempotency_key = ?
                """,
                (
                    receipt.request_id,
                    receipt.client_instance_id,
                    receipt.principal_fingerprint,
                    receipt.command_name,
                    receipt.payload_hash,
                    receipt.protocol_version,
                    receipt.schema_version,
                    receipt.state.value,
                    receipt.expected_entity_revision,
                    receipt.payload_hash_scope,
                    receipt.payload_canonicalization_algorithm,
                    receipt.payload_hash_algorithm,
                    receipt.result_entity_type,
                    receipt.result_entity_id,
                    receipt.rejection_reason,
                    receipt.idempotency_key,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_NOT_FOUND")
            self._connection.commit()
        except sqlite3.Error as exc:
            raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_UPDATE_FAILED") from exc


def _receipt_parameters(receipt: CommandReceipt) -> tuple[object, ...]:
    return (
        receipt.idempotency_key,
        receipt.request_id,
        receipt.client_instance_id,
        receipt.principal_fingerprint,
        receipt.command_name,
        receipt.payload_hash,
        receipt.protocol_version,
        receipt.schema_version,
        receipt.state.value,
        receipt.expected_entity_revision,
        receipt.payload_hash_scope,
        receipt.payload_canonicalization_algorithm,
        receipt.payload_hash_algorithm,
        receipt.result_entity_type,
        receipt.result_entity_id,
        receipt.rejection_reason,
    )
