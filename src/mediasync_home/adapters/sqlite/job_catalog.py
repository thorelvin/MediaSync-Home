from __future__ import annotations

import json
import sqlite3
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


class SqliteJobCatalogError(ValueError):
    pass


class SqliteStandardBackupJobCatalog(StandardBackupJobCatalog):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_standard_backup_job(self, job: SealedStandardBackupJob) -> None:
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
            self._connection.execute(
                """
                INSERT INTO job_revisions (job_id, id, filter_set_id)
                    VALUES (?, ?, ?)
                """,
                (job.job_id, job.job_revision_id, job.filter_set_id),
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
                details.targets_json
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
                details.targets_json
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
                details.targets_json
            FROM standard_backup_job_revision_details AS details
            INNER JOIN job_revisions AS revisions
                ON revisions.job_id = details.job_id
                AND revisions.id = details.job_revision_id
            WHERE details.idempotency_key = ?
            """,
            (idempotency_key,),
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
