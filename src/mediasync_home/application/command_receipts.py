from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, TypeVar

from mediasync_home.generated.contract_types import (
    COMMAND_RECEIPT_TERMINAL_STATES,
    COMMAND_RECEIPT_TRANSITIONS,
    CommandReceiptState as CommandReceiptState,
)


_T = TypeVar("_T")


TERMINAL_COMMAND_RECEIPT_STATES = COMMAND_RECEIPT_TERMINAL_STATES

EARLY_RECONCILABLE_COMMAND_RECEIPT_STATES = {
    CommandReceiptState.RECEIVED,
    CommandReceiptState.VALIDATED,
}

PENDING_EFFECT_RECONCILIATION_COMMAND_RECEIPT_STATES = {
    CommandReceiptState.EFFECT_PREPARED,
    CommandReceiptState.ACCEPTED,
    CommandReceiptState.RUNNING,
}

MAX_COMMAND_RECEIPT_STARTUP_RECONCILIATION_LIMIT = 1000
COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION = (
    "COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION"
)

class CommandReceiptConflict(ValueError):
    pass


class CommandReceiptTransitionViolation(ValueError):
    pass


class CommandReceiptReconciliationViolation(ValueError):
    pass


class CommandEffectStorageFailure(RuntimeError):
    def __init__(self, error_code: str, *, retryable: bool) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable


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


@dataclass(frozen=True)
class CommandReceiptStartupReconciliationRequest:
    reconciler_instance_id: str
    limit: int


@dataclass(frozen=True)
class CommandReceiptStartupReconciliationReport:
    reconciler_instance_id: str
    scanned: int
    rejected_idempotency_keys: tuple[str, ...]
    pending_effect_reconciliation_keys: tuple[str, ...]


class CommandReceiptStore(Protocol):
    def record_received(self, receipt: CommandReceipt) -> CommandReceipt: ...

    def load_command_receipt(self, idempotency_key: str) -> CommandReceipt | None: ...

    def update_command_receipt(self, receipt: CommandReceipt) -> None: ...


class CommandEffectTransaction(Protocol):
    def run(self, work: Callable[[], _T]) -> _T: ...


class CommandReceiptStartupReconciliationStore(Protocol):
    def reconcile_non_terminal_after_startup(
        self,
        request: CommandReceiptStartupReconciliationRequest,
    ) -> CommandReceiptStartupReconciliationReport: ...


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


def validate_command_receipt_startup_reconciliation_request(
    request: CommandReceiptStartupReconciliationRequest,
) -> None:
    if not request.reconciler_instance_id.strip():
        raise CommandReceiptReconciliationViolation(
            "COMMAND_RECEIPT_RECONCILIATION_REQUIRES_RECONCILER"
        )
    if request.limit < 1:
        raise CommandReceiptReconciliationViolation(
            "COMMAND_RECEIPT_RECONCILIATION_LIMIT_MUST_BE_POSITIVE"
        )
    if request.limit > MAX_COMMAND_RECEIPT_STARTUP_RECONCILIATION_LIMIT:
        raise CommandReceiptReconciliationViolation(
            "COMMAND_RECEIPT_RECONCILIATION_LIMIT_TOO_LARGE"
        )


def reject_early_command_receipt_after_startup(
    receipt: CommandReceipt,
    request: CommandReceiptStartupReconciliationRequest,
) -> CommandReceipt:
    validate_command_receipt_startup_reconciliation_request(request)
    if receipt.state not in EARLY_RECONCILABLE_COMMAND_RECEIPT_STATES:
        raise CommandReceiptReconciliationViolation(
            "COMMAND_RECEIPT_RECONCILIATION_REQUIRES_EARLY_STATE"
        )
    return transition_command_receipt(
        receipt,
        CommandReceiptState.REJECTED,
        rejection_reason=COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
    )


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
