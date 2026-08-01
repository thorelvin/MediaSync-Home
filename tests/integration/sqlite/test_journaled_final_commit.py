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
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.application.journaled_commit import JournaledFinalCommitPort
from mediasync_home.application.ports import RelativePath, VerifiedStagingArtifact
from mediasync_home.application.recovery_intents import durable_recovery_intent_segment
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


def test_sqlite_journaled_final_commit_records_lab_filesystem_apply(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())
        store = SqliteRecoveryOperationStore(connection)
        _record_commit_intent_operation(store)
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        payload = b"image"
        artifact = VerifiedStagingArtifact(
            object_id="operation-a",
            relative_path=RelativePath("Photos/image.jpg"),
            content_hash=_sha256(payload),
        )
        (staging_root / "operation-a.payload").write_bytes(payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        runner = JournaledFinalCommitPort(
            recovery_operations=store,
            final_commit_port=LabNoOverwriteFinalCommitAdapter(
                target_root=target_root,
                staging_root=staging_root,
                permit_validator=lease,
            ),
            process_instance_id="host-a",
        )

        runner.commit_verified_artifact(lease.issue_mutation_permit(), artifact)

        operation = store.load_operation(run_id="run-a", operation_id="operation-a")
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.FINAL_VERIFIED
        assert (target_root / "Photos" / "image.jpg").read_bytes() == payload
        assert _event_phases(connection)[-4:] == [
            "COMMIT_PRECONDITIONS_REVALIDATED",
            "FILESYSTEM_APPLIED",
            "FINAL_DURABLE",
            "FINAL_VERIFIED",
        ]
    finally:
        connection.close()


def test_sqlite_journaled_final_commit_records_versioned_replace(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())
        store = SqliteRecoveryOperationStore(connection)
        _record_commit_intent_operation(
            store,
            operation=_replace_operation(expected_target_payload=b"old-image"),
        )
        target_root = tmp_path / "target"
        staging_root = tmp_path / "staging"
        _prepare_lab_target(target_root=target_root, staging_root=staging_root)
        (target_root / "Photos" / "image.jpg").write_bytes(b"old-image")
        payload = b"new-image"
        artifact = VerifiedStagingArtifact(
            object_id="operation-a",
            relative_path=RelativePath("Photos/image.jpg"),
            content_hash=_sha256(payload),
        )
        (staging_root / "operation-a.payload").write_bytes(payload)
        lease = _lease(target_root / ".mediasync" / "locks" / "mutation.lock")
        adapter = LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )
        runner = JournaledFinalCommitPort(
            recovery_operations=store,
            final_commit_port=adapter,
            old_target_preservation_port=adapter,
            process_instance_id="host-a",
        )

        runner.commit_verified_artifact(lease.issue_mutation_permit(), artifact)

        operation = store.load_operation(run_id="run-a", operation_id="operation-a")
        assert operation is not None
        assert operation.phase is RecoveryOperationPhase.FINAL_VERIFIED
        assert operation.version_object_id == "operation-a"
        assert operation.final_durability_state == "LOCAL_FILE_FLUSH_CONFIRMED"
        assert (target_root / "Photos" / "image.jpg").read_bytes() == payload
        assert (
            target_root / ".mediasync" / "objects" / "versions" / "operation-a.payload"
        ).read_bytes() == b"old-image"
        assert _event_phases(connection)[-5:] == [
            "COMMIT_PRECONDITIONS_REVALIDATED",
            "OLD_TARGET_PRESERVED",
            "FILESYSTEM_APPLIED",
            "FINAL_DURABLE",
            "FINAL_VERIFIED",
        ]
        durability_payload = json.loads(
            str(
                connection.execute(
                    """
                    SELECT payload_json
                    FROM recovery_events
                    WHERE run_id = ? AND operation_id = ? AND to_phase = 'FINAL_DURABLE'
                    """,
                    ("run-a", "operation-a"),
                ).fetchone()[0]
            )
        )
        assert durability_payload == {
            "durability_state": "LOCAL_FILE_FLUSH_CONFIRMED",
            "file_flush_succeeded": True,
            "write_through_move_used": False,
        }
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


def _record_commit_intent_operation(
    store: SqliteRecoveryOperationStore,
    *,
    operation: RecoveryOperation | None = None,
) -> RecoveryOperation:
    operation = store.record_planned_operation(
        _operation() if operation is None else operation,
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
    updated = store.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        process_instance_id="host-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
    )
    assert updated is not None
    return updated


def _operation() -> RecoveryOperation:
    return planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="operation-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Photos/image.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        job_id="job-a",
        job_revision_id="job-rev-a",
        retention_policy="THIRTY_DAYS",
    )


def _replace_operation(*, expected_target_payload: bytes) -> RecoveryOperation:
    return replace(
        _operation(),
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json=json.dumps(
            {
                "byte_count": len(expected_target_payload),
                "content_hash": _sha256(expected_target_payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _segment():
    return durable_recovery_intent_segment(
        segment_id="segment-a",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        fencing_token=1,
        segment_sequence=0,
        relative_path="installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        schema_version=1,
        operation_count=1,
        byte_count=256,
        segment_hash="a" * 64,
    )


def _prepare_lab_target(*, target_root: Path, staging_root: Path) -> None:
    (target_root / ".mediasync" / "locks").mkdir(parents=True)
    (target_root / "Photos").mkdir()
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
    (target_root / ".mediasync_test_root").write_text('{"run_id":"run-a"}', encoding="utf-8")
    staging_root.mkdir()


class _FakeHandle:
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True


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


def _event_phases(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT to_phase
            FROM recovery_events
            WHERE run_id = ?
            ORDER BY run_sequence
            """,
            ("run-a",),
        ).fetchall()
    ]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
