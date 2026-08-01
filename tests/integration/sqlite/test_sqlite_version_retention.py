from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.catalog_handoffs import (
    SqliteFinalFileCatalogHandoffStore,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.adapters.sqlite.version_retention import (
    SqliteVersionRetentionStore,
    SqliteVersionRetentionStoreError,
)
from mediasync_home.adapters.version_retention import (
    LocalVersionRetentionDeletionAdapter,
)
from mediasync_home.application.catalog_handoff import (
    FinalFileCatalogHandoff,
    RetainedVersionCatalogHandoff,
)
from mediasync_home.application.retained_version_history import (
    ProtectRetainedVersionForRestoreCommand,
    RetainedVersionCursor,
)
from mediasync_home.application.version_retention import (
    RetainedVersionRecord,
    RetainedVersionState,
    apply_next_version_retention_item,
    create_version_retention_plan,
    maintain_version_retention,
)
from mediasync_home.application.runs import EndpointLeaseAttempt, EndpointLeaseRequest
from mediasync_home.application.recovery_intents import durable_recovery_intent_segment
from mediasync_home.application.recovery_operations import (
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.version_objects import (
    VersionObjectManifest,
    create_quarantine_object_manifest,
    create_version_object_manifest,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_sqlite_version_history_lists_run_versions_with_active_hold(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        _insert_job(connection)
        handoffs = SqliteFinalFileCatalogHandoffStore(connection)
        handoffs.record_final_file_handoff(_handoff())
        second = replace(
            _handoff(),
            handoff_id="final-file:run-a:operation-b",
            operation_id="operation-b",
            final_relative_path="Photos/second.jpg",
            retained_version=replace(
                _handoff().retained_version,
                version_object_id="version-b",
                created_utc="2026-08-02T00:00:00.000Z",
                retention_until_utc="2026-09-01T00:00:00.000Z",
                manifest_hash="e" * 64,
            ),
        )
        handoffs.record_final_file_handoff(second)
        store = SqliteVersionRetentionStore(connection)

        first = store.list_retained_versions_for_run(
            run_id="run-a",
            limit=1,
            after=None,
        )
        second_page = store.list_retained_versions_for_run(
            run_id="run-a",
            limit=2,
            after=RetainedVersionCursor(
                created_utc=first[0].created_utc,
                version_object_id=first[0].version_object_id,
            ),
        )

        assert [version.version_object_id for version in first] == ["version-b"]
        assert [version.version_object_id for version in second_page] == ["version-a"]
        assert first[0].protected_for_restore is False
    finally:
        connection.close()


def test_sqlite_restore_protection_is_idempotent_and_blocks_expiry(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff()
        )
        store = SqliteVersionRetentionStore(connection)
        command = ProtectRetainedVersionForRestoreCommand(
            request_id="request-a",
            idempotency_key="key-a",
            version_object_id="version-a",
            expected_row_version=1,
            explicit_confirmation=True,
        )

        first = store.protect_retained_version_for_restore(
            command=command,
            created_utc="2026-08-10T00:00:00.000Z",
        )
        replay = store.protect_retained_version_for_restore(
            command=command,
            created_utc="2026-08-10T00:00:01.000Z",
        )

        assert first.protected is True
        assert first.idempotent_replay is False
        assert first.version is not None and first.version.protected_for_restore
        assert replay.protected is True and replay.idempotent_replay is True
        assert connection.execute(
            "SELECT count(*) FROM version_retention_holds"
        ).fetchone() == (1,)
        assert store.list_due_retained_versions(
            cutoff_utc="2026-09-01T00:00:00.000Z",
            limit=10,
        ) == ()
    finally:
        connection.close()


def test_sqlite_restore_protection_rejects_stale_or_expiring_version(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff()
        )
        store = SqliteVersionRetentionStore(connection)
        stale = store.protect_retained_version_for_restore(
            command=ProtectRetainedVersionForRestoreCommand(
                request_id="request-a",
                idempotency_key="key-a",
                version_object_id="version-a",
                expected_row_version=2,
                explicit_confirmation=True,
            ),
            created_utc="2026-08-10T00:00:00.000Z",
        )
        _create_due_plan(store)
        expiring = store.protect_retained_version_for_restore(
            command=ProtectRetainedVersionForRestoreCommand(
                request_id="request-b",
                idempotency_key="key-b",
                version_object_id="version-a",
                expected_row_version=2,
                explicit_confirmation=True,
            ),
            created_utc="2026-09-01T00:00:02.000Z",
        )

        assert stale.protected is False
        assert stale.validation_code == "VERSION_RESTORE_VERSION_CHANGED"
        assert expiring.protected is False
        assert expiring.validation_code == "VERSION_RESTORE_VERSION_NOT_RETAINED"
    finally:
        connection.close()


def test_sqlite_due_versions_exclude_archived_jobs_and_active_holds(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff()
        )
        store = SqliteVersionRetentionStore(connection)

        due = store.list_due_retained_versions(
            cutoff_utc="2026-09-01T00:00:00.000Z",
            limit=10,
        )
        assert [item.version_object_id for item in due] == ["version-a"]

        connection.execute(
            "UPDATE jobs SET lifecycle_state = 'ARCHIVED', archived_utc = ? WHERE id = 'job-a'",
            ("2026-09-01T00:00:00.000Z",),
        )
        assert store.list_due_retained_versions(
            cutoff_utc="2026-09-01T00:00:00.000Z",
            limit=10,
        ) == ()

        connection.execute(
            "UPDATE jobs SET lifecycle_state = 'ACTIVE', archived_utc = NULL WHERE id = 'job-a'"
        )
        connection.execute(
            """
            INSERT INTO version_retention_holds (
                hold_id, version_object_id, reason, created_utc
            )
            VALUES ('hold-a', 'version-a', 'user restore review', ?)
            """,
            ("2026-09-01T00:00:00.000Z",),
        )
        assert store.list_due_retained_versions(
            cutoff_utc="2026-09-01T00:00:00.000Z",
            limit=10,
        ) == ()
    finally:
        connection.close()


def test_sqlite_retention_plan_marks_candidates_and_journals_manifest(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff()
        )
        store = SqliteVersionRetentionStore(connection)
        candidate = store.list_due_retained_versions(
            cutoff_utc="2026-09-01T00:00:00.000Z",
            limit=10,
        )[0]
        plan = create_version_retention_plan(
            plan_id="retention-a",
            cutoff_utc="2026-09-01T00:00:00.000Z",
            created_utc="2026-09-01T00:00:01.000Z",
            candidates=(candidate,),
        )

        first = store.create_version_retention_plan(plan)
        second = store.create_version_retention_plan(plan)

        assert first == second == plan
        object_row = connection.execute(
            """
            SELECT state, deletion_plan_id, row_version
            FROM retained_version_objects
            WHERE version_object_id = 'version-a'
            """
        ).fetchone()
        assert object_row == ("DELETE_PENDING", "retention-a", 2)
        item_row = connection.execute(
            """
            SELECT state, expected_object_row_version, expected_manifest_hash
            FROM version_retention_items
            WHERE plan_id = 'retention-a'
            """
        ).fetchone()
        assert item_row == ("PLANNED", 2, "d" * 64)
        assert connection.execute(
            "SELECT event_kind FROM version_retention_events WHERE plan_id = 'retention-a'"
        ).fetchone() == ("PLAN_CREATED",)
    finally:
        connection.close()


def test_sqlite_retention_plan_rechecks_archived_state_before_marking(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    try:
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff()
        )
        store = SqliteVersionRetentionStore(connection)
        candidate = store.list_due_retained_versions(
            cutoff_utc="2026-09-01T00:00:00.000Z",
            limit=10,
        )[0]
        plan = create_version_retention_plan(
            plan_id="retention-a",
            cutoff_utc="2026-09-01T00:00:00.000Z",
            created_utc="2026-09-01T00:00:01.000Z",
            candidates=(candidate,),
        )
        connection.execute(
            "UPDATE jobs SET lifecycle_state = 'ARCHIVED' WHERE id = 'job-a'"
        )
        connection.commit()

        with pytest.raises(
            SqliteVersionRetentionStoreError,
            match="VERSION_RETENTION_PLAN_CANDIDATE_CHANGED",
        ):
            store.create_version_retention_plan(plan)

        assert connection.execute(
            "SELECT count(*) FROM version_retention_plans"
        ).fetchone() == (0,)
        record = connection.execute(
            "SELECT state, row_version FROM retained_version_objects"
        ).fetchone()
        assert record == (RetainedVersionState.RETAINED.value, 1)
    finally:
        connection.close()


def test_sqlite_retention_execution_deletes_verified_version_and_journals(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_version_object(target_root)
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)
        lease = _Lease()

        outcome = maintain_version_retention(
            plan_id="retention-b",
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(lease),
            deletion=LocalVersionRetentionDeletionAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-09-01T00:00:02.000Z",
        )

        assert outcome.planning.scanned == 0
        assert outcome.planning.plan is None
        assert outcome.apply.deleted is True
        assert lease.released is True
        assert connection.execute(
            "SELECT count(*) FROM version_retention_plans"
        ).fetchone() == (1,)
        assert not (target_root / ".mediasync/objects/versions/version-a.payload").exists()
        assert not (
            target_root / ".mediasync/objects/versions/version-a.manifest.json"
        ).exists()
        assert connection.execute(
            "SELECT state, row_version FROM retained_version_objects"
        ).fetchone() == ("DELETED", 3)
        assert connection.execute(
            "SELECT state FROM version_retention_plans"
        ).fetchone() == ("COMPLETED",)
        assert [
            str(row[0])
            for row in connection.execute(
                "SELECT event_kind FROM version_retention_events ORDER BY plan_sequence"
            ).fetchall()
        ] == [
            "PLAN_CREATED",
            "DELETE_INTENT_RECORDED",
            "FILESYSTEM_DELETED",
            "ITEM_DELETED",
        ]
    finally:
        connection.close()


def test_sqlite_retention_execution_deletes_verified_empty_directory_quarantine(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_quarantine_object(target_root)
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)

        outcome = apply_next_version_retention_item(
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(_Lease()),
            deletion=LocalVersionRetentionDeletionAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-09-01T00:00:02.000Z",
        )

        object_root = target_root / ".mediasync/objects/quarantine"
        assert outcome.deleted is True
        assert not (object_root / "version-a.payload").exists()
        assert not (object_root / "version-a.manifest.json").exists()
        assert connection.execute(
            "SELECT state, object_role FROM retained_version_objects"
        ).fetchone() == ("DELETED", "EMPTY_DIRECTORY_QUARANTINE")
    finally:
        connection.close()


def test_sqlite_retention_execution_blocks_tampered_payload_without_deleting(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_version_object(target_root)
        payload_path = target_root / ".mediasync/objects/versions/version-a.payload"
        payload_path.write_bytes(b"tampered")
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)

        outcome = apply_next_version_retention_item(
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(_Lease()),
            deletion=LocalVersionRetentionDeletionAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-09-01T00:00:02.000Z",
        )

        assert outcome.deleted is False
        assert outcome.validation_codes == (
            "VERSION_RETENTION_PAYLOAD_FINGERPRINT_MISMATCH",
        )
        assert payload_path.read_bytes() == b"tampered"
        assert connection.execute(
            "SELECT state, deletion_plan_id FROM retained_version_objects"
        ).fetchone() == ("BLOCKED", None)
        assert store.list_due_retained_versions(
            cutoff_utc="2026-09-02T00:00:00.000Z",
            limit=10,
        ) == ()
        assert connection.execute(
            "SELECT state FROM version_retention_plans"
        ).fetchone() == ("BLOCKED",)
    finally:
        connection.close()


def test_sqlite_retention_execution_blocks_tampered_manifest_without_deleting(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_version_object(target_root)
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)
        manifest_path = (
            target_root / ".mediasync/objects/versions/version-a.manifest.json"
        )
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                '"job_id":"job-a"',
                '"job_id":"job-z"',
            ),
            encoding="utf-8",
        )

        outcome = apply_next_version_retention_item(
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(_Lease()),
            deletion=LocalVersionRetentionDeletionAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-09-01T00:00:02.000Z",
        )

        assert outcome.deleted is False
        assert outcome.validation_codes == (
            "VERSION_OBJECT_MANIFEST_HASH_MISMATCH",
        )
        assert (target_root / ".mediasync/objects/versions/version-a.payload").exists()
        assert manifest_path.exists()
        assert connection.execute(
            "SELECT state, deletion_plan_id FROM retained_version_objects"
        ).fetchone() == ("BLOCKED", None)
    finally:
        connection.close()


def test_sqlite_retention_execution_resumes_after_filesystem_delete_crash(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_version_object(target_root)
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)
        adapter = LocalVersionRetentionDeletionAdapter(
            root_resolver=_RootResolver(target_root)
        )
        first_lease = _Lease()
        first_permit = first_lease.issue_mutation_permit()
        planned = store.load_next_version_retention_item()
        assert planned is not None
        adapter.verify_retained_version(
            permit_validator=first_lease,
            permit=first_permit,
            item=planned,
        )
        intent = store.record_version_delete_intent(
            item=planned,
            permit=first_permit,
            event_utc="2026-09-01T00:00:02.000Z",
        )
        adapter.delete_retained_version(
            permit_validator=first_lease,
            permit=first_permit,
            item=intent,
            resuming_delete_intent=False,
        )
        first_lease.release()

        outcome = apply_next_version_retention_item(
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(_Lease(lease_id="retention-lease-b", fencing_token=8)),
            deletion=adapter,
            event_utc="2026-09-01T00:00:03.000Z",
        )

        assert outcome.deleted is True
        assert [
            str(row[0])
            for row in connection.execute(
                "SELECT event_kind FROM version_retention_events ORDER BY plan_sequence"
            ).fetchall()
        ] == [
            "PLAN_CREATED",
            "DELETE_INTENT_RECORDED",
            "DELETE_INTENT_REFRESHED",
            "FILESYSTEM_DELETED",
            "ITEM_DELETED",
        ]
    finally:
        connection.close()


def test_sqlite_retention_resume_keeps_partial_delete_journal_when_job_archives(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_version_object(target_root)
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)
        lease = _Lease()
        permit = lease.issue_mutation_permit()
        planned = store.load_next_version_retention_item()
        assert planned is not None
        intent = store.record_version_delete_intent(
            item=planned,
            permit=permit,
            event_utc="2026-09-01T00:00:02.000Z",
        )
        payload_path = target_root / ".mediasync/objects/versions/version-a.payload"
        payload_path.unlink()
        connection.execute(
            "UPDATE jobs SET lifecycle_state = 'ARCHIVED' WHERE id = 'job-a'"
        )
        connection.commit()

        outcome = apply_next_version_retention_item(
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(_Lease(lease_id="retention-lease-b")),
            deletion=LocalVersionRetentionDeletionAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-09-01T00:00:03.000Z",
        )

        assert outcome.deleted is False
        assert outcome.validation_codes == (
            "VERSION_RETENTION_DELETE_INTENT_REVALIDATION_FAILED",
        )
        assert connection.execute(
            "SELECT state, deletion_plan_id FROM retained_version_objects"
        ).fetchone() == ("DELETE_PENDING", "retention-a")
        assert connection.execute(
            "SELECT state FROM version_retention_plans"
        ).fetchone() == ("APPLYING",)
        assert connection.execute(
            "SELECT state FROM version_retention_items"
        ).fetchone() == ("DELETE_INTENT_RECORDED",)
        assert not payload_path.exists()
        assert (
            target_root / ".mediasync/objects/versions/version-a.manifest.json"
        ).exists()
        assert intent.state.value == "DELETE_INTENT_RECORDED"
    finally:
        connection.close()


def test_sqlite_retention_execution_rechecks_archive_after_plan(
    tmp_path: Path,
) -> None:
    connection = _prepared_catalog_connection(tmp_path)
    target_root = tmp_path / "target"
    try:
        manifest = _write_version_object(target_root)
        _insert_job(connection)
        SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
            _handoff_from_manifest(manifest)
        )
        store = SqliteVersionRetentionStore(connection)
        _create_due_plan(store)
        connection.execute(
            "UPDATE jobs SET lifecycle_state = 'ARCHIVED' WHERE id = 'job-a'"
        )
        connection.commit()

        outcome = apply_next_version_retention_item(
            versions=store,
            recovery_references=_ReleasedReferences(),
            leases=_LeaseAuthority(_Lease()),
            deletion=LocalVersionRetentionDeletionAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-09-01T00:00:02.000Z",
        )

        assert outcome.deleted is False
        assert outcome.validation_codes == (
            "VERSION_RETENTION_DELETE_INTENT_REVALIDATION_FAILED",
        )
        assert (target_root / ".mediasync/objects/versions/version-a.payload").exists()
        assert connection.execute(
            "SELECT state, deletion_plan_id FROM retained_version_objects"
        ).fetchone() == ("RETAINED", None)
        assert connection.execute(
            "SELECT state FROM version_retention_plans"
        ).fetchone() == ("BLOCKED",)
    finally:
        connection.close()


def test_sqlite_recovery_reference_requires_exact_cleaned_operation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    try:
        SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
            lease_id="lease-a",
            resource_key="endpoint:target-a",
            owner_instance_id="owner-a",
            ownership_epoch=3,
            run_id="run-a",
            run_target_id="run-target-a",
            endpoint_id="target-a",
            endpoint_generation=2,
            lease_mode="EXCLUSIVE",
            os_lock_kind="LOCAL_OS_HANDLE",
        )
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(
            durable_recovery_intent_segment(
                segment_id="segment-a",
                run_id="run-a",
                run_target_id="run-target-a",
                target_endpoint_id="target-a",
                target_endpoint_revision_id="target-rev-a",
                endpoint_generation=2,
                owner_installation_id="owner-a",
                ownership_epoch=3,
                lease_id="lease-a",
                fencing_token=1,
                segment_sequence=0,
                relative_path="installations/owner-a/recovery/run-a/segment.intent.jsonl",
                schema_version=1,
                operation_count=1,
                byte_count=1,
                segment_hash="a" * 64,
            )
        )
        store = SqliteRecoveryOperationStore(connection)
        store.record_planned_operation(
            planned_recovery_operation(
                run_id="run-a",
                run_target_id="run-target-a",
                operation_id="operation-a",
                target_endpoint_id="target-a",
                target_endpoint_revision_id="target-rev-a",
                endpoint_generation=2,
                owner_installation_id="owner-a",
                ownership_epoch=3,
                lease_id="lease-a",
                lease_resource_key="endpoint:target-a",
                fencing_token=1,
                final_relative_path="Photos/image.jpg",
                target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
                job_id="job-a",
                job_revision_id="job-rev-a",
                retention_policy="THIRTY_DAYS",
            ),
            process_instance_id="host-a",
        )
        connection.execute(
            """
            UPDATE recovery_operations
            SET
                phase = 'CLEANED',
                intent_segment_id = 'segment-a',
                intent_ordinal = 0,
                version_object_id = 'version-a',
                version_created_utc = '2026-08-01T00:00:00.000Z',
                version_retention_until_utc = '2026-08-31T00:00:00.000Z',
                version_manifest_hash = ?
            WHERE run_id = 'run-a' AND operation_id = 'operation-a'
            """,
            ("d" * 64,),
        )
        record = _retained_record()

        assert store.released_reference_validation_code(record) is None
        connection.execute(
            "UPDATE recovery_operations SET phase = 'USER_DECISION_REQUIRED'"
        )
        assert (
            store.released_reference_validation_code(record)
            == "VERSION_RETENTION_RECOVERY_REFERENCE_ACTIVE"
        )
        connection.execute(
            "UPDATE recovery_operations SET phase = 'CLEANED', version_manifest_hash = ?",
            ("e" * 64,),
        )
        assert (
            store.released_reference_validation_code(record)
            == "VERSION_RETENTION_RECOVERY_REFERENCE_MISMATCH"
        )
    finally:
        connection.close()


