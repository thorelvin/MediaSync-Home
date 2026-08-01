from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.endpoint_classifications import (
    SqliteEndpointClassificationRefresher,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.transactions import SqliteImmediateTransactionRunner
from mediasync_home.adapters.sqlite.writable_endpoint_registrations import (
    SqliteWritableEndpointRegistrationStore,
)
from mediasync_home.adapters.writable_endpoint_registration import (
    LocalWritableEndpointControlAreaProvisioner,
)
from mediasync_home.application.writable_endpoint_registration import (
    PreparedWritableEndpoint,
    WritableEndpointRegistrationCoordinator,
    WritableEndpointRegistrationIds,
    WritableEndpointRegistrationIntent,
    WritableEndpointRegistrationState,
    WritableEndpointRegistrationCandidate,
    WritableEndpointTargetIds,
    WritableEndpointRegistrationCommandName,
)
from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.command_receipts import CommandReceiptState
from mediasync_home.application.endpoint_capabilities import (
    EndpointCapabilityEvidence,
    EndpointCapabilityProbeScope,
)
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService


INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
JOB_REVISION_ID = "33333333-3333-4333-8333-333333333333"
TARGET_ENDPOINT_ID = "44444444-4444-4444-8444-444444444444"
TARGET_REVISION_ID = "55555555-5555-4555-8555-555555555555"
NEW_TARGET_REVISION_ID = "66666666-6666-4666-8666-666666666666"
CONTROL_AREA_ID = "77777777-7777-4777-8777-777777777777"
INTENT_ID = "88888888-8888-4888-8888-888888888888"
NEW_JOB_REVISION_ID = "99999999-9999-4999-8999-999999999999"
SECOND_TARGET_ENDPOINT_ID = "44444444-2222-4222-8222-444444444444"
SECOND_TARGET_REVISION_ID = "55555555-2222-4222-8222-555555555555"
SECOND_NEW_TARGET_REVISION_ID = "66666666-2222-4222-8222-666666666666"
SECOND_CONTROL_AREA_ID = "77777777-2222-4222-8222-777777777777"
SECOND_INTENT_ID = "88888888-2222-4222-8222-888888888888"
SECOND_NEW_JOB_REVISION_ID = "99999999-2222-4222-8222-999999999999"


def test_registration_coordinator_appends_revisions_and_stays_writable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        refresher = _refresher(connection)
        refresher.refresh_endpoint_classifications(observed_utc="2026-07-31T11:00:00Z")
        store = SqliteWritableEndpointRegistrationStore(connection)
        coordinator = WritableEndpointRegistrationCoordinator(
            store=store,
            provisioner=LocalWritableEndpointControlAreaProvisioner(),
            id_factory=_FixedIds(),
            owner_installation_id=INSTALLATION_ID,
        )

        report = coordinator.register_job_targets(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            command_request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            command_idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            observed_utc="2026-07-31T11:01:00Z",
        )
        replay = coordinator.register_job_targets(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            command_request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            command_idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            observed_utc="2026-07-31T11:02:00Z",
        )
        not_required = coordinator.register_job_targets(
            job_id=JOB_ID,
            job_revision_id=NEW_JOB_REVISION_ID,
            command_request_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            command_idempotency_key="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            observed_utc="2026-07-31T11:02:30Z",
        )

        assert report.completed is True
        assert report.active_job_revision_id == NEW_JOB_REVISION_ID
        assert report.registered_target_count == 1
        assert replay.completed is True
        assert replay.idempotent_replay is True
        assert not_required.completed is True
        assert not_required.validation_codes == (
            "WRITABLE_ENDPOINT_REGISTRATION_NOT_REQUIRED",
        )
        assert not_required.next_action == "No target registration is required."
        assert connection.execute(
            "SELECT active_revision_id FROM job_heads WHERE job_id = ?",
            (JOB_ID,),
        ).fetchone() == (NEW_JOB_REVISION_ID,)
        assert connection.execute(
            "SELECT active_revision_id FROM endpoint_heads WHERE endpoint_id = ?",
            (TARGET_ENDPOINT_ID,),
        ).fetchone() == (NEW_TARGET_REVISION_ID,)
        assert connection.execute(
            """
            SELECT
                generation,
                control_area_id,
                owner_installation_id,
                ownership_epoch,
                root_identity_hash_algorithm,
                control_marker_checksum_algorithm
            FROM endpoint_revisions
            WHERE endpoint_id = ? AND id = ?
            """,
            (TARGET_ENDPOINT_ID, NEW_TARGET_REVISION_ID),
        ).fetchone() == (
            2,
            CONTROL_AREA_ID,
            INSTALLATION_ID,
            1,
            "BLAKE3-256",
            "BLAKE3-256",
        )
        assert connection.execute(
            """
            SELECT registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ?
                AND job_revision_id = ?
                AND role = 'TARGET'
            """,
            (JOB_ID, NEW_JOB_REVISION_ID),
        ).fetchone() == (
            "WRITABLE_READY",
            "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED",
        )


def test_registration_preserves_ready_binding_while_registering_new_target(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    second_target = tmp_path / "target-two"
    source.mkdir()
    target.mkdir()
    second_target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        refresher = _refresher(connection)
        refresher.refresh_endpoint_classifications(
            observed_utc="2026-07-31T11:00:00Z"
        )
        first = WritableEndpointRegistrationCoordinator(
            store=SqliteWritableEndpointRegistrationStore(connection),
            provisioner=LocalWritableEndpointControlAreaProvisioner(),
            id_factory=_FixedIds(),
            owner_installation_id=INSTALLATION_ID,
        ).register_job_targets(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            command_request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            command_idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            observed_utc="2026-07-31T11:01:00Z",
        )
        assert first.completed is True
        _insert_endpoint(
            connection,
            endpoint_id=SECOND_TARGET_ENDPOINT_ID,
            endpoint_revision_id=SECOND_TARGET_REVISION_ID,
            root=second_target,
            role="TARGET",
            ordinal=2,
            job_revision_id=NEW_JOB_REVISION_ID,
        )
        connection.commit()
        refresher.refresh_endpoint_classifications(
            observed_utc="2026-07-31T11:02:00Z"
        )
        store = SqliteWritableEndpointRegistrationStore(connection)

        candidates = store.load_registration_candidates(
            job_id=JOB_ID,
            job_revision_id=NEW_JOB_REVISION_ID,
        )

        assert tuple(candidate.target_ordinal for candidate in candidates) == (2,)
        report = WritableEndpointRegistrationCoordinator(
            store=store,
            provisioner=LocalWritableEndpointControlAreaProvisioner(),
            id_factory=_SecondFixedIds(),
            owner_installation_id=INSTALLATION_ID,
        ).register_job_targets(
            job_id=JOB_ID,
            job_revision_id=NEW_JOB_REVISION_ID,
            command_request_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            command_idempotency_key="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            observed_utc="2026-07-31T11:03:00Z",
        )

        assert report.completed is True
        assert report.registered_target_count == 1
        assert report.active_job_revision_id == SECOND_NEW_JOB_REVISION_ID
        assert connection.execute(
            """
            SELECT ordinal, registration_state
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ?
                AND job_revision_id = ?
                AND role = 'TARGET'
            ORDER BY ordinal
            """,
            (JOB_ID, SECOND_NEW_JOB_REVISION_ID),
        ).fetchall() == [(1, "WRITABLE_READY"), (2, "WRITABLE_READY")]
        assert connection.execute(
            """
            SELECT endpoint_generation, intent_id, probe_completed_utc
            FROM writable_endpoint_registrations
            """
        ).fetchone() == (2, INTENT_ID, "2026-07-31T11:01:00Z")
        capability_row = connection.execute(
            """
            SELECT write_capabilities_json, write_capabilities_hash
            FROM writable_endpoint_registrations
            """
        ).fetchone()
        assert capability_row is not None
        EndpointCapabilityEvidence(
            profile_json=str(capability_row[0]),
            capabilities_hash=str(capability_row[1]),
        ).validated_profile(
            expected_scope=EndpointCapabilityProbeScope.CONTROLLED_WRITABLE
        )

        refresher.refresh_endpoint_classifications(observed_utc="2026-07-31T11:03:00Z")
        assert connection.execute(
            """
            SELECT registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ?
                AND job_revision_id = ?
                AND role = 'TARGET'
            """,
            (JOB_ID, NEW_JOB_REVISION_ID),
        ).fetchone() == (
            "WRITABLE_READY",
            "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED",
        )


def test_startup_reconciliation_finishes_exact_published_intent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        _refresher(connection).refresh_endpoint_classifications(
            observed_utc="2026-07-31T12:00:00Z"
        )
        store = SqliteWritableEndpointRegistrationStore(connection)
        provisioner = LocalWritableEndpointControlAreaProvisioner()
        candidate = store.load_registration_candidates(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
        )[0]
        prepared = provisioner.prepare_new_control_area(
            candidate,
            intent_id=INTENT_ID,
            target_ids=WritableEndpointTargetIds(
                target_ordinal=1,
                endpoint_revision_id=NEW_TARGET_REVISION_ID,
                control_area_id=CONTROL_AREA_ID,
            ),
            owner_installation_id=INSTALLATION_ID,
            created_utc="2026-07-31T12:01:00Z",
        )
        store.save_prepared_registration_intent(
            _intent(prepared, created_utc="2026-07-31T12:01:00Z")
        )
        provisioner.apply_prepared_control_area(prepared, intent_id=INTENT_ID)

        coordinator = WritableEndpointRegistrationCoordinator(
            store=store,
            provisioner=LocalWritableEndpointControlAreaProvisioner(),
            id_factory=_FixedIds(),
            owner_installation_id=INSTALLATION_ID,
        )
        reports = coordinator.reconcile_pending(
            observed_utc="2026-07-31T12:02:00Z",
        )

        assert len(reports) == 1
        assert reports[0].state is WritableEndpointRegistrationState.COMMITTED
        assert reports[0].idempotent_replay is True
        assert connection.execute(
            """
            SELECT state, committed_utc
            FROM writable_endpoint_registration_intents
            WHERE intent_id = ?
            """,
            (INTENT_ID,),
        ).fetchone() == ("COMMITTED", "2026-07-31T12:02:00Z")


def test_explicit_ipc_command_repairs_pending_job_without_registration_intent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        refresher = _refresher(connection)
        initial_refresh = refresher.refresh_endpoint_classifications(
            observed_utc="2026-08-01T07:00:00Z"
        )
        receipts = SqliteCommandReceiptStore(connection)
        coordinator = WritableEndpointRegistrationCoordinator(
            store=SqliteWritableEndpointRegistrationStore(connection),
            provisioner=LocalWritableEndpointControlAreaProvisioner(),
            id_factory=_FixedIds(),
            owner_installation_id=INSTALLATION_ID,
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
            writable_endpoint_registration=coordinator,
            writable_endpoint_registration_utc_now=(
                lambda: "2026-08-01T07:01:00Z"
            ),
            endpoint_classification_refresh=(
                lambda: refresher.refresh_endpoint_classifications(
                    observed_utc="2026-08-01T07:02:00Z"
                )
            ),
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-registration-repair-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        ipc_client.connect()
        payload = {
            "job_id": JOB_ID,
            "job_revision_id": JOB_REVISION_ID,
        }

        response = ipc_client.submit_command(
            WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
            request_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            idempotency_key="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            payload=payload,
            payload_hash=canonical_command_payload_hash(payload),
        )
        replay = ipc_client.submit_command(
            WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
            request_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            idempotency_key="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            payload=payload,
            payload_hash=canonical_command_payload_hash(payload),
        )

        receipt = receipts.load_command_receipt(
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        )
        assert initial_refresh.pending_binding_count == 1
        assert response.status is IpcStatus.ACCEPTED
        assert response.payload["writable_endpoint_registration"]["completed"] is True
        assert response.payload["job"]["job_revision_id"] == NEW_JOB_REVISION_ID
        assert response.payload["endpoint_classification_refresh"]["completed"] is True
        assert replay.status is IpcStatus.ACCEPTED
        assert replay.payload["idempotent_replay"] is True
        assert receipt is not None
        assert receipt.state is CommandReceiptState.SUCCEEDED
        assert receipt.result_entity_id == INTENT_ID
        assert (target / ".mediasync" / "endpoint.json").is_file()
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
        assert connection.execute(
            "SELECT count(*) FROM writable_endpoint_registration_intents"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM writable_endpoint_registrations"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM endpoint_revisions WHERE endpoint_id = ?",
            (TARGET_ENDPOINT_ID,),
        ).fetchone() == (2,)


def test_registration_tables_reject_identity_rewrite(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database, source=source, target=target)
        _refresher(connection).refresh_endpoint_classifications(
            observed_utc="2026-07-31T13:00:00Z"
        )
        coordinator = WritableEndpointRegistrationCoordinator(
            store=SqliteWritableEndpointRegistrationStore(connection),
            provisioner=LocalWritableEndpointControlAreaProvisioner(),
            id_factory=_FixedIds(),
            owner_installation_id=INSTALLATION_ID,
        )
        coordinator.register_job_targets(
            job_id=JOB_ID,
            job_revision_id=JOB_REVISION_ID,
            command_request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            command_idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            observed_utc="2026-07-31T13:01:00Z",
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="WRITABLE_ENDPOINT_REGISTRATION_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE writable_endpoint_registrations
                SET ownership_epoch = 2
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="WRITABLE_ENDPOINT_REGISTRATION_INTENT_IDENTITY_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE writable_endpoint_registration_intents
                SET source_job_revision_id = 'changed'
                """
            )


class _FixedIds:
    def new_registration_ids(
        self,
        candidates: tuple[WritableEndpointRegistrationCandidate, ...],
    ) -> WritableEndpointRegistrationIds:
        assert len(candidates) == 1
        return WritableEndpointRegistrationIds(
            intent_id=INTENT_ID,
            resulting_job_revision_id=NEW_JOB_REVISION_ID,
            targets=(
                WritableEndpointTargetIds(
                    target_ordinal=1,
                    endpoint_revision_id=NEW_TARGET_REVISION_ID,
                    control_area_id=CONTROL_AREA_ID,
                ),
            ),
        )


class _SecondFixedIds:
    def new_registration_ids(
        self,
        candidates: tuple[WritableEndpointRegistrationCandidate, ...],
    ) -> WritableEndpointRegistrationIds:
        assert tuple(candidate.target_ordinal for candidate in candidates) == (2,)
        return WritableEndpointRegistrationIds(
            intent_id=SECOND_INTENT_ID,
            resulting_job_revision_id=SECOND_NEW_JOB_REVISION_ID,
            targets=(
                WritableEndpointTargetIds(
                    target_ordinal=2,
                    endpoint_revision_id=SECOND_NEW_TARGET_REVISION_ID,
                    control_area_id=SECOND_CONTROL_AREA_ID,
                ),
            ),
        )


def _intent(
    prepared: PreparedWritableEndpoint,
    *,
    created_utc: str,
) -> WritableEndpointRegistrationIntent:
    return WritableEndpointRegistrationIntent(
        intent_id=INTENT_ID,
        job_id=JOB_ID,
        source_job_revision_id=JOB_REVISION_ID,
        resulting_job_revision_id=NEW_JOB_REVISION_ID,
        command_request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        command_idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        state=WritableEndpointRegistrationState.PREPARED,
        prepared_targets=(prepared,),
        created_utc=created_utc,
        updated_utc=created_utc,
    )


def _refresher(connection: sqlite3.Connection) -> SqliteEndpointClassificationRefresher:
    return SqliteEndpointClassificationRefresher(
        connection,
        classifier=LocalEndpointControlAreaClassifier(),
        local_installation_id=INSTALLATION_ID,
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
        "INSERT INTO jobs (id, kind) VALUES (?, 'multi_target_backup')",
        (JOB_ID,),
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES (?, 'filter-a')",
        (JOB_ID,),
    )
    insert_default_filter_set_version(
        connection,
        job_id=JOB_ID,
        filter_set_id="filter-a",
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
        [
            {
                "independent_device_id": None,
                "name": "Target",
                "path_label": str(target),
            }
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_drafts (
            draft_id,
            schema_version,
            source_name,
            source_path_label,
            defaults_json,
            targets_json
        )
        VALUES ('draft-a', 1, 'Source', ?, ?, ?)
        """,
        (str(source), defaults_json, targets_json),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_revision_details (
            job_id,
            job_revision_id,
            draft_id,
            command_request_id,
            idempotency_key,
            source_name,
            source_path_label,
            defaults_json,
            targets_json
        )
        VALUES (?, ?, 'draft-a', 'request-a', 'create-a', 'Source', ?, ?, ?)
        """,
        (
            JOB_ID,
            JOB_REVISION_ID,
            str(source),
            defaults_json,
            targets_json,
        ),
    )
    connection.execute(
        "INSERT INTO job_heads (job_id, active_revision_id) VALUES (?, ?)",
        (JOB_ID, JOB_REVISION_ID),
    )
    _insert_endpoint(
        connection,
        endpoint_id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
        endpoint_revision_id="bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb",
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
    job_revision_id: str = JOB_REVISION_ID,
) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES (?)", (endpoint_id,))
    connection.execute(
        """
        INSERT INTO endpoint_revisions (
            endpoint_id,
            id,
            display_name,
            root_uri,
            generation
        )
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
            job_id,
            job_revision_id,
            role,
            ordinal,
            endpoint_id,
            endpoint_revision_id,
            registration_state,
            registration_reason_code
        )
        VALUES (?, ?, ?, ?, ?, ?, 'REGISTRATION_PENDING', 'ENDPOINT_CLASSIFICATION_PENDING')
        """,
        (
            JOB_ID,
            job_revision_id,
            role,
            ordinal,
            endpoint_id,
            endpoint_revision_id,
        ),
    )
