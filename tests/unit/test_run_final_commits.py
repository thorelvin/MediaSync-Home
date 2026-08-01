from __future__ import annotations

from dataclasses import replace
from typing import Mapping

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
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_final_commits import (
    RunTargetFinalCommitOperationStore,
    commit_next_run_target_verified_artifact,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_commit_next_run_target_verified_artifact_runs_journaled_final_commit() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    final_commit = _FakeFinalCommitPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=recovery_operations,
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.idle is False
    assert outcome.committed is True
    assert outcome.validation_codes == ()
    assert outcome.operation_id == "op-a"
    assert outcome.receipt == CommitReceipt(
        operation_id="op-a",
        final_relative_path=RelativePath("Pictures/A.jpg"),
    )
    assert operation.phase is RecoveryOperationPhase.FINAL_VERIFIED
    assert final_commit.calls == (
        (
            "lease-a",
            VerifiedStagingArtifact(
                object_id="op-a",
                relative_path=RelativePath("Pictures/A.jpg"),
                content_hash="a" * 64,
            ),
        ),
    )
    assert [transition[2] for transition in recovery_operations.transitions] == [
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ]


def test_commit_next_run_target_verified_artifact_preserves_matching_target_before_replace() -> None:
    operation = replace(
        _operation(),
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json='{"content_hash":"' + ("b" * 64) + '"}',
    )
    recovery_operations = _FakeRecoveryOperationStore((operation,))
    final_commit = _FakeFinalCommitPort()
    preservation = _FakeOldTargetPreservationPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=recovery_operations,
        final_commit_port=final_commit,
        old_target_preservation_port=preservation,
        process_instance_id="host-a",
    )

    stored = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.committed is True
    assert outcome.validation_codes == ()
    assert [transition[2] for transition in recovery_operations.transitions] == [
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ]
    assert preservation.calls == (("lease-a", RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED),)
    assert stored.version_object_id == "version-a"
    assert stored.final_durability_state == "FINAL_COMMIT_ADAPTER_COMPLETED"


def test_commit_next_run_target_verified_artifact_resumes_preserved_replacement() -> None:
    operation = _old_target_preserved_operation()
    recovery_operations = _FakeRecoveryOperationStore((operation,))
    final_commit = _FakeFinalCommitPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=recovery_operations,
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    stored = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.committed is True
    assert outcome.validation_codes == ()
    assert stored.phase is RecoveryOperationPhase.FINAL_VERIFIED
    assert stored.version_object_id == "op-a"
    assert stored.final_durability_state == "FINAL_COMMIT_ADAPTER_COMPLETED"
    assert final_commit.calls == (
        (
            "lease-a",
            VerifiedStagingArtifact(
                object_id="op-a",
                relative_path=RelativePath("Pictures/A.jpg"),
                content_hash="a" * 64,
            ),
        ),
    )
    assert [transition[2] for transition in recovery_operations.transitions] == [
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ]


def test_commit_next_run_target_verified_artifact_resumes_quarantined_directory_commit() -> None:
    operation = _directory_quarantined_operation()
    recovery_operations = _FakeRecoveryOperationStore((operation,))
    final_commit = _FakeFinalCommitPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=recovery_operations,
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    stored = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.committed is True
    assert outcome.validation_codes == ()
    assert stored.phase is RecoveryOperationPhase.FINAL_VERIFIED
    assert stored.quarantine_object_id == "op-a"
    assert final_commit.calls == (
        (
            "lease-a",
            VerifiedStagingArtifact(
                object_id="op-a",
                relative_path=RelativePath("Pictures/A.jpg"),
                content_hash="a" * 64,
            ),
        ),
    )
    assert [transition[2] for transition in recovery_operations.transitions] == [
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ]


def test_commit_next_run_target_verified_artifact_requires_preserved_old_target() -> None:
    operation = _old_target_preserved_operation(version_object_id=None)
    final_commit = _FakeFinalCommitPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((operation,)),
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    assert outcome.committed is False
    assert outcome.validation_codes == ("RUN_TARGET_FINAL_COMMIT_REQUIRES_PRESERVED_OLD_TARGET",)
    assert final_commit.calls == ()


def test_commit_next_run_target_verified_artifact_reports_idle_without_commit_intent() -> None:
    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore(()),
        final_commit_port=_FakeFinalCommitPort(),
        process_instance_id="host-a",
    )

    assert outcome.idle is True
    assert outcome.committed is False
    assert outcome.validation_codes == ()


def test_commit_next_run_target_verified_artifact_requires_staging_metadata() -> None:
    operation = replace(_operation(), expected_staging_fingerprint_json=None)
    final_commit = _FakeFinalCommitPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((operation,)),
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    assert outcome.committed is False
    assert outcome.validation_codes == ("RUN_TARGET_FINAL_COMMIT_REQUIRES_VERIFIED_STAGING_ARTIFACT",)
    assert final_commit.calls == ()


