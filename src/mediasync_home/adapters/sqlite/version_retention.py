from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mediasync_home.application.retained_version_history import (
    ProtectRetainedVersionForRestoreCommand,
    RetainedVersionCursor,
    RetainedVersionSummary,
    RestoreRetainedVersionCommand,
    VersionRestoreProtectionOutcome,
    VersionRestoreRequestOutcome,
)
from mediasync_home.application.version_restore import (
    VersionRestoreApplyReceipt,
    VersionRestoreOperation,
    VersionRestoreRollbackReceipt,
    VersionRestoreState,
    canonical_fingerprint_json,
)
from mediasync_home.application.version_retention import (
    RetainedVersionRecord,
    RetainedVersionState,
    VersionRetentionDeleteReceipt,
    VersionRetentionItemState,
    VersionRetentionPlan,
    VersionRetentionWorkItem,
)
from mediasync_home.domain.capabilities import MutationPermit


class SqliteVersionRetentionStoreError(ValueError):
    pass


class SqliteVersionRetentionStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_due_retained_versions(
        self,
        *,
        cutoff_utc: str,
        limit: int,
    ) -> tuple[RetainedVersionRecord, ...]:
        if limit < 1 or limit > 1000:
            raise SqliteVersionRetentionStoreError("VERSION_RETENTION_QUERY_LIMIT_INVALID")
        rows = self._connection.execute(
            """
            SELECT
                versions.version_object_id,
                versions.handoff_id,
                versions.run_id,
                versions.run_target_id,
                versions.operation_id,
                versions.job_id,
                versions.job_revision_id,
                versions.target_endpoint_id,
                versions.target_endpoint_revision_id,
                versions.endpoint_generation,
                versions.owner_installation_id,
                versions.ownership_epoch,
                versions.final_relative_path,
                versions.original_fingerprint_json,
                versions.created_utc,
                versions.retention_policy,
                versions.retention_until_utc,
                versions.manifest_hash,
                versions.state,
                versions.row_version
            FROM retained_version_objects AS versions
            INNER JOIN jobs
                ON jobs.id = versions.job_id
            WHERE versions.state = 'RETAINED'
                AND versions.retention_until_utc <= ?
                AND jobs.lifecycle_state = 'ACTIVE'
                AND NOT EXISTS (
                    SELECT 1
                    FROM version_retention_holds AS holds
                    WHERE holds.version_object_id = versions.version_object_id
                        AND holds.released_utc IS NULL
                )
            ORDER BY versions.retention_until_utc, versions.version_object_id
            LIMIT ?
            """,
            (cutoff_utc, limit),
        ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def list_retained_versions_for_run(
        self,
        *,
        run_id: str,
        limit: int,
        after: RetainedVersionCursor | None,
    ) -> tuple[RetainedVersionSummary, ...]:
        if limit < 1 or limit > 26:
            raise SqliteVersionRetentionStoreError(
                "RETAINED_VERSION_HISTORY_LIMIT_INVALID"
            )
        parameters: list[object] = [run_id]
        cursor_clause = ""
        if after is not None:
            cursor_clause = """
                AND (
                    versions.created_utc < ?
                    OR (
                        versions.created_utc = ?
                        AND versions.version_object_id < ?
                    )
                )
            """
            parameters.extend(
                [after.created_utc, after.created_utc, after.version_object_id]
            )
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT
                versions.version_object_id,
                versions.run_id,
                versions.operation_id,
                versions.job_id,
                versions.target_endpoint_id,
                versions.final_relative_path,
                versions.created_utc,
                versions.retention_until_utc,
                versions.state,
                versions.row_version,
                holds.hold_id,
                holds.reason,
                holds.created_utc,
                restores.restore_id,
                restores.state,
                restores.last_validation_code,
                restores.created_utc,
                restores.completed_utc
            FROM retained_version_objects AS versions
            LEFT JOIN version_retention_holds AS holds
                ON holds.version_object_id = versions.version_object_id
                AND holds.released_utc IS NULL
            LEFT JOIN retained_version_restore_operations AS restores
                ON restores.restore_id = (
                    SELECT candidate.restore_id
                    FROM retained_version_restore_operations AS candidate
                    WHERE candidate.version_object_id = versions.version_object_id
                        AND (
                            holds.hold_id IS NULL
                            OR candidate.hold_id = holds.hold_id
                        )
                    ORDER BY candidate.created_utc DESC, candidate.restore_id DESC
                    LIMIT 1
                )
            WHERE versions.run_id = ?
                {cursor_clause}
            ORDER BY versions.created_utc DESC, versions.version_object_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(_summary_from_row(row) for row in rows)

    def protect_retained_version_for_restore(
        self,
        *,
        command: ProtectRetainedVersionForRestoreCommand,
        created_utc: str,
    ) -> VersionRestoreProtectionOutcome:
        hold_id = f"restore:{command.idempotency_key}"
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing_hold = self._connection.execute(
                """
                SELECT version_object_id
                FROM version_retention_holds
                WHERE hold_id = ?
                """,
                (hold_id,),
            ).fetchone()
            if existing_hold is not None:
                if str(existing_hold[0]) != command.version_object_id:
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RESTORE_PROTECTION_IDEMPOTENCY_CONFLICT"
                    )
                outcome = self._protected_outcome(
                    command.version_object_id,
                    idempotent_replay=True,
                )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return outcome

            current = self._load_version_summary(command.version_object_id)
            if current is None:
                outcome = VersionRestoreProtectionOutcome(
                    protected=False,
                    validation_code="VERSION_RESTORE_VERSION_NOT_FOUND",
                    next_action="Refresh version history before trying again.",
                )
            elif current.state != "RETAINED":
                outcome = VersionRestoreProtectionOutcome(
                    protected=False,
                    validation_code="VERSION_RESTORE_VERSION_NOT_RETAINED",
                    next_action="Choose a retained version that has not entered expiry.",
                    version=current,
                )
            elif current.row_version != command.expected_row_version:
                outcome = VersionRestoreProtectionOutcome(
                    protected=False,
                    validation_code="VERSION_RESTORE_VERSION_CHANGED",
                    next_action="Refresh version history before trying again.",
                    version=current,
                )
            elif current.protected_for_restore:
                outcome = VersionRestoreProtectionOutcome(
                    protected=True,
                    validation_code="VERSION_RESTORE_ALREADY_PROTECTED",
                    next_action="The retained version is protected from automatic expiry.",
                    version=current,
                    idempotent_replay=True,
                )
            else:
                self._connection.execute(
                    """
                    INSERT INTO version_retention_holds (
                        hold_id,
                        version_object_id,
                        reason,
                        created_utc
                    )
                    SELECT ?, version_object_id, 'RESTORE_REQUESTED', ?
                    FROM retained_version_objects
                    WHERE version_object_id = ?
                        AND state = 'RETAINED'
                        AND row_version = ?
                    """,
                    (
                        hold_id,
                        created_utc,
                        command.version_object_id,
                        command.expected_row_version,
                    ),
                )
                outcome = self._protected_outcome(
                    command.version_object_id,
                    idempotent_replay=False,
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return outcome
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_PROTECTION_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_PROTECTION_PERSISTENCE_FAILED"
            ) from exc

    def _protected_outcome(
        self,
        version_object_id: str,
        *,
        idempotent_replay: bool,
    ) -> VersionRestoreProtectionOutcome:
        version = self._load_version_summary(version_object_id)
        if version is None or not version.protected_for_restore:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_PROTECTION_LOAD_FAILED"
            )
        return VersionRestoreProtectionOutcome(
            protected=True,
            validation_code="VERSION_RESTORE_PROTECTED",
            next_action="The retained version is protected from automatic expiry.",
            version=version,
            idempotent_replay=idempotent_replay,
        )

    def _load_version_summary(
        self,
        version_object_id: str,
    ) -> RetainedVersionSummary | None:
        row = self._connection.execute(
            """
            SELECT
                versions.version_object_id,
                versions.run_id,
                versions.operation_id,
                versions.job_id,
                versions.target_endpoint_id,
                versions.final_relative_path,
                versions.created_utc,
                versions.retention_until_utc,
                versions.state,
                versions.row_version,
                holds.hold_id,
                holds.reason,
                holds.created_utc,
                restores.restore_id,
                restores.state,
                restores.last_validation_code,
                restores.created_utc,
                restores.completed_utc
            FROM retained_version_objects AS versions
            LEFT JOIN version_retention_holds AS holds
                ON holds.version_object_id = versions.version_object_id
                AND holds.released_utc IS NULL
            LEFT JOIN retained_version_restore_operations AS restores
                ON restores.restore_id = (
                    SELECT candidate.restore_id
                    FROM retained_version_restore_operations AS candidate
                    WHERE candidate.version_object_id = versions.version_object_id
                        AND (
                            holds.hold_id IS NULL
                            OR candidate.hold_id = holds.hold_id
                        )
                    ORDER BY candidate.created_utc DESC, candidate.restore_id DESC
                    LIMIT 1
                )
            WHERE versions.version_object_id = ?
            """,
            (version_object_id,),
        ).fetchone()
        return None if row is None else _summary_from_row(row)

    def request_retained_version_restore(
        self,
        *,
        command: RestoreRetainedVersionCommand,
        created_utc: str,
    ) -> VersionRestoreRequestOutcome:
        restore_id = _restore_id(command.idempotency_key)
        rollback_object_id = f"rollback-{restore_id.removeprefix('restore-')}"
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                """
                SELECT restore_id, version_object_id, state
                FROM retained_version_restore_operations
                WHERE idempotency_key = ?
                """,
                (command.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != restore_id
                    or str(existing[1]) != command.version_object_id
                ):
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RESTORE_IDEMPOTENCY_CONFLICT"
                    )
                version = self._load_version_summary(command.version_object_id)
                outcome = VersionRestoreRequestOutcome(
                    scheduled=True,
                    validation_code="VERSION_RESTORE_ALREADY_SCHEDULED",
                    next_action="The protected version restore is already scheduled.",
                    restore_id=restore_id,
                    state=str(existing[2]),
                    version=version,
                    idempotent_replay=True,
                )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return outcome

            version = self._load_version_summary(command.version_object_id)
            if version is None:
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_VERSION_NOT_FOUND",
                    "Refresh version history before trying again.",
                )
            elif version.state != "RETAINED":
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_VERSION_NOT_RETAINED",
                    "Choose a retained version that has not entered expiry.",
                    version=version,
                )
            elif version.row_version != command.expected_row_version:
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_VERSION_CHANGED",
                    "Refresh version history before trying again.",
                    version=version,
                )
            elif not version.protected_for_restore or version.hold_id is None:
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_PROTECTION_REQUIRED",
                    "Protect the retained version before scheduling its restore.",
                    version=version,
                )
            elif version.hold_reason != "RESTORE_REQUESTED":
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_HOLD_REASON_INVALID",
                    "Refresh version history before scheduling the restore.",
                    version=version,
                )
            elif version.restore_state == VersionRestoreState.FAILED_BLOCKED.value:
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_REVIEW_REQUIRED",
                    "Review the blocked restore before scheduling another request.",
                    version=version,
                )
            elif version.restore_state is not None:
                outcome = _restore_request_rejected(
                    "VERSION_RESTORE_ALREADY_ACTIVE",
                    "Wait for the active protected-version restore to finish.",
                    version=version,
                )
            else:
                self._connection.execute(
                    """
                    INSERT INTO retained_version_restore_operations (
                        restore_id,
                        request_id,
                        idempotency_key,
                        version_object_id,
                        hold_id,
                        expected_source_row_version,
                        rollback_object_id,
                        created_utc,
                        rollback_retention_until_utc,
                        state,
                        updated_utc
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUESTED', ?
                    WHERE EXISTS (
                        SELECT 1
                        FROM retained_version_objects AS versions
                        INNER JOIN version_retention_holds AS holds
                            ON holds.version_object_id = versions.version_object_id
                        WHERE versions.version_object_id = ?
                            AND versions.state = 'RETAINED'
                            AND versions.row_version = ?
                            AND holds.hold_id = ?
                            AND holds.reason = 'RESTORE_REQUESTED'
                            AND holds.released_utc IS NULL
                    )
                    """,
                    (
                        restore_id,
                        command.request_id,
                        command.idempotency_key,
                        command.version_object_id,
                        version.hold_id,
                        command.expected_row_version,
                        rollback_object_id,
                        created_utc,
                        _retention_until_utc(created_utc),
                        created_utc,
                        command.version_object_id,
                        command.expected_row_version,
                        version.hold_id,
                    ),
                )
                if self._connection.execute("SELECT changes()").fetchone() != (1,):
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RESTORE_REQUEST_REVALIDATION_FAILED"
                    )
                self._append_restore_event(
                    restore_id=restore_id,
                    from_state=None,
                    to_state=VersionRestoreState.REQUESTED,
                    event_kind="RESTORE_REQUESTED",
                    event_utc=created_utc,
                    payload={
                        "expected_source_row_version": command.expected_row_version,
                        "hold_id": version.hold_id,
                        "version_object_id": command.version_object_id,
                    },
                )
                outcome = VersionRestoreRequestOutcome(
                    scheduled=True,
                    validation_code="VERSION_RESTORE_SCHEDULED",
                    next_action="The restore will run under the endpoint mutation lease.",
                    restore_id=restore_id,
                    state=VersionRestoreState.REQUESTED.value,
                    version=version,
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return outcome
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_REQUEST_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_REQUEST_PERSISTENCE_FAILED"
            ) from exc

    def load_next_version_restore_operation(self) -> VersionRestoreOperation | None:
        row = self._connection.execute(
            f"""
            {_RESTORE_OPERATION_SELECT}
            WHERE restores.state NOT IN ('COMPLETED', 'FAILED_BLOCKED')
            ORDER BY restores.created_utc, restores.restore_id
            LIMIT 1
            """
        ).fetchone()
        return None if row is None else _restore_operation_from_row(row)

    def record_version_restore_intent(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        current_final_fingerprint_json: str,
        event_utc: str,
    ) -> VersionRestoreOperation:
        _require_restore_endpoint_permit_binding(operation=operation, permit=permit)
        fingerprint = canonical_fingerprint_json(current_final_fingerprint_json)
        next_state = VersionRestoreState.INTENT_RECORDED
        event_kind = (
            "RESTORE_INTENT_RECORDED"
            if operation.state is VersionRestoreState.REQUESTED
            else "RESTORE_INTENT_REFRESHED"
        )
        return self._transition_restore_operation(
            operation=operation,
            expected_states=(
                VersionRestoreState.REQUESTED,
                VersionRestoreState.INTENT_RECORDED,
            ),
            next_state=next_state,
            event_kind=event_kind,
            event_utc=event_utc,
            assignments={
                "current_final_fingerprint_json": fingerprint,
                "lease_id": permit.lease_id,
                "fencing_token": permit.fencing_token,
                "last_validation_code": None,
            },
            payload={
                "current_final_fingerprint_json": fingerprint,
                "fencing_token": permit.fencing_token,
                "lease_id": permit.lease_id,
            },
        )

    def record_version_restore_lease_refreshed(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRestoreOperation:
        _require_restore_endpoint_permit_binding(operation=operation, permit=permit)
        if operation.state not in {
            VersionRestoreState.CURRENT_FINAL_PRESERVED,
            VersionRestoreState.HISTORICAL_APPLIED,
        }:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_LEASE_REFRESH_STATE_INVALID"
            )
        return self._transition_restore_operation(
            operation=operation,
            expected_states=(operation.state,),
            next_state=operation.state,
            event_kind="RESTORE_LEASE_REFRESHED",
            event_utc=event_utc,
            assignments={
                "lease_id": permit.lease_id,
                "fencing_token": permit.fencing_token,
                "last_validation_code": None,
            },
            payload={
                "fencing_token": permit.fencing_token,
                "lease_id": permit.lease_id,
            },
        )

    def record_current_final_preserved(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        receipt: VersionRestoreRollbackReceipt,
        event_utc: str,
    ) -> VersionRestoreOperation:
        _require_restore_journaled_permit_binding(operation=operation, permit=permit)
        if (
            receipt.rollback_object_id != operation.rollback_object_id
            or canonical_fingerprint_json(receipt.current_final_fingerprint_json)
            != operation.current_final_fingerprint_json
            or not _valid_hash(receipt.manifest_hash)
        ):
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_ROLLBACK_RECEIPT_MISMATCH"
            )
        return self._transition_restore_operation(
            operation=operation,
            expected_states=(VersionRestoreState.INTENT_RECORDED,),
            next_state=VersionRestoreState.CURRENT_FINAL_PRESERVED,
            event_kind="CURRENT_FINAL_PRESERVED",
            event_utc=event_utc,
            assignments={"rollback_manifest_hash": receipt.manifest_hash},
            payload={
                "rollback_manifest_hash": receipt.manifest_hash,
                "rollback_object_id": receipt.rollback_object_id,
            },
        )

    def record_historical_version_applied(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        receipt: VersionRestoreApplyReceipt,
        event_utc: str,
    ) -> VersionRestoreOperation:
        _require_restore_journaled_permit_binding(operation=operation, permit=permit)
        if (
            canonical_fingerprint_json(receipt.historical_fingerprint_json)
            != canonical_fingerprint_json(operation.record.original_fingerprint_json)
        ):
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_APPLY_RECEIPT_MISMATCH"
            )
        return self._transition_restore_operation(
            operation=operation,
            expected_states=(VersionRestoreState.CURRENT_FINAL_PRESERVED,),
            next_state=VersionRestoreState.HISTORICAL_APPLIED,
            event_kind="HISTORICAL_VERSION_APPLIED",
            event_utc=event_utc,
            assignments={},
            payload={"historical_fingerprint_json": receipt.historical_fingerprint_json},
        )

    def record_version_restore_final_verified(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
        receipt: VersionRestoreApplyReceipt,
        event_utc: str,
    ) -> VersionRestoreOperation:
        _require_restore_journaled_permit_binding(operation=operation, permit=permit)
        if (
            canonical_fingerprint_json(receipt.historical_fingerprint_json)
            != canonical_fingerprint_json(operation.record.original_fingerprint_json)
        ):
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_VERIFY_RECEIPT_MISMATCH"
            )
        return self._transition_restore_operation(
            operation=operation,
            expected_states=(VersionRestoreState.HISTORICAL_APPLIED,),
            next_state=VersionRestoreState.FINAL_VERIFIED,
            event_kind="RESTORED_FINAL_VERIFIED",
            event_utc=event_utc,
            assignments={},
            payload={"historical_fingerprint_json": receipt.historical_fingerprint_json},
        )

    def complete_version_restore(
        self,
        *,
        operation: VersionRestoreOperation,
        event_utc: str,
        already_current: bool = False,
    ) -> VersionRestoreOperation:
        expected = (
            VersionRestoreState.REQUESTED
            if already_current
            else VersionRestoreState.FINAL_VERIFIED
        )
        completed = self._transition_restore_operation(
            operation=operation,
            expected_states=(expected,),
            next_state=VersionRestoreState.COMPLETED,
            event_kind=(
                "RESTORE_ALREADY_CURRENT" if already_current else "RESTORE_COMPLETED"
            ),
            event_utc=event_utc,
            assignments={"completed_utc": event_utc, "last_validation_code": None},
            payload={"already_current": already_current},
            release_hold=True,
        )
        return completed

    def record_version_restore_failure(
        self,
        *,
        operation: VersionRestoreOperation,
        validation_code: str,
        retryable: bool,
        event_utc: str,
    ) -> VersionRestoreOperation:
        if not validation_code.strip():
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_FAILURE_CODE_MISSING"
            )
        return self._transition_restore_operation(
            operation=operation,
            expected_states=(operation.state,),
            next_state=(
                operation.state if retryable else VersionRestoreState.FAILED_BLOCKED
            ),
            event_kind=(
                "RESTORE_RETRYABLE_FAILURE" if retryable else "RESTORE_BLOCKED"
            ),
            event_utc=event_utc,
            assignments={"last_validation_code": validation_code},
            payload={
                "retryable": retryable,
                "validation_code": validation_code,
            },
        )

    def _transition_restore_operation(
        self,
        *,
        operation: VersionRestoreOperation,
        expected_states: tuple[VersionRestoreState, ...],
        next_state: VersionRestoreState,
        event_kind: str,
        event_utc: str,
        assignments: dict[str, object],
        payload: dict[str, object],
        release_hold: bool = False,
    ) -> VersionRestoreOperation:
        allowed_columns = {
            "completed_utc",
            "current_final_fingerprint_json",
            "fencing_token",
            "last_validation_code",
            "lease_id",
            "rollback_manifest_hash",
        }
        if not set(assignments) <= allowed_columns:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_TRANSITION_ASSIGNMENT_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            state_placeholders = ", ".join("?" for _ in expected_states)
            set_parts = ["state = ?", "row_version = row_version + 1", "updated_utc = ?"]
            parameters: list[object] = [next_state.value, event_utc]
            for column, value in assignments.items():
                set_parts.append(f"{column} = ?")
                parameters.append(value)
            parameters.extend(
                [
                    operation.restore_id,
                    operation.row_version,
                    *(state.value for state in expected_states),
                    operation.record.version_object_id,
                    operation.expected_source_row_version,
                    operation.hold_id,
                ]
            )
            cursor = self._connection.execute(
                f"""
                UPDATE retained_version_restore_operations
                SET {', '.join(set_parts)}
                WHERE restore_id = ?
                    AND row_version = ?
                    AND state IN ({state_placeholders})
                    AND EXISTS (
                        SELECT 1
                        FROM retained_version_objects AS versions
                        INNER JOIN version_retention_holds AS holds
                            ON holds.version_object_id = versions.version_object_id
                        WHERE versions.version_object_id = ?
                            AND versions.state = 'RETAINED'
                            AND versions.row_version = ?
                            AND holds.hold_id = ?
                            AND holds.released_utc IS NULL
                    )
                """,
                parameters,
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RESTORE_TRANSITION_CONFLICT"
                )
            if release_hold:
                released = self._connection.execute(
                    """
                    UPDATE version_retention_holds
                    SET released_utc = ?, row_version = row_version + 1
                    WHERE hold_id = ? AND released_utc IS NULL
                    """,
                    (event_utc, operation.hold_id),
                )
                if released.rowcount != 1:
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RESTORE_HOLD_RELEASE_CONFLICT"
                    )
            self._append_restore_event(
                restore_id=operation.restore_id,
                from_state=operation.state,
                to_state=next_state,
                event_kind=event_kind,
                event_utc=event_utc,
                payload=payload,
            )
            loaded = self._load_restore_operation(operation.restore_id)
            if loaded is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RESTORE_OPERATION_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RESTORE_TRANSITION_PERSISTENCE_FAILED"
            ) from exc

    def _load_restore_operation(
        self,
        restore_id: str,
    ) -> VersionRestoreOperation | None:
        row = self._connection.execute(
            f"""
            {_RESTORE_OPERATION_SELECT}
            WHERE restores.restore_id = ?
            """,
            (restore_id,),
        ).fetchone()
        return None if row is None else _restore_operation_from_row(row)

    def _append_restore_event(
        self,
        *,
        restore_id: str,
        from_state: VersionRestoreState | None,
        to_state: VersionRestoreState,
        event_kind: str,
        event_utc: str,
        payload: dict[str, object],
    ) -> None:
        previous = self._connection.execute(
            """
            SELECT restore_sequence, event_hash
            FROM retained_version_restore_events
            WHERE restore_id = ?
            ORDER BY restore_sequence DESC
            LIMIT 1
            """,
            (restore_id,),
        ).fetchone()
        sequence = 0 if previous is None else _int_column(previous[0]) + 1
        previous_hash = None if previous is None else str(previous[1])
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        body = {
            "event_kind": event_kind,
            "event_utc": event_utc,
            "from_state": None if from_state is None else from_state.value,
            "payload_json": payload_json,
            "previous_event_hash": previous_hash,
            "restore_id": restore_id,
            "restore_sequence": sequence,
            "to_state": to_state.value,
        }
        event_hash = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            """
            INSERT INTO retained_version_restore_events (
                restore_id,
                restore_sequence,
                from_state,
                to_state,
                event_kind,
                event_utc,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                restore_id,
                sequence,
                None if from_state is None else from_state.value,
                to_state.value,
                event_kind,
                event_utc,
                payload_json,
                previous_hash,
                event_hash,
            ),
        )

    def create_version_retention_plan(
        self,
        plan: VersionRetentionPlan,
    ) -> VersionRetentionPlan:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                """
                SELECT cutoff_utc, created_utc, candidate_count, manifest_json,
                       manifest_hash, state
                FROM version_retention_plans
                WHERE plan_id = ?
                """,
                (plan.plan_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != plan.cutoff_utc
                    or str(existing[1]) != plan.created_utc
                    or int(existing[2]) != len(plan.candidates)
                    or str(existing[3]) != plan.manifest_json
                    or str(existing[4]) != plan.manifest_hash
                    or str(existing[5]) != plan.state.value
                    or not self._items_match_plan(plan)
                ):
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RETENTION_PLAN_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return plan

            self._connection.execute(
                """
                INSERT INTO version_retention_plans (
                    plan_id,
                    cutoff_utc,
                    created_utc,
                    candidate_count,
                    manifest_json,
                    manifest_hash,
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.cutoff_utc,
                    plan.created_utc,
                    len(plan.candidates),
                    plan.manifest_json,
                    plan.manifest_hash,
                    plan.state.value,
                ),
            )
            for ordinal, record in enumerate(plan.candidates):
                updated = self._connection.execute(
                    """
                    UPDATE retained_version_objects
                    SET
                        state = 'DELETE_PENDING',
                        deletion_plan_id = ?,
                        row_version = row_version + 1
                    WHERE version_object_id = ?
                        AND state = 'RETAINED'
                        AND row_version = ?
                        AND manifest_hash = ?
                        AND retention_until_utc <= ?
                        AND EXISTS (
                            SELECT 1
                            FROM jobs
                            WHERE jobs.id = retained_version_objects.job_id
                                AND jobs.lifecycle_state = 'ACTIVE'
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM version_retention_holds AS holds
                            WHERE holds.version_object_id = retained_version_objects.version_object_id
                                AND holds.released_utc IS NULL
                        )
                    """,
                    (
                        plan.plan_id,
                        record.version_object_id,
                        record.row_version,
                        record.manifest_hash,
                        plan.cutoff_utc,
                    ),
                )
                if updated.rowcount != 1:
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RETENTION_PLAN_CANDIDATE_CHANGED"
                    )
                self._connection.execute(
                    """
                    INSERT INTO version_retention_items (
                        plan_id,
                        ordinal,
                        version_object_id,
                        expected_object_row_version,
                        expected_manifest_hash,
                        target_endpoint_id,
                        target_endpoint_revision_id,
                        endpoint_generation,
                        owner_installation_id,
                        ownership_epoch,
                        final_relative_path,
                        state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED')
                    """,
                    (
                        plan.plan_id,
                        ordinal,
                        record.version_object_id,
                        record.row_version + 1,
                        record.manifest_hash,
                        record.target_endpoint_id,
                        record.target_endpoint_revision_id,
                        record.endpoint_generation,
                        record.owner_installation_id,
                        record.ownership_epoch,
                        record.final_relative_path,
                    ),
                )
            self._append_planned_event(plan)
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return plan
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_PLAN_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_PLAN_PERSISTENCE_FAILED"
            ) from exc

    def load_next_version_retention_item(self) -> VersionRetentionWorkItem | None:
        row = self._connection.execute(
            """
            SELECT
                plans.plan_id,
                plans.manifest_hash,
                items.ordinal,
                items.state,
                items.expected_object_row_version,
                versions.version_object_id,
                versions.handoff_id,
                versions.run_id,
                versions.run_target_id,
                versions.operation_id,
                versions.job_id,
                versions.job_revision_id,
                versions.target_endpoint_id,
                versions.target_endpoint_revision_id,
                versions.endpoint_generation,
                versions.owner_installation_id,
                versions.ownership_epoch,
                versions.final_relative_path,
                versions.original_fingerprint_json,
                versions.created_utc,
                versions.retention_policy,
                versions.retention_until_utc,
                versions.manifest_hash,
                versions.state,
                versions.row_version
            FROM version_retention_plans AS plans
            INNER JOIN version_retention_items AS items
                ON items.plan_id = plans.plan_id
            INNER JOIN retained_version_objects AS versions
                ON versions.version_object_id = items.version_object_id
            WHERE plans.state IN ('PLANNED', 'APPLYING')
                AND items.state IN (
                    'PLANNED',
                    'DELETE_INTENT_RECORDED',
                    'FILESYSTEM_DELETED'
                )
            ORDER BY plans.created_utc, plans.plan_id, items.ordinal
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return _work_item_from_row(row)

    def record_version_delete_intent(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRetentionWorkItem:
        _require_permit_binding(item=item, permit=permit)
        if item.state not in {
            VersionRetentionItemState.PLANNED,
            VersionRetentionItemState.DELETE_INTENT_RECORDED,
        }:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_DELETE_INTENT_STATE_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE version_retention_items
                SET
                    state = 'DELETE_INTENT_RECORDED',
                    delete_lease_id = ?,
                    delete_fencing_token = ?,
                    delete_intent_utc = ?,
                    row_version = row_version + 1
                WHERE plan_id = ?
                    AND ordinal = ?
                    AND version_object_id = ?
                    AND state = ?
                    AND expected_object_row_version = ?
                    AND expected_manifest_hash = ?
                    AND EXISTS (
                        SELECT 1
                        FROM version_retention_plans AS plans
                        WHERE plans.plan_id = version_retention_items.plan_id
                            AND plans.manifest_hash = ?
                            AND plans.state IN ('PLANNED', 'APPLYING')
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM retained_version_objects AS versions
                        INNER JOIN jobs ON jobs.id = versions.job_id
                        WHERE versions.version_object_id = version_retention_items.version_object_id
                            AND versions.state = 'DELETE_PENDING'
                            AND versions.deletion_plan_id = version_retention_items.plan_id
                            AND versions.row_version = version_retention_items.expected_object_row_version
                            AND versions.manifest_hash = version_retention_items.expected_manifest_hash
                            AND jobs.lifecycle_state = 'ACTIVE'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM version_retention_holds AS holds
                                WHERE holds.version_object_id = versions.version_object_id
                                    AND holds.released_utc IS NULL
                            )
                    )
                """,
                (
                    permit.lease_id,
                    permit.fencing_token,
                    event_utc,
                    item.plan_id,
                    item.ordinal,
                    item.record.version_object_id,
                    item.state.value,
                    item.expected_object_row_version,
                    item.record.manifest_hash,
                    item.plan_manifest_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_DELETE_INTENT_REVALIDATION_FAILED"
                )
            self._connection.execute(
                """
                UPDATE version_retention_plans
                SET state = 'APPLYING', row_version = row_version + 1
                WHERE plan_id = ? AND state IN ('PLANNED', 'APPLYING')
                """,
                (item.plan_id,),
            )
            event_kind = (
                "DELETE_INTENT_RECORDED"
                if item.state is VersionRetentionItemState.PLANNED
                else "DELETE_INTENT_REFRESHED"
            )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind=event_kind,
                event_utc=event_utc,
                payload={
                    "fencing_token": permit.fencing_token,
                    "lease_id": permit.lease_id,
                    "manifest_hash": item.record.manifest_hash,
                },
            )
            loaded = self._load_work_item(plan_id=item.plan_id, ordinal=item.ordinal)
            if loaded is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_ITEM_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_DELETE_INTENT_PERSISTENCE_FAILED"
            ) from exc

    def record_version_filesystem_deleted(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
        receipt: VersionRetentionDeleteReceipt,
        event_utc: str,
    ) -> VersionRetentionWorkItem:
        _require_permit_binding(item=item, permit=permit)
        if item.state is not VersionRetentionItemState.DELETE_INTENT_RECORDED:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_FILESYSTEM_DELETED_STATE_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE version_retention_items
                SET
                    state = 'FILESYSTEM_DELETED',
                    filesystem_deleted_utc = ?,
                    row_version = row_version + 1
                WHERE plan_id = ?
                    AND ordinal = ?
                    AND state = 'DELETE_INTENT_RECORDED'
                    AND delete_lease_id = ?
                    AND delete_fencing_token = ?
                    AND expected_manifest_hash = ?
                """,
                (
                    event_utc,
                    item.plan_id,
                    item.ordinal,
                    permit.lease_id,
                    permit.fencing_token,
                    receipt.manifest_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_FILESYSTEM_DELETED_CONFLICT"
                )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind="FILESYSTEM_DELETED",
                event_utc=event_utc,
                payload={"manifest_hash": receipt.manifest_hash},
            )
            loaded = self._load_work_item(plan_id=item.plan_id, ordinal=item.ordinal)
            if loaded is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_ITEM_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_FILESYSTEM_DELETED_PERSISTENCE_FAILED"
            ) from exc

    def complete_version_retention_item(
        self,
        *,
        item: VersionRetentionWorkItem,
        event_utc: str,
    ) -> VersionRetentionWorkItem:
        if item.state is not VersionRetentionItemState.FILESYSTEM_DELETED:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_COMPLETE_STATE_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            object_cursor = self._connection.execute(
                """
                UPDATE retained_version_objects
                SET
                    state = 'DELETED',
                    deleted_utc = ?,
                    row_version = row_version + 1
                WHERE version_object_id = ?
                    AND state = 'DELETE_PENDING'
                    AND deletion_plan_id = ?
                    AND row_version = ?
                    AND manifest_hash = ?
                """,
                (
                    event_utc,
                    item.record.version_object_id,
                    item.plan_id,
                    item.expected_object_row_version,
                    item.record.manifest_hash,
                ),
            )
            if object_cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_COMPLETE_OBJECT_CONFLICT"
                )
            item_cursor = self._connection.execute(
                """
                UPDATE version_retention_items
                SET state = 'DELETED', deleted_utc = ?, row_version = row_version + 1
                WHERE plan_id = ? AND ordinal = ? AND state = 'FILESYSTEM_DELETED'
                """,
                (event_utc, item.plan_id, item.ordinal),
            )
            if item_cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_COMPLETE_ITEM_CONFLICT"
                )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind="ITEM_DELETED",
                event_utc=event_utc,
                payload={"manifest_hash": item.record.manifest_hash},
            )
            remaining = self._connection.execute(
                """
                SELECT count(*)
                FROM version_retention_items
                WHERE plan_id = ? AND state NOT IN ('DELETED', 'BLOCKED')
                """,
                (item.plan_id,),
            ).fetchone()
            if remaining is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_COMPLETE_COUNT_FAILED"
                )
            plan_state = "COMPLETED" if _int_column(remaining[0]) == 0 else "APPLYING"
            self._connection.execute(
                """
                UPDATE version_retention_plans
                SET
                    state = ?,
                    completed_utc = CASE WHEN ? = 'COMPLETED' THEN ? ELSE NULL END,
                    row_version = row_version + 1
                WHERE plan_id = ? AND state IN ('PLANNED', 'APPLYING')
                """,
                (plan_state, plan_state, event_utc, item.plan_id),
            )
            completed = VersionRetentionWorkItem(
                plan_id=item.plan_id,
                plan_manifest_hash=item.plan_manifest_hash,
                ordinal=item.ordinal,
                state=VersionRetentionItemState.DELETED,
                expected_object_row_version=item.expected_object_row_version,
                record=replace(
                    item.record,
                    state=RetainedVersionState.DELETED,
                    row_version=item.expected_object_row_version + 1,
                ),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return completed
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_COMPLETE_PERSISTENCE_FAILED"
            ) from exc

    def block_version_retention_plan(
        self,
        *,
        item: VersionRetentionWorkItem,
        validation_code: str,
        event_utc: str,
    ) -> None:
        if not validation_code.strip():
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_BLOCK_CODE_MISSING"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            requeue_blocked_object = _is_retryable_plan_block(validation_code)
            self._connection.execute(
                """
                UPDATE retained_version_objects
                SET
                    state = CASE
                        WHEN version_object_id = ? AND ? = 0 THEN 'BLOCKED'
                        ELSE 'RETAINED'
                    END,
                    deletion_plan_id = NULL,
                    last_validation_code = ?,
                    row_version = row_version + 1
                WHERE deletion_plan_id = ? AND state = 'DELETE_PENDING'
                """,
                (
                    item.record.version_object_id,
                    int(requeue_blocked_object),
                    validation_code,
                    item.plan_id,
                ),
            )
            self._connection.execute(
                """
                UPDATE version_retention_items
                SET
                    state = 'BLOCKED',
                    last_validation_code = ?,
                    row_version = row_version + 1
                WHERE plan_id = ?
                    AND state IN ('PLANNED', 'DELETE_INTENT_RECORDED')
                """,
                (validation_code, item.plan_id),
            )
            cursor = self._connection.execute(
                """
                UPDATE version_retention_plans
                SET state = 'BLOCKED', completed_utc = ?, row_version = row_version + 1
                WHERE plan_id = ? AND state IN ('PLANNED', 'APPLYING')
                """,
                (event_utc, item.plan_id),
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_BLOCK_PLAN_CONFLICT"
                )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind="PLAN_BLOCKED",
                event_utc=event_utc,
                payload={"validation_code": validation_code},
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_BLOCK_PERSISTENCE_FAILED"
            ) from exc

    def _load_work_item(
        self,
        *,
        plan_id: str,
        ordinal: int,
    ) -> VersionRetentionWorkItem | None:
        row = self._connection.execute(
            """
            SELECT
                plans.plan_id,
                plans.manifest_hash,
                items.ordinal,
                items.state,
                items.expected_object_row_version,
                versions.version_object_id,
                versions.handoff_id,
                versions.run_id,
                versions.run_target_id,
                versions.operation_id,
                versions.job_id,
                versions.job_revision_id,
                versions.target_endpoint_id,
                versions.target_endpoint_revision_id,
                versions.endpoint_generation,
                versions.owner_installation_id,
                versions.ownership_epoch,
                versions.final_relative_path,
                versions.original_fingerprint_json,
                versions.created_utc,
                versions.retention_policy,
                versions.retention_until_utc,
                versions.manifest_hash,
                versions.state,
                versions.row_version
            FROM version_retention_plans AS plans
            INNER JOIN version_retention_items AS items
                ON items.plan_id = plans.plan_id
            INNER JOIN retained_version_objects AS versions
                ON versions.version_object_id = items.version_object_id
            WHERE plans.plan_id = ? AND items.ordinal = ?
            """,
            (plan_id, ordinal),
        ).fetchone()
        if row is None:
            return None
        return _work_item_from_row(row)

    def _append_event(
        self,
        *,
        plan_id: str,
        version_object_id: str | None,
        event_kind: str,
        event_utc: str,
        payload: dict[str, object],
    ) -> None:
        previous = self._connection.execute(
            """
            SELECT plan_sequence, event_hash
            FROM version_retention_events
            WHERE plan_id = ?
            ORDER BY plan_sequence DESC
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        sequence = 0 if previous is None else _int_column(previous[0]) + 1
        previous_hash = None if previous is None else str(previous[1])
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event_hash = hashlib.sha256(
            json.dumps(
                {
                    "event_kind": event_kind,
                    "event_utc": event_utc,
                    "payload_json": payload_json,
                    "plan_id": plan_id,
                    "plan_sequence": sequence,
                    "previous_event_hash": previous_hash,
                    "version_object_id": version_object_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            """
            INSERT INTO version_retention_events (
                plan_id,
                plan_sequence,
                version_object_id,
                event_kind,
                event_utc,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                sequence,
                version_object_id,
                event_kind,
                event_utc,
                payload_json,
                previous_hash,
                event_hash,
            ),
        )

    def _items_match_plan(self, plan: VersionRetentionPlan) -> bool:
        rows = self._connection.execute(
            """
            SELECT ordinal, version_object_id, expected_object_row_version,
                   expected_manifest_hash, target_endpoint_id,
                   target_endpoint_revision_id, endpoint_generation,
                   owner_installation_id, ownership_epoch, final_relative_path
            FROM version_retention_items
            WHERE plan_id = ?
            ORDER BY ordinal
            """,
            (plan.plan_id,),
        ).fetchall()
        expected = [
            (
                ordinal,
                record.version_object_id,
                record.row_version + 1,
                record.manifest_hash,
                record.target_endpoint_id,
                record.target_endpoint_revision_id,
                record.endpoint_generation,
                record.owner_installation_id,
                record.ownership_epoch,
                record.final_relative_path,
            )
            for ordinal, record in enumerate(plan.candidates)
        ]
        return [tuple(row) for row in rows] == expected

    def _append_planned_event(self, plan: VersionRetentionPlan) -> None:
        payload_json = json.dumps(
            {
                "candidate_count": len(plan.candidates),
                "manifest_hash": plan.manifest_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        event_utc = plan.created_utc
        event_hash = hashlib.sha256(
            json.dumps(
                {
                    "event_kind": "PLAN_CREATED",
                    "event_utc": event_utc,
                    "payload_json": payload_json,
                    "plan_id": plan.plan_id,
                    "plan_sequence": 0,
                    "previous_event_hash": None,
                    "version_object_id": None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            """
            INSERT INTO version_retention_events (
                plan_id,
                plan_sequence,
                version_object_id,
                event_kind,
                event_utc,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, 0, NULL, 'PLAN_CREATED', ?, ?, NULL, ?)
            """,
            (plan.plan_id, event_utc, payload_json, event_hash),
        )


_RESTORE_OPERATION_SELECT = """
    SELECT
        restores.restore_id,
        restores.hold_id,
        restores.rollback_object_id,
        restores.expected_source_row_version,
        restores.created_utc,
        restores.rollback_retention_until_utc,
        restores.state,
        restores.current_final_fingerprint_json,
        restores.rollback_manifest_hash,
        restores.lease_id,
        restores.fencing_token,
        restores.completed_utc,
        restores.last_validation_code,
        restores.row_version,
        versions.version_object_id,
        versions.handoff_id,
        versions.run_id,
        versions.run_target_id,
        versions.operation_id,
        versions.job_id,
        versions.job_revision_id,
        versions.target_endpoint_id,
        versions.target_endpoint_revision_id,
        versions.endpoint_generation,
        versions.owner_installation_id,
        versions.ownership_epoch,
        versions.final_relative_path,
        versions.original_fingerprint_json,
        versions.created_utc,
        versions.retention_policy,
        versions.retention_until_utc,
        versions.manifest_hash,
        versions.state,
        versions.row_version
    FROM retained_version_restore_operations AS restores
    INNER JOIN retained_version_objects AS versions
        ON versions.version_object_id = restores.version_object_id
"""


def _restore_operation_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> VersionRestoreOperation:
    return VersionRestoreOperation(
        restore_id=str(row[0]),
        hold_id=str(row[1]),
        rollback_object_id=str(row[2]),
        expected_source_row_version=_int_column(row[3]),
        created_utc=str(row[4]),
        rollback_retention_until_utc=str(row[5]),
        state=VersionRestoreState(str(row[6])),
        current_final_fingerprint_json=(
            None if row[7] is None else str(row[7])
        ),
        rollback_manifest_hash=None if row[8] is None else str(row[8]),
        lease_id=None if row[9] is None else str(row[9]),
        fencing_token=None if row[10] is None else _int_column(row[10]),
        completed_utc=None if row[11] is None else str(row[11]),
        last_validation_code=None if row[12] is None else str(row[12]),
        row_version=_int_column(row[13]),
        record=_record_from_row(tuple(row[14:34])),
    )


def _restore_request_rejected(
    validation_code: str,
    next_action: str,
    *,
    version: RetainedVersionSummary | None = None,
) -> VersionRestoreRequestOutcome:
    return VersionRestoreRequestOutcome(
        scheduled=False,
        validation_code=validation_code,
        next_action=next_action,
        version=version,
    )


def _restore_id(idempotency_key: str) -> str:
    if not idempotency_key.strip():
        raise SqliteVersionRetentionStoreError(
            "VERSION_RESTORE_IDEMPOTENCY_KEY_MISSING"
        )
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"restore-{digest[:32]}"


def _retention_until_utc(created_utc: str) -> str:
    try:
        created = datetime.fromisoformat(created_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SqliteVersionRetentionStoreError(
            "VERSION_RESTORE_CREATED_UTC_INVALID"
        ) from exc
    if created.tzinfo is None or created.utcoffset() != timedelta(0):
        raise SqliteVersionRetentionStoreError(
            "VERSION_RESTORE_CREATED_UTC_INVALID"
        )
    retained_until = created.astimezone(timezone.utc) + timedelta(days=30)
    return retained_until.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _valid_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _require_restore_endpoint_permit_binding(
    *,
    operation: VersionRestoreOperation,
    permit: MutationPermit,
) -> None:
    if (
        permit.run_id != f"version-restore:{operation.restore_id}"
        or permit.run_target_id != f"version-restore:{operation.restore_id}"
        or permit.endpoint_id != operation.record.target_endpoint_id
        or permit.endpoint_revision_id != operation.record.target_endpoint_revision_id
        or permit.endpoint_generation != operation.record.endpoint_generation
        or permit.owner_installation_id != operation.record.owner_installation_id
        or permit.ownership_epoch != operation.record.ownership_epoch
        or permit.resource_key != f"endpoint:{operation.record.target_endpoint_id}"
    ):
        raise SqliteVersionRetentionStoreError("VERSION_RESTORE_PERMIT_MISMATCH")


def _require_restore_journaled_permit_binding(
    *,
    operation: VersionRestoreOperation,
    permit: MutationPermit,
) -> None:
    _require_restore_endpoint_permit_binding(operation=operation, permit=permit)
    if (
        operation.lease_id != permit.lease_id
        or operation.fencing_token != permit.fencing_token
    ):
        raise SqliteVersionRetentionStoreError(
            "VERSION_RESTORE_JOURNALED_PERMIT_MISMATCH"
        )


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> RetainedVersionRecord:
    return RetainedVersionRecord(
        version_object_id=str(row[0]),
        handoff_id=str(row[1]),
        run_id=str(row[2]),
        run_target_id=str(row[3]),
        operation_id=str(row[4]),
        job_id=str(row[5]),
        job_revision_id=str(row[6]),
        target_endpoint_id=str(row[7]),
        target_endpoint_revision_id=str(row[8]),
        endpoint_generation=_int_column(row[9]),
        owner_installation_id=str(row[10]),
        ownership_epoch=_int_column(row[11]),
        final_relative_path=str(row[12]),
        original_fingerprint_json=str(row[13]),
        created_utc=str(row[14]),
        retention_policy=str(row[15]),
        retention_until_utc=str(row[16]),
        manifest_hash=str(row[17]),
        state=RetainedVersionState(str(row[18])),
        row_version=_int_column(row[19]),
    )


def _summary_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> RetainedVersionSummary:
    return RetainedVersionSummary(
        version_object_id=str(row[0]),
        run_id=str(row[1]),
        operation_id=str(row[2]),
        job_id=str(row[3]),
        target_endpoint_id=str(row[4]),
        final_relative_path=str(row[5]),
        created_utc=str(row[6]),
        retention_until_utc=str(row[7]),
        state=str(row[8]),
        row_version=_int_column(row[9]),
        hold_id=None if row[10] is None else str(row[10]),
        hold_reason=None if row[11] is None else str(row[11]),
        hold_created_utc=None if row[12] is None else str(row[12]),
        restore_id=None if row[13] is None else str(row[13]),
        restore_state=None if row[14] is None else str(row[14]),
        restore_validation_code=None if row[15] is None else str(row[15]),
        restore_created_utc=None if row[16] is None else str(row[16]),
        restore_completed_utc=None if row[17] is None else str(row[17]),
    )


def _work_item_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> VersionRetentionWorkItem:
    return VersionRetentionWorkItem(
        plan_id=str(row[0]),
        plan_manifest_hash=str(row[1]),
        ordinal=_int_column(row[2]),
        state=VersionRetentionItemState(str(row[3])),
        expected_object_row_version=_int_column(row[4]),
        record=_record_from_row(tuple(row[5:25])),
    )


def _require_permit_binding(
    *,
    item: VersionRetentionWorkItem,
    permit: MutationPermit,
) -> None:
    if (
        permit.run_id != f"version-retention:{item.plan_id}"
        or permit.run_target_id != f"version-retention:{item.plan_id}:{item.ordinal}"
        or permit.endpoint_id != item.record.target_endpoint_id
        or permit.endpoint_revision_id != item.record.target_endpoint_revision_id
        or permit.endpoint_generation != item.record.endpoint_generation
        or permit.owner_installation_id != item.record.owner_installation_id
        or permit.ownership_epoch != item.record.ownership_epoch
        or permit.resource_key != f"endpoint:{item.record.target_endpoint_id}"
    ):
        raise SqliteVersionRetentionStoreError("VERSION_RETENTION_PERMIT_MISMATCH")


def _int_column(value: object) -> int:
    if isinstance(value, bool):
        raise SqliteVersionRetentionStoreError("VERSION_RETENTION_INTEGER_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise SqliteVersionRetentionStoreError("VERSION_RETENTION_INTEGER_INVALID")


def _is_retryable_plan_block(validation_code: str) -> bool:
    return (
        validation_code.startswith("VERSION_RETENTION_RECOVERY_REFERENCE_")
        or validation_code
        == "VERSION_RETENTION_DELETE_INTENT_REVALIDATION_FAILED"
    )
