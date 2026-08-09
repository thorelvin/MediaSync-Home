from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.application.job_lifecycle import (
    ChangeJobLifecycleCommand,
    JobLifecycleRecord,
    JobLifecycleState,
    JobLifecycleStore,
    JobLifecycleTransitionOutcome,
)
from mediasync_home.application.job_editing import JobScheduleInvalidationError
from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.task_scheduler import (
    bind_same_user_task_scheduler_definition_hash,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


class SqliteJobLifecycleError(JobScheduleInvalidationError):
    pass


_BLOCKING_RUN_STATES = (
    "CREATED",
    "QUEUED",
    "PREFLIGHT",
    "EXECUTING",
    "PAUSING",
    "PAUSED",
    "RECOVERY_REQUIRED",
)


class SqliteJobLifecycleStore(JobLifecycleStore):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        installation_id: str,
        task_scheduler_executable_path: str | None = None,
    ) -> None:
        self._connection = connection
        self._installation_id = installation_id
        self._task_scheduler_executable_path = task_scheduler_executable_path

    def load_job_lifecycle(self, job_id: str) -> JobLifecycleRecord | None:
        row = self._connection.execute(
            """
            SELECT
                jobs.id,
                heads.active_revision_id,
                CASE
                    WHEN deletions.job_id IS NOT NULL THEN 'DELETED'
                    ELSE jobs.lifecycle_state
                END,
                jobs.lifecycle_row_version,
                jobs.archived_utc
            FROM jobs
            INNER JOIN job_heads AS heads ON heads.job_id = jobs.id
            LEFT JOIN job_deletions AS deletions ON deletions.job_id = jobs.id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
        return None if row is None else _record_from_row(row)

    def archive_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome:
        replay = self._load_event_outcome(command.idempotency_key)
        if replay is not None:
            return replace(replay, idempotent_replay=True)
        current = self.load_job_lifecycle(command.job_id)
        rejection = _validate_transition(
            current=current,
            command=command,
            expected_state=JobLifecycleState.ACTIVE,
        )
        if rejection is not None:
            return rejection
        assert current is not None
        blocking_state = self._blocking_run_state(command.job_id)
        if blocking_state is not None:
            code = (
                "JOB_ARCHIVE_RECOVERY_REQUIRED"
                if blocking_state == "RECOVERY_REQUIRED"
                else "JOB_ARCHIVE_ACTIVE_RUN"
            )
            return JobLifecycleTransitionOutcome(
                applied=False,
                validation_code=code,
                next_action=(
                    "Resolve recovery before archiving this job."
                    if blocking_state == "RECOVERY_REQUIRED"
                    else "Wait for the active backup to finish or stop it safely."
                ),
                record=current,
            )
        if self._has_recovery_required_target(command.job_id):
            return JobLifecycleTransitionOutcome(
                applied=False,
                validation_code="JOB_ARCHIVE_RECOVERY_REQUIRED",
                next_action="Resolve recovery before archiving this job.",
                record=current,
            )
        try:
            disabled_count = self.disable_enabled_schedules(
                command.job_id,
                validation_code_prefix="JOB_ARCHIVE",
            )
        except SqliteJobLifecycleError as exc:
            return JobLifecycleTransitionOutcome(
                applied=False,
                validation_code=str(exc),
                next_action="Restore Task Scheduler reconciliation context and retry.",
                record=current,
            )
        cursor = self._connection.execute(
            """
            UPDATE jobs
            SET
                lifecycle_state = 'ARCHIVED',
                archived_utc = ?,
                lifecycle_row_version = lifecycle_row_version + 1
            WHERE id = ?
                AND lifecycle_state = 'ACTIVE'
                AND lifecycle_row_version = ?
            """,
            (occurred_utc, command.job_id, current.row_version),
        )
        if cursor.rowcount != 1:
            raise SqliteJobLifecycleError("JOB_LIFECYCLE_TRANSITION_CONFLICT")
        self._connection.execute(
            """
            UPDATE backup_analysis_requests
            SET
                state = 'BLOCKED',
                started_utc = COALESCE(started_utc, ?),
                completed_utc = ?,
                reason_code = 'BACKUP_ANALYSIS_JOB_ARCHIVED',
                row_version = row_version + 1
            WHERE job_id = ?
                AND state IN ('QUEUED', 'RUNNING')
            """,
            (occurred_utc, occurred_utc, command.job_id),
        )
        recorded = self.load_job_lifecycle(command.job_id)
        assert recorded is not None
        self._record_event(
            command=command,
            previous_state=JobLifecycleState.ACTIVE,
            record=recorded,
            occurred_utc=occurred_utc,
            disabled_schedule_count=disabled_count,
            analysis_request_id=None,
        )
        return JobLifecycleTransitionOutcome(
            applied=True,
            validation_code="JOB_ARCHIVED",
            next_action="The job is archived; history and files are retained.",
            record=recorded,
            disabled_schedule_count=disabled_count,
        )

    def reactivate_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome:
        replay = self._load_event_outcome(command.idempotency_key)
        if replay is not None:
            return replace(replay, idempotent_replay=True)
        current = self.load_job_lifecycle(command.job_id)
        rejection = _validate_transition(
            current=current,
            command=command,
            expected_state=JobLifecycleState.ARCHIVED,
        )
        if rejection is not None:
            return rejection
        assert current is not None
        cursor = self._connection.execute(
            """
            UPDATE jobs
            SET
                lifecycle_state = 'ACTIVE',
                archived_utc = NULL,
                lifecycle_row_version = lifecycle_row_version + 1
            WHERE id = ?
                AND lifecycle_state = 'ARCHIVED'
                AND lifecycle_row_version = ?
            """,
            (command.job_id, current.row_version),
        )
        if cursor.rowcount != 1:
            raise SqliteJobLifecycleError("JOB_LIFECYCLE_TRANSITION_CONFLICT")
        recorded = self.load_job_lifecycle(command.job_id)
        assert recorded is not None
        self._record_event(
            command=command,
            previous_state=JobLifecycleState.ARCHIVED,
            record=recorded,
            occurred_utc=occurred_utc,
            disabled_schedule_count=0,
            analysis_request_id=command.request_id,
        )
        return JobLifecycleTransitionOutcome(
            applied=True,
            validation_code="JOB_REACTIVATED_NEEDS_CHECK",
            next_action="Run the queued full check before starting another backup.",
            record=recorded,
            analysis_request_id=command.request_id,
        )

    def delete_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome:
        replay = self._load_deletion_outcome(command.idempotency_key)
        if replay is not None:
            return replace(replay, idempotent_replay=True)
        current = self.load_job_lifecycle(command.job_id)
        rejection = _validate_deletion(current=current, command=command)
        if rejection is not None:
            return rejection
        assert current is not None
        blocking_state = self._blocking_run_state(command.job_id)
        if blocking_state is not None:
            return JobLifecycleTransitionOutcome(
                applied=False,
                validation_code=(
                    "JOB_DELETE_RECOVERY_REQUIRED"
                    if blocking_state == "RECOVERY_REQUIRED"
                    else "JOB_DELETE_ACTIVE_RUN"
                ),
                next_action=(
                    "Resolve recovery before deleting this job."
                    if blocking_state == "RECOVERY_REQUIRED"
                    else "Wait for the active backup to finish or stop it safely."
                ),
                record=current,
            )
        if self._has_recovery_required_target(command.job_id):
            return JobLifecycleTransitionOutcome(
                applied=False,
                validation_code="JOB_DELETE_RECOVERY_REQUIRED",
                next_action="Resolve recovery before deleting this job.",
                record=current,
            )
        try:
            disabled_count = self.disable_enabled_schedules(
                command.job_id,
                validation_code_prefix="JOB_DELETE",
            )
        except SqliteJobLifecycleError as exc:
            return JobLifecycleTransitionOutcome(
                applied=False,
                validation_code=str(exc),
                next_action="Restore Task Scheduler reconciliation context and retry.",
                record=current,
            )
        cursor = self._connection.execute(
            """
            UPDATE jobs
            SET lifecycle_row_version = lifecycle_row_version + 1
            WHERE id = ? AND lifecycle_row_version = ?
            """,
            (command.job_id, current.row_version),
        )
        if cursor.rowcount != 1:
            raise SqliteJobLifecycleError("JOB_LIFECYCLE_TRANSITION_CONFLICT")
        deleted_row_version = current.row_version + 1
        self._connection.execute(
            """
            INSERT INTO job_deletions (
                job_id,
                job_revision_id,
                command_request_id,
                command_idempotency_key,
                occurred_utc,
                lifecycle_row_version,
                disabled_schedule_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command.job_id,
                command.expected_job_revision_id,
                command.request_id,
                command.idempotency_key,
                occurred_utc,
                deleted_row_version,
                disabled_count,
            ),
        )
        self._connection.execute(
            """
            UPDATE backup_analysis_requests
            SET
                state = 'BLOCKED',
                started_utc = COALESCE(started_utc, ?),
                completed_utc = ?,
                reason_code = 'BACKUP_ANALYSIS_JOB_DELETED',
                row_version = row_version + 1
            WHERE job_id = ? AND state IN ('QUEUED', 'RUNNING')
            """,
            (occurred_utc, occurred_utc, command.job_id),
        )
        recorded = self.load_job_lifecycle(command.job_id)
        assert recorded is not None and recorded.state is JobLifecycleState.DELETED
        return JobLifecycleTransitionOutcome(
            applied=True,
            validation_code="JOB_DELETED",
            next_action="The job configuration was deleted; history and files are retained.",
            record=recorded,
            disabled_schedule_count=disabled_count,
        )

    def _blocking_run_state(self, job_id: str) -> str | None:
        placeholders = ", ".join("?" for _ in _BLOCKING_RUN_STATES)
        row = self._connection.execute(
            f"""
            SELECT state
            FROM runs
            WHERE job_id = ?
                AND state IN ({placeholders})
            ORDER BY started_utc DESC, id DESC
            LIMIT 1
            """,
            (job_id, *_BLOCKING_RUN_STATES),
        ).fetchone()
        return None if row is None else str(row[0])

    def _has_recovery_required_target(self, job_id: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1
            FROM run_targets
            INNER JOIN runs ON runs.id = run_targets.run_id
            WHERE runs.job_id = ?
                AND run_targets.state = 'RECOVERY_REQUIRED'
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        return row is not None

    def disable_enabled_schedules(
        self,
        job_id: str,
        *,
        validation_code_prefix: str = "JOB_LIFECYCLE",
    ) -> int:
        rows = self._connection.execute(
            f"""
            SELECT {_SCHEDULE_COLUMNS}
            FROM schedules
            WHERE job_id = ? AND enabled = 1
            ORDER BY id
            """,
            (job_id,),
        ).fetchall()
        if not rows:
            return 0
        executable_path = self._task_scheduler_executable_path
        if executable_path is None or not executable_path.strip():
            raise SqliteJobLifecycleError(
                f"{validation_code_prefix}_SCHEDULER_CONTEXT_UNAVAILABLE"
            )
        normalized_path = str(Path(executable_path))
        for row in rows:
            schedule = _schedule_from_row(row)
            disabled = bind_same_user_task_scheduler_definition_hash(
                replace(
                    schedule,
                    enabled=False,
                    definition_generation=schedule.definition_generation + 1,
                    row_version=schedule.row_version + 1,
                    desired_definition_hash="0" * 64,
                ),
                installation_id=self._installation_id,
                executable_path=normalized_path,
            )
            cursor = self._connection.execute(
                """
                UPDATE schedules
                SET
                    definition_generation = ?,
                    desired_definition_hash = ?,
                    enabled = 0,
                    row_version = ?
                WHERE id = ? AND row_version = ? AND enabled = 1
                """,
                (
                    disabled.definition_generation,
                    disabled.desired_definition_hash,
                    disabled.row_version,
                    disabled.schedule_id,
                    schedule.row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteJobLifecycleError(
                    f"{validation_code_prefix}_SCHEDULE_CONFLICT"
                )
        return len(rows)

    def _record_event(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        previous_state: JobLifecycleState,
        record: JobLifecycleRecord,
        occurred_utc: str,
        disabled_schedule_count: int,
        analysis_request_id: str | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO job_lifecycle_events (
                event_id,
                job_id,
                job_revision_id,
                command_request_id,
                command_idempotency_key,
                transition_kind,
                previous_state,
                next_state,
                occurred_utc,
                lifecycle_row_version,
                disabled_schedule_count,
                analysis_request_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command.request_id,
                record.job_id,
                record.job_revision_id,
                command.request_id,
                command.idempotency_key,
                "ARCHIVE" if record.state is JobLifecycleState.ARCHIVED else "REACTIVATE",
                previous_state.value,
                record.state.value,
                occurred_utc,
                record.row_version,
                disabled_schedule_count,
                analysis_request_id,
            ),
        )

    def _load_event_outcome(
        self,
        idempotency_key: str,
    ) -> JobLifecycleTransitionOutcome | None:
        row = self._connection.execute(
            """
            SELECT
                job_id,
                job_revision_id,
                next_state,
                occurred_utc,
                lifecycle_row_version,
                disabled_schedule_count,
                analysis_request_id
            FROM job_lifecycle_events
            WHERE command_idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        state = JobLifecycleState(str(row[2]))
        record = JobLifecycleRecord(
            job_id=str(row[0]),
            job_revision_id=str(row[1]),
            state=state,
            row_version=_required_int(row[4]),
            archived_utc=str(row[3]) if state is JobLifecycleState.ARCHIVED else None,
        )
        return JobLifecycleTransitionOutcome(
            applied=True,
            validation_code=(
                "JOB_ARCHIVED"
                if state is JobLifecycleState.ARCHIVED
                else "JOB_REACTIVATED_NEEDS_CHECK"
            ),
            next_action=(
                "The job is archived; history and files are retained."
                if state is JobLifecycleState.ARCHIVED
                else "Run the queued full check before starting another backup."
            ),
            record=record,
            disabled_schedule_count=_required_int(row[5]),
            analysis_request_id=None if row[6] is None else str(row[6]),
        )

    def _load_deletion_outcome(
        self,
        idempotency_key: str,
    ) -> JobLifecycleTransitionOutcome | None:
        row = self._connection.execute(
            """
            SELECT
                job_id,
                job_revision_id,
                lifecycle_row_version,
                disabled_schedule_count
            FROM job_deletions
            WHERE command_idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return JobLifecycleTransitionOutcome(
            applied=True,
            validation_code="JOB_DELETED",
            next_action="The job configuration was deleted; history and files are retained.",
            record=JobLifecycleRecord(
                job_id=str(row[0]),
                job_revision_id=str(row[1]),
                state=JobLifecycleState.DELETED,
                row_version=_required_int(row[2]),
            ),
            disabled_schedule_count=_required_int(row[3]),
        )


def _validate_transition(
    *,
    current: JobLifecycleRecord | None,
    command: ChangeJobLifecycleCommand,
    expected_state: JobLifecycleState,
) -> JobLifecycleTransitionOutcome | None:
    if current is None:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_JOB_NOT_FOUND",
            next_action="Refresh the Jobs view and choose an existing job.",
        )
    if current.job_revision_id != command.expected_job_revision_id:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_REVISION_STALE",
            next_action="Refresh the job before changing its lifecycle.",
            record=current,
        )
    if current.row_version != command.expected_lifecycle_row_version:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_VERSION_STALE",
            next_action="Refresh the job before changing its lifecycle.",
            record=current,
        )
    if current.state is not expected_state:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_STATE_CHANGED",
            next_action="Refresh the job before changing its lifecycle.",
            record=current,
        )
    return None


