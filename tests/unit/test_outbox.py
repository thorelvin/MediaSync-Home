from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.command_receipts import CommandReceipt, CommandReceiptState
from mediasync_home.application.outbox import (
    MAX_OUTBOX_STARTUP_RECONCILIATION_LIMIT,
    OutboxClaimTokenFactory,
    OutboxDeliveryOutcome,
    OutboxDeliveryPort,
    OutboxDeliveryResult,
    OutboxMessage,
    OutboxMessageState,
    OutboxStore,
    OutboxStartupReconciliationRequest,
    OutboxViolation,
    claimed_message,
    command_effect_outbox_message,
    dead_letter_message,
    delivered_message_from_tombstone,
    delivered_message,
    dispatch_one_outbox_message,
    requeued_claimed_message_after_startup,
    validate_outbox_startup_reconciliation_request,
)
from tests.support.fake_clock import FakeClock


CLAIM_STARTED_UTC = "2026-07-31T00:00:00.000Z"
CLAIM_TTL_MS = 30_000


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

    claimed = replace(
        claimed_message(
            message,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_started_utc=CLAIM_STARTED_UTC,
            claim_ttl_ms=CLAIM_TTL_MS,
        ),
        last_error_code="OLD_WARNING",
    )
    delivered = delivered_message(claimed, terminal_effect_hash="a" * 64)

    assert claimed.state is OutboxMessageState.CLAIMED
    assert claimed.claim_owner_instance_id == "host-a"
    assert claimed.claim_generation == 1
    assert claimed.claim_token == "claim-a"
    assert claimed.attempt_count == 1
    assert delivered.state is OutboxMessageState.DELIVERED
    assert delivered.terminal_effect_hash == "a" * 64
    assert delivered.last_error_code is None


def test_delivery_requires_claimed_message() -> None:
    message = command_effect_outbox_message(_succeeded_receipt())

    with pytest.raises(OutboxViolation, match="OUTBOX_DELIVERY_REQUIRES_CLAIMED_MESSAGE"):
        delivered_message(message, terminal_effect_hash="a" * 64)


def test_tombstone_replay_returns_terminal_delivered_message() -> None:
    message = command_effect_outbox_message(_succeeded_receipt())

    replay = delivered_message_from_tombstone(message, terminal_effect_hash="b" * 64)

    assert replay.state is OutboxMessageState.DELIVERED
    assert replay.terminal_effect_hash == "b" * 64
    assert replay.claim_token is None
    assert replay.last_error_code is None


def test_dead_letter_requires_claimed_message_and_error_code() -> None:
    message = command_effect_outbox_message(_succeeded_receipt())

    with pytest.raises(OutboxViolation, match="OUTBOX_DEAD_LETTER_REQUIRES_CLAIMED_MESSAGE"):
        dead_letter_message(message, error_code="PERMANENT_FAILURE")
    with pytest.raises(OutboxViolation, match="OUTBOX_DEAD_LETTER_REQUIRES_ERROR_CODE"):
        dead_letter_message(
            claimed_message(
                message,
                owner_instance_id="host-a",
                claim_token="claim-a",
                claim_started_utc=CLAIM_STARTED_UTC,
                claim_ttl_ms=CLAIM_TTL_MS,
            ),
            error_code=" ",
        )


def test_outbox_dispatcher_delivers_claimed_message() -> None:
    clock = FakeClock()
    store = _MemoryOutboxStore(command_effect_outbox_message(_succeeded_receipt()))
    delivery = _DeliveryPort(OutboxDeliveryResult(OutboxDeliveryOutcome.DELIVERED, "b" * 64))

    result = dispatch_one_outbox_message(
        store=store,
        delivery=delivery,
        owner_instance_id="host-a",
        claim_tokens=_FixedClaimTokenFactory("claim-a"),
        clock=clock,
        claim_ttl_ms=CLAIM_TTL_MS,
    )

    assert result.claimed is True
    assert result.delivered is True
    assert result.dead_lettered is False
    assert store.message is not None
    assert store.message.state is OutboxMessageState.DELIVERED
    assert store.message.claim_token == "claim-a"
    assert store.message.terminal_effect_hash == "b" * 64
    assert delivery.delivered_messages == (store.message.message_id,)


