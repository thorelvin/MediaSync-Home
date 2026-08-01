from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Mapping

import pytest

from mediasync_home.adapters.endpoint_leases import LocalEndpointLease
from mediasync_home.adapters.final_commit import LabNoOverwriteFinalCommitAdapter
from mediasync_home.application.journaled_commit import (
    JournaledFinalCommitError,
    JournaledFinalCommitPort,
)
from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    OldTargetPreservationReceipt,
    RelativePath,
    VerifiedStagingArtifact,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.domain.capabilities import MutationPermit


def test_journaled_final_commit_records_before_and_after_filesystem_apply() -> None:
    actions: list[str] = []
    store = _FakeRecoveryOperationStore(_operation(), actions=actions)
    inner = _FakeFinalCommitPort(actions=actions)
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )
    artifact = _artifact()
    permit = _permit()

    receipt = runner.commit_verified_artifact(permit, artifact)

    assert receipt == CommitReceipt(
        operation_id="operation-a",
        final_relative_path=artifact.relative_path,
    )
    assert actions == [
        "transition:COMMIT_PRECONDITIONS_REVALIDATED",
        "commit",
        "transition:FILESYSTEM_APPLIED",
        "transition:FINAL_DURABLE",
        "transition:FINAL_VERIFIED",
    ]
    assert [transition.next_phase for transition in store.transitions] == [
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ]
    assert inner.calls == [(permit, artifact)]
    assert store.operation is not None
    assert store.operation.phase is RecoveryOperationPhase.FINAL_VERIFIED


def test_journaled_final_commit_wraps_lab_no_overwrite_adapter(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (target_root / ".mediasync" / "locks").mkdir(parents=True)
    (target_root / "Photos").mkdir()
    (target_root / ".mediasync_test_root").write_text('{"run_id":"run-a"}', encoding="utf-8")
    staging_root.mkdir()
    payload = b"image"
    artifact = VerifiedStagingArtifact(
        object_id="operation-a",
        relative_path=RelativePath("Photos/image.jpg"),
        content_hash=_sha256(payload),
    )
    (staging_root / "operation-a.payload").write_bytes(payload)
    lease = _lease(lock_path=target_root / ".mediasync" / "locks" / "mutation.lock")
    store = _FakeRecoveryOperationStore(_operation())
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=LabNoOverwriteFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        ),
        process_instance_id="host-a",
    )

    receipt = runner.commit_verified_artifact(lease.issue_mutation_permit(), artifact)

    assert receipt == CommitReceipt(
        operation_id="operation-a",
        final_relative_path=artifact.relative_path,
    )
    assert (target_root / "Photos" / "image.jpg").read_bytes() == payload
    assert store.operation is not None
    assert store.operation.phase is RecoveryOperationPhase.FINAL_VERIFIED


def test_journaled_final_commit_records_old_target_preservation_before_replace() -> None:
    actions: list[str] = []
    operation = replace(
        _operation(),
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json='{"byte_count":5,"content_hash":"' + ("b" * 64) + '"}',
    )
    store = _FakeRecoveryOperationStore(operation, actions=actions)
    preservation = _FakeOldTargetPreservationPort(actions=actions)
    inner = _FakeFinalCommitPort(actions=actions)
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        old_target_preservation_port=preservation,
        process_instance_id="host-a",
    )
    artifact = _artifact()
    permit = _permit()

    receipt = runner.commit_verified_artifact(permit, artifact)

    assert receipt == CommitReceipt(
        operation_id="operation-a",
        final_relative_path=artifact.relative_path,
    )
    assert actions == [
        "transition:COMMIT_PRECONDITIONS_REVALIDATED",
        "preserve",
        "transition:OLD_TARGET_PRESERVED",
        "commit",
        "transition:FILESYSTEM_APPLIED",
        "transition:FINAL_DURABLE",
        "transition:FINAL_VERIFIED",
    ]
    assert [transition.next_phase for transition in store.transitions] == [
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ]
    assert preservation.calls == [(permit, RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED)]
    assert store.operation is not None
    assert store.operation.version_object_id == "version-a"
    assert store.operation.final_durability_state == "FINAL_COMMIT_ADAPTER_COMPLETED"


