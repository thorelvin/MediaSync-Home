from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.recovery_intents import (
    MAX_INTENT_SEGMENT_BYTES,
    MAX_INTENT_SEGMENT_OPERATIONS,
    RecoveryIntentSegmentDurabilityState,
    RecoveryIntentSegmentState,
    RecoveryIntentSegmentViolation,
    durable_recovery_intent_segment,
    validate_recovery_intent_segment,
)


def test_durable_recovery_intent_segment_defaults_to_durable_states() -> None:
    segment = _segment()

    assert segment.state is RecoveryIntentSegmentState.DURABLE
    assert segment.durability_state is RecoveryIntentSegmentDurabilityState.DURABLE
    assert segment.operation_count == 2
    assert segment.byte_count == 256


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute.intent.jsonl",
        "\\absolute.intent.jsonl",
        "C:\\absolute.intent.jsonl",
        "recovery/../segment.intent.jsonl",
        "recovery//segment.intent.jsonl",
    ],
)
def test_recovery_intent_segment_rejects_unsafe_relative_paths(relative_path: str) -> None:
    with pytest.raises(
        RecoveryIntentSegmentViolation,
        match="RECOVERY_INTENT_SEGMENT_REQUIRES_RELATIVE_PATH",
    ):
        _segment(relative_path=relative_path)


@pytest.mark.parametrize(
    "mutation",
    [
        {"operation_count": 0, "code": "RECOVERY_INTENT_SEGMENT_OPERATION_COUNT_OUT_OF_RANGE"},
        {
            "operation_count": MAX_INTENT_SEGMENT_OPERATIONS + 1,
            "code": "RECOVERY_INTENT_SEGMENT_OPERATION_COUNT_OUT_OF_RANGE",
        },
        {"byte_count": -1, "code": "RECOVERY_INTENT_SEGMENT_BYTE_COUNT_OUT_OF_RANGE"},
        {
            "byte_count": MAX_INTENT_SEGMENT_BYTES + 1,
            "code": "RECOVERY_INTENT_SEGMENT_BYTE_COUNT_OUT_OF_RANGE",
        },
        {"segment_hash": "not-a-hash", "code": "RECOVERY_INTENT_SEGMENT_REQUIRES_HASH"},
        {"previous_segment_hash": "not-a-hash", "code": "RECOVERY_INTENT_SEGMENT_REQUIRES_PREVIOUS_HASH"},
    ],
)
def test_recovery_intent_segment_validates_bounds_and_hashes(mutation: dict[str, object]) -> None:
    code = str(mutation.pop("code"))
    segment = replace(_segment(), **mutation)

    with pytest.raises(RecoveryIntentSegmentViolation, match=code):
        validate_recovery_intent_segment(segment)


def _segment(**overrides: object):
    values: dict[str, object] = {
        "segment_id": "segment-a",
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "owner_installation_id": "owner-a",
        "ownership_epoch": 1,
        "lease_id": "lease-a",
        "fencing_token": 1,
        "segment_sequence": 0,
        "relative_path": "installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        "schema_version": 1,
        "operation_count": 2,
        "byte_count": 256,
        "segment_hash": "a" * 64,
        "previous_segment_hash": None,
    }
    values.update(overrides)
    return durable_recovery_intent_segment(**values)
