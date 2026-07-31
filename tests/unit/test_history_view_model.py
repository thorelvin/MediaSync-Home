from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.history import (
    empty_history_timeline_state,
    history_timeline_from_response,
)


def test_history_view_model_parses_activity_targets_and_duration() -> None:
    state = history_timeline_from_response(
        IpcResponse.accepted(
            {
                "history_timeline": {
                    "read_model_available": True,
                    "limit": 25,
                    "offset": 0,
                    "has_more": True,
                    "activity_filter": "ALL",
                    "job_id": None,
                    "activities": [
                        {
                            "activity_id": "run-a",
                            "activity_kind": "BACKUP",
                            "job_id": "job-a",
                            "job_revision_id": "job-rev-a",
                            "job_title": "Pictures",
                            "run_id": "run-a",
                            "analysis_id": None,
                            "plan_id": "plan-a",
                            "state": "COMPLETED",
                            "started_utc": "2026-07-20T12:00:00.000Z",
                            "finished_utc": "2026-07-20T12:01:30.000Z",
                            "planned_operations": 2,
                            "completed_operations": 2,
                            "planned_bytes": 1024,
                            "completed_bytes": 1024,
                            "warning_count": 1,
                            "error_count": 0,
                            "trigger_type": "MANUAL_LOCAL_PREVIEW",
                            "targets": [
                                {
                                    "endpoint_id": "target-a",
                                    "endpoint_revision_id": "target-rev-a",
                                    "state": "SUCCEEDED_WITH_WARNINGS",
                                    "planned_operations": 2,
                                    "completed_operations": 2,
                                    "planned_bytes": 1024,
                                    "completed_bytes": 1024,
                                    "warning_count": 1,
                                    "error_count": 0,
                                }
                            ],
                        },
                        {"activity_id": ""},
                    ],
                }
            }
        )
    )

    assert state.read_model_available is True
    assert state.has_more is True
    assert state.limit == 25
    assert state.selected_activity_id == "BACKUP:run-a"
    assert len(state.activities) == 1
    assert state.activities[0].duration_seconds == 90
    assert state.activities[0].targets[0].state == "SUCCEEDED_WITH_WARNINGS"


def test_history_view_model_distinguishes_empty_and_unavailable() -> None:
    empty = history_timeline_from_response(
        IpcResponse.accepted(
            {
                "history_timeline": {
                    "read_model_available": True,
                    "has_more": False,
                    "activity_filter": "CONTROLS",
                    "activities": [],
                }
            }
        )
    )
    unavailable = history_timeline_from_response(None)

    assert empty.read_model_available is True
    assert empty.activity_filter == "CONTROLS"
    assert empty.activities == ()
    assert unavailable == empty_history_timeline_state()
