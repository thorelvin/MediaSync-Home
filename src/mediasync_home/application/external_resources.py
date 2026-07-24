from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol


MAX_EXTERNAL_RESOURCE_STARTUP_RECONCILIATION_LIMIT = 100
HEX_256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ExternalResourceViolation(ValueError):
    pass


class ExternalResourceType(str, Enum):
    TASK_SCHEDULER = "task_scheduler"
    NOTIFICATION_CHANNEL = "notification_channel"
    CONTROL_MARKER = "control_marker"


class ExternalResourceState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    IN_SYNC = "IN_SYNC"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ExternalResourceRecord:
    resource_type: ExternalResourceType
    resource_id: str
    desired_generation: int
    desired_hash: str
    observed_generation: int | None = None
    observed_hash: str | None = None
    state: ExternalResourceState = ExternalResourceState.PENDING
    claim_owner_instance_id: str | None = None
    claim_generation: int = 0
    claim_token: str | None = None
    claim_started_utc: str | None = None
    claim_ttl_ms: int | None = None
    last_attempt_utc: str | None = None
    last_success_utc: str | None = None
    last_error_code: str | None = None
    attempt_count: int = 0
    row_version: int = 1


@dataclass(frozen=True)
class ExternalResourceStartupReconciliationRequest:
    reconciler_instance_id: str
    resource_type: ExternalResourceType
    inactive_owner_instance_ids: tuple[str, ...]
    limit: int = MAX_EXTERNAL_RESOURCE_STARTUP_RECONCILIATION_LIMIT


@dataclass(frozen=True)
class ExternalResourceStartupReconciliationReport:
    reconciler_instance_id: str
    resource_type: ExternalResourceType
    scanned: int
    requeued_resource_ids: tuple[str, ...]


class ExternalResourceStateStore(Protocol):
    def upsert_desired_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        desired_hash: str,
    ) -> ExternalResourceRecord: ...

    def load_external_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
    ) -> ExternalResourceRecord | None: ...

    def claim_next_pending_external_resource(
        self,
        *,
        resource_type: ExternalResourceType,
        owner_instance_id: str,
        claim_token: str,
        claim_ttl_ms: int,
    ) -> ExternalResourceRecord | None: ...

    def mark_external_resource_in_sync(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        claim_token: str,
        observed_hash: str,
    ) -> ExternalResourceRecord: ...

    def mark_external_resource_blocked(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        claim_token: str,
        error_code: str,
    ) -> ExternalResourceRecord: ...

    def requeue_claimed_after_startup(
        self,
        request: ExternalResourceStartupReconciliationRequest,
    ) -> ExternalResourceStartupReconciliationReport: ...


def validate_external_resource_identity(
    *,
    resource_type: ExternalResourceType,
    resource_id: str,
) -> None:
    if not isinstance(resource_type, ExternalResourceType):
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_INVALID_TYPE")
    _identifier(resource_id, "RESOURCE_ID")


def validate_desired_external_resource_state(
    *,
    resource_type: ExternalResourceType,
    resource_id: str,
    desired_generation: int,
    desired_hash: str,
) -> None:
    validate_external_resource_identity(resource_type=resource_type, resource_id=resource_id)
    if desired_generation < 1:
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_DESIRED_GENERATION_MUST_BE_POSITIVE")
    _hash(desired_hash, "DESIRED_HASH")


def validate_external_resource_claim(
    *,
    resource_type: ExternalResourceType,
    owner_instance_id: str,
    claim_token: str,
    claim_ttl_ms: int,
) -> None:
    if not isinstance(resource_type, ExternalResourceType):
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_INVALID_TYPE")
    _identifier(owner_instance_id, "OWNER_INSTANCE_ID")
    _identifier(claim_token, "CLAIM_TOKEN")
    if claim_ttl_ms <= 0:
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_CLAIM_TTL_MUST_BE_POSITIVE")


