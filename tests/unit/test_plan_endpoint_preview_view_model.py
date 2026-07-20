from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.plan_endpoints import (
    empty_plan_endpoint_preview_state,
    plan_endpoint_preview_from_response,
)


def test_empty_plan_endpoint_preview_state_is_read_only_placeholder() -> None:
    state = empty_plan_endpoint_preview_state()

    assert state.plan_id is None
    assert state.title == "Plan endpoints"
    assert state.summary_label == "No sealed plan endpoints to show."
    assert state.read_model_available is False
    assert state.rows == ()


def test_plan_endpoint_preview_from_response_renders_endpoint_snapshot_rows() -> None:
    state = plan_endpoint_preview_from_response(
        IpcResponse.accepted(
            {
                "plan_endpoints": {
                    "plan_id": "plan-a",
                    "limit": 4,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "endpoints": [
                        {
                            "endpoint_id": "source-a",
                            "endpoint_revision_id": "source-rev-a",
                            "snapshot_id": "source-snapshot-a",
                            "role": "SOURCE",
                            "target_ordinal": None,
                            "planned_operations": 0,
                            "planned_bytes": 0,
                        },
                        {
                            "endpoint_id": "target-a",
                            "endpoint_revision_id": "target-rev-a",
                            "snapshot_id": "target-snapshot-a",
                            "role": "TARGET_WRITABLE",
                            "target_ordinal": 0,
                            "planned_operations": 2,
                            "planned_bytes": 2048,
                        },
                    ],
                }
            }
        )
    )

    assert state.plan_id == "plan-a"
    assert state.summary_label == "2 endpoints from plan-a."
    assert [row.display_line for row in state.rows] == [
        "Source endpoint: source-a · snapshot source-snapshot-a",
        "Target endpoint 1: target-a · snapshot target-snapshot-a",
    ]


def test_plan_endpoint_preview_handles_unavailable_read_model() -> None:
    state = plan_endpoint_preview_from_response(
        IpcResponse.accepted(
            {
                "plan_endpoints": {
                    "plan_id": "plan-a",
                    "limit": 4,
                    "has_more": False,
                    "read_model_available": False,
                    "next_cursor": None,
                    "endpoints": [],
                }
            }
        )
    )

    assert state.plan_id == "plan-a"
    assert state.summary_label == "Plan endpoint read model is not available."
    assert state.rows == ()
