from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.migrations import recovery_migration_plan
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.application.command_receipts import (
    COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
    CommandReceipt,
    CommandReceiptState,
    transition_command_receipt,
)
from mediasync_home.application.outbox import command_effect_outbox_message
from mediasync_home.application.outbox import OutboxMessageState
from mediasync_home.application.recovery_operations import (
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.recovery_reconciliation import (
    RecoveryOperationStartupClassification,
)
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationRequest,
    reconcile_engine_host_after_startup,
)


def test_sqlite_engine_host_startup_reconciliation_coordinates_stores(
    tmp_path: Path,
) -> None:
    catalog_database = tmp_path / "catalog.sqlite"
    recovery_database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(catalog_database) as catalog_connection:
        recovery_connection = sqlite3.connect(recovery_database)
        try:
            _prepare_catalog(catalog_connection, catalog_database)
            _prepare_recovery(recovery_connection, recovery_database)
            receipts = SqliteCommandReceiptStore(catalog_connection)
            outbox = SqliteOutboxStore(catalog_connection)
            recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
            _register_resource_lease(recovery_connection)
            recovery_operations.record_planned_operation(
                _planned_operation(),
                process_instance_id="host-old",
            )
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
                    recovery_operation_limit=10,
                    inactive_outbox_owner_instance_ids=("host-old",),
                ),
                command_receipts=receipts,
                outbox=outbox,
                recovery_operations=recovery_operations,
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
            assert report.recovery_operations is not None
            assert report.recovery_operations.scanned == 1
            assert report.recovery_operations.findings[0].operation_id == "op-a"
            assert report.recovery_operations.findings[0].classification is (
                RecoveryOperationStartupClassification.DISCARD_UNVERIFIED_INBOX
            )
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
        finally:
            recovery_connection.close()


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _prepare_recovery(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())


def _register_resource_lease(connection: sqlite3.Connection) -> None:
    assert SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    ) == 1


def _planned_operation():
    return planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="op-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Pictures/A.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
    )


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
