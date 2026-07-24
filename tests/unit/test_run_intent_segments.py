from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from mediasync_home.application.recovery_intents import RecoveryIntentSegment, RecoveryIntentSegmentStore
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_intent_segments import (
    RunTargetIntentOperationStore,
    publish_run_target_recovery_intent_segment,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_publish_run_target_recovery_intent_segment_publishes_and_binds_operations() -> None:
    recovery_operations = _FakeRecoveryOperationStore(
        (
            _operation(operation_id="op-b", final_relative_path="Pictures/B.jpg"),
            _operation(operation_id="op-a", final_relative_path="Pictures/A.jpg"),
        )
    )
    intent_segments = _FakeIntentSegmentStore()

    outcome = publish_run_target_recovery_intent_segment(
        permit=_permit(),
        recovery_operations=recovery_operations,
        intent_segments=intent_segments,
        process_instance_id="host-a",
    )

    assert outcome.published is True
    assert outcome.validation_codes == ()
    assert outcome.segment is not None
    assert outcome.segment.segment_id == "run-a-target-0000-intent-000000"
    assert outcome.segment.relative_path == "installations/owner-a/recovery/run-a/segment-000000.intent.jsonl"
    assert outcome.segment.operation_count == 2
    assert outcome.segment.byte_count == 256
    assert len(outcome.segment.segment_hash) == 64
    assert outcome.operations_bound == 2
    assert intent_segments.published == (outcome.segment,)
    assert recovery_operations.load_operation(run_id="run-a", operation_id="op-a").phase is (
        RecoveryOperationPhase.COMMIT_INTENT_RECORDED
    )
    assert recovery_operations.load_operation(run_id="run-a", operation_id="op-b").phase is (
        RecoveryOperationPhase.COMMIT_INTENT_RECORDED
    )
    assert recovery_operations.load_operation(run_id="run-a", operation_id="op-a").intent_ordinal == 0
    assert recovery_operations.load_operation(run_id="run-a", operation_id="op-b").intent_ordinal == 1


def test_publish_run_target_recovery_intent_segment_requires_staging_verified_operations() -> None:
    outcome = publish_run_target_recovery_intent_segment(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore(()),
        intent_segments=_FakeIntentSegmentStore(),
        process_instance_id="host-a",
    )

    assert outcome.published is False
    assert outcome.validation_codes == ("RUN_TARGET_NO_STAGING_VERIFIED_OPERATIONS",)
    assert outcome.segment is None


def test_publish_run_target_recovery_intent_segment_rejects_permit_mismatch() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(lease_id="other-lease"),))
    intent_segments = _FakeIntentSegmentStore()

    outcome = publish_run_target_recovery_intent_segment(
        permit=_permit(),
        recovery_operations=recovery_operations,
        intent_segments=intent_segments,
        process_instance_id="host-a",
    )

    assert outcome.published is False
    assert outcome.validation_codes == ("RUN_TARGET_INTENT_OPERATION_PERMIT_MISMATCH",)
    assert intent_segments.published == ()


def test_publish_run_target_recovery_intent_segment_reports_publish_failure() -> None:
    intent_segments = _FakeIntentSegmentStore(failure=ValueError("RECOVERY_INTENT_SEGMENT_LEASE_MISMATCH"))

    outcome = publish_run_target_recovery_intent_segment(
        permit=_permit(),
        recovery_operations=_FakeRecoveryOperationStore((_operation(),)),
        intent_segments=intent_segments,
        process_instance_id="host-a",
    )

    assert outcome.published is False
    assert outcome.validation_codes == ("RECOVERY_INTENT_SEGMENT_LEASE_MISMATCH",)
    assert outcome.operations_bound == 0


def test_publish_run_target_recovery_intent_segment_reports_operation_phase_conflict() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),), conflict_operation_id="op-a")

    outcome = publish_run_target_recovery_intent_segment(
        permit=_permit(),
        recovery_operations=recovery_operations,
        intent_segments=_FakeIntentSegmentStore(),
        process_instance_id="host-a",
    )

    assert outcome.published is False
    assert outcome.validation_codes == ("RUN_TARGET_INTENT_OPERATION_PHASE_CONFLICT",)
    assert outcome.segment is not None
    assert outcome.operations_bound == 0


class _FakeRecoveryOperationStore(RunTargetIntentOperationStore):
    def __init__(
        self,
        operations: tuple[RecoveryOperation, ...],
        *,
        conflict_operation_id: str | None = None,
    ) -> None:
        self.operations = {
            (operation.run_id, operation.operation_id): operation for operation in operations
        }
        self.conflict_operation_id = conflict_operation_id
        self.transitions: tuple[tuple[str, RecoveryOperationPhase], ...] = ()

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        raise AssertionError("intent segment publisher should not plan operations")

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
    ) -> RecoveryOperation | None:
        if operation_id == self.conflict_operation_id:
            return None
        operation = self.operations.get((run_id, operation_id))
        if operation is None or operation.phase is not expected_phase:
            return None
        updated = replace(
            operation,
            phase=next_phase,
            intent_segment_id=intent_segment_id,
            intent_ordinal=intent_ordinal,
        )
        self.operations[(run_id, operation_id)] = updated
        self.transitions = (*self.transitions, (operation_id, next_phase))
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
            for operation in self.operations.values()
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _FakeIntentSegmentStore(RecoveryIntentSegmentStore):
    def __init__(self, *, failure: ValueError | None = None) -> None:
        self.published: tuple[RecoveryIntentSegment, ...] = ()
        self._failure = failure

    def publish_intent_segment(self, segment: RecoveryIntentSegment) -> RecoveryIntentSegment:
        if self._failure is not None:
            raise self._failure
        self.published = (*self.published, segment)
        return segment

    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None:
        return next((segment for segment in self.published if segment.segment_id == segment_id), None)


def _operation(
    *,
    operation_id: str = "op-a",
    final_relative_path: str = "Pictures/A.jpg",
    lease_id: str = "lease-a",
) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id=operation_id,
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id=lease_id,
            lease_resource_key="endpoint:target-a",
            fencing_token=1,
            final_relative_path=final_relative_path,
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        ),
        phase=RecoveryOperationPhase.STAGING_VERIFIED,
        staging_object_id=operation_id,
        expected_staging_fingerprint_json='{"byte_count":128}',
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
