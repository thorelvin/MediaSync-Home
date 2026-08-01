from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.retained_versions import (
    retained_version_page_from_response,
)


def test_retained_version_view_model_parses_protection_state() -> None:
    state = retained_version_page_from_response(
        IpcResponse.accepted(
            {
                "retained_versions": {
                    "run_id": "run-a",
                    "limit": 25,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "versions": [
                        {
                            "version_object_id": "version-a",
                            "run_id": "run-a",
                            "operation_id": "operation-a",
                            "job_id": "job-a",
                            "target_endpoint_id": "target-a",
                            "final_relative_path": "Photos/image.jpg",
                            "created_utc": "2026-08-01T00:00:00.000Z",
                            "retention_until_utc": "2026-08-31T00:00:00.000Z",
                            "state": "RETAINED",
                            "row_version": 1,
                            "restorable": True,
                            "protected_for_restore": True,
                            "restore_id": "restore-a",
                            "restore_state": "HISTORICAL_APPLIED",
                            "restore_pending": True,
                            "restore_validation_code": "RESTORE_RESUMING",
                            "hold_id": "restore:key-a",
                            "hold_reason": "RESTORE_REQUESTED",
                            "hold_created_utc": "2026-08-10T00:00:00.000Z",
                        }
                    ],
                }
            }
        )
    )

    assert state.run_id == "run-a"
    assert state.read_model_available is True
    assert state.versions[0].version_object_id == "version-a"
    assert state.versions[0].protected_for_restore is True
    assert state.versions[0].restore_id == "restore-a"
    assert state.versions[0].restore_state == "HISTORICAL_APPLIED"
    assert state.versions[0].restore_pending is True
    assert state.versions[0].restore_validation_code == "RESTORE_RESUMING"


def test_retained_version_view_model_drops_malformed_rows() -> None:
    state = retained_version_page_from_response(
        IpcResponse.accepted(
            {
                "retained_versions": {
                    "run_id": "run-a",
                    "read_model_available": True,
                    "has_more": False,
                    "versions": [{"version_object_id": "version-a"}],
                }
            }
        )
    )

    assert state.read_model_available is True
    assert state.versions == ()
