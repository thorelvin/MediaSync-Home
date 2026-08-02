from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any

from mediasync_home.application.job_creation import (
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
    StandardBackupJobCatalog,
)
from mediasync_home.application.job_drafts import (
    AutomationPolicy,
    BackupBehavior,
    ExtraFilesPreset,
    FileSelectionPreset,
    PerformancePreset,
    RetentionPreset,
    StandardBackupDefaults,
    VerificationPreset,
    draft_path_labels_overlap,
)
from mediasync_home.application.job_read_models import (
    BackupAnalysisRequestSummary,
    BackupAutomationScheduleSummary,
    InitialBackupPlanSummary,
    StandardBackupJobDetail,
    StandardBackupJobSummary,
    StandardBackupTargetSummary,
)
from mediasync_home.application.job_scheduling import daily_backup_schedule_id
from mediasync_home.application.job_lifecycle import JobLifecycleState
from mediasync_home.application.file_filters import (
    canonical_file_filter_policy_json,
    default_file_filter_policy,
)


class SqliteJobCatalogError(ValueError):
    pass


FILTER_SET_INITIAL_VERSION = 1


class SqliteStandardBackupJobCatalog(StandardBackupJobCatalog):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_standard_backup_job(self, job: SealedStandardBackupJob) -> None:
        if job.filter_set_version != FILTER_SET_INITIAL_VERSION:
            raise SqliteJobCatalogError("FILTER_SET_INITIAL_VERSION_INVALID")
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._reject_active_standard_backup_root_overlap(job)
            self._connection.execute(
                "INSERT INTO jobs (id, kind) VALUES (?, 'multi_target_backup')",
                (job.job_id,),
            )
            self._connection.execute(
                """
                INSERT INTO filter_sets (job_id, id, description)
                    VALUES (?, ?, 'standard backup defaults')
                """,
                (job.job_id, job.filter_set_id),
            )
            filter_rules_json = _serialize_filter_rules(job.defaults)
            self._connection.execute(
                """
                INSERT INTO filter_set_versions (
                    job_id,
                    filter_set_id,
                    version,
                    rules_hash,
                    rules_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.filter_set_id,
                    job.filter_set_version,
                    hashlib.sha256(filter_rules_json.encode("utf-8")).hexdigest(),
                    filter_rules_json,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO job_revisions (
                    job_id,
                    id,
                    filter_set_id,
                    filter_set_version
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.job_revision_id,
                    job.filter_set_id,
                    job.filter_set_version,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO standard_backup_job_revision_details (
                    job_id,
                    job_revision_id,
                    draft_id,
                    command_request_id,
                    idempotency_key,
                    source_name,
                    source_path_label,
                    defaults_json,
                    targets_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.job_revision_id,
                    job.draft_id,
                    job.command_request_id,
                    job.idempotency_key,
                    job.source_name,
                    job.source_path_label,
                    _serialize_defaults(job.defaults),
                    _serialize_targets(job.targets),
                ),
            )
            self._connection.execute(
                "INSERT INTO job_heads (job_id, active_revision_id) VALUES (?, ?)",
                (job.job_id, job.job_revision_id),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except SqliteJobCatalogError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteJobCatalogError(
                "STANDARD_BACKUP_JOB_PERSISTENCE_FAILED"
            ) from exc

    def load_standard_backup_job(self, job_id: str) -> SealedStandardBackupJob | None:
        return self._load_one(
            """
            SELECT
                details.job_id,
                details.job_revision_id,
                revisions.filter_set_id,
                details.draft_id,
                details.command_request_id,
                details.idempotency_key,
                details.source_name,
                details.source_path_label,
                details.defaults_json,
                details.targets_json,
                revisions.filter_set_version
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = details.job_id
                AND heads.active_revision_id = details.job_revision_id
            INNER JOIN jobs
                ON jobs.id = details.job_id
                AND jobs.lifecycle_state = 'ACTIVE'
            WHERE details.job_id = ?
            """,
            (job_id,),
        )

    def load_standard_backup_job_revision(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> SealedStandardBackupJob | None:
        return self._load_one(
            """
            SELECT
                details.job_id,
                details.job_revision_id,
                revisions.filter_set_id,
                details.draft_id,
                details.command_request_id,
                details.idempotency_key,
                details.source_name,
                details.source_path_label,
                details.defaults_json,
                details.targets_json,
                revisions.filter_set_version
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            WHERE details.job_id = ?
                AND details.job_revision_id = ?
            """,
            (job_id, job_revision_id),
        )

    def append_standard_backup_job_revision(
        self,
        job: SealedStandardBackupJob,
        *,
        expected_active_revision_id: str,
    ) -> None:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            active_row = self._connection.execute(
                """
                SELECT
                    heads.active_revision_id,
                    jobs.lifecycle_state,
                    revisions.filter_set_id,
                    revisions.filter_set_version
                FROM job_heads AS heads
                INNER JOIN jobs ON jobs.id = heads.job_id
                INNER JOIN job_revisions AS revisions
                    ON revisions.job_id = heads.job_id
                    AND revisions.id = heads.active_revision_id
                WHERE heads.job_id = ?
                """,
                (job.job_id,),
            ).fetchone()
            if active_row is None:
                raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_NOT_FOUND")
            if str(active_row[1]) != JobLifecycleState.ACTIVE.value:
                raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_NOT_ACTIVE")
            active_revision_id = str(active_row[0])
            if active_revision_id != expected_active_revision_id:
                raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_REVISION_STALE")
            if job.filter_set_id != str(active_row[2]):
                raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_FILTER_SET_CHANGED")
            active_filter_version = _required_int(active_row[3])
            self._persist_edited_filter_version(
                job,
                active_filter_version=active_filter_version,
            )
            self._reject_active_standard_backup_root_overlap(
                job,
                exclude_job_id=job.job_id,
            )
            self._connection.execute(
                """
                INSERT INTO job_revisions (
                    job_id,
                    id,
                    filter_set_id,
                    filter_set_version
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.job_revision_id,
                    job.filter_set_id,
                    job.filter_set_version,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO standard_backup_job_revision_details (
                    job_id,
                    job_revision_id,
                    draft_id,
                    command_request_id,
                    idempotency_key,
                    source_name,
                    source_path_label,
                    defaults_json,
                    targets_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.job_revision_id,
                    job.draft_id,
                    job.command_request_id,
                    job.idempotency_key,
                    job.source_name,
                    job.source_path_label,
                    _serialize_defaults(job.defaults),
                    _serialize_targets(job.targets),
                ),
            )
            cursor = self._connection.execute(
                """
                UPDATE job_heads
                SET active_revision_id = ?
                WHERE job_id = ?
                    AND active_revision_id = ?
                """,
                (
                    job.job_revision_id,
                    job.job_id,
                    expected_active_revision_id,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_REVISION_STALE")
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except SqliteJobCatalogError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteJobCatalogError(
                "STANDARD_BACKUP_JOB_REVISION_APPEND_FAILED"
            ) from exc

    def list_active_standard_backup_jobs(self) -> tuple[SealedStandardBackupJob, ...]:
        return self._load_many(
            """
            SELECT
                details.job_id,
                details.job_revision_id,
                revisions.filter_set_id,
                details.draft_id,
                details.command_request_id,
                details.idempotency_key,
                details.source_name,
                details.source_path_label,
                details.defaults_json,
                details.targets_json,
                revisions.filter_set_version
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = details.job_id
                AND heads.active_revision_id = details.job_revision_id
            INNER JOIN jobs
                ON jobs.id = details.job_id
                AND jobs.lifecycle_state = 'ACTIVE'
            ORDER BY details.job_id
            """,
            (),
        )

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None:
        return self._load_one(
            """
            SELECT
                details.job_id,
                details.job_revision_id,
                revisions.filter_set_id,
                details.draft_id,
                details.command_request_id,
                details.idempotency_key,
                details.source_name,
                details.source_path_label,
                details.defaults_json,
                details.targets_json,
                revisions.filter_set_version
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            WHERE details.idempotency_key = ?
            """,
            (idempotency_key,),
        )

    def list_standard_backup_job_summaries(
        self,
        *,
        lifecycle_state: JobLifecycleState,
        limit: int,
        offset: int,
    ) -> tuple[StandardBackupJobSummary, ...]:
        if limit < 1 or offset < 0:
            raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_QUERY_BOUNDS_INVALID")
        rows = self._connection.execute(
            """
            SELECT
                details.job_id,
                details.job_revision_id,
                revisions.filter_set_id,
                details.source_name,
                details.source_path_label,
                details.targets_json,
                revisions.filter_set_version,
                jobs.lifecycle_state,
                jobs.lifecycle_row_version,
                jobs.archived_utc
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = details.job_id
                AND heads.active_revision_id = details.job_revision_id
            INNER JOIN jobs ON jobs.id = details.job_id
            WHERE jobs.lifecycle_state = ?
            ORDER BY details.job_id
            LIMIT ? OFFSET ?
            """,
            (lifecycle_state.value, limit, offset),
        ).fetchall()
        return tuple(_job_summary_from_row(row) for row in rows)

    def list_active_standard_backup_job_summaries(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[StandardBackupJobSummary, ...]:
        return self.list_standard_backup_job_summaries(
            lifecycle_state=JobLifecycleState.ACTIVE,
            limit=limit,
            offset=offset,
        )

    def load_standard_backup_job_detail(
        self, job_id: str
    ) -> StandardBackupJobDetail | None:
        row = self._connection.execute(
            """
            SELECT
                details.job_id,
                details.job_revision_id,
                revisions.filter_set_id,
                details.source_name,
                details.source_path_label,
                details.defaults_json,
                details.targets_json,
                revisions.filter_set_version,
                jobs.lifecycle_state,
                jobs.lifecycle_row_version,
                jobs.archived_utc
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = details.job_id
                AND heads.active_revision_id = details.job_revision_id
            INNER JOIN jobs ON jobs.id = details.job_id
            WHERE details.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        detail = _job_detail_from_row(row)
        registration_rows = self._connection.execute(
            """
            SELECT
                bindings.ordinal,
                bindings.endpoint_id,
                bindings.registration_state,
                bindings.registration_reason_code,
                observations.marker_json
            FROM standard_backup_job_endpoint_bindings AS bindings
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = bindings.endpoint_id
                AND observations.endpoint_revision_id = bindings.endpoint_revision_id
            WHERE job_id = ?
                AND job_revision_id = ?
                AND role = 'TARGET'
            ORDER BY bindings.ordinal
            """,
            (detail.job_id, detail.job_revision_id),
        ).fetchall()
        registrations = {
            int(registration_row[0]): _target_registration_metadata(registration_row)
            for registration_row in registration_rows
        }
        initial_plan_row = self._connection.execute(
            """
            SELECT
                materializations.state,
                materializations.reason_code,
                materializations.analysis_id,
                materializations.plan_id,
                seals.plan_checksum,
                materializations.operation_count,
                materializations.planned_bytes,
                materializations.plan_runnable,
                materializations.next_action
            FROM initial_backup_plan_materializations AS materializations
            LEFT JOIN plan_seal_details AS seals
                ON seals.plan_id = materializations.plan_id
            WHERE materializations.job_id = ?
                AND materializations.job_revision_id = ?
            ORDER BY
                materializations.completed_utc DESC,
                materializations.materialization_id DESC
            LIMIT 1
            """,
            (detail.job_id, detail.job_revision_id),
        ).fetchone()
        analysis_request_row = self._connection.execute(
            """
            SELECT
                request_id,
                state,
                requested_utc,
                reason_code,
                analysis_id,
                plan_id,
                start_when_safe,
                started_run_id,
                row_version
            FROM backup_analysis_requests
            WHERE job_id = ?
                AND job_revision_id = ?
            ORDER BY requested_utc DESC, request_id DESC
            LIMIT 1
            """,
            (detail.job_id, detail.job_revision_id),
        ).fetchone()
        automation_schedule_row = self._connection.execute(
            """
            SELECT
                schedules.id,
                schedules.trigger_type,
                schedules.enabled,
                schedules.row_version,
                schedules.definition_generation,
                schedules.configuration_json,
                schedules.time_zone_id,
                schedules.task_logon_type,
                schedules.requires_network,
                schedules.run_only_when_logged_on,
                external_resource_state.state,
                external_resource_state.last_error_code
            FROM schedules
            LEFT JOIN external_resource_state
                ON external_resource_state.resource_type = 'task_scheduler'
                AND external_resource_state.resource_id = schedules.id
            WHERE schedules.id = ? AND schedules.job_id = ?
            """,
            (daily_backup_schedule_id(detail.job_id), detail.job_id),
        ).fetchone()
        return replace(
            detail,
            targets=tuple(
                replace(
                    target,
                    target_ordinal=index,
                    endpoint_id=registrations.get(
                        index, (None, None, None, None, None, None)
                    )[0],
                    registration_state=registrations.get(
                        index, (None, None, None, None, None, None)
                    )[1],
                    registration_reason_code=registrations.get(
                        index, (None, None, None, None, None, None)
                    )[2],
                    foreign_owner_installation_id=registrations.get(
                        index, (None, None, None, None, None, None)
                    )[3],
                    foreign_ownership_epoch=registrations.get(
                        index, (None, None, None, None, None, None)
                    )[4],
                    foreign_recovery_status=registrations.get(
                        index, (None, None, None, None, None, None)
                    )[5],
                )
                for index, target in enumerate(detail.targets, start=1)
            ),
            initial_plan=(
                None
                if initial_plan_row is None
                else _initial_plan_summary_from_row(initial_plan_row)
            ),
            latest_analysis_request=(
                None
                if analysis_request_row is None
                else BackupAnalysisRequestSummary(
                    request_id=str(analysis_request_row[0]),
                    state=str(analysis_request_row[1]),
                    requested_utc=str(analysis_request_row[2]),
                    reason_code=(
                        None
                        if analysis_request_row[3] is None
                        else str(analysis_request_row[3])
                    ),
                    analysis_id=(
                        None
                        if analysis_request_row[4] is None
                        else str(analysis_request_row[4])
                    ),
                    plan_id=(
                        None
                        if analysis_request_row[5] is None
                        else str(analysis_request_row[5])
                    ),
                    start_when_safe=bool(_required_int(analysis_request_row[6])),
                    started_run_id=(
                        None
                        if analysis_request_row[7] is None
                        else str(analysis_request_row[7])
                    ),
                    row_version=_required_int(analysis_request_row[8]),
                )
            ),
            automation_schedule=(
                None
                if automation_schedule_row is None
                else _automation_schedule_summary_from_row(automation_schedule_row)
            ),
        )

    def _load_many(
        self,
        query: str,
        parameters: tuple[object, ...],
    ) -> tuple[SealedStandardBackupJob, ...]:
        rows = self._connection.execute(query, parameters).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def _load_one(
        self,
        query: str,
        parameters: tuple[object, ...],
    ) -> SealedStandardBackupJob | None:
        row = self._connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        return _job_from_row(row)

    def _reject_active_standard_backup_root_overlap(
        self,
        job: SealedStandardBackupJob,
        *,
        exclude_job_id: str | None = None,
    ) -> None:
        new_roots = _job_roots(job)
        for existing_job in self.list_active_standard_backup_jobs():
            if existing_job.job_id == exclude_job_id:
                continue
            for new_root in new_roots:
                for existing_root in _job_roots(existing_job):
                    if not new_root[1] and not existing_root[1]:
                        continue
                    if draft_path_labels_overlap(new_root[0], existing_root[0]):
                        raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_ROOT_OVERLAP")

    def _persist_edited_filter_version(
        self,
        job: SealedStandardBackupJob,
        *,
        active_filter_version: int,
    ) -> None:
        filter_rules_json = _serialize_filter_rules(job.defaults)
        if job.filter_set_version == active_filter_version:
            row = self._connection.execute(
                """
                SELECT rules_json
                FROM filter_set_versions
                WHERE job_id = ?
                    AND filter_set_id = ?
                    AND version = ?
                """,
                (job.job_id, job.filter_set_id, active_filter_version),
            ).fetchone()
            if row is None or str(row[0]) != filter_rules_json:
                raise SqliteJobCatalogError(
                    "STANDARD_BACKUP_JOB_FILTER_VERSION_REUSE_INVALID"
                )
            return
        if job.filter_set_version != active_filter_version + 1:
            raise SqliteJobCatalogError(
                "STANDARD_BACKUP_JOB_FILTER_VERSION_SEQUENCE_INVALID"
            )
        self._connection.execute(
            """
            INSERT INTO filter_set_versions (
                job_id,
                filter_set_id,
                version,
                rules_hash,
                rules_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                job.filter_set_id,
                job.filter_set_version,
                hashlib.sha256(filter_rules_json.encode("utf-8")).hexdigest(),
                filter_rules_json,
            ),
        )


def _job_from_row(row: sqlite3.Row | tuple[object, ...]) -> SealedStandardBackupJob:
    return SealedStandardBackupJob(
        job_id=str(row[0]),
        job_revision_id=str(row[1]),
        filter_set_id=str(row[2]),
        draft_id=str(row[3]),
        command_request_id=str(row[4]),
        idempotency_key=str(row[5]),
        source_name=str(row[6]),
        source_path_label=str(row[7]),
        defaults=_deserialize_defaults(str(row[8])),
        targets=_deserialize_targets(str(row[9])),
        filter_set_version=_required_int(row[10]),
    )


def _job_summary_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> StandardBackupJobSummary:
    return StandardBackupJobSummary(
        job_id=str(row[0]),
        job_revision_id=str(row[1]),
        filter_set_id=str(row[2]),
        source_name=str(row[3]),
        source_path_label=str(row[4]),
        targets=tuple(
            StandardBackupTargetSummary(
                name=target.name,
                path_label=target.path_label,
                independent_device_id=target.independent_device_id,
            )
            for target in _deserialize_targets(str(row[5]))
        ),
        filter_set_version=_required_int(row[6]),
        lifecycle_state=JobLifecycleState(str(row[7])),
        lifecycle_row_version=_required_int(row[8]),
        archived_utc=_optional_str(row[9]),
    )


def _job_detail_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> StandardBackupJobDetail:
    targets = _deserialize_targets(str(row[6]))
    return StandardBackupJobDetail(
        job_id=str(row[0]),
        job_revision_id=str(row[1]),
        filter_set_id=str(row[2]),
        source_name=str(row[3]),
        source_path_label=str(row[4]),
        defaults=_deserialize_defaults(str(row[5])),
        targets=tuple(
            StandardBackupTargetSummary(
                name=target.name,
                path_label=target.path_label,
                independent_device_id=target.independent_device_id,
            )
            for target in targets
        ),
        filter_set_version=_required_int(row[7]),
        lifecycle_state=JobLifecycleState(str(row[8])),
        lifecycle_row_version=_required_int(row[9]),
        archived_utc=_optional_str(row[10]),
    )


def _initial_plan_summary_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> InitialBackupPlanSummary:
    state = str(row[0])
    plan_id = _optional_str(row[3])
    plan_checksum = _optional_str(row[4])
    operation_count = _required_non_negative_int(row[5])
    return InitialBackupPlanSummary(
        state=state,
        reason_code=str(row[1]),
        analysis_id=_optional_str(row[2]),
        plan_id=plan_id,
        plan_checksum=plan_checksum,
        operation_count=operation_count,
        planned_bytes=_required_non_negative_int(row[6]),
        plan_runnable=(
            state == "SEALED"
            and plan_id is not None
            and plan_checksum is not None
            and operation_count > 0
        ),
        next_action=str(row[8]),
    )


def _automation_schedule_summary_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> BackupAutomationScheduleSummary:
    daily_local_time = None
    try:
        configuration = json.loads(str(row[5]))
        if isinstance(configuration, dict) and configuration.get("kind") == "daily":
            hour = configuration.get("hour")
            minute = configuration.get("minute")
            if (
                isinstance(hour, int)
                and not isinstance(hour, bool)
                and 0 <= hour <= 23
                and isinstance(minute, int)
                and not isinstance(minute, bool)
                and 0 <= minute <= 59
            ):
                daily_local_time = f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return BackupAutomationScheduleSummary(
        schedule_id=str(row[0]),
        trigger_type=str(row[1]),
        enabled=bool(_required_int(row[2])),
        row_version=_required_int(row[3]),
        definition_generation=_required_int(row[4]),
        daily_local_time=daily_local_time,
        time_zone_id=_optional_str(row[6]),
        task_logon_type=str(row[7]),
        requires_network=bool(_required_int(row[8])),
        run_only_when_logged_on=bool(_required_int(row[9])),
        reconciliation_state=_optional_str(row[10]),
        reconciliation_error_code=_optional_str(row[11]),
    )


def _job_roots(job: SealedStandardBackupJob) -> tuple[tuple[str, bool], ...]:
    roots: list[tuple[str, bool]] = [(job.source_path_label, False)]
    roots.extend((target.path_label, True) for target in job.targets)
    return tuple(roots)


def _serialize_defaults(defaults: StandardBackupDefaults) -> str:
    return json.dumps(
        {
            "behavior": defaults.behavior.value,
            "file_selection": defaults.file_selection.value,
            "verification": defaults.verification.value,
            "retention": defaults.retention.value,
            "extra_files": defaults.extra_files.value,
            "performance": defaults.performance.value,
            "automation_policy": defaults.automation_policy.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _serialize_filter_rules(defaults: StandardBackupDefaults) -> str:
    policy = default_file_filter_policy()
    if defaults.file_selection.value != policy.preset:
        raise SqliteJobCatalogError("STANDARD_BACKUP_FILE_SELECTION_UNSUPPORTED")
    return canonical_file_filter_policy_json(policy)


def _deserialize_defaults(payload: str) -> StandardBackupDefaults:
    data = _json_object(payload, "STANDARD_BACKUP_JOB_DEFAULTS_JSON_INVALID")
    try:
        return StandardBackupDefaults(
            behavior=BackupBehavior(str(data["behavior"])),
            file_selection=FileSelectionPreset(str(data["file_selection"])),
            verification=VerificationPreset(str(data["verification"])),
            retention=RetentionPreset(str(data["retention"])),
            extra_files=ExtraFilesPreset(str(data["extra_files"])),
            performance=PerformancePreset(str(data["performance"])),
            automation_policy=AutomationPolicy(
                str(data.get("automation_policy", AutomationPolicy.NEW_FILES_ONLY.value))
            ),
        )
    except (KeyError, ValueError) as exc:
        raise SqliteJobCatalogError(
            "STANDARD_BACKUP_JOB_DEFAULTS_JSON_INVALID"
        ) from exc


def _serialize_targets(targets: tuple[SealedStandardBackupTarget, ...]) -> str:
    return json.dumps(
        [
            {
                "name": target.name,
                "path_label": target.path_label,
                "independent_device_id": target.independent_device_id,
            }
            for target in targets
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


def _deserialize_targets(payload: str) -> tuple[SealedStandardBackupTarget, ...]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_TARGETS_JSON_INVALID") from exc
    if not isinstance(data, list):
        raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_TARGETS_JSON_INVALID")
    targets: list[SealedStandardBackupTarget] = []
    for item in data:
        if not isinstance(item, dict):
            raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_TARGETS_JSON_INVALID")
        try:
            targets.append(
                SealedStandardBackupTarget(
                    name=str(item["name"]),
                    path_label=str(item["path_label"]),
                    independent_device_id=_optional_str(
                        item.get("independent_device_id")
                    ),
                )
            )
        except KeyError as exc:
            raise SqliteJobCatalogError(
                "STANDARD_BACKUP_JOB_TARGETS_JSON_INVALID"
            ) from exc
    return tuple(targets)


def _json_object(payload: str, reason: str) -> dict[str, object]:
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqliteJobCatalogError(reason) from exc
    if not isinstance(data, dict):
        raise SqliteJobCatalogError(reason)
    return data


def _target_registration_metadata(
    row: sqlite3.Row | tuple[object, ...],
) -> tuple[str, str, str, str | None, int | None, str | None]:
    endpoint_id = str(row[1])
    registration_state = str(row[2])
    reason_code = str(row[3])
    if reason_code != "ENDPOINT_TARGET_FOREIGN_READ_ONLY" or not isinstance(
        row[4], str
    ):
        return endpoint_id, registration_state, reason_code, None, None, None
    try:
        marker: Any = json.loads(row[4])
    except json.JSONDecodeError:
        return endpoint_id, registration_state, reason_code, None, None, "UNKNOWN"
    if not isinstance(marker, dict):
        return endpoint_id, registration_state, reason_code, None, None, "UNKNOWN"
    owner = marker.get("owner_installation_id")
    epoch = marker.get("ownership_epoch")
    if (
        not isinstance(owner, str)
        or not owner
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 1
    ):
        return endpoint_id, registration_state, reason_code, None, None, "UNKNOWN"
    return (
        endpoint_id,
        registration_state,
        reason_code,
        owner,
        epoch,
        "CHECK_REQUIRED_UNDER_LOCK",
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_FILTER_VERSION_INVALID")
    return value


def _required_non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SqliteJobCatalogError("INITIAL_BACKUP_PLAN_SUMMARY_INVALID")
    return value