def _prepared_catalog_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
    return connection


def _insert_job(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'STANDARD_BACKUP')")


def _handoff() -> FinalFileCatalogHandoff:
    return FinalFileCatalogHandoff(
        handoff_id="final-file:run-a:operation-a",
        run_id="run-a",
        run_target_id="run-target-a",
        operation_id="operation-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        final_relative_path="Photos/image.jpg",
        content_hash="a" * 64,
        lease_id="lease-a",
        fencing_token=4,
        retained_version=RetainedVersionCatalogHandoff(
            version_object_id="version-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            endpoint_generation=2,
            owner_installation_id="owner-a",
            ownership_epoch=3,
            original_fingerprint_json='{"byte_count":9,"content_hash":"' + ("b" * 64) + '"}',
            created_utc="2026-08-01T00:00:00.000Z",
            retention_policy="THIRTY_DAYS",
            retention_until_utc="2026-08-31T00:00:00.000Z",
            manifest_hash="d" * 64,
        ),
    )


def _write_version_object(target_root: Path) -> VersionObjectManifest:
    payload = b"old-image"
    object_root = target_root / ".mediasync" / "objects" / "versions"
    object_root.mkdir(parents=True)
    manifest = create_version_object_manifest(
        version_object_id="version-a",
        operation_id="operation-a",
        run_id="run-a",
        run_target_id="run-target-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=2,
        owner_installation_id="owner-a",
        ownership_epoch=3,
        final_relative_path="Photos/image.jpg",
        fingerprint={
            "byte_count": len(payload),
            "content_hash": hashlib.sha256(payload).hexdigest(),
        },
        created_utc="2026-08-01T00:00:00.000Z",
        retention_policy="THIRTY_DAYS",
    )
    (object_root / "version-a.payload").write_bytes(payload)
    (object_root / "version-a.manifest.json").write_text(
        manifest.canonical_json,
        encoding="utf-8",
    )
    return manifest


