from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.plan_preview import (
    empty_plan_operation_preview_state,
    plan_operation_preview_from_response,
)


def test_plan_preview_view_model_renders_bounded_operation_rows() -> None:
    response = IpcResponse.accepted(
        {
            "plan_operations": {
                "plan_id": "plan-a",
                "limit": 2,
                "has_more": True,
                "read_model_available": True,
                "next_cursor": {
                    "execution_phase": 20,
                    "stable_order_key": "photos/2026",
                    "operation_id": "op-b",
                },
                "operations": [
                    {
                        "operation_id": "op-a",
                        "operation_type": "CREATE_DIRECTORY",
                        "sequence_no": 0,
                        "execution_phase": 10,
                        "stable_order_key": "photos",
                        "target_precondition_kind": "ABSENT",
                        "reason_code": "TARGET_DIRECTORY_MISSING",
                        "risk_level": "LOW",
                        "target_endpoint_id": "target-a",
                        "target_relative_path": "Photos",
                        "planned_bytes": 0,
                    },
                    {
                        "operation_id": "op-b",
                        "operation_type": "COPY_NEW",
                        "sequence_no": 1,
                        "execution_phase": 20,
                        "stable_order_key": "photos/2026",
                        "target_precondition_kind": "ABSENT",
                        "reason_code": "SOURCE_ONLY",
                        "risk_level": "LOW",
                        "target_endpoint_id": "target-b",
                        "target_relative_path": "Photos/2026/a.jpg",
                        "planned_bytes": 2048,
                    },
                ],
            }
        }
    )

    state = plan_operation_preview_from_response(response)

    assert state.plan_id == "plan-a"
    assert state.read_model_available is True
    assert state.has_more_operations is True
    assert state.summary_label == "2 operations from plan-a. More operations exist."
    assert [row.display_line for row in state.rows] == [
        "Create folder: Photos -> target-a",
        "Copy new: Photos/2026/a.jpg - 2.0 KiB -> target-b",
    ]
    assert [row.risk_label for row in state.rows] == ["Low", "Low"]
    assert [row.target_endpoint_id for row in state.rows] == ["target-a", "target-b"]


def test_plan_preview_view_model_handles_unavailable_read_model() -> None:
    response = IpcResponse.accepted(
        {
            "plan_operations": {
                "plan_id": "plan-a",
                "limit": 3,
                "has_more": False,
                "read_model_available": False,
                "next_cursor": None,
                "operations": [],
            }
        }
    )

    state = plan_operation_preview_from_response(response)

    assert state.plan_id == "plan-a"
    assert state.read_model_available is False
    assert state.summary_label == "Plan read model is not available."
    assert state.rows == ()


def test_plan_preview_view_model_handles_missing_payload() -> None:
    assert plan_operation_preview_from_response(IpcResponse.accepted({})) == empty_plan_operation_preview_state()
