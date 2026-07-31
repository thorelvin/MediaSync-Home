from __future__ import annotations

import sqlite3

from mediasync_home.application.backup_analysis import (
    BackupAnalysisRequest,
    BackupAnalysisRequestState,
    TERMINAL_BACKUP_ANALYSIS_STATES,
)


class SqliteBackupAnalysisRequestError(RuntimeError):
    pass


class SqliteBackupAnalysisRequestStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def enqueue_backup_analysis(
        self,
        request: BackupAnalysisRequest,
    ) -> BackupAnalysisRequest:
        if request.state is not BackupAnalysisRequestState.QUEUED:
            raise ValueError("backup analysis must be queued when enqueued")
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                INSERT INTO backup_analysis_requests (
                    request_id,
                    command_idempotency_key,
                    job_id,
                    job_revision_id,
                    state,
                    requested_utc
                )
                VALUES (?, ?, ?, ?, 'QUEUED', ?)
                """,
                (
                    request.request_id,
                    request.command_idempotency_key,
                    request.job_id,
                    request.job_revision_id,
                    request.requested_utc,
                ),
            )
            recorded = self.load_backup_analysis_request(request.request_id)
            if recorded is None:
                raise SqliteBackupAnalysisRequestError(
                    "BACKUP_ANALYSIS_REQUEST_NOT_RECORDED"
                )
            if owns_transaction:
                self._connection.execute("COMMIT")
            return recorded
        except sqlite3.IntegrityError:
            existing = self.load_backup_analysis_request(request.request_id)
            if existing is None or (
                existing.command_idempotency_key,
                existing.job_id,
                existing.job_revision_id,
            ) != (
                request.command_idempotency_key,
                request.job_id,
                request.job_revision_id,
            ):
                if owns_transaction:
                    _rollback(self._connection)
                raise SqliteBackupAnalysisRequestError(
                    "BACKUP_ANALYSIS_REQUEST_CONFLICT"
                ) from None
            if owns_transaction:
                self._connection.execute("COMMIT")
            return existing
        except Exception:
            if owns_transaction:
                _rollback(self._connection)
            raise

    def load_backup_analysis_request(
        self,
        request_id: str,
    ) -> BackupAnalysisRequest | None:
        row = self._connection.execute(
            f"""
            SELECT {_REQUEST_COLUMNS}
            FROM backup_analysis_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        return None if row is None else _request_from_row(row)

    def claim_next_backup_analysis(
        self,
        *,
        started_utc: str,
    ) -> BackupAnalysisRequest | None:
        if self._connection.in_transaction:
            raise SqliteBackupAnalysisRequestError(
                "BACKUP_ANALYSIS_CLAIM_REQUIRES_IDLE_CONNECTION"
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT request_id
                FROM backup_analysis_requests
                WHERE state = 'QUEUED'
                ORDER BY requested_utc, request_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            request_id = str(row[0])
            cursor = self._connection.execute(
                """
                UPDATE backup_analysis_requests
                SET
                    state = 'RUNNING',
                    started_utc = ?,
                    row_version = row_version + 1
                WHERE request_id = ?
                    AND state = 'QUEUED'
                """,
                (started_utc, request_id),
            )
            if cursor.rowcount != 1:
                raise SqliteBackupAnalysisRequestError(
                    "BACKUP_ANALYSIS_CLAIM_CONFLICT"
                )
            claimed = self.load_backup_analysis_request(request_id)
            self._connection.execute("COMMIT")
        except Exception:
            _rollback(self._connection)
            raise
        if claimed is None:
            raise SqliteBackupAnalysisRequestError(
                "BACKUP_ANALYSIS_CLAIM_NOT_RECORDED"
            )
        return claimed

    def complete_backup_analysis(
        self,
        *,
        request_id: str,
        state: BackupAnalysisRequestState,
        completed_utc: str,
        analysis_id: str | None,
        plan_id: str | None,
        reason_code: str,
        operation_count: int,
        planned_bytes: int,
    ) -> BackupAnalysisRequest:
        if state not in TERMINAL_BACKUP_ANALYSIS_STATES:
            raise ValueError("backup analysis completion state must be terminal")
        if operation_count < 0 or planned_bytes < 0 or not reason_code:
            raise ValueError("backup analysis completion values are invalid")
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE backup_analysis_requests
                SET
                    state = ?,
                    completed_utc = ?,
                    analysis_id = ?,
                    plan_id = ?,
                    reason_code = ?,
                    operation_count = ?,
                    planned_bytes = ?,
                    row_version = row_version + 1
                WHERE request_id = ?
                    AND state = 'RUNNING'
                """,
                (
                    state.value,
                    completed_utc,
                    analysis_id,
                    plan_id,
                    reason_code,
                    operation_count,
                    planned_bytes,
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteBackupAnalysisRequestError(
                    "BACKUP_ANALYSIS_COMPLETION_CONFLICT"
                )
            completed = self.load_backup_analysis_request(request_id)
            if completed is None:
                raise SqliteBackupAnalysisRequestError(
                    "BACKUP_ANALYSIS_COMPLETION_NOT_RECORDED"
                )
            if owns_transaction:
                self._connection.execute("COMMIT")
        except Exception:
            if owns_transaction:
                _rollback(self._connection)
            raise
        return completed

    def requeue_interrupted_backup_analyses(self) -> int:
        if self._connection.in_transaction:
            raise SqliteBackupAnalysisRequestError(
                "BACKUP_ANALYSIS_REQUEUE_REQUIRES_IDLE_CONNECTION"
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE backup_analysis_requests
                SET
                    state = 'QUEUED',
                    started_utc = NULL,
                    row_version = row_version + 1
                WHERE state = 'RUNNING'
                """
            )
            count = max(0, cursor.rowcount)
            self._connection.execute("COMMIT")
        except Exception:
            _rollback(self._connection)
            raise
        return count


_REQUEST_COLUMNS = """
    request_id,
    command_idempotency_key,
    job_id,
    job_revision_id,
    state,
    requested_utc,
    started_utc,
    completed_utc,
    analysis_id,
    plan_id,
    reason_code,
    operation_count,
    planned_bytes,
    row_version
"""


def _request_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> BackupAnalysisRequest:
    return BackupAnalysisRequest(
        request_id=str(row[0]),
        command_idempotency_key=str(row[1]),
        job_id=str(row[2]),
        job_revision_id=str(row[3]),
        state=BackupAnalysisRequestState(str(row[4])),
        requested_utc=str(row[5]),
        started_utc=None if row[6] is None else str(row[6]),
        completed_utc=None if row[7] is None else str(row[7]),
        analysis_id=None if row[8] is None else str(row[8]),
        plan_id=None if row[9] is None else str(row[9]),
        reason_code=None if row[10] is None else str(row[10]),
        operation_count=_required_int(row[11]),
        planned_bytes=_required_int(row[12]),
        row_version=_required_int(row[13]),
    )


def _required_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("backup analysis integer column is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("backup analysis integer column is invalid")


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.execute("ROLLBACK")