def _write_quarantine_object(target_root: Path) -> VersionObjectManifest:
    object_root = target_root / ".mediasync" / "objects" / "quarantine"
    object_root.mkdir(parents=True)
    manifest = create_quarantine_object_manifest(
        version_object_id="version-a",
        operation_id="operation-a",
        run_id="run-a",
        run_target_id="run-target-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=2,
        owner_installation_id="owner-a",
        ownership_epoch=3,
        final_relative_path="Photos/image.jpg",
        fingerprint={"entry_count": 0, "kind": "DIRECTORY_EMPTY"},
        created_utc="2026-08-01T00:00:00.000Z",
        retention_policy="THIRTY_DAYS",
    )
    (object_root / "version-a.payload").mkdir()
    (object_root / "version-a.manifest.json").write_text(
        manifest.canonical_json,
        encoding="utf-8",
    )
    return manifest


def _handoff_from_manifest(manifest: VersionObjectManifest) -> FinalFileCatalogHandoff:
    return replace(
        _handoff(),
        retained_version=RetainedVersionCatalogHandoff(
            version_object_id=manifest.version_object_id,
            job_id=manifest.job_id,
            job_revision_id=manifest.job_revision_id,
            endpoint_generation=manifest.endpoint_generation,
            owner_installation_id=manifest.owner_installation_id,
            ownership_epoch=manifest.ownership_epoch,
            original_fingerprint_json=manifest.fingerprint_json,
            created_utc=manifest.created_utc,
            retention_policy=manifest.retention_policy,
            retention_until_utc=manifest.retention_until_utc,
            manifest_hash=manifest.manifest_hash,
            object_role=manifest.object_role,
        ),
    )


