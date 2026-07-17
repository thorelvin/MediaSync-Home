from __future__ import annotations

import json
import sqlite3
from typing import Any

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


class SqliteRunStore(RunStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_started_run(self, run: StartedRun) -> None:
        try:
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
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
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

    def _load_targets(self, run_id: str) -> tuple[StartedRunTarget, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                endpoint_id,
                endpoint_revision_id,
                required_owner_installation_id,
                required_ownership_epoch,
                state,
                lease_resource_key,
                planned_operations,
                planned_bytes
            FROM run_targets
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            StartedRunTarget(
                run_target_id=str(row[0]),
                endpoint_id=str(row[1]),
                endpoint_revision_id=str(row[2]),
                required_owner_installation_id=None if row[3] is None else str(row[3]),
                required_ownership_epoch=None if row[4] is None else int(row[4]),
                state=RunTargetState(str(row[5])),
                lease_resource_key=None if row[6] is None else str(row[6]),
                planned_operations=int(row[7]),
                planned_bytes=int(row[8]),
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
