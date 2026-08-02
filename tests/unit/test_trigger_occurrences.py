from __future__ import annotations

import pytest

from mediasync_home.application.runs import RunState
from mediasync_home.application.trigger_occurrences import (
    TriggerDeliveryContext,
    TriggerKind,
    TriggerOccurrenceConflict,
    TriggerOccurrencePayloadError,
    TriggerOccurrenceState,
    build_enqueue_trigger_occurrence_payload,
    build_trigger_occurrence,
    ensure_trigger_occurrence_compatible,
    parse_enqueue_trigger_occurrence_command,
    payload_hash,
    terminal_trigger_occurrence_state_for_run,
)


DELIVERY_ID = "11111111-1111-4111-8111-111111111111"


def test_enqueue_trigger_occurrence_payload_round_trips_to_command() -> None:
    delivery = TriggerDeliveryContext(
        delivery_id=DELIVERY_ID,
        observed_start_utc="2026-07-20T12:00:00.000Z",
        trigger_kind=TriggerKind.SCHEDULED_TIME,
        task_definition_hash="b" * 64,
        task_instance_id="task-instance-a",
        scheduled_slot_utc="2026-07-20T12:00:00.000Z",
    )

    payload = build_enqueue_trigger_occurrence_payload(
        schedule_id="schedule-a",
        schedule_revision_hash="a" * 64,
        delivery=delivery,
    )
    command = parse_enqueue_trigger_occurrence_command(
        request_id=DELIVERY_ID,
        idempotency_key=DELIVERY_ID,
        payload=payload,
    )

    assert command.schedule_id == "schedule-a"
    assert command.schedule_revision_hash == "a" * 64
    assert command.delivery == delivery
    assert payload_hash(payload) == payload_hash(payload)
    assert len(payload_hash(payload)) == 64


def test_enqueue_trigger_occurrence_requires_delivery_id_to_match_idempotency_key() -> (
    None
):
    payload = build_enqueue_trigger_occurrence_payload(
        schedule_id="schedule-a",
        schedule_revision_hash="a" * 64,
        delivery=TriggerDeliveryContext(
            delivery_id=DELIVERY_ID,
            observed_start_utc="2026-07-20T12:00:00.000Z",
            trigger_kind=TriggerKind.SCHEDULED_TIME,
            task_definition_hash="b" * 64,
        ),
    )

    with pytest.raises(
        TriggerOccurrencePayloadError,
        match="ENQUEUE_TRIGGER_DELIVERY_ID_MUST_MATCH_IDEMPOTENCY_KEY",
    ):
        parse_enqueue_trigger_occurrence_command(
            request_id=DELIVERY_ID,
            idempotency_key="22222222-2222-4222-8222-222222222222",
            payload=payload,
        )


def test_enqueue_trigger_occurrence_rejects_unstable_payload_shape() -> None:
    with pytest.raises(TriggerOccurrencePayloadError, match="ENQUEUE_TRIGGER_INVALID"):
        build_enqueue_trigger_occurrence_payload(
            schedule_id="../not-a-schedule",
            schedule_revision_hash="a" * 64,
            delivery=TriggerDeliveryContext(
                delivery_id=DELIVERY_ID,
                observed_start_utc="not-utc",
                trigger_kind=TriggerKind.SCHEDULED_TIME,
                task_definition_hash="b" * 64,
            ),
        )


def test_trigger_occurrence_key_factory_coalesces_scheduled_retry_delivery() -> None:
    first = _parsed_command(
        delivery_id=DELIVERY_ID,
        scheduled_slot_utc="2026-07-20T12:00:00.000Z",
        observed_start_utc="2026-07-20T12:00:02.000Z",
    )
    second = _parsed_command(
        delivery_id="22222222-2222-4222-8222-222222222222",
        scheduled_slot_utc="2026-07-20T12:00:00.000Z",
        observed_start_utc="2026-07-20T12:00:08.000Z",
    )

    first_occurrence = build_trigger_occurrence(
        installation_id="preview-a",
        job_id="job-a",
        command=first,
    )
    second_occurrence = build_trigger_occurrence(
        installation_id="preview-a",
        job_id="job-a",
        command=second,
    )

    assert first_occurrence.deduplication_key == second_occurrence.deduplication_key
    assert first_occurrence.payload_hash == second_occurrence.payload_hash
    assert first_occurrence.first_delivery_id == DELIVERY_ID
    assert second_occurrence.first_delivery_id == "22222222-2222-4222-8222-222222222222"
    assert first_occurrence.occurrence_slot_utc == "2026-07-20T12:00:00.000Z"


