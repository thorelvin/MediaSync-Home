from __future__ import annotations

from mediasync_home.application.command_receipts import CommandReceiptState
from mediasync_home.application.recovery_operations import RecoveryOperationPhase
from mediasync_home.generated import contract_types
from tools import build_contract_types


def test_generated_contract_outputs_match_checked_in_files() -> None:
    outputs = build_contract_types.build_outputs()

    assert build_contract_types.check_outputs(outputs) == ()
    assert len(contract_types.CONTRACT_SOURCE_SHA256) == 64


def test_runtime_command_and_recovery_types_are_generated_contract_types() -> None:
    assert CommandReceiptState is contract_types.CommandReceiptState
    assert RecoveryOperationPhase is contract_types.RecoveryOperationPhase
    assert tuple(state.value for state in CommandReceiptState) == (
        "RECEIVED",
        "VALIDATED",
        "EFFECT_PREPARED",
        "ACCEPTED",
        "RUNNING",
        "SUCCEEDED",
        "REJECTED",
        "FAILED",
        "CANCELLED",
    )
    assert tuple(phase.value for phase in RecoveryOperationPhase) == (
        "PLANNED",
        "SOURCE_VALIDATED",
        "SOURCE_STABILITY_BOUND",
        "TARGET_PRECONDITION_VALIDATED",
        "STAGING_ALLOCATED",
        "TRANSFERRED",
        "STAGING_DURABLE",
        "STAGING_VERIFIED",
        "COMMIT_INTENT_RECORDED",
        "COMMIT_PRECONDITIONS_REVALIDATED",
        "OLD_TARGET_PRESERVED",
        "FILESYSTEM_APPLIED",
        "FINAL_DURABLE",
        "FINAL_VERIFIED",
        "CATALOG_RECORDED",
        "CLEANED",
        "SKIPPED",
        "CONFLICT",
        "DEFERRED",
        "FAILED_RETRYABLE",
        "FAILED_BLOCKED",
        "CANCELLED",
        "ROLLBACK_REQUIRED",
        "USER_DECISION_REQUIRED",
    )


def test_generated_reason_code_inventory_has_typed_metadata() -> None:
    assert len(contract_types.ReasonCode) == 24
    assert contract_types.REASON_CODE_METADATA[
        contract_types.ReasonCode.DATABASE_FULL
    ] == ("DATABASE", "BLOCKED")
    assert contract_types.REASON_CODE_METADATA[
        contract_types.ReasonCode.SNAPSHOT_NAMED_STREAM_PRESENT
    ] == ("CAPABILITY", "RESTRICTED")
