from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.adapters.endpoint_leases import LocalEndpointLease
from mediasync_home.adapters.final_commit import (
    LabNoOverwriteFinalCommitAdapter,
    LocalVersionedReplaceFinalCommitAdapter,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, recovery_migration_plan
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.directory_recovery import (
    SqliteDirectoryRecoveryStore,
)
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_final_commits import commit_next_run_target_verified_artifact
from mediasync_home.application.run_intent_segments import publish_run_target_recovery_intent_segment
from mediasync_home.application.run_preserved_old_target_restore import (
    restore_next_run_target_preserved_old_target,
)
from mediasync_home.application.directory_recovery import (
    DirectoryRecoveryKind,
    directory_recovery_id,
)
from mediasync_home.generated.contract_types import DirectoryRestoreState


def test_sqlite_run_final_commit_bridge_applies_lab_commit(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        recovery_operations = SqliteRecoveryOperationStore(connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        payload = b"image"
        operation = _record_staging_verified_operation(
            recovery_operations,
            content_hash=_sha256(payload),
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        (staging_root / "op-a.payload").write_bytes(payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        intent = publish_run_target_recovery_intent_segment(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )
        assert intent.published is True
        assert operation.operation_id == "op-a"

        outcome = commit_next_run_target_verified_artifact(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            final_commit_port=LabNoOverwriteFinalCommitAdapter(
                target_root=target_root,
                staging_root=staging_root,
                permit_validator=lease,
            ),
            process_instance_id="host-a",
        )

        loaded = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        assert outcome.committed is True
        assert outcome.validation_codes == ()
        assert outcome.receipt is not None
        assert outcome.receipt.operation_id == "op-a"
        assert loaded is not None
        assert loaded.phase is RecoveryOperationPhase.FINAL_VERIFIED
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == payload
        assert _event_phases(connection)[-4:] == [
            "COMMIT_PRECONDITIONS_REVALIDATED",
            "FILESYSTEM_APPLIED",
            "FINAL_DURABLE",
            "FINAL_VERIFIED",
        ]
    finally:
        connection.close()


def test_sqlite_run_final_commit_bridge_resumes_preserved_replacement(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        recovery_operations = SqliteRecoveryOperationStore(connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        old_payload = b"old-image"
        new_payload = b"new-image"
        operation = _record_staging_verified_operation(
            recovery_operations,
            content_hash=_sha256(new_payload),
            content_byte_count=len(new_payload),
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
            expected_target_payload=old_payload,
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        (target_root / "Pictures" / "A.jpg").write_bytes(old_payload)
        (staging_root / "op-a.payload").write_bytes(new_payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        adapter = LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )
        intent = publish_run_target_recovery_intent_segment(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )
        assert intent.published is True
        preconditions = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            next_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            process_instance_id="host-a",
        )
        assert preconditions is not None
        preservation = adapter.preserve_old_target(lease.issue_mutation_permit(), preconditions)
        preserved = recovery_operations.record_operation_phase_transition(
            run_id=preconditions.run_id,
            operation_id=preconditions.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            next_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
            process_instance_id="host-a",
            operation_metadata=RecoveryOperationMetadata(
                version_object_id=preservation.version_object_id,
                version_created_utc=preservation.version_created_utc,
                version_retention_until_utc=preservation.version_retention_until_utc,
                version_manifest_hash=preservation.version_manifest_hash,
            ),
        )
        assert preserved is not None

        outcome = commit_next_run_target_verified_artifact(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            final_commit_port=adapter,
            process_instance_id="host-a",
        )

        loaded = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        assert outcome.committed is True
        assert outcome.validation_codes == ()
        assert loaded is not None
        assert loaded.phase is RecoveryOperationPhase.FINAL_VERIFIED
        assert loaded.version_object_id == "op-a"
        assert loaded.final_durability_state == "LOCAL_FILE_FLUSH_CONFIRMED"
        assert (target_root / "Pictures" / "A.jpg").read_bytes() == new_payload
        assert (target_root / ".mediasync" / "objects" / "versions" / "op-a.payload").read_bytes() == old_payload
        assert _event_phases(connection)[-3:] == [
            "FILESYSTEM_APPLIED",
            "FINAL_DURABLE",
            "FINAL_VERIFIED",
        ]
    finally:
        connection.close()


def test_sqlite_run_final_commit_bridge_completes_preserved_replacement_when_final_missing(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        recovery_operations = SqliteRecoveryOperationStore(connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        old_payload = b"old-image"
        new_payload = b"new-image"
        operation = _record_staging_verified_operation(
            recovery_operations,
            content_hash=_sha256(new_payload),
            content_byte_count=len(new_payload),
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
            expected_target_payload=old_payload,
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        final = target_root / "Pictures" / "A.jpg"
        final.write_bytes(old_payload)
        (staging_root / "op-a.payload").write_bytes(new_payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        adapter = LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )
        intent = publish_run_target_recovery_intent_segment(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )
        assert intent.published is True
        preconditions = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            next_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            process_instance_id="host-a",
        )
        assert preconditions is not None
        preservation = adapter.preserve_old_target(lease.issue_mutation_permit(), preconditions)
        preserved = recovery_operations.record_operation_phase_transition(
            run_id=preconditions.run_id,
            operation_id=preconditions.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            next_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
            process_instance_id="host-a",
            operation_metadata=RecoveryOperationMetadata(
                version_object_id=preservation.version_object_id,
                version_created_utc=preservation.version_created_utc,
                version_retention_until_utc=preservation.version_retention_until_utc,
                version_manifest_hash=preservation.version_manifest_hash,
            ),
        )
        assert preserved is not None
        final.unlink()

        outcome = commit_next_run_target_verified_artifact(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            final_commit_port=adapter,
            process_instance_id="host-a",
        )

        loaded = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        assert outcome.committed is True
        assert outcome.validation_codes == ()
        assert loaded is not None
        assert loaded.phase is RecoveryOperationPhase.FINAL_VERIFIED
        assert final.read_bytes() == new_payload
        assert (target_root / ".mediasync" / "objects" / "versions" / "op-a.payload").read_bytes() == old_payload
        assert _event_phases(connection)[-3:] == [
            "FILESYSTEM_APPLIED",
            "FINAL_DURABLE",
            "FINAL_VERIFIED",
        ]
    finally:
        connection.close()


def test_sqlite_run_final_commit_bridge_marks_preserved_drift_user_decision(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        recovery_operations = SqliteRecoveryOperationStore(connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        old_payload = b"old-image"
        new_payload = b"new-image"
        operation = _record_staging_verified_operation(
            recovery_operations,
            content_hash=_sha256(new_payload),
            content_byte_count=len(new_payload),
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
            expected_target_payload=old_payload,
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        final = target_root / "Pictures" / "A.jpg"
        final.write_bytes(old_payload)
        (staging_root / "op-a.payload").write_bytes(new_payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        adapter = LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )
        intent = publish_run_target_recovery_intent_segment(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )
        assert intent.published is True
        preconditions = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            next_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            process_instance_id="host-a",
        )
        assert preconditions is not None
        preservation = adapter.preserve_old_target(lease.issue_mutation_permit(), preconditions)
        preserved = recovery_operations.record_operation_phase_transition(
            run_id=preconditions.run_id,
            operation_id=preconditions.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            next_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
            process_instance_id="host-a",
            operation_metadata=RecoveryOperationMetadata(
                version_object_id=preservation.version_object_id,
                version_created_utc=preservation.version_created_utc,
                version_retention_until_utc=preservation.version_retention_until_utc,
                version_manifest_hash=preservation.version_manifest_hash,
            ),
        )
        assert preserved is not None
        final.write_bytes(b"edited-after-preserve")

        outcome = commit_next_run_target_verified_artifact(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            final_commit_port=adapter,
            process_instance_id="host-a",
        )

        loaded = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        assert outcome.committed is False
        assert outcome.validation_codes == ("LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",)
        assert loaded is not None
        assert loaded.phase is RecoveryOperationPhase.USER_DECISION_REQUIRED
        assert loaded.last_error_code == "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE"
        assert final.read_bytes() == b"edited-after-preserve"
        assert (target_root / ".mediasync" / "objects" / "versions" / "op-a.payload").read_bytes() == old_payload
        assert _event_phases(connection)[-1:] == ["USER_DECISION_REQUIRED"]
    finally:
        connection.close()


def test_sqlite_preserved_old_target_restore_records_cancelled_operation(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        recovery_operations = SqliteRecoveryOperationStore(connection)
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        old_payload = b"old-image"
        new_payload = b"new-image"
        operation = _record_staging_verified_operation(
            recovery_operations,
            content_hash=_sha256(new_payload),
            content_byte_count=len(new_payload),
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
            expected_target_payload=old_payload,
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        final = target_root / "Pictures" / "A.jpg"
        final.write_bytes(old_payload)
        (staging_root / "op-a.payload").write_bytes(new_payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        adapter = LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )
        intent = publish_run_target_recovery_intent_segment(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )
        assert intent.published is True
        preconditions = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            next_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            process_instance_id="host-a",
        )
        assert preconditions is not None
        preservation = adapter.preserve_old_target(lease.issue_mutation_permit(), preconditions)
        preserved = recovery_operations.record_operation_phase_transition(
            run_id=preconditions.run_id,
            operation_id=preconditions.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            next_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
            process_instance_id="host-a",
            operation_metadata=RecoveryOperationMetadata(
                version_object_id=preservation.version_object_id,
                version_created_utc=preservation.version_created_utc,
                version_retention_until_utc=preservation.version_retention_until_utc,
                version_manifest_hash=preservation.version_manifest_hash,
            ),
        )
        assert preserved is not None
        final.unlink()

        outcome = restore_next_run_target_preserved_old_target(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            old_target_restore_port=adapter,
            process_instance_id="host-a",
        )

        loaded = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        assert outcome.restored is True
        assert outcome.validation_codes == ()
        assert loaded is not None
        assert loaded.phase is RecoveryOperationPhase.CANCELLED
        assert loaded.last_error_code == "RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED"
        assert final.read_bytes() == old_payload
        assert (target_root / ".mediasync" / "objects" / "versions" / "op-a.payload").read_bytes() == old_payload
        assert _event_phases(connection)[-1:] == ["CANCELLED"]
    finally:
        connection.close()


def test_sqlite_preserved_empty_directory_restore_records_cancelled_operation(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        directory_recovery = SqliteDirectoryRecoveryStore(connection)
        recovery_operations = SqliteRecoveryOperationStore(
            connection,
            directory_recovery_store=directory_recovery,
        )
        intent_segments = SqliteRecoveryIntentSegmentStore(connection)
        new_payload = b"new-image"
        operation = _record_staging_verified_operation(
            recovery_operations,
            content_hash=_sha256(new_payload),
            content_byte_count=len(new_payload),
            target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
            expected_target_fingerprint_json=_empty_directory_fingerprint_json(),
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        final = target_root / "Pictures" / "A.jpg"
        final.mkdir()
        (staging_root / "op-a.payload").write_bytes(new_payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        adapter = LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )
        intent = publish_run_target_recovery_intent_segment(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            intent_segments=intent_segments,
            process_instance_id="host-a",
        )
        assert intent.published is True
        preconditions = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
            next_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            process_instance_id="host-a",
        )
        assert preconditions is not None
        preservation = adapter.preserve_old_target(lease.issue_mutation_permit(), preconditions)
        preserved = recovery_operations.record_operation_phase_transition(
            run_id=preconditions.run_id,
            operation_id=preconditions.operation_id,
            expected_phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            next_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
            process_instance_id="host-a",
            operation_metadata=RecoveryOperationMetadata(
                quarantine_object_id=preservation.quarantine_object_id,
            ),
        )
        assert preserved is not None
        quarantine_payload = target_root / ".mediasync" / "objects" / "quarantine" / "op-a.payload"

        outcome = restore_next_run_target_preserved_old_target(
            permit=lease.issue_mutation_permit(),
            recovery_operations=recovery_operations,
            old_target_restore_port=adapter,
            process_instance_id="host-a",
            directory_recovery_operations=directory_recovery,
            directory_mutation_preparation_port=adapter,
        )

        loaded = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
        directory_restore = directory_recovery.load_directory_recovery_operation(
            directory_recovery_id(
                run_id="run-a",
                operation_id="op-a",
                kind=DirectoryRecoveryKind.RESTORE,
            )
        )
        assert outcome.restored is True
        assert outcome.validation_codes == ()
        assert loaded is not None
        assert loaded.phase is RecoveryOperationPhase.CANCELLED
        assert loaded.last_error_code == "RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED"
        assert directory_restore is not None
        assert directory_restore.state is DirectoryRestoreState.RESTORE_CATALOG_RECORDED
        assert final.is_dir()
        assert list(final.iterdir()) == []
        assert quarantine_payload.is_dir()
        assert _event_phases(connection)[-1:] == ["CANCELLED"]
    finally:
        connection.close()


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection


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


def _record_staging_verified_operation(
    store: SqliteRecoveryOperationStore,
    *,
    content_hash: str,
    content_byte_count: int = 5,
    target_precondition_kind: RecoveryTargetPreconditionKind = RecoveryTargetPreconditionKind.ABSENT,
    expected_target_payload: bytes | None = None,
    expected_target_fingerprint_json: str | None = None,
) -> RecoveryOperation:
    operation = store.record_planned_operation(
        _operation(
            content_hash=content_hash,
            content_byte_count=content_byte_count,
            target_precondition_kind=target_precondition_kind,
            expected_target_payload=expected_target_payload,
            expected_target_fingerprint_json=expected_target_fingerprint_json,
        ),
        process_instance_id="host-a",
    )
    for next_phase in (
        RecoveryOperationPhase.SOURCE_VALIDATED,
        RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
        RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
        RecoveryOperationPhase.STAGING_ALLOCATED,
        RecoveryOperationPhase.TRANSFERRED,
        RecoveryOperationPhase.STAGING_DURABLE,
        RecoveryOperationPhase.STAGING_VERIFIED,
    ):
        updated = store.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=next_phase,
            process_instance_id="host-a",
        )
        assert updated is not None
        operation = updated
    return operation


def _operation(
    *,
    content_hash: str,
    content_byte_count: int = 5,
    target_precondition_kind: RecoveryTargetPreconditionKind = RecoveryTargetPreconditionKind.ABSENT,
    expected_target_payload: bytes | None = None,
    expected_target_fingerprint_json: str | None = None,
) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
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
            target_precondition_kind=target_precondition_kind,
            job_id="job-a",
            job_revision_id="job-rev-a",
            retention_policy="THIRTY_DAYS",
        ),
        staging_object_id="op-a",
        expected_target_fingerprint_json=_target_fingerprint_json(
            expected_target_payload=expected_target_payload,
            expected_target_fingerprint_json=expected_target_fingerprint_json,
        ),
        expected_staging_fingerprint_json=json.dumps(
            {"byte_count": content_byte_count, "content_hash": content_hash},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _target_fingerprint_json(
    *,
    expected_target_payload: bytes | None,
    expected_target_fingerprint_json: str | None,
) -> str | None:
    if expected_target_fingerprint_json is not None:
        return expected_target_fingerprint_json
    if expected_target_payload is None:
        return None
    return json.dumps(
        {
            "byte_count": len(expected_target_payload),
            "content_hash": _sha256(expected_target_payload),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _prepare_lab_target(*, target_root: Path, staging_root: Path) -> None:
    (target_root / "Pictures").mkdir(parents=True)
    (target_root / ".mediasync" / "locks").mkdir(parents=True)
    (target_root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(
            {
                "endpoint_id": "target-a",
                "owner_installation_id": "owner-a",
                "ownership_epoch": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (target_root / ".mediasync_test_root").write_text(
        json.dumps({"run_id": "run-a"}),
        encoding="utf-8",
    )
    staging_root.mkdir()


def _lease(lock_path: Path) -> LocalEndpointLease:
    return LocalEndpointLease(
        lease_id="lease-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        resource_key="endpoint:target-a",
        lock_path=lock_path,
        _lock_handle=_FakeHandle(lock_path),
    )


class _FakeHandle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.closed = False

    def is_alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


def _event_phases(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT to_phase
            FROM recovery_events
            WHERE run_id = 'run-a'
            ORDER BY run_sequence
            """
        ).fetchall()
    ]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _empty_directory_fingerprint_json() -> str:
    return json.dumps(
        {
            "entry_count": 0,
            "kind": "DIRECTORY_EMPTY",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