def validate_external_resource_completion(
    *,
    resource_type: ExternalResourceType,
    resource_id: str,
    desired_generation: int,
    claim_token: str,
    observed_hash: str,
) -> None:
    validate_external_resource_identity(resource_type=resource_type, resource_id=resource_id)
    if desired_generation < 1:
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_DESIRED_GENERATION_MUST_BE_POSITIVE")
    _identifier(claim_token, "CLAIM_TOKEN")
    _hash(observed_hash, "OBSERVED_HASH")


def validate_external_resource_blocked(
    *,
    resource_type: ExternalResourceType,
    resource_id: str,
    claim_token: str,
    error_code: str,
) -> None:
    validate_external_resource_identity(resource_type=resource_type, resource_id=resource_id)
    _identifier(claim_token, "CLAIM_TOKEN")
    _identifier(error_code, "ERROR_CODE")


def validate_external_resource_startup_reconciliation_request(
    request: ExternalResourceStartupReconciliationRequest,
) -> None:
    _identifier(request.reconciler_instance_id, "RECONCILER_INSTANCE_ID")
    if not isinstance(request.resource_type, ExternalResourceType):
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_INVALID_TYPE")
    if request.limit < 1:
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_RECONCILIATION_LIMIT_MUST_BE_POSITIVE")
    if request.limit > MAX_EXTERNAL_RESOURCE_STARTUP_RECONCILIATION_LIMIT:
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_RECONCILIATION_LIMIT_TOO_LARGE")
    if not request.inactive_owner_instance_ids:
        raise ExternalResourceViolation(
            "EXTERNAL_RESOURCE_RECONCILIATION_REQUIRES_INACTIVE_OWNER_PROOF"
        )
    owners = set()
    for owner_instance_id in request.inactive_owner_instance_ids:
        _identifier(owner_instance_id, "OWNER_INSTANCE_ID")
        if owner_instance_id == request.reconciler_instance_id:
            raise ExternalResourceViolation(
                "EXTERNAL_RESOURCE_RECONCILIATION_CANNOT_STEAL_CURRENT_OWNER"
            )
        owners.add(owner_instance_id)
    if len(owners) != len(request.inactive_owner_instance_ids):
        raise ExternalResourceViolation(
            "EXTERNAL_RESOURCE_RECONCILIATION_OWNERS_MUST_BE_UNIQUE"
        )


def requeued_external_resource_after_startup(
    record: ExternalResourceRecord,
    request: ExternalResourceStartupReconciliationRequest,
) -> ExternalResourceRecord:
    validate_external_resource_startup_reconciliation_request(request)
    if record.resource_type is not request.resource_type:
        raise ExternalResourceViolation("EXTERNAL_RESOURCE_RECONCILIATION_TYPE_MISMATCH")
    if record.state is not ExternalResourceState.CLAIMED:
        raise ExternalResourceViolation(
            "EXTERNAL_RESOURCE_RECONCILIATION_REQUIRES_CLAIMED_RESOURCE"
        )
    if record.claim_owner_instance_id not in set(request.inactive_owner_instance_ids):
        raise ExternalResourceViolation(
            "EXTERNAL_RESOURCE_RECONCILIATION_REQUIRES_INACTIVE_OWNER_PROOF"
        )
    return replace(
        record,
        state=ExternalResourceState.PENDING,
        claim_owner_instance_id=None,
        claim_generation=record.claim_generation + 1,
        claim_token=None,
        claim_started_utc=None,
        claim_ttl_ms=None,
        last_error_code="EXTERNAL_RESOURCE_CLAIM_REQUEUED_AFTER_STARTUP",
        row_version=record.row_version + 1,
    )


def _identifier(value: str, field_name: str) -> None:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ExternalResourceViolation(f"EXTERNAL_RESOURCE_INVALID_{field_name}")


def _hash(value: str, field_name: str) -> None:
    if HEX_256_PATTERN.fullmatch(value) is None:
        raise ExternalResourceViolation(f"EXTERNAL_RESOURCE_INVALID_{field_name}")