def test_outbox_dispatcher_dead_letters_permanent_failure() -> None:
    clock = FakeClock()
    store = _MemoryOutboxStore(command_effect_outbox_message(_succeeded_receipt()))
    delivery = _DeliveryPort(
        OutboxDeliveryResult(
            OutboxDeliveryOutcome.DEAD_LETTER,
            error_code="PERMANENT_FAILURE",
        )
    )

    result = dispatch_one_outbox_message(
        store=store,
        delivery=delivery,
        owner_instance_id="host-a",
        claim_tokens=_FixedClaimTokenFactory("claim-a"),
        clock=clock,
        claim_ttl_ms=CLAIM_TTL_MS,
    )

    assert result.claimed is True
    assert result.delivered is False
    assert result.dead_lettered is True
    assert store.message is not None
    assert store.message.state is OutboxMessageState.DEAD_LETTER
    assert store.message.last_error_code == "PERMANENT_FAILURE"


def test_outbox_dispatcher_returns_idle_when_no_pending_message() -> None:
    clock = FakeClock()
    store = _MemoryOutboxStore(None)
    delivery = _DeliveryPort(OutboxDeliveryResult(OutboxDeliveryOutcome.DELIVERED, "b" * 64))

    result = dispatch_one_outbox_message(
        store=store,
        delivery=delivery,
        owner_instance_id="host-a",
        claim_tokens=_FixedClaimTokenFactory("claim-a"),
        clock=clock,
        claim_ttl_ms=CLAIM_TTL_MS,
    )

    assert result.claimed is False
    assert delivery.delivered_messages == ()


@pytest.mark.parametrize(
    "jumped_utc",
    ("1999-01-01T00:00:00.000Z", "2099-01-01T00:00:00.000Z"),
)
def test_outbox_live_claim_ignores_wall_clock_jumps(jumped_utc: str) -> None:
    clock = FakeClock()
    store = _MemoryOutboxStore(command_effect_outbox_message(_succeeded_receipt()))
    delivery = _ClockChangingDeliveryPort(clock, jumped_utc=jumped_utc)

    result = dispatch_one_outbox_message(
        store=store,
        delivery=delivery,
        owner_instance_id="host-a",
        claim_tokens=_FixedClaimTokenFactory("claim-a"),
        clock=clock,
        claim_ttl_ms=CLAIM_TTL_MS,
    )

    assert result.delivered is True
    assert result.claim_expired is False
    assert store.message is not None
    assert store.message.state is OutboxMessageState.DELIVERED


def test_outbox_late_result_is_rejected_and_claim_is_requeued() -> None:
    clock = FakeClock()
    store = _MemoryOutboxStore(command_effect_outbox_message(_succeeded_receipt()))
    delivery = _ClockChangingDeliveryPort(
        clock,
        advance_monotonic_ms=CLAIM_TTL_MS,
    )

    result = dispatch_one_outbox_message(
        store=store,
        delivery=delivery,
        owner_instance_id="host-a",
        claim_tokens=_FixedClaimTokenFactory("claim-a"),
        clock=clock,
        claim_ttl_ms=CLAIM_TTL_MS,
    )

    assert result.claimed is True
    assert result.delivered is False
    assert result.claim_expired is True
    assert store.message is not None
    assert store.message.state is OutboxMessageState.PENDING
    assert store.message.claim_generation == 2
    assert store.message.claim_token is None
    assert store.message.terminal_effect_hash is None


def test_startup_reconciliation_request_requires_bounded_inactive_owner_proof() -> None:
    validate_outbox_startup_reconciliation_request(
        OutboxStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            inactive_owner_instance_ids=("host-a",),
            limit=MAX_OUTBOX_STARTUP_RECONCILIATION_LIMIT,
        )
    )

    with pytest.raises(OutboxViolation, match="OUTBOX_RECONCILIATION_REQUIRES_INACTIVE_OWNER_PROOF"):
        validate_outbox_startup_reconciliation_request(
            OutboxStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                inactive_owner_instance_ids=(),
                limit=10,
            )
        )
    with pytest.raises(OutboxViolation, match="OUTBOX_RECONCILIATION_LIMIT_TOO_LARGE"):
        validate_outbox_startup_reconciliation_request(
            OutboxStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                inactive_owner_instance_ids=("host-a",),
                limit=MAX_OUTBOX_STARTUP_RECONCILIATION_LIMIT + 1,
            )
        )
    with pytest.raises(OutboxViolation, match="OUTBOX_RECONCILIATION_CANNOT_STEAL_CURRENT_OWNER"):
        validate_outbox_startup_reconciliation_request(
            OutboxStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                inactive_owner_instance_ids=("host-b",),
                limit=10,
            )
        )


