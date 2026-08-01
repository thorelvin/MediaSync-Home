from __future__ import annotations

import pytest

from mediasync_home.application.endpoint_takeover import (
    EndpointTakeoverPayloadError,
    parse_start_controlled_endpoint_takeover_command,
)


def test_controlled_takeover_payload_binds_confirmation_and_foreign_epoch() -> None:
    command = parse_start_controlled_endpoint_takeover_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload=_payload(),
    )

    assert command.job_id == "job-a"
    assert command.job_revision_id == "revision-a"
    assert command.target_ordinal == 1
    assert command.endpoint_id == "11111111-1111-4111-8111-111111111111"
    assert command.expected_foreign_owner_installation_id == (
        "22222222-2222-4222-8222-222222222222"
    )
    assert command.expected_ownership_epoch == 7


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("explicit_confirmation", False),
        ("target_ordinal", 0),
        ("target_ordinal", 4),
        ("expected_ownership_epoch", 0),
        ("endpoint_id", "not-a-uuid"),
        ("expected_foreign_owner_installation_id", "not-a-uuid"),
    ],
)
def test_controlled_takeover_payload_rejects_unbound_or_invalid_values(
    field: str,
    value: object,
) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(EndpointTakeoverPayloadError):
        parse_start_controlled_endpoint_takeover_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload=payload,
        )


def test_controlled_takeover_payload_rejects_unknown_fields() -> None:
    payload = _payload()
    payload["force"] = True

    with pytest.raises(EndpointTakeoverPayloadError):
        parse_start_controlled_endpoint_takeover_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload=payload,
        )


def _payload() -> dict[str, object]:
    return {
        "job_id": "job-a",
        "job_revision_id": "revision-a",
        "target_ordinal": 1,
        "endpoint_id": "11111111-1111-4111-8111-111111111111",
        "expected_foreign_owner_installation_id": (
            "22222222-2222-4222-8222-222222222222"
        ),
        "expected_ownership_epoch": 7,
        "explicit_confirmation": True,
    }
