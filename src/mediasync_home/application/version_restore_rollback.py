from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    LiveEndpointLease,
)
from mediasync_home.application.version_restore import (
    VersionRestoreOperation,
    canonical_fingerprint_json,
)
from mediasync_home.domain.capabilities import MutationPermit


class VersionRestoreRollbackState(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNDO_REQUESTED = "UNDO_REQUESTED"
    UNDO_INTENT_RECORDED = "UNDO_INTENT_RECORDED"
    UNDO_APPLIED = "UNDO_APPLIED"
    UNDO_VERIFIED = "UNDO_VERIFIED"
    UNDONE = "UNDONE"
    EXPIRY_INTENT_RECORDED = "EXPIRY_INTENT_RECORDED"
    EXPIRED = "EXPIRED"
    FAILED_BLOCKED = "FAILED_BLOCKED"


class VersionRestoreRollbackError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass(frozen=True, slots=True)
class VersionRestoreRollbackOperation:
    restore: VersionRestoreOperation
    state: VersionRestoreRollbackState
    expected_restored_final_fingerprint_json: str
    rollback_fingerprint_json: str
    rollback_manifest_hash: str
    retention_until_utc: str
    undo_request_id: str | None = None
    undo_idempotency_key: str | None = None
    lease_id: str | None = None
    fencing_token: int | None = None
    completed_utc: str | None = None
    last_validation_code: str | None = None
    row_version: int = 1

    @property
    def restore_id(self) -> str:
        return self.restore.restore_id

    @property
    def rollback_object_id(self) -> str:
        return self.restore.rollback_object_id


@dataclass(frozen=True, slots=True)
class VersionRestoreUndoInspectionReceipt:
    current_final_fingerprint_json: str
    rollback_fingerprint_json: str
    already_undone: bool


@dataclass(frozen=True, slots=True)
class VersionRestoreUndoApplyReceipt:
    rollback_fingerprint_json: str


@dataclass(frozen=True, slots=True)
class VersionRestoreRollbackDeleteReceipt:
    restore_id: str
    rollback_object_id: str
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class VersionRestoreRollbackApplyOutcome:
    idle: bool
    completed: bool
    action: str | None
    restore_id: str | None
    state: VersionRestoreRollbackState | None
    validation_codes: tuple[str, ...]
    next_action: str


class VersionRestoreRollbackExecutionStore(Protocol):
    def load_next_version_restore_undo_operation(
        self,
    ) -> VersionRestoreRollbackOperation | None: ...

    def load_next_due_version_restore_rollback(
        self,
        *,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation | None: ...

    def record_version_restore_undo_intent(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation: ...

    def record_version_restore_undo_applied(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        permit: MutationPermit,
        receipt: VersionRestoreUndoApplyReceipt,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation: ...

    def record_version_restore_undo_verified(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        permit: MutationPermit,
        receipt: VersionRestoreUndoApplyReceipt,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation: ...

    def complete_version_restore_undo(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        event_utc: str,
        already_undone: bool = False,
    ) -> VersionRestoreRollbackOperation: ...

    def record_version_restore_rollback_expiry_intent(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation: ...

    def complete_version_restore_rollback_expiry(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        permit: MutationPermit,
        receipt: VersionRestoreRollbackDeleteReceipt,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation: ...

    def record_version_restore_rollback_failure(
        self,
        *,
        operation: VersionRestoreRollbackOperation,
        validation_code: str,
        retryable: bool,
        event_utc: str,
    ) -> VersionRestoreRollbackOperation: ...


class VersionRestoreRollbackPermitValidator(Protocol):
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None: ...


class VersionRestoreRollbackFilesystemPort(Protocol):
    def inspect_restore_undo(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> VersionRestoreUndoInspectionReceipt: ...

    def apply_restore_undo(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> VersionRestoreUndoApplyReceipt: ...

    def verify_restore_undo(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> VersionRestoreUndoApplyReceipt: ...

    def verify_restore_rollback_for_expiry(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> None: ...

    def delete_restore_rollback(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
        resuming_delete_intent: bool,
    ) -> VersionRestoreRollbackDeleteReceipt: ...


def apply_next_version_restore_undo(
    *,
    rollbacks: VersionRestoreRollbackExecutionStore,
    leases: EndpointLeaseAuthority,
    filesystem: VersionRestoreRollbackFilesystemPort,
    event_utc: str,
) -> VersionRestoreRollbackApplyOutcome:
    _require_utc(event_utc)
    operation = rollbacks.load_next_version_restore_undo_operation()
    if operation is None:
        return _idle_outcome("No protected-version undo is waiting for execution.")
    _validate_operation(operation)
    attempt = _acquire_lease(operation=operation, leases=leases, action="undo")
    if not attempt.acquired or attempt.lease is None:
        return _lease_wait_outcome(
            operation=operation,
            action="undo",
            validation_codes=attempt.validation_codes,
            next_action=attempt.next_action,
        )
    lease = attempt.lease
    try:
        validator = _permit_validator(lease)
        permit = lease.issue_mutation_permit()
        try:
            if operation.state is VersionRestoreRollbackState.UNDO_REQUESTED:
                inspection = filesystem.inspect_restore_undo(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                canonical_fingerprint_json(
                    inspection.current_final_fingerprint_json
                )
                canonical_fingerprint_json(inspection.rollback_fingerprint_json)
                operation = rollbacks.record_version_restore_undo_intent(
                    operation=operation,
                    permit=permit,
                    event_utc=event_utc,
                )
            elif operation.state is VersionRestoreRollbackState.UNDO_INTENT_RECORDED:
                operation = rollbacks.record_version_restore_undo_intent(
                    operation=operation,
                    permit=permit,
                    event_utc=event_utc,
                )

            if operation.state is VersionRestoreRollbackState.UNDO_INTENT_RECORDED:
                receipt = filesystem.apply_restore_undo(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                operation = rollbacks.record_version_restore_undo_applied(
                    operation=operation,
                    permit=permit,
                    receipt=receipt,
                    event_utc=event_utc,
                )

            if operation.state is VersionRestoreRollbackState.UNDO_APPLIED:
                operation = rollbacks.record_version_restore_undo_intent(
                    operation=operation,
                    permit=permit,
                    event_utc=event_utc,
                )
                receipt = filesystem.verify_restore_undo(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
                operation = rollbacks.record_version_restore_undo_verified(
                    operation=operation,
                    permit=permit,
                    receipt=receipt,
                    event_utc=event_utc,
                )
        except (ValueError, RuntimeError) as exc:
            return _failure_outcome(
                rollbacks=rollbacks,
                operation=operation,
                action="undo",
                exc=exc,
                event_utc=event_utc,
            )

        if operation.state is not VersionRestoreRollbackState.UNDO_VERIFIED:
            raise VersionRestoreRollbackError("VERSION_RESTORE_UNDO_PHASE_INCOMPLETE")
        completed = rollbacks.complete_version_restore_undo(
            operation=operation,
            event_utc=event_utc,
        )
        return _completed_outcome(completed, action="undo")
    finally:
        lease.release()


def apply_next_version_restore_rollback_expiry(
    *,
    rollbacks: VersionRestoreRollbackExecutionStore,
    leases: EndpointLeaseAuthority,
    filesystem: VersionRestoreRollbackFilesystemPort,
    event_utc: str,
) -> VersionRestoreRollbackApplyOutcome:
    _require_utc(event_utc)
    operation = rollbacks.load_next_due_version_restore_rollback(
        event_utc=event_utc
    )
    if operation is None:
        return _idle_outcome("No restore rollback object is due for expiry.")
    _validate_operation(operation)
    attempt = _acquire_lease(operation=operation, leases=leases, action="expiry")
    if not attempt.acquired or attempt.lease is None:
        return _lease_wait_outcome(
            operation=operation,
            action="expiry",
            validation_codes=attempt.validation_codes,
            next_action=attempt.next_action,
        )
    lease = attempt.lease
    try:
        validator = _permit_validator(lease)
        permit = lease.issue_mutation_permit()
        try:
            resuming = (
                operation.state
                is VersionRestoreRollbackState.EXPIRY_INTENT_RECORDED
            )
            if not resuming:
                filesystem.verify_restore_rollback_for_expiry(
                    permit_validator=validator,
                    permit=permit,
                    operation=operation,
                )
            operation = rollbacks.record_version_restore_rollback_expiry_intent(
                operation=operation,
                permit=permit,
                event_utc=event_utc,
            )
            receipt = filesystem.delete_restore_rollback(
                permit_validator=validator,
                permit=permit,
                operation=operation,
                resuming_delete_intent=resuming,
            )
            operation = rollbacks.complete_version_restore_rollback_expiry(
                operation=operation,
                permit=permit,
                receipt=receipt,
                event_utc=event_utc,
            )
        except (ValueError, RuntimeError) as exc:
            return _failure_outcome(
                rollbacks=rollbacks,
                operation=operation,
                action="expiry",
                exc=exc,
                event_utc=event_utc,
            )
        return _completed_outcome(operation, action="expiry")
    finally:
        lease.release()


def _acquire_lease(
    *,
    operation: VersionRestoreRollbackOperation,
    leases: EndpointLeaseAuthority,
    action: str,
) -> EndpointLeaseAttempt:
    record = operation.restore.record
    return leases.acquire_endpoint_lease(
        EndpointLeaseRequest(
            run_id=f"version-restore-{action}:{operation.restore_id}",
            run_target_id=f"version-restore-{action}:{operation.restore_id}",
            endpoint_id=record.target_endpoint_id,
            endpoint_revision_id=record.target_endpoint_revision_id,
            resource_key=f"endpoint:{record.target_endpoint_id}",
            required_owner_installation_id=record.owner_installation_id,
            required_ownership_epoch=record.ownership_epoch,
        )
    )


def _permit_validator(
    lease: LiveEndpointLease,
) -> VersionRestoreRollbackPermitValidator:
    validator = getattr(lease, "assert_mutation_permit_current", None)
    if not callable(validator):
        raise VersionRestoreRollbackError(
            "VERSION_RESTORE_ROLLBACK_LEASE_VALIDATOR_MISSING"
        )
    return cast(VersionRestoreRollbackPermitValidator, lease)


def _validate_operation(operation: VersionRestoreRollbackOperation) -> None:
    if operation.restore.state.value != "COMPLETED":
        raise VersionRestoreRollbackError(
            "VERSION_RESTORE_ROLLBACK_SOURCE_NOT_COMPLETED"
        )
    if operation.row_version < 1:
        raise VersionRestoreRollbackError(
            "VERSION_RESTORE_ROLLBACK_ROW_VERSION_INVALID"
        )
    for value in (
        operation.restore_id,
        operation.rollback_object_id,
        operation.rollback_manifest_hash,
        operation.retention_until_utc,
    ):
        if not value.strip():
            raise VersionRestoreRollbackError(
                "VERSION_RESTORE_ROLLBACK_BINDING_INVALID"
            )
    canonical_fingerprint_json(
        operation.expected_restored_final_fingerprint_json
    )
    canonical_fingerprint_json(operation.rollback_fingerprint_json)
    if (
        operation.restore.rollback_manifest_hash
        != operation.rollback_manifest_hash
        or operation.restore.current_final_fingerprint_json
        != operation.rollback_fingerprint_json
        or canonical_fingerprint_json(
            operation.restore.record.original_fingerprint_json
        )
        != operation.expected_restored_final_fingerprint_json
        or operation.restore.rollback_retention_until_utc
        != operation.retention_until_utc
    ):
        raise VersionRestoreRollbackError(
            "VERSION_RESTORE_ROLLBACK_SOURCE_BINDING_MISMATCH"
        )
    _require_utc(operation.retention_until_utc)


def _failure_outcome(
    *,
    rollbacks: VersionRestoreRollbackExecutionStore,
    operation: VersionRestoreRollbackOperation,
    action: str,
    exc: BaseException,
    event_utc: str,
) -> VersionRestoreRollbackApplyOutcome:
    code = _error_code(exc)
    retryable = bool(getattr(exc, "retryable", False))
    failed = rollbacks.record_version_restore_rollback_failure(
        operation=operation,
        validation_code=code,
        retryable=retryable,
        event_utc=event_utc,
    )
    return VersionRestoreRollbackApplyOutcome(
        idle=False,
        completed=False,
        action=action,
        restore_id=failed.restore_id,
        state=failed.state,
        validation_codes=(code,),
        next_action=(
            "The rollback lifecycle will retry after the endpoint is available."
            if retryable
            else "The rollback object and live final require review."
        ),
    )


def _lease_wait_outcome(
    *,
    operation: VersionRestoreRollbackOperation,
    action: str,
    validation_codes: tuple[str, ...],
    next_action: str,
) -> VersionRestoreRollbackApplyOutcome:
    return VersionRestoreRollbackApplyOutcome(
        idle=False,
        completed=False,
        action=action,
        restore_id=operation.restore_id,
        state=operation.state,
        validation_codes=validation_codes
        or ("VERSION_RESTORE_ROLLBACK_ENDPOINT_LEASE_UNAVAILABLE",),
        next_action=next_action,
    )


def _completed_outcome(
    operation: VersionRestoreRollbackOperation,
    *,
    action: str,
) -> VersionRestoreRollbackApplyOutcome:
    return VersionRestoreRollbackApplyOutcome(
        idle=False,
        completed=True,
        action=action,
        restore_id=operation.restore_id,
        state=operation.state,
        validation_codes=(),
        next_action=(
            "The pre-restore final file has been restored and verified."
            if action == "undo"
            else "The expired restore rollback object was verified and removed."
        ),
    )


def _idle_outcome(next_action: str) -> VersionRestoreRollbackApplyOutcome:
    return VersionRestoreRollbackApplyOutcome(
        idle=True,
        completed=False,
        action=None,
        restore_id=None,
        state=None,
        validation_codes=(),
        next_action=next_action,
    )


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc).strip()
    return message if message else type(exc).__name__


def _require_utc(value: str) -> None:
    if not value.endswith("Z") or len(value) < 20:
        raise VersionRestoreRollbackError(
            "VERSION_RESTORE_ROLLBACK_TIME_INVALID"
        )