def test_journaled_final_commit_records_directory_quarantine_before_commit() -> None:
    actions: list[str] = []
    operation = replace(
        _operation(),
        target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        expected_target_fingerprint_json='{"entry_count":0,"kind":"DIRECTORY_EMPTY"}',
    )
    store = _FakeRecoveryOperationStore(operation, actions=actions)
    preservation = _FakeOldTargetPreservationPort(actions=actions, quarantine=True)
    inner = _FakeFinalCommitPort(actions=actions)
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        old_target_preservation_port=preservation,
        process_instance_id="host-a",
    )

    receipt = runner.commit_verified_artifact(_permit(), _artifact())

    assert receipt == CommitReceipt(
        operation_id="operation-a",
        final_relative_path=RelativePath("Photos/image.jpg"),
    )
    assert actions == [
        "transition:COMMIT_PRECONDITIONS_REVALIDATED",
        "preserve",
        "transition:OLD_TARGET_PRESERVED",
        "commit",
        "transition:FILESYSTEM_APPLIED",
        "transition:FINAL_DURABLE",
        "transition:FINAL_VERIFIED",
    ]
    assert store.operation is not None
    assert store.operation.quarantine_object_id == "quarantine-a"
    assert store.operation.version_object_id is None


def test_journaled_final_commit_rejects_permit_mismatch_before_filesystem_call() -> None:
    store = _FakeRecoveryOperationStore(replace(_operation(), lease_id="lease-b"))
    inner = _FakeFinalCommitPort()
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )

    with pytest.raises(JournaledFinalCommitError) as exc_info:
        runner.commit_verified_artifact(_permit(), _artifact())

    assert exc_info.value.validation_code == "RECOVERY_COMMIT_PERMIT_MISMATCH"
    assert store.transitions == []
    assert inner.calls == []


def test_journaled_final_commit_requires_durable_commit_intent() -> None:
    store = _FakeRecoveryOperationStore(
        replace(
            _operation(),
            phase=RecoveryOperationPhase.STAGING_VERIFIED,
            intent_segment_id=None,
            intent_ordinal=None,
        )
    )
    inner = _FakeFinalCommitPort()
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )

    with pytest.raises(JournaledFinalCommitError) as exc_info:
        runner.commit_verified_artifact(_permit(), _artifact())

    assert exc_info.value.validation_code == "RECOVERY_COMMIT_REQUIRES_COMMIT_INTENT"
    assert store.transitions == []
    assert inner.calls == []


def test_journaled_final_commit_requires_preservation_port_for_matching_target() -> None:
    store = _FakeRecoveryOperationStore(
        replace(
            _operation(),
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
            expected_target_fingerprint_json='{"byte_count":5,"content_hash":"' + ("b" * 64) + '"}',
        )
    )
    inner = _FakeFinalCommitPort()
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )

    with pytest.raises(JournaledFinalCommitError) as exc_info:
        runner.commit_verified_artifact(_permit(), _artifact())

    assert exc_info.value.validation_code == "RECOVERY_COMMIT_REQUIRES_OLD_TARGET_PRESERVATION_PORT"
    assert store.transitions == []
    assert inner.calls == []


def test_journaled_final_commit_requires_quarantine_port_for_empty_directory() -> None:
    store = _FakeRecoveryOperationStore(
        replace(
            _operation(),
            target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
            expected_target_fingerprint_json='{"entry_count":0,"kind":"DIRECTORY_EMPTY"}',
        )
    )
    inner = _FakeFinalCommitPort()
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )

    with pytest.raises(JournaledFinalCommitError) as exc_info:
        runner.commit_verified_artifact(_permit(), _artifact())

    assert exc_info.value.validation_code == "RECOVERY_COMMIT_REQUIRES_DIRECTORY_QUARANTINE_PORT"
    assert store.transitions == []
    assert inner.calls == []


