from __future__ import annotations

import pytest

from mediasync_home.application.trigger_occurrences import (
    TriggerDeliveryContext,
    TriggerKind,
    TriggerOccurrencePayloadError,
    build_enqueue_trigger_occurrence_payload,
    parse_enqueue_trigger_occurrence_command,
    payload_hash,
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


def test_enqueue_trigger_occurrence_requires_delivery_id_to_match_idempotency_key() -> None:
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
