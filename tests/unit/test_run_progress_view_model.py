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
                    "targets": [
                        {
                            "endpoint_id": "target-a",
                            "state": "EXECUTING",
                            "planned_operations": 3,
                            "completed_operations": 1,
                            "planned_bytes": 300,
                            "completed_bytes": 100,
                            "warning_count": 0,
                            "error_count": 0,
                        }
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
    assert state.targets[0].endpoint_id == "target-a"
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
