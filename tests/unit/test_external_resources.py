from __future__ import annotations

import pytest

from mediasync_home.application.external_resources import (
    ExternalResourceType,
    ExternalResourceViolation,
    validate_desired_external_resource_state,
    validate_external_resource_blocked,
    validate_external_resource_claim,
    validate_external_resource_completion,
)


def test_external_resource_desired_state_requires_identity_generation_and_hash() -> None:
    validate_desired_external_resource_state(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        resource_id="schedule-a",
        desired_generation=1,
        desired_hash="a" * 64,
    )

    with pytest.raises(ExternalResourceViolation, match="EXTERNAL_RESOURCE_INVALID_RESOURCE_ID"):
        validate_desired_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="../schedule",
            desired_generation=1,
            desired_hash="a" * 64,
        )
    with pytest.raises(
        ExternalResourceViolation,
        match="EXTERNAL_RESOURCE_DESIRED_GENERATION_MUST_BE_POSITIVE",
    ):
        validate_desired_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=0,
            desired_hash="a" * 64,
        )
    with pytest.raises(ExternalResourceViolation, match="EXTERNAL_RESOURCE_INVALID_DESIRED_HASH"):
        validate_desired_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            desired_hash="not-a-hash",
        )


def test_external_resource_claim_and_completion_requests_are_validated() -> None:
    validate_external_resource_claim(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        owner_instance_id="host-a",
        claim_token="claim-a",
        claim_ttl_ms=30_000,
    )
    validate_external_resource_completion(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        resource_id="schedule-a",
        desired_generation=1,
        claim_token="claim-a",
        observed_hash="a" * 64,
    )
    validate_external_resource_blocked(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        resource_id="schedule-a",
        claim_token="claim-a",
        error_code="TASK_DRIFT",
    )

    with pytest.raises(ExternalResourceViolation, match="EXTERNAL_RESOURCE_CLAIM_TTL"):
        validate_external_resource_claim(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=0,
        )
    with pytest.raises(ExternalResourceViolation, match="EXTERNAL_RESOURCE_INVALID_OBSERVED_HASH"):
        validate_external_resource_completion(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            claim_token="claim-a",
            observed_hash="not-a-hash",
        )