def _create_due_plan(store: SqliteVersionRetentionStore) -> None:
    candidate = store.list_due_retained_versions(
        cutoff_utc="2026-09-01T00:00:00.000Z",
        limit=10,
    )[0]
    store.create_version_retention_plan(
        create_version_retention_plan(
            plan_id="retention-a",
            cutoff_utc="2026-09-01T00:00:00.000Z",
            created_utc="2026-09-01T00:00:01.000Z",
            candidates=(candidate,),
        )
    )


def _retained_record() -> RetainedVersionRecord:
    return RetainedVersionRecord(
        version_object_id="version-a",
        handoff_id="final-file:run-a:operation-a",
        run_id="run-a",
        run_target_id="run-target-a",
        operation_id="operation-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=2,
        owner_installation_id="owner-a",
        ownership_epoch=3,
        final_relative_path="Photos/image.jpg",
        original_fingerprint_json='{"byte_count":9,"content_hash":"' + ("b" * 64) + '"}',
        created_utc="2026-08-01T00:00:00.000Z",
        retention_policy="THIRTY_DAYS",
        retention_until_utc="2026-08-31T00:00:00.000Z",
        manifest_hash="d" * 64,
        state=RetainedVersionState.RETAINED,
        row_version=1,
    )


class _ReleasedReferences:
    def released_reference_validation_code(
        self,
        record: RetainedVersionRecord,
    ) -> str | None:
        return None


