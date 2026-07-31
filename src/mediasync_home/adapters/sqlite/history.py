from __future__ import annotations

import sqlite3

from mediasync_home.application.history_read_models import (
    HistoryActivityFilter,
    HistoryActivityKind,
    HistoryActivitySummary,
    HistoryTargetSummary,
    HistoryTimelineReadModelStore,
)


class SqliteHistoryReadModelError(ValueError):
    pass


class SqliteHistoryReadModelStore(HistoryTimelineReadModelStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_recent_history_activities(
        self,
        *,
        limit: int,
        offset: int,
        activity_filter: HistoryActivityFilter,
        job_id: str | None,
    ) -> tuple[HistoryActivitySummary, ...]:
        if limit < 1 or offset < 0:
            raise SqliteHistoryReadModelError("HISTORY_QUERY_BOUNDS_INVALID")
        kind_clause = ""
        parameters: list[object] = []
        if activity_filter is HistoryActivityFilter.CONTROLS:
            kind_clause = "AND activity_kind = 'CONTROL'"
        elif activity_filter is HistoryActivityFilter.BACKUPS:
            kind_clause = "AND activity_kind = 'BACKUP'"
        job_clause = ""
        if job_id is not None:
            job_clause = "AND job_id = ?"
            parameters.append(job_id)
        parameters.extend((limit, offset))
        rows = self._connection.execute(
            f"""
            SELECT
                activity_kind,
                activity_id,
                job_id,
                job_revision_id,
                job_title,
                run_id,
                analysis_id,
                plan_id,
                state,
                started_utc,
                finished_utc,
                planned_operations,
                planned_bytes,
                warning_count,
                error_count,
                trigger_type
            FROM (
                SELECT
                    'CONTROL' AS activity_kind,
                    COALESCE(
                        materializations.analysis_id,
                        'control:' || materializations.job_id || ':' ||
                            materializations.job_revision_id
                    ) AS activity_id,
                    materializations.job_id AS job_id,
                    materializations.job_revision_id AS job_revision_id,
                    details.source_name AS job_title,
                    NULL AS run_id,
                    materializations.analysis_id AS analysis_id,
                    materializations.plan_id AS plan_id,
                    materializations.state AS state,
                    materializations.started_utc AS started_utc,
                    materializations.completed_utc AS finished_utc,
                    materializations.operation_count AS planned_operations,
                    materializations.planned_bytes AS planned_bytes,
                    COALESCE((
                        SELECT count(*)
                        FROM snapshot_issues AS issues
                        JOIN snapshots
                            ON snapshots.id = issues.snapshot_id
                        WHERE snapshots.analysis_id = materializations.analysis_id
                            AND issues.blocks_destructive_actions = 0
                    ), 0) AS warning_count,
                    COALESCE((
                        SELECT count(*)
                        FROM snapshot_issues AS issues
                        JOIN snapshots
                            ON snapshots.id = issues.snapshot_id
                        WHERE snapshots.analysis_id = materializations.analysis_id
                            AND issues.blocks_destructive_actions = 1
                    ), 0) AS error_count,
                    'INITIAL_JOB_SETUP' AS trigger_type
                FROM initial_backup_plan_materializations AS materializations
                JOIN standard_backup_job_revision_details AS details
                    ON details.job_id = materializations.job_id
                    AND details.job_revision_id = materializations.job_revision_id

                UNION ALL

                SELECT
                    'BACKUP' AS activity_kind,
                    runs.id AS activity_id,
                    runs.job_id AS job_id,
                    runs.job_revision_id AS job_revision_id,
                    details.source_name AS job_title,
                    runs.id AS run_id,
                    NULL AS analysis_id,
                    runs.plan_id AS plan_id,
                    runs.state AS state,
                    runs.started_utc AS started_utc,
                    runs.finished_utc AS finished_utc,
                    runs.planned_operations AS planned_operations,
                    runs.planned_bytes AS planned_bytes,
                    runs.warning_count AS warning_count,
                    runs.error_count AS error_count,
                    runs.trigger_type AS trigger_type
                FROM runs
                JOIN standard_backup_job_revision_details AS details
                    ON details.job_id = runs.job_id
                    AND details.job_revision_id = runs.job_revision_id
            )
            WHERE 1 = 1
                {kind_clause}
                {job_clause}
            ORDER BY started_utc DESC, activity_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(parameters),
        ).fetchall()
        return tuple(self._activity_from_row(row) for row in rows)

    def _activity_from_row(
        self,
        row: sqlite3.Row | tuple[object, ...],
    ) -> HistoryActivitySummary:
        kind = HistoryActivityKind(str(row[0]))
        activity_id = str(row[1])
        targets = self._load_targets(
            kind=kind,
            activity_id=activity_id,
            job_id=str(row[2]),
            job_revision_id=str(row[3]),
        )
        completed_operations = sum(target.completed_operations for target in targets)
        completed_bytes = sum(target.completed_bytes for target in targets)
        return HistoryActivitySummary(
            activity_kind=kind,
            activity_id=activity_id,
            job_id=str(row[2]),
            job_revision_id=str(row[3]),
            job_title=str(row[4]),
            run_id=_optional_text(row[5]),
            analysis_id=_optional_text(row[6]),
            plan_id=_optional_text(row[7]),
            state=str(row[8]),
            started_utc=str(row[9]),
            finished_utc=_optional_text(row[10]),
            planned_operations=_required_int(row[11]),
            completed_operations=completed_operations,
            planned_bytes=_required_int(row[12]),
            completed_bytes=completed_bytes,
            warning_count=_required_int(row[13]),
            error_count=_required_int(row[14]),
            trigger_type=str(row[15]),
            targets=targets,
        )

    def _load_targets(
        self,
        *,
        kind: HistoryActivityKind,
        activity_id: str,
        job_id: str,
        job_revision_id: str,
    ) -> tuple[HistoryTargetSummary, ...]:
        if kind is HistoryActivityKind.BACKUP:
            rows = self._connection.execute(
                """
                SELECT
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
                (activity_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT
                    endpoint_id,
                    endpoint_revision_id,
                    registration_state,
                    0,
                    0,
                    0,
                    0,
                    0,
                    CASE WHEN registration_state = 'BLOCKED' THEN 1 ELSE 0 END
                FROM standard_backup_job_endpoint_bindings
                WHERE job_id = ?
                    AND job_revision_id = ?
                    AND role = 'TARGET'
                ORDER BY ordinal
                """,
                (job_id, job_revision_id),
            ).fetchall()
        return tuple(
            HistoryTargetSummary(
                endpoint_id=str(target[0]),
                endpoint_revision_id=str(target[1]),
                state=str(target[2]),
                planned_operations=_required_int(target[3]),
                completed_operations=_required_int(target[4]),
                planned_bytes=_required_int(target[5]),
                completed_bytes=_required_int(target[6]),
                warning_count=_required_int(target[7]),
                error_count=_required_int(target[8]),
            )
            for target in rows
        )


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SqliteHistoryReadModelError("HISTORY_QUERY_INTEGER_INVALID")
    return int(value)
