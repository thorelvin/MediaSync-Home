from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentStore,
    durable_recovery_intent_segment,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_preserved_commit_refresh import (
    RunTargetPreservedCommitRefreshOperationStore,
    refresh_next_run_target_preserved_commit_intent_for_fresh_lease,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_refresh_preserved_commit_intent_rebinds_to_fresh_lease() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    intent_segments = _FakeIntentSegmentStore((_segment(),))

    outcome = refresh_next_run_target_preserved_commit_intent_for_fresh_lease(
        permit=_permit(lease_id="lease-b", fencing_token=2),
        recovery_operations=recovery_operations,
        intent_segments=intent_segments,
        process_instance_id="host-b",
        max_operations=10,
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    refreshed_segment = intent_segments.load_intent_segment("run-a-target-0000-intent-000001")
    assert outcome.refreshed is True
    assert outcome.validation_codes == ()
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.OLD_TARGET_PRESERVED
    assert operation.lease_id == "lease-b"
    assert operation.fencing_token == 2
    assert operation.intent_segment_id == "run-a-target-0000-intent-000001"
    assert operation.intent_ordinal == 0
    assert refreshed_segment is not None
    assert refreshed_segment.segment_sequence == 1
    assert refreshed_segment.previous_segment_hash == _segment().segment_hash
    assert refreshed_segment.lease_id == "lease-b"
    assert refreshed_segment.fencing_token == 2


def test_refresh_preserved_commit_intent_is_idle_when_operation_already_matches() -> None:
    outcome = refresh_next_run_target_preserved_commit_intent_for_fresh_lease(
        permit=_permit(lease_id="lease-b", fencing_token=2),
        recovery_operations=_FakeRecoveryOperationStore(
            (_operation(lease_id="lease-b", fencing_token=2),)
        ),
        intent_segments=_FakeIntentSegmentStore(),
        process_instance_id="host-b",
        max_operations=10,
    )

    assert outcome.idle is True
    assert outcome.refreshed is False
    assert outcome.validation_codes == ()


def test_refresh_preserved_commit_intent_rejects_mismatched_existing_segment() -> None:
    recovery_operations = _FakeRecoveryOperationStore((_operation(),))
    intent_segments = _FakeIntentSegmentStore((_segment(lease_id="other-lease"),))

    outcome = refresh_next_run_target_preserved_commit_intent_for_fresh_lease(
        permit=_permit(lease_id="lease-b", fencing_token=2),
        recovery_operations=recovery_operations,
        intent_segments=intent_segments,
        process_instance_id="host-b",
        max_operations=10,
    )

    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert outcome.refreshed is False
    assert outcome.validation_codes == ("RUN_TARGET_PRESERVED_COMMIT_REFRESH_SEGMENT_MISMATCH",)
    assert operation is not None
    assert operation.lease_id == "lease-a"


class _FakeRecoveryOperationStore(RunTargetPreservedCommitRefreshOperationStore):
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self.operations = {
            (operation.run_id, operation.operation_id): operation for operation in operations
        }

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        raise AssertionError("preserved commit refresh should not plan operations")

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
        raise AssertionError("preserved commit refresh should not transition phases")

    def record_operation_lease_rebound(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        raise AssertionError("preserved commit refresh should not use lease rebind")

    def record_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        raise AssertionError("preserved commit refresh should use the preserved refresh method")

    def record_old_target_preserved_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        operation = self.operations.get((run_id, operation_id))
        if (
            operation is None
            or operation.phase is not RecoveryOperationPhase.OLD_TARGET_PRESERVED
            or operation.lease_id != expected_lease_id
            or operation.ownership_epoch != expected_ownership_epoch
            or operation.fencing_token != expected_fencing_token
        ):
            return None
        updated = replace(
            operation,
            owner_installation_id=owner_installation_id,
            ownership_epoch=ownership_epoch,
            lease_id=lease_id,
            fencing_token=fencing_token,
            intent_segment_id=intent_segment_id,
            intent_ordinal=intent_ordinal,
        )
        self.operations[(run_id, operation_id)] = updated
        return updated

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        return self.operations.get((run_id, operation_id))

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
            for operation in sorted(self.operations.values(), key=lambda item: item.operation_id)
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _FakeIntentSegmentStore(RecoveryIntentSegmentStore):
    def __init__(self, segments: tuple[RecoveryIntentSegment, ...] = ()) -> None:
        self.segments = {segment.segment_id: segment for segment in segments}

    def publish_intent_segment(self, segment: RecoveryIntentSegment) -> RecoveryIntentSegment:
        self.segments.setdefault(segment.segment_id, segment)
        return segment

    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None:
        return self.segments.get(segment_id)

    def load_latest_intent_segment_for_run_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> RecoveryIntentSegment | None:
        matches = tuple(
            segment
            for segment in self.segments.values()
            if segment.run_id == run_id and segment.run_target_id == run_target_id
        )
        if not matches:
            return None
        return max(matches, key=lambda segment: segment.segment_sequence)


def _operation(
    *,
    lease_id: str = "lease-a",
    fencing_token: int = 1,
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
            lease_id=lease_id,
            lease_resource_key="endpoint:target-a",
            fencing_token=fencing_token,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        ),
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        staging_object_id="op-a",
        version_object_id="op-a",
        intent_segment_id="run-a-target-0000-intent-000000",
        intent_ordinal=0,
        expected_staging_fingerprint_json='{"byte_count":128,"content_hash":"' + ("a" * 64) + '"}',
        expected_target_fingerprint_json='{"byte_count":128,"content_hash":"' + ("b" * 64) + '"}',
    )


def _segment(
    *,
    lease_id: str = "lease-a",
    fencing_token: int = 1,
) -> RecoveryIntentSegment:
    return durable_recovery_intent_segment(
        segment_id="run-a-target-0000-intent-000000",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id=lease_id,
        fencing_token=fencing_token,
        segment_sequence=0,
        relative_path="installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        schema_version=1,
        operation_count=1,
        byte_count=128,
        segment_hash="a" * 64,
    )


def _permit(*, lease_id: str, fencing_token: int) -> MutationPermit:
    return _issue_mutation_permit(
        lease_id=lease_id,
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=fencing_token,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
