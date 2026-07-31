from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any, Mapping

from mediasync_home.adapters.system_clock import SystemClock
from mediasync_home.application.clocks import ClockPort
from mediasync_home.application.operation_audit import (
    RecoveryOperationAuditEvent,
    RunProcessAuditEvidence,
)
from mediasync_home.application.recovery_operations import (
    PRE_COMMIT_LEASE_REBIND_PHASES,
    TERMINAL_PHASES,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
    validate_recovery_operation,
    validate_recovery_phase_transition,
)
from mediasync_home.application.runs import RunTargetStopProgress
from mediasync_home.application.staging_retry import (
    MonotonicStagingRetryScheduler,
    StagingRetryViolation,
    staging_retry_backoff_ms,
)


class SqliteRecoveryOperationStoreError(ValueError):
    pass


class SqliteRecoveryOperationStore(RecoveryOperationStore):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: ClockPort | None = None,
        staging_retry_scheduler: MonotonicStagingRetryScheduler | None = None,
    ) -> None:
        if clock is not None and staging_retry_scheduler is not None:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_RETRY_CLOCK_IS_AMBIGUOUS"
            )
        self._connection = connection
        self._staging_retries = (
            staging_retry_scheduler
            or MonotonicStagingRetryScheduler(clock or SystemClock())
        )

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        if operation.phase is not RecoveryOperationPhase.PLANNED:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_PLANNED_PHASE"
            )
        _validate_process_instance_id(process_instance_id)
        validate_recovery_operation(operation)

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_operation(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
            )
            if existing is not None:
                if existing != operation:
                    raise SqliteRecoveryOperationStoreError(
                        "RECOVERY_OPERATION_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing

            self._require_active_matching_lease(operation)
            self._insert_operation(operation)
            self._append_event(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
                from_phase=None,
                to_phase=operation.phase,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            planned = self.load_operation(
                run_id=operation.run_id, operation_id=operation.operation_id
            )
            if planned is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return planned
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_FAILED"
            ) from exc

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: RecoveryOperationMetadata | None = None,
    ) -> RecoveryOperation | None:
        _validate_process_instance_id(process_instance_id)
        validate_recovery_phase_transition(expected_phase, next_phase)

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            operation = self.load_operation(run_id=run_id, operation_id=operation_id)
            if operation is None or operation.phase is not expected_phase:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            updated = self._operation_after_transition(
                operation,
                next_phase=next_phase,
                intent_segment_id=intent_segment_id,
                intent_ordinal=intent_ordinal,
                catalog_handoff_id=catalog_handoff_id,
                operation_metadata=operation_metadata,
            )
            self._require_active_matching_lease(updated)
            if next_phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED:
                self._require_matching_intent_segment(updated)

            cursor = self._connection.execute(
                """
                UPDATE recovery_operations
                SET
                    phase = ?,
                    source_guard_kind = ?,
                    source_guard_evidence_hash = ?,
                    source_hash_evidence_kind = ?,
                    staging_object_id = ?,
                    version_object_id = ?,
                    quarantine_object_id = ?,
                    intent_segment_id = ?,
                    intent_ordinal = ?,
                    expected_source_fingerprint_json = ?,
                    expected_target_fingerprint_json = ?,
                    expected_staging_fingerprint_json = ?,
                    expected_final_fingerprint_json = ?,
                    transfer_state = ?,
                    assurance_level = ?,
                    staging_durability_state = ?,
                    final_durability_state = ?,
                    catalog_handoff_id = ?,
                    last_error_code = ?,
                    staging_retry_backoff_ms = NULL,
                    staging_retry_not_before_utc = NULL,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE run_id = ?
                    AND operation_id = ?
                    AND phase = ?
                """,
                (
                    updated.phase.value,
                    updated.source_guard_kind,
                    updated.source_guard_evidence_hash,
                    updated.source_hash_evidence_kind,
                    updated.staging_object_id,
                    updated.version_object_id,
                    updated.quarantine_object_id,
                    updated.intent_segment_id,
                    updated.intent_ordinal,
                    updated.expected_source_fingerprint_json,
                    updated.expected_target_fingerprint_json,
                    updated.expected_staging_fingerprint_json,
                    updated.expected_final_fingerprint_json,
                    updated.transfer_state,
                    updated.assurance_level,
                    updated.staging_durability_state,
                    updated.final_durability_state,
                    updated.catalog_handoff_id,
                    updated.last_error_code,
                    run_id,
                    operation_id,
                    expected_phase.value,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            self._append_event(
                run_id=run_id,
                operation_id=operation_id,
                from_phase=expected_phase,
                to_phase=next_phase,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            loaded = self.load_operation(run_id=run_id, operation_id=operation_id)
            if loaded is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            self._staging_retries.discard(
                run_id=run_id,
                operation_id=operation_id,
            )
            return loaded
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_FAILED"
            ) from exc

    def record_staging_failure(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        expected_failure_count: int,
        next_phase: RecoveryOperationPhase,
        error_code: str,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        _validate_process_instance_id(process_instance_id)
        normalized_error_code = error_code.strip()
        if expected_failure_count < 0:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_NONNEGATIVE_FAILURE_COUNT"
            )
        if not normalized_error_code:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_ERROR_CODE"
            )
        if expected_phase not in PRE_COMMIT_LEASE_REBIND_PHASES[:-1]:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_STAGING_FAILURE_PHASE_UNSUPPORTED"
            )
        if next_phase not in {expected_phase, RecoveryOperationPhase.SKIPPED}:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_STAGING_FAILURE_TRANSITION_UNSUPPORTED"
            )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            operation = self.load_operation(run_id=run_id, operation_id=operation_id)
            if (
                operation is None
                or operation.phase is not expected_phase
                or operation.staging_failure_count != expected_failure_count
            ):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            failure_count = expected_failure_count + 1
            retry_timing = None
            if next_phase is expected_phase:
                retry_timing = self._staging_retries.plan(
                    backoff_ms=staging_retry_backoff_ms(
                        run_id=run_id,
                        operation_id=operation_id,
                        attempt_no=failure_count,
                    )
                )
            updated = replace(
                operation,
                phase=next_phase,
                last_error_code=normalized_error_code,
                staging_failure_count=failure_count,
                staging_retry_backoff_ms=(
                    None if retry_timing is None else retry_timing.backoff_ms
                ),
                staging_retry_not_before_utc=(
                    None
                    if retry_timing is None
                    else retry_timing.retry_not_before_utc
                ),
            )
            validate_recovery_operation(updated)
            self._require_active_matching_lease(updated)
            cursor = self._connection.execute(
                """
                UPDATE recovery_operations
                SET
                    phase = ?,
                    last_error_code = ?,
                    staging_failure_count = ?,
                    staging_retry_backoff_ms = ?,
                    staging_retry_not_before_utc = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE run_id = ?
                    AND operation_id = ?
                    AND phase = ?
                    AND staging_failure_count = ?
                """,
                (
                    next_phase.value,
                    normalized_error_code,
                    failure_count,
                    updated.staging_retry_backoff_ms,
                    updated.staging_retry_not_before_utc,
                    run_id,
                    operation_id,
                    expected_phase.value,
                    expected_failure_count,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            event_payload = {
                **({} if payload is None else payload),
                "attempt_number": failure_count,
                "error_code": normalized_error_code,
                "event_kind": "STAGING_ATTEMPT_FAILED",
                "retry_scheduled": next_phase is expected_phase,
                "retry_backoff_ms": updated.staging_retry_backoff_ms,
                "retry_not_before_utc": updated.staging_retry_not_before_utc,
            }
            self._append_event(
                run_id=run_id,
                operation_id=operation_id,
                from_phase=expected_phase,
                to_phase=next_phase,
                process_instance_id=process_instance_id,
                payload=event_payload,
            )
            loaded = self.load_operation(run_id=run_id, operation_id=operation_id)
            if loaded is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            if retry_timing is not None:
                self._staging_retries.activate(
                    run_id=run_id,
                    operation_id=operation_id,
                    timing=retry_timing,
                )
            else:
                self._staging_retries.discard(
                    run_id=run_id,
                    operation_id=operation_id,
                )
            return loaded
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_CONFLICT"
            ) from exc
        except (sqlite3.Error, StagingRetryViolation) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_FAILED"
            ) from exc

    def staging_retry_is_due(self, operation: RecoveryOperation) -> bool:
        if (
            operation.staging_retry_backoff_ms is None
            and operation.staging_retry_not_before_utc is None
        ):
            return True
        if (
            operation.staging_retry_backoff_ms is None
            or operation.staging_retry_not_before_utc is None
        ):
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_STAGING_RETRY_INVALID"
            )
        try:
            return self._staging_retries.is_due(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
                backoff_ms=operation.staging_retry_backoff_ms,
                retry_not_before_utc=operation.staging_retry_not_before_utc,
            )
        except StagingRetryViolation as exc:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_STAGING_RETRY_INVALID"
            ) from exc

    def record_operation_lease_rebound(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        _validate_process_instance_id(process_instance_id)
        if expected_phase not in PRE_COMMIT_LEASE_REBIND_PHASES:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_LEASE_REBIND_PHASE_UNSUPPORTED"
            )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            operation = self.load_operation(run_id=run_id, operation_id=operation_id)
            if operation is None or operation.phase is not expected_phase:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            if (
                operation.lease_id != expected_lease_id
                or operation.ownership_epoch != expected_ownership_epoch
                or operation.fencing_token != expected_fencing_token
            ):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            updated = replace(
                operation,
                owner_installation_id=owner_installation_id,
                ownership_epoch=ownership_epoch,
                lease_id=lease_id,
                fencing_token=fencing_token,
            )
            validate_recovery_operation(updated)
            self._require_active_matching_lease(updated)

            cursor = self._connection.execute(
                """
                UPDATE recovery_operations
                SET
                    owner_installation_id = ?,
                    ownership_epoch = ?,
                    lease_id = ?,
                    fencing_token = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE run_id = ?
                    AND operation_id = ?
                    AND phase = ?
                    AND lease_id = ?
                    AND ownership_epoch = ?
                    AND fencing_token = ?
                """,
                (
                    updated.owner_installation_id,
                    updated.ownership_epoch,
                    updated.lease_id,
                    updated.fencing_token,
                    run_id,
                    operation_id,
                    expected_phase.value,
                    expected_lease_id,
                    expected_ownership_epoch,
                    expected_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            self._append_event(
                run_id=run_id,
                operation_id=operation_id,
                from_phase=expected_phase,
                to_phase=expected_phase,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            loaded = self.load_operation(run_id=run_id, operation_id=operation_id)
            if loaded is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_FAILED"
            ) from exc

    def record_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        _validate_process_instance_id(process_instance_id)

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            operation = self.load_operation(run_id=run_id, operation_id=operation_id)
            if (
                operation is None
                or operation.phase is not RecoveryOperationPhase.COMMIT_INTENT_RECORDED
            ):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            if (
                operation.lease_id != expected_lease_id
                or operation.ownership_epoch != expected_ownership_epoch
                or operation.fencing_token != expected_fencing_token
            ):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            updated = replace(
                operation,
                owner_installation_id=owner_installation_id,
                ownership_epoch=ownership_epoch,
                lease_id=lease_id,
                fencing_token=fencing_token,
                intent_segment_id=intent_segment_id,
                intent_ordinal=intent_ordinal,
            )
            validate_recovery_operation(updated)
            self._require_active_matching_lease(updated)
            self._require_matching_intent_segment(updated)

            cursor = self._connection.execute(
                """
                UPDATE recovery_operations
                SET
                    owner_installation_id = ?,
                    ownership_epoch = ?,
                    lease_id = ?,
                    fencing_token = ?,
                    intent_segment_id = ?,
                    intent_ordinal = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE run_id = ?
                    AND operation_id = ?
                    AND phase = 'COMMIT_INTENT_RECORDED'
                    AND lease_id = ?
                    AND ownership_epoch = ?
                    AND fencing_token = ?
                """,
                (
                    updated.owner_installation_id,
                    updated.ownership_epoch,
                    updated.lease_id,
                    updated.fencing_token,
                    updated.intent_segment_id,
                    updated.intent_ordinal,
                    run_id,
                    operation_id,
                    expected_lease_id,
                    expected_ownership_epoch,
                    expected_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            self._append_event(
                run_id=run_id,
                operation_id=operation_id,
                from_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
                to_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            loaded = self.load_operation(run_id=run_id, operation_id=operation_id)
            if loaded is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_FAILED"
            ) from exc

    def record_old_target_preserved_commit_intent_refreshed(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
        intent_segment_id: str,
        intent_ordinal: int,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation | None:
        _validate_process_instance_id(process_instance_id)

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            operation = self.load_operation(run_id=run_id, operation_id=operation_id)
            if (
                operation is None
                or operation.phase is not RecoveryOperationPhase.OLD_TARGET_PRESERVED
            ):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            if (
                operation.lease_id != expected_lease_id
                or operation.ownership_epoch != expected_ownership_epoch
                or operation.fencing_token != expected_fencing_token
            ):
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            updated = replace(
                operation,
                owner_installation_id=owner_installation_id,
                ownership_epoch=ownership_epoch,
                lease_id=lease_id,
                fencing_token=fencing_token,
                intent_segment_id=intent_segment_id,
                intent_ordinal=intent_ordinal,
            )
            validate_recovery_operation(updated)
            self._require_active_matching_lease(updated)
            self._require_matching_intent_segment(updated)

            cursor = self._connection.execute(
                """
                UPDATE recovery_operations
                SET
                    owner_installation_id = ?,
                    ownership_epoch = ?,
                    lease_id = ?,
                    fencing_token = ?,
                    intent_segment_id = ?,
                    intent_ordinal = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE run_id = ?
                    AND operation_id = ?
                    AND phase = 'OLD_TARGET_PRESERVED'
                    AND lease_id = ?
                    AND ownership_epoch = ?
                    AND fencing_token = ?
                """,
                (
                    updated.owner_installation_id,
                    updated.ownership_epoch,
                    updated.lease_id,
                    updated.fencing_token,
                    updated.intent_segment_id,
                    updated.intent_ordinal,
                    run_id,
                    operation_id,
                    expected_lease_id,
                    expected_ownership_epoch,
                    expected_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            self._append_event(
                run_id=run_id,
                operation_id=operation_id,
                from_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
                to_phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            loaded = self.load_operation(run_id=run_id, operation_id=operation_id)
            if loaded is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_PERSISTENCE_FAILED"
            ) from exc

    def load_operation(
        self, *, run_id: str, operation_id: str
    ) -> RecoveryOperation | None:
        row = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE run_id = ?
                AND operation_id = ?
            """,
            (run_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        return _operation_from_row(row)

    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        if limit < 1:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_POSITIVE_LIMIT"
            )
        rows = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE phase = ?
            ORDER BY updated_utc, run_id, operation_id
            LIMIT ?
            """,
            (phase.value, limit),
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        if limit < 1:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_POSITIVE_LIMIT"
            )
        rows = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE run_id = ?
                AND run_target_id = ?
                AND phase = ?
            ORDER BY plan_sequence_no, operation_id
            LIMIT ?
            """,
            (run_id, run_target_id, phase.value, limit),
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def list_operations_for_run_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        _validate_positive_limit(limit)
        rows = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE run_id = ?
                AND run_target_id = ?
            ORDER BY plan_sequence_no, operation_id
            LIMIT ?
            """,
            (run_id, run_target_id, limit),
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def list_operation_audit_events(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[RecoveryOperationAuditEvent, ...]:
        _validate_positive_limit(limit)
        rows = self._connection.execute(
            """
            SELECT
                run_id,
                run_sequence,
                operation_id,
                from_phase,
                to_phase,
                event_utc,
                process_instance_id,
                payload_json,
                event_hash
            FROM recovery_events
            WHERE run_id = ?
                AND operation_id = ?
            ORDER BY run_sequence
            LIMIT ?
            """,
            (run_id, operation_id, limit),
        ).fetchall()
        return tuple(_operation_audit_event_from_row(row) for row in rows)

    def list_run_process_audit_evidence(
        self,
        *,
        run_id: str,
        limit: int,
    ) -> tuple[RunProcessAuditEvidence, ...]:
        _validate_positive_limit(limit)
        rows = self._connection.execute(
            """
            WITH process_bounds AS (
                SELECT
                    process_instance_id,
                    min(run_sequence) AS first_run_sequence,
                    max(run_sequence) AS last_run_sequence
                FROM recovery_events
                WHERE run_id = ?
                GROUP BY process_instance_id
                ORDER BY first_run_sequence, process_instance_id
                LIMIT ?
            )
            SELECT
                bounds.process_instance_id,
                bounds.first_run_sequence,
                first_event.event_utc,
                last_event.event_utc
            FROM process_bounds AS bounds
            JOIN recovery_events AS first_event
                ON first_event.run_id = ?
                AND first_event.run_sequence = bounds.first_run_sequence
            JOIN recovery_events AS last_event
                ON last_event.run_id = ?
                AND last_event.run_sequence = bounds.last_run_sequence
            ORDER BY bounds.first_run_sequence, bounds.process_instance_id
            """,
            (run_id, limit, run_id, run_id),
        ).fetchall()
        return tuple(
            RunProcessAuditEvidence(
                process_instance_id=str(row[0]),
                first_run_sequence=int(row[1]),
                first_event_utc=str(row[2]),
                last_event_utc=str(row[3]),
            )
            for row in rows
        )

    def list_started_operations_for_run(
        self,
        *,
        run_id: str,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        if limit < 1:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_POSITIVE_LIMIT"
            )
        terminal_values = tuple(phase.value for phase in TERMINAL_PHASES)
        placeholders = ", ".join("?" for _ in terminal_values)
        rows = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE run_id = ?
                AND phase <> 'PLANNED'
                AND phase NOT IN ({placeholders})
            ORDER BY plan_sequence_no, operation_id
            LIMIT ?
            """,
            (run_id, *terminal_values, limit),
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def list_planned_operations_for_run(
        self,
        *,
        run_id: str,
        exclude_operation_id: str | None,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        if limit < 1:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_REQUIRES_POSITIVE_LIMIT"
            )
        exclusion = "" if exclude_operation_id is None else "AND operation_id <> ?"
        parameters: tuple[object, ...] = (
            (run_id, limit)
            if exclude_operation_id is None
            else (run_id, exclude_operation_id, limit)
        )
        rows = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE run_id = ?
                AND phase = 'PLANNED'
                {exclusion}
            ORDER BY run_target_id, plan_sequence_no, operation_id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def summarize_successful_operations_for_run(
        self,
        run_id: str,
    ) -> tuple[RunTargetStopProgress, ...]:
        rows = self._connection.execute(
            """
            SELECT
                run_target_id,
                count(*),
                coalesce(sum(planned_bytes), 0)
            FROM recovery_operations
            WHERE run_id = ?
                AND phase IN ('CATALOG_RECORDED', 'CLEANED')
            GROUP BY run_target_id
            ORDER BY run_target_id
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            RunTargetStopProgress(
                run_target_id=str(row[0]),
                completed_operations=int(row[1]),
                completed_bytes=int(row[2]),
            )
            for row in rows
        )

    def _operation_after_transition(
        self,
        operation: RecoveryOperation,
        *,
        next_phase: RecoveryOperationPhase,
        intent_segment_id: str | None,
        intent_ordinal: int | None,
        catalog_handoff_id: str | None,
        operation_metadata: RecoveryOperationMetadata | None,
    ) -> RecoveryOperation:
        if next_phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED:
            if intent_segment_id is None or intent_ordinal is None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_REQUIRES_INTENT_SEGMENT"
                )
            if catalog_handoff_id is not None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_UNEXPECTED_CATALOG_HANDOFF"
                )
            updated = replace(
                operation,
                phase=next_phase,
                intent_segment_id=intent_segment_id,
                intent_ordinal=intent_ordinal,
            )
        elif next_phase is RecoveryOperationPhase.CATALOG_RECORDED:
            if intent_segment_id is not None or intent_ordinal is not None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_UNEXPECTED_INTENT_SEGMENT"
                )
            if catalog_handoff_id is None or not catalog_handoff_id.strip():
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_REQUIRES_CATALOG_HANDOFF"
                )
            updated = replace(
                operation,
                phase=next_phase,
                catalog_handoff_id=catalog_handoff_id,
            )
        else:
            if intent_segment_id is not None or intent_ordinal is not None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_UNEXPECTED_INTENT_SEGMENT"
                )
            if catalog_handoff_id is not None:
                raise SqliteRecoveryOperationStoreError(
                    "RECOVERY_OPERATION_UNEXPECTED_CATALOG_HANDOFF"
                )
            updated = replace(operation, phase=next_phase)
        updated = replace(
            updated,
            staging_retry_backoff_ms=None,
            staging_retry_not_before_utc=None,
        )
        updated = _apply_operation_metadata(updated, operation_metadata)
        validate_recovery_operation(updated)
        return updated

    def _require_active_matching_lease(self, operation: RecoveryOperation) -> None:
        row = self._connection.execute(
            """
            SELECT
                resource_key,
                owner_instance_id,
                ownership_epoch,
                fencing_token,
                endpoint_id,
                state
            FROM resource_leases
            WHERE lease_id = ?
            """,
            (operation.lease_id,),
        ).fetchone()
        if row is None:
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_LEASE_MISMATCH")
        if (
            str(row[0]) != operation.lease_resource_key
            or str(row[1]) != operation.owner_installation_id
            or int(row[2]) != operation.ownership_epoch
            or int(row[3]) != operation.fencing_token
            or str(row[4]) != operation.target_endpoint_id
            or str(row[5]) != "ACQUIRED"
        ):
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_LEASE_MISMATCH")

    def _require_matching_intent_segment(self, operation: RecoveryOperation) -> None:
        row = self._connection.execute(
            """
            SELECT
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                durability_state,
                state
            FROM recovery_intent_segments
            WHERE id = ?
            """,
            (operation.intent_segment_id,),
        ).fetchone()
        if row is None:
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_INTENT_SEGMENT_MISMATCH"
            )
        if (
            str(row[0]) != operation.run_id
            or str(row[1]) != operation.run_target_id
            or str(row[2]) != operation.target_endpoint_id
            or str(row[3]) != operation.target_endpoint_revision_id
            or int(row[4]) != operation.endpoint_generation
            or str(row[5]) != operation.owner_installation_id
            or int(row[6]) != operation.ownership_epoch
            or str(row[7]) != operation.lease_id
            or int(row[8]) != operation.fencing_token
            or str(row[9]) != "DURABLE"
            or str(row[10]) != "DURABLE"
        ):
            raise SqliteRecoveryOperationStoreError(
                "RECOVERY_OPERATION_INTENT_SEGMENT_MISMATCH"
            )

    def _insert_operation(self, operation: RecoveryOperation) -> None:
        self._connection.execute(
            f"""
            INSERT INTO recovery_operations (
                {RECOVERY_OPERATION_COLUMNS}
            )
            VALUES ({RECOVERY_OPERATION_PLACEHOLDERS})
            """,
            _operation_values(operation),
        )

    def _append_event(
        self,
        *,
        run_id: str,
        operation_id: str,
        from_phase: RecoveryOperationPhase | None,
        to_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT run_sequence, event_hash
            FROM recovery_events
            WHERE run_id = ?
            ORDER BY run_sequence DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            run_sequence = 0
            previous_event_hash = None
        else:
            run_sequence = int(row[0]) + 1
            previous_event_hash = str(row[1])
        operation = self.load_operation(run_id=run_id, operation_id=operation_id)
        event_payload = {} if payload is None else dict(payload)
        if operation is not None and (
            event_payload.get("event_kind") == "STAGING_ATTEMPT_FAILED"
            or (
                from_phase is RecoveryOperationPhase.STAGING_DURABLE
                and to_phase is RecoveryOperationPhase.STAGING_VERIFIED
            )
        ):
            event_payload["operation_audit"] = _operation_audit_payload(operation)
        payload_json = _canonical_json(event_payload)
        event_hash = _event_hash(
            run_id=run_id,
            run_sequence=run_sequence,
            operation_id=operation_id,
            from_phase=from_phase,
            to_phase=to_phase,
            process_instance_id=process_instance_id,
            payload_json=payload_json,
            previous_event_hash=previous_event_hash,
        )
        self._connection.execute(
            """
            INSERT INTO recovery_events (
                run_id,
                run_sequence,
                operation_id,
                from_phase,
                to_phase,
                process_instance_id,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_sequence,
                operation_id,
                None if from_phase is None else from_phase.value,
                to_phase.value,
                process_instance_id,
                payload_json,
                previous_event_hash,
                event_hash,
            ),
        )


RECOVERY_OPERATION_COLUMN_NAMES = (
    "run_id",
    "run_target_id",
    "operation_id",
    "operation_kind",
    "plan_sequence_no",
    "source_endpoint_id",
    "source_endpoint_revision_id",
    "source_precondition_json",
    "target_endpoint_id",
    "target_endpoint_revision_id",
    "endpoint_generation",
    "owner_installation_id",
    "ownership_epoch",
    "lease_id",
    "lease_resource_key",
    "fencing_token",
    "phase",
    "source_relative_path",
    "source_guard_kind",
    "source_guard_evidence_hash",
    "source_hash_evidence_kind",
    "source_path_chain_hash",
    "source_case_context_hash",
    "staging_object_id",
    "final_relative_path",
    "version_object_id",
    "quarantine_object_id",
    "intent_segment_id",
    "intent_ordinal",
    "target_precondition_kind",
    "expected_source_fingerprint_json",
    "expected_target_fingerprint_json",
    "expected_source_parent_identity_json",
    "expected_target_parent_identity_json",
    "expected_target_path_chain_hash",
    "expected_staging_fingerprint_json",
    "expected_final_fingerprint_json",
    "observed_target_file_id",
    "transfer_state",
    "assurance_level",
    "staging_durability_state",
    "final_durability_state",
    "catalog_handoff_id",
    "last_error_code",
    "planned_bytes",
    "staging_failure_count",
    "staging_retry_backoff_ms",
    "staging_retry_not_before_utc",
)
RECOVERY_OPERATION_COLUMNS = ", ".join(RECOVERY_OPERATION_COLUMN_NAMES)
RECOVERY_OPERATION_PLACEHOLDERS = ", ".join(
    "?" for _ in RECOVERY_OPERATION_COLUMN_NAMES
)


def _operation_values(operation: RecoveryOperation) -> tuple[object, ...]:
    return (
        operation.run_id,
        operation.run_target_id,
        operation.operation_id,
        operation.operation_kind.value,
        operation.plan_sequence_no,
        operation.source_endpoint_id,
        operation.source_endpoint_revision_id,
        operation.source_precondition_json,
        operation.target_endpoint_id,
        operation.target_endpoint_revision_id,
        operation.endpoint_generation,
        operation.owner_installation_id,
        operation.ownership_epoch,
        operation.lease_id,
        operation.lease_resource_key,
        operation.fencing_token,
        operation.phase.value,
        operation.source_relative_path,
        operation.source_guard_kind,
        operation.source_guard_evidence_hash,
        operation.source_hash_evidence_kind,
        operation.source_path_chain_hash,
        operation.source_case_context_hash,
        operation.staging_object_id,
        operation.final_relative_path,
        operation.version_object_id,
        operation.quarantine_object_id,
        operation.intent_segment_id,
        operation.intent_ordinal,
        operation.target_precondition_kind.value,
        operation.expected_source_fingerprint_json,
        operation.expected_target_fingerprint_json,
        operation.expected_source_parent_identity_json,
        operation.expected_target_parent_identity_json,
        operation.expected_target_path_chain_hash,
        operation.expected_staging_fingerprint_json,
        operation.expected_final_fingerprint_json,
        operation.observed_target_file_id,
        operation.transfer_state,
        operation.assurance_level,
        operation.staging_durability_state,
        operation.final_durability_state,
        operation.catalog_handoff_id,
        operation.last_error_code,
        operation.planned_bytes,
        operation.staging_failure_count,
        operation.staging_retry_backoff_ms,
        operation.staging_retry_not_before_utc,
    )


def _operation_from_row(row: sqlite3.Row | tuple[Any, ...]) -> RecoveryOperation:
    return RecoveryOperation(
        run_id=str(row[0]),
        run_target_id=str(row[1]),
        operation_id=str(row[2]),
        operation_kind=RecoveryOperationKind(str(row[3])),
        plan_sequence_no=int(row[4]),
        source_endpoint_id=None if row[5] is None else str(row[5]),
        source_endpoint_revision_id=None if row[6] is None else str(row[6]),
        source_precondition_json=None if row[7] is None else str(row[7]),
        target_endpoint_id=str(row[8]),
        target_endpoint_revision_id=str(row[9]),
        endpoint_generation=int(row[10]),
        owner_installation_id=str(row[11]),
        ownership_epoch=int(row[12]),
        lease_id=str(row[13]),
        lease_resource_key=str(row[14]),
        fencing_token=int(row[15]),
        phase=RecoveryOperationPhase(str(row[16])),
        source_relative_path=None if row[17] is None else str(row[17]),
        source_guard_kind=None if row[18] is None else str(row[18]),
        source_guard_evidence_hash=None if row[19] is None else str(row[19]),
        source_hash_evidence_kind=None if row[20] is None else str(row[20]),
        source_path_chain_hash=None if row[21] is None else str(row[21]),
        source_case_context_hash=None if row[22] is None else str(row[22]),
        staging_object_id=None if row[23] is None else str(row[23]),
        final_relative_path=str(row[24]),
        version_object_id=None if row[25] is None else str(row[25]),
        quarantine_object_id=None if row[26] is None else str(row[26]),
        intent_segment_id=None if row[27] is None else str(row[27]),
        intent_ordinal=None if row[28] is None else int(row[28]),
        target_precondition_kind=RecoveryTargetPreconditionKind(str(row[29])),
        expected_source_fingerprint_json=None if row[30] is None else str(row[30]),
        expected_target_fingerprint_json=None if row[31] is None else str(row[31]),
        expected_source_parent_identity_json=None if row[32] is None else str(row[32]),
        expected_target_parent_identity_json=None if row[33] is None else str(row[33]),
        expected_target_path_chain_hash=None if row[34] is None else str(row[34]),
        expected_staging_fingerprint_json=None if row[35] is None else str(row[35]),
        expected_final_fingerprint_json=None if row[36] is None else str(row[36]),
        observed_target_file_id=None if row[37] is None else str(row[37]),
        transfer_state=None if row[38] is None else str(row[38]),
        assurance_level=None if row[39] is None else str(row[39]),
        staging_durability_state=None if row[40] is None else str(row[40]),
        final_durability_state=None if row[41] is None else str(row[41]),
        catalog_handoff_id=None if row[42] is None else str(row[42]),
        last_error_code=None if row[43] is None else str(row[43]),
        planned_bytes=int(row[44]),
        staging_failure_count=int(row[45]),
        staging_retry_backoff_ms=None if row[46] is None else int(row[46]),
        staging_retry_not_before_utc=None if row[47] is None else str(row[47]),
    )


def _apply_operation_metadata(
    operation: RecoveryOperation,
    metadata: RecoveryOperationMetadata | None,
) -> RecoveryOperation:
    if metadata is None:
        return operation
    return replace(
        operation,
        source_guard_kind=metadata.source_guard_kind
        if metadata.source_guard_kind is not None
        else operation.source_guard_kind,
        source_guard_evidence_hash=metadata.source_guard_evidence_hash
        if metadata.source_guard_evidence_hash is not None
        else operation.source_guard_evidence_hash,
        source_hash_evidence_kind=metadata.source_hash_evidence_kind
        if metadata.source_hash_evidence_kind is not None
        else operation.source_hash_evidence_kind,
        staging_object_id=metadata.staging_object_id
        if metadata.staging_object_id is not None
        else operation.staging_object_id,
        version_object_id=metadata.version_object_id
        if metadata.version_object_id is not None
        else operation.version_object_id,
        quarantine_object_id=metadata.quarantine_object_id
        if metadata.quarantine_object_id is not None
        else operation.quarantine_object_id,
        expected_source_fingerprint_json=metadata.expected_source_fingerprint_json
        if metadata.expected_source_fingerprint_json is not None
        else operation.expected_source_fingerprint_json,
        expected_target_fingerprint_json=metadata.expected_target_fingerprint_json
        if metadata.expected_target_fingerprint_json is not None
        else operation.expected_target_fingerprint_json,
        expected_staging_fingerprint_json=metadata.expected_staging_fingerprint_json
        if metadata.expected_staging_fingerprint_json is not None
        else operation.expected_staging_fingerprint_json,
        expected_final_fingerprint_json=metadata.expected_final_fingerprint_json
        if metadata.expected_final_fingerprint_json is not None
        else operation.expected_final_fingerprint_json,
        transfer_state=metadata.transfer_state
        if metadata.transfer_state is not None
        else operation.transfer_state,
        assurance_level=metadata.assurance_level
        if metadata.assurance_level is not None
        else operation.assurance_level,
        staging_durability_state=metadata.staging_durability_state
        if metadata.staging_durability_state is not None
        else operation.staging_durability_state,
        final_durability_state=metadata.final_durability_state
        if metadata.final_durability_state is not None
        else operation.final_durability_state,
        last_error_code=metadata.last_error_code
        if metadata.last_error_code is not None
        else operation.last_error_code,
    )


def _operation_audit_event_from_row(
    row: sqlite3.Row | tuple[Any, ...],
) -> RecoveryOperationAuditEvent:
    try:
        payload = json.loads(str(row[7]))
    except (json.JSONDecodeError, TypeError) as exc:
        raise SqliteRecoveryOperationStoreError(
            "RECOVERY_OPERATION_AUDIT_PAYLOAD_INVALID"
        ) from exc
    if not isinstance(payload, dict):
        raise SqliteRecoveryOperationStoreError(
            "RECOVERY_OPERATION_AUDIT_PAYLOAD_INVALID"
        )
    return RecoveryOperationAuditEvent(
        run_id=str(row[0]),
        run_sequence=int(row[1]),
        operation_id=str(row[2]),
        from_phase=(
            None if row[3] is None else RecoveryOperationPhase(str(row[3]))
        ),
        to_phase=RecoveryOperationPhase(str(row[4])),
        event_utc=str(row[5]),
        process_instance_id=str(row[6]),
        payload=payload,
        event_hash=str(row[8]),
    )


def _operation_audit_payload(operation: RecoveryOperation) -> Mapping[str, object]:
    return {
        "assurance_level": operation.assurance_level,
        "durability_level": (
            operation.final_durability_state or operation.staging_durability_state
        ),
        "fencing_token": operation.fencing_token,
        "lease_id": operation.lease_id,
        "ownership_epoch": operation.ownership_epoch,
        "planned_bytes": operation.planned_bytes,
        "source_guard_evidence_hash": operation.source_guard_evidence_hash,
        "source_guard_kind": operation.source_guard_kind,
        "staging_object_id": operation.staging_object_id,
        "transfer_state": operation.transfer_state,
    }


def _validate_positive_limit(limit: int) -> None:
    if limit < 1:
        raise SqliteRecoveryOperationStoreError(
            "RECOVERY_OPERATION_REQUIRES_POSITIVE_LIMIT"
        )


def _validate_process_instance_id(process_instance_id: str) -> None:
    if not process_instance_id.strip():
        raise SqliteRecoveryOperationStoreError(
            "RECOVERY_OPERATION_REQUIRES_PROCESS_INSTANCE"
        )


def _event_hash(
    *,
    run_id: str,
    run_sequence: int,
    operation_id: str,
    from_phase: RecoveryOperationPhase | None,
    to_phase: RecoveryOperationPhase,
    process_instance_id: str,
    payload_json: str,
    previous_event_hash: str | None,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schema_version": 1,
                "run_id": run_id,
                "run_sequence": run_sequence,
                "operation_id": operation_id,
                "from_phase": None if from_phase is None else from_phase.value,
                "to_phase": to_phase.value,
                "process_instance_id": process_instance_id,
                "payload_json": payload_json,
                "previous_event_hash": previous_event_hash,
            }
        ).encode("utf-8")
    ).hexdigest()


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