def test_journaled_final_commit_stops_when_precondition_transition_conflicts() -> None:
    store = _FakeRecoveryOperationStore(
        _operation(),
        conflict_on=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
    )
    inner = _FakeFinalCommitPort()
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )

    with pytest.raises(JournaledFinalCommitError) as exc_info:
        runner.commit_verified_artifact(_permit(), _artifact())

    assert exc_info.value.validation_code == "RECOVERY_COMMIT_PHASE_CONFLICT"
    assert inner.calls == []


def test_journaled_final_commit_records_retryable_failure_when_adapter_fails() -> None:
    actions: list[str] = []
    store = _FakeRecoveryOperationStore(_operation(), actions=actions)
    inner = _FakeFinalCommitPort(actions=actions, failure=_CommitFailure())
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        process_instance_id="host-a",
    )

    with pytest.raises(_CommitFailure):
        runner.commit_verified_artifact(_permit(), _artifact())

    assert actions == [
        "transition:COMMIT_PRECONDITIONS_REVALIDATED",
        "commit",
        "transition:FAILED_RETRYABLE",
    ]
    assert store.transitions[-1].next_phase is RecoveryOperationPhase.FAILED_RETRYABLE
    assert store.transitions[-1].payload == {
        "error_code": "LAB_FINAL_COMMIT_STAGING_HASH_MISMATCH",
        "error_type": "_CommitFailure",
    }


def test_journaled_final_commit_records_user_decision_for_ambiguous_preserved_drift() -> None:
    actions: list[str] = []
    operation = replace(
        _operation(),
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json='{"byte_count":5,"content_hash":"' + ("b" * 64) + '"}',
    )
    store = _FakeRecoveryOperationStore(operation, actions=actions)
    preservation = _FakeOldTargetPreservationPort(actions=actions)
    inner = _FakeFinalCommitPort(
        actions=actions,
        failure=_CommitFailure("LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE"),
    )
    runner = JournaledFinalCommitPort(
        recovery_operations=store,
        final_commit_port=inner,
        old_target_preservation_port=preservation,
        process_instance_id="host-a",
    )

    with pytest.raises(_CommitFailure):
        runner.commit_verified_artifact(_permit(), _artifact())

    assert actions == [
        "transition:COMMIT_PRECONDITIONS_REVALIDATED",
        "preserve",
        "transition:OLD_TARGET_PRESERVED",
        "commit",
        "transition:USER_DECISION_REQUIRED",
    ]
    assert store.transitions[-1].next_phase is RecoveryOperationPhase.USER_DECISION_REQUIRED
    assert store.transitions[-1].payload == {
        "error_code": "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",
        "error_type": "_CommitFailure",
    }
    assert store.operation is not None
    assert store.operation.last_error_code == "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE"


class _Transition:
    def __init__(
        self,
        *,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        payload: Mapping[str, object] | None,
    ) -> None:
        self.expected_phase = expected_phase
        self.next_phase = next_phase
        self.payload = payload


