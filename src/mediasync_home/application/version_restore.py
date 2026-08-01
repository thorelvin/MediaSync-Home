from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from mediasync_home.application.file_object_fingerprints import (
    FileObjectFingerprintError,
    file_object_fingerprint_from_json,
)

from mediasync_home.application.runs import (
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    LiveEndpointLease,
)
from mediasync_home.application.version_retention import RetainedVersionRecord
from mediasync_home.domain.capabilities import MutationPermit


_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class VersionRestoreState(str, Enum):
    REQUESTED = "REQUESTED"
    INTENT_RECORDED = "INTENT_RECORDED"
    CURRENT_FINAL_PRESERVED = "CURRENT_FINAL_PRESERVED"
    HISTORICAL_APPLIED = "HISTORICAL_APPLIED"
    FINAL_VERIFIED = "FINAL_VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED_BLOCKED = "FAILED_BLOCKED"


class VersionRestoreError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass(frozen=True, slots=True)
class VersionRestoreOperation:
    restore_id: str
    hold_id: str
    rollback_object_id: str
    record: RetainedVersionRecord
    expected_source_row_version: int
    created_utc: str
    rollback_retention_until_utc: str
    state: VersionRestoreState = VersionRestoreState.REQUESTED
    current_final_fingerprint_json: str | None = None
    rollback_manifest_hash: str | None = None
    lease_id: str | None = None
    fencing_token: int | None = None
    completed_utc: str | None = None
    last_validation_code: str | None = None
    row_version: int = 1


@dataclass(frozen=True, slots=True)
class VersionRestoreInspectionReceipt:
    historical_fingerprint_json: str
    current_final_fingerprint_json: str
    already_current: bool


@dataclass(frozen=True, slots=True)
class VersionRestoreRollbackReceipt:
    rollback_object_id: str
    current_final_fingerprint_json: str
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class VersionRestoreApplyReceipt:
    historical_fingerprint_json: str


@dataclass(frozen=True, slots=True)
class VersionRestoreApplyOutcome:
    idle: bool
    completed: bool
    restore_id: str | None
    version_object_id: str | None
    state: VersionRestoreState | None
    validation_codes: tuple[str, ...]
    next_action: str