def test_commit_next_run_target_verified_artifact_rejects_permit_mismatch() -> None:
    operation = _operation(lease_id="other-lease")
    final_commit = _FakeFinalCommitPort()

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((operation,)),
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    assert outcome.committed is False
    assert outcome.validation_codes == ("RUN_TARGET_FINAL_COMMIT_PERMIT_MISMATCH",)
    assert final_commit.calls == ()


def test_commit_next_run_target_verified_artifact_reports_adapter_failure() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    final_commit = _FakeFinalCommitPort(
        failure=_FinalCommitFailure("LAB_FINAL_COMMIT_TARGET_EXISTS", "Use replace flow.")
    )

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=recovery_operations,
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.committed is False
    assert outcome.validation_codes == ("LAB_FINAL_COMMIT_TARGET_EXISTS",)
    assert outcome.next_action == "Use replace flow."
    assert operation.phase is RecoveryOperationPhase.FAILED_RETRYABLE


def test_commit_next_run_target_verified_artifact_records_user_decision_for_ambiguous_preserved_drift() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_old_target_preserved_operation(),))
    final_commit = _FakeFinalCommitPort(
        failure=_FinalCommitFailure(
            "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",
            "Refresh analysis because the final target changed after old-target preservation.",
        )
    )

    outcome = commit_next_run_target_verified_artifact(
        permit=_permit(),
        recovery_operations=recovery_operations,
        final_commit_port=final_commit,
        process_instance_id="host-a",
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.committed is False
    assert outcome.validation_codes == ("LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",)
    assert operation.phase is RecoveryOperationPhase.USER_DECISION_REQUIRED
    assert operation.last_error_code == "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE"
    assert recovery_operations.transitions == (
        (
            "op-a",
            RecoveryOperationPhase.OLD_TARGET_PRESERVED,
            RecoveryOperationPhase.USER_DECISION_REQUIRED,
        ),
    )


class _FakeRecoveryOperationStore(RunTargetFinalCommitOperationStore):
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self.operations = {
            (operation.run_id, operation.operation_id): operation for operation in operations
        }
        self.transitions: tuple[
            tuple[str, RecoveryOperationPhase, RecoveryOperationPhase],
            ...,
        ] = ()

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        raise AssertionError("final commit bridge should not plan operations")

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
        operation = self.operations.get((run_id, operation_id))
        if operation is None or operation.phase is not expected_phase:
            return None
        updated = replace(operation, phase=next_phase)
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
        self.operations[(run_id, operation_id)] = updated
        self.transitions = (*self.transitions, (operation_id, expected_phase, next_phase))
        return updated

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation:
        operation = self.operations.get((run_id, operation_id))
        if operation is None:
            raise AssertionError("operation should exist")
        return operation

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in sorted(
                self.operations.values(),
                key=lambda item: item.operation_id,
            )
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _FakeFinalCommitPort(FinalCommitPort):
    def __init__(self, *, failure: RuntimeError | None = None) -> None:
        self.calls: tuple[tuple[str, VerifiedStagingArtifact], ...] = ()
        self._failure = failure

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        self.calls = (*self.calls, (permit.lease_id, artifact))
        if self._failure is not None:
            raise self._failure
        return CommitReceipt(
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path,
        )


class _FakeOldTargetPreservationPort:
    def __init__(self) -> None:
        self.calls: tuple[tuple[str, RecoveryOperationPhase], ...] = ()

    def preserve_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetPreservationReceipt:
        self.calls = (*self.calls, (permit.lease_id, operation.phase))
        return OldTargetPreservationReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            version_object_id="version-a",
            fingerprint_json='{"content_hash":"' + ("b" * 64) + '"}',
            version_created_utc="2026-08-01T00:00:00.000Z",
            version_retention_until_utc="2026-08-31T00:00:00.000Z",
            version_manifest_hash="c" * 64,
        )


class _FinalCommitFailure(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


def _operation(*, lease_id: str = "lease-a") -> RecoveryOperation:
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
            lease_id=lease_id,
            lease_resource_key="endpoint:target-a",
            fencing_token=1,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
            job_id="job-a",
            job_revision_id="job-rev-a",
            retention_policy="THIRTY_DAYS",
        ),
        phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        staging_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        expected_staging_fingerprint_json='{"content_hash":"' + ("a" * 64) + '"}',
    )


def _old_target_preserved_operation(
    *,
    lease_id: str = "lease-a",
    version_object_id: str | None = "op-a",
) -> RecoveryOperation:
    return replace(
        _operation(lease_id=lease_id),
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json='{"content_hash":"' + ("b" * 64) + '"}',
        version_object_id=version_object_id,
    )


def _directory_quarantined_operation() -> RecoveryOperation:
    return replace(
        _operation(),
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        expected_target_fingerprint_json='{"entry_count":0,"kind":"DIRECTORY_EMPTY"}',
        quarantine_object_id="op-a",
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
