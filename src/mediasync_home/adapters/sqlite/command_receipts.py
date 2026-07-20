from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence

from mediasync_home.application.command_receipts import (
    COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
    CommandReceipt,
    CommandReceiptState,
    CommandReceiptStore,
    CommandReceiptStartupReconciliationReport,
    CommandReceiptStartupReconciliationRequest,
    CommandReceiptStartupReconciliationStore,
    EARLY_RECONCILABLE_COMMAND_RECEIPT_STATES,
    PENDING_EFFECT_RECONCILIATION_COMMAND_RECEIPT_STATES,
    TERMINAL_COMMAND_RECEIPT_STATES,
    ensure_idempotency_compatible,
    validate_command_receipt_startup_reconciliation_request,
)


class SqliteCommandReceiptStoreError(ValueError):
    pass


class SqliteCommandReceiptStore(CommandReceiptStore, CommandReceiptStartupReconciliationStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_received(self, receipt: CommandReceipt) -> CommandReceipt:
        existing = self.load_command_receipt(receipt.idempotency_key)
        if existing is not None:
            return ensure_idempotency_compatible(existing, receipt)
        outer_transaction = self._connection.in_transaction
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
            if not outer_transaction:
                self._connection.commit()
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
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
            return self._load_command_tombstone(idempotency_key)
        return _receipt_from_row(row)

    def compact_terminal_command_receipt(self, idempotency_key: str) -> CommandReceipt:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            receipt = self._load_active_command_receipt(idempotency_key)
            if receipt is None:
                compacted = self._load_command_tombstone(idempotency_key)
                if compacted is None:
                    raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_NOT_FOUND")
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return compacted
            if receipt.state not in TERMINAL_COMMAND_RECEIPT_STATES:
                raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_COMPACTION_REQUIRES_TERMINAL")
            first_seen = self._active_receipt_created_utc(idempotency_key)
            self._connection.execute(
                """
                INSERT INTO command_dedup_tombstones (
                    idempotency_key,
                    request_id,
                    client_instance_id,
                    principal_fingerprint,
                    command_name,
                    payload_hash,
                    protocol_version,
                    schema_version,
                    terminal_state,
                    expected_entity_revision,
                    payload_hash_scope,
                    payload_canonicalization_algorithm,
                    payload_hash_algorithm,
                    result_entity_type,
                    result_entity_id,
                    rejection_reason,
                    terminal_effect_hash,
                    first_seen_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
                    _terminal_effect_hash(receipt),
                    first_seen,
                ),
            )
            cursor = self._connection.execute(
                "DELETE FROM command_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            if cursor.rowcount != 1:
                raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_COMPACTION_DELETE_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return receipt
        except (sqlite3.Error, SqliteCommandReceiptStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteCommandReceiptStoreError):
                raise
            raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_COMPACTION_FAILED") from exc

    def reconcile_non_terminal_after_startup(
        self,
        request: CommandReceiptStartupReconciliationRequest,
    ) -> CommandReceiptStartupReconciliationReport:
        validate_command_receipt_startup_reconciliation_request(request)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                """
                SELECT idempotency_key, state
                FROM command_receipts
                WHERE state IN (
                    'RECEIVED',
                    'VALIDATED',
                    'EFFECT_PREPARED',
                    'ACCEPTED',
                    'RUNNING'
                )
                ORDER BY updated_utc, idempotency_key
                LIMIT ?
                """,
                (request.limit,),
            ).fetchall()

            rejected: list[str] = []
            pending_effect_reconciliation: list[str] = []
            for row in rows:
                idempotency_key = str(row[0])
                state = CommandReceiptState(str(row[1]))
                if state in EARLY_RECONCILABLE_COMMAND_RECEIPT_STATES:
                    cursor = self._connection.execute(
                        """
                        UPDATE command_receipts
                        SET
                            state = 'REJECTED',
                            rejection_reason = ?,
                            updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE idempotency_key = ?
                            AND state = ?
                        """,
                        (
                            COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
                            idempotency_key,
                            state.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SqliteCommandReceiptStoreError(
                            "COMMAND_RECEIPT_RECONCILIATION_STATE_CONFLICT"
                        )
                    rejected.append(idempotency_key)
                elif state in PENDING_EFFECT_RECONCILIATION_COMMAND_RECEIPT_STATES:
                    pending_effect_reconciliation.append(idempotency_key)
                else:
                    raise SqliteCommandReceiptStoreError(
                        "COMMAND_RECEIPT_RECONCILIATION_STATE_UNSUPPORTED"
                    )

            if not outer_transaction:
                self._connection.execute("COMMIT")
            return CommandReceiptStartupReconciliationReport(
                reconciler_instance_id=request.reconciler_instance_id,
                scanned=len(rows),
                rejected_idempotency_keys=tuple(rejected),
                pending_effect_reconciliation_keys=tuple(pending_effect_reconciliation),
            )
        except (sqlite3.Error, SqliteCommandReceiptStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteCommandReceiptStoreError):
                raise
            raise SqliteCommandReceiptStoreError(
                "COMMAND_RECEIPT_RECONCILIATION_FAILED"
            ) from exc

    def _load_active_command_receipt(self, idempotency_key: str) -> CommandReceipt | None:
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
        return _receipt_from_row(row)

    def _load_command_tombstone(self, idempotency_key: str) -> CommandReceipt | None:
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
                terminal_state,
                expected_entity_revision,
                payload_hash_scope,
                payload_canonicalization_algorithm,
                payload_hash_algorithm,
                result_entity_type,
                result_entity_id,
                rejection_reason
            FROM command_dedup_tombstones
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return _receipt_from_row(row)

    def _active_receipt_created_utc(self, idempotency_key: str) -> str:
        row = self._connection.execute(
            "SELECT created_utc FROM command_receipts WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_NOT_FOUND")
        return str(row[0])

    def update_command_receipt(self, receipt: CommandReceipt) -> None:
        outer_transaction = self._connection.in_transaction
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
            if not outer_transaction:
                self._connection.commit()
        except SqliteCommandReceiptStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteCommandReceiptStoreError("COMMAND_RECEIPT_UPDATE_FAILED") from exc


def _receipt_from_row(row: Sequence[object]) -> CommandReceipt:
    return CommandReceipt(
        request_id=str(row[0]),
        client_instance_id=str(row[1]),
        principal_fingerprint=str(row[2]),
        idempotency_key=str(row[3]),
        command_name=str(row[4]),
        payload_hash=str(row[5]),
        protocol_version=_int_field(row[6]),
        schema_version=_int_field(row[7]),
        state=CommandReceiptState(str(row[8])),
        expected_entity_revision=None if row[9] is None else _int_field(row[9]),
        payload_hash_scope=str(row[10]),
        payload_canonicalization_algorithm=str(row[11]),
        payload_hash_algorithm=str(row[12]),
        result_entity_type=None if row[13] is None else str(row[13]),
        result_entity_id=None if row[14] is None else str(row[14]),
        rejection_reason=None if row[15] is None else str(row[15]),
    )


def _int_field(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError("SQLite integer field must be int-compatible")


def _terminal_effect_hash(receipt: CommandReceipt) -> str:
    payload = {
        "command_name": receipt.command_name,
        "idempotency_key": receipt.idempotency_key,
        "rejection_reason": receipt.rejection_reason,
        "result_entity_id": receipt.result_entity_id,
        "result_entity_type": receipt.result_entity_type,
        "terminal_state": receipt.state.value,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
