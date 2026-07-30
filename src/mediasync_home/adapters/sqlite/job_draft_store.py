from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediasync_home.application.job_drafts import (
    BackupBehavior,
    DraftTarget,
    ExtraFilesPreset,
    FileSelectionPreset,
    JobDraftStore,
    PerformancePreset,
    RetentionPreset,
    StandardBackupDefaults,
    StandardBackupJobDraft,
    VerificationPreset,
)


class SqliteJobDraftStoreError(ValueError):
    pass


class SqliteJobDraftStore(JobDraftStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        outer_transaction = self._connection.in_transaction
        try:
            self._connection.execute(
                """
                INSERT INTO standard_backup_job_drafts (
                    draft_id,
                    schema_version,
                    source_name,
                    source_path_label,
                    defaults_json,
                    targets_json,
                    updated_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(draft_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    source_name = excluded.source_name,
                    source_path_label = excluded.source_path_label,
                    defaults_json = excluded.defaults_json,
                    targets_json = excluded.targets_json,
                    updated_utc = excluded.updated_utc
                """,
                (
                    draft.draft_id,
                    draft.schema_version,
                    draft.source_name,
                    draft.source_path_label,
                    _serialize_defaults(draft.defaults),
                    _serialize_targets(draft.targets),
                ),
            )
            if not outer_transaction:
                self._connection.commit()
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteJobDraftStoreError("DRAFT_PERSISTENCE_FAILED") from exc

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None:
        row = self._connection.execute(
            """
            SELECT
                draft_id,
                schema_version,
                source_name,
                source_path_label,
                defaults_json,
                targets_json
            FROM standard_backup_job_drafts
            WHERE draft_id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None:
            return None
        return StandardBackupJobDraft(
            draft_id=str(row[0]),
            schema_version=int(row[1]),
            source_name=_optional_str(row[2]),
            source_path_label=_optional_str(row[3]),
            defaults=_deserialize_defaults(str(row[4])),
            targets=_deserialize_targets(str(row[5])),
        )


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
    data = _json_object(payload, "DRAFT_DEFAULTS_JSON_INVALID")
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
        raise SqliteJobDraftStoreError("DRAFT_DEFAULTS_JSON_INVALID") from exc


def _serialize_targets(targets: tuple[DraftTarget, ...]) -> str:
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


def _deserialize_targets(payload: str) -> tuple[DraftTarget, ...]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqliteJobDraftStoreError("DRAFT_TARGETS_JSON_INVALID") from exc
    if not isinstance(data, list):
        raise SqliteJobDraftStoreError("DRAFT_TARGETS_JSON_INVALID")
    targets: list[DraftTarget] = []
    for item in data:
        if not isinstance(item, dict):
            raise SqliteJobDraftStoreError("DRAFT_TARGETS_JSON_INVALID")
        try:
            targets.append(
                DraftTarget(
                    name=str(item["name"]),
                    path_label=str(item["path_label"]),
                    independent_device_id=_optional_str(item.get("independent_device_id")),
                )
            )
        except KeyError as exc:
            raise SqliteJobDraftStoreError("DRAFT_TARGETS_JSON_INVALID") from exc
    return tuple(targets)


def _json_object(payload: str, reason: str) -> dict[str, object]:
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqliteJobDraftStoreError(reason) from exc
    if not isinstance(data, dict):
        raise SqliteJobDraftStoreError(reason)
    return data


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
