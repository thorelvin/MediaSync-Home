from __future__ import annotations

import pytest

from mediasync_home.application.external_resources import (
    ExternalResourceRecord,
    ExternalResourceStartupReconciliationRequest,
    ExternalResourceState,
    ExternalResourceType,
    ExternalResourceViolation,
    requeued_external_resource_after_startup,
    validate_desired_external_resource_state,
    validate_external_resource_blocked,
    validate_external_resource_claim,
    validate_external_resource_completion,
    validate_external_resource_startup_reconciliation_request,
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


def test_external_resource_startup_reconciliation_requires_inactive_owner_proof() -> None:
    request = ExternalResourceStartupReconciliationRequest(
        reconciler_instance_id="host-b",
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        inactive_owner_instance_ids=("host-a",),
        limit=10,
    )

    validate_external_resource_startup_reconciliation_request(request)

    with pytest.raises(
        ExternalResourceViolation,
        match="EXTERNAL_RESOURCE_RECONCILIATION_REQUIRES_INACTIVE_OWNER_PROOF",
    ):
        validate_external_resource_startup_reconciliation_request(
            ExternalResourceStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                inactive_owner_instance_ids=(),
            )
        )
    with pytest.raises(
        ExternalResourceViolation,
        match="EXTERNAL_RESOURCE_RECONCILIATION_CANNOT_STEAL_CURRENT_OWNER",
    ):
        validate_external_resource_startup_reconciliation_request(
            ExternalResourceStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                inactive_owner_instance_ids=("host-b",),
            )
        )
    with pytest.raises(
        ExternalResourceViolation,
        match="EXTERNAL_RESOURCE_RECONCILIATION_OWNERS_MUST_BE_UNIQUE",
    ):
        validate_external_resource_startup_reconciliation_request(
            ExternalResourceStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                inactive_owner_instance_ids=("host-a", "host-a"),
            )
        )


def test_external_resource_startup_reconciliation_requeues_inactive_owner_claim() -> None:
    request = ExternalResourceStartupReconciliationRequest(
        reconciler_instance_id="host-b",
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        inactive_owner_instance_ids=("host-a",),
    )
    claimed = ExternalResourceRecord(
        resource_type=ExternalResourceType.TASK_SCHEDULER,
        resource_id="schedule-a",
        desired_generation=1,
        desired_hash="a" * 64,
        state=ExternalResourceState.CLAIMED,
        claim_owner_instance_id="host-a",
        claim_generation=3,
        claim_token="claim-a",
        claim_started_utc="2026-07-24T10:00:00.000Z",
        claim_ttl_ms=30_000,
        last_error_code="old",
        row_version=5,
    )

    requeued = requeued_external_resource_after_startup(claimed, request)

    assert requeued.state is ExternalResourceState.PENDING
    assert requeued.claim_owner_instance_id is None
    assert requeued.claim_generation == 4
    assert requeued.claim_token is None
    assert requeued.claim_started_utc is None
    assert requeued.claim_ttl_ms is None
    assert requeued.last_error_code == "EXTERNAL_RESOURCE_CLAIM_REQUEUED_AFTER_STARTUP"
    assert requeued.row_version == 6
