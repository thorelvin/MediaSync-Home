from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.endpoint_takeover import LocalEndpointTakeoverFilesystem
from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.endpoint_classifications import (
    SqliteEndpointClassificationRefresher,
)
from mediasync_home.adapters.sqlite.endpoint_takeovers import (
    SqliteEndpointTakeoverStore,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.transactions import SqliteImmediateTransactionRunner
from mediasync_home.adapters.writable_endpoint_registration import (
    LocalWritableEndpointControlAreaProvisioner,
)
from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.command_receipts import CommandReceiptState
from mediasync_home.application.endpoint_takeover import (
    EndpointTakeoverCommandName,
    EndpointTakeoverCoordinator,
    EndpointTakeoverIds,
    EndpointTakeoverIntent,
    EndpointTakeoverState,
    PreparedEndpointTakeover,
    StartControlledEndpointTakeoverCommand,
)
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCandidate,
    WritableEndpointTargetIds,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService


LOCAL_OWNER = "11111111-1111-4111-8111-111111111111"
FOREIGN_OWNER = "22222222-2222-4222-8222-222222222222"
JOB_ID = "33333333-3333-4333-8333-333333333333"
JOB_REVISION_ID = "44444444-4444-4444-8444-444444444444"
TARGET_ENDPOINT_ID = "55555555-5555-4555-8555-555555555555"
TARGET_REVISION_ID = "66666666-6666-4666-8666-666666666666"
CONTROL_AREA_ID = "77777777-7777-4777-8777-777777777777"
INTENT_ID = "88888888-8888-4888-8888-888888888888"
NEW_ENDPOINT_REVISION_ID = "99999999-9999-4999-8999-999999999999"
NEW_JOB_REVISION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ANALYSIS_REQUEST_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_ipc_controlled_takeover_commits_revisions_and_queues_full_analysis(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        _create_foreign_marker(target)
        refresher = _refresher(connection)
        refresher.refresh_endpoint_classifications(observed_utc="2026-08-01T11:00:00Z")
        receipts = SqliteCommandReceiptStore(connection)
        coordinator = EndpointTakeoverCoordinator(
            store=SqliteEndpointTakeoverStore(connection),
            filesystem=LocalEndpointTakeoverFilesystem(),
            id_factory=_FixedTakeoverIds(),
            owner_installation_id=LOCAL_OWNER,
        )
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
            command_receipt_store=receipts,
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
            endpoint_takeover=coordinator,
            endpoint_takeover_utc_now=lambda: "2026-08-01T11:01:00Z",
            endpoint_classification_refresh=lambda: refresher.refresh_endpoint_classifications(
                observed_utc="2026-08-01T11:02:00Z"
            ),
        )
        client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="endpoint-takeover-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        )
        client.connect()
        payload: dict[str, object] = {
            "job_id": JOB_ID,
            "job_revision_id": JOB_REVISION_ID,
            "target_ordinal": 1,
            "endpoint_id": TARGET_ENDPOINT_ID,
            "expected_foreign_owner_installation_id": FOREIGN_OWNER,
            "expected_ownership_epoch": 1,
            "explicit_confirmation": True,
        }

        response = client.submit_command(
            EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value,
            request_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            idempotency_key="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            payload=payload,
            payload_hash=canonical_command_payload_hash(payload),
        )
        replay = client.submit_command(
            EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value,
            request_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            idempotency_key="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            payload=payload,
            payload_hash=canonical_command_payload_hash(payload),
        )
        conflicting_payload = dict(payload)
        conflicting_payload["expected_ownership_epoch"] = 2
        conflict = client.submit_command(
            EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value,
            request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            payload=conflicting_payload,
            payload_hash=canonical_command_payload_hash(conflicting_payload),
        )

        receipt = receipts.load_command_receipt("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
        assert response.status is IpcStatus.ACCEPTED
        assert response.payload["endpoint_takeover"]["completed"] is True
        assert response.payload["endpoint_takeover"]["full_analysis_queued"] is True
        assert response.payload["job"]["job_revision_id"] == NEW_JOB_REVISION_ID
        assert replay.status is IpcStatus.ACCEPTED
        assert replay.payload["endpoint_takeover"]["idempotent_replay"] is True
        assert conflict.status is IpcStatus.REJECTED
        assert conflict.reason is IpcReason.COMMAND_PRECONDITION_FAILED
        assert conflict.payload["endpoint_takeover"]["validation_codes"] == [
            "ENDPOINT_TAKEOVER_INTENT_CONFLICT"
        ]
        assert receipt is not None
        assert receipt.state is CommandReceiptState.SUCCEEDED
        assert receipt.result_entity_type == "controlled_endpoint_takeover"
        assert connection.execute(
            "SELECT active_revision_id FROM endpoint_heads WHERE endpoint_id = ?",
            (TARGET_ENDPOINT_ID,),
        ).fetchone() == (NEW_ENDPOINT_REVISION_ID,)
        assert connection.execute(
            "SELECT active_revision_id FROM job_heads WHERE job_id = ?",
            (JOB_ID,),
        ).fetchone() == (NEW_JOB_REVISION_ID,)
        assert connection.execute(
            """
            SELECT registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ? AND job_revision_id = ? AND role = 'TARGET'
            """,
            (JOB_ID, NEW_JOB_REVISION_ID),
        ).fetchone() == (
            "WRITABLE_READY",
            "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED",
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="CONTROLLED_ENDPOINT_TAKEOVER_IMMUTABLE",
        ):
            connection.execute(
                "UPDATE controlled_endpoint_takeovers SET ownership_epoch = 3"
            )
        connection.rollback()
        with pytest.raises(
            sqlite3.IntegrityError,
            match="CONTROLLED_ENDPOINT_TAKEOVER_INTENT_IDENTITY_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE controlled_endpoint_takeover_intents
                SET source_job_revision_id = 'changed'
                """
            )
        connection.rollback()
        assert connection.execute(
            """
            SELECT state, job_revision_id, start_when_safe
            FROM backup_analysis_requests
            WHERE request_id = ?
            """,
            (ANALYSIS_REQUEST_ID,),
        ).fetchone() == ("QUEUED", NEW_JOB_REVISION_ID, 0)
        assert connection.execute(
            """
            SELECT previous_owner_installation_id, previous_ownership_epoch,
                   owner_installation_id, ownership_epoch, endpoint_generation
            FROM controlled_endpoint_takeovers
            """
        ).fetchone() == (FOREIGN_OWNER, 1, LOCAL_OWNER, 2, 2)

        refresher.refresh_endpoint_classifications(observed_utc="2026-08-01T11:03:00Z")
        assert connection.execute(
            """
            SELECT registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ? AND job_revision_id = ? AND role = 'TARGET'
            """,
            (JOB_ID, NEW_JOB_REVISION_ID),
        ).fetchone() == (
            "WRITABLE_READY",
            "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED",
        )


def test_startup_reconciliation_finishes_published_takeover(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        _create_foreign_marker(target)
        _refresher(connection).refresh_endpoint_classifications(
            observed_utc="2026-08-01T12:00:00Z"
        )
        store = SqliteEndpointTakeoverStore(connection)
        filesystem = LocalEndpointTakeoverFilesystem()
        candidate = store.load_takeover_candidate(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            target_ordinal=1,
            endpoint_id=TARGET_ENDPOINT_ID,
            expected_foreign_owner_installation_id=FOREIGN_OWNER,
            expected_ownership_epoch=1,
        )
        prepared = filesystem.prepare_controlled_takeover(
            candidate,
            intent_id=INTENT_ID,
            resulting_endpoint_revision_id=NEW_ENDPOINT_REVISION_ID,
            owner_installation_id=LOCAL_OWNER,
            created_utc="2026-08-01T12:01:00Z",
        )
        command = StartControlledEndpointTakeoverCommand(
            request_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            idempotency_key="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            target_ordinal=1,
            endpoint_id=TARGET_ENDPOINT_ID,
            expected_foreign_owner_installation_id=FOREIGN_OWNER,
            expected_ownership_epoch=1,
        )
        coordinator = EndpointTakeoverCoordinator(
            store=store,
            filesystem=filesystem,
            id_factory=_FixedTakeoverIds(),
            owner_installation_id=LOCAL_OWNER,
        )
        intent = store.save_prepared_takeover_intent(
            _prepared_intent(command, prepared)
        )
        filesystem.apply_prepared_takeover(prepared, intent_id=INTENT_ID)

        reports = coordinator.reconcile_pending(observed_utc="2026-08-01T12:02:00Z")

        assert intent.state is EndpointTakeoverState.PREPARED
        assert len(reports) == 1
        assert reports[0].completed is True
        assert reports[0].idempotent_replay is True
        assert connection.execute(
            "SELECT state FROM controlled_endpoint_takeover_intents WHERE intent_id = ?",
            (INTENT_ID,),
        ).fetchone() == ("COMMITTED",)


class _FixedTakeoverIds:
    def new_takeover_ids(self) -> EndpointTakeoverIds:
        return EndpointTakeoverIds(
            intent_id=INTENT_ID,
            resulting_endpoint_revision_id=NEW_ENDPOINT_REVISION_ID,
            resulting_job_revision_id=NEW_JOB_REVISION_ID,
            analysis_request_id=ANALYSIS_REQUEST_ID,
        )


def _prepared_intent(
    command: StartControlledEndpointTakeoverCommand,
    prepared: PreparedEndpointTakeover,
) -> EndpointTakeoverIntent:
    return EndpointTakeoverIntent(
        intent_id=INTENT_ID,
        job_id=command.job_id,
        source_job_revision_id=command.job_revision_id,
        resulting_job_revision_id=NEW_JOB_REVISION_ID,
        analysis_request_id=ANALYSIS_REQUEST_ID,
        command_request_id=command.request_id,
        command_idempotency_key=command.idempotency_key,
        state=EndpointTakeoverState.PREPARED,
        prepared=prepared,
        created_utc="2026-08-01T12:01:00Z",
        updated_utc="2026-08-01T12:01:00Z",
    )


def _refresher(connection: sqlite3.Connection) -> SqliteEndpointClassificationRefresher:
    return SqliteEndpointClassificationRefresher(
        connection,
        classifier=LocalEndpointControlAreaClassifier(),
        local_installation_id=LOCAL_OWNER,
    )


def _create_foreign_marker(target: Path) -> None:
    provisioner = LocalWritableEndpointControlAreaProvisioner()
    prepared = provisioner.prepare_new_control_area(
        WritableEndpointRegistrationCandidate(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            target_ordinal=1,
            endpoint_id=TARGET_ENDPOINT_ID,
            endpoint_revision_id=TARGET_REVISION_ID,
            endpoint_generation=1,
            display_name="Target",
            root_uri=target.as_uri(),
        ),
        intent_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        target_ids=WritableEndpointTargetIds(
            target_ordinal=1,
            endpoint_revision_id="12121212-1212-4212-8212-121212121212",
            control_area_id=CONTROL_AREA_ID,
        ),
        owner_installation_id=FOREIGN_OWNER,
        created_utc="2026-08-01T10:00:00Z",
    )
    provisioner.apply_prepared_control_area(
        prepared,
        intent_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
    )


def _prepare_catalog(
    connection: sqlite3.Connection,
    database: Path,
    *,
    source: Path,
    target: Path,
) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES (?, 'multi_target_backup')", (JOB_ID,)
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES (?, 'filter-a')", (JOB_ID,)
    )
    insert_default_filter_set_version(
        connection, job_id=JOB_ID, filter_set_id="filter-a"
    )
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id, filter_set_version)
        VALUES (?, ?, 'filter-a', 1)
        """,
        (JOB_ID, JOB_REVISION_ID),
    )
    defaults_json = (
        '{"behavior":"UPDATE_BACKUP","extra_files":"LEAVE_UNCHANGED",'
        '"file_selection":"ALL_USER_FILES","performance":"STANDARD",'
        '"retention":"KEEP_30_DAYS","verification":"STANDARD"}'
    )
    targets_json = json.dumps(
        [{"independent_device_id": None, "name": "Target", "path_label": str(target)}],
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_drafts (
            draft_id, schema_version, source_name, source_path_label,
            defaults_json, targets_json
        )
        VALUES ('draft-a', 1, 'Source', ?, ?, ?)
        """,
        (str(source), defaults_json, targets_json),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_revision_details (
            job_id, job_revision_id, draft_id, command_request_id, idempotency_key,
            source_name, source_path_label, defaults_json, targets_json
        )
        VALUES (?, ?, 'draft-a', 'request-a', 'create-a', 'Source', ?, ?, ?)
        """,
        (JOB_ID, JOB_REVISION_ID, str(source), defaults_json, targets_json),
    )
    connection.execute(
        "INSERT INTO job_heads (job_id, active_revision_id) VALUES (?, ?)",
        (JOB_ID, JOB_REVISION_ID),
    )
    _insert_endpoint(
        connection,
        endpoint_id="13131313-1313-4313-8313-131313131313",
        endpoint_revision_id="14141414-1414-4414-8414-141414141414",
        root=source,
        role="SOURCE",
        ordinal=0,
    )
    _insert_endpoint(
        connection,
        endpoint_id=TARGET_ENDPOINT_ID,
        endpoint_revision_id=TARGET_REVISION_ID,
        root=target,
        role="TARGET",
        ordinal=1,
    )
    connection.commit()


def _insert_endpoint(
    connection: sqlite3.Connection,
    *,
    endpoint_id: str,
    endpoint_revision_id: str,
    root: Path,
    role: str,
    ordinal: int,
) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES (?)", (endpoint_id,))
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri, generation)
        VALUES (?, ?, ?, ?, 1)
        """,
        (endpoint_id, endpoint_revision_id, role.title(), root.as_uri()),
    )
    connection.execute(
        "INSERT INTO endpoint_heads (endpoint_id, active_revision_id) VALUES (?, ?)",
        (endpoint_id, endpoint_revision_id),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_endpoint_bindings (
            job_id, job_revision_id, role, ordinal, endpoint_id, endpoint_revision_id,
            registration_state, registration_reason_code
        )
        VALUES (?, ?, ?, ?, ?, ?, 'REGISTRATION_PENDING', 'ENDPOINT_CLASSIFICATION_PENDING')
        """,
        (JOB_ID, JOB_REVISION_ID, role, ordinal, endpoint_id, endpoint_revision_id),
    )
