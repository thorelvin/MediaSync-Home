from __future__ import annotations

import sqlite3

from mediasync_home.application.operation_audit import (
    OperationAttemptAudit,
    OperationAuditCatalogStore,
    OperationAuditWriteResult,
    OperationOutcomeAudit,
    RunAttemptAudit,
)
from mediasync_home.application.operation_audit_read_models import (
    OperationAttemptSummary,
    OperationAuditIdentity,
    OperationAuditReadModelStore,
    OperationOutcomeSummary,
)


class SqliteOperationAuditStoreError(ValueError):
    pass


class SqliteOperationAuditStore(
    OperationAuditCatalogStore,
    OperationAuditReadModelStore,
):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def reconcile_operation_audit(
        self,
        *,
        run_attempts: tuple[RunAttemptAudit, ...],
        operation_attempts: tuple[OperationAttemptAudit, ...],
        operation_outcome: OperationOutcomeAudit | None,
    ) -> OperationAuditWriteResult:
        run_id = _audit_run_id(
            run_attempts=run_attempts,
            operation_attempts=operation_attempts,
            operation_outcome=operation_outcome,
        )
        if run_id is None:
            return OperationAuditWriteResult(changed=False)
        plan_id = self._load_run_plan_id(run_id)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            inserted_run_attempts = sum(
                self._insert_run_attempt(attempt) for attempt in run_attempts
            )
            inserted_operation_attempts = sum(
                self._insert_operation_attempt(attempt, plan_id=plan_id)
                for attempt in operation_attempts
            )
            inserted_outcome = (
                False
                if operation_outcome is None
                else self._insert_operation_outcome(
                    operation_outcome,
                    plan_id=plan_id,
                )
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return OperationAuditWriteResult(
                changed=(
                    inserted_run_attempts > 0
                    or inserted_operation_attempts > 0
                    or inserted_outcome
                ),
                run_attempts_inserted=inserted_run_attempts,
                operation_attempts_inserted=inserted_operation_attempts,
                operation_outcome_inserted=inserted_outcome,
            )
        except SqliteOperationAuditStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteOperationAuditStoreError(
                "OPERATION_AUDIT_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteOperationAuditStoreError(
                "OPERATION_AUDIT_PERSISTENCE_FAILED"
            ) from exc

    def load_operation_audit_identity(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationAuditIdentity | None:
        row = self._connection.execute(
            """
            SELECT
                runs.id,
                run_targets.id,
                details.operation_id,
                details.target_relative_path
            FROM runs
            JOIN plan_operation_seal_details AS details
                ON details.plan_id = runs.plan_id
            JOIN run_targets
                ON run_targets.run_id = runs.id
                AND run_targets.endpoint_id = details.target_endpoint_id
            WHERE runs.id = ?
                AND details.operation_id = ?
            """,
            (run_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        return OperationAuditIdentity(
            run_id=str(row[0]),
            run_target_id=str(row[1]),
            operation_id=str(row[2]),
            target_relative_path=None if row[3] is None else str(row[3]),
        )

    def list_operation_attempt_summaries(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[OperationAttemptSummary, ...]:
        _validate_read_limit(limit)
        rows = self._connection.execute(
            """
            SELECT
                attempts.attempt_number,
                attempts.state,
                run_attempts.process_instance_id,
                attempts.finished_utc,
                attempts.bytes_transferred,
                attempts.batch_id,
                attempts.lease_id,
                attempts.ownership_epoch,
                attempts.fencing_token,
                attempts.source_guard_kind,
                attempts.source_guard_evidence_hash,
                attempts.transfer_state,
                attempts.assurance_level,
                attempts.durability_level,
                attempts.verification_json,
                attempts.error_code
            FROM operation_attempts AS attempts
            JOIN run_attempts
                ON run_attempts.id = attempts.run_attempt_id
                AND run_attempts.run_id = attempts.run_id
            WHERE attempts.run_id = ?
                AND attempts.operation_id = ?
            ORDER BY attempts.attempt_number
            LIMIT ?
            """,
            (run_id, operation_id, limit),
        ).fetchall()
        return tuple(
            OperationAttemptSummary(
                attempt_number=int(row[0]),
                state=str(row[1]),
                process_instance_id=str(row[2]),
                finished_utc=str(row[3]),
                bytes_transferred=int(row[4]),
                batch_id=_optional_text(row[5]),
                lease_id=_optional_text(row[6]),
                ownership_epoch=_optional_int(row[7]),
                fencing_token=_optional_int(row[8]),
                source_guard_kind=_optional_text(row[9]),
                source_guard_evidence_hash=_optional_text(row[10]),
                transfer_state=_optional_text(row[11]),
                assurance_level=_optional_text(row[12]),
                durability_level=_optional_text(row[13]),
                verification_json=_optional_text(row[14]),
                error_code=_optional_text(row[15]),
            )
            for row in rows
        )

    def load_operation_outcome_summary(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationOutcomeSummary | None:
        row = self._connection.execute(
            """
            SELECT
                final_state,
                completed_utc,
                bytes_transferred,
                transfer_state,
                assurance_level,
                hash_evidence_kind,
                durability_level,
                verification_json,
                error_code
            FROM operation_outcomes
            WHERE run_id = ? AND operation_id = ?
            """,
            (run_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        return OperationOutcomeSummary(
            final_state=str(row[0]),
            completed_utc=str(row[1]),
            bytes_transferred=int(row[2]),
            transfer_state=str(row[3]),
            assurance_level=str(row[4]),
            hash_evidence_kind=_optional_text(row[5]),
            durability_level=str(row[6]),
            verification_json=_optional_text(row[7]),
            error_code=_optional_text(row[8]),
        )

    def _load_run_plan_id(self, run_id: str) -> str:
        row = self._connection.execute(
            "SELECT plan_id FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise SqliteOperationAuditStoreError("OPERATION_AUDIT_RUN_NOT_FOUND")
        return str(row[0])

    def _insert_run_attempt(self, attempt: RunAttemptAudit) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO run_attempts (
                id,
                run_id,
                attempt_number,
                process_instance_id,
                started_utc,
                finished_utc,
                termination_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                attempt.id,
                attempt.run_id,
                attempt.attempt_number,
                attempt.process_instance_id,
                attempt.started_utc,
                attempt.finished_utc,
                attempt.termination_reason,
            ),
        )
        if cursor.rowcount == 1:
            return 1
        row = self._connection.execute(
            """
            SELECT
                id,
                run_id,
                attempt_number,
                process_instance_id,
                started_utc
            FROM run_attempts
            WHERE id = ?
            """,
            (attempt.id,),
        ).fetchone()
        expected = (
            attempt.id,
            attempt.run_id,
            attempt.attempt_number,
            attempt.process_instance_id,
            attempt.started_utc,
        )
        if row is None or tuple(row) != expected:
            raise SqliteOperationAuditStoreError(
                "OPERATION_AUDIT_RUN_ATTEMPT_IDEMPOTENCY_CONFLICT"
            )
        return 0

    def _insert_operation_attempt(
        self,
        attempt: OperationAttemptAudit,
        *,
        plan_id: str,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO operation_attempts (
                id,
                run_attempt_id,
                run_id,
                plan_id,
                run_target_id,
                operation_id,
                attempt_number,
                state,
                batch_id,
                lease_id,
                ownership_epoch,
                fencing_token,
                source_guard_kind,
                source_guard_evidence_hash,
                transfer_state,
                assurance_level,
                durability_level,
                started_utc,
                finished_utc,
                bytes_transferred,
                duration_ms,
                robocopy_exit_code,
                verification_json,
                error_code,
                error_message
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            ON CONFLICT DO NOTHING
            """,
            _operation_attempt_values(attempt, plan_id=plan_id),
        )
        if cursor.rowcount == 1:
            return 1
        row = self._connection.execute(
            """
            SELECT
                id,
                run_attempt_id,
                run_id,
                plan_id,
                run_target_id,
                operation_id,
                attempt_number,
                state,
                batch_id,
                lease_id,
                ownership_epoch,
                fencing_token,
                source_guard_kind,
                source_guard_evidence_hash,
                transfer_state,
                assurance_level,
                durability_level,
                started_utc,
                finished_utc,
                bytes_transferred,
                duration_ms,
                robocopy_exit_code,
                verification_json,
                error_code,
                error_message
            FROM operation_attempts
            WHERE id = ?
            """,
            (attempt.id,),
        ).fetchone()
        if row is None or tuple(row) != _operation_attempt_values(
            attempt, plan_id=plan_id
        ):
            raise SqliteOperationAuditStoreError(
                "OPERATION_AUDIT_ATTEMPT_IDEMPOTENCY_CONFLICT"
            )
        return 0

    def _insert_operation_outcome(
        self,
        outcome: OperationOutcomeAudit,
        *,
        plan_id: str,
    ) -> bool:
        cursor = self._connection.execute(
            """
            INSERT INTO operation_outcomes (
                run_id,
                plan_id,
                run_target_id,
                operation_id,
                final_state,
                bytes_transferred,
                transfer_state,
                assurance_level,
                hash_evidence_kind,
                durability_level,
                verification_json,
                error_code,
                error_message,
                completed_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            _operation_outcome_values(outcome, plan_id=plan_id),
        )
        if cursor.rowcount == 1:
            return True
        row = self._connection.execute(
            """
            SELECT
                run_id,
                plan_id,
                run_target_id,
                operation_id,
                final_state,
                bytes_transferred,
                transfer_state,
                assurance_level,
                hash_evidence_kind,
                durability_level,
                verification_json,
                error_code,
                error_message,
                completed_utc
            FROM operation_outcomes
            WHERE run_id = ? AND operation_id = ?
            """,
            (outcome.run_id, outcome.operation_id),
        ).fetchone()
        if row is None or tuple(row) != _operation_outcome_values(
            outcome, plan_id=plan_id
        ):
            raise SqliteOperationAuditStoreError(
                "OPERATION_AUDIT_OUTCOME_IDEMPOTENCY_CONFLICT"
            )
        return False


def _audit_run_id(
    *,
    run_attempts: tuple[RunAttemptAudit, ...],
    operation_attempts: tuple[OperationAttemptAudit, ...],
    operation_outcome: OperationOutcomeAudit | None,
) -> str | None:
    run_ids = {attempt.run_id for attempt in run_attempts}
    run_ids.update(attempt.run_id for attempt in operation_attempts)
    if operation_outcome is not None:
        run_ids.add(operation_outcome.run_id)
    if not run_ids:
        return None
    if len(run_ids) != 1:
        raise SqliteOperationAuditStoreError("OPERATION_AUDIT_RUN_SCOPE_MISMATCH")
    return next(iter(run_ids))


def _operation_attempt_values(
    attempt: OperationAttemptAudit,
    *,
    plan_id: str,
) -> tuple[object, ...]:
    return (
        attempt.id,
        attempt.run_attempt_id,
        attempt.run_id,
        plan_id,
        attempt.run_target_id,
        attempt.operation_id,
        attempt.attempt_number,
        attempt.state.value,
        attempt.batch_id,
        attempt.lease_id,
        attempt.ownership_epoch,
        attempt.fencing_token,
        attempt.source_guard_kind,
        attempt.source_guard_evidence_hash,
        attempt.transfer_state,
        attempt.assurance_level,
        attempt.durability_level,
        attempt.started_utc,
        attempt.finished_utc,
        attempt.bytes_transferred,
        attempt.duration_ms,
        attempt.robocopy_exit_code,
        attempt.verification_json,
        attempt.error_code,
        attempt.error_message,
    )


def _operation_outcome_values(
    outcome: OperationOutcomeAudit,
    *,
    plan_id: str,
) -> tuple[object, ...]:
    return (
        outcome.run_id,
        plan_id,
        outcome.run_target_id,
        outcome.operation_id,
        outcome.final_state.value,
        outcome.bytes_transferred,
        outcome.transfer_state,
        outcome.assurance_level,
        outcome.hash_evidence_kind,
        outcome.durability_level,
        outcome.verification_json,
        outcome.error_code,
        outcome.error_message,
        outcome.completed_utc,
    )


def _validate_read_limit(limit: int) -> None:
    if limit < 1:
        raise SqliteOperationAuditStoreError("OPERATION_AUDIT_QUERY_BOUNDS_INVALID")


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SqliteOperationAuditStoreError("OPERATION_AUDIT_QUERY_INTEGER_INVALID")
    return int(value)
