from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.operation_audit import (
    empty_operation_audit_state,
    operation_audit_from_response,
)


def test_operation_audit_view_model_parses_retry_and_terminal_outcome() -> None:
    state = operation_audit_from_response(
        IpcResponse.accepted(
            {
                "operation_audit": {
                    "run_id": "run-a",
                    "run_target_id": "target-a",
                    "operation_id": "op-a",
                    "target_relative_path": "Pictures/A.jpg",
                    "limit": 25,
                    "read_model_available": True,
                    "found": True,
                    "attempts": [
                        {
                            "attempt_number": 1,
                            "state": "FAILED",
                            "finished_utc": "2026-07-31T10:00:00.000Z",
                            "bytes_transferred": 0,
                            "transfer_state": "FAILED",
                            "assurance_level": "NONE",
                            "durability_level": "NOT_REQUESTED",
                            "error_code": "LOCAL_IO_TRANSIENT",
                        },
                        {
                            "attempt_number": 2,
                            "state": "SUCCEEDED",
                            "finished_utc": "2026-07-31T10:00:02.000Z",
                            "bytes_transferred": 128,
                            "transfer_state": "TRANSFERRED",
                            "assurance_level": "PRIMARY_STREAM_HASH_VERIFIED",
                            "durability_level": "LOCAL_FILE_FLUSH_CONFIRMED",
                            "error_code": None,
                        },
                    ],
                    "outcome": {
                        "final_state": "SUCCEEDED",
                        "completed_utc": "2026-07-31T10:00:03.000Z",
                        "bytes_transferred": 128,
                        "transfer_state": "TRANSFERRED",
                        "assurance_level": "PRIMARY_STREAM_HASH_VERIFIED",
                        "hash_evidence_kind": "CURRENT_READ_HASH",
                        "durability_level": "WRITE_THROUGH_REQUEST_CONFIRMED",
                        "error_code": None,
                    },
                }
            }
        )
    )

    assert state.read_model_available is True
    assert state.found is True
    assert state.target_relative_path == "Pictures/A.jpg"
    assert [attempt.state for attempt in state.attempts] == ["FAILED", "SUCCEEDED"]
    assert state.attempts[0].error_code == "LOCAL_IO_TRANSIENT"
    assert state.outcome is not None
    assert state.outcome.final_state == "SUCCEEDED"
    assert state.outcome.bytes_transferred == 128


def test_operation_audit_view_model_distinguishes_missing_from_unavailable() -> None:
    missing = operation_audit_from_response(
        IpcResponse.accepted(
            {
                "operation_audit": {
                    "run_id": "run-a",
                    "operation_id": "op-missing",
                    "read_model_available": True,
                    "found": False,
                }
            }
        )
    )
    unavailable = operation_audit_from_response(
        IpcResponse.accepted(
            {
                "operation_audit": {
                    "run_id": "run-a",
                    "operation_id": "op-a",
                    "read_model_available": False,
                    "found": False,
                }
            }
        )
    )

    assert missing.read_model_available is True
    assert missing.found is False
    assert unavailable.read_model_available is False
    assert unavailable.found is False


def test_operation_audit_view_model_drops_invalid_attempts_and_outcomes() -> None:
    state = operation_audit_from_response(
        IpcResponse.accepted(
            {
                "operation_audit": {
                    "run_id": "run-a",
                    "operation_id": "op-a",
                    "read_model_available": True,
                    "found": True,
                    "attempts": [
                        {
                            "attempt_number": 0,
                            "state": "FAILED",
                            "finished_utc": "bad",
                            "bytes_transferred": -1,
                        }
                    ],
                    "outcome": {
                        "final_state": "SUCCEEDED",
                        "completed_utc": "2026-07-31T10:00:00.000Z",
                    },
                }
            }
        )
    )

    assert state.attempts == ()
    assert state.outcome is None
    assert operation_audit_from_response(IpcResponse.accepted({})) == (
        empty_operation_audit_state()
    )