class _FakeRecoveryOperationStore(RecoveryOperationStore):
    def __init__(
        self,
        operation: RecoveryOperation | None,
        *,
        actions: list[str] | None = None,
        conflict_on: RecoveryOperationPhase | None = None,
    ) -> None:
        self.operation = operation
        self._actions = actions
        self._conflict_on = conflict_on
        self.transitions: list[_Transition] = []

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        raise AssertionError("journaled final commit should not plan operations")

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: RecoveryOperationMetadata | None = None,
    ) -> RecoveryOperation | None:
        if self._actions is not None:
            self._actions.append(f"transition:{next_phase.value}")
        if next_phase is self._conflict_on:
            return None
        if self.operation is None:
            return None
        if (
            self.operation.run_id != run_id
            or self.operation.operation_id != operation_id
            or self.operation.phase is not expected_phase
            or intent_segment_id is not None
            or intent_ordinal is not None
            or catalog_handoff_id is not None
            or process_instance_id != "host-a"
        ):
            return None
        self.transitions.append(
            _Transition(
                expected_phase=expected_phase,
                next_phase=next_phase,
                payload=payload,
            )
        )
        updated = replace(self.operation, phase=next_phase)
        if operation_metadata is not None:
            updated = replace(
                updated,
                version_object_id=operation_metadata.version_object_id
                if operation_metadata.version_object_id is not None
                else updated.version_object_id,
                quarantine_object_id=operation_metadata.quarantine_object_id
                if operation_metadata.quarantine_object_id is not None
                else updated.quarantine_object_id,
                final_durability_state=operation_metadata.final_durability_state
                if operation_metadata.final_durability_state is not None
                else updated.final_durability_state,
                last_error_code=operation_metadata.last_error_code
                if operation_metadata.last_error_code is not None
                else updated.last_error_code,
            )
        self.operation = updated
        return self.operation

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        if self.operation is None:
            return None
        if self.operation.run_id == run_id and self.operation.operation_id == operation_id:
            return self.operation
        return None


class _FakeFinalCommitPort(FinalCommitPort):
    def __init__(
        self,
        *,
        actions: list[str] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self._actions = actions
        self._failure = failure
        self.calls: list[tuple[MutationPermit, VerifiedStagingArtifact]] = []

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        if self._actions is not None:
            self._actions.append("commit")
        self.calls.append((permit, artifact))
        if self._failure is not None:
            raise self._failure
        return CommitReceipt(
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path,
        )


class _FakeOldTargetPreservationPort:
    def __init__(self, *, actions: list[str] | None = None, quarantine: bool = False) -> None:
        self._actions = actions
        self._quarantine = quarantine
        self.calls: list[tuple[MutationPermit, RecoveryOperationPhase]] = []

    def preserve_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetPreservationReceipt:
        if self._actions is not None:
            self._actions.append("preserve")
        self.calls.append((permit, operation.phase))
        if self._quarantine:
            return OldTargetPreservationReceipt(
                operation_id=operation.operation_id,
                final_relative_path=RelativePath(operation.final_relative_path),
                quarantine_object_id="quarantine-a",
                fingerprint_json='{"entry_count":0,"kind":"DIRECTORY_EMPTY"}',
                version_created_utc="2026-08-01T10:00:00.000Z",
                version_retention_until_utc="2026-08-31T10:00:00.000Z",
                version_manifest_hash="c" * 64,
            )
        return OldTargetPreservationReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            version_object_id="version-a",
            fingerprint_json='{"byte_count":5,"content_hash":"' + ("b" * 64) + '"}',
            version_created_utc="2026-08-01T10:00:00.000Z",
            version_retention_until_utc="2026-08-31T10:00:00.000Z",
            version_manifest_hash="c" * 64,
        )


class _CommitFailure(RuntimeError):
    def __init__(self, validation_code: str = "LAB_FINAL_COMMIT_STAGING_HASH_MISMATCH") -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


class _FakeHandle:
    def __init__(self, path: Path = Path("mutation.lock")) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True


def _operation() -> RecoveryOperation:
    operation = planned_recovery_operation(
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
        fencing_token=42,
        final_relative_path="Photos/image.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
    )
    return replace(
        operation,
        phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        intent_segment_id="segment-a",
        intent_ordinal=0,
        staging_object_id="operation-a",
    )


def _artifact() -> VerifiedStagingArtifact:
    return VerifiedStagingArtifact(
        object_id="operation-a",
        relative_path=RelativePath("Photos/image.jpg"),
        content_hash="a" * 64,
    )


def _permit() -> MutationPermit:
    return _lease().issue_mutation_permit()


def _lease(*, lock_path: Path = Path("mutation.lock")) -> LocalEndpointLease:
    return LocalEndpointLease(
        lease_id="lease-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=42,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        resource_key="endpoint:target-a",
        lock_path=lock_path,
        _lock_handle=_FakeHandle(lock_path),
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
