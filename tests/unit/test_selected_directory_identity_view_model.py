from __future__ import annotations

from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.selected_directory_identity import (
    selected_directory_identity_from_response,
)


def test_selected_directory_identity_view_model_counts_confirmed_and_unknown_targets() -> (
    None
):
    state = selected_directory_identity_from_response(
        IpcResponse.accepted(
            {
                "selected_directory_identities": {
                    "items": [
                        _item(0, "a" * 64),
                        _item(1, "a" * 64),
                        _item(2, "b" * 64),
                        _item(3, None, status="UNAVAILABLE"),
                    ],
                    "relationships": [
                        _relationship(0, 1, "SAME_PHYSICAL_DEVICE", False)
                    ],
                }
            }
        ),
        expected_count=4,
    )

    assert state.read_model_available is True
    assert state.blocking is False
    assert state.same_physical_device is True
    assert state.confirmed_target_device_count == 2
    assert state.unknown_target_count == 1


def test_selected_directory_identity_view_model_exposes_blocking_alias() -> None:
    state = selected_directory_identity_from_response(
        IpcResponse.accepted(
            {
                "selected_directory_identities": {
                    "items": [_item(0, "a" * 64), _item(1, "a" * 64)],
                    "relationships": [_relationship(0, 1, "SAME_ROOT_ALIAS", True)],
                }
            }
        ),
        expected_count=2,
    )

    assert state.blocking is True


def test_selected_directory_identity_view_model_fails_closed_on_malformed_shape() -> (
    None
):
    state = selected_directory_identity_from_response(
        IpcResponse.accepted(
            {
                "selected_directory_identities": {
                    "items": [_item(1, "a" * 64)],
                    "relationships": [],
                }
            }
        ),
        expected_count=1,
    )

    assert state.read_model_available is False


def _item(
    ordinal: int,
    independent_device_id: str | None,
    *,
    status: str = "READY",
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "status": status,
        "independent_device_id": independent_device_id,
        "storage_identity_trust": "CONFIRMED",
        "validation_code": None,
    }


def _relationship(
    left: int,
    right: int,
    kind: str,
    blocking: bool,
) -> dict[str, object]:
    return {
        "left_ordinal": left,
        "right_ordinal": right,
        "kind": kind,
        "blocking": blocking,
    }
