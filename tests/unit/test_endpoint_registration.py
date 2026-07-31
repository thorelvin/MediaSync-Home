from __future__ import annotations

from pathlib import Path

import pytest

from mediasync_home.application.endpoint_classification import (
    EndpointControlAreaClassification,
    EndpointControlAreaState,
    EndpointMarkerEvidence,
)
from mediasync_home.application.endpoint_registration import (
    EndpointRegistrationDecision,
    decide_endpoint_registration,
)
from mediasync_home.application.job_endpoints import (
    EndpointRegistrationState,
    JobEndpointRole,
)


@pytest.mark.parametrize(
    ("state", "role", "expected_state", "expected_reason"),
    [
        (
            EndpointControlAreaState.ABSENT,
            JobEndpointRole.SOURCE,
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_SOURCE_READ_ONLY_WITHOUT_CONTROL_AREA",
        ),
        (
            EndpointControlAreaState.ABSENT,
            JobEndpointRole.TARGET,
            EndpointRegistrationState.REGISTRATION_PENDING,
            "ENDPOINT_TARGET_REGISTRATION_REQUIRED",
        ),
        (
            EndpointControlAreaState.VALID_OWNED,
            JobEndpointRole.SOURCE,
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_SOURCE_CONTROL_AREA_VALID_READ_ONLY",
        ),
        (
            EndpointControlAreaState.VALID_FOREIGN,
            JobEndpointRole.SOURCE,
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_SOURCE_CONTROL_AREA_VALID_READ_ONLY",
        ),
        (
            EndpointControlAreaState.VALID_READ_ONLY_NEWER_SCHEMA,
            JobEndpointRole.SOURCE,
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_SOURCE_CONTROL_AREA_VALID_READ_ONLY",
        ),
        (
            EndpointControlAreaState.VALID_OWNED,
            JobEndpointRole.TARGET,
            EndpointRegistrationState.REGISTRATION_PENDING,
            "ENDPOINT_TARGET_WRITABLE_PROBE_REQUIRED",
        ),
        (
            EndpointControlAreaState.VALID_FOREIGN,
            JobEndpointRole.TARGET,
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_TARGET_FOREIGN_READ_ONLY",
        ),
        (
            EndpointControlAreaState.VALID_READ_ONLY_NEWER_SCHEMA,
            JobEndpointRole.TARGET,
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_TARGET_NEWER_SCHEMA_READ_ONLY",
        ),
    ],
)
def test_registration_decision_maps_absent_and_valid_states(
    state: EndpointControlAreaState,
    role: JobEndpointRole,
    expected_state: EndpointRegistrationState,
    expected_reason: str,
) -> None:
    classification = _classification(
        state,
        marker=None if state is EndpointControlAreaState.ABSENT else _marker(),
    )

    decision = decide_endpoint_registration(
        role=role,
        expected_endpoint_id="11111111-1111-4111-8111-111111111111",
        classification=classification,
    )

    assert decision.state is expected_state
    assert decision.reason_code == expected_reason


@pytest.mark.parametrize(
    "state",
    [
        EndpointControlAreaState.PARTIAL_CONTROL_AREA,
        EndpointControlAreaState.UNKNOWN_EMPTY_DIRECTORY,
        EndpointControlAreaState.UNKNOWN_NONEMPTY_DIRECTORY,
        EndpointControlAreaState.CASE_ALIAS_COLLISION,
        EndpointControlAreaState.CORRUPT_MARKER,
    ],
)
@pytest.mark.parametrize("role", list(JobEndpointRole))
def test_registration_decision_blocks_every_unsafe_state(
    state: EndpointControlAreaState,
    role: JobEndpointRole,
) -> None:
    classification = _classification(state, reason_code=f"REASON_{state.value}")

    decision = decide_endpoint_registration(
        role=role,
        expected_endpoint_id="11111111-1111-4111-8111-111111111111",
        classification=classification,
    )

    assert decision.state is EndpointRegistrationState.BLOCKED
    assert decision.reason_code == f"REASON_{state.value}"


@pytest.mark.parametrize("role", list(JobEndpointRole))
def test_registration_decision_blocks_missing_or_mismatched_valid_marker(
    role: JobEndpointRole,
) -> None:
    missing = decide_endpoint_registration(
        role=role,
        expected_endpoint_id="11111111-1111-4111-8111-111111111111",
        classification=_classification(EndpointControlAreaState.VALID_OWNED),
    )
    mismatch = decide_endpoint_registration(
        role=role,
        expected_endpoint_id="22222222-2222-4222-8222-222222222222",
        classification=_classification(
            EndpointControlAreaState.VALID_OWNED,
            marker=_marker(),
        ),
    )

    assert missing.state is EndpointRegistrationState.BLOCKED
    assert missing.reason_code == "ENDPOINT_VALID_CLASSIFICATION_MARKER_MISSING"
    assert mismatch.state is EndpointRegistrationState.BLOCKED
    assert mismatch.reason_code == "ENDPOINT_MARKER_ENDPOINT_ID_MISMATCH"


def test_read_only_classification_never_grants_writable_ready() -> None:
    decisions = [
        decide_endpoint_registration(
            role=role,
            expected_endpoint_id="11111111-1111-4111-8111-111111111111",
            classification=_classification(
                state,
                marker=(
                    None if state is EndpointControlAreaState.ABSENT else _marker()
                ),
            ),
        )
        for state in EndpointControlAreaState
        for role in JobEndpointRole
    ]

    assert all(
        decision.state is not EndpointRegistrationState.WRITABLE_READY
        for decision in decisions
    )


def test_owned_target_requires_exact_persisted_writable_probe() -> None:
    classification = _classification(
        EndpointControlAreaState.VALID_OWNED,
        marker=_marker(),
    )

    pending = decide_endpoint_registration(
        role=JobEndpointRole.TARGET,
        expected_endpoint_id="11111111-1111-4111-8111-111111111111",
        classification=classification,
    )
    verified = decide_endpoint_registration(
        role=JobEndpointRole.TARGET,
        expected_endpoint_id="11111111-1111-4111-8111-111111111111",
        classification=classification,
        writable_probe_verified=True,
    )

    assert pending == EndpointRegistrationDecision(
        EndpointRegistrationState.REGISTRATION_PENDING,
        "ENDPOINT_TARGET_WRITABLE_PROBE_REQUIRED",
    )
    assert verified == EndpointRegistrationDecision(
        EndpointRegistrationState.WRITABLE_READY,
        "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED",
    )


def _classification(
    state: EndpointControlAreaState,
    *,
    reason_code: str = "CLASSIFIED",
    marker: EndpointMarkerEvidence | None = None,
) -> EndpointControlAreaClassification:
    return EndpointControlAreaClassification(
        root=Path("C:/Endpoint"),
        state=state,
        reason_codes=(reason_code,),
        marker=marker,
    )


def _marker() -> EndpointMarkerEvidence:
    return EndpointMarkerEvidence(
        control_schema_version=4,
        endpoint_id="11111111-1111-4111-8111-111111111111",
        control_area_id="22222222-2222-4222-8222-222222222222",
        owner_installation_id="33333333-3333-4333-8333-333333333333",
        ownership_epoch=1,
        root_identity_hash_algorithm="BLAKE3-256",
        root_identity_hash="a" * 64,
        marker_checksum_algorithm="BLAKE3-256",
        marker_checksum="b" * 64,
        latest_ownership_record="ownership/00000000000000000001.json",
    )
