from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.filter_decisions import (
    empty_filter_decision_preview_state,
    filter_decision_preview_from_response,
)


def test_filter_decision_preview_formats_exact_path_reason_and_rule() -> None:
    response = IpcResponse.accepted(
        {
            "snapshot_filter_decisions": {
                "read_model_available": True,
                "has_more": False,
                "decisions": [
                    {
                        "relative_path": "$RECYCLE.BIN",
                        "decision_state": "EXCLUDED",
                        "reason_code": "FILTER_RULE_EXCLUDED",
                        "matched_rule_id": "default-recycle-bin",
                    }
                ],
            }
        }
    )

    state = filter_decision_preview_from_response(
        snapshot_id="snapshot-a",
        response=response,
    )

    assert state.read_model_available is True
    assert state.rows[0].display_line == (
        "Excluded: $RECYCLE.BIN - Matched exclusion rule - "
        "Rule: default-recycle-bin"
    )


def test_filter_decision_preview_handles_missing_snapshot_and_unavailable_store() -> None:
    assert filter_decision_preview_from_response(
        snapshot_id=None,
        response=None,
    ) == empty_filter_decision_preview_state()

    unavailable = filter_decision_preview_from_response(
        snapshot_id="snapshot-a",
        response=IpcResponse.accepted(
            {
                "snapshot_filter_decisions": {
                    "read_model_available": False,
                }
            }
        ),
    )

    assert unavailable.summary_label == "File-selection details are not available."
