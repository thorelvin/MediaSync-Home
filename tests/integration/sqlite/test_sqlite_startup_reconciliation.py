from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore
from mediasync_home.application.command_receipts import (
    COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
    CommandReceipt,
    CommandReceiptState,
    transition_command_receipt,
)
from mediasync_home.application.outbox import command_effect_outbox_message
from mediasync_home.application.outbox import OutboxMessageState
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationRequest,
    reconcile_engine_host_after_startup,
)


def test_sqlite_engine_host_startup_reconciliation_coordinates_stores(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        receipts = SqliteCommandReceiptStore(connection)
        outbox = SqliteOutboxStore(connection)
        early = receipts.record_received(_receipt("idempotency-early"))
        prepared = _store_prepared_receipt(receipts, "idempotency-prepared")
        message = command_effect_outbox_message(_succeeded_receipt("idempotency-effect"))
        outbox.enqueue_outbox_message(message)
        outbox.claim_next_pending(owner_instance_id="host-old", claim_token="claim-old")

        report = reconcile_engine_host_after_startup(
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id="host-new",
                command_receipt_limit=10,
                outbox_limit=10,
                inactive_outbox_owner_instance_ids=("host-old",),
            ),
            command_receipts=receipts,
            outbox=outbox,
        )

        loaded_early = receipts.load_command_receipt(early.idempotency_key)
        loaded_prepared = receipts.load_command_receipt(prepared.idempotency_key)
        loaded_message = outbox.load_outbox_message(message.message_id)
        assert report.command_receipts is not None
        assert report.command_receipts.scanned == 2
        assert report.command_receipts.rejected_idempotency_keys == (early.idempotency_key,)
        assert report.command_receipts.pending_effect_reconciliation_keys == (
            prepared.idempotency_key,
        )
        assert report.outbox is not None
        assert report.outbox.scanned == 1
        assert report.outbox.requeued_message_ids == (message.message_id,)
        assert loaded_early is not None
        assert loaded_early.state is CommandReceiptState.REJECTED
        assert (
            loaded_early.rejection_reason
            == COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION
        )
        assert loaded_prepared is not None
        assert loaded_prepared.state is CommandReceiptState.EFFECT_PREPARED
        assert loaded_message is not None
        assert loaded_message.state is OutboxMessageState.PENDING
        assert loaded_message.claim_owner_instance_id is None
        assert loaded_message.claim_token is None
        assert loaded_message.claim_generation == 2


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _receipt(idempotency_key: str) -> CommandReceipt:
    return CommandReceipt(
        request_id=f"request-{idempotency_key}",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key=idempotency_key,
        command_name="create_standard_backup_job",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
        expected_entity_revision=7,
    )


def _store_prepared_receipt(
    store: SqliteCommandReceiptStore,
    idempotency_key: str,
) -> CommandReceipt:
    receipt = store.record_received(_receipt(idempotency_key))
    validated = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
    prepared = transition_command_receipt(
        validated,
        CommandReceiptState.EFFECT_PREPARED,
        result_entity_type="standard_backup_job",
        result_entity_id="job-a",
    )
    store.update_command_receipt(prepared)
    return prepared


def _succeeded_receipt(idempotency_key: str) -> CommandReceipt:
    return replace(
        _receipt(idempotency_key),
        state=CommandReceiptState.SUCCEEDED,
        result_entity_type="standard_backup_job",
        result_entity_id="job-a",
    )
