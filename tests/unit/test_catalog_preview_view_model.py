from __future__ import annotations

from mediasync_home.ipc.protocol import IpcReason, IpcResponse
from mediasync_home.presentation.view_models.catalog_preview import (
    cataloged_files_preview_from_response,
)


def test_catalog_preview_uses_empty_state_for_rejected_response() -> None:
    state = cataloged_files_preview_from_response(IpcResponse.rejected(IpcReason.INVALID_FRAME))

    assert state.title == "Cataloged files"
    assert state.summary_label == "No cataloged files to show."
    assert state.read_model_available is False
    assert state.rows == ()


def test_catalog_preview_reports_unavailable_read_model() -> None:
    state = cataloged_files_preview_from_response(
        IpcResponse.accepted(
            {
                "cataloged_files": {
                    "limit": 3,
                    "offset": 0,
                    "has_more": False,
                    "read_model_available": False,
                    "files": [],
                }
            }
        )
    )

    assert state.summary_label == "Catalog read model is not available."
    assert state.read_model_available is False


def test_catalog_preview_renders_recent_cataloged_file_rows() -> None:
    state = cataloged_files_preview_from_response(
        IpcResponse.accepted(
            {
                "cataloged_files": {
                    "limit": 3,
                    "offset": 0,
                    "has_more": True,
                    "read_model_available": True,
                    "files": [
                        {
                            "handoff_id": "final-file:run-a:operation-a",
                            "final_relative_path": "Photos/2026/a.jpg",
                            "target_endpoint_id": "target-a",
                            "content_hash": "abcdef0123456789" * 4,
                        }
                    ],
                }
            }
        )
    )

    assert state.read_model_available is True
    assert state.has_more_files is True
    assert state.summary_label == "1 cataloged file. More cataloged files exist."
    assert state.rows[0].handoff_id == "final-file:run-a:operation-a"
    assert state.rows[0].display_line == "Photos/2026/a.jpg · target-a · sha abcdef01"
