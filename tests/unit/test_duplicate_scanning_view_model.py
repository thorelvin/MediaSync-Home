from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.duplicate_scanning import (
    duplicate_group_page_from_response,
    duplicate_member_page_from_response,
    duplicate_scan_from_response,
)


def test_duplicate_scan_view_model_parses_bounded_progress() -> None:
    state = duplicate_scan_from_response(
        IpcResponse.accepted(
            {
                "duplicate_scan": {
                    "analysis_id": "analysis-a",
                    "available": True,
                    "scan": {
                        "scan_id": "scan-a",
                        "analysis_id": "analysis-a",
                        "state": "RUNNING",
                        "stage": "FULL_HASH",
                        "candidate_file_count": 8,
                        "quick_completed_count": 8,
                        "full_hash_candidate_count": 3,
                        "full_hash_completed_count": 2,
                        "issue_count": 1,
                        "reason_code": None,
                    },
                }
            }
        )
    )

    assert state.available is True
    assert state.active is True
    assert state.progress_numerator == 10
    assert state.progress_denominator == 11


def test_duplicate_scan_view_model_rejects_inconsistent_progress() -> None:
    state = duplicate_scan_from_response(
        IpcResponse.accepted(
            {
                "duplicate_scan": {
                    "analysis_id": "analysis-a",
                    "available": True,
                    "scan": {
                        "scan_id": "scan-a",
                        "analysis_id": "analysis-a",
                        "state": "RUNNING",
                        "stage": "QUICK_SIGNATURE",
                        "candidate_file_count": 1,
                        "quick_completed_count": 2,
                        "full_hash_candidate_count": 0,
                        "full_hash_completed_count": 0,
                        "issue_count": 0,
                    },
                }
            }
        )
    )

    assert state.available is True
    assert state.found is False


def test_duplicate_group_and_member_pages_require_valid_keyset_cursors() -> None:
    full_hash = "a" * 64
    groups = duplicate_group_page_from_response(
        IpcResponse.accepted(
            {
                "duplicate_groups": {
                    "analysis_id": "analysis-a",
                    "groups": [
                        {
                            "group_id": "group-a",
                            "relationship_class": "INTRA_ENDPOINT_DUPLICATE",
                            "full_hash": full_hash,
                            "size_bytes": 4096,
                            "member_count": 2,
                            "physical_object_count": 2,
                            "expected_replica_count": 0,
                            "potential_savings_bytes": 4096,
                            "review_state": "UNREVIEWED",
                            "created_utc": "2026-08-02T10:00:00Z",
                        }
                    ],
                    "has_more": True,
                    "next_cursor": {
                        "relationship_class": "INTRA_ENDPOINT_DUPLICATE",
                        "full_hash": full_hash,
                        "group_id": "group-a",
                    },
                }
            }
        )
    )
    members = duplicate_member_page_from_response(
        IpcResponse.accepted(
            {
                "duplicate_members": {
                    "group_id": "group-a",
                    "members": [
                        {
                            "group_id": "group-a",
                            "snapshot_id": "snapshot-a",
                            "endpoint_id": "source-a",
                            "file_entry_id": "file-a",
                            "relative_path": "Photos/A.jpg",
                            "member_role": "ORIGINAL",
                            "physical_object_key": "physical-a",
                        }
                    ],
                    "has_more": True,
                    "next_cursor": {
                        "relative_path": "Photos/A.jpg",
                        "snapshot_id": "snapshot-a",
                        "file_entry_id": "file-a",
                    },
                }
            }
        )
    )

    assert groups.read_model_available is True
    assert groups.has_more is True
    assert groups.groups[0].potential_savings_bytes == 4096
    assert members.read_model_available is True
    assert members.has_more is True
    assert members.members[0].relative_path == "Photos/A.jpg"


def test_duplicate_page_rejects_more_than_bounded_rows() -> None:
    page = duplicate_group_page_from_response(
        IpcResponse.accepted(
            {
                "duplicate_groups": {
                    "analysis_id": "analysis-a",
                    "groups": [{} for _ in range(201)],
                    "has_more": False,
                    "next_cursor": None,
                }
            }
        )
    )

    assert page.read_model_available is False
    assert page.groups == ()
