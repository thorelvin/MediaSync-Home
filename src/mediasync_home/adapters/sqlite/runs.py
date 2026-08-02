from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediasync_home.adapters.system_clock import SystemClock
from mediasync_home.application.activity_read_models import (
    RunActivityReadModelStore,
    RunActivitySummary,
    RunTargetActivitySummary,
)
from mediasync_home.application.clocks import ClockPort
from mediasync_home.application.endpoint_retry import (
    EndpointRetryViolation,
    MonotonicEndpointRetryScheduler,
    endpoint_retry_backoff_ms,
)
from mediasync_home.application.progress_read_models import (
    MAX_PROGRESS_SNAPSHOT_TARGETS,
    ProgressSnapshotQueryError,
    RunProgressSnapshot,
    RunProgressSnapshotStore,
    RunTargetProgressSnapshot,
)
from mediasync_home.application.runs import (
    RunState,
    RunStopRequest,
    RunStore,
    RunTargetStopProgress,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)


class SqliteRunStoreError(ValueError):
    pass


_MAX_ENDPOINT_RETRY_CANDIDATES = 128


class SqliteRunStore(RunStore, RunActivityReadModelStore, RunProgressSnapshotStore):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: ClockPort | None = None,
        endpoint_retry_scheduler: MonotonicEndpointRetryScheduler | None = None,
    ) -> None:
        if clock is not None and endpoint_retry_scheduler is not None:
            raise SqliteRunStoreError("RUN_STORE_RETRY_CLOCK_IS_AMBIGUOUS")
        self._connection = connection
        self._endpoint_retries = (
            endpoint_retry_scheduler
            or MonotonicEndpointRetryScheduler(clock or SystemClock())
        )

    def save_started_run(self, run: StartedRun) -> None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                INSERT INTO runs (
                    id,
                    job_id,
                    job_revision_id,
                    plan_id,
                    command_request_id,
                    command_receipt_id,
                    trigger_occurrence_id,
                    logical_run_group_id,
                    resumed_from_run_id,
                    trigger_type,
                    state,
                    summary_json,
                    warning_count,
                    error_count,
                    app_version,
                    plan_checksum,
                    idempotency_key,
                    planned_operations,
                    planned_bytes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.job_id,
                    run.job_revision_id,
                    run.plan_id,
                    run.command_request_id,
                    run.command_receipt_id,
                    run.trigger_occurrence_id,
                    run.logical_run_group_id,
                    run.resumed_from_run_id,
                    run.trigger_type.value,
                    run.state.value,
                    _json_dump(run.summary),
                    run.warning_count,
                    run.error_count,
                    run.app_version,
                    run.plan_checksum,
                    run.idempotency_key,
                    run.planned_operations,
                    run.planned_bytes,
                ),
            )
            for target in run.targets:
                self._connection.execute(
                    """
                    INSERT INTO run_targets (
                        id,
                        run_id,
                        endpoint_id,
                        endpoint_revision_id,
                        required_owner_installation_id,
                        required_ownership_epoch,
                        state,
                        lease_resource_key,
                        planned_operations,
                        planned_bytes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target.run_target_id,
                        run.run_id,
                        target.endpoint_id,
                        target.endpoint_revision_id,
                        target.required_owner_installation_id,
                        target.required_ownership_epoch,
                        target.state.value,
                        target.lease_resource_key,
                        target.planned_operations,
                        target.planned_bytes,
                    ),
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRunStoreError("RUN_PERSISTENCE_FAILED") from exc

    def load_started_run(self, run_id: str) -> StartedRun | None:
        return self._load_one(
            """
            SELECT
                id,
                job_id,
                job_revision_id,
                plan_id,
                command_request_id,
                command_receipt_id,
                trigger_occurrence_id,
                logical_run_group_id,
                resumed_from_run_id,
                trigger_type,
                state,
                summary_json,
                warning_count,
                error_count,
                app_version,
                plan_checksum,
                idempotency_key,
                planned_operations,
                planned_bytes
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        )

    def load_started_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> StartedRun | None:
        return self._load_one(
            """
            SELECT
                id,
                job_id,
                job_revision_id,
                plan_id,
                command_request_id,
                command_receipt_id,
                trigger_occurrence_id,
                logical_run_group_id,
                resumed_from_run_id,
                trigger_type,
                state,
                summary_json,
                warning_count,
                error_count,
                app_version,
                plan_checksum,
                idempotency_key,
                planned_operations,
                planned_bytes
            FROM runs
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        )

    def load_active_run_for_job(self, job_id: str) -> StartedRun | None:
        return self._load_one(
            """
            SELECT
                id,
                job_id,
                job_revision_id,
                plan_id,
                command_request_id,
                command_receipt_id,
                trigger_occurrence_id,
                logical_run_group_id,
                resumed_from_run_id,
                trigger_type,
                state,
                summary_json,
                warning_count,
                error_count,
                app_version,
                plan_checksum,
                idempotency_key,
                planned_operations,
                planned_bytes
            FROM runs
            WHERE job_id = ?
                AND state IN (
                    'CREATED',
                    'QUEUED',
                    'PREFLIGHT',
                    'EXECUTING',
                    'PAUSING',
                    'PAUSED',
                    'RECOVERY_REQUIRED'
                )
            ORDER BY started_utc, id
            LIMIT 1
            """,
            (job_id,),
        )

    def request_run_pause(self, run_id: str) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = 'PAUSING',
                    row_version = row_version + 1
                WHERE id = ?
                    AND state IN ('CREATED', 'QUEUED', 'PREFLIGHT', 'EXECUTING')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM run_stop_requests
                        WHERE run_stop_requests.run_id = runs.id
                            AND run_stop_requests.state = 'PENDING'
                    )
                """,
                (run_id,),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_PAUSE_REQUEST_FAILED") from exc

    def load_next_pausing_run(self) -> StartedRun | None:
        return self._load_one(
            """
            SELECT
                id,
                job_id,
                job_revision_id,
                plan_id,
                command_request_id,
                command_receipt_id,
                trigger_occurrence_id,
                logical_run_group_id,
                resumed_from_run_id,
                trigger_type,
                state,
                summary_json,
                warning_count,
                error_count,
                app_version,
                plan_checksum,
                idempotency_key,
                planned_operations,
                planned_bytes
            FROM runs
            WHERE state = 'PAUSING'
            ORDER BY started_utc, id
            LIMIT 1
            """,
            (),
        )

    def finalize_requested_run_pause(self, run_id: str) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'PAUSED',
                    last_lease_id = NULL,
                    last_ownership_epoch = NULL,
                    last_fencing_token = NULL,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND state IN (
                        'ACQUIRING_LEASE',
                        'REVALIDATING',
                        'EXECUTING',
                        'WAITING_FOR_ENDPOINT'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state = 'PAUSING'
                    )
                """,
                (run_id,),
            )
            cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = 'PAUSED',
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'PAUSING'
                """,
                (run_id,),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_PAUSE_BOUNDARY_FAILED") from exc

    def resume_paused_run(self, run_id: str) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'PENDING',
                    last_lease_id = NULL,
                    last_ownership_epoch = NULL,
                    last_fencing_token = NULL,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND state = 'PAUSED'
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state = 'PAUSED'
                    )
                """,
                (run_id,),
            )
            cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = 'QUEUED',
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'PAUSED'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM run_stop_requests
                        WHERE run_stop_requests.run_id = runs.id
                            AND run_stop_requests.state = 'PENDING'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM run_targets
                        WHERE run_targets.run_id = runs.id
                            AND run_targets.state = 'PENDING'
                    )
                """,
                (run_id,),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_RESUME_FAILED") from exc

    def request_run_stop_after_active_file(self, run_id: str) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                INSERT INTO run_stop_requests (
                    run_id,
                    mode,
                    state
                )
                SELECT id, 'AFTER_ACTIVE_FILE', 'PENDING'
                FROM runs
                WHERE id = ?
                    AND state IN (
                        'CREATED',
                        'QUEUED',
                        'PREFLIGHT',
                        'EXECUTING',
                        'PAUSING',
                        'PAUSED'
                    )
                ON CONFLICT (run_id) DO NOTHING
                """,
                (run_id,),
            )
            if cursor.rowcount == 1:
                self._connection.execute(
                    """
                    UPDATE runs
                    SET row_version = row_version + 1
                    WHERE id = ?
                    """,
                    (run_id,),
                )
            else:
                existing = self.load_run_stop_request(run_id)
                if existing is None:
                    if not outer_transaction:
                        self._connection.execute("ROLLBACK")
                    return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_STOP_REQUEST_FAILED") from exc

    def load_run_stop_request(self, run_id: str) -> RunStopRequest | None:
        row = self._connection.execute(
            """
            SELECT
                run_id,
                boundary_run_target_id,
                boundary_operation_id
            FROM run_stop_requests
            WHERE run_id = ?
                AND state = 'PENDING'
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunStopRequest(
            run_id=str(row[0]),
            boundary_run_target_id=None if row[1] is None else str(row[1]),
            boundary_operation_id=None if row[2] is None else str(row[2]),
        )

    def load_next_requested_run_stop(self) -> RunStopRequest | None:
        row = self._connection.execute(
            """
            SELECT
                run_id,
                boundary_run_target_id,
                boundary_operation_id
            FROM run_stop_requests
            WHERE state = 'PENDING'
            ORDER BY requested_utc, run_id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return RunStopRequest(
            run_id=str(row[0]),
            boundary_run_target_id=None if row[1] is None else str(row[1]),
            boundary_operation_id=None if row[2] is None else str(row[2]),
        )

    def bind_requested_run_stop_boundary(
        self,
        *,
        run_id: str,
        run_target_id: str,
        operation_id: str,
    ) -> RunStopRequest | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE run_stop_requests
                SET
                    boundary_run_target_id = ?,
                    boundary_operation_id = ?,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND state = 'PENDING'
                    AND boundary_run_target_id IS NULL
                    AND boundary_operation_id IS NULL
                """,
                (run_target_id, operation_id, run_id),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            request = self.load_run_stop_request(run_id)
            if request is None:
                raise SqliteRunStoreError("RUN_STOP_REQUEST_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return request
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_STOP_BOUNDARY_BIND_FAILED") from exc

    def activate_requested_run_stop(self, run_id: str) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            run = self.load_started_run(run_id)
            stop_request = self.load_run_stop_request(run_id)
            if run is None or stop_request is None:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            if run.state is RunState.PAUSED:
                if stop_request.boundary_run_target_id is None:
                    if not outer_transaction:
                        self._connection.execute("ROLLBACK")
                    return None
                target_cursor = self._connection.execute(
                    """
                    UPDATE run_targets
                    SET
                        state = 'PENDING',
                        last_lease_id = NULL,
                        last_ownership_epoch = NULL,
                        last_fencing_token = NULL,
                        row_version = row_version + 1
                    WHERE run_id = ?
                        AND id = ?
                        AND state = 'PAUSED'
                    """,
                    (run_id, stop_request.boundary_run_target_id),
                )
                if target_cursor.rowcount != 1:
                    if not outer_transaction:
                        self._connection.execute("ROLLBACK")
                    return None
                self._connection.execute(
                    """
                    UPDATE runs
                    SET
                        state = 'QUEUED',
                        row_version = row_version + 1
                    WHERE id = ?
                        AND state = 'PAUSED'
                    """,
                    (run_id,),
                )
            elif run.state is RunState.PAUSING:
                active_state = self._connection.execute(
                    """
                    SELECT state
                    FROM run_targets
                    WHERE run_id = ?
                        AND state IN ('EXECUTING', 'REVALIDATING', 'ACQUIRING_LEASE')
                    ORDER BY
                        CASE state
                            WHEN 'EXECUTING' THEN 0
                            WHEN 'REVALIDATING' THEN 1
                            ELSE 2
                        END
                    LIMIT 1
                    """,
                    (run_id,),
                ).fetchone()
                next_state = (
                    "EXECUTING"
                    if active_state is not None and str(active_state[0]) == "EXECUTING"
                    else "PREFLIGHT"
                    if active_state is not None
                    else "QUEUED"
                )
                self._connection.execute(
                    """
                    UPDATE runs
                    SET
                        state = ?,
                        row_version = row_version + 1
                    WHERE id = ?
                        AND state = 'PAUSING'
                    """,
                    (next_state, run_id),
                )
            activated = self.load_started_run(run_id)
            if activated is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return activated
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_STOP_ACTIVATION_FAILED") from exc

    def finalize_requested_run_stop(
        self,
        *,
        run_id: str,
        target_progress: tuple[RunTargetStopProgress, ...],
    ) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            run = self.load_started_run(run_id)
            if run is None or self.load_run_stop_request(run_id) is None:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            progress_by_target = {item.run_target_id: item for item in target_progress}
            for target in run.targets:
                progress = progress_by_target.get(target.run_target_id)
                completed_operations = max(
                    target.completed_operations,
                    0 if progress is None else progress.completed_operations,
                )
                completed_bytes = max(
                    target.completed_bytes,
                    0 if progress is None else progress.completed_bytes,
                )
                self._connection.execute(
                    """
                    UPDATE run_targets
                    SET
                        state = 'CANCELLED',
                        completed_operations = ?,
                        completed_bytes = ?,
                        last_lease_id = NULL,
                        last_ownership_epoch = NULL,
                        last_fencing_token = NULL,
                        finished_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        result_json = ?,
                        row_version = row_version + 1
                    WHERE run_id = ?
                        AND id = ?
                        AND state IN (
                            'PENDING',
                            'ACQUIRING_LEASE',
                            'REVALIDATING',
                            'EXECUTING',
                            'PAUSED',
                            'WAITING_FOR_ENDPOINT',
                            'NEEDS_REVIEW'
                        )
                    """,
                    (
                        completed_operations,
                        completed_bytes,
                        _json_dump({"reason": "USER_STOP_AFTER_ACTIVE_FILE"}),
                        run_id,
                        target.run_target_id,
                    ),
                )
            summary = dict(run.summary)
            summary["result"] = "STOPPED"
            summary["stop_mode"] = "AFTER_ACTIVE_FILE"
            cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = 'CANCELLED',
                    finished_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    summary_json = ?,
                    row_version = row_version + 1
                WHERE id = ?
                    AND state IN (
                        'CREATED',
                        'QUEUED',
                        'PREFLIGHT',
                        'EXECUTING',
                        'PAUSING',
                        'PAUSED'
                    )
                """,
                (_json_dump(summary), run_id),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            self._connection.execute(
                """
                UPDATE run_stop_requests
                SET
                    state = 'APPLIED',
                    applied_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND state = 'PENDING'
                """,
                (run_id,),
            )
            stopped = self.load_started_run(run_id)
            if stopped is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return stopped
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_STOP_FINALIZE_FAILED") from exc

    def load_next_runnable_run(self) -> StartedRun | None:
        return self._load_one(
            """
            SELECT
                id,
                job_id,
                job_revision_id,
                plan_id,
                command_request_id,
                command_receipt_id,
                trigger_occurrence_id,
                logical_run_group_id,
                resumed_from_run_id,
                trigger_type,
                state,
                summary_json,
                warning_count,
                error_count,
                app_version,
                plan_checksum,
                idempotency_key,
                planned_operations,
                planned_bytes
            FROM runs
            WHERE state IN ('QUEUED', 'PREFLIGHT', 'EXECUTING')
                AND EXISTS (
                    SELECT 1
                    FROM run_targets
                    WHERE run_targets.run_id = runs.id
                        AND run_targets.state = 'PENDING'
                )
            ORDER BY started_utc, id
            LIMIT 1
            """,
            (),
        )

    def load_next_revalidating_run_target_key(self) -> tuple[str, str] | None:
        row = self._connection.execute(
            """
            SELECT runs.id, run_targets.id
            FROM run_targets
            JOIN runs ON runs.id = run_targets.run_id
            WHERE runs.state IN ('PREFLIGHT', 'EXECUTING')
                AND run_targets.state = 'REVALIDATING'
            ORDER BY runs.started_utc, runs.id, run_targets.id
            LIMIT 1
            """,
            (),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), str(row[1])

    def load_next_executing_run_target_key(self) -> tuple[str, str] | None:
        row = self._connection.execute(
            """
            SELECT runs.id, run_targets.id
            FROM run_targets
            JOIN runs ON runs.id = run_targets.run_id
            WHERE runs.state = 'EXECUTING'
                AND run_targets.state = 'EXECUTING'
            ORDER BY runs.started_utc, runs.id, run_targets.id
            LIMIT 1
            """,
            (),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), str(row[1])

    def list_recent_run_activity_summaries(
        self,
        *,
        limit: int,
        offset: int,
        job_id: str | None = None,
    ) -> tuple[RunActivitySummary, ...]:
        if limit < 1 or offset < 0:
            raise SqliteRunStoreError("RUN_ACTIVITY_QUERY_BOUNDS_INVALID")
        if job_id is None:
            rows = self._connection.execute(
                """
                SELECT
                    id,
                    job_id,
                    job_revision_id,
                    plan_id,
                    trigger_type,
                    state,
                    started_utc,
                    finished_utc,
                    planned_operations,
                    planned_bytes,
                    warning_count,
                    error_count,
                    summary_json
                FROM runs
                ORDER BY started_utc DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT
                    id,
                    job_id,
                    job_revision_id,
                    plan_id,
                    trigger_type,
                    state,
                    started_utc,
                    finished_utc,
                    planned_operations,
                    planned_bytes,
                    warning_count,
                    error_count,
                    summary_json
                FROM runs
                WHERE job_id = ?
                ORDER BY started_utc DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (job_id, limit, offset),
            ).fetchall()
        return tuple(self._run_activity_summary(row) for row in rows)

    def _run_activity_summary(
        self,
        row: sqlite3.Row | tuple[Any, ...],
    ) -> RunActivitySummary:
        action_required, deferred_count, deferred_bytes = _run_action_metadata(
            str(row[12])
        )
        return RunActivitySummary(
            run_id=str(row[0]),
            job_id=str(row[1]),
            job_revision_id=str(row[2]),
            plan_id=str(row[3]),
            trigger_type=RunTriggerType(str(row[4])),
            state=RunState(str(row[5])),
            started_utc=str(row[6]),
            finished_utc=None if row[7] is None else str(row[7]),
            planned_operations=int(row[8]),
            planned_bytes=int(row[9]),
            warning_count=int(row[10]),
            error_count=int(row[11]),
            action_required=action_required,
            deferred_operation_count=deferred_count,
            deferred_planned_bytes=deferred_bytes,
            targets=self._load_target_activity_summaries(str(row[0])),
        )

    def load_run_progress_snapshot(self, run_id: str) -> RunProgressSnapshot | None:
        rows = self._connection.execute(
            """
            SELECT
                runs.id,
                runs.job_id,
                runs.job_revision_id,
                runs.plan_id,
                runs.state,
                runs.started_utc,
                runs.finished_utc,
                runs.planned_operations,
                runs.planned_bytes,
                runs.warning_count,
                runs.error_count,
                runs.row_version,
                run_targets.id,
                run_targets.endpoint_id,
                run_targets.endpoint_revision_id,
                run_targets.state,
                run_targets.planned_operations,
                run_targets.completed_operations,
                run_targets.planned_bytes,
                run_targets.completed_bytes,
                run_targets.warning_count,
                run_targets.error_count,
                run_targets.row_version,
                (
                    SELECT count(*)
                    FROM run_target_endpoint_wait_events AS wait_events
                    WHERE wait_events.run_id = run_targets.run_id
                        AND wait_events.run_target_id = run_targets.id
                ),
                (
                    SELECT coalesce(sum(wait_events.backoff_ms), 0)
                    FROM run_target_endpoint_wait_events AS wait_events
                    WHERE wait_events.run_id = run_targets.run_id
                        AND wait_events.run_target_id = run_targets.id
                ),
                (
                    SELECT wait_events.backoff_ms
                    FROM run_target_endpoint_wait_events AS wait_events
                    WHERE wait_events.run_id = run_targets.run_id
                        AND wait_events.run_target_id = run_targets.id
                    ORDER BY wait_events.id DESC
                    LIMIT 1
                ),
                (
                    SELECT wait_events.retry_not_before_utc
                    FROM run_target_endpoint_wait_events AS wait_events
                    WHERE wait_events.run_id = run_targets.run_id
                        AND wait_events.run_target_id = run_targets.id
                    ORDER BY wait_events.id DESC
                    LIMIT 1
                ),
                (
                    SELECT wait_events.reason_code
                    FROM run_target_endpoint_wait_events AS wait_events
                    WHERE wait_events.run_id = run_targets.run_id
                        AND wait_events.run_target_id = run_targets.id
                    ORDER BY wait_events.id DESC
                    LIMIT 1
                ),
                (
                    SELECT min(wait_events.observed_utc)
                    FROM run_target_endpoint_wait_events AS wait_events
                    WHERE wait_events.run_id = run_targets.run_id
                        AND wait_events.run_target_id = run_targets.id
                ),
                runs.summary_json
            FROM runs
            LEFT JOIN run_targets ON run_targets.run_id = runs.id
            WHERE runs.id = ?
            ORDER BY run_targets.id
            LIMIT ?
            """,
            (run_id, MAX_PROGRESS_SNAPSHOT_TARGETS + 1),
        ).fetchall()
        if not rows:
            return None
        target_rows = tuple(row for row in rows if row[12] is not None)
        if len(target_rows) > MAX_PROGRESS_SNAPSHOT_TARGETS:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_TARGET_LIMIT_EXCEEDED")
        stop_row = self._connection.execute(
            """
            SELECT state, row_version
            FROM run_stop_requests
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        stop_requested = stop_row is not None and str(stop_row[0]) == "PENDING"
        stop_sequence = 0 if stop_row is None else int(stop_row[1])

        targets = tuple(
            RunTargetProgressSnapshot(
                run_target_id=str(target_row[12]),
                endpoint_id=str(target_row[13]),
                endpoint_revision_id=str(target_row[14]),
                state=RunTargetState(str(target_row[15])),
                planned_operations=int(target_row[16]),
                completed_operations=int(target_row[17]),
                planned_bytes=int(target_row[18]),
                completed_bytes=int(target_row[19]),
                warning_count=int(target_row[20]),
                error_count=int(target_row[21]),
                endpoint_wait_attempts=int(target_row[23]),
                endpoint_wait_total_backoff_ms=int(target_row[24]),
                endpoint_retry_backoff_ms=(
                    None if target_row[25] is None else int(target_row[25])
                ),
                endpoint_retry_not_before_utc=(
                    None if target_row[26] is None else str(target_row[26])
                ),
                endpoint_wait_reason_code=(
                    None if target_row[27] is None else str(target_row[27])
                ),
                endpoint_wait_started_utc=(
                    None if target_row[28] is None else str(target_row[28])
                ),
            )
            for target_row in target_rows
        )
        row = rows[0]
        state = RunState(str(row[4]))
        action_required, deferred_count, deferred_bytes = _run_action_metadata(
            str(row[29])
        )
        return RunProgressSnapshot(
            run_id=str(row[0]),
            job_id=str(row[1]),
            job_revision_id=str(row[2]),
            plan_id=str(row[3]),
            sequence_no=(
                int(row[11])
                + sum(int(target_row[22]) for target_row in target_rows)
                + stop_sequence
            ),
            state=state,
            terminal=state in _TERMINAL_RUN_STATES,
            started_utc=str(row[5]),
            finished_utc=None if row[6] is None else str(row[6]),
            planned_operations=int(row[7]),
            completed_operations=sum(target.completed_operations for target in targets),
            planned_bytes=int(row[8]),
            completed_bytes=sum(target.completed_bytes for target in targets),
            warning_count=int(row[9]),
            error_count=int(row[10]),
            action_required=action_required,
            deferred_operation_count=deferred_count,
            deferred_planned_bytes=deferred_bytes,
            targets=targets,
            stop_requested=stop_requested,
        )

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        row = self._connection.execute(
            """
            SELECT id
            FROM run_targets
            WHERE run_id = ?
                AND state = 'PENDING'
            ORDER BY id
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._load_target(run_id=run_id, run_target_id=str(row[0]))

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            target_cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'ACQUIRING_LEASE',
                    started_utc = COALESCE(started_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'PENDING'
                    AND lease_resource_key IS NOT NULL
                    AND trim(lease_resource_key) != ''
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state IN ('QUEUED', 'PREFLIGHT', 'EXECUTING')
                    )
                """,
                (run_id, run_target_id),
            )
            if target_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run_cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = CASE
                        WHEN state = 'EXECUTING' THEN 'EXECUTING'
                        ELSE 'PREFLIGHT'
                    END,
                    row_version = row_version + 1
                WHERE id = ?
                    AND state IN ('QUEUED', 'PREFLIGHT', 'EXECUTING')
                """,
                (run_id,),
            )
            if run_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            target = self._load_target(run_id=run_id, run_target_id=run_target_id)
            if target is None:
                raise SqliteRunStoreError("RUN_TARGET_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return target
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_PREFLIGHT_FAILED") from exc

    def record_run_target_waiting_for_endpoint(
        self,
        *,
        run_id: str,
        run_target_id: str,
        expected_state: RunTargetState,
        reason_code: str,
    ) -> StartedRunTarget | None:
        normalized_reason = reason_code.strip()
        if (
            expected_state
            not in {
                RunTargetState.ACQUIRING_LEASE,
                RunTargetState.REVALIDATING,
                RunTargetState.EXECUTING,
            }
            or not normalized_reason
        ):
            return None
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            attempt_row = self._connection.execute(
                """
                SELECT coalesce(max(attempt_no), 0) + 1
                FROM run_target_endpoint_wait_events
                WHERE run_id = ?
                    AND run_target_id = ?
                """,
                (run_id, run_target_id),
            ).fetchone()
            if attempt_row is None:
                raise SqliteRunStoreError(
                    "RUN_TARGET_ENDPOINT_WAIT_ATTEMPT_LOAD_FAILED"
                )
            attempt_no = int(attempt_row[0])
            backoff_ms = endpoint_retry_backoff_ms(
                run_id=run_id,
                run_target_id=run_target_id,
                attempt_no=attempt_no,
            )
            timing = self._endpoint_retries.plan(backoff_ms=backoff_ms)
            cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'WAITING_FOR_ENDPOINT',
                    last_lease_id = NULL,
                    last_ownership_epoch = NULL,
                    last_fencing_token = NULL,
                    result_json = ?,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = ?
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state IN ('PREFLIGHT', 'EXECUTING')
                    )
                """,
                (
                    _json_dump(
                        {
                            "endpoint_retry_backoff_ms": timing.backoff_ms,
                            "endpoint_retry_not_before_utc": (
                                timing.retry_not_before_utc
                            ),
                            "endpoint_wait_attempts": attempt_no,
                            "last_endpoint_error_code": normalized_reason,
                            "status": "WAITING_FOR_ENDPOINT",
                        }
                    ),
                    run_id,
                    run_target_id,
                    expected_state.value,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            event_cursor = self._connection.execute(
                """
                INSERT INTO run_target_endpoint_wait_events (
                    run_id,
                    run_target_id,
                    attempt_no,
                    reason_code,
                    observed_utc,
                    backoff_ms,
                    retry_not_before_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_target_id,
                    attempt_no,
                    normalized_reason,
                    timing.observed_utc,
                    timing.backoff_ms,
                    timing.retry_not_before_utc,
                ),
            )
            event_id = event_cursor.lastrowid
            if event_id is None:
                raise SqliteRunStoreError("RUN_TARGET_ENDPOINT_WAIT_EVENT_ID_MISSING")
            target = self._load_target(run_id=run_id, run_target_id=run_target_id)
            if target is None:
                raise SqliteRunStoreError("RUN_TARGET_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            self._endpoint_retries.activate(event_id=event_id, timing=timing)
            return target
        except (sqlite3.Error, SqliteRunStoreError, EndpointRetryViolation) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_ENDPOINT_WAIT_RECORD_FAILED") from exc

    def requeue_next_due_waiting_run_target(self) -> StartedRunTarget | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                """
                SELECT
                    run_targets.run_id,
                    run_targets.id,
                    events.id,
                    events.backoff_ms,
                    events.retry_not_before_utc
                FROM run_targets
                INNER JOIN runs ON runs.id = run_targets.run_id
                INNER JOIN run_target_endpoint_wait_events AS events
                    ON events.id = (
                        SELECT max(latest.id)
                        FROM run_target_endpoint_wait_events AS latest
                        WHERE latest.run_id = run_targets.run_id
                            AND latest.run_target_id = run_targets.id
                    )
                WHERE run_targets.state = 'WAITING_FOR_ENDPOINT'
                    AND runs.state IN ('QUEUED', 'PREFLIGHT', 'EXECUTING')
                ORDER BY events.id, run_targets.run_id, run_targets.id
                LIMIT ?
                """,
                (_MAX_ENDPOINT_RETRY_CANDIDATES,),
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in rows
                    if self._endpoint_retries.is_due(
                        event_id=int(candidate[2]),
                        backoff_ms=int(candidate[3]),
                        retry_not_before_utc=str(candidate[4]),
                    )
                ),
                None,
            )
            if row is None:
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return None
            run_id, run_target_id = str(row[0]), str(row[1])
            event_id = int(row[2])
            cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'PENDING',
                    result_json = NULL,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'WAITING_FOR_ENDPOINT'
                """,
                (run_id, run_target_id),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            target = self._load_target(run_id=run_id, run_target_id=run_target_id)
            if target is None:
                raise SqliteRunStoreError("RUN_TARGET_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            self._endpoint_retries.discard(event_id=event_id)
            return target
        except (sqlite3.Error, SqliteRunStoreError, EndpointRetryViolation) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_ENDPOINT_REQUEUE_FAILED") from exc

    def record_run_target_lease_acquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'REVALIDATING',
                    last_lease_id = ?,
                    last_ownership_epoch = ?,
                    last_fencing_token = ?,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'ACQUIRING_LEASE'
                    AND (required_owner_installation_id IS NULL OR required_owner_installation_id = ?)
                    AND (required_ownership_epoch IS NULL OR required_ownership_epoch = ?)
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state IN ('PREFLIGHT', 'EXECUTING')
                    )
                """,
                (
                    lease_id,
                    ownership_epoch,
                    fencing_token,
                    run_id,
                    run_target_id,
                    owner_installation_id,
                    ownership_epoch,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            target = self._load_target(run_id=run_id, run_target_id=run_target_id)
            if target is None:
                raise SqliteRunStoreError("RUN_TARGET_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return target
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_LEASE_RECORD_FAILED") from exc

    def record_run_target_lease_reacquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        expected_lease_id: str,
        expected_ownership_epoch: int,
        expected_fencing_token: int,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    last_lease_id = ?,
                    last_ownership_epoch = ?,
                    last_fencing_token = ?,
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state IN ('REVALIDATING', 'EXECUTING')
                    AND last_lease_id = ?
                    AND last_ownership_epoch = ?
                    AND last_fencing_token = ?
                    AND (required_owner_installation_id IS NULL OR required_owner_installation_id = ?)
                    AND (required_ownership_epoch IS NULL OR required_ownership_epoch = ?)
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND (
                                (
                                    run_targets.state = 'REVALIDATING'
                                    AND runs.state IN ('PREFLIGHT', 'EXECUTING')
                                )
                                OR (run_targets.state = 'EXECUTING' AND runs.state = 'EXECUTING')
                            )
                    )
                """,
                (
                    lease_id,
                    ownership_epoch,
                    fencing_token,
                    run_id,
                    run_target_id,
                    expected_lease_id,
                    expected_ownership_epoch,
                    expected_fencing_token,
                    owner_installation_id,
                    ownership_epoch,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            target = self._load_target(run_id=run_id, run_target_id=run_target_id)
            if target is None:
                raise SqliteRunStoreError("RUN_TARGET_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return target
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_LEASE_REACQUIRE_FAILED") from exc

    def record_run_target_execution_started(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            target_cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'EXECUTING',
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'REVALIDATING'
                    AND last_lease_id = ?
                    AND last_ownership_epoch = ?
                    AND last_fencing_token = ?
                    AND (required_owner_installation_id IS NULL OR required_owner_installation_id = ?)
                    AND (required_ownership_epoch IS NULL OR required_ownership_epoch = ?)
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state IN ('PREFLIGHT', 'EXECUTING')
                    )
                """,
                (
                    run_id,
                    run_target_id,
                    lease_id,
                    ownership_epoch,
                    fencing_token,
                    owner_installation_id,
                    ownership_epoch,
                ),
            )
            if target_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run_cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = 'EXECUTING',
                    row_version = row_version + 1
                WHERE id = ?
                    AND state IN ('PREFLIGHT', 'EXECUTING')
                """,
                (run_id,),
            )
            if run_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            target = self._load_target(run_id=run_id, run_target_id=run_target_id)
            if target is None:
                raise SqliteRunStoreError("RUN_TARGET_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return target
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_EXECUTION_START_FAILED") from exc

    def record_run_target_succeeded(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
    ) -> StartedRun | None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            target_cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'SUCCEEDED',
                    completed_operations = ?,
                    completed_bytes = ?,
                    finished_utc = COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'EXECUTING'
                    AND planned_operations = ?
                    AND planned_bytes = ?
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state = 'EXECUTING'
                    )
                """,
                (
                    completed_operations,
                    completed_bytes,
                    run_id,
                    run_target_id,
                    completed_operations,
                    completed_bytes,
                ),
            )
            if target_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run_cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state IN (
                                    'PENDING',
                                    'ACQUIRING_LEASE',
                                    'REVALIDATING',
                                    'EXECUTING',
                                    'PAUSED',
                                    'WAITING_FOR_ENDPOINT',
                                    'NEEDS_REVIEW'
                                )
                        )
                        THEN CASE
                            WHEN instr(
                                summary_json,
                                '"action_required":true'
                            ) > 0
                            THEN 'COMPLETED_WITH_WARNINGS'
                            WHEN NOT EXISTS (
                                SELECT 1
                                FROM run_targets
                                WHERE run_targets.run_id = runs.id
                                    AND run_targets.state != 'SUCCEEDED'
                            )
                            THEN 'COMPLETED'
                            WHEN NOT EXISTS (
                                SELECT 1
                                FROM run_targets
                                WHERE run_targets.run_id = runs.id
                                    AND run_targets.state NOT IN (
                                        'SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS'
                                    )
                            )
                            THEN 'COMPLETED_WITH_WARNINGS'
                            ELSE 'PARTIAL_FAILURE'
                        END
                        ELSE state
                    END,
                    finished_utc = CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state IN (
                                    'PENDING',
                                    'ACQUIRING_LEASE',
                                    'REVALIDATING',
                                    'EXECUTING',
                                    'PAUSED',
                                    'WAITING_FOR_ENDPOINT',
                                    'NEEDS_REVIEW'
                                )
                        )
                        THEN COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                        ELSE finished_utc
                    END,
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'EXECUTING'
                """,
                (run_id,),
            )
            if run_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_COMPLETION_FAILED") from exc

    def record_run_target_succeeded_with_warnings(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
        skipped_operations: int,
        skipped_bytes: int,
        last_error_code: str,
    ) -> StartedRun | None:
        normalized_error_code = last_error_code.strip()
        if (
            completed_operations < 0
            or completed_bytes < 0
            or skipped_operations < 1
            or skipped_bytes < 0
            or not normalized_error_code
        ):
            return None
        result_json = _json_dump(
            {
                "last_error_code": normalized_error_code,
                "skipped_bytes": skipped_bytes,
                "skipped_operations": skipped_operations,
            }
        )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            target_cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'SUCCEEDED_WITH_WARNINGS',
                    completed_operations = ?,
                    completed_bytes = ?,
                    warning_count = warning_count + ?,
                    result_json = ?,
                    finished_utc = COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'EXECUTING'
                    AND planned_operations = ?
                    AND planned_bytes = ?
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state = 'EXECUTING'
                    )
                """,
                (
                    completed_operations,
                    completed_bytes,
                    skipped_operations,
                    result_json,
                    run_id,
                    run_target_id,
                    completed_operations + skipped_operations,
                    completed_bytes + skipped_bytes,
                ),
            )
            if target_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run_cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state IN (
                                    'PENDING',
                                    'ACQUIRING_LEASE',
                                    'REVALIDATING',
                                    'EXECUTING',
                                    'PAUSED',
                                    'WAITING_FOR_ENDPOINT',
                                    'NEEDS_REVIEW'
                                )
                        )
                        THEN CASE
                            WHEN NOT EXISTS (
                                SELECT 1
                                FROM run_targets
                                WHERE run_targets.run_id = runs.id
                                    AND run_targets.state NOT IN (
                                        'SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS'
                                    )
                            )
                            THEN 'COMPLETED_WITH_WARNINGS'
                            ELSE 'PARTIAL_FAILURE'
                        END
                        ELSE state
                    END,
                    finished_utc = CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state IN (
                                    'PENDING',
                                    'ACQUIRING_LEASE',
                                    'REVALIDATING',
                                    'EXECUTING',
                                    'PAUSED',
                                    'WAITING_FOR_ENDPOINT',
                                    'NEEDS_REVIEW'
                                )
                        )
                        THEN COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                        ELSE finished_utc
                    END,
                    warning_count = warning_count + ?,
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'EXECUTING'
                """,
                (skipped_operations, run_id),
            )
            if run_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_WARNING_COMPLETION_FAILED") from exc

    def record_run_target_recovery_required(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None:
        normalized_error_code = last_error_code.strip()
        if not normalized_error_code:
            return None
        result_json = _json_dump(
            {
                "last_error_code": normalized_error_code,
                "terminal_recovery_phase": "USER_DECISION_REQUIRED",
            }
        )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            target_cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'RECOVERY_REQUIRED',
                    error_count = error_count + 1,
                    result_json = ?,
                    finished_utc = COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'EXECUTING'
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state = 'EXECUTING'
                    )
                """,
                (result_json, run_id, run_target_id),
            )
            if target_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run_cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = 'RECOVERY_REQUIRED',
                    error_count = error_count + 1,
                    finished_utc = COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'EXECUTING'
                """,
                (run_id,),
            )
            if run_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_TERMINAL_RECOVERY_FAILED") from exc

    def record_run_target_cancelled(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None:
        normalized_error_code = last_error_code.strip()
        if not normalized_error_code:
            return None
        result_json = _json_dump(
            {
                "last_error_code": normalized_error_code,
                "terminal_recovery_phase": "CANCELLED",
            }
        )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            target_cursor = self._connection.execute(
                """
                UPDATE run_targets
                SET
                    state = 'CANCELLED',
                    error_count = error_count + 1,
                    result_json = ?,
                    finished_utc = COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    row_version = row_version + 1
                WHERE run_id = ?
                    AND id = ?
                    AND state = 'EXECUTING'
                    AND EXISTS (
                        SELECT 1
                        FROM runs
                        WHERE runs.id = run_targets.run_id
                            AND runs.state = 'EXECUTING'
                    )
                """,
                (result_json, run_id, run_target_id),
            )
            if target_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run_cursor = self._connection.execute(
                """
                UPDATE runs
                SET
                    state = CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state IN (
                                    'PENDING',
                                    'ACQUIRING_LEASE',
                                    'REVALIDATING',
                                    'EXECUTING',
                                    'PAUSED',
                                    'WAITING_FOR_ENDPOINT',
                                    'NEEDS_REVIEW'
                                )
                        )
                        THEN state
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state != 'CANCELLED'
                        )
                        THEN 'CANCELLED'
                        ELSE 'PARTIAL_FAILURE'
                    END,
                    finished_utc = CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state IN (
                                    'PENDING',
                                    'ACQUIRING_LEASE',
                                    'REVALIDATING',
                                    'EXECUTING',
                                    'PAUSED',
                                    'WAITING_FOR_ENDPOINT',
                                    'NEEDS_REVIEW'
                                )
                        )
                        THEN finished_utc
                        ELSE COALESCE(finished_utc, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    END,
                    error_count = error_count + 1,
                    row_version = row_version + 1
                WHERE id = ?
                    AND state = 'EXECUTING'
                """,
                (run_id,),
            )
            if run_cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            run = self.load_started_run(run_id)
            if run is None:
                raise SqliteRunStoreError("RUN_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return run
        except (sqlite3.Error, SqliteRunStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteRunStoreError):
                raise
            raise SqliteRunStoreError("RUN_TARGET_TERMINAL_RECOVERY_FAILED") from exc

    def _load_one(
        self,
        query: str,
        parameters: tuple[object, ...],
    ) -> StartedRun | None:
        row = self._connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        run_id = str(row[0])
        return StartedRun(
            run_id=run_id,
            job_id=str(row[1]),
            job_revision_id=str(row[2]),
            plan_id=str(row[3]),
            command_request_id=str(row[4]),
            command_receipt_id=str(row[5]),
            trigger_occurrence_id=None if row[6] is None else str(row[6]),
            logical_run_group_id=str(row[7]),
            resumed_from_run_id=None if row[8] is None else str(row[8]),
            trigger_type=RunTriggerType(str(row[9])),
            state=RunState(str(row[10])),
            summary=_json_object(str(row[11])),
            warning_count=int(row[12]),
            error_count=int(row[13]),
            app_version=str(row[14]),
            plan_checksum=str(row[15]),
            idempotency_key=str(row[16]),
            planned_operations=int(row[17]),
            planned_bytes=int(row[18]),
            targets=self._load_targets(run_id),
        )

    def _load_target(
        self, *, run_id: str, run_target_id: str
    ) -> StartedRunTarget | None:
        rows = self._load_targets_by_query(
            """
            SELECT
                id,
                endpoint_id,
                endpoint_revision_id,
                required_owner_installation_id,
                required_ownership_epoch,
                state,
                lease_resource_key,
                last_lease_id,
                last_ownership_epoch,
                last_fencing_token,
                planned_operations,
                planned_bytes,
                completed_operations,
                completed_bytes
            FROM run_targets
            WHERE run_id = ?
                AND id = ?
            """,
            (run_id, run_target_id),
        )
        if not rows:
            return None
        return rows[0]

    def _load_targets(self, run_id: str) -> tuple[StartedRunTarget, ...]:
        return self._load_targets_by_query(
            """
            SELECT
                id,
                endpoint_id,
                endpoint_revision_id,
                required_owner_installation_id,
                required_ownership_epoch,
                state,
                lease_resource_key,
                last_lease_id,
                last_ownership_epoch,
                last_fencing_token,
                planned_operations,
                planned_bytes,
                completed_operations,
                completed_bytes
            FROM run_targets
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        )

    def _load_targets_by_query(
        self,
        query: str,
        parameters: tuple[object, ...],
    ) -> tuple[StartedRunTarget, ...]:
        rows = self._connection.execute(query, parameters).fetchall()
        return tuple(
            StartedRunTarget(
                run_target_id=str(row[0]),
                endpoint_id=str(row[1]),
                endpoint_revision_id=str(row[2]),
                required_owner_installation_id=None if row[3] is None else str(row[3]),
                required_ownership_epoch=None if row[4] is None else int(row[4]),
                state=RunTargetState(str(row[5])),
                lease_resource_key=None if row[6] is None else str(row[6]),
                last_lease_id=None if row[7] is None else str(row[7]),
                last_ownership_epoch=None if row[8] is None else int(row[8]),
                last_fencing_token=None if row[9] is None else int(row[9]),
                planned_operations=int(row[10]),
                planned_bytes=int(row[11]),
                completed_operations=int(row[12]),
                completed_bytes=int(row[13]),
            )
            for row in rows
        )

    def _load_target_activity_summaries(
        self,
        run_id: str,
    ) -> tuple[RunTargetActivitySummary, ...]:
        rows = self._connection.execute(
            """
            SELECT
                current_target.id,
                current_target.endpoint_id,
                current_target.endpoint_revision_id,
                current_target.state,
                current_target.planned_operations,
                current_target.completed_operations,
                current_target.planned_bytes,
                current_target.completed_bytes,
                current_target.warning_count,
                current_target.error_count,
                (
                    SELECT max(success_target.finished_utc)
                    FROM runs AS success_run
                    JOIN run_targets AS success_target
                        ON success_target.run_id = success_run.id
                    WHERE success_run.job_id = current_run.job_id
                        AND success_target.endpoint_id = current_target.endpoint_id
                        AND success_target.state IN (
                            'SUCCEEDED',
                            'SUCCEEDED_WITH_WARNINGS'
                        )
                        AND success_target.finished_utc IS NOT NULL
                ) AS last_success_utc
            FROM run_targets AS current_target
            JOIN runs AS current_run
                ON current_run.id = current_target.run_id
            WHERE current_target.run_id = ?
            ORDER BY current_target.id
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            RunTargetActivitySummary(
                run_target_id=str(row[0]),
                endpoint_id=str(row[1]),
                endpoint_revision_id=str(row[2]),
                state=RunTargetState(str(row[3])),
                planned_operations=int(row[4]),
                completed_operations=int(row[5]),
                planned_bytes=int(row[6]),
                completed_bytes=int(row[7]),
                warning_count=int(row[8]),
                error_count=int(row[9]),
                last_success_utc=None if row[10] is None else str(row[10]),
            )
            for row in rows
        )


def _json_dump(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_object(payload: str) -> dict[str, object]:
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqliteRunStoreError("RUN_JSON_INVALID") from exc
    if not isinstance(data, dict):
        raise SqliteRunStoreError("RUN_JSON_INVALID")
    return data


def _run_action_metadata(payload: str) -> tuple[bool, int, int]:
    summary = _json_object(payload)
    action_value = summary.get("action_required", False)
    deferred_count_value = summary.get("deferred_operation_count", 0)
    deferred_bytes_value = summary.get("deferred_planned_bytes", 0)
    if not isinstance(action_value, bool):
        raise SqliteRunStoreError("RUN_ACTION_REQUIRED_METADATA_INVALID")
    if (
        isinstance(deferred_count_value, bool)
        or not isinstance(deferred_count_value, int)
        or deferred_count_value < 0
        or isinstance(deferred_bytes_value, bool)
        or not isinstance(deferred_bytes_value, int)
        or deferred_bytes_value < 0
        or action_value != (deferred_count_value > 0)
    ):
        raise SqliteRunStoreError("RUN_ACTION_REQUIRED_METADATA_INVALID")
    return action_value, deferred_count_value, deferred_bytes_value


_TERMINAL_RUN_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.COMPLETED_WITH_WARNINGS,
        RunState.PARTIAL_FAILURE,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.BLOCKED_BY_SAFETY,
        RunState.RECOVERY_REQUIRED,
    }
)