def test_startup_reconciliation_requeues_claimed_message_and_invalidates_claim() -> None:
    message = claimed_message(
        command_effect_outbox_message(_succeeded_receipt()),
        owner_instance_id="host-a",
        claim_token="claim-a",
        claim_started_utc=CLAIM_STARTED_UTC,
        claim_ttl_ms=CLAIM_TTL_MS,
    )

    requeued = requeued_claimed_message_after_startup(
        message,
        OutboxStartupReconciliationRequest(
            reconciler_instance_id="host-b",
            inactive_owner_instance_ids=("host-a",),
            limit=10,
        ),
    )

    assert requeued.state is OutboxMessageState.PENDING
    assert requeued.claim_owner_instance_id is None
    assert requeued.claim_generation == 2
    assert requeued.claim_token is None
    assert requeued.attempt_count == 1
    assert requeued.last_error_code == "OUTBOX_CLAIM_REQUEUED_AFTER_STARTUP"


class _FixedClaimTokenFactory(OutboxClaimTokenFactory):
    def __init__(self, claim_token: str) -> None:
        self._claim_token = claim_token

    def new_claim_token(self) -> str:
        return self._claim_token


class _DeliveryPort(OutboxDeliveryPort):
    def __init__(self, result: OutboxDeliveryResult) -> None:
        self._result = result
        self.delivered_messages: tuple[str, ...] = ()

    def deliver_outbox_message(self, message: OutboxMessage) -> OutboxDeliveryResult:
        self.delivered_messages = (*self.delivered_messages, message.message_id)
        return self._result


class _ClockChangingDeliveryPort(OutboxDeliveryPort):
    def __init__(
        self,
        clock: FakeClock,
        *,
        jumped_utc: str | None = None,
        advance_monotonic_ms: int = 0,
    ) -> None:
        self._clock = clock
        self._jumped_utc = jumped_utc
        self._advance_monotonic_ms = advance_monotonic_ms

    def deliver_outbox_message(self, message: OutboxMessage) -> OutboxDeliveryResult:
        if self._jumped_utc is not None:
            self._clock.set_utc(self._jumped_utc)
        self._clock.advance_monotonic_ms(self._advance_monotonic_ms)
        return OutboxDeliveryResult(
            OutboxDeliveryOutcome.DELIVERED,
            terminal_effect_hash="b" * 64,
        )


class _MemoryOutboxStore(OutboxStore):
    def __init__(self, message: OutboxMessage | None) -> None:
        self.message = message

    def enqueue_outbox_message(self, message: OutboxMessage) -> OutboxMessage:
        self.message = message
        return message

    def load_outbox_message(self, message_id: str) -> OutboxMessage | None:
        if self.message is not None and self.message.message_id == message_id:
            return self.message
        return None

    def claim_next_pending(
        self,
        *,
        owner_instance_id: str,
        claim_token: str,
        claim_started_utc: str,
        claim_ttl_ms: int,
    ) -> OutboxMessage | None:
        if self.message is None or self.message.state is not OutboxMessageState.PENDING:
            return None
        self.message = claimed_message(
            self.message,
            owner_instance_id=owner_instance_id,
            claim_token=claim_token,
            claim_started_utc=claim_started_utc,
            claim_ttl_ms=claim_ttl_ms,
        )
        return self.message

    def requeue_expired_claim(
        self,
        *,
        message_id: str,
        owner_instance_id: str,
        claim_generation: int,
        claim_token: str,
        requeued_utc: str,
    ) -> OutboxMessage:
        message = self.load_outbox_message(message_id)
        assert message is not None
        assert message.claim_owner_instance_id == owner_instance_id
        assert message.claim_generation == claim_generation
        assert message.claim_token == claim_token
        self.message = replace(
            message,
            state=OutboxMessageState.PENDING,
            claim_owner_instance_id=None,
            claim_generation=claim_generation + 1,
            claim_token=None,
            claim_started_utc=None,
            claim_ttl_ms=None,
            last_error_code="OUTBOX_CLAIM_REQUEUED_AFTER_MONOTONIC_EXPIRY",
        )
        return self.message

    def mark_delivered(
        self,
        *,
        message_id: str,
        claim_token: str,
        terminal_effect_hash: str,
    ) -> OutboxMessage:
        message = self.load_outbox_message(message_id)
        assert message is not None
        assert message.claim_token == claim_token
        self.message = delivered_message(message, terminal_effect_hash=terminal_effect_hash)
        return self.message

    def mark_dead_letter(
        self,
        *,
        message_id: str,
        claim_token: str,
        error_code: str,
    ) -> OutboxMessage:
        message = self.load_outbox_message(message_id)
        assert message is not None
        assert message.claim_token == claim_token
        self.message = dead_letter_message(message, error_code=error_code)
        return self.message


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
