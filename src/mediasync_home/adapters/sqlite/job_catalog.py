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
    InitialBackupPlanSummary,
    StandardBackupJobDetail,
    StandardBackupJobSummary,
    StandardBackupTargetSummary,
)


class SqliteJobCatalogError(ValueError):
    pass


FILTER_SET_INITIAL_VERSION = 1
FILTER_RULES_SCHEMA_VERSION = 1


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
            raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_PERSISTENCE_FAILED") from exc

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
            WHERE details.job_id = ?
            """,
            (job_id,),
        )

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

    def list_active_standard_backup_job_summaries(
        self,
        *,
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
                revisions.filter_set_version
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = details.job_id
                AND heads.active_revision_id = details.job_revision_id
            ORDER BY details.job_id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return tuple(_job_summary_from_row(row) for row in rows)

    def load_standard_backup_job_detail(self, job_id: str) -> StandardBackupJobDetail | None:
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
                revisions.filter_set_version
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = details.job_id
                AND heads.active_revision_id = details.job_revision_id
            WHERE details.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        detail = _job_detail_from_row(row)
        registration_rows = self._connection.execute(
            """
            SELECT ordinal, registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ?
                AND job_revision_id = ?
                AND role = 'TARGET'
            ORDER BY ordinal
            """,
            (detail.job_id, detail.job_revision_id),
        ).fetchall()
        registrations = {
            int(registration_row[0]): (
                str(registration_row[1]),
                str(registration_row[2]),
            )
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
        return replace(
            detail,
            targets=tuple(
                replace(
                    target,
                    registration_state=registrations.get(index, (None, None))[0],
                    registration_reason_code=registrations.get(index, (None, None))[1],
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
                    start_when_safe=bool(
                        _required_int(analysis_request_row[6])
                    ),
                    started_run_id=(
                        None
                        if analysis_request_row[7] is None
                        else str(analysis_request_row[7])
                    ),
                    row_version=_required_int(analysis_request_row[8]),
                )
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

    def _reject_active_standard_backup_root_overlap(self, job: SealedStandardBackupJob) -> None:
        new_roots = _job_roots(job)
        for existing_job in self.list_active_standard_backup_jobs():
            for new_root in new_roots:
                for existing_root in _job_roots(existing_job):
                    if not new_root[1] and not existing_root[1]:
                        continue
                    if draft_path_labels_overlap(new_root[0], existing_root[0]):
                        raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_ROOT_OVERLAP")


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


def _job_summary_from_row(row: sqlite3.Row | tuple[object, ...]) -> StandardBackupJobSummary:
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
    )


def _job_detail_from_row(row: sqlite3.Row | tuple[object, ...]) -> StandardBackupJobDetail:
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
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _serialize_filter_rules(defaults: StandardBackupDefaults) -> str:
    return json.dumps(
        {
            "preset": defaults.file_selection.value,
            "schema_version": FILTER_RULES_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


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
        )
    except (KeyError, ValueError) as exc:
        raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_DEFAULTS_JSON_INVALID") from exc


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
                    independent_device_id=_optional_str(item.get("independent_device_id")),
                )
            )
        except KeyError as exc:
            raise SqliteJobCatalogError("STANDARD_BACKUP_JOB_TARGETS_JSON_INVALID") from exc
    return tuple(targets)


def _json_object(payload: str, reason: str) -> dict[str, object]:
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqliteJobCatalogError(reason) from exc
    if not isinstance(data, dict):
        raise SqliteJobCatalogError(reason)
    return data


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
