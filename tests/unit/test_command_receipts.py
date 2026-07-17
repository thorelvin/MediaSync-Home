from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptConflict,
    CommandReceiptState,
    CommandReceiptTransitionViolation,
    ensure_idempotency_compatible,
    transition_command_receipt,
)


def test_idempotency_replay_accepts_same_command_identity_with_new_request_id() -> None:
    existing = _receipt()
    incoming = replace(existing, request_id="request-retry")

    assert ensure_idempotency_compatible(existing, incoming) is existing


def test_idempotency_replay_rejects_payload_hash_conflict() -> None:
    existing = _receipt()
    incoming = replace(existing, payload_hash="b" * 64)

    with pytest.raises(CommandReceiptConflict, match="COMMAND_IDEMPOTENCY_CONFLICT:payload_hash"):
        ensure_idempotency_compatible(existing, incoming)


def test_idempotency_replay_rejects_command_name_conflict() -> None:
    existing = _receipt()
    incoming = replace(existing, command_name="delete_standard_backup_job")

    with pytest.raises(CommandReceiptConflict, match="COMMAND_IDEMPOTENCY_CONFLICT:command_name"):
        ensure_idempotency_compatible(existing, incoming)


def test_command_receipt_state_machine_accepts_contract_path_to_success() -> None:
    receipt = _receipt()

    validated = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
    prepared = transition_command_receipt(validated, CommandReceiptState.EFFECT_PREPARED)
    accepted = transition_command_receipt(prepared, CommandReceiptState.ACCEPTED)
    succeeded = transition_command_receipt(
        accepted,
        CommandReceiptState.SUCCEEDED,
        result_entity_type="standard_backup_job",
        result_entity_id="job-a",
    )

    assert succeeded.state is CommandReceiptState.SUCCEEDED
    assert succeeded.result_entity_type == "standard_backup_job"
    assert succeeded.result_entity_id == "job-a"


def test_command_receipt_state_machine_rejects_forbidden_transition() -> None:
    receipt = _receipt()

    with pytest.raises(
        CommandReceiptTransitionViolation,
        match="COMMAND_RECEIPT_TRANSITION_FORBIDDEN:RECEIVED->SUCCEEDED",
    ):
        transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)


def test_command_receipt_same_state_transition_is_idempotent() -> None:
    receipt = _receipt()

    assert transition_command_receipt(receipt, CommandReceiptState.RECEIVED) is receipt


def _receipt() -> CommandReceipt:
    return CommandReceipt(
        request_id="request-a",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key="idempotency-a",
        command_name="create_standard_backup_job",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
        expected_entity_revision=7,
    )
