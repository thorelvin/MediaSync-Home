from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentState,
    durable_recovery_intent_segment,
)
from mediasync_home.application.run_intent_cleanup import (
    RunIntentCleanupAction,
    advance_next_run_target_intent_cleanup,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_run_intent_cleanup_requires_terminal_reconciliation_then_verified_absence() -> None:
    store = _LifecycleStore(_segment())
    cleanup = _Cleanup(False, True)

    waiting = advance_next_run_target_intent_cleanup(
        permit=_permit(),
        intent_segments=store,
        target_cleanup=cleanup,
    )
    assert waiting.action is RunIntentCleanupAction.IDLE
    assert waiting.all_cleaned is False

    store.ready = True
    actions = tuple(
        advance_next_run_target_intent_cleanup(
            permit=_permit(),
            intent_segments=store,
            target_cleanup=cleanup,
        ).action
        for _ in range(4)
    )
    assert actions == (
        RunIntentCleanupAction.RECONCILED,
        RunIntentCleanupAction.CLEANUP_ELIGIBLE,
        RunIntentCleanupAction.TARGET_REMOVED,
        RunIntentCleanupAction.CLEANED,
    )
    assert cleanup.calls == 2
    assert store.segment.state is RecoveryIntentSegmentState.CLEANED

    completed = advance_next_run_target_intent_cleanup(
        permit=_permit(),
        intent_segments=store,
        target_cleanup=cleanup,
    )
    assert completed.action is RunIntentCleanupAction.IDLE
    assert completed.all_cleaned is True


def test_run_intent_cleanup_rejects_current_permit_binding_mismatch() -> None:
    store = _LifecycleStore(_segment())
    mismatched = _permit(endpoint_revision_id="target-rev-b")

    outcome = advance_next_run_target_intent_cleanup(
        permit=mismatched,
        intent_segments=store,
        target_cleanup=_Cleanup(),
    )

    assert outcome.action is RunIntentCleanupAction.BLOCKED
    assert outcome.validation_codes == ("RUN_INTENT_CLEANUP_PERMIT_MISMATCH",)


class _LifecycleStore:
    def __init__(self, segment: RecoveryIntentSegment) -> None:
        self.segment = segment
        self.ready = False

    def load_next_intent_segment_lifecycle_candidate(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> RecoveryIntentSegment | None:
        assert run_id == self.segment.run_id
        assert run_target_id == self.segment.run_target_id
        if self.segment.state is RecoveryIntentSegmentState.CLEANED:
            return None
        return self.segment

    def intent_segment_reconciliation_ready(self, *, segment_id: str) -> bool:
        assert segment_id == self.segment.segment_id
        return self.ready

    def transition_intent_segment_state(
        self,
        *,
        segment_id: str,
        expected_state: RecoveryIntentSegmentState,
        next_state: RecoveryIntentSegmentState,
    ) -> RecoveryIntentSegment | None:
        assert segment_id == self.segment.segment_id
        if self.segment.state is not expected_state:
            return None
        self.segment = replace(self.segment, state=next_state)
        return self.segment


class _Cleanup:
    def __init__(self, *results: bool) -> None:
        self._results = iter(results)
        self.calls = 0

    def ensure_target_intent_segment_absent(
        self,
        *,
        permit: MutationPermit,
        segment: RecoveryIntentSegment,
    ) -> bool:
        assert permit.run_id == "run-a"
        assert permit.run_target_id == "run-a-target-0000"
        assert permit.endpoint_id == "target-a"
        assert permit.endpoint_revision_id == "target-rev-a"
        assert segment.state is RecoveryIntentSegmentState.CLEANUP_ELIGIBLE
        self.calls += 1
        return next(self._results)


def _segment() -> RecoveryIntentSegment:
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
        relative_path=(
            "installations/owner-a/recovery/run-a/segment-000000.intent.jsonl"
        ),
        schema_version=1,
        operation_count=1,
        byte_count=128,
        segment_hash="a" * 64,
    )


def _permit(*, endpoint_revision_id: str = "target-rev-a") -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id=endpoint_revision_id,
    )
