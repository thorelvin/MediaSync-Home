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
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptConflict,
    CommandReceiptState,
    transition_command_receipt,
)
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService


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


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


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


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
