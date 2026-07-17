from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore, SqliteOutboxStoreError
from mediasync_home.application.command_receipts import CommandReceipt, CommandReceiptState
from mediasync_home.application.outbox import (
    OutboxClaimTokenFactory,
    OutboxDeliveryOutcome,
    OutboxDeliveryPort,
    OutboxDeliveryResult,
    OutboxMessage,
    OutboxMessageState,
    command_effect_outbox_message,
    dispatch_one_outbox_message,
)


def test_sqlite_outbox_enqueue_claim_and_deliver(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)
        message = command_effect_outbox_message(_succeeded_receipt())

        stored = store.enqueue_outbox_message(message)
        claimed = store.claim_next_pending(owner_instance_id="host-a", claim_token="claim-a")
        delivered = store.mark_delivered(
            message_id=message.message_id,
            claim_token="claim-a",
            terminal_effect_hash="b" * 64,
        )

        assert stored == message
        assert claimed is not None
        assert claimed.state is OutboxMessageState.CLAIMED
        assert claimed.claim_owner_instance_id == "host-a"
        assert claimed.claim_generation == 1
        assert claimed.claim_token == "claim-a"
        assert claimed.attempt_count == 1
        assert delivered.state is OutboxMessageState.DELIVERED
        assert delivered.terminal_effect_hash == "b" * 64
        assert _row_count(connection, "outbox_messages") == 1
        assert _row_count(connection, "effect_dedup_tombstones") == 1


def test_sqlite_outbox_replays_same_message_and_rejects_payload_conflict(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)
        message = command_effect_outbox_message(_succeeded_receipt())
        store.enqueue_outbox_message(message)

        replay = store.enqueue_outbox_message(message)

        assert replay == message
        with pytest.raises(SqliteOutboxStoreError, match="OUTBOX_IDEMPOTENCY_CONFLICT"):
            store.enqueue_outbox_message(replace(message, payload_hash="c" * 64))


def test_sqlite_outbox_delivery_requires_matching_claim_token(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)
        message = command_effect_outbox_message(_succeeded_receipt())
        store.enqueue_outbox_message(message)
        store.claim_next_pending(owner_instance_id="host-a", claim_token="claim-a")

        with pytest.raises(SqliteOutboxStoreError, match="OUTBOX_DELIVERY_CLAIM_MISMATCH"):
            store.mark_delivered(
                message_id=message.message_id,
                claim_token="late-claim",
                terminal_effect_hash="b" * 64,
            )


def test_sqlite_outbox_dead_letter_requires_matching_claim_token(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)
        message = command_effect_outbox_message(_succeeded_receipt())
        store.enqueue_outbox_message(message)
        store.claim_next_pending(owner_instance_id="host-a", claim_token="claim-a")

        with pytest.raises(SqliteOutboxStoreError, match="OUTBOX_DEAD_LETTER_CLAIM_MISMATCH"):
            store.mark_dead_letter(
                message_id=message.message_id,
                claim_token="late-claim",
                error_code="PERMANENT_FAILURE",
            )


def test_sqlite_outbox_dispatcher_delivers_without_open_transaction(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)
        message = command_effect_outbox_message(_succeeded_receipt())
        store.enqueue_outbox_message(message)
        delivery = _AssertingDeliveryPort(
            connection,
            OutboxDeliveryResult(OutboxDeliveryOutcome.DELIVERED, terminal_effect_hash="b" * 64),
        )

        result = dispatch_one_outbox_message(
            store=store,
            delivery=delivery,
            owner_instance_id="host-a",
            claim_tokens=_FixedClaimTokenFactory("claim-a"),
        )

        loaded = store.load_outbox_message(message.message_id)
        assert result.claimed is True
        assert result.delivered is True
        assert result.dead_lettered is False
        assert delivery.saw_open_transaction is False
        assert loaded is not None
        assert loaded.state is OutboxMessageState.DELIVERED
        assert loaded.terminal_effect_hash == "b" * 64
        assert _row_count(connection, "effect_dedup_tombstones") == 1


def test_sqlite_outbox_dispatcher_dead_letters_permanent_failure(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)
        message = command_effect_outbox_message(_succeeded_receipt())
        store.enqueue_outbox_message(message)

        result = dispatch_one_outbox_message(
            store=store,
            delivery=_AssertingDeliveryPort(
                connection,
                OutboxDeliveryResult(
                    OutboxDeliveryOutcome.DEAD_LETTER,
                    error_code="PERMANENT_FAILURE",
                ),
            ),
            owner_instance_id="host-a",
            claim_tokens=_FixedClaimTokenFactory("claim-a"),
        )

        loaded = store.load_outbox_message(message.message_id)
        assert result.claimed is True
        assert result.dead_lettered is True
        assert loaded is not None
        assert loaded.state is OutboxMessageState.DEAD_LETTER
        assert loaded.last_error_code == "PERMANENT_FAILURE"
        assert _row_count(connection, "effect_dedup_tombstones") == 0


def test_sqlite_outbox_returns_none_when_no_pending_message(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteOutboxStore(connection)

        assert store.claim_next_pending(owner_instance_id="host-a", claim_token="claim-a") is None


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _succeeded_receipt() -> CommandReceipt:
    return CommandReceipt(
        request_id="request-a",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key="idempotency-a",
        command_name="START_RUN",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
        state=CommandReceiptState.SUCCEEDED,
        result_entity_type="run",
        result_entity_id="run-a",
    )


class _FixedClaimTokenFactory(OutboxClaimTokenFactory):
    def __init__(self, claim_token: str) -> None:
        self._claim_token = claim_token

    def new_claim_token(self) -> str:
        return self._claim_token


class _AssertingDeliveryPort(OutboxDeliveryPort):
    def __init__(
        self,
        connection: sqlite3.Connection,
        result: OutboxDeliveryResult,
    ) -> None:
        self._connection = connection
        self._result = result
        self.saw_open_transaction: bool | None = None
        self.message: OutboxMessage | None = None

    def deliver_outbox_message(self, message: OutboxMessage) -> OutboxDeliveryResult:
        self.message = message
        self.saw_open_transaction = self._connection.in_transaction
        return self._result


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
