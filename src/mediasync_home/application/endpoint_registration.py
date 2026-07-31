from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.endpoint_classification import (
    EndpointControlAreaClassification,
    EndpointControlAreaState,
)
from mediasync_home.application.job_endpoints import (
    EndpointRegistrationState,
    JobEndpointRole,
)


@dataclass(frozen=True, slots=True)
class EndpointRegistrationDecision:
    state: EndpointRegistrationState
    reason_code: str


@dataclass(frozen=True, slots=True)
class EndpointClassificationRefreshReport:
    classified_endpoint_count: int
    failed_endpoint_count: int
    pending_binding_count: int
    read_only_ready_binding_count: int
    writable_ready_binding_count: int
    blocked_binding_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "classified_endpoint_count": self.classified_endpoint_count,
            "failed_endpoint_count": self.failed_endpoint_count,
            "pending_binding_count": self.pending_binding_count,
            "read_only_ready_binding_count": self.read_only_ready_binding_count,
            "writable_ready_binding_count": self.writable_ready_binding_count,
            "blocked_binding_count": self.blocked_binding_count,
        }


class EndpointClassificationRefresher(Protocol):
    def refresh_endpoint_classifications(
        self,
        *,
        observed_utc: str,
    ) -> EndpointClassificationRefreshReport: ...


def decide_endpoint_registration(
    *,
    role: JobEndpointRole,
    expected_endpoint_id: str,
    classification: EndpointControlAreaClassification,
    writable_probe_verified: bool = False,
) -> EndpointRegistrationDecision:
    state = classification.state
    if state is EndpointControlAreaState.ABSENT:
        if role is JobEndpointRole.SOURCE:
            return EndpointRegistrationDecision(
                EndpointRegistrationState.READ_ONLY_READY,
                "ENDPOINT_SOURCE_READ_ONLY_WITHOUT_CONTROL_AREA",
            )
        return EndpointRegistrationDecision(
            EndpointRegistrationState.REGISTRATION_PENDING,
            "ENDPOINT_TARGET_REGISTRATION_REQUIRED",
        )

    valid_states = {
        EndpointControlAreaState.VALID_OWNED,
        EndpointControlAreaState.VALID_FOREIGN,
        EndpointControlAreaState.VALID_READ_ONLY_NEWER_SCHEMA,
    }
    if state not in valid_states:
        return EndpointRegistrationDecision(
            EndpointRegistrationState.BLOCKED,
            _primary_reason_code(classification),
        )
    if classification.marker is None:
        return EndpointRegistrationDecision(
            EndpointRegistrationState.BLOCKED,
            "ENDPOINT_VALID_CLASSIFICATION_MARKER_MISSING",
        )
    if classification.marker.endpoint_id != expected_endpoint_id:
        return EndpointRegistrationDecision(
            EndpointRegistrationState.BLOCKED,
            "ENDPOINT_MARKER_ENDPOINT_ID_MISMATCH",
        )
    if role is JobEndpointRole.SOURCE:
        return EndpointRegistrationDecision(
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_SOURCE_CONTROL_AREA_VALID_READ_ONLY",
        )
    if state is EndpointControlAreaState.VALID_OWNED:
        if writable_probe_verified:
            return EndpointRegistrationDecision(
                EndpointRegistrationState.WRITABLE_READY,
                "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED",
            )
        return EndpointRegistrationDecision(
            EndpointRegistrationState.REGISTRATION_PENDING,
            "ENDPOINT_TARGET_WRITABLE_PROBE_REQUIRED",
        )
    if state is EndpointControlAreaState.VALID_FOREIGN:
        return EndpointRegistrationDecision(
            EndpointRegistrationState.READ_ONLY_READY,
            "ENDPOINT_TARGET_FOREIGN_READ_ONLY",
        )
    return EndpointRegistrationDecision(
        EndpointRegistrationState.READ_ONLY_READY,
        "ENDPOINT_TARGET_NEWER_SCHEMA_READ_ONLY",
    )


def _primary_reason_code(
    classification: EndpointControlAreaClassification,
) -> str:
    if classification.reason_codes and classification.reason_codes[0]:
        return classification.reason_codes[0]
    return "ENDPOINT_CONTROL_AREA_UNSAFE"
