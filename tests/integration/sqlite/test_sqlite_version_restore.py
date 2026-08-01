from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.catalog_handoffs import (
    SqliteFinalFileCatalogHandoffStore,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.version_retention import SqliteVersionRetentionStore
from mediasync_home.adapters.version_restore import (
    LocalRetainedVersionRestoreAdapter,
    VersionRestoreFilesystemError,
)
from mediasync_home.application.catalog_handoff import (
    FinalFileCatalogHandoff,
    RetainedVersionCatalogHandoff,
)
from mediasync_home.application.retained_version_history import (
    ProtectRetainedVersionForRestoreCommand,
    RestoreRetainedVersionCommand,
    UndoRetainedVersionRestoreCommand,
)
from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseRequest,
)
from mediasync_home.application.version_objects import (
    VersionObjectManifest,
    create_version_object_manifest,
)
from mediasync_home.application.version_restore import (
    VersionRestoreFilesystemPort,
    apply_next_version_restore,
)
from mediasync_home.application.version_restore_rollback import (
    VersionRestoreRollbackFilesystemPort,
    apply_next_version_restore_rollback_expiry,
    apply_next_version_restore_undo,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_retained_version_restore_preserves_current_and_applies_historical(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _schedule_restore(store)
        leases = _LeaseAuthority()

        outcome = apply_next_version_restore(
            restores=store,
            leases=leases,
            filesystem=LocalRetainedVersionRestoreAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-08-10T00:00:01.000Z",
        )

        assert outcome.completed is True
        assert (target_root / "Photos/image.jpg").read_bytes() == b"old-image"
        rollback = next(
            (target_root / ".mediasync/objects/restores").glob("*.payload")
        )
        assert rollback.read_bytes() == b"current-image"
        assert connection.execute(
            "SELECT state, completed_utc FROM retained_version_restore_operations"
        ).fetchone() == ("COMPLETED", "2026-08-10T00:00:01.000Z")
        assert connection.execute(
            "SELECT released_utc FROM version_retention_holds"
        ).fetchone() == ("2026-08-10T00:00:01.000Z",)
        assert [
            str(row[0])
            for row in connection.execute(
                """
                SELECT event_kind
                FROM retained_version_restore_events
                ORDER BY restore_sequence
                """
            ).fetchall()
        ] == [
            "RESTORE_REQUESTED",
            "RESTORE_INTENT_RECORDED",
            "CURRENT_FINAL_PRESERVED",
            "HISTORICAL_VERSION_APPLIED",
            "RESTORED_FINAL_VERIFIED",
            "RESTORE_COMPLETED",
        ]
        assert leases.leases and all(lease.released for lease in leases.leases)
    finally:
        connection.close()


@pytest.mark.parametrize("failure_stage", ["preserve", "apply"])
def test_retained_version_restore_resumes_after_filesystem_before_journal_crash(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _schedule_restore(store)
        adapter = LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        )
        crashing = _CrashAfterEffect(adapter, failure_stage=failure_stage)
        leases = _LeaseAuthority()

        first = apply_next_version_restore(
            restores=store,
            leases=leases,
            filesystem=crashing,
            event_utc="2026-08-10T00:00:01.000Z",
        )
        resumed = apply_next_version_restore(
            restores=store,
            leases=leases,
            filesystem=adapter,
            event_utc="2026-08-10T00:00:02.000Z",
        )

        assert first.completed is False
        assert first.validation_codes == ("TEST_INTERRUPTED_AFTER_EFFECT",)
        assert resumed.completed is True
        assert (target_root / "Photos/image.jpg").read_bytes() == b"old-image"
        rollback = next(
            (target_root / ".mediasync/objects/restores").glob("*.payload")
        )
        assert rollback.read_bytes() == b"current-image"
        assert connection.execute(
            "SELECT state FROM retained_version_restore_operations"
        ).fetchone() == ("COMPLETED",)
    finally:
        connection.close()


def test_restore_request_requires_active_protection_and_is_idempotent(
    tmp_path: Path,
) -> None:
    connection, _ = _prepared_restore(tmp_path, protect=False)
    try:
        store = SqliteVersionRetentionStore(connection)
        command = RestoreRetainedVersionCommand(
            request_id="request-restore",
            idempotency_key="restore-key",
            version_object_id="version-a",
            expected_row_version=1,
            explicit_confirmation=True,
        )

        rejected = store.request_retained_version_restore(
            command=command,
            created_utc="2026-08-10T00:00:00.000Z",
        )
        _protect(store)
        scheduled = store.request_retained_version_restore(
            command=command,
            created_utc="2026-08-10T00:00:00.000Z",
        )
        replay = store.request_retained_version_restore(
            command=command,
            created_utc="2026-08-10T00:00:01.000Z",
        )

        assert rejected.scheduled is False
        assert rejected.validation_code == "VERSION_RESTORE_PROTECTION_REQUIRED"
        assert scheduled.scheduled is True
        assert replay.scheduled is True and replay.idempotent_replay is True
        assert connection.execute(
            "SELECT count(*) FROM retained_version_restore_operations"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_retained_version_restore_blocks_tampered_history_without_touching_final(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _schedule_restore(store)
        (target_root / ".mediasync/objects/versions/version-a.payload").write_bytes(
            b"tampered"
        )

        outcome = apply_next_version_restore(
            restores=store,
            leases=_LeaseAuthority(),
            filesystem=LocalRetainedVersionRestoreAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-08-10T00:00:01.000Z",
        )

        assert outcome.completed is False
        assert outcome.validation_codes == (
            "VERSION_RESTORE_HISTORICAL_PAYLOAD_MISMATCH",
        )
        assert (target_root / "Photos/image.jpg").read_bytes() == b"current-image"
        assert connection.execute(
            "SELECT state FROM retained_version_restore_operations"
        ).fetchone() == ("FAILED_BLOCKED",)
        assert connection.execute(
            "SELECT released_utc FROM version_retention_holds"
        ).fetchone() == (None,)
        repeated = store.request_retained_version_restore(
            command=RestoreRetainedVersionCommand(
                request_id="request-after-block",
                idempotency_key="restore-after-block",
                version_object_id="version-a",
                expected_row_version=1,
                explicit_confirmation=True,
            ),
            created_utc="2026-08-10T00:00:02.000Z",
        )
        assert repeated.scheduled is False
        assert repeated.validation_code == "VERSION_RESTORE_REVIEW_REQUIRED"
    finally:
        connection.close()


def test_retained_version_restore_blocks_when_current_final_changes_after_intent(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _schedule_restore(store)
        adapter = LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        )

        outcome = apply_next_version_restore(
            restores=store,
            leases=_LeaseAuthority(),
            filesystem=_ChangeFinalBeforePreserve(
                adapter,
                final_path=target_root / "Photos/image.jpg",
            ),
            event_utc="2026-08-10T00:00:01.000Z",
        )

        assert outcome.completed is False
        assert outcome.validation_codes == (
            "VERSION_RESTORE_CURRENT_FINAL_CHANGED_BEFORE_PRESERVE",
        )
        assert (target_root / "Photos/image.jpg").read_bytes() == b"newer-image"
        assert not (target_root / ".mediasync/objects/restores").exists()
        assert connection.execute(
            "SELECT state FROM retained_version_restore_operations"
        ).fetchone() == ("FAILED_BLOCKED",)
        assert connection.execute(
            "SELECT released_utc FROM version_retention_holds"
        ).fetchone() == (None,)
    finally:
        connection.close()


def test_completed_restore_can_be_protected_and_scheduled_again(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _schedule_restore(store)
        completed = apply_next_version_restore(
            restores=store,
            leases=_LeaseAuthority(),
            filesystem=LocalRetainedVersionRestoreAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-08-10T00:00:01.000Z",
        )
        assert completed.completed

        protected = store.protect_retained_version_for_restore(
            command=ProtectRetainedVersionForRestoreCommand(
                request_id="request-protect-again",
                idempotency_key="protect-key-again",
                version_object_id="version-a",
                expected_row_version=1,
                explicit_confirmation=True,
            ),
            created_utc="2026-08-10T00:00:02.000Z",
        )
        scheduled = store.request_retained_version_restore(
            command=RestoreRetainedVersionCommand(
                request_id="request-restore-again",
                idempotency_key="restore-key-again",
                version_object_id="version-a",
                expected_row_version=1,
                explicit_confirmation=True,
            ),
            created_utc="2026-08-10T00:00:03.000Z",
        )

        assert protected.protected
        assert protected.version is not None
        assert protected.version.restore_state is None
        assert scheduled.scheduled
        assert scheduled.idempotent_replay is False
        assert connection.execute(
            "SELECT count(*) FROM retained_version_restore_operations"
        ).fetchone() == (2,)
    finally:
        connection.close()


def test_completed_restore_can_be_undone_and_rollback_expires_when_due(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _complete_restore(store, target_root=target_root)
        _schedule_undo(store)
        adapter = LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        )

        undone = apply_next_version_restore_undo(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=adapter,
            event_utc="2026-08-11T00:00:00.000Z",
        )

        assert undone.completed is True
        assert undone.action == "undo"
        assert (target_root / "Photos/image.jpg").read_bytes() == b"current-image"
        assert connection.execute(
            "SELECT state FROM retained_version_restore_rollbacks"
        ).fetchone() == ("UNDONE",)
        assert [
            str(row[0])
            for row in connection.execute(
                """
                SELECT event_kind
                FROM retained_version_restore_rollback_events
                ORDER BY lifecycle_sequence
                """
            ).fetchall()
        ] == [
            "RESTORE_ROLLBACK_AVAILABLE",
            "RESTORE_UNDO_REQUESTED",
            "RESTORE_UNDO_INTENT_RECORDED",
            "RESTORE_UNDO_APPLIED",
            "RESTORE_UNDO_LEASE_REFRESHED",
            "RESTORE_UNDO_VERIFIED",
            "RESTORE_UNDO_COMPLETED",
        ]

        expired = apply_next_version_restore_rollback_expiry(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=adapter,
            event_utc="2026-09-10T00:00:00.000Z",
        )

        assert expired.completed is True
        assert expired.action == "expiry"
        assert connection.execute(
            "SELECT state FROM retained_version_restore_rollbacks"
        ).fetchone() == ("EXPIRED",)
        assert not next(
            (target_root / ".mediasync/objects/restores").glob("*.payload"),
            None,
        )
        assert not next(
            (target_root / ".mediasync/objects/restores").glob("*.manifest.json"),
            None,
        )
    finally:
        connection.close()


def test_restore_undo_blocks_when_final_changed_after_restore(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _complete_restore(store, target_root=target_root)
        _schedule_undo(store)
        final_path = target_root / "Photos/image.jpg"
        final_path.write_bytes(b"new-work-after-restore")

        outcome = apply_next_version_restore_undo(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=LocalRetainedVersionRestoreAdapter(
                root_resolver=_RootResolver(target_root)
            ),
            event_utc="2026-08-11T00:00:00.000Z",
        )

        assert outcome.completed is False
        assert outcome.validation_codes == ("VERSION_RESTORE_UNDO_FINAL_CHANGED",)
        assert final_path.read_bytes() == b"new-work-after-restore"
        assert connection.execute(
            "SELECT state FROM retained_version_restore_rollbacks"
        ).fetchone() == ("FAILED_BLOCKED",)
        assert next(
            (target_root / ".mediasync/objects/restores").glob("*.payload")
        ).read_bytes() == b"current-image"
    finally:
        connection.close()


def test_restore_undo_resumes_after_apply_before_journal_crash(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _complete_restore(store, target_root=target_root)
        _schedule_undo(store)
        adapter = LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        )
        crashing = _CrashRollbackLifecycleAfterEffect(
            adapter,
            failure_stage="undo",
        )

        first = apply_next_version_restore_undo(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=crashing,
            event_utc="2026-08-11T00:00:00.000Z",
        )
        resumed = apply_next_version_restore_undo(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=adapter,
            event_utc="2026-08-11T00:00:01.000Z",
        )

        assert first.completed is False
        assert first.validation_codes == ("TEST_ROLLBACK_LIFECYCLE_INTERRUPTED",)
        assert resumed.completed is True
        assert (target_root / "Photos/image.jpg").read_bytes() == b"current-image"
        assert connection.execute(
            "SELECT state FROM retained_version_restore_rollbacks"
        ).fetchone() == ("UNDONE",)
    finally:
        connection.close()


def test_restore_rollback_expiry_resumes_after_delete_before_journal_crash(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _complete_restore(store, target_root=target_root)
        adapter = LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        )
        crashing = _CrashRollbackLifecycleAfterEffect(
            adapter,
            failure_stage="expiry",
        )

        first = apply_next_version_restore_rollback_expiry(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=crashing,
            event_utc="2026-09-10T00:00:00.000Z",
        )
        resumed = apply_next_version_restore_rollback_expiry(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=adapter,
            event_utc="2026-09-10T00:00:01.000Z",
        )

        assert first.completed is False
        assert first.validation_codes == ("TEST_ROLLBACK_LIFECYCLE_INTERRUPTED",)
        assert resumed.completed is True
        assert connection.execute(
            "SELECT state FROM retained_version_restore_rollbacks"
        ).fetchone() == ("EXPIRED",)
    finally:
        connection.close()


def test_restore_rollback_expiry_resumes_after_payload_delete_before_manifest(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _complete_restore(store, target_root=target_root)
        adapter = LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        )
        crashing = _CrashAfterRollbackPayloadDelete(
            adapter,
            target_root=target_root,
        )

        first = apply_next_version_restore_rollback_expiry(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=crashing,
            event_utc="2026-09-10T00:00:00.000Z",
        )
        object_root = target_root / ".mediasync/objects/restores"
        assert first.completed is False
        assert first.validation_codes == ("TEST_INTERRUPTED_BETWEEN_PAIR_DELETES",)
        assert not next(object_root.glob("*.payload"), None)
        assert next(object_root.glob("*.manifest.json"), None) is not None

        resumed = apply_next_version_restore_rollback_expiry(
            rollbacks=store,
            leases=_LeaseAuthority(),
            filesystem=adapter,
            event_utc="2026-09-10T00:00:01.000Z",
        )

        assert resumed.completed is True
        assert not next(object_root.glob("*.manifest.json"), None)
        assert connection.execute(
            "SELECT state FROM retained_version_restore_rollbacks"
        ).fetchone() == ("EXPIRED",)
    finally:
        connection.close()


def test_restore_undo_request_rejects_expired_window(
    tmp_path: Path,
) -> None:
    connection, target_root = _prepared_restore(tmp_path)
    try:
        store = SqliteVersionRetentionStore(connection)
        _complete_restore(store, target_root=target_root)

        outcome = store.request_retained_version_restore_undo(
            command=UndoRetainedVersionRestoreCommand(
                request_id="request-undo",
                idempotency_key="undo-key",
                restore_id=_restore_id_for_test(),
                version_object_id="version-a",
                expected_row_version=1,
                explicit_confirmation=True,
            ),
            created_utc="2026-09-10T00:00:00.000Z",
        )

        assert outcome.scheduled is False
        assert outcome.validation_code == "VERSION_RESTORE_UNDO_WINDOW_EXPIRED"
    finally:
        connection.close()


def _prepared_restore(
    tmp_path: Path,
    *,
    protect: bool = True,
) -> tuple[sqlite3.Connection, Path]:
    database = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'STANDARD_BACKUP')")
    target_root = tmp_path / "target"
    manifest = _write_version_object(target_root)
    final_path = target_root / "Photos/image.jpg"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"current-image")
    SqliteFinalFileCatalogHandoffStore(connection).record_final_file_handoff(
        _handoff_from_manifest(manifest)
    )
    if protect:
        _protect(SqliteVersionRetentionStore(connection))
    return connection, target_root


def _protect(store: SqliteVersionRetentionStore) -> None:
    outcome = store.protect_retained_version_for_restore(
        command=ProtectRetainedVersionForRestoreCommand(
            request_id="request-protect",
            idempotency_key="protect-key",
            version_object_id="version-a",
            expected_row_version=1,
            explicit_confirmation=True,
        ),
        created_utc="2026-08-10T00:00:00.000Z",
    )
    assert outcome.protected


def _schedule_restore(store: SqliteVersionRetentionStore) -> None:
    outcome = store.request_retained_version_restore(
        command=RestoreRetainedVersionCommand(
            request_id="request-restore",
            idempotency_key="restore-key",
            version_object_id="version-a",
            expected_row_version=1,
            explicit_confirmation=True,
        ),
        created_utc="2026-08-10T00:00:00.000Z",
    )
    assert outcome.scheduled


def _complete_restore(
    store: SqliteVersionRetentionStore,
    *,
    target_root: Path,
) -> None:
    _schedule_restore(store)
    outcome = apply_next_version_restore(
        restores=store,
        leases=_LeaseAuthority(),
        filesystem=LocalRetainedVersionRestoreAdapter(
            root_resolver=_RootResolver(target_root)
        ),
        event_utc="2026-08-10T00:00:01.000Z",
    )
    assert outcome.completed


def _schedule_undo(store: SqliteVersionRetentionStore) -> None:
    outcome = store.request_retained_version_restore_undo(
        command=UndoRetainedVersionRestoreCommand(
            request_id="request-undo",
            idempotency_key="undo-key",
            restore_id=_restore_id_for_test(),
            version_object_id="version-a",
            expected_row_version=1,
            explicit_confirmation=True,
        ),
        created_utc="2026-08-11T00:00:00.000Z",
    )
    assert outcome.scheduled


def _restore_id_for_test() -> str:
    return f"restore-{hashlib.sha256(b'restore-key').hexdigest()[:32]}"


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


def _handoff_from_manifest(manifest: VersionObjectManifest) -> FinalFileCatalogHandoff:
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
            version_object_id=manifest.version_object_id,
            job_id=manifest.job_id,
            job_revision_id=manifest.job_revision_id,
            endpoint_generation=manifest.endpoint_generation,
            owner_installation_id=manifest.owner_installation_id,
            ownership_epoch=manifest.ownership_epoch,
            original_fingerprint_json=(
                '{"byte_count":9,"content_hash":"'
                + manifest.fingerprint_content_hash
                + '"}'
            ),
            created_utc=manifest.created_utc,
            retention_policy=manifest.retention_policy,
            retention_until_utc=manifest.retention_until_utc,
            manifest_hash=manifest.manifest_hash,
        ),
    )


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
        assert resource_key == "endpoint:target-a"
        assert endpoint_id == "target-a"
        assert endpoint_revision_id == "target-rev-a"
        return self._root


class _Lease:
    def __init__(self, request: EndpointLeaseRequest, *, sequence: int) -> None:
        self._request = request
        self.lease_id = f"restore-lease-{sequence}"
        self.fencing_token = sequence
        self.released = False
        self._permit: MutationPermit | None = None

    def issue_mutation_permit(self) -> MutationPermit:
        self._permit = _issue_mutation_permit(
            lease_id=self.lease_id,
            resource_key=self._request.resource_key,
            owner_installation_id="owner-a",
            ownership_epoch=3,
            fencing_token=self.fencing_token,
            run_id=self._request.run_id,
            run_target_id=self._request.run_target_id,
            endpoint_id=self._request.endpoint_id,
            endpoint_revision_id=self._request.endpoint_revision_id,
            endpoint_generation=2,
        )
        return self._permit

    def assert_mutation_permit_current(self, permit: MutationPermit) -> None:
        assert permit is self._permit
        assert not self.released

    def release(self) -> None:
        self.released = True


class _LeaseAuthority:
    def __init__(self) -> None:
        self.leases: list[_Lease] = []

    def acquire_endpoint_lease(
        self,
        request: EndpointLeaseRequest,
    ) -> EndpointLeaseAttempt:
        lease = _Lease(request, sequence=len(self.leases) + 1)
        self.leases.append(lease)
        return EndpointLeaseAttempt(
            acquired=True,
            lease=lease,
            validation_codes=(),
            next_action="acquired",
        )


class _CrashAfterEffect:
    def __init__(
        self,
        delegate: VersionRestoreFilesystemPort,
        *,
        failure_stage: str,
    ) -> None:
        self._delegate = delegate
        self._failure_stage = failure_stage
        self._raised = False

    def inspect_restore(self, **kwargs):
        return self._delegate.inspect_restore(**kwargs)

    def preserve_current_final(self, **kwargs):
        receipt = self._delegate.preserve_current_final(**kwargs)
        self._raise_once("preserve")
        return receipt

    def apply_historical_version(self, **kwargs):
        receipt = self._delegate.apply_historical_version(**kwargs)
        self._raise_once("apply")
        return receipt

    def verify_restored_final(self, **kwargs):
        return self._delegate.verify_restored_final(**kwargs)

    def _raise_once(self, stage: str) -> None:
        if self._raised or stage != self._failure_stage:
            return
        self._raised = True
        raise VersionRestoreFilesystemError(
            "TEST_INTERRUPTED_AFTER_EFFECT",
            "resume",
            retryable=True,
        )


class _ChangeFinalBeforePreserve:
    def __init__(
        self,
        delegate: VersionRestoreFilesystemPort,
        *,
        final_path: Path,
    ) -> None:
        self._delegate = delegate
        self._final_path = final_path

    def inspect_restore(self, **kwargs):
        return self._delegate.inspect_restore(**kwargs)

    def preserve_current_final(self, **kwargs):
        self._final_path.write_bytes(b"newer-image")
        return self._delegate.preserve_current_final(**kwargs)

    def apply_historical_version(self, **kwargs):
        return self._delegate.apply_historical_version(**kwargs)

    def verify_restored_final(self, **kwargs):
        return self._delegate.verify_restored_final(**kwargs)


class _CrashRollbackLifecycleAfterEffect:
    def __init__(
        self,
        delegate: VersionRestoreRollbackFilesystemPort,
        *,
        failure_stage: str,
    ) -> None:
        self._delegate = delegate
        self._failure_stage = failure_stage
        self._raised = False

    def inspect_restore_undo(self, **kwargs):
        return self._delegate.inspect_restore_undo(**kwargs)

    def apply_restore_undo(self, **kwargs):
        receipt = self._delegate.apply_restore_undo(**kwargs)
        self._raise_once("undo")
        return receipt

    def verify_restore_undo(self, **kwargs):
        return self._delegate.verify_restore_undo(**kwargs)

    def verify_restore_rollback_for_expiry(self, **kwargs):
        return self._delegate.verify_restore_rollback_for_expiry(**kwargs)

    def delete_restore_rollback(self, **kwargs):
        receipt = self._delegate.delete_restore_rollback(**kwargs)
        self._raise_once("expiry")
        return receipt

    def _raise_once(self, stage: str) -> None:
        if self._raised or stage != self._failure_stage:
            return
        self._raised = True
        raise VersionRestoreFilesystemError(
            "TEST_ROLLBACK_LIFECYCLE_INTERRUPTED",
            "resume",
            retryable=True,
        )


class _CrashAfterRollbackPayloadDelete:
    def __init__(
        self,
        delegate: VersionRestoreRollbackFilesystemPort,
        *,
        target_root: Path,
    ) -> None:
        self._delegate = delegate
        self._target_root = target_root

    def inspect_restore_undo(self, **kwargs):
        return self._delegate.inspect_restore_undo(**kwargs)

    def apply_restore_undo(self, **kwargs):
        return self._delegate.apply_restore_undo(**kwargs)

    def verify_restore_undo(self, **kwargs):
        return self._delegate.verify_restore_undo(**kwargs)

    def verify_restore_rollback_for_expiry(self, **kwargs):
        return self._delegate.verify_restore_rollback_for_expiry(**kwargs)

    def delete_restore_rollback(self, **kwargs):
        operation = kwargs["operation"]
        payload_path = (
            self._target_root
            / ".mediasync/objects/restores"
            / f"{operation.rollback_object_id}.payload"
        )
        payload_path.unlink()
        raise VersionRestoreFilesystemError(
            "TEST_INTERRUPTED_BETWEEN_PAIR_DELETES",
            "resume",
            retryable=True,
        )
