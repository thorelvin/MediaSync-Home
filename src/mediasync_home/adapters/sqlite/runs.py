from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediasync_home.application.activity_read_models import (
    RunActivityReadModelStore,
    RunActivitySummary,
    RunTargetActivitySummary,
)
from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)


class SqliteRunStoreError(ValueError):
    pass


class SqliteRunStore(RunStore, RunActivityReadModelStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

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

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
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
            WHERE state IN ('QUEUED', 'PREFLIGHT')
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
                    error_count
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
                    error_count
                FROM runs
                WHERE job_id = ?
                ORDER BY started_utc DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (job_id, limit, offset),
            ).fetchall()
        return tuple(
            RunActivitySummary(
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
                targets=self._load_target_activity_summaries(str(row[0])),
            )
            for row in rows
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
                            AND runs.state IN ('QUEUED', 'PREFLIGHT')
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
                    state = 'PREFLIGHT',
                    row_version = row_version + 1
                WHERE id = ?
                    AND state IN ('QUEUED', 'PREFLIGHT')
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
                            AND runs.state = 'PREFLIGHT'
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
                                AND run_targets.state != 'SUCCEEDED'
                        )
                        THEN 'COMPLETED'
                        ELSE state
                    END,
                    finished_utc = CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM run_targets
                            WHERE run_targets.run_id = runs.id
                                AND run_targets.state != 'SUCCEEDED'
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

    def _load_target(self, *, run_id: str, run_target_id: str) -> StartedRunTarget | None:
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
                id,
                endpoint_id,
                endpoint_revision_id,
                state,
                planned_operations,
                completed_operations,
                planned_bytes,
                completed_bytes,
                warning_count,
                error_count
            FROM run_targets
            WHERE run_id = ?
            ORDER BY id
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