def test_trigger_occurrence_key_factory_keeps_manual_deliveries_distinct() -> None:
    first = _parsed_command(
        delivery_id=DELIVERY_ID, trigger_kind=TriggerKind.MANUAL_LOCAL_PREVIEW
    )
    second = _parsed_command(
        delivery_id="22222222-2222-4222-8222-222222222222",
        trigger_kind=TriggerKind.MANUAL_LOCAL_PREVIEW,
    )

    first_occurrence = build_trigger_occurrence(
        installation_id="preview-a",
        job_id="job-a",
        command=first,
    )
    second_occurrence = build_trigger_occurrence(
        installation_id="preview-a",
        job_id="job-a",
        command=second,
    )

    assert first_occurrence.deduplication_key != second_occurrence.deduplication_key
    assert first_occurrence.payload_hash != second_occurrence.payload_hash


def test_trigger_occurrence_compatibility_rejects_same_key_payload_drift() -> None:
    command = _parsed_command(
        delivery_id=DELIVERY_ID,
        scheduled_slot_utc="2026-07-20T12:00:00.000Z",
    )
    occurrence = build_trigger_occurrence(
        installation_id="preview-a",
        job_id="job-a",
        command=command,
    )

    with pytest.raises(TriggerOccurrenceConflict, match="payload_hash"):
        ensure_trigger_occurrence_compatible(
            occurrence,
            build_trigger_occurrence(
                installation_id="preview-a",
                job_id="job-a",
                command=_parsed_command(
                    delivery_id="22222222-2222-4222-8222-222222222222",
                    scheduled_slot_utc="2026-07-20T12:00:00.000Z",
                    task_definition_hash="c" * 64,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("run_state", "expected"),
    (
        (RunState.COMPLETED, TriggerOccurrenceState.SUCCEEDED),
        (RunState.COMPLETED_WITH_WARNINGS, TriggerOccurrenceState.SUCCEEDED),
        (RunState.PARTIAL_FAILURE, TriggerOccurrenceState.FAILED),
        (RunState.FAILED, TriggerOccurrenceState.FAILED),
        (RunState.CANCELLED, TriggerOccurrenceState.CANCELLED),
        (RunState.BLOCKED_BY_SAFETY, TriggerOccurrenceState.REJECTED),
        (RunState.RECOVERY_REQUIRED, TriggerOccurrenceState.FAILED),
        (RunState.QUEUED, None),
        (RunState.EXECUTING, None),
    ),
)
def test_terminal_trigger_occurrence_state_follows_run_outcome(
    run_state: RunState,
    expected: TriggerOccurrenceState | None,
) -> None:
    assert terminal_trigger_occurrence_state_for_run(run_state) is expected


def _parsed_command(
    *,
    delivery_id: str,
    trigger_kind: TriggerKind = TriggerKind.SCHEDULED_TIME,
    observed_start_utc: str = "2026-07-20T12:00:00.000Z",
    scheduled_slot_utc: str | None = None,
    task_definition_hash: str = "b" * 64,
):
    payload = build_enqueue_trigger_occurrence_payload(
        schedule_id="schedule-a",
        schedule_revision_hash="a" * 64,
        delivery=TriggerDeliveryContext(
            delivery_id=delivery_id,
            observed_start_utc=observed_start_utc,
            trigger_kind=trigger_kind,
            task_definition_hash=task_definition_hash,
            scheduled_slot_utc=scheduled_slot_utc,
        ),
    )
    return parse_enqueue_trigger_occurrence_command(
        request_id=delivery_id,
        idempotency_key=delivery_id,
        payload=payload,
    )
