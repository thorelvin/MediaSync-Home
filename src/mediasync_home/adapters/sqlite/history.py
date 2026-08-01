from __future__ import annotations

import sqlite3

from mediasync_home.application.history_read_models import (
    HistoryActivityFilter,
    HistoryActivityKind,
    HistoryActivitySummary,
    HistoryTargetSummary,
    HistoryTimelineCursor,
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
        after: HistoryTimelineCursor | None,
        offset: int,
        activity_filter: HistoryActivityFilter,
        job_id: str | None,
    ) -> tuple[HistoryActivitySummary, ...]:
        if limit < 1 or offset < 0:
            raise SqliteHistoryReadModelError("HISTORY_QUERY_BOUNDS_INVALID")
        candidate_limit = limit + offset
        rows: list[sqlite3.Row | tuple[object, ...]] = []
        if activity_filter is not HistoryActivityFilter.BACKUPS:
            rows.extend(
                self._list_initial_control_rows(
                    limit=candidate_limit,
                    after=after,
                    job_id=job_id,
                )
            )
            rows.extend(
                self._list_requested_control_rows(
                    limit=candidate_limit,
                    after=after,
                    job_id=job_id,
                )
            )
        if activity_filter is not HistoryActivityFilter.CONTROLS:
            rows.extend(
                self._list_backup_rows(
                    limit=candidate_limit,
                    after=after,
                    job_id=job_id,
                )
            )
        rows.sort(
            key=lambda row: (str(row[9]), str(row[0]), str(row[1])),
            reverse=True,
        )
        page = rows[offset : offset + limit]
        return tuple(self._activity_from_row(row) for row in page)

    def _list_initial_control_rows(
        self,
        *,
        limit: int,
        after: HistoryTimelineCursor | None,
        job_id: str | None,
    ) -> list[sqlite3.Row | tuple[object, ...]]:
        activity_id_expression = """COALESCE(
            materializations.analysis_id,
            'control:' || materializations.job_id || ':' ||
                materializations.job_revision_id
        )"""
        scope_clause, parameters = _history_scope(
            kind=HistoryActivityKind.CONTROL,
            started_expression="materializations.started_utc",
            activity_id_expression=activity_id_expression,
            job_expression="materializations.job_id",
            after=after,
            job_id=job_id,
        )
        parameters.append(limit)
        return self._connection.execute(
            f"""
            SELECT
                'CONTROL' AS activity_kind,
                {activity_id_expression} AS activity_id,
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
                    JOIN snapshots ON snapshots.id = issues.snapshot_id
                    WHERE snapshots.analysis_id = materializations.analysis_id
                        AND issues.blocks_destructive_actions = 0
                ), 0) AS warning_count,
                COALESCE((
                    SELECT count(*)
                    FROM snapshot_issues AS issues
                    JOIN snapshots ON snapshots.id = issues.snapshot_id
                    WHERE snapshots.analysis_id = materializations.analysis_id
                        AND issues.blocks_destructive_actions = 1
                ), 0) AS error_count,
                'INITIAL_JOB_SETUP' AS trigger_type
            FROM initial_backup_plan_materializations AS materializations
            JOIN standard_backup_job_revision_details AS details
                ON details.job_id = materializations.job_id
                AND details.job_revision_id = materializations.job_revision_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM backup_analysis_requests AS requests
                WHERE requests.analysis_id IS NOT NULL
                    AND requests.analysis_id = materializations.analysis_id
            )
                {scope_clause}
            ORDER BY materializations.started_utc DESC, activity_id DESC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()

    def _list_requested_control_rows(
        self,
        *,
        limit: int,
        after: HistoryTimelineCursor | None,
        job_id: str | None,
    ) -> list[sqlite3.Row | tuple[object, ...]]:
        started_expression = "COALESCE(requests.started_utc, requests.requested_utc)"
        scope_clause, parameters = _history_scope(
            kind=HistoryActivityKind.CONTROL,
            started_expression=started_expression,
            activity_id_expression="requests.request_id",
            job_expression="requests.job_id",
            after=after,
            job_id=job_id,
        )
        parameters.append(limit)
        return self._connection.execute(
            f"""
            SELECT
                'CONTROL' AS activity_kind,
                requests.request_id AS activity_id,
                requests.job_id AS job_id,
                requests.job_revision_id AS job_revision_id,
                details.source_name AS job_title,
                NULL AS run_id,
                requests.analysis_id AS analysis_id,
                requests.plan_id AS plan_id,
                requests.state AS state,
                {started_expression} AS started_utc,
                requests.completed_utc AS finished_utc,
                requests.operation_count AS planned_operations,
                requests.planned_bytes AS planned_bytes,
                COALESCE((
                    SELECT count(*)
                    FROM snapshot_issues AS issues
                    JOIN snapshots ON snapshots.id = issues.snapshot_id
                    WHERE snapshots.analysis_id = requests.analysis_id
                        AND issues.blocks_destructive_actions = 0
                ), 0) AS warning_count,
                COALESCE((
                    SELECT count(*)
                    FROM snapshot_issues AS issues
                    JOIN snapshots ON snapshots.id = issues.snapshot_id
                    WHERE snapshots.analysis_id = requests.analysis_id
                        AND issues.blocks_destructive_actions = 1
                ), 0) AS error_count,
                'MANUAL_BACKUP_CHECK' AS trigger_type
            FROM backup_analysis_requests AS requests
            JOIN standard_backup_job_revision_details AS details
                ON details.job_id = requests.job_id
                AND details.job_revision_id = requests.job_revision_id
            WHERE 1 = 1
                {scope_clause}
            ORDER BY started_utc DESC, requests.request_id DESC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()

    def _list_backup_rows(
        self,
        *,
        limit: int,
        after: HistoryTimelineCursor | None,
        job_id: str | None,
    ) -> list[sqlite3.Row | tuple[object, ...]]:
        scope_clause, parameters = _history_scope(
            kind=HistoryActivityKind.BACKUP,
            started_expression="runs.started_utc",
            activity_id_expression="runs.id",
            job_expression="runs.job_id",
            after=after,
            job_id=job_id,
        )
        parameters.append(limit)
        return self._connection.execute(
            f"""
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
            WHERE 1 = 1
                {scope_clause}
            ORDER BY runs.started_utc DESC, runs.id DESC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()

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


def _history_scope(
    *,
    kind: HistoryActivityKind,
    started_expression: str,
    activity_id_expression: str,
    job_expression: str,
    after: HistoryTimelineCursor | None,
    job_id: str | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if job_id is not None:
        clauses.append(f"AND {job_expression} = ?")
        parameters.append(job_id)
    if after is not None:
        if kind is after.activity_kind:
            clauses.append(
                f"""AND (
                    {started_expression} < ?
                    OR (
                        {started_expression} = ?
                        AND {activity_id_expression} < ?
                    )
                )"""
            )
            parameters.extend(
                (after.started_utc, after.started_utc, after.activity_id)
            )
        elif kind.value < after.activity_kind.value:
            clauses.append(f"AND {started_expression} <= ?")
            parameters.append(after.started_utc)
        else:
            clauses.append(f"AND {started_expression} < ?")
            parameters.append(after.started_utc)
    return "\n                ".join(clauses), parameters
