from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.run_progress import (
    empty_run_progress_state,
    run_progress_from_response,
)


def test_run_progress_view_model_parses_snapshot_and_retains_unchanged_state() -> None:
    response = IpcResponse.accepted(
        {
            "run_progress": {
                "run_id": "run-a",
                "read_model_available": True,
                "run_found": True,
                "changed": True,
                "snapshot": {
                    "run_id": "run-a",
                    "job_id": "job-a",
                    "state": "EXECUTING",
                    "terminal": False,
                    "sequence_no": 7,
                    "planned_operations": 3,
                    "completed_operations": 1,
                    "planned_bytes": 300,
                    "completed_bytes": 100,
                    "warning_count": 0,
                    "error_count": 0,
                    "active_relative_path": "photos/a.jpg",
                    "active_phase": "STAGING_ALLOCATED",
                    "active_planned_bytes": 100,
                    "active_staging_failure_count": 1,
                    "active_retry_backoff_ms": 900,
                    "active_retry_not_before_utc": "2026-07-31T00:00:00.900Z",
                    "active_last_error_code": "LOCAL_STAGING_TRANSFER_FAILED",
                    "targets": [
                        {
                            "endpoint_id": "target-a",
                            "state": "WAITING_FOR_ENDPOINT",
                            "planned_operations": 3,
                            "completed_operations": 1,
                            "planned_bytes": 300,
                            "completed_bytes": 100,
                            "warning_count": 0,
                            "error_count": 0,
                            "endpoint_wait_attempts": 2,
                            "endpoint_wait_total_backoff_ms": 14_250,
                            "endpoint_retry_backoff_ms": 9_500,
                            "endpoint_retry_not_before_utc": (
                                "2026-07-31T00:00:09.500Z"
                            ),
                            "endpoint_wait_reason_code": "NETWORK_INTERRUPTED",
                            "endpoint_wait_started_utc": "2026-07-31T00:00:00.000Z",
                        },
                        {
                            "endpoint_id": "target-b",
                            "state": "SUCCEEDED",
                            "planned_operations": 1,
                            "completed_operations": 1,
                            "planned_bytes": 50,
                            "completed_bytes": 50,
                            "warning_count": 0,
                            "error_count": 0,
                        },
                        {
                            "endpoint_id": "target-c",
                            "state": "SUCCEEDED_WITH_WARNINGS",
                            "planned_operations": 1,
                            "completed_operations": 1,
                            "planned_bytes": 50,
                            "completed_bytes": 50,
                            "warning_count": 1,
                            "error_count": 0,
                        },
                    ],
                },
            }
        }
    )

    state = run_progress_from_response(response)
    unchanged = run_progress_from_response(
        IpcResponse.accepted(
            {
                "run_progress": {
                    "run_id": "run-a",
                    "read_model_available": True,
                    "run_found": True,
                    "changed": False,
                    "snapshot": None,
                }
            }
        ),
        previous=state,
    )

    assert state.active is True
    assert state.sequence_no == 7
    assert state.completed_operations == 1
    assert state.active_staging_failure_count == 1
    assert state.active_retry_backoff_ms == 900
    assert state.active_retry_not_before_utc == "2026-07-31T00:00:00.900Z"
    assert state.active_last_error_code == "LOCAL_STAGING_TRANSFER_FAILED"
    assert state.targets[0].endpoint_id == "target-a"
    assert state.targets[0].endpoint_wait_attempts == 2
    assert state.targets[0].endpoint_wait_total_backoff_ms == 14_250
    assert state.targets[0].endpoint_retry_backoff_ms == 9_500
    assert (
        state.targets[0].endpoint_wait_reason_code == "NETWORK_INTERRUPTED"
    )
    assert state.target_count == 3
    assert state.completed_target_count == 2
    assert unchanged == state


def test_run_progress_view_model_fails_closed_for_missing_run() -> None:
    state = run_progress_from_response(
        IpcResponse.accepted(
            {
                "run_progress": {
                    "run_id": "missing",
                    "read_model_available": True,
                    "run_found": False,
                    "changed": False,
                    "snapshot": None,
                }
            }
        ),
        previous=empty_run_progress_state(),
    )

    assert state.run_id == "missing"
    assert state.run_found is False
    assert state.active is False
    assert state.target_count == 0
    assert state.completed_target_count == 0
