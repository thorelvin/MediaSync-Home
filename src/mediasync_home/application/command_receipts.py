from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol


class CommandReceiptState(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    EFFECT_PREPARED = "EFFECT_PREPARED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_COMMAND_RECEIPT_STATES = {
    CommandReceiptState.SUCCEEDED,
    CommandReceiptState.REJECTED,
    CommandReceiptState.FAILED,
    CommandReceiptState.CANCELLED,
}

COMMAND_RECEIPT_TRANSITIONS = {
    CommandReceiptState.RECEIVED: (CommandReceiptState.VALIDATED, CommandReceiptState.REJECTED),
    CommandReceiptState.VALIDATED: (
        CommandReceiptState.EFFECT_PREPARED,
        CommandReceiptState.REJECTED,
    ),
    CommandReceiptState.EFFECT_PREPARED: (
        CommandReceiptState.ACCEPTED,
        CommandReceiptState.FAILED,
    ),
    CommandReceiptState.ACCEPTED: (
        CommandReceiptState.RUNNING,
        CommandReceiptState.SUCCEEDED,
        CommandReceiptState.FAILED,
        CommandReceiptState.CANCELLED,
    ),
    CommandReceiptState.RUNNING: (
        CommandReceiptState.SUCCEEDED,
        CommandReceiptState.FAILED,
        CommandReceiptState.CANCELLED,
    ),
}


class CommandReceiptConflict(ValueError):
    pass


class CommandReceiptTransitionViolation(ValueError):
    pass


@dataclass(frozen=True)
class CommandReceipt:
    request_id: str
    client_instance_id: str
    principal_fingerprint: str
    idempotency_key: str
    command_name: str
    payload_hash: str
    protocol_version: int
    schema_version: int
    state: CommandReceiptState = CommandReceiptState.RECEIVED
    expected_entity_revision: int | None = None
    payload_hash_scope: str = "PAYLOAD_ONLY"
    payload_canonicalization_algorithm: str = "JCS-RFC8785"
    payload_hash_algorithm: str = "BLAKE3-256"
    result_entity_type: str | None = None
    result_entity_id: str | None = None
    rejection_reason: str | None = None


class CommandReceiptStore(Protocol):
    def record_received(self, receipt: CommandReceipt) -> CommandReceipt: ...

    def load_command_receipt(self, idempotency_key: str) -> CommandReceipt | None: ...

    def update_command_receipt(self, receipt: CommandReceipt) -> None: ...


def ensure_idempotency_compatible(
    existing: CommandReceipt,
    incoming: CommandReceipt,
) -> CommandReceipt:
    compared_fields = (
        "client_instance_id",
        "principal_fingerprint",
        "command_name",
        "expected_entity_revision",
        "payload_hash_scope",
        "payload_canonicalization_algorithm",
        "payload_hash_algorithm",
        "payload_hash",
        "protocol_version",
        "schema_version",
    )
    for field_name in compared_fields:
        if getattr(existing, field_name) != getattr(incoming, field_name):
            raise CommandReceiptConflict(f"COMMAND_IDEMPOTENCY_CONFLICT:{field_name}")
    return existing


def transition_command_receipt(
    receipt: CommandReceipt,
    next_state: CommandReceiptState,
    *,
    result_entity_type: str | None = None,
    result_entity_id: str | None = None,
    rejection_reason: str | None = None,
) -> CommandReceipt:
    if receipt.state is next_state:
        return receipt
    allowed = COMMAND_RECEIPT_TRANSITIONS.get(receipt.state, ())
    if next_state not in allowed:
        raise CommandReceiptTransitionViolation(
            f"COMMAND_RECEIPT_TRANSITION_FORBIDDEN:{receipt.state.value}->{next_state.value}"
        )
    return replace(
        receipt,
        state=next_state,
        result_entity_type=result_entity_type if result_entity_type is not None else receipt.result_entity_type,
        result_entity_id=result_entity_id if result_entity_id is not None else receipt.result_entity_id,
        rejection_reason=rejection_reason if rejection_reason is not None else receipt.rejection_reason,
    )
