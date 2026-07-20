from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.snapshot_health import (
    empty_snapshot_health_preview_state,
    snapshot_health_preview_from_responses,
)


def test_empty_snapshot_health_preview_state_is_read_only_placeholder() -> None:
    state = empty_snapshot_health_preview_state()

    assert state.snapshot_id is None
    assert state.title == "Snapshot health"
    assert state.summary_label == "No source snapshot to inspect."
    assert state.read_model_available is False
    assert state.rows == ()


def test_snapshot_health_preview_prioritizes_blocking_issues_then_coverage() -> None:
    state = snapshot_health_preview_from_responses(
        snapshot_id="source-snapshot-a",
        blocking_issues_response=IpcResponse.accepted(
            {
                "snapshot_issues": {
                    "snapshot_id": "source-snapshot-a",
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": True,
                    "blocking_only": True,
                    "next_cursor": None,
                    "issues": [
                        {
                            "issue_id": 1,
                            "relative_path": "Archive",
                            "issue_type": "UNREADABLE_DIRECTORY",
                            "blocks_destructive_actions": True,
                            "error_code": "ERROR_ACCESS_DENIED",
                            "sanitized_message": "access denied",
                        }
                    ],
                }
            }
        ),
        coverage_response=IpcResponse.accepted(
            {
                "snapshot_coverage": {
                    "snapshot_id": "source-snapshot-a",
                    "limit": 2,
                    "has_more": True,
                    "read_model_available": True,
                    "coverage_states": ["VOLATILE"],
                    "next_cursor": None,
                    "coverage": [
                        {
                            "relative_path": "Videos",
                            "comparison_key": "videos",
                            "coverage_state": "VOLATILE",
                            "case_mode": "CASE_INSENSITIVE",
                            "case_mode_evidence": "probe-ok",
                            "case_context_hash": "a" * 64,
                            "case_probe_error": None,
                        }
                    ],
                }
            }
        ),
    )

    assert state.snapshot_id == "source-snapshot-a"
    assert state.summary_label == "1 blocking issue in source-snapshot-a. More snapshot rows exist."
    assert state.has_more_health_rows is True
    assert [row.display_line for row in state.rows] == [
        "Blocking issue: Archive · UNREADABLE_DIRECTORY",
        "Coverage warning: Videos · VOLATILE",
    ]


def test_snapshot_health_preview_handles_clear_snapshot() -> None:
    state = snapshot_health_preview_from_responses(
        snapshot_id="source-snapshot-a",
        blocking_issues_response=IpcResponse.accepted(
            {
                "snapshot_issues": {
                    "snapshot_id": "source-snapshot-a",
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": True,
                    "blocking_only": True,
                    "next_cursor": None,
                    "issues": [],
                }
            }
        ),
        coverage_response=IpcResponse.accepted(
            {
                "snapshot_coverage": {
                    "snapshot_id": "source-snapshot-a",
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": True,
                    "coverage_states": [],
                    "next_cursor": None,
                    "coverage": [],
                }
            }
        ),
    )

    assert state.summary_label == "No blocking snapshot issues in source-snapshot-a."
    assert state.rows == ()


def test_snapshot_health_preview_handles_unavailable_read_model() -> None:
    state = snapshot_health_preview_from_responses(
        snapshot_id="source-snapshot-a",
        blocking_issues_response=IpcResponse.accepted(
            {
                "snapshot_issues": {
                    "snapshot_id": "source-snapshot-a",
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": False,
                    "blocking_only": True,
                    "next_cursor": None,
                    "issues": [],
                }
            }
        ),
        coverage_response=IpcResponse.accepted(
            {
                "snapshot_coverage": {
                    "snapshot_id": "source-snapshot-a",
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": True,
                    "coverage_states": [],
                    "next_cursor": None,
                    "coverage": [],
                }
            }
        ),
    )

    assert state.summary_label == "Snapshot health read model is not available."
    assert state.rows == ()
