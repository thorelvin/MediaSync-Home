from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from mediasync_home.application.command_receipts import CommandReceipt, CommandReceiptState


class OutboxViolation(ValueError):
    pass


class OutboxMessageState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"


class OutboxMessageType(str, Enum):
    COMMAND_EFFECT_ACCEPTED = "COMMAND_EFFECT_ACCEPTED"


@dataclass(frozen=True)
class OutboxMessage:
    message_id: str
    message_type: str
    aggregate_type: str
    aggregate_id: str
    idempotency_key: str
    payload_json: str
    payload_hash: str
    state: OutboxMessageState = OutboxMessageState.PENDING
    claim_owner_instance_id: str | None = None
    claim_generation: int = 0
    claim_token: str | None = None
    attempt_count: int = 0
    terminal_effect_hash: str | None = None
    last_error_code: str | None = None


class OutboxStore(Protocol):
    def enqueue_outbox_message(self, message: OutboxMessage) -> OutboxMessage: ...

    def load_outbox_message(self, message_id: str) -> OutboxMessage | None: ...

    def claim_next_pending(
        self,
        *,
        owner_instance_id: str,
        claim_token: str,
    ) -> OutboxMessage | None: ...

    def mark_delivered(
        self,
        *,
        message_id: str,
        claim_token: str,
        terminal_effect_hash: str,
    ) -> OutboxMessage: ...


def command_effect_outbox_message(receipt: CommandReceipt) -> OutboxMessage:
    if receipt.state is not CommandReceiptState.SUCCEEDED:
        raise OutboxViolation("OUTBOX_COMMAND_EFFECT_REQUIRES_SUCCEEDED_RECEIPT")
    if receipt.result_entity_type is None or receipt.result_entity_id is None:
        raise OutboxViolation("OUTBOX_COMMAND_EFFECT_REQUIRES_RESULT_ENTITY")

    payload = {
        "command_name": receipt.command_name,
        "idempotency_key": receipt.idempotency_key,
        "request_id": receipt.request_id,
        "result_entity_id": receipt.result_entity_id,
        "result_entity_type": receipt.result_entity_type,
    }
    payload_json = _canonical_json(payload)
    return OutboxMessage(
        message_id=f"command-effect:{receipt.idempotency_key}",
        message_type=OutboxMessageType.COMMAND_EFFECT_ACCEPTED.value,
        aggregate_type=receipt.result_entity_type,
        aggregate_id=receipt.result_entity_id,
        idempotency_key=f"command-effect:{receipt.idempotency_key}",
        payload_json=payload_json,
        payload_hash=_sha256_hex(payload_json),
    )


def claimed_message(
    message: OutboxMessage,
    *,
    owner_instance_id: str,
    claim_token: str,
) -> OutboxMessage:
    return replace(
        message,
        state=OutboxMessageState.CLAIMED,
        claim_owner_instance_id=owner_instance_id,
        claim_generation=message.claim_generation + 1,
        claim_token=claim_token,
        attempt_count=message.attempt_count + 1,
    )


def delivered_message(
    message: OutboxMessage,
    *,
    terminal_effect_hash: str,
) -> OutboxMessage:
    if message.state is not OutboxMessageState.CLAIMED:
        raise OutboxViolation("OUTBOX_DELIVERY_REQUIRES_CLAIMED_MESSAGE")
    return replace(
        message,
        state=OutboxMessageState.DELIVERED,
        terminal_effect_hash=terminal_effect_hash,
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