def _validate_deletion(
    *,
    current: JobLifecycleRecord | None,
    command: ChangeJobLifecycleCommand,
) -> JobLifecycleTransitionOutcome | None:
    if current is None:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_JOB_NOT_FOUND",
            next_action="Refresh the Jobs view and choose an existing job.",
        )
    if current.job_revision_id != command.expected_job_revision_id:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_REVISION_STALE",
            next_action="Refresh the job before deleting it.",
            record=current,
        )
    if current.row_version != command.expected_lifecycle_row_version:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_LIFECYCLE_VERSION_STALE",
            next_action="Refresh the job before deleting it.",
            record=current,
        )
    if current.state is JobLifecycleState.DELETED:
        return JobLifecycleTransitionOutcome(
            applied=False,
            validation_code="JOB_ALREADY_DELETED",
            next_action="Refresh the Jobs view.",
            record=current,
        )
    return None


_SCHEDULE_COLUMNS = """
    id,
    job_id,
    plan_id,
    plan_checksum,
    trigger_type,
    configuration_json,
    definition_generation,
    desired_definition_hash,
    time_zone_id,
    dst_policy,
    misfire_policy,
    coalescing_window_seconds,
    task_logon_type,
    requires_network,
    run_only_when_logged_on,
    enabled,
    row_version,
    last_triggered_utc
"""


def _schedule_from_row(row: sqlite3.Row | tuple[object, ...]) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id=str(row[0]),
        job_id=str(row[1]),
        plan_id=str(row[2]),
        plan_checksum=str(row[3]),
        trigger_type=TriggerKind(str(row[4])),
        configuration_json=str(row[5]),
        definition_generation=_required_int(row[6]),
        desired_definition_hash=str(row[7]),
        time_zone_id=None if row[8] is None else str(row[8]),
        dst_policy=str(row[9]),
        misfire_policy=str(row[10]),
        coalescing_window_seconds=_required_int(row[11]),
        task_logon_type=str(row[12]),
        requires_network=bool(row[13]),
        run_only_when_logged_on=bool(row[14]),
        enabled=bool(row[15]),
        row_version=_required_int(row[16]),
        last_triggered_utc=None if row[17] is None else str(row[17]),
    )


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> JobLifecycleRecord:
    return JobLifecycleRecord(
        job_id=str(row[0]),
        job_revision_id=str(row[1]),
        state=JobLifecycleState(str(row[2])),
        row_version=_required_int(row[3]),
        archived_utc=None if row[4] is None else str(row[4]),
    )


def _required_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("SQLite integer field must not be bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError("SQLite integer field must be int-compatible")
