from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.command_receipts import (
    SqliteCommandReceiptStore,
    SqliteCommandReceiptStoreError,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.job_catalog import SqliteStandardBackupJobCatalog
from mediasync_home.adapters.sqlite.job_draft_store import SqliteJobDraftStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore, SqliteOutboxStoreError
from mediasync_home.adapters.sqlite.transactions import SqliteImmediateTransactionRunner
from mediasync_home.application.command_receipts import (
    COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
    CommandReceipt,
    CommandReceiptConflict,
    CommandReceiptState,
    CommandReceiptStartupReconciliationRequest,
    transition_command_receipt,
)
from mediasync_home.application.job_creation import (
    JobCreationCommandName,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
)
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.outbox import OutboxMessage
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService


class FixedStandardBackupJobIdFactory(StandardBackupJobIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_standard_backup_job_ids(self) -> StandardBackupJobIds:
        self.calls += 1
        return StandardBackupJobIds(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
        )


def test_sqlite_command_receipts_roundtrip_received_receipt(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        receipt = _receipt()

        stored = store.record_received(receipt)

        assert stored == receipt
        assert store.load_command_receipt("idempotency-a") == receipt


def test_sqlite_command_receipts_replay_same_idempotency_key(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        receipt = _receipt()
        store.record_received(receipt)

        replay = store.record_received(replace(receipt, request_id="request-retry"))

        assert replay == receipt
        assert _row_count(connection, "command_receipts") == 1


def test_sqlite_command_receipts_reject_idempotency_conflict(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        receipt = _receipt()
        store.record_received(receipt)

        with pytest.raises(CommandReceiptConflict, match="COMMAND_IDEMPOTENCY_CONFLICT:payload_hash"):
            store.record_received(replace(receipt, request_id="request-retry", payload_hash="b" * 64))


def test_sqlite_command_receipts_update_state_and_result(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        received = store.record_received(_receipt())

        validated = transition_command_receipt(received, CommandReceiptState.VALIDATED)
        store.update_command_receipt(validated)
        prepared = transition_command_receipt(validated, CommandReceiptState.EFFECT_PREPARED)
        accepted = transition_command_receipt(prepared, CommandReceiptState.ACCEPTED)
        succeeded = transition_command_receipt(
            accepted,
            CommandReceiptState.SUCCEEDED,
            result_entity_type="standard_backup_job",
            result_entity_id="job-a",
        )
        store.update_command_receipt(succeeded)

        loaded = store.load_command_receipt("idempotency-a")
        assert loaded == succeeded


def test_sqlite_command_receipts_compact_terminal_receipt_and_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        received = store.record_received(_receipt())
        succeeded = _succeeded_receipt(received)
        store.update_command_receipt(succeeded)

        compacted = store.compact_terminal_command_receipt("idempotency-a")
        replay = store.record_received(replace(received, request_id="request-retry"))

        assert compacted == succeeded
        assert replay == succeeded
        assert store.load_command_receipt("idempotency-a") == succeeded
        assert _row_count(connection, "command_receipts") == 0
        assert _row_count(connection, "command_dedup_tombstones") == 1


def test_sqlite_command_receipts_tombstone_rejects_payload_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        received = store.record_received(_receipt())
        store.update_command_receipt(_succeeded_receipt(received))
        store.compact_terminal_command_receipt("idempotency-a")

        with pytest.raises(CommandReceiptConflict, match="COMMAND_IDEMPOTENCY_CONFLICT:payload_hash"):
            store.record_received(replace(received, request_id="request-retry", payload_hash="b" * 64))

        assert _row_count(connection, "command_receipts") == 0
        assert _row_count(connection, "command_dedup_tombstones") == 1


def test_sqlite_command_receipts_compaction_requires_terminal_receipt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        store.record_received(_receipt())

        with pytest.raises(
            SqliteCommandReceiptStoreError,
            match="COMMAND_RECEIPT_COMPACTION_REQUIRES_TERMINAL",
        ):
            store.compact_terminal_command_receipt("idempotency-a")

        assert _row_count(connection, "command_receipts") == 1
        assert _row_count(connection, "command_dedup_tombstones") == 0


def test_sqlite_command_receipts_startup_reconciliation_rejects_early_receipts_bounded(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        first = store.record_received(_receipt_with_key("idempotency-a"))
        second = store.record_received(_receipt_with_key("idempotency-b"))
        store.update_command_receipt(
            transition_command_receipt(second, CommandReceiptState.VALIDATED)
        )

        first_report = store.reconcile_non_terminal_after_startup(
            CommandReceiptStartupReconciliationRequest(
                reconciler_instance_id="host-a",
                limit=1,
            )
        )
        first_loaded = store.load_command_receipt(first.idempotency_key)
        second_loaded = store.load_command_receipt(second.idempotency_key)

        assert first_loaded is not None
        assert second_loaded is not None
        assert first_report.reconciler_instance_id == "host-a"
        assert first_report.scanned == 1
        assert len(first_report.rejected_idempotency_keys) == 1
        assert first_report.pending_effect_reconciliation_keys == ()
        assert [first_loaded.state, second_loaded.state].count(CommandReceiptState.REJECTED) == 1
        assert {first_loaded.state, second_loaded.state} <= {
            CommandReceiptState.RECEIVED,
            CommandReceiptState.VALIDATED,
            CommandReceiptState.REJECTED,
        }

        second_report = store.reconcile_non_terminal_after_startup(
            CommandReceiptStartupReconciliationRequest(
                reconciler_instance_id="host-a",
                limit=10,
            )
        )

        assert second_report.scanned == 1
        assert set(second_report.rejected_idempotency_keys) == (
            {first.idempotency_key, second.idempotency_key}
            - set(first_report.rejected_idempotency_keys)
        )
        assert _load_receipt(store, first.idempotency_key).rejection_reason == (
            COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION
        )
        assert _load_receipt(store, second.idempotency_key).rejection_reason == (
            COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION
        )


def test_sqlite_command_receipts_startup_reconciliation_reports_prepared_effects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        prepared = _stored_receipt_in_state(
            store,
            "idempotency-prepared",
            CommandReceiptState.EFFECT_PREPARED,
        )
        accepted = _stored_receipt_in_state(
            store,
            "idempotency-accepted",
            CommandReceiptState.ACCEPTED,
        )
        running = _stored_receipt_in_state(
            store,
            "idempotency-running",
            CommandReceiptState.RUNNING,
        )
        succeeded = _succeeded_receipt(store.record_received(_receipt_with_key("idempotency-done")))
        store.update_command_receipt(succeeded)

        report = store.reconcile_non_terminal_after_startup(
            CommandReceiptStartupReconciliationRequest(
                reconciler_instance_id="host-a",
                limit=10,
            )
        )

        assert report.scanned == 3
        assert report.rejected_idempotency_keys == ()
        assert set(report.pending_effect_reconciliation_keys) == {
            prepared.idempotency_key,
            accepted.idempotency_key,
            running.idempotency_key,
        }
        assert _load_receipt(store, prepared.idempotency_key).state is (
            CommandReceiptState.EFFECT_PREPARED
        )
        assert _load_receipt(store, accepted.idempotency_key).state is (
            CommandReceiptState.ACCEPTED
        )
        assert _load_receipt(store, running.idempotency_key).state is (
            CommandReceiptState.RUNNING
        )
        assert _load_receipt(store, succeeded.idempotency_key).state is (
            CommandReceiptState.SUCCEEDED
        )


def test_sqlite_command_receipts_update_requires_existing_row(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)

        with pytest.raises(SqliteCommandReceiptStoreError, match="COMMAND_RECEIPT_NOT_FOUND"):
            store.update_command_receipt(_receipt())


def test_sqlite_command_receipts_persist_from_ipc_command(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteCommandReceiptStore(connection)
        service = EngineHostIpcService(
            ClientAuthorizationPolicy(
                expected_user_sid_hash="same-user",
                expected_session_id=42,
            ),
            command_receipt_store=store,
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-ipc-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        ipc_client.connect()

        response = ipc_client.submit_command(
            JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload={"draft_id": "draft-a"},
            payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
        )

        loaded = store.load_command_receipt("66666666-6666-4666-8666-666666666666")
        assert response.status is IpcStatus.REJECTED
        assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
        assert response.payload["receipt"]["state"] == CommandReceiptState.REJECTED.value
        assert loaded is not None
        assert loaded.state is CommandReceiptState.REJECTED
        assert loaded.principal_fingerprint == "same-user"


def test_sqlite_enabled_ipc_command_persists_job_and_success_receipt(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        receipts = SqliteCommandReceiptStore(connection)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        outbox = SqliteOutboxStore(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        drafts.save_standard_backup_draft(_complete_draft())
        service = EngineHostIpcService(
            ClientAuthorizationPolicy(
                expected_user_sid_hash="same-user",
                expected_session_id=42,
            ),
            status=replace(
                startup_status(ProcessRole.ENGINE_HOST),
                mutations_enabled=True,
                scope="0B_LOCAL_MUTATION_PREVIEW",
            ),
            job_draft_store=drafts,
            standard_backup_job_catalog=catalog,
            standard_backup_job_id_factory=id_factory,
            command_receipt_store=receipts,
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
            outbox_store=outbox,
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-ipc-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        ipc_client.connect()

        response = ipc_client.submit_command(
            JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload={"draft_id": "draft-a"},
            payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
        )

        loaded_receipt = receipts.load_command_receipt("66666666-6666-4666-8666-666666666666")
        loaded_job = catalog.load_standard_backup_job("job-a")
        loaded_outbox = outbox.load_outbox_message(
            "command-effect:66666666-6666-4666-8666-666666666666"
        )
        assert response.status is IpcStatus.ACCEPTED
        assert response.reason is None
        assert response.payload["receipt"]["state"] == CommandReceiptState.SUCCEEDED.value
        assert response.payload["job"]["job_id"] == "job-a"
        assert loaded_receipt is not None
        assert loaded_receipt.state is CommandReceiptState.SUCCEEDED
        assert loaded_receipt.result_entity_type == "standard_backup_job"
        assert loaded_receipt.result_entity_id == "job-a"
        assert loaded_job is not None
        assert loaded_job.idempotency_key == "66666666-6666-4666-8666-666666666666"
        assert loaded_outbox is not None
        assert loaded_outbox.aggregate_type == "standard_backup_job"
        assert loaded_outbox.aggregate_id == "job-a"
        assert _row_count(connection, "standard_backup_job_revision_details") == 1
        assert _row_count(connection, "command_receipts") == 1
        assert _row_count(connection, "outbox_messages") == 1
        assert id_factory.calls == 1


def test_sqlite_enabled_ipc_command_replays_from_compacted_receipt_tombstone(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        receipts = SqliteCommandReceiptStore(connection)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        outbox = SqliteOutboxStore(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        drafts.save_standard_backup_draft(_complete_draft())
        service = EngineHostIpcService(
            ClientAuthorizationPolicy(
                expected_user_sid_hash="same-user",
                expected_session_id=42,
            ),
            status=replace(
                startup_status(ProcessRole.ENGINE_HOST),
                mutations_enabled=True,
                scope="0B_LOCAL_MUTATION_PREVIEW",
            ),
            job_draft_store=drafts,
            standard_backup_job_catalog=catalog,
            standard_backup_job_id_factory=id_factory,
            command_receipt_store=receipts,
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
            outbox_store=outbox,
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-ipc-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        ipc_client.connect()
        first = ipc_client.submit_command(
            JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload={"draft_id": "draft-a"},
            payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
        )
        receipts.compact_terminal_command_receipt("66666666-6666-4666-8666-666666666666")

        second = ipc_client.submit_command(
            JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            request_id="77777777-7777-4777-8777-777777777777",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload={"draft_id": "draft-a"},
            payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
        )

        assert first.status is IpcStatus.ACCEPTED
        assert second.status is IpcStatus.ACCEPTED
        assert second.payload["created"] is False
        assert second.payload["idempotent_replay"] is True
        assert second.payload["receipt"]["request_id"] == "44444444-4444-4444-8444-444444444444"
        assert second.payload["receipt"]["state"] == CommandReceiptState.SUCCEEDED.value
        assert second.payload["job"]["job_id"] == "job-a"
        assert _row_count(connection, "standard_backup_job_revision_details") == 1
        assert _row_count(connection, "command_receipts") == 0
        assert _row_count(connection, "command_dedup_tombstones") == 1
        assert _row_count(connection, "outbox_messages") == 1
        assert id_factory.calls == 1


def test_sqlite_enabled_ipc_command_rolls_back_effect_when_outbox_enqueue_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        receipts = SqliteCommandReceiptStore(connection)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        outbox = _FailingOutboxStore(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        drafts.save_standard_backup_draft(_complete_draft())
        service = EngineHostIpcService(
            ClientAuthorizationPolicy(
                expected_user_sid_hash="same-user",
                expected_session_id=42,
            ),
            status=replace(
                startup_status(ProcessRole.ENGINE_HOST),
                mutations_enabled=True,
                scope="0B_LOCAL_MUTATION_PREVIEW",
            ),
            job_draft_store=drafts,
            standard_backup_job_catalog=catalog,
            standard_backup_job_id_factory=id_factory,
            command_receipt_store=receipts,
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
            outbox_store=outbox,
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-ipc-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        ipc_client.connect()

        with pytest.raises(SqliteOutboxStoreError, match="OUTBOX_INJECTED_FAILURE"):
            ipc_client.submit_command(
                JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
                request_id="44444444-4444-4444-8444-444444444444",
                idempotency_key="66666666-6666-4666-8666-666666666666",
                payload={"draft_id": "draft-a"},
                payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
            )

        assert receipts.load_command_receipt("66666666-6666-4666-8666-666666666666") is None
        assert catalog.load_standard_backup_job("job-a") is None
        assert _row_count(connection, "standard_backup_job_revision_details") == 0
        assert _row_count(connection, "command_receipts") == 0
        assert _row_count(connection, "outbox_messages") == 0
        assert id_factory.calls == 1


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


class _FailingOutboxStore(SqliteOutboxStore):
    def enqueue_outbox_message(self, message: OutboxMessage) -> OutboxMessage:
        super().enqueue_outbox_message(message)
        raise SqliteOutboxStoreError("OUTBOX_INJECTED_FAILURE")


def _receipt() -> CommandReceipt:
    return CommandReceipt(
        request_id="request-a",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key="idempotency-a",
        command_name="create_standard_backup_job",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
        expected_entity_revision=7,
    )


def _receipt_with_key(idempotency_key: str) -> CommandReceipt:
    return replace(
        _receipt(),
        request_id=f"request-{idempotency_key}",
        idempotency_key=idempotency_key,
    )


def _stored_receipt_in_state(
    store: SqliteCommandReceiptStore,
    idempotency_key: str,
    state: CommandReceiptState,
) -> CommandReceipt:
    receipt = store.record_received(_receipt_with_key(idempotency_key))
    if state is CommandReceiptState.RECEIVED:
        return receipt

    validated = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
    if state is CommandReceiptState.VALIDATED:
        store.update_command_receipt(validated)
        return validated

    prepared = transition_command_receipt(
        validated,
        CommandReceiptState.EFFECT_PREPARED,
        result_entity_type="standard_backup_job",
        result_entity_id=f"job-{idempotency_key}",
    )
    if state is CommandReceiptState.EFFECT_PREPARED:
        store.update_command_receipt(prepared)
        return prepared

    accepted = transition_command_receipt(prepared, CommandReceiptState.ACCEPTED)
    if state is CommandReceiptState.ACCEPTED:
        store.update_command_receipt(accepted)
        return accepted

    running = transition_command_receipt(accepted, CommandReceiptState.RUNNING)
    if state is CommandReceiptState.RUNNING:
        store.update_command_receipt(running)
        return running

    raise AssertionError(f"unsupported test receipt state: {state.value}")


def _succeeded_receipt(receipt: CommandReceipt) -> CommandReceipt:
    validated = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
    prepared = transition_command_receipt(validated, CommandReceiptState.EFFECT_PREPARED)
    accepted = transition_command_receipt(prepared, CommandReceiptState.ACCEPTED)
    return transition_command_receipt(
        accepted,
        CommandReceiptState.SUCCEEDED,
        result_entity_type="standard_backup_job",
        result_entity_id="job-a",
    )


def _complete_draft() -> StandardBackupJobDraft:
    return (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _load_receipt(store: SqliteCommandReceiptStore, idempotency_key: str) -> CommandReceipt:
    receipt = store.load_command_receipt(idempotency_key)
    assert receipt is not None
    return receipt