class VersionRestoreExecutionStore(Protocol):
    def load_next_version_restore_operation(self) -> VersionRestoreOperation | None: ...

    def record_version_restore_intent(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        current_final_fingerprint_json: str,
        event_utc: str,
    ) -> VersionRestoreOperation: ...

    def record_version_restore_lease_refreshed(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRestoreOperation: ...

    def record_current_final_preserved(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        receipt: VersionRestoreRollbackReceipt,
        event_utc: str,
    ) -> VersionRestoreOperation: ...

    def record_historical_version_applied(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        receipt: VersionRestoreApplyReceipt,
        event_utc: str,
    ) -> VersionRestoreOperation: ...

    def record_version_restore_final_verified(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        receipt: VersionRestoreApplyReceipt,
        event_utc: str,
    ) -> VersionRestoreOperation: ...

    def complete_version_restore(
        self,
        *,
        operation: VersionRestoreOperation,
        event_utc: str,
        already_current: bool = False,
    ) -> VersionRestoreOperation: ...

    def record_version_restore_failure(
        self,
        *,
        operation: VersionRestoreOperation,
        validation_code: str,
        retryable: bool,
        event_utc: str,
    ) -> VersionRestoreOperation: ...


class VersionRestorePermitValidator(Protocol):
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None: ...


class VersionRestoreFilesystemPort(Protocol):
    def inspect_restore(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreInspectionReceipt: ...

    def preserve_current_final(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreRollbackReceipt: ...

    def apply_historical_version(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreApplyReceipt: ...

    def verify_restored_final(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreApplyReceipt: ...


def apply_next_version_restore(
    *,
    restores: VersionRestoreExecutionStore,
    leases: EndpointLeaseAuthority,
    filesystem: VersionRestoreFilesystemPort,
    event_utc: str,
) -> VersionRestoreApplyOutcome:
    _require_utc(event_utc)
    operation = restores.load_next_version_restore_operation()
    if operation is None:
        return VersionRestoreApplyOutcome(
            idle=True,
            completed=False,
            restore_id=None,
            version_object_id=None,
            state=None,
            validation_codes=(),
            next_action="No retained-version restore is waiting for execution.",
        )
    _validate_operation(operation)
    if operation.state is VersionRestoreState.FINAL_VERIFIED:
        completed = restores.complete_version_restore(
            operation=operation,
            event_utc=event_utc,
        )
        return _completed_outcome(completed)

    attempt = leases.acquire_endpoint_lease(
        EndpointLeaseRequest(
            run_id=f"version-restore:{operation.restore_id}",
            run_target_id=f"version-restore:{operation.restore_id}",
            endpoint_id=operation.record.target_endpoint_id,
            endpoint_revision_id=operation.record.target_endpoint_revision_id,
            resource_key=f"endpoint:{operation.record.target_endpoint_id}",
            required_owner_installation_id=operation.record.owner_installation_id,
            required_ownership_epoch=operation.record.ownership_epoch,
        )
    )
    if not attempt.acquired or attempt.lease is None:
        return VersionRestoreApplyOutcome(
            idle=False,
            completed=False,
            restore_id=operation.restore_id,
            version_object_id=operation.record.version_object_id,
            state=operation.state,
            validation_codes=attempt.validation_codes
            or ("VERSION_RESTORE_ENDPOINT_LEASE_UNAVAILABLE",),
            next_action=attempt.next_action,
        )

    lease = attempt.lease
    try:
        validator = _permit_validator(lease)
        permit = lease.issue_mutation_permit()
        try:
            if operation.state in {
                VersionRestoreState.CURRENT_FINAL_PRESERVED,
                VersionRestoreState.HISTORICAL_APPLIED,
            }:
                operation = restores.record_version_restore_lease_refreshed(
                    operation=operation,
                    permit=permit,
                    event_utc=event_utc,
                )
            if operation.state is VersionRestoreState.REQUESTED:
                inspection = filesystem.inspect_restore(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                if inspection.already_current:
                    completed = restores.complete_version_restore(
                        operation=operation,
                        event_utc=event_utc,
                        already_current=True,
                    )
                    return _completed_outcome(completed)
                operation = restores.record_version_restore_intent(
                    operation=operation,
                    permit=permit,
                    current_final_fingerprint_json=(
                        inspection.current_final_fingerprint_json
                    ),
                    event_utc=event_utc,
                )
            elif operation.state is VersionRestoreState.INTENT_RECORDED:
                current = operation.current_final_fingerprint_json
                if current is None:
                    raise VersionRestoreError(
                        "VERSION_RESTORE_CURRENT_FINGERPRINT_MISSING"
                    )
                operation = restores.record_version_restore_intent(
                    operation=operation,
                    permit=permit,
                    current_final_fingerprint_json=current,
                    event_utc=event_utc,
                )

            if operation.state is VersionRestoreState.INTENT_RECORDED:
                rollback = filesystem.preserve_current_final(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                operation = restores.record_current_final_preserved(
                    operation=operation,
                    permit=permit,
                    receipt=rollback,
                    event_utc=event_utc,
                )

            if operation.state is VersionRestoreState.CURRENT_FINAL_PRESERVED:
                applied = filesystem.apply_historical_version(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                operation = restores.record_historical_version_applied(
                    operation=operation,
                    permit=permit,
                    receipt=applied,
                    event_utc=event_utc,
                )

            if operation.state is VersionRestoreState.HISTORICAL_APPLIED:
                verified = filesystem.verify_restored_final(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                operation = restores.record_version_restore_final_verified(
                    operation=operation,
                    permit=permit,
                    receipt=verified,
                    event_utc=event_utc,
                )
        except (ValueError, RuntimeError) as exc:
            code = _error_code(exc)
            retryable = bool(getattr(exc, "retryable", False))
            failed = restores.record_version_restore_failure(
                operation=operation,
                validation_code=code,
                retryable=retryable,
                event_utc=event_utc,
            )
            return VersionRestoreApplyOutcome(
                idle=False,
                completed=False,
                restore_id=failed.restore_id,
                version_object_id=failed.record.version_object_id,
                state=failed.state,
                validation_codes=(code,),
                next_action=(
                    "The journaled restore will retry after the endpoint is available."
                    if retryable
                    else "The protected version and rollback evidence require review."
                ),
            )

        if operation.state is not VersionRestoreState.FINAL_VERIFIED:
            raise VersionRestoreError("VERSION_RESTORE_PHASE_ADVANCE_INCOMPLETE")
        completed = restores.complete_version_restore(
            operation=operation,
            event_utc=event_utc,
        )
        return _completed_outcome(completed)
    finally:
        lease.release()


def canonical_fingerprint_json(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VersionRestoreError("VERSION_RESTORE_FINGERPRINT_INVALID") from exc
    if not isinstance(value, dict):
        raise VersionRestoreError("VERSION_RESTORE_FINGERPRINT_INVALID")
    if set(value) == {"entry_count", "kind"}:
        if value.get("entry_count") != 0 or value.get("kind") != "DIRECTORY_EMPTY":
            raise VersionRestoreError("VERSION_RESTORE_FINGERPRINT_INVALID")
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    try:
        file_object_fingerprint_from_json(raw)
    except FileObjectFingerprintError as exc:
        raise VersionRestoreError("VERSION_RESTORE_FINGERPRINT_INVALID") from exc
    return raw


def _validate_operation(operation: VersionRestoreOperation) -> None:
    values = (
        operation.restore_id,
        operation.hold_id,
        operation.rollback_object_id,
        operation.record.version_object_id,
        operation.created_utc,
        operation.rollback_retention_until_utc,
    )
    if not all(value.strip() for value in values):
        raise VersionRestoreError("VERSION_RESTORE_OPERATION_BINDING_INVALID")
    if operation.expected_source_row_version < 1 or operation.row_version < 1:
        raise VersionRestoreError("VERSION_RESTORE_OPERATION_VERSION_INVALID")
    canonical_fingerprint_json(operation.record.original_fingerprint_json)
    if operation.current_final_fingerprint_json is not None:
        canonical_fingerprint_json(operation.current_final_fingerprint_json)
    _require_utc(operation.created_utc)
    _require_utc(operation.rollback_retention_until_utc)


def _permit_validator(lease: LiveEndpointLease) -> VersionRestorePermitValidator:
    validator = getattr(lease, "assert_mutation_permit_current", None)
    if not callable(validator):
        raise VersionRestoreError("VERSION_RESTORE_LEASE_VALIDATOR_MISSING")
    return cast(VersionRestorePermitValidator, lease)


def _completed_outcome(operation: VersionRestoreOperation) -> VersionRestoreApplyOutcome:
    return VersionRestoreApplyOutcome(
        idle=False,
        completed=True,
        restore_id=operation.restore_id,
        version_object_id=operation.record.version_object_id,
        state=operation.state,
        validation_codes=(),
        next_action="The selected retained version is now the verified final file.",
    )


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc).strip()
    return message if message else type(exc).__name__


def _require_utc(value: str) -> None:
    if not value.endswith("Z") or len(value) < 20:
        raise VersionRestoreError("VERSION_RESTORE_TIME_INVALID")
