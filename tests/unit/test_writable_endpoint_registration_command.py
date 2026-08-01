from __future__ import annotations

import pytest

from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationPayloadError,
    parse_register_writable_targets_command,
)


def test_parse_register_writable_targets_binds_exact_job_revision() -> None:
    command = parse_register_writable_targets_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"job_id": "job-a", "job_revision_id": "job-revision-a"},
    )

    assert command.request_id == "request-a"
    assert command.idempotency_key == "idempotency-a"
    assert command.job_id == "job-a"
    assert command.job_revision_id == "job-revision-a"


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"job_id": "job-a"},
        {"job_id": "job-a", "job_revision_id": "job-revision-a", "extra": True},
        {"job_id": "", "job_revision_id": "job-revision-a"},
        {"job_id": "job-a", "job_revision_id": ""},
    ),
)
def test_parse_register_writable_targets_rejects_ambiguous_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(WritableEndpointRegistrationPayloadError):
        parse_register_writable_targets_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload=payload,
        )
