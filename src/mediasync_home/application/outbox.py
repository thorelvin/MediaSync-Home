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


class OutboxDeliveryOutcome(str, Enum):
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"


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


@dataclass(frozen=True)
class OutboxDeliveryResult:
    outcome: OutboxDeliveryOutcome
    terminal_effect_hash: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class OutboxDispatchResult:
    claimed: bool
    delivered: bool
    dead_lettered: bool
    message_id: str | None = None


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

    def mark_dead_letter(
        self,
        *,
        message_id: str,
        claim_token: str,
        error_code: str,
    ) -> OutboxMessage: ...


class OutboxDeliveryPort(Protocol):
    def deliver_outbox_message(self, message: OutboxMessage) -> OutboxDeliveryResult: ...


class OutboxClaimTokenFactory(Protocol):
    def new_claim_token(self) -> str: ...


def dispatch_one_outbox_message(
    *,
    store: OutboxStore,
    delivery: OutboxDeliveryPort,
    owner_instance_id: str,
    claim_tokens: OutboxClaimTokenFactory,
) -> OutboxDispatchResult:
    claim_token = claim_tokens.new_claim_token()
    message = store.claim_next_pending(
        owner_instance_id=owner_instance_id,
        claim_token=claim_token,
    )
    if message is None:
        return OutboxDispatchResult(claimed=False, delivered=False, dead_lettered=False)

    result = delivery.deliver_outbox_message(message)
    if result.outcome is OutboxDeliveryOutcome.DELIVERED:
        if result.terminal_effect_hash is None:
            raise OutboxViolation("OUTBOX_DELIVERY_REQUIRES_TERMINAL_EFFECT_HASH")
        store.mark_delivered(
            message_id=message.message_id,
            claim_token=claim_token,
            terminal_effect_hash=result.terminal_effect_hash,
        )
        return OutboxDispatchResult(
            claimed=True,
            delivered=True,
            dead_lettered=False,
            message_id=message.message_id,
        )

    if result.error_code is None or not result.error_code.strip():
        raise OutboxViolation("OUTBOX_DEAD_LETTER_REQUIRES_ERROR_CODE")
    store.mark_dead_letter(
        message_id=message.message_id,
        claim_token=claim_token,
        error_code=result.error_code,
    )
    return OutboxDispatchResult(
        claimed=True,
        delivered=False,
        dead_lettered=True,
        message_id=message.message_id,
    )


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


def dead_letter_message(
    message: OutboxMessage,
    *,
    error_code: str,
) -> OutboxMessage:
    if message.state is not OutboxMessageState.CLAIMED:
        raise OutboxViolation("OUTBOX_DEAD_LETTER_REQUIRES_CLAIMED_MESSAGE")
    if not error_code.strip():
        raise OutboxViolation("OUTBOX_DEAD_LETTER_REQUIRES_ERROR_CODE")
    return replace(
        message,
        state=OutboxMessageState.DEAD_LETTER,
        last_error_code=error_code,
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
