from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.command_receipts import CommandReceipt, CommandReceiptState
from mediasync_home.application.outbox import (
    OutboxMessageState,
    OutboxViolation,
    claimed_message,
    command_effect_outbox_message,
    delivered_message,
)


def test_command_effect_outbox_message_is_stable_for_succeeded_receipt() -> None:
    receipt = _succeeded_receipt()

    message = command_effect_outbox_message(receipt)
    repeated = command_effect_outbox_message(receipt)

    assert message == repeated
    assert message.message_id == "command-effect:idempotency-a"
    assert message.idempotency_key == "command-effect:idempotency-a"
    assert message.message_type == "COMMAND_EFFECT_ACCEPTED"
    assert message.aggregate_type == "run"
    assert message.aggregate_id == "run-a"
    assert len(message.payload_hash) == 64
    assert '"result_entity_id":"run-a"' in message.payload_json


def test_command_effect_outbox_message_requires_succeeded_receipt_with_result() -> None:
    with pytest.raises(OutboxViolation, match="OUTBOX_COMMAND_EFFECT_REQUIRES_SUCCEEDED_RECEIPT"):
        command_effect_outbox_message(replace(_succeeded_receipt(), state=CommandReceiptState.ACCEPTED))
    with pytest.raises(OutboxViolation, match="OUTBOX_COMMAND_EFFECT_REQUIRES_RESULT_ENTITY"):
        command_effect_outbox_message(replace(_succeeded_receipt(), result_entity_id=None))


def test_claim_and_deliver_helpers_preserve_fencing_shape() -> None:
    message = command_effect_outbox_message(_succeeded_receipt())

    claimed = claimed_message(message, owner_instance_id="host-a", claim_token="claim-a")
    delivered = delivered_message(claimed, terminal_effect_hash="a" * 64)

    assert claimed.state is OutboxMessageState.CLAIMED
    assert claimed.claim_owner_instance_id == "host-a"
    assert claimed.claim_generation == 1
    assert claimed.claim_token == "claim-a"
    assert claimed.attempt_count == 1
    assert delivered.state is OutboxMessageState.DELIVERED
    assert delivered.terminal_effect_hash == "a" * 64


def test_delivery_requires_claimed_message() -> None:
    message = command_effect_outbox_message(_succeeded_receipt())

    with pytest.raises(OutboxViolation, match="OUTBOX_DELIVERY_REQUIRES_CLAIMED_MESSAGE"):
        delivered_message(message, terminal_effect_hash="a" * 64)


def _succeeded_receipt() -> CommandReceipt:
    return CommandReceipt(
        request_id="request-a",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key="idempotency-a",
        command_name="START_RUN",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
        state=CommandReceiptState.SUCCEEDED,
        result_entity_type="run",
        result_entity_id="run-a",
    )