class _RootResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        return self._root


class _Lease:
    def __init__(
        self,
        *,
        lease_id: str = "retention-lease-a",
        fencing_token: int = 7,
    ) -> None:
        self.lease_id = lease_id
        self.owner_installation_id = "owner-a"
        self.ownership_epoch = 3
        self.fencing_token = fencing_token
        self.released = False
        self._permit: MutationPermit | None = None

    def issue_mutation_permit(self) -> MutationPermit:
        self._permit = _issue_mutation_permit(
            lease_id=self.lease_id,
            resource_key="endpoint:target-a",
            owner_installation_id=self.owner_installation_id,
            ownership_epoch=self.ownership_epoch,
            fencing_token=self.fencing_token,
            run_id="version-retention:retention-a",
            run_target_id="version-retention:retention-a:0",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
            endpoint_generation=2,
        )
        return self._permit

    def assert_mutation_permit_current(self, permit: MutationPermit) -> None:
        assert permit is self._permit
        assert self.released is False

    def release(self) -> None:
        self.released = True


class _LeaseAuthority:
    def __init__(self, lease: _Lease) -> None:
        self._lease = lease

    def acquire_endpoint_lease(
        self,
        request: EndpointLeaseRequest,
    ) -> EndpointLeaseAttempt:
        assert request.endpoint_id == "target-a"
        return EndpointLeaseAttempt(
            acquired=True,
            lease=self._lease,
            validation_codes=(),
            next_action="acquired",
        )
