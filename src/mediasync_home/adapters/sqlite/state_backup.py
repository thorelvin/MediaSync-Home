from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteStore,
    StateStoreLayout,
    apply_sqlite_connection_policy,
    catalog_reader_policy,
    recovery_reader_policy,
)


STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION = 2
STATE_BACKUP_SET_INTENT_SCHEMA_VERSION = 1
STATE_RESTORE_EPOCH_SCHEMA_VERSION = 1
STATE_BACKUP_SET_STORES = (SqliteStore.CATALOG, SqliteStore.RECOVERY)
STATE_BACKUP_SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
BACKUP_SET_INTENT_FILENAME = "backup-set.intent.json"
BACKUP_SET_MANIFEST_FILENAME = "backup-set.manifest.json"
STATE_RESTORE_EPOCHS_DIR_NAME = "state-restore-epochs"
STATE_RESTORE_INTENT_FILENAME = "state-restore.intent.json"
STATE_RESTORE_COMMITTED_FILENAME = "state-restore.committed.json"
STATE_RESTORE_ROLLED_BACK_FILENAME = "state-restore.rolled-back.json"
STATE_COMPACTION_EPOCH_SCHEMA_VERSION = 1
STATE_COMPACTION_EPOCHS_DIR_NAME = "state-compaction-epochs"
STATE_COMPACTION_INTENT_FILENAME = "state-compaction.intent.json"
STATE_COMPACTION_COMMITTED_FILENAME = "state-compaction.committed.json"
STATE_COMPACTION_ROLLED_BACK_FILENAME = "state-compaction.rolled-back.json"
STATE_RESTORE_MAINTENANCE_TERMINAL_RUN_STATES = (
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "PARTIAL_FAILURE",
    "FAILED",
    "CANCELLED",
    "BLOCKED_BY_SAFETY",
)
STATE_RESTORE_MAINTENANCE_TERMINAL_RUN_TARGET_STATES = (
    "SUCCEEDED",
    "SUCCEEDED_WITH_WARNINGS",
    "FAILED",
    "CANCELLED",
    "BLOCKED",
)
STATE_RESTORE_MAINTENANCE_TERMINAL_COMMAND_RECEIPT_STATES = (
    "SUCCEEDED",
    "REJECTED",
    "FAILED",
    "CANCELLED",
)
STATE_RESTORE_MAINTENANCE_TERMINAL_OUTBOX_STATES = ("DELIVERED", "DEAD_LETTER")
STATE_RESTORE_MAINTENANCE_UNRESOLVED_INTENT_STATES = ("BUILDING", "DURABLE")


class SqliteStateBackupViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SqliteStateStoreBackup:
    store: SqliteStore
    file_name: str
    size_bytes: int
    sha256: str
    schema_version: int
    migration_count: int
    latest_migration_utc: str | None
    page_count: int
    quick_check: str
    foreign_key_violations: int
    unresolved_target_intent_count: int = 0
    target_intent_high_water_utc: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "migration_count": self.migration_count,
            "latest_migration_utc": self.latest_migration_utc,
            "page_count": self.page_count,
            "quick_check": self.quick_check,
            "foreign_key_violations": self.foreign_key_violations,
            "unresolved_target_intent_count": self.unresolved_target_intent_count,
            "target_intent_high_water_utc": self.target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateBackupManifest:
    backup_set_id: str
    created_utc: str
    state_set_hash: str
    stores: tuple[SqliteStateStoreBackup, ...]
    schema_version: int = STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backup_set_id": self.backup_set_id,
            "created_utc": self.created_utc,
            "state_set_hash": self.state_set_hash,
            "stores": [entry.to_payload() for entry in self.stores],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreFile:
    store: SqliteStore
    backup_path: Path
    target_path: Path
    size_bytes: int
    sha256: str
    schema_version: int
    migration_count: int
    latest_migration_utc: str | None
    page_count: int
    quick_check: str
    foreign_key_violations: int
    unresolved_target_intent_count: int = 0
    target_intent_high_water_utc: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "backup_path": str(self.backup_path),
            "target_path": str(self.target_path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "migration_count": self.migration_count,
            "latest_migration_utc": self.latest_migration_utc,
            "page_count": self.page_count,
            "quick_check": self.quick_check,
            "foreign_key_violations": self.foreign_key_violations,
            "unresolved_target_intent_count": self.unresolved_target_intent_count,
            "target_intent_high_water_utc": self.target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestorePlan:
    backup_set_id: str
    state_set_hash: str
    target_layout: StateStoreLayout
    restore_files: tuple[SqliteStateRestoreFile, ...]
    backup_unresolved_target_intent_count: int
    backup_target_intent_high_water_utc: str | None
    current_unresolved_target_intent_count: int
    current_target_intent_high_water_utc: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "backup_set_id": self.backup_set_id,
            "state_set_hash": self.state_set_hash,
            "target_layout": {
                "root": str(self.target_layout.root),
                "catalog": str(self.target_layout.catalog),
                "recovery": str(self.target_layout.recovery),
            },
            "restore_files": [entry.to_payload() for entry in self.restore_files],
            "backup_unresolved_target_intent_count": (
                self.backup_unresolved_target_intent_count
            ),
            "backup_target_intent_high_water_utc": self.backup_target_intent_high_water_utc,
            "current_unresolved_target_intent_count": (
                self.current_unresolved_target_intent_count
            ),
            "current_target_intent_high_water_utc": self.current_target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateSidecarRollback:
    path: Path
    rollback_path: Path

    def to_payload(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "rollback_path": str(self.rollback_path),
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoredFile:
    store: SqliteStore
    target_path: Path
    rollback_path: Path | None
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]
    size_bytes: int
    sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "target_path": str(self.target_path),
            "rollback_path": None if self.rollback_path is None else str(self.rollback_path),
            "sidecar_rollbacks": [sidecar.to_payload() for sidecar in self.sidecar_rollbacks],
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreReceipt:
    restore_epoch_id: str
    backup_set_id: str
    state_set_hash: str
    intent_path: Path
    committed_path: Path
    restored_files: tuple[SqliteStateRestoredFile, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "restore_epoch_id": self.restore_epoch_id,
            "backup_set_id": self.backup_set_id,
            "state_set_hash": self.state_set_hash,
            "intent_path": str(self.intent_path),
            "committed_path": str(self.committed_path),
            "restored_files": [entry.to_payload() for entry in self.restored_files],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreEpochRecovery:
    restore_epoch_id: str
    intent_path: Path
    rolled_back_path: Path
    rolled_back_store_count: int
    removed_temp_file_count: int
    restored_sidecar_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "restore_epoch_id": self.restore_epoch_id,
            "intent_path": str(self.intent_path),
            "rolled_back_path": str(self.rolled_back_path),
            "rolled_back_store_count": self.rolled_back_store_count,
            "removed_temp_file_count": self.removed_temp_file_count,
            "restored_sidecar_count": self.restored_sidecar_count,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreEpochRecoveryReport:
    scanned_epoch_count: int
    committed_epoch_count: int
    previously_rolled_back_epoch_count: int
    recovered_epochs: tuple[SqliteStateRestoreEpochRecovery, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "scanned_epoch_count": self.scanned_epoch_count,
            "committed_epoch_count": self.committed_epoch_count,
            "previously_rolled_back_epoch_count": self.previously_rolled_back_epoch_count,
            "recovered_epochs": [entry.to_payload() for entry in self.recovered_epochs],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateCompactionFile:
    store: SqliteStore
    target_path: Path
    temp_path: Path
    rollback_path: Path
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]
    size_bytes: int
    sha256: str
    schema_version: int
    migration_count: int
    latest_migration_utc: str | None
    page_count: int
    quick_check: str
    foreign_key_violations: int
    unresolved_target_intent_count: int = 0
    target_intent_high_water_utc: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "target_path": str(self.target_path),
            "temp_path": str(self.temp_path),
            "rollback_path": str(self.rollback_path),
            "sidecar_rollbacks": [sidecar.to_payload() for sidecar in self.sidecar_rollbacks],
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
            "migration_count": self.migration_count,
            "latest_migration_utc": self.latest_migration_utc,
            "page_count": self.page_count,
            "quick_check": self.quick_check,
            "foreign_key_violations": self.foreign_key_violations,
            "unresolved_target_intent_count": self.unresolved_target_intent_count,
            "target_intent_high_water_utc": self.target_intent_high_water_utc,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateCompactedFile:
    store: SqliteStore
    target_path: Path
    rollback_path: Path | None
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]
    size_bytes: int
    sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "store": self.store.value,
            "target_path": str(self.target_path),
            "rollback_path": None if self.rollback_path is None else str(self.rollback_path),
            "sidecar_rollbacks": [sidecar.to_payload() for sidecar in self.sidecar_rollbacks],
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateCompactionReceipt:
    compaction_epoch_id: str
    state_set_hash: str
    intent_path: Path
    committed_path: Path
    compacted_files: tuple[SqliteStateCompactedFile, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "compaction_epoch_id": self.compaction_epoch_id,
            "state_set_hash": self.state_set_hash,
            "intent_path": str(self.intent_path),
            "committed_path": str(self.committed_path),
            "compacted_files": [entry.to_payload() for entry in self.compacted_files],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateCompactionEpochRecovery:
    compaction_epoch_id: str
    intent_path: Path
    rolled_back_path: Path
    rolled_back_store_count: int
    removed_temp_file_count: int
    restored_sidecar_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "compaction_epoch_id": self.compaction_epoch_id,
            "intent_path": str(self.intent_path),
            "rolled_back_path": str(self.rolled_back_path),
            "rolled_back_store_count": self.rolled_back_store_count,
            "removed_temp_file_count": self.removed_temp_file_count,
            "restored_sidecar_count": self.restored_sidecar_count,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateCompactionEpochRecoveryReport:
    scanned_epoch_count: int
    committed_epoch_count: int
    previously_rolled_back_epoch_count: int
    recovered_epochs: tuple[SqliteStateCompactionEpochRecovery, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "scanned_epoch_count": self.scanned_epoch_count,
            "committed_epoch_count": self.committed_epoch_count,
            "previously_rolled_back_epoch_count": self.previously_rolled_back_epoch_count,
            "recovered_epochs": [entry.to_payload() for entry in self.recovered_epochs],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreMaintenanceBlocker:
    code: str
    count: int
    store: SqliteStore | None = None
    detail: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "count": self.count,
        }
        if self.store is not None:
            payload["store"] = self.store.value
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True, slots=True)
class SqliteStateRestoreMaintenanceAdmission:
    active_run_count: int
    active_run_target_count: int
    non_terminal_command_receipt_count: int
    pending_outbox_message_count: int
    active_resource_lease_count: int
    unresolved_target_intent_segment_count: int
    incomplete_restore_epoch_count: int
    incomplete_compaction_epoch_count: int
    retained_run_target_lease_count: int = 0
    blockers: tuple[SqliteStateRestoreMaintenanceBlocker, ...] = ()

    @property
    def admitted(self) -> bool:
        return not self.blockers

    def with_retained_run_target_lease_count(
        self,
        retained_count: int,
    ) -> SqliteStateRestoreMaintenanceAdmission:
        if retained_count <= 0:
            return self
        return SqliteStateRestoreMaintenanceAdmission(
            active_run_count=self.active_run_count,
            active_run_target_count=self.active_run_target_count,
            non_terminal_command_receipt_count=self.non_terminal_command_receipt_count,
            pending_outbox_message_count=self.pending_outbox_message_count,
            active_resource_lease_count=self.active_resource_lease_count,
            unresolved_target_intent_segment_count=(
                self.unresolved_target_intent_segment_count
            ),
            incomplete_restore_epoch_count=self.incomplete_restore_epoch_count,
            incomplete_compaction_epoch_count=self.incomplete_compaction_epoch_count,
            retained_run_target_lease_count=retained_count,
            blockers=self.blockers
            + (
                SqliteStateRestoreMaintenanceBlocker(
                    code="STATE_RESTORE_MAINTENANCE_RETAINED_RUN_TARGET_LEASES",
                    count=retained_count,
                ),
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "admitted": self.admitted,
            "active_run_count": self.active_run_count,
            "active_run_target_count": self.active_run_target_count,
            "non_terminal_command_receipt_count": (
                self.non_terminal_command_receipt_count
            ),
            "pending_outbox_message_count": self.pending_outbox_message_count,
            "active_resource_lease_count": self.active_resource_lease_count,
            "unresolved_target_intent_segment_count": (
                self.unresolved_target_intent_segment_count
            ),
            "incomplete_restore_epoch_count": self.incomplete_restore_epoch_count,
            "incomplete_compaction_epoch_count": self.incomplete_compaction_epoch_count,
            "retained_run_target_lease_count": self.retained_run_target_lease_count,
            "blockers": [entry.to_payload() for entry in self.blockers],
        }


@dataclass(frozen=True, slots=True)
class SqliteStateMaintenanceRetentionPolicy:
    keep_latest_backup_sets: int = 1
    keep_latest_restore_epochs: int = 10
    keep_latest_compaction_epochs: int = 10
    retain_backup_sets_created_on_or_after_utc: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "keep_latest_backup_sets": self.keep_latest_backup_sets,
            "keep_latest_restore_epochs": self.keep_latest_restore_epochs,
            "keep_latest_compaction_epochs": self.keep_latest_compaction_epochs,
            "retain_backup_sets_created_on_or_after_utc": (
                self.retain_backup_sets_created_on_or_after_utc
            ),
        }


@dataclass(frozen=True, slots=True)
class SqliteStateMaintenanceRetentionArtifact:
    artifact_type: str
    artifact_id: str
    path: Path
    created_utc: str
    terminal_state: str
    associated_paths: tuple[Path, ...] = ()
    backup_set_id: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "artifact_type": self.artifact_type,
            "artifact_id": self.artifact_id,
            "path": str(self.path),
            "created_utc": self.created_utc,
            "terminal_state": self.terminal_state,
            "associated_paths": [str(path) for path in self.associated_paths],
        }
        if self.backup_set_id is not None:
            payload["backup_set_id"] = self.backup_set_id
        return payload


@dataclass(frozen=True, slots=True)
class SqliteStateMaintenanceRetentionSkip:
    artifact_type: str
    artifact_id: str
    path: Path
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "artifact_type": self.artifact_type,
            "artifact_id": self.artifact_id,
            "path": str(self.path),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SqliteStateMaintenanceRetentionPlan:
    backup_root: Path
    state_root: Path
    policy: SqliteStateMaintenanceRetentionPolicy
    delete_artifacts: tuple[SqliteStateMaintenanceRetentionArtifact, ...]
    retained_artifacts: tuple[SqliteStateMaintenanceRetentionArtifact, ...]
    skipped_artifacts: tuple[SqliteStateMaintenanceRetentionSkip, ...]
    protected_backup_set_ids: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "backup_root": str(self.backup_root),
            "state_root": str(self.state_root),
            "policy": self.policy.to_payload(),
            "delete_artifacts": [
                artifact.to_payload() for artifact in self.delete_artifacts
            ],
            "retained_artifacts": [
                artifact.to_payload() for artifact in self.retained_artifacts
            ],
            "skipped_artifacts": [skip.to_payload() for skip in self.skipped_artifacts],
            "protected_backup_set_ids": list(self.protected_backup_set_ids),
        }


@dataclass(frozen=True, slots=True)
class SqliteStateMaintenanceRetentionResult:
    plan: SqliteStateMaintenanceRetentionPlan
    deleted_artifacts: tuple[SqliteStateMaintenanceRetentionArtifact, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "plan": self.plan.to_payload(),
            "deleted_artifacts": [
                artifact.to_payload() for artifact in self.deleted_artifacts
            ],
        }


@dataclass(frozen=True, slots=True)
class _PreparedRestoreFile:
    restore_file: SqliteStateRestoreFile
    temp_path: Path
    rollback_path: Path
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]


@dataclass(frozen=True, slots=True)
class _PreparedCompactionFile:
    compaction_file: SqliteStateCompactionFile
    temp_path: Path
    rollback_path: Path
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]


@dataclass(frozen=True, slots=True)
class _RestoreEpochIntentFile:
    store: SqliteStore
    target_path: Path
    temp_path: Path
    rollback_path: Path
    sidecar_rollbacks: tuple[SqliteStateSidecarRollback, ...]


@dataclass(frozen=True, slots=True)
class _RestoreEpochIntent:
    restore_epoch_id: str
    backup_set_id: str
    state_set_hash: str
    intent_path: Path
    restore_files: tuple[_RestoreEpochIntentFile, ...]


@dataclass(frozen=True, slots=True)
class _CompactionEpochIntent:
    compaction_epoch_id: str
    state_set_hash: str
    intent_path: Path
    compaction_files: tuple[SqliteStateCompactionFile, ...]


def create_sqlite_state_backup_set(
    layout: StateStoreLayout,
    backup_root: Path,
    *,
    backup_set_id: str,
    created_utc: str,
) -> SqliteStateBackupManifest:
    _validate_backup_set_id(backup_set_id)
    _validate_local_absolute_path(backup_root, "STATE_BACKUP_ROOT")
    backup_dir = backup_root / backup_set_id
    if backup_dir.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_SET_ALREADY_EXISTS")
    backup_dir.mkdir(parents=True)
    _write_json_no_overwrite(
        backup_dir / BACKUP_SET_INTENT_FILENAME,
        _intent_payload(backup_set_id=backup_set_id, created_utc=created_utc),
    )

    entries: list[SqliteStateStoreBackup] = []
    for store in STATE_BACKUP_SET_STORES:
        source_path = _source_path(layout, store)
        backup_path = backup_dir / _backup_file_name(store)
        _backup_sqlite_database(source_path, backup_path)
        entries.append(_inspect_backup_file(store=store, backup_path=backup_path))

    manifest = SqliteStateBackupManifest(
        backup_set_id=backup_set_id,
        created_utc=created_utc,
        state_set_hash=_state_set_hash(
            backup_set_id=backup_set_id,
            created_utc=created_utc,
            stores=tuple(entries),
        ),
        stores=tuple(entries),
    )
    _write_json_no_overwrite(backup_dir / BACKUP_SET_MANIFEST_FILENAME, manifest.to_payload())
    return manifest


def load_sqlite_state_backup_manifest(backup_dir: Path) -> SqliteStateBackupManifest:
    manifest_path = backup_dir / BACKUP_SET_MANIFEST_FILENAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SqliteStateBackupViolation("STATE_BACKUP_MANIFEST_MISSING") from exc
    return sqlite_state_backup_manifest_from_payload(payload)


def verify_sqlite_state_backup_set(
    backup_dir: Path,
    *,
    manifest: SqliteStateBackupManifest | None = None,
) -> SqliteStateBackupManifest:
    loaded = manifest or load_sqlite_state_backup_manifest(backup_dir)
    _validate_manifest_store_set(loaded.stores)
    expected_hash = _state_set_hash(
        backup_set_id=loaded.backup_set_id,
        created_utc=loaded.created_utc,
        stores=loaded.stores,
    )
    if loaded.state_set_hash != expected_hash:
        raise SqliteStateBackupViolation("STATE_BACKUP_STATE_SET_HASH_MISMATCH")

    verified_entries: list[SqliteStateStoreBackup] = []
    for entry in loaded.stores:
        backup_path = backup_dir / entry.file_name
        if not backup_path.is_file():
            raise SqliteStateBackupViolation("STATE_BACKUP_FILE_MISSING")
        if backup_path.stat().st_size != entry.size_bytes:
            raise SqliteStateBackupViolation("STATE_BACKUP_SIZE_MISMATCH")
        if _sha256_file(backup_path) != entry.sha256:
            raise SqliteStateBackupViolation("STATE_BACKUP_CHECKSUM_MISMATCH")
        inspected = _inspect_backup_file(store=entry.store, backup_path=backup_path)
        if inspected != entry:
            raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_MISMATCH")
        verified_entries.append(inspected)

    verified = SqliteStateBackupManifest(
        backup_set_id=loaded.backup_set_id,
        created_utc=loaded.created_utc,
        state_set_hash=loaded.state_set_hash,
        stores=tuple(verified_entries),
        schema_version=loaded.schema_version,
    )
    return verified


def plan_sqlite_state_restore(
    backup_dir: Path,
    target_layout: StateStoreLayout,
    *,
    current_layout: StateStoreLayout | None = None,
    manifest: SqliteStateBackupManifest | None = None,
) -> SqliteStateRestorePlan:
    _validate_state_store_layout(target_layout, field_name="STATE_RESTORE_TARGET")
    if current_layout is not None:
        _validate_state_store_layout(current_layout, field_name="STATE_RESTORE_CURRENT")
    verified = verify_sqlite_state_backup_set(backup_dir, manifest=manifest)
    backup_intent_count, backup_high_water = _target_intent_evidence_from_manifest(verified)
    current_intent_count, current_high_water = _current_target_intent_evidence(
        (current_layout or target_layout).recovery
    )
    if _has_newer_unresolved_target_intents(
        backup_count=backup_intent_count,
        backup_high_water=backup_high_water,
        current_count=current_intent_count,
        current_high_water=current_high_water,
    ):
        raise SqliteStateBackupViolation("STATE_RESTORE_BLOCKED_BY_NEWER_TARGET_INTENTS")

    restore_files = tuple(
        SqliteStateRestoreFile(
            store=entry.store,
            backup_path=backup_dir / entry.file_name,
            target_path=_source_path(target_layout, entry.store),
            size_bytes=entry.size_bytes,
            sha256=entry.sha256,
            schema_version=entry.schema_version,
            migration_count=entry.migration_count,
            latest_migration_utc=entry.latest_migration_utc,
            page_count=entry.page_count,
            quick_check=entry.quick_check,
            foreign_key_violations=entry.foreign_key_violations,
            unresolved_target_intent_count=entry.unresolved_target_intent_count,
            target_intent_high_water_utc=entry.target_intent_high_water_utc,
        )
        for entry in verified.stores
    )
    return SqliteStateRestorePlan(
        backup_set_id=verified.backup_set_id,
        state_set_hash=verified.state_set_hash,
        target_layout=target_layout,
        restore_files=restore_files,
        backup_unresolved_target_intent_count=backup_intent_count,
        backup_target_intent_high_water_utc=backup_high_water,
        current_unresolved_target_intent_count=current_intent_count,
        current_target_intent_high_water_utc=current_high_water,
    )


def restore_sqlite_state_backup_set(
    backup_dir: Path,
    target_layout: StateStoreLayout,
    *,
    restore_epoch_id: str,
    started_utc: str,
    current_layout: StateStoreLayout | None = None,
    manifest: SqliteStateBackupManifest | None = None,
) -> SqliteStateRestoreReceipt:
    plan = plan_sqlite_state_restore(
        backup_dir,
        target_layout,
        current_layout=current_layout,
        manifest=manifest,
    )
    return apply_sqlite_state_restore_plan(
        plan,
        restore_epoch_id=restore_epoch_id,
        started_utc=started_utc,
    )


def admit_sqlite_state_restore_maintenance(
    layout: StateStoreLayout,
) -> SqliteStateRestoreMaintenanceAdmission:
    _validate_state_store_layout(layout, field_name="STATE_RESTORE_MAINTENANCE")

    active_run_count = 0
    active_run_target_count = 0
    non_terminal_command_receipt_count = 0
    pending_outbox_message_count = 0
    active_resource_lease_count = 0
    unresolved_target_intent_segment_count = 0
    blockers: list[SqliteStateRestoreMaintenanceBlocker] = []

    try:
        catalog_counts = _catalog_restore_maintenance_counts(layout.catalog)
        active_run_count = catalog_counts[0]
        active_run_target_count = catalog_counts[1]
        non_terminal_command_receipt_count = catalog_counts[2]
        pending_outbox_message_count = catalog_counts[3]
    except SqliteStateBackupViolation as exc:
        blockers.append(
            SqliteStateRestoreMaintenanceBlocker(
                code="STATE_RESTORE_MAINTENANCE_CATALOG_UNREADABLE",
                count=1,
                store=SqliteStore.CATALOG,
                detail=str(exc),
            )
        )

    try:
        recovery_counts = _recovery_restore_maintenance_counts(layout.recovery)
        active_resource_lease_count = recovery_counts[0]
        unresolved_target_intent_segment_count = recovery_counts[1]
    except SqliteStateBackupViolation as exc:
        blockers.append(
            SqliteStateRestoreMaintenanceBlocker(
                code="STATE_RESTORE_MAINTENANCE_RECOVERY_UNREADABLE",
                count=1,
                store=SqliteStore.RECOVERY,
                detail=str(exc),
            )
        )

    try:
        incomplete_restore_epoch_count = _incomplete_restore_epoch_count(layout)
    except SqliteStateBackupViolation as exc:
        incomplete_restore_epoch_count = 0
        blockers.append(
            SqliteStateRestoreMaintenanceBlocker(
                code="STATE_RESTORE_MAINTENANCE_EPOCHS_UNREADABLE",
                count=1,
                detail=str(exc),
            )
        )
    try:
        incomplete_compaction_epoch_count = _incomplete_compaction_epoch_count(layout)
    except SqliteStateBackupViolation as exc:
        incomplete_compaction_epoch_count = 0
        blockers.append(
            SqliteStateRestoreMaintenanceBlocker(
                code="STATE_RESTORE_MAINTENANCE_COMPACTION_EPOCHS_UNREADABLE",
                count=1,
                detail=str(exc),
            )
        )

    blockers.extend(
        _count_blockers(
            active_run_count=active_run_count,
            active_run_target_count=active_run_target_count,
            non_terminal_command_receipt_count=non_terminal_command_receipt_count,
            pending_outbox_message_count=pending_outbox_message_count,
            active_resource_lease_count=active_resource_lease_count,
            unresolved_target_intent_segment_count=unresolved_target_intent_segment_count,
            incomplete_restore_epoch_count=incomplete_restore_epoch_count,
            incomplete_compaction_epoch_count=incomplete_compaction_epoch_count,
        )
    )

    return SqliteStateRestoreMaintenanceAdmission(
        active_run_count=active_run_count,
        active_run_target_count=active_run_target_count,
        non_terminal_command_receipt_count=non_terminal_command_receipt_count,
        pending_outbox_message_count=pending_outbox_message_count,
        active_resource_lease_count=active_resource_lease_count,
        unresolved_target_intent_segment_count=unresolved_target_intent_segment_count,
        incomplete_restore_epoch_count=incomplete_restore_epoch_count,
        incomplete_compaction_epoch_count=incomplete_compaction_epoch_count,
        blockers=tuple(blockers),
    )


def apply_sqlite_state_restore_plan(
    plan: SqliteStateRestorePlan,
    *,
    restore_epoch_id: str,
    started_utc: str,
) -> SqliteStateRestoreReceipt:
    _validate_restore_epoch_id(restore_epoch_id)
    _validate_restore_plan(plan)
    epoch_dir = _restore_epoch_dir(plan.target_layout, restore_epoch_id)
    if epoch_dir.exists():
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ALREADY_EXISTS")
    epoch_dir.parent.mkdir(parents=True, exist_ok=True)
    epoch_dir.mkdir()

    prepared_files = _prepare_restore_files(plan, restore_epoch_id=restore_epoch_id)
    intent_path = epoch_dir / STATE_RESTORE_INTENT_FILENAME
    committed_path = epoch_dir / STATE_RESTORE_COMMITTED_FILENAME
    _write_json_no_overwrite(
        intent_path,
        _restore_intent_payload(
            plan=plan,
            restore_epoch_id=restore_epoch_id,
            started_utc=started_utc,
            prepared_files=prepared_files,
        ),
    )

    restored_files: list[SqliteStateRestoredFile] = []
    try:
        for prepared in prepared_files:
            restored_files.append(_swap_prepared_restore_file(prepared))
        for prepared in prepared_files:
            _verify_restored_file(prepared.restore_file, prepared.restore_file.target_path)
        _write_json_no_overwrite(
            committed_path,
            _restore_committed_payload(
                plan=plan,
                restore_epoch_id=restore_epoch_id,
                started_utc=started_utc,
                restored_files=tuple(restored_files),
            ),
        )
    except Exception as exc:
        try:
            _rollback_restored_files(tuple(reversed(restored_files)))
        except Exception as rollback_exc:
            raise SqliteStateBackupViolation("STATE_RESTORE_ROLLBACK_FAILED") from rollback_exc
        raise SqliteStateBackupViolation("STATE_RESTORE_SWAP_FAILED") from exc

    return SqliteStateRestoreReceipt(
        restore_epoch_id=restore_epoch_id,
        backup_set_id=plan.backup_set_id,
        state_set_hash=plan.state_set_hash,
        intent_path=intent_path,
        committed_path=committed_path,
        restored_files=tuple(restored_files),
    )


def compact_sqlite_state_stores(
    layout: StateStoreLayout,
    *,
    compaction_epoch_id: str,
    started_utc: str,
) -> SqliteStateCompactionReceipt:
    _validate_compaction_epoch_id(compaction_epoch_id)
    _validate_state_store_layout(layout, field_name="STATE_COMPACTION")
    admission = admit_sqlite_state_restore_maintenance(layout)
    if not admission.admitted:
        raise SqliteStateBackupViolation("STATE_COMPACTION_BLOCKED_BY_MAINTENANCE")

    epoch_dir = _compaction_epoch_dir(layout, compaction_epoch_id)
    if epoch_dir.exists():
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_ALREADY_EXISTS")

    prepared_files = _prepare_compaction_files(
        layout,
        compaction_epoch_id=compaction_epoch_id,
    )
    epoch_dir.parent.mkdir(parents=True, exist_ok=True)
    epoch_dir.mkdir()
    state_set_hash = _state_compaction_set_hash(
        compaction_epoch_id=compaction_epoch_id,
        started_utc=started_utc,
        compaction_files=tuple(entry.compaction_file for entry in prepared_files),
    )
    intent_path = epoch_dir / STATE_COMPACTION_INTENT_FILENAME
    committed_path = epoch_dir / STATE_COMPACTION_COMMITTED_FILENAME
    _write_json_no_overwrite(
        intent_path,
        _compaction_intent_payload(
            compaction_epoch_id=compaction_epoch_id,
            started_utc=started_utc,
            state_set_hash=state_set_hash,
            prepared_files=prepared_files,
        ),
    )

    compacted_files: list[SqliteStateCompactedFile] = []
    try:
        for prepared in prepared_files:
            compacted_files.append(_swap_prepared_compaction_file(prepared))
        for prepared in prepared_files:
            _verify_compacted_file(
                prepared.compaction_file,
                prepared.compaction_file.target_path,
            )
        _write_json_no_overwrite(
            committed_path,
            _compaction_committed_payload(
                compaction_epoch_id=compaction_epoch_id,
                started_utc=started_utc,
                state_set_hash=state_set_hash,
                compacted_files=tuple(compacted_files),
            ),
        )
    except Exception as exc:
        try:
            _rollback_compacted_files(tuple(reversed(compacted_files)))
        except Exception as rollback_exc:
            raise SqliteStateBackupViolation("STATE_COMPACTION_ROLLBACK_FAILED") from rollback_exc
        raise SqliteStateBackupViolation("STATE_COMPACTION_SWAP_FAILED") from exc

    return SqliteStateCompactionReceipt(
        compaction_epoch_id=compaction_epoch_id,
        state_set_hash=state_set_hash,
        intent_path=intent_path,
        committed_path=committed_path,
        compacted_files=tuple(compacted_files),
    )


def recover_incomplete_sqlite_state_restore_epochs(
    layout: StateStoreLayout,
    *,
    recovered_utc: str,
) -> SqliteStateRestoreEpochRecoveryReport:
    _validate_state_store_layout(layout, field_name="STATE_RESTORE_RECOVERY")
    epochs_dir = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return SqliteStateRestoreEpochRecoveryReport(
            scanned_epoch_count=0,
            committed_epoch_count=0,
            previously_rolled_back_epoch_count=0,
            recovered_epochs=(),
        )
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCHS_PATH_NOT_DIRECTORY")

    scanned = 0
    committed = 0
    previously_rolled_back = 0
    recovered_epochs: list[SqliteStateRestoreEpochRecovery] = []
    for epoch_dir in sorted(path for path in epochs_dir.iterdir() if path.is_dir()):
        scanned += 1
        committed_path = epoch_dir / STATE_RESTORE_COMMITTED_FILENAME
        rolled_back_path = epoch_dir / STATE_RESTORE_ROLLED_BACK_FILENAME
        if committed_path.exists():
            committed += 1
            continue
        if rolled_back_path.exists():
            previously_rolled_back += 1
            continue
        intent = _load_restore_epoch_intent(epoch_dir=epoch_dir, layout=layout)
        rollback_counts = _rollback_incomplete_restore_epoch(intent)
        _write_json_no_overwrite(
            rolled_back_path,
            _restore_rolled_back_payload(
                intent=intent,
                recovered_utc=recovered_utc,
                rolled_back_store_count=rollback_counts[0],
                removed_temp_file_count=rollback_counts[1],
                restored_sidecar_count=rollback_counts[2],
            ),
        )
        recovered_epochs.append(
            SqliteStateRestoreEpochRecovery(
                restore_epoch_id=intent.restore_epoch_id,
                intent_path=intent.intent_path,
                rolled_back_path=rolled_back_path,
                rolled_back_store_count=rollback_counts[0],
                removed_temp_file_count=rollback_counts[1],
                restored_sidecar_count=rollback_counts[2],
            )
        )

    return SqliteStateRestoreEpochRecoveryReport(
        scanned_epoch_count=scanned,
        committed_epoch_count=committed,
        previously_rolled_back_epoch_count=previously_rolled_back,
        recovered_epochs=tuple(recovered_epochs),
    )


def recover_incomplete_sqlite_state_compaction_epochs(
    layout: StateStoreLayout,
    *,
    recovered_utc: str,
) -> SqliteStateCompactionEpochRecoveryReport:
    _validate_state_store_layout(layout, field_name="STATE_COMPACTION_RECOVERY")
    epochs_dir = layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return SqliteStateCompactionEpochRecoveryReport(
            scanned_epoch_count=0,
            committed_epoch_count=0,
            previously_rolled_back_epoch_count=0,
            recovered_epochs=(),
        )
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCHS_PATH_NOT_DIRECTORY")

    scanned = 0
    committed = 0
    previously_rolled_back = 0
    recovered_epochs: list[SqliteStateCompactionEpochRecovery] = []
    for epoch_dir in sorted(path for path in epochs_dir.iterdir() if path.is_dir()):
        scanned += 1
        committed_path = epoch_dir / STATE_COMPACTION_COMMITTED_FILENAME
        rolled_back_path = epoch_dir / STATE_COMPACTION_ROLLED_BACK_FILENAME
        if committed_path.exists():
            committed += 1
            continue
        if rolled_back_path.exists():
            previously_rolled_back += 1
            continue
        intent = _load_compaction_epoch_intent(epoch_dir=epoch_dir, layout=layout)
        rollback_counts = _rollback_incomplete_compaction_epoch(intent)
        _write_json_no_overwrite(
            rolled_back_path,
            _compaction_rolled_back_payload(
                intent=intent,
                recovered_utc=recovered_utc,
                rolled_back_store_count=rollback_counts[0],
                removed_temp_file_count=rollback_counts[1],
                restored_sidecar_count=rollback_counts[2],
            ),
        )
        recovered_epochs.append(
            SqliteStateCompactionEpochRecovery(
                compaction_epoch_id=intent.compaction_epoch_id,
                intent_path=intent.intent_path,
                rolled_back_path=rolled_back_path,
                rolled_back_store_count=rollback_counts[0],
                removed_temp_file_count=rollback_counts[1],
                restored_sidecar_count=rollback_counts[2],
            )
        )

    return SqliteStateCompactionEpochRecoveryReport(
        scanned_epoch_count=scanned,
        committed_epoch_count=committed,
        previously_rolled_back_epoch_count=previously_rolled_back,
        recovered_epochs=tuple(recovered_epochs),
    )


def plan_sqlite_state_maintenance_retention(
    layout: StateStoreLayout,
    backup_root: Path,
    *,
    policy: SqliteStateMaintenanceRetentionPolicy | None = None,
) -> SqliteStateMaintenanceRetentionPlan:
    retention_policy = policy or SqliteStateMaintenanceRetentionPolicy()
    _validate_retention_policy(retention_policy)
    _validate_state_store_layout(layout, field_name="STATE_RETENTION")
    _validate_local_absolute_path(backup_root, "STATE_RETENTION_BACKUP_ROOT")

    backup_sets, backup_skips = _scan_backup_set_retention_artifacts(backup_root)
    restore_epochs, restore_skips = _scan_restore_epoch_retention_artifacts(layout)
    compaction_epochs, compaction_skips = _scan_compaction_epoch_retention_artifacts(
        layout
    )

    retained_restore_epochs = _latest_retention_artifacts(
        restore_epochs,
        retention_policy.keep_latest_restore_epochs,
    )
    retained_compaction_epochs = _latest_retention_artifacts(
        compaction_epochs,
        retention_policy.keep_latest_compaction_epochs,
    )
    protected_backup_set_ids = tuple(
        sorted(
            artifact.backup_set_id
            for artifact in retained_restore_epochs
            if artifact.backup_set_id is not None
        )
    )
    latest_backup_sets = _latest_retention_artifacts(
        backup_sets,
        retention_policy.keep_latest_backup_sets,
    )
    retained_backup_sets = tuple(
        artifact
        for artifact in backup_sets
        if artifact.artifact_id in {entry.artifact_id for entry in latest_backup_sets}
        or artifact.artifact_id in protected_backup_set_ids
        or _retention_cutoff_protects_backup_set(artifact, retention_policy)
    )
    retained_backup_set_ids = {artifact.artifact_id for artifact in retained_backup_sets}
    retained_restore_epoch_ids = {
        artifact.artifact_id for artifact in retained_restore_epochs
    }
    retained_compaction_epoch_ids = {
        artifact.artifact_id for artifact in retained_compaction_epochs
    }

    delete_artifacts = tuple(
        artifact
        for artifact in backup_sets
        if artifact.artifact_id not in retained_backup_set_ids
    ) + tuple(
        artifact
        for artifact in restore_epochs
        if artifact.artifact_id not in retained_restore_epoch_ids
    ) + tuple(
        artifact
        for artifact in compaction_epochs
        if artifact.artifact_id not in retained_compaction_epoch_ids
    )
    retained_artifacts = (
        retained_backup_sets + retained_restore_epochs + retained_compaction_epochs
    )
    return SqliteStateMaintenanceRetentionPlan(
        backup_root=backup_root,
        state_root=layout.root,
        policy=retention_policy,
        delete_artifacts=delete_artifacts,
        retained_artifacts=retained_artifacts,
        skipped_artifacts=backup_skips + restore_skips + compaction_skips,
        protected_backup_set_ids=protected_backup_set_ids,
    )


def apply_sqlite_state_maintenance_retention(
    layout: StateStoreLayout,
    backup_root: Path,
    *,
    policy: SqliteStateMaintenanceRetentionPolicy | None = None,
) -> SqliteStateMaintenanceRetentionResult:
    plan = plan_sqlite_state_maintenance_retention(
        layout,
        backup_root,
        policy=policy,
    )
    deleted_artifacts: list[SqliteStateMaintenanceRetentionArtifact] = []
    for artifact in plan.delete_artifacts:
        _delete_retention_artifact(
            artifact,
            backup_root=backup_root,
            layout=layout,
        )
        deleted_artifacts.append(artifact)
    return SqliteStateMaintenanceRetentionResult(
        plan=plan,
        deleted_artifacts=tuple(deleted_artifacts),
    )


def _validate_retention_policy(policy: SqliteStateMaintenanceRetentionPolicy) -> None:
    if policy.keep_latest_backup_sets < 1:
        raise SqliteStateBackupViolation("STATE_RETENTION_BACKUP_KEEP_COUNT_INVALID")
    if policy.keep_latest_restore_epochs < 0:
        raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_KEEP_COUNT_INVALID")
    if policy.keep_latest_compaction_epochs < 0:
        raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_KEEP_COUNT_INVALID")
    cutoff = policy.retain_backup_sets_created_on_or_after_utc
    if cutoff is not None and not cutoff.strip():
        raise SqliteStateBackupViolation("STATE_RETENTION_BACKUP_CUTOFF_INVALID")


def _scan_backup_set_retention_artifacts(
    backup_root: Path,
) -> tuple[
    tuple[SqliteStateMaintenanceRetentionArtifact, ...],
    tuple[SqliteStateMaintenanceRetentionSkip, ...],
]:
    if not backup_root.exists():
        return ((), ())
    if not backup_root.is_dir():
        raise SqliteStateBackupViolation("STATE_RETENTION_BACKUP_ROOT_NOT_DIRECTORY")

    artifacts: list[SqliteStateMaintenanceRetentionArtifact] = []
    skips: list[SqliteStateMaintenanceRetentionSkip] = []
    for backup_dir in sorted(backup_root.iterdir(), key=lambda path: path.name):
        if not backup_dir.is_dir() or backup_dir.is_symlink():
            skips.append(
                _retention_skip(
                    "backup_set",
                    backup_dir,
                    "STATE_RETENTION_BACKUP_SET_NOT_DIRECTORY",
                )
            )
            continue
        try:
            _validate_backup_set_id(backup_dir.name)
            manifest = verify_sqlite_state_backup_set(backup_dir)
            if manifest.backup_set_id != backup_dir.name:
                raise SqliteStateBackupViolation("STATE_RETENTION_BACKUP_SET_ID_MISMATCH")
        except SqliteStateBackupViolation as exc:
            skips.append(_retention_skip("backup_set", backup_dir, str(exc)))
            continue
        artifacts.append(
            SqliteStateMaintenanceRetentionArtifact(
                artifact_type="backup_set",
                artifact_id=manifest.backup_set_id,
                path=backup_dir,
                created_utc=manifest.created_utc,
                terminal_state="MANIFESTED",
            )
        )
    return (tuple(artifacts), tuple(skips))


def _scan_restore_epoch_retention_artifacts(
    layout: StateStoreLayout,
) -> tuple[
    tuple[SqliteStateMaintenanceRetentionArtifact, ...],
    tuple[SqliteStateMaintenanceRetentionSkip, ...],
]:
    epochs_dir = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return ((), ())
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_EPOCHS_NOT_DIRECTORY")

    artifacts: list[SqliteStateMaintenanceRetentionArtifact] = []
    skips: list[SqliteStateMaintenanceRetentionSkip] = []
    for epoch_dir in sorted(epochs_dir.iterdir(), key=lambda path: path.name):
        if not epoch_dir.is_dir() or epoch_dir.is_symlink():
            skips.append(
                _retention_skip(
                    "restore_epoch",
                    epoch_dir,
                    "STATE_RETENTION_RESTORE_EPOCH_NOT_DIRECTORY",
                )
            )
            continue
        try:
            artifacts.append(_restore_epoch_retention_artifact(epoch_dir, layout))
        except SqliteStateBackupViolation as exc:
            skips.append(_retention_skip("restore_epoch", epoch_dir, str(exc)))
    return (tuple(artifacts), tuple(skips))


def _scan_compaction_epoch_retention_artifacts(
    layout: StateStoreLayout,
) -> tuple[
    tuple[SqliteStateMaintenanceRetentionArtifact, ...],
    tuple[SqliteStateMaintenanceRetentionSkip, ...],
]:
    epochs_dir = layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return ((), ())
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_EPOCHS_NOT_DIRECTORY")

    artifacts: list[SqliteStateMaintenanceRetentionArtifact] = []
    skips: list[SqliteStateMaintenanceRetentionSkip] = []
    for epoch_dir in sorted(epochs_dir.iterdir(), key=lambda path: path.name):
        if not epoch_dir.is_dir() or epoch_dir.is_symlink():
            skips.append(
                _retention_skip(
                    "compaction_epoch",
                    epoch_dir,
                    "STATE_RETENTION_COMPACTION_EPOCH_NOT_DIRECTORY",
                )
            )
            continue
        try:
            artifacts.append(_compaction_epoch_retention_artifact(epoch_dir, layout))
        except SqliteStateBackupViolation as exc:
            skips.append(_retention_skip("compaction_epoch", epoch_dir, str(exc)))
    return (tuple(artifacts), tuple(skips))


def _restore_epoch_retention_artifact(
    epoch_dir: Path,
    layout: StateStoreLayout,
) -> SqliteStateMaintenanceRetentionArtifact:
    restore_epoch_id = epoch_dir.name
    _validate_restore_epoch_id(restore_epoch_id)
    committed_path = epoch_dir / STATE_RESTORE_COMMITTED_FILENAME
    rolled_back_path = epoch_dir / STATE_RESTORE_ROLLED_BACK_FILENAME
    if committed_path.exists() and rolled_back_path.exists():
        raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_EPOCH_TERMINAL_CONFLICT")
    if committed_path.exists():
        payload = _read_json_object(committed_path, "STATE_RETENTION_RESTORE_COMMITTED")
        _validate_terminal_schema(
            payload,
            schema_version=STATE_RESTORE_EPOCH_SCHEMA_VERSION,
            status="COMMITTED",
        )
        if _str_field(payload, "restore_epoch_id") != restore_epoch_id:
            raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_EPOCH_ID_MISMATCH")
        backup_set_id = _str_field(payload, "backup_set_id")
        _validate_backup_set_id(backup_set_id)
        _hex_field(payload, "state_set_hash")
        started_utc = _str_field(payload, "started_utc")
        return SqliteStateMaintenanceRetentionArtifact(
            artifact_type="restore_epoch",
            artifact_id=restore_epoch_id,
            path=epoch_dir,
            created_utc=started_utc,
            terminal_state="COMMITTED",
            associated_paths=_restore_epoch_committed_associated_paths(
                payload,
                layout,
                restore_epoch_id,
            ),
            backup_set_id=backup_set_id,
        )
    if rolled_back_path.exists():
        payload = _read_json_object(rolled_back_path, "STATE_RETENTION_RESTORE_ROLLED_BACK")
        _validate_terminal_schema(
            payload,
            schema_version=STATE_RESTORE_EPOCH_SCHEMA_VERSION,
            status="ROLLED_BACK",
        )
        if _str_field(payload, "restore_epoch_id") != restore_epoch_id:
            raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_EPOCH_ID_MISMATCH")
        backup_set_id = _str_field(payload, "backup_set_id")
        _validate_backup_set_id(backup_set_id)
        _hex_field(payload, "state_set_hash")
        return SqliteStateMaintenanceRetentionArtifact(
            artifact_type="restore_epoch",
            artifact_id=restore_epoch_id,
            path=epoch_dir,
            created_utc=_str_field(payload, "recovered_utc"),
            terminal_state="ROLLED_BACK",
            backup_set_id=backup_set_id,
        )
    raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_EPOCH_INCOMPLETE")


def _compaction_epoch_retention_artifact(
    epoch_dir: Path,
    layout: StateStoreLayout,
) -> SqliteStateMaintenanceRetentionArtifact:
    compaction_epoch_id = epoch_dir.name
    _validate_compaction_epoch_id(compaction_epoch_id)
    committed_path = epoch_dir / STATE_COMPACTION_COMMITTED_FILENAME
    rolled_back_path = epoch_dir / STATE_COMPACTION_ROLLED_BACK_FILENAME
    if committed_path.exists() and rolled_back_path.exists():
        raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_EPOCH_TERMINAL_CONFLICT")
    if committed_path.exists():
        payload = _read_json_object(
            committed_path,
            "STATE_RETENTION_COMPACTION_COMMITTED",
        )
        _validate_terminal_schema(
            payload,
            schema_version=STATE_COMPACTION_EPOCH_SCHEMA_VERSION,
            status="COMMITTED",
        )
        if _str_field(payload, "compaction_epoch_id") != compaction_epoch_id:
            raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_EPOCH_ID_MISMATCH")
        _hex_field(payload, "state_set_hash")
        started_utc = _str_field(payload, "started_utc")
        return SqliteStateMaintenanceRetentionArtifact(
            artifact_type="compaction_epoch",
            artifact_id=compaction_epoch_id,
            path=epoch_dir,
            created_utc=started_utc,
            terminal_state="COMMITTED",
            associated_paths=_compaction_epoch_committed_associated_paths(
                payload,
                layout,
                compaction_epoch_id,
            ),
        )
    if rolled_back_path.exists():
        payload = _read_json_object(
            rolled_back_path,
            "STATE_RETENTION_COMPACTION_ROLLED_BACK",
        )
        _validate_terminal_schema(
            payload,
            schema_version=STATE_COMPACTION_EPOCH_SCHEMA_VERSION,
            status="ROLLED_BACK",
        )
        if _str_field(payload, "compaction_epoch_id") != compaction_epoch_id:
            raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_EPOCH_ID_MISMATCH")
        _hex_field(payload, "state_set_hash")
        return SqliteStateMaintenanceRetentionArtifact(
            artifact_type="compaction_epoch",
            artifact_id=compaction_epoch_id,
            path=epoch_dir,
            created_utc=_str_field(payload, "recovered_utc"),
            terminal_state="ROLLED_BACK",
        )
    raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_EPOCH_INCOMPLETE")


def _restore_epoch_committed_associated_paths(
    payload: dict[str, Any],
    layout: StateStoreLayout,
    restore_epoch_id: str,
) -> tuple[Path, ...]:
    files_payload = payload.get("restored_files")
    if not isinstance(files_payload, list):
        raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_FILES_NOT_ARRAY")
    stores: list[SqliteStore] = []
    associated_paths: list[Path] = []
    for entry in files_payload:
        if not isinstance(entry, dict):
            raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_FILE_NOT_OBJECT")
        try:
            store = SqliteStore(_str_field(entry, "store"))
        except ValueError as exc:
            raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_STORE_UNSUPPORTED") from exc
        stores.append(store)
        target_path = _path_field(entry, "target_path")
        if target_path != _source_path(layout, store):
            raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_TARGET_MISMATCH")
        rollback_path = _optional_path_field(entry, "rollback_path")
        if rollback_path is not None:
            if rollback_path != _restore_rollback_path(target_path, restore_epoch_id):
                raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_ROLLBACK_MISMATCH")
            associated_paths.append(rollback_path)
        associated_paths.extend(
            _terminal_sidecar_rollback_paths(
                entry,
                target_path=target_path,
                epoch_id=restore_epoch_id,
                expected_rollback_path=_restore_rollback_path,
                violation_prefix="STATE_RETENTION_RESTORE",
            )
        )
    if tuple(stores) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_RETENTION_RESTORE_INCOMPLETE_STORE_SET")
    return tuple(associated_paths)


def _compaction_epoch_committed_associated_paths(
    payload: dict[str, Any],
    layout: StateStoreLayout,
    compaction_epoch_id: str,
) -> tuple[Path, ...]:
    files_payload = payload.get("compacted_files")
    if not isinstance(files_payload, list):
        raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_FILES_NOT_ARRAY")
    stores: list[SqliteStore] = []
    associated_paths: list[Path] = []
    for entry in files_payload:
        if not isinstance(entry, dict):
            raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_FILE_NOT_OBJECT")
        try:
            store = SqliteStore(_str_field(entry, "store"))
        except ValueError as exc:
            raise SqliteStateBackupViolation(
                "STATE_RETENTION_COMPACTION_STORE_UNSUPPORTED"
            ) from exc
        stores.append(store)
        target_path = _path_field(entry, "target_path")
        if target_path != _source_path(layout, store):
            raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_TARGET_MISMATCH")
        rollback_path = _optional_path_field(entry, "rollback_path")
        if rollback_path is not None:
            if rollback_path != _compaction_rollback_path(target_path, compaction_epoch_id):
                raise SqliteStateBackupViolation(
                    "STATE_RETENTION_COMPACTION_ROLLBACK_MISMATCH"
                )
            associated_paths.append(rollback_path)
        associated_paths.extend(
            _terminal_sidecar_rollback_paths(
                entry,
                target_path=target_path,
                epoch_id=compaction_epoch_id,
                expected_rollback_path=_compaction_rollback_path,
                violation_prefix="STATE_RETENTION_COMPACTION",
            )
        )
    if tuple(stores) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_RETENTION_COMPACTION_INCOMPLETE_STORE_SET")
    return tuple(associated_paths)


def _terminal_sidecar_rollback_paths(
    payload: dict[str, Any],
    *,
    target_path: Path,
    epoch_id: str,
    expected_rollback_path: Callable[[Path, str], Path],
    violation_prefix: str,
) -> tuple[Path, ...]:
    sidecar_payload = payload.get("sidecar_rollbacks")
    if not isinstance(sidecar_payload, list):
        raise SqliteStateBackupViolation(f"{violation_prefix}_SIDECARS_NOT_ARRAY")
    associated_paths: list[Path] = []
    for sidecar_entry in sidecar_payload:
        if not isinstance(sidecar_entry, dict):
            raise SqliteStateBackupViolation(f"{violation_prefix}_SIDECAR_NOT_OBJECT")
        sidecar_path = _path_field(sidecar_entry, "path")
        if sidecar_path not in _sqlite_sidecar_paths(target_path):
            raise SqliteStateBackupViolation(f"{violation_prefix}_SIDECAR_PATH_MISMATCH")
        rollback_path = _path_field(sidecar_entry, "rollback_path")
        if rollback_path != expected_rollback_path(sidecar_path, epoch_id):
            raise SqliteStateBackupViolation(
                f"{violation_prefix}_SIDECAR_ROLLBACK_MISMATCH"
            )
        associated_paths.append(rollback_path)
    return tuple(associated_paths)


def _latest_retention_artifacts(
    artifacts: tuple[SqliteStateMaintenanceRetentionArtifact, ...],
    keep_count: int,
) -> tuple[SqliteStateMaintenanceRetentionArtifact, ...]:
    if keep_count == 0:
        return ()
    return tuple(
        sorted(
            artifacts,
            key=lambda artifact: (artifact.created_utc, artifact.artifact_id),
            reverse=True,
        )[:keep_count]
    )


def _retention_cutoff_protects_backup_set(
    artifact: SqliteStateMaintenanceRetentionArtifact,
    policy: SqliteStateMaintenanceRetentionPolicy,
) -> bool:
    cutoff = policy.retain_backup_sets_created_on_or_after_utc
    return cutoff is not None and artifact.created_utc >= cutoff


def _delete_retention_artifact(
    artifact: SqliteStateMaintenanceRetentionArtifact,
    *,
    backup_root: Path,
    layout: StateStoreLayout,
) -> None:
    if artifact.artifact_type == "backup_set":
        _delete_retention_directory(
            artifact.path,
            expected_parent=backup_root,
            violation="STATE_RETENTION_BACKUP_SET_DELETE_PATH_INVALID",
        )
        return
    if artifact.artifact_type == "restore_epoch":
        expected_parent = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME
        associated_parent = layout.root
        violation = "STATE_RETENTION_RESTORE_EPOCH_DELETE_PATH_INVALID"
    elif artifact.artifact_type == "compaction_epoch":
        expected_parent = layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME
        associated_parent = layout.root
        violation = "STATE_RETENTION_COMPACTION_EPOCH_DELETE_PATH_INVALID"
    else:
        raise SqliteStateBackupViolation("STATE_RETENTION_ARTIFACT_TYPE_UNSUPPORTED")

    for associated_path in artifact.associated_paths:
        _delete_retention_file(
            associated_path,
            expected_parent=associated_parent,
            violation="STATE_RETENTION_ASSOCIATED_FILE_DELETE_PATH_INVALID",
        )
    _delete_retention_directory(
        artifact.path,
        expected_parent=expected_parent,
        violation=violation,
    )


def _delete_retention_file(path: Path, *, expected_parent: Path, violation: str) -> None:
    if path.parent != expected_parent:
        raise SqliteStateBackupViolation(violation)
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise SqliteStateBackupViolation("STATE_RETENTION_ASSOCIATED_PATH_NOT_FILE")
    path.unlink()


def _delete_retention_directory(path: Path, *, expected_parent: Path, violation: str) -> None:
    if path.parent != expected_parent:
        raise SqliteStateBackupViolation(violation)
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise SqliteStateBackupViolation("STATE_RETENTION_ARTIFACT_PATH_NOT_DIRECTORY")
    shutil.rmtree(path)


def _read_json_object(path: Path, code_prefix: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SqliteStateBackupViolation(f"{code_prefix}_MISSING") from exc
    except json.JSONDecodeError as exc:
        raise SqliteStateBackupViolation(f"{code_prefix}_INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation(f"{code_prefix}_NOT_OBJECT")
    return payload


def _validate_terminal_schema(
    payload: dict[str, Any],
    *,
    schema_version: int,
    status: str,
) -> None:
    if _int_field(payload, "schema_version") != schema_version:
        raise SqliteStateBackupViolation("STATE_RETENTION_TERMINAL_SCHEMA_UNSUPPORTED")
    if _str_field(payload, "status") != status:
        raise SqliteStateBackupViolation("STATE_RETENTION_TERMINAL_STATUS_UNSUPPORTED")


def _retention_skip(
    artifact_type: str,
    path: Path,
    reason: str,
) -> SqliteStateMaintenanceRetentionSkip:
    return SqliteStateMaintenanceRetentionSkip(
        artifact_type=artifact_type,
        artifact_id=path.name,
        path=path,
        reason=reason,
    )


def sqlite_state_backup_manifest_from_payload(payload: object) -> SqliteStateBackupManifest:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_BACKUP_MANIFEST_NOT_OBJECT")
    schema_version = _int_field(payload, "schema_version")
    if schema_version != STATE_BACKUP_SET_MANIFEST_SCHEMA_VERSION:
        raise SqliteStateBackupViolation("STATE_BACKUP_MANIFEST_SCHEMA_UNSUPPORTED")
    backup_set_id = _str_field(payload, "backup_set_id")
    _validate_backup_set_id(backup_set_id)
    created_utc = _str_field(payload, "created_utc")
    state_set_hash = _hex_field(payload, "state_set_hash")
    stores_payload = payload.get("stores")
    if not isinstance(stores_payload, list):
        raise SqliteStateBackupViolation("STATE_BACKUP_STORES_NOT_ARRAY")
    stores = tuple(_store_backup_from_payload(entry) for entry in stores_payload)
    _validate_manifest_store_set(stores)
    return SqliteStateBackupManifest(
        backup_set_id=backup_set_id,
        created_utc=created_utc,
        state_set_hash=state_set_hash,
        stores=stores,
        schema_version=schema_version,
    )


def _store_backup_from_payload(payload: object) -> SqliteStateStoreBackup:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_BACKUP_STORE_NOT_OBJECT")
    try:
        store = SqliteStore(_str_field(payload, "store"))
    except ValueError as exc:
        raise SqliteStateBackupViolation("STATE_BACKUP_STORE_UNSUPPORTED") from exc
    latest_migration_utc = payload.get("latest_migration_utc")
    if latest_migration_utc is not None and not isinstance(latest_migration_utc, str):
        raise SqliteStateBackupViolation("STATE_BACKUP_LATEST_MIGRATION_UTC_INVALID")
    return SqliteStateStoreBackup(
        store=store,
        file_name=_safe_file_name(_str_field(payload, "file_name")),
        size_bytes=_non_negative_int_field(payload, "size_bytes"),
        sha256=_hex_field(payload, "sha256"),
        schema_version=_non_negative_int_field(payload, "schema_version"),
        migration_count=_non_negative_int_field(payload, "migration_count"),
        latest_migration_utc=latest_migration_utc,
        page_count=_non_negative_int_field(payload, "page_count"),
        quick_check=_str_field(payload, "quick_check"),
        foreign_key_violations=_non_negative_int_field(payload, "foreign_key_violations"),
        unresolved_target_intent_count=_non_negative_int_field(
            payload,
            "unresolved_target_intent_count",
        ),
        target_intent_high_water_utc=_optional_str_field(
            payload,
            "target_intent_high_water_utc",
        ),
    )


def _backup_sqlite_database(source_path: Path, backup_path: Path) -> None:
    if not source_path.is_file():
        raise SqliteStateBackupViolation("STATE_BACKUP_SOURCE_MISSING")
    if backup_path.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_FILE_ALREADY_EXISTS")
    with sqlite3.connect(source_path) as source:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)


def _inspect_backup_file(*, store: SqliteStore, backup_path: Path) -> SqliteStateStoreBackup:
    try:
        with sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True) as connection:
            identity = _optional_scalar(
                connection,
                "SELECT store FROM store_identity WHERE singleton = 1",
            )
            if identity != store.value:
                raise SqliteStateBackupViolation("STATE_BACKUP_STORE_IDENTITY_MISMATCH")
            quick_check = str(_required_scalar(connection, "PRAGMA quick_check"))
            if quick_check.lower() != "ok":
                raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_QUICK_CHECK_FAILED")
            foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            if foreign_key_violations:
                raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_FOREIGN_KEY_CHECK_FAILED")
            schema_row = connection.execute(
                """
                SELECT
                    coalesce(max(version), 0),
                    count(*),
                    max(applied_utc)
                FROM schema_migrations
                WHERE store = ?
                """,
                (store.value,),
            ).fetchone()
            if schema_row is None:
                raise SqliteStateBackupViolation("STATE_BACKUP_SCHEMA_MIGRATIONS_MISSING")
            page_count = _required_int_scalar(connection, "PRAGMA page_count")
            unresolved_count, target_intent_high_water = _target_intent_evidence(
                connection,
                store=store,
            )
    except sqlite3.Error as exc:
        raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_UNREADABLE") from exc

    return SqliteStateStoreBackup(
        store=store,
        file_name=backup_path.name,
        size_bytes=backup_path.stat().st_size,
        sha256=_sha256_file(backup_path),
        schema_version=int(schema_row[0]),
        migration_count=int(schema_row[1]),
        latest_migration_utc=None if schema_row[2] is None else str(schema_row[2]),
        page_count=page_count,
        quick_check=quick_check,
        foreign_key_violations=foreign_key_violations,
        unresolved_target_intent_count=unresolved_count,
        target_intent_high_water_utc=target_intent_high_water,
    )


def _target_intent_evidence(
    connection: sqlite3.Connection,
    *,
    store: SqliteStore,
) -> tuple[int, str | None]:
    if store is not SqliteStore.RECOVERY:
        return (0, None)
    row = connection.execute(
        """
        SELECT count(*), max(updated_utc)
        FROM recovery_intent_segments
        WHERE state IN ('BUILDING', 'DURABLE')
        """
    ).fetchone()
    if row is None:
        raise SqliteStateBackupViolation("STATE_BACKUP_TARGET_INTENT_EVIDENCE_MISSING")
    count = int(row[0])
    high_water = None if row[1] is None else str(row[1])
    return (count, high_water)


def _current_target_intent_evidence(recovery_path: Path) -> tuple[int, str | None]:
    if not recovery_path.exists():
        return (0, None)
    try:
        with sqlite3.connect(f"file:{recovery_path.as_posix()}?mode=ro", uri=True) as connection:
            identity = _optional_scalar(
                connection,
                "SELECT store FROM store_identity WHERE singleton = 1",
            )
            if identity != SqliteStore.RECOVERY.value:
                raise SqliteStateBackupViolation("STATE_RESTORE_CURRENT_RECOVERY_MISMATCH")
            return _target_intent_evidence(connection, store=SqliteStore.RECOVERY)
    except sqlite3.Error as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_CURRENT_RECOVERY_UNREADABLE") from exc


def _catalog_restore_maintenance_counts(catalog_path: Path) -> tuple[int, int, int, int]:
    if not catalog_path.exists():
        return (0, 0, 0, 0)
    connection: sqlite3.Connection | None = None
    try:
        connection = _readonly_state_store_connection(
            catalog_path,
            store=SqliteStore.CATALOG,
        )
        identity = _optional_scalar(
            connection,
            "SELECT store FROM store_identity WHERE singleton = 1",
        )
        if identity != SqliteStore.CATALOG.value:
            raise SqliteStateBackupViolation(
                "STATE_RESTORE_MAINTENANCE_CATALOG_IDENTITY_MISMATCH"
            )
        return (
            _count_states_not_in(
                connection,
                table_name="runs",
                terminal_states=STATE_RESTORE_MAINTENANCE_TERMINAL_RUN_STATES,
            ),
            _count_states_not_in(
                connection,
                table_name="run_targets",
                terminal_states=STATE_RESTORE_MAINTENANCE_TERMINAL_RUN_TARGET_STATES,
            ),
            _count_states_not_in(
                connection,
                table_name="command_receipts",
                terminal_states=(
                    STATE_RESTORE_MAINTENANCE_TERMINAL_COMMAND_RECEIPT_STATES
                ),
            ),
            _count_states_not_in(
                connection,
                table_name="outbox_messages",
                terminal_states=STATE_RESTORE_MAINTENANCE_TERMINAL_OUTBOX_STATES,
            ),
        )
    except sqlite3.Error as exc:
        raise SqliteStateBackupViolation(
            "STATE_RESTORE_MAINTENANCE_CATALOG_EVIDENCE_UNREADABLE"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _recovery_restore_maintenance_counts(recovery_path: Path) -> tuple[int, int]:
    if not recovery_path.exists():
        return (0, 0)
    connection: sqlite3.Connection | None = None
    try:
        connection = _readonly_state_store_connection(
            recovery_path,
            store=SqliteStore.RECOVERY,
        )
        identity = _optional_scalar(
            connection,
            "SELECT store FROM store_identity WHERE singleton = 1",
        )
        if identity != SqliteStore.RECOVERY.value:
            raise SqliteStateBackupViolation(
                "STATE_RESTORE_MAINTENANCE_RECOVERY_IDENTITY_MISMATCH"
            )
        return (
            _count_states_in(
                connection,
                table_name="resource_leases",
                states=("ACQUIRED",),
            ),
            _count_states_in(
                connection,
                table_name="recovery_intent_segments",
                states=STATE_RESTORE_MAINTENANCE_UNRESOLVED_INTENT_STATES,
            ),
        )
    except sqlite3.Error as exc:
        raise SqliteStateBackupViolation(
            "STATE_RESTORE_MAINTENANCE_RECOVERY_EVIDENCE_UNREADABLE"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _readonly_state_store_connection(
    database_path: Path,
    *,
    store: SqliteStore,
) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        if store is SqliteStore.CATALOG:
            apply_sqlite_connection_policy(connection, catalog_reader_policy(database_path))
        elif store is SqliteStore.RECOVERY:
            apply_sqlite_connection_policy(connection, recovery_reader_policy(database_path))
        else:
            raise SqliteStateBackupViolation("STATE_BACKUP_STORE_UNSUPPORTED")
    except Exception:
        connection.close()
        raise
    return connection


def _incomplete_restore_epoch_count(layout: StateStoreLayout) -> int:
    epochs_dir = layout.root / STATE_RESTORE_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return 0
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCHS_PATH_NOT_DIRECTORY")
    count = 0
    for epoch_dir in epochs_dir.iterdir():
        if not epoch_dir.is_dir():
            continue
        if (epoch_dir / STATE_RESTORE_COMMITTED_FILENAME).exists():
            continue
        if (epoch_dir / STATE_RESTORE_ROLLED_BACK_FILENAME).exists():
            continue
        count += 1
    return count


def _incomplete_compaction_epoch_count(layout: StateStoreLayout) -> int:
    epochs_dir = layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME
    if not epochs_dir.exists():
        return 0
    if not epochs_dir.is_dir():
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCHS_PATH_NOT_DIRECTORY")
    count = 0
    for epoch_dir in epochs_dir.iterdir():
        if not epoch_dir.is_dir():
            continue
        if (epoch_dir / STATE_COMPACTION_COMMITTED_FILENAME).exists():
            continue
        if (epoch_dir / STATE_COMPACTION_ROLLED_BACK_FILENAME).exists():
            continue
        count += 1
    return count


def _count_blockers(
    *,
    active_run_count: int,
    active_run_target_count: int,
    non_terminal_command_receipt_count: int,
    pending_outbox_message_count: int,
    active_resource_lease_count: int,
    unresolved_target_intent_segment_count: int,
    incomplete_restore_epoch_count: int,
    incomplete_compaction_epoch_count: int,
) -> tuple[SqliteStateRestoreMaintenanceBlocker, ...]:
    blockers: list[SqliteStateRestoreMaintenanceBlocker] = []
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_ACTIVE_RUNS",
        count=active_run_count,
        store=SqliteStore.CATALOG,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_ACTIVE_RUN_TARGETS",
        count=active_run_target_count,
        store=SqliteStore.CATALOG,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_NON_TERMINAL_COMMAND_RECEIPTS",
        count=non_terminal_command_receipt_count,
        store=SqliteStore.CATALOG,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_PENDING_OUTBOX_MESSAGES",
        count=pending_outbox_message_count,
        store=SqliteStore.CATALOG,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_ACTIVE_RESOURCE_LEASES",
        count=active_resource_lease_count,
        store=SqliteStore.RECOVERY,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_UNRESOLVED_TARGET_INTENTS",
        count=unresolved_target_intent_segment_count,
        store=SqliteStore.RECOVERY,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_INCOMPLETE_RESTORE_EPOCHS",
        count=incomplete_restore_epoch_count,
    )
    _append_count_blocker(
        blockers,
        code="STATE_RESTORE_MAINTENANCE_INCOMPLETE_COMPACTION_EPOCHS",
        count=incomplete_compaction_epoch_count,
    )
    return tuple(blockers)


def _append_count_blocker(
    blockers: list[SqliteStateRestoreMaintenanceBlocker],
    *,
    code: str,
    count: int,
    store: SqliteStore | None = None,
) -> None:
    if count > 0:
        blockers.append(
            SqliteStateRestoreMaintenanceBlocker(
                code=code,
                count=count,
                store=store,
            )
        )


def _count_states_not_in(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    terminal_states: tuple[str, ...],
) -> int:
    placeholders = _sql_placeholders(terminal_states)
    return _required_int_scalar(
        connection,
        f"SELECT count(*) FROM {table_name} WHERE state NOT IN ({placeholders})",
        terminal_states,
    )


def _count_states_in(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    states: tuple[str, ...],
) -> int:
    placeholders = _sql_placeholders(states)
    return _required_int_scalar(
        connection,
        f"SELECT count(*) FROM {table_name} WHERE state IN ({placeholders})",
        states,
    )


def _sql_placeholders(values: tuple[object, ...]) -> str:
    if not values:
        raise SqliteStateBackupViolation("STATE_BACKUP_SQL_PLACEHOLDERS_EMPTY")
    return ", ".join("?" for _ in values)


def _target_intent_evidence_from_manifest(
    manifest: SqliteStateBackupManifest,
) -> tuple[int, str | None]:
    for entry in manifest.stores:
        if entry.store is SqliteStore.RECOVERY:
            return (entry.unresolved_target_intent_count, entry.target_intent_high_water_utc)
    raise SqliteStateBackupViolation("STATE_BACKUP_RECOVERY_STORE_MISSING")


def _has_newer_unresolved_target_intents(
    *,
    backup_count: int,
    backup_high_water: str | None,
    current_count: int,
    current_high_water: str | None,
) -> bool:
    if current_count == 0 or current_high_water is None:
        return False
    if backup_count == 0 or backup_high_water is None:
        return True
    return current_high_water > backup_high_water or (
        current_high_water == backup_high_water and current_count > backup_count
    )


def _validate_restore_plan(plan: SqliteStateRestorePlan) -> None:
    _validate_state_store_layout(plan.target_layout, field_name="STATE_RESTORE_TARGET")
    if tuple(entry.store for entry in plan.restore_files) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_RESTORE_INCOMPLETE_STORE_SET")
    for entry in plan.restore_files:
        expected_target = _source_path(plan.target_layout, entry.store)
        if entry.target_path != expected_target:
            raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_PATH_MISMATCH")
        if entry.target_path.parent != plan.target_layout.root:
            raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_STORES_MUST_BE_IN_ROOT")


def _prepare_restore_files(
    plan: SqliteStateRestorePlan,
    *,
    restore_epoch_id: str,
) -> tuple[_PreparedRestoreFile, ...]:
    prepared_files: list[_PreparedRestoreFile] = []
    temp_paths: list[Path] = []
    try:
        for entry in plan.restore_files:
            temp_path = _restore_temp_path(entry.target_path, restore_epoch_id)
            rollback_path = _restore_rollback_path(entry.target_path, restore_epoch_id)
            _require_absent(temp_path, "STATE_RESTORE_TEMP_FILE_ALREADY_EXISTS")
            _require_absent(rollback_path, "STATE_RESTORE_ROLLBACK_FILE_ALREADY_EXISTS")
            sidecar_rollbacks = tuple(
                SqliteStateSidecarRollback(
                    path=sidecar_path,
                    rollback_path=_restore_rollback_path(sidecar_path, restore_epoch_id),
                )
                for sidecar_path in _sqlite_sidecar_paths(entry.target_path)
                if sidecar_path.exists()
            )
            for sidecar in sidecar_rollbacks:
                if not sidecar.path.is_file():
                    raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_SIDECAR_NOT_FILE")
                _require_absent(
                    sidecar.rollback_path,
                    "STATE_RESTORE_SIDECAR_ROLLBACK_ALREADY_EXISTS",
                )
            _copy_file_no_overwrite(source=entry.backup_path, destination=temp_path)
            temp_paths.append(temp_path)
            _verify_restored_file(entry, temp_path)
            prepared_files.append(
                _PreparedRestoreFile(
                    restore_file=entry,
                    temp_path=temp_path,
                    rollback_path=rollback_path,
                    sidecar_rollbacks=sidecar_rollbacks,
                )
            )
    except Exception:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        raise
    return tuple(prepared_files)


def _swap_prepared_restore_file(prepared: _PreparedRestoreFile) -> SqliteStateRestoredFile:
    target_path = prepared.restore_file.target_path
    main_moved = False
    moved_sidecars: list[SqliteStateSidecarRollback] = []
    try:
        if target_path.exists():
            if not target_path.is_file():
                raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_NOT_FILE")
            target_path.replace(prepared.rollback_path)
            main_moved = True
        for sidecar in prepared.sidecar_rollbacks:
            if sidecar.path.exists():
                sidecar.path.replace(sidecar.rollback_path)
                moved_sidecars.append(sidecar)
        prepared.temp_path.replace(target_path)
    except Exception:
        _rollback_prepared_file(
            prepared,
            main_moved=main_moved,
            moved_sidecars=tuple(reversed(moved_sidecars)),
        )
        raise
    return SqliteStateRestoredFile(
        store=prepared.restore_file.store,
        target_path=target_path,
        rollback_path=prepared.rollback_path if main_moved else None,
        sidecar_rollbacks=tuple(moved_sidecars),
        size_bytes=prepared.restore_file.size_bytes,
        sha256=prepared.restore_file.sha256,
    )


def _rollback_prepared_file(
    prepared: _PreparedRestoreFile,
    *,
    main_moved: bool,
    moved_sidecars: tuple[SqliteStateSidecarRollback, ...],
) -> None:
    target_path = prepared.restore_file.target_path
    if target_path.exists() and target_path.is_file():
        target_path.unlink()
    if main_moved and prepared.rollback_path.exists():
        prepared.rollback_path.replace(target_path)
    for sidecar in moved_sidecars:
        if sidecar.path.exists() and sidecar.path.is_file():
            sidecar.path.unlink()
        if sidecar.rollback_path.exists():
            sidecar.rollback_path.replace(sidecar.path)


def _rollback_restored_files(restored_files: tuple[SqliteStateRestoredFile, ...]) -> None:
    for restored in restored_files:
        if restored.target_path.exists() and restored.target_path.is_file():
            restored.target_path.unlink()
        if restored.rollback_path is not None and restored.rollback_path.exists():
            restored.rollback_path.replace(restored.target_path)
        for sidecar in restored.sidecar_rollbacks:
            if sidecar.path.exists() and sidecar.path.is_file():
                sidecar.path.unlink()
            if sidecar.rollback_path.exists():
                sidecar.rollback_path.replace(sidecar.path)


def _prepare_compaction_files(
    layout: StateStoreLayout,
    *,
    compaction_epoch_id: str,
) -> tuple[_PreparedCompactionFile, ...]:
    prepared_files: list[_PreparedCompactionFile] = []
    temp_paths: list[Path] = []
    try:
        for store in STATE_BACKUP_SET_STORES:
            target_path = _source_path(layout, store)
            if not target_path.is_file():
                raise SqliteStateBackupViolation("STATE_COMPACTION_SOURCE_MISSING")
            temp_path = _compaction_temp_path(target_path, compaction_epoch_id)
            rollback_path = _compaction_rollback_path(target_path, compaction_epoch_id)
            _require_absent(temp_path, "STATE_COMPACTION_TEMP_FILE_ALREADY_EXISTS")
            _require_absent(rollback_path, "STATE_COMPACTION_ROLLBACK_FILE_ALREADY_EXISTS")
            sidecar_rollbacks = tuple(
                SqliteStateSidecarRollback(
                    path=sidecar_path,
                    rollback_path=_compaction_rollback_path(
                        sidecar_path,
                        compaction_epoch_id,
                    ),
                )
                for sidecar_path in _sqlite_sidecar_paths(target_path)
                if sidecar_path.exists()
            )
            for sidecar in sidecar_rollbacks:
                if not sidecar.path.is_file():
                    raise SqliteStateBackupViolation("STATE_COMPACTION_TARGET_SIDECAR_NOT_FILE")
                _require_absent(
                    sidecar.rollback_path,
                    "STATE_COMPACTION_SIDECAR_ROLLBACK_ALREADY_EXISTS",
                )
            _vacuum_sqlite_database_into(source_path=target_path, output_path=temp_path)
            temp_paths.append(temp_path)
            inspected = _inspect_backup_file(store=store, backup_path=temp_path)
            compaction_file = SqliteStateCompactionFile(
                store=store,
                target_path=target_path,
                temp_path=temp_path,
                rollback_path=rollback_path,
                sidecar_rollbacks=sidecar_rollbacks,
                size_bytes=inspected.size_bytes,
                sha256=inspected.sha256,
                schema_version=inspected.schema_version,
                migration_count=inspected.migration_count,
                latest_migration_utc=inspected.latest_migration_utc,
                page_count=inspected.page_count,
                quick_check=inspected.quick_check,
                foreign_key_violations=inspected.foreign_key_violations,
                unresolved_target_intent_count=inspected.unresolved_target_intent_count,
                target_intent_high_water_utc=inspected.target_intent_high_water_utc,
            )
            _verify_compacted_file(compaction_file, temp_path)
            prepared_files.append(
                _PreparedCompactionFile(
                    compaction_file=compaction_file,
                    temp_path=temp_path,
                    rollback_path=rollback_path,
                    sidecar_rollbacks=sidecar_rollbacks,
                )
            )
    except Exception:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        raise
    return tuple(prepared_files)


def _swap_prepared_compaction_file(
    prepared: _PreparedCompactionFile,
) -> SqliteStateCompactedFile:
    target_path = prepared.compaction_file.target_path
    main_moved = False
    moved_sidecars: list[SqliteStateSidecarRollback] = []
    try:
        if target_path.exists():
            if not target_path.is_file():
                raise SqliteStateBackupViolation("STATE_COMPACTION_TARGET_NOT_FILE")
            target_path.replace(prepared.rollback_path)
            main_moved = True
        for sidecar in prepared.sidecar_rollbacks:
            if sidecar.path.exists():
                sidecar.path.replace(sidecar.rollback_path)
                moved_sidecars.append(sidecar)
        prepared.temp_path.replace(target_path)
    except Exception:
        _rollback_prepared_compaction_file(
            prepared,
            main_moved=main_moved,
            moved_sidecars=tuple(reversed(moved_sidecars)),
        )
        raise
    return SqliteStateCompactedFile(
        store=prepared.compaction_file.store,
        target_path=target_path,
        rollback_path=prepared.rollback_path if main_moved else None,
        sidecar_rollbacks=tuple(moved_sidecars),
        size_bytes=prepared.compaction_file.size_bytes,
        sha256=prepared.compaction_file.sha256,
    )


def _rollback_prepared_compaction_file(
    prepared: _PreparedCompactionFile,
    *,
    main_moved: bool,
    moved_sidecars: tuple[SqliteStateSidecarRollback, ...],
) -> None:
    target_path = prepared.compaction_file.target_path
    if target_path.exists() and target_path.is_file():
        target_path.unlink()
    if main_moved and prepared.rollback_path.exists():
        prepared.rollback_path.replace(target_path)
    for sidecar in moved_sidecars:
        if sidecar.path.exists() and sidecar.path.is_file():
            sidecar.path.unlink()
        if sidecar.rollback_path.exists():
            sidecar.rollback_path.replace(sidecar.path)


def _rollback_compacted_files(compacted_files: tuple[SqliteStateCompactedFile, ...]) -> None:
    for compacted in compacted_files:
        if compacted.target_path.exists() and compacted.target_path.is_file():
            compacted.target_path.unlink()
        if compacted.rollback_path is not None and compacted.rollback_path.exists():
            compacted.rollback_path.replace(compacted.target_path)
        for sidecar in compacted.sidecar_rollbacks:
            if sidecar.path.exists() and sidecar.path.is_file():
                sidecar.path.unlink()
            if sidecar.rollback_path.exists():
                sidecar.rollback_path.replace(sidecar.path)


def _load_compaction_epoch_intent(
    *,
    epoch_dir: Path,
    layout: StateStoreLayout,
) -> _CompactionEpochIntent:
    intent_path = epoch_dir / STATE_COMPACTION_INTENT_FILENAME
    try:
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_INTENT_MISSING") from exc
    except json.JSONDecodeError as exc:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_INTENT_INVALID_JSON") from exc
    return _compaction_epoch_intent_from_payload(
        payload,
        intent_path=intent_path,
        layout=layout,
        expected_epoch_id=epoch_dir.name,
    )


def _compaction_epoch_intent_from_payload(
    payload: object,
    *,
    intent_path: Path,
    layout: StateStoreLayout,
    expected_epoch_id: str,
) -> _CompactionEpochIntent:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_INTENT_NOT_OBJECT")
    schema_version = _int_field(payload, "schema_version")
    if schema_version != STATE_COMPACTION_EPOCH_SCHEMA_VERSION:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_SCHEMA_UNSUPPORTED")
    if _str_field(payload, "status") != "PREPARED":
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_STATUS_UNSUPPORTED")
    compaction_epoch_id = _str_field(payload, "compaction_epoch_id")
    _validate_compaction_epoch_id(compaction_epoch_id)
    if compaction_epoch_id != expected_epoch_id:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_ID_MISMATCH")
    state_set_hash = _hex_field(payload, "state_set_hash")
    files_payload = payload.get("compaction_files")
    if not isinstance(files_payload, list):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_FILES_NOT_ARRAY")
    compaction_files = tuple(
        _compaction_epoch_file_from_payload(
            entry,
            layout=layout,
            compaction_epoch_id=compaction_epoch_id,
        )
        for entry in files_payload
    )
    if tuple(entry.store for entry in compaction_files) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_INCOMPLETE_STORE_SET")
    return _CompactionEpochIntent(
        compaction_epoch_id=compaction_epoch_id,
        state_set_hash=state_set_hash,
        intent_path=intent_path,
        compaction_files=compaction_files,
    )


def _compaction_epoch_file_from_payload(
    payload: object,
    *,
    layout: StateStoreLayout,
    compaction_epoch_id: str,
) -> SqliteStateCompactionFile:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_FILE_NOT_OBJECT")
    try:
        store = SqliteStore(_str_field(payload, "store"))
    except ValueError as exc:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_STORE_UNSUPPORTED") from exc
    target_path = _path_field(payload, "target_path")
    expected_target_path = _source_path(layout, store)
    if target_path != expected_target_path:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_TARGET_PATH_MISMATCH")
    temp_path = _path_field(payload, "temp_path")
    if temp_path != _compaction_temp_path(target_path, compaction_epoch_id):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_TEMP_PATH_MISMATCH")
    rollback_path = _path_field(payload, "rollback_path")
    if rollback_path != _compaction_rollback_path(target_path, compaction_epoch_id):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_ROLLBACK_PATH_MISMATCH")
    sidecar_payload = payload.get("sidecar_rollbacks")
    if not isinstance(sidecar_payload, list):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_SIDECARS_NOT_ARRAY")
    sidecar_rollbacks = tuple(
        _compaction_sidecar_rollback_from_payload(
            entry,
            target_path=target_path,
            compaction_epoch_id=compaction_epoch_id,
        )
        for entry in sidecar_payload
    )
    return SqliteStateCompactionFile(
        store=store,
        target_path=target_path,
        temp_path=temp_path,
        rollback_path=rollback_path,
        sidecar_rollbacks=sidecar_rollbacks,
        size_bytes=_non_negative_int_field(payload, "size_bytes"),
        sha256=_hex_field(payload, "sha256"),
        schema_version=_non_negative_int_field(payload, "schema_version"),
        migration_count=_non_negative_int_field(payload, "migration_count"),
        latest_migration_utc=_optional_str_field(payload, "latest_migration_utc"),
        page_count=_non_negative_int_field(payload, "page_count"),
        quick_check=_str_field(payload, "quick_check"),
        foreign_key_violations=_non_negative_int_field(payload, "foreign_key_violations"),
        unresolved_target_intent_count=_non_negative_int_field(
            payload,
            "unresolved_target_intent_count",
        ),
        target_intent_high_water_utc=_optional_str_field(
            payload,
            "target_intent_high_water_utc",
        ),
    )


def _compaction_sidecar_rollback_from_payload(
    payload: object,
    *,
    target_path: Path,
    compaction_epoch_id: str,
) -> SqliteStateSidecarRollback:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_SIDECAR_NOT_OBJECT")
    sidecar_path = _path_field(payload, "path")
    if sidecar_path not in _sqlite_sidecar_paths(target_path):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_SIDECAR_PATH_MISMATCH")
    rollback_path = _path_field(payload, "rollback_path")
    if rollback_path != _compaction_rollback_path(sidecar_path, compaction_epoch_id):
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_SIDECAR_ROLLBACK_MISMATCH")
    return SqliteStateSidecarRollback(path=sidecar_path, rollback_path=rollback_path)


def _rollback_incomplete_compaction_epoch(
    intent: _CompactionEpochIntent,
) -> tuple[int, int, int]:
    rolled_back_store_count = 0
    removed_temp_file_count = 0
    restored_sidecar_count = 0
    for compaction_file in reversed(intent.compaction_files):
        if compaction_file.temp_path.exists():
            if not compaction_file.temp_path.is_file():
                raise SqliteStateBackupViolation("STATE_COMPACTION_TEMP_PATH_NOT_FILE")
            compaction_file.temp_path.unlink()
            removed_temp_file_count += 1
        if compaction_file.rollback_path.exists():
            if not compaction_file.rollback_path.is_file():
                raise SqliteStateBackupViolation("STATE_COMPACTION_ROLLBACK_PATH_NOT_FILE")
            if compaction_file.target_path.exists():
                if not compaction_file.target_path.is_file():
                    raise SqliteStateBackupViolation("STATE_COMPACTION_TARGET_NOT_FILE")
                compaction_file.target_path.unlink()
            compaction_file.rollback_path.replace(compaction_file.target_path)
            rolled_back_store_count += 1
        for sidecar in compaction_file.sidecar_rollbacks:
            if sidecar.rollback_path.exists():
                if not sidecar.rollback_path.is_file():
                    raise SqliteStateBackupViolation(
                        "STATE_COMPACTION_SIDECAR_ROLLBACK_NOT_FILE"
                    )
                if sidecar.path.exists():
                    if not sidecar.path.is_file():
                        raise SqliteStateBackupViolation("STATE_COMPACTION_SIDECAR_PATH_NOT_FILE")
                    sidecar.path.unlink()
                sidecar.rollback_path.replace(sidecar.path)
                restored_sidecar_count += 1
    return (rolled_back_store_count, removed_temp_file_count, restored_sidecar_count)


def _load_restore_epoch_intent(
    *,
    epoch_dir: Path,
    layout: StateStoreLayout,
) -> _RestoreEpochIntent:
    intent_path = epoch_dir / STATE_RESTORE_INTENT_FILENAME
    try:
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INTENT_MISSING") from exc
    except json.JSONDecodeError as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INTENT_INVALID_JSON") from exc
    return _restore_epoch_intent_from_payload(
        payload,
        intent_path=intent_path,
        layout=layout,
        expected_epoch_id=epoch_dir.name,
    )


def _restore_epoch_intent_from_payload(
    payload: object,
    *,
    intent_path: Path,
    layout: StateStoreLayout,
    expected_epoch_id: str,
) -> _RestoreEpochIntent:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INTENT_NOT_OBJECT")
    schema_version = _int_field(payload, "schema_version")
    if schema_version != STATE_RESTORE_EPOCH_SCHEMA_VERSION:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SCHEMA_UNSUPPORTED")
    if _str_field(payload, "status") != "PREPARED":
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_STATUS_UNSUPPORTED")
    restore_epoch_id = _str_field(payload, "restore_epoch_id")
    _validate_restore_epoch_id(restore_epoch_id)
    if restore_epoch_id != expected_epoch_id:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ID_MISMATCH")
    backup_set_id = _str_field(payload, "backup_set_id")
    _validate_backup_set_id(backup_set_id)
    state_set_hash = _hex_field(payload, "state_set_hash")
    restore_files_payload = payload.get("restore_files")
    if not isinstance(restore_files_payload, list):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_FILES_NOT_ARRAY")
    restore_files = tuple(
        _restore_epoch_intent_file_from_payload(
            entry,
            layout=layout,
            restore_epoch_id=restore_epoch_id,
        )
        for entry in restore_files_payload
    )
    if tuple(entry.store for entry in restore_files) != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_INCOMPLETE_STORE_SET")
    return _RestoreEpochIntent(
        restore_epoch_id=restore_epoch_id,
        backup_set_id=backup_set_id,
        state_set_hash=state_set_hash,
        intent_path=intent_path,
        restore_files=restore_files,
    )


def _restore_epoch_intent_file_from_payload(
    payload: object,
    *,
    layout: StateStoreLayout,
    restore_epoch_id: str,
) -> _RestoreEpochIntentFile:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_FILE_NOT_OBJECT")
    try:
        store = SqliteStore(_str_field(payload, "store"))
    except ValueError as exc:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_STORE_UNSUPPORTED") from exc
    target_path = _path_field(payload, "target_path")
    expected_target_path = _source_path(layout, store)
    if target_path != expected_target_path:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_TARGET_PATH_MISMATCH")
    temp_path = _path_field(payload, "temp_path")
    if temp_path != _restore_temp_path(target_path, restore_epoch_id):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_TEMP_PATH_MISMATCH")
    rollback_path = _path_field(payload, "rollback_path")
    if rollback_path != _restore_rollback_path(target_path, restore_epoch_id):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ROLLBACK_PATH_MISMATCH")
    sidecar_payload = payload.get("sidecar_rollbacks")
    if not isinstance(sidecar_payload, list):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECARS_NOT_ARRAY")
    sidecar_rollbacks = tuple(
        _sidecar_rollback_from_payload(
            entry,
            target_path=target_path,
            restore_epoch_id=restore_epoch_id,
        )
        for entry in sidecar_payload
    )
    return _RestoreEpochIntentFile(
        store=store,
        target_path=target_path,
        temp_path=temp_path,
        rollback_path=rollback_path,
        sidecar_rollbacks=sidecar_rollbacks,
    )


def _sidecar_rollback_from_payload(
    payload: object,
    *,
    target_path: Path,
    restore_epoch_id: str,
) -> SqliteStateSidecarRollback:
    if not isinstance(payload, dict):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECAR_NOT_OBJECT")
    sidecar_path = _path_field(payload, "path")
    if sidecar_path not in _sqlite_sidecar_paths(target_path):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECAR_PATH_MISMATCH")
    rollback_path = _path_field(payload, "rollback_path")
    if rollback_path != _restore_rollback_path(sidecar_path, restore_epoch_id):
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_SIDECAR_ROLLBACK_MISMATCH")
    return SqliteStateSidecarRollback(path=sidecar_path, rollback_path=rollback_path)


def _rollback_incomplete_restore_epoch(intent: _RestoreEpochIntent) -> tuple[int, int, int]:
    rolled_back_store_count = 0
    removed_temp_file_count = 0
    restored_sidecar_count = 0
    for restore_file in reversed(intent.restore_files):
        if restore_file.temp_path.exists():
            if not restore_file.temp_path.is_file():
                raise SqliteStateBackupViolation("STATE_RESTORE_TEMP_PATH_NOT_FILE")
            restore_file.temp_path.unlink()
            removed_temp_file_count += 1
        if restore_file.rollback_path.exists():
            if not restore_file.rollback_path.is_file():
                raise SqliteStateBackupViolation("STATE_RESTORE_ROLLBACK_PATH_NOT_FILE")
            if restore_file.target_path.exists():
                if not restore_file.target_path.is_file():
                    raise SqliteStateBackupViolation("STATE_RESTORE_TARGET_NOT_FILE")
                restore_file.target_path.unlink()
            restore_file.rollback_path.replace(restore_file.target_path)
            rolled_back_store_count += 1
        for sidecar in restore_file.sidecar_rollbacks:
            if sidecar.rollback_path.exists():
                if not sidecar.rollback_path.is_file():
                    raise SqliteStateBackupViolation("STATE_RESTORE_SIDECAR_ROLLBACK_NOT_FILE")
                if sidecar.path.exists():
                    if not sidecar.path.is_file():
                        raise SqliteStateBackupViolation("STATE_RESTORE_SIDECAR_PATH_NOT_FILE")
                    sidecar.path.unlink()
                sidecar.rollback_path.replace(sidecar.path)
                restored_sidecar_count += 1
    return (rolled_back_store_count, removed_temp_file_count, restored_sidecar_count)


def _verify_restored_file(entry: SqliteStateRestoreFile, database_path: Path) -> None:
    inspected = _inspect_backup_file(store=entry.store, backup_path=database_path)
    expected = SqliteStateStoreBackup(
        store=entry.store,
        file_name=database_path.name,
        size_bytes=entry.size_bytes,
        sha256=entry.sha256,
        schema_version=entry.schema_version,
        migration_count=entry.migration_count,
        latest_migration_utc=entry.latest_migration_utc,
        page_count=entry.page_count,
        quick_check=entry.quick_check,
        foreign_key_violations=entry.foreign_key_violations,
        unresolved_target_intent_count=entry.unresolved_target_intent_count,
        target_intent_high_water_utc=entry.target_intent_high_water_utc,
    )
    if inspected != expected:
        raise SqliteStateBackupViolation("STATE_RESTORE_SQLITE_EVIDENCE_MISMATCH")


def _verify_compacted_file(entry: SqliteStateCompactionFile, database_path: Path) -> None:
    inspected = _inspect_backup_file(store=entry.store, backup_path=database_path)
    expected = SqliteStateStoreBackup(
        store=entry.store,
        file_name=database_path.name,
        size_bytes=entry.size_bytes,
        sha256=entry.sha256,
        schema_version=entry.schema_version,
        migration_count=entry.migration_count,
        latest_migration_utc=entry.latest_migration_utc,
        page_count=entry.page_count,
        quick_check=entry.quick_check,
        foreign_key_violations=entry.foreign_key_violations,
        unresolved_target_intent_count=entry.unresolved_target_intent_count,
        target_intent_high_water_utc=entry.target_intent_high_water_utc,
    )
    if inspected != expected:
        raise SqliteStateBackupViolation("STATE_COMPACTION_SQLITE_EVIDENCE_MISMATCH")


def _copy_file_no_overwrite(*, source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SqliteStateBackupViolation("STATE_RESTORE_BACKUP_FILE_MISSING")
    if destination.exists():
        raise SqliteStateBackupViolation("STATE_RESTORE_TEMP_FILE_ALREADY_EXISTS")
    try:
        with source.open("rb") as source_handle:
            with destination.open("xb") as destination_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    destination_handle.write(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise SqliteStateBackupViolation("STATE_RESTORE_TEMP_COPY_FAILED") from exc


def _vacuum_sqlite_database_into(*, source_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise SqliteStateBackupViolation("STATE_COMPACTION_TEMP_FILE_ALREADY_EXISTS")
    try:
        with sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True) as connection:
            connection.execute("VACUUM INTO ?", (str(output_path),))
        _fsync_file(output_path)
    except sqlite3.Error as exc:
        output_path.unlink(missing_ok=True)
        raise SqliteStateBackupViolation("STATE_COMPACTION_VACUUM_FAILED") from exc
    except OSError as exc:
        output_path.unlink(missing_ok=True)
        raise SqliteStateBackupViolation("STATE_COMPACTION_TEMP_SYNC_FAILED") from exc


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _restore_epoch_dir(layout: StateStoreLayout, restore_epoch_id: str) -> Path:
    return layout.root / STATE_RESTORE_EPOCHS_DIR_NAME / restore_epoch_id


def _compaction_epoch_dir(layout: StateStoreLayout, compaction_epoch_id: str) -> Path:
    return layout.root / STATE_COMPACTION_EPOCHS_DIR_NAME / compaction_epoch_id


def _restore_temp_path(target_path: Path, restore_epoch_id: str) -> Path:
    return target_path.with_name(f".{target_path.name}.{restore_epoch_id}.restore-new.tmp")


def _restore_rollback_path(target_path: Path, restore_epoch_id: str) -> Path:
    return target_path.with_name(f".{target_path.name}.{restore_epoch_id}.restore-rollback")


def _compaction_temp_path(target_path: Path, compaction_epoch_id: str) -> Path:
    return target_path.with_name(
        f".{target_path.name}.{compaction_epoch_id}.compaction-new.tmp"
    )


def _compaction_rollback_path(target_path: Path, compaction_epoch_id: str) -> Path:
    return target_path.with_name(
        f".{target_path.name}.{compaction_epoch_id}.compaction-rollback"
    )


def _sqlite_sidecar_paths(database_path: Path) -> tuple[Path, ...]:
    raw = str(database_path)
    return (Path(f"{raw}-wal"), Path(f"{raw}-shm"), Path(f"{raw}-journal"))


def _require_absent(path: Path, violation: str) -> None:
    if path.exists():
        raise SqliteStateBackupViolation(violation)


def _restore_intent_payload(
    *,
    plan: SqliteStateRestorePlan,
    restore_epoch_id: str,
    started_utc: str,
    prepared_files: tuple[_PreparedRestoreFile, ...],
) -> dict[str, object]:
    return {
        "schema_version": STATE_RESTORE_EPOCH_SCHEMA_VERSION,
        "status": "PREPARED",
        "restore_epoch_id": restore_epoch_id,
        "started_utc": started_utc,
        "backup_set_id": plan.backup_set_id,
        "state_set_hash": plan.state_set_hash,
        "backup_unresolved_target_intent_count": (
            plan.backup_unresolved_target_intent_count
        ),
        "backup_target_intent_high_water_utc": plan.backup_target_intent_high_water_utc,
        "current_unresolved_target_intent_count": (
            plan.current_unresolved_target_intent_count
        ),
        "current_target_intent_high_water_utc": plan.current_target_intent_high_water_utc,
        "restore_files": [
            {
                **prepared.restore_file.to_payload(),
                "temp_path": str(prepared.temp_path),
                "rollback_path": str(prepared.rollback_path),
                "sidecar_rollbacks": [
                    {
                        "path": str(sidecar.path),
                        "rollback_path": str(sidecar.rollback_path),
                    }
                    for sidecar in prepared.sidecar_rollbacks
                ],
            }
            for prepared in prepared_files
        ],
    }


def _restore_committed_payload(
    *,
    plan: SqliteStateRestorePlan,
    restore_epoch_id: str,
    started_utc: str,
    restored_files: tuple[SqliteStateRestoredFile, ...],
) -> dict[str, object]:
    return {
        "schema_version": STATE_RESTORE_EPOCH_SCHEMA_VERSION,
        "status": "COMMITTED",
        "restore_epoch_id": restore_epoch_id,
        "started_utc": started_utc,
        "backup_set_id": plan.backup_set_id,
        "state_set_hash": plan.state_set_hash,
        "restored_files": [entry.to_payload() for entry in restored_files],
    }


def _restore_rolled_back_payload(
    *,
    intent: _RestoreEpochIntent,
    recovered_utc: str,
    rolled_back_store_count: int,
    removed_temp_file_count: int,
    restored_sidecar_count: int,
) -> dict[str, object]:
    return {
        "schema_version": STATE_RESTORE_EPOCH_SCHEMA_VERSION,
        "status": "ROLLED_BACK",
        "restore_epoch_id": intent.restore_epoch_id,
        "recovered_utc": recovered_utc,
        "backup_set_id": intent.backup_set_id,
        "state_set_hash": intent.state_set_hash,
        "rolled_back_store_count": rolled_back_store_count,
        "removed_temp_file_count": removed_temp_file_count,
        "restored_sidecar_count": restored_sidecar_count,
    }


def _compaction_intent_payload(
    *,
    compaction_epoch_id: str,
    started_utc: str,
    state_set_hash: str,
    prepared_files: tuple[_PreparedCompactionFile, ...],
) -> dict[str, object]:
    return {
        "schema_version": STATE_COMPACTION_EPOCH_SCHEMA_VERSION,
        "status": "PREPARED",
        "compaction_epoch_id": compaction_epoch_id,
        "started_utc": started_utc,
        "state_set_hash": state_set_hash,
        "compaction_files": [
            {
                **prepared.compaction_file.to_payload(),
                "temp_path": str(prepared.temp_path),
                "rollback_path": str(prepared.rollback_path),
                "sidecar_rollbacks": [
                    {
                        "path": str(sidecar.path),
                        "rollback_path": str(sidecar.rollback_path),
                    }
                    for sidecar in prepared.sidecar_rollbacks
                ],
            }
            for prepared in prepared_files
        ],
    }


def _compaction_committed_payload(
    *,
    compaction_epoch_id: str,
    started_utc: str,
    state_set_hash: str,
    compacted_files: tuple[SqliteStateCompactedFile, ...],
) -> dict[str, object]:
    return {
        "schema_version": STATE_COMPACTION_EPOCH_SCHEMA_VERSION,
        "status": "COMMITTED",
        "compaction_epoch_id": compaction_epoch_id,
        "started_utc": started_utc,
        "state_set_hash": state_set_hash,
        "compacted_files": [entry.to_payload() for entry in compacted_files],
    }


def _compaction_rolled_back_payload(
    *,
    intent: _CompactionEpochIntent,
    recovered_utc: str,
    rolled_back_store_count: int,
    removed_temp_file_count: int,
    restored_sidecar_count: int,
) -> dict[str, object]:
    return {
        "schema_version": STATE_COMPACTION_EPOCH_SCHEMA_VERSION,
        "status": "ROLLED_BACK",
        "compaction_epoch_id": intent.compaction_epoch_id,
        "recovered_utc": recovered_utc,
        "state_set_hash": intent.state_set_hash,
        "rolled_back_store_count": rolled_back_store_count,
        "removed_temp_file_count": removed_temp_file_count,
        "restored_sidecar_count": restored_sidecar_count,
    }


def _validate_manifest_store_set(stores: tuple[SqliteStateStoreBackup, ...]) -> None:
    seen = tuple(entry.store for entry in stores)
    if seen != STATE_BACKUP_SET_STORES:
        raise SqliteStateBackupViolation("STATE_BACKUP_INCOMPLETE_STORE_SET")
    if len(set(seen)) != len(seen):
        raise SqliteStateBackupViolation("STATE_BACKUP_DUPLICATE_STORE")
    for entry in stores:
        if entry.file_name != _backup_file_name(entry.store):
            raise SqliteStateBackupViolation("STATE_BACKUP_FILE_NAME_MISMATCH")


def _state_set_hash(
    *,
    backup_set_id: str,
    created_utc: str,
    stores: tuple[SqliteStateStoreBackup, ...],
) -> str:
    return _sha256_bytes(
        _canonical_json_bytes(
            {
                "backup_set_id": backup_set_id,
                "created_utc": created_utc,
                "stores": [entry.to_payload() for entry in stores],
            }
        )
    )


def _state_compaction_set_hash(
    *,
    compaction_epoch_id: str,
    started_utc: str,
    compaction_files: tuple[SqliteStateCompactionFile, ...],
) -> str:
    return _sha256_bytes(
        _canonical_json_bytes(
            {
                "compaction_epoch_id": compaction_epoch_id,
                "started_utc": started_utc,
                "compaction_files": [entry.to_payload() for entry in compaction_files],
            }
        )
    )


def _intent_payload(*, backup_set_id: str, created_utc: str) -> dict[str, object]:
    return {
        "schema_version": STATE_BACKUP_SET_INTENT_SCHEMA_VERSION,
        "backup_set_id": backup_set_id,
        "created_utc": created_utc,
        "expected_stores": [store.value for store in STATE_BACKUP_SET_STORES],
        "expected_files": [_backup_file_name(store) for store in STATE_BACKUP_SET_STORES],
    }


def _source_path(layout: StateStoreLayout, store: SqliteStore) -> Path:
    if store is SqliteStore.CATALOG:
        return layout.catalog
    if store is SqliteStore.RECOVERY:
        return layout.recovery
    raise SqliteStateBackupViolation("STATE_BACKUP_STORE_UNSUPPORTED")


def _validate_state_store_layout(layout: StateStoreLayout, *, field_name: str) -> None:
    _validate_local_absolute_path(layout.root, f"{field_name}_ROOT")
    _validate_local_absolute_path(layout.catalog, f"{field_name}_CATALOG")
    _validate_local_absolute_path(layout.recovery, f"{field_name}_RECOVERY")
    if layout.catalog == layout.recovery:
        raise SqliteStateBackupViolation(f"{field_name}_STORES_MUST_BE_SEPARATE_FILES")


def _backup_file_name(store: SqliteStore) -> str:
    return f"{store.value}.sqlite.backup"


def _write_json_no_overwrite(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise SqliteStateBackupViolation("STATE_BACKUP_CONTROL_FILE_ALREADY_EXISTS")
    with path.open("xb") as handle:
        handle.write(_canonical_json_bytes(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _canonical_json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_backup_set_id(value: str) -> None:
    if STATE_BACKUP_SET_ID_PATTERN.fullmatch(value) is None:
        raise SqliteStateBackupViolation("STATE_BACKUP_SET_ID_INVALID")


def _validate_restore_epoch_id(value: str) -> None:
    if STATE_BACKUP_SET_ID_PATTERN.fullmatch(value) is None:
        raise SqliteStateBackupViolation("STATE_RESTORE_EPOCH_ID_INVALID")


def _validate_compaction_epoch_id(value: str) -> None:
    if STATE_BACKUP_SET_ID_PATTERN.fullmatch(value) is None:
        raise SqliteStateBackupViolation("STATE_COMPACTION_EPOCH_ID_INVALID")


def _validate_local_absolute_path(path: Path, field_name: str) -> None:
    if not path.is_absolute():
        raise SqliteStateBackupViolation(f"{field_name}_MUST_BE_ABSOLUTE")
    if str(path).startswith("\\\\"):
        raise SqliteStateBackupViolation(f"{field_name}_MUST_BE_LOCAL")


def _safe_file_name(value: str) -> str:
    if "/" in value or "\\" in value or value in {"", ".", ".."}:
        raise SqliteStateBackupViolation("STATE_BACKUP_FILE_NAME_INVALID")
    return value


def _required_scalar(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> object:
    row = connection.execute(query, parameters).fetchone()
    if row is None:
        raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_MISSING")
    return cast(object, row[0])


def _required_int_scalar(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> int:
    value = _required_scalar(connection, query, parameters)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SqliteStateBackupViolation("STATE_BACKUP_SQLITE_EVIDENCE_INVALID")
    return value


def _optional_scalar(connection: sqlite3.Connection, query: str) -> object | None:
    row = connection.execute(query).fetchone()
    if row is None:
        return None
    return cast(object | None, row[0])


def _str_field(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _optional_str_field(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _path_field(payload: dict[str, Any], field_name: str) -> Path:
    value = _str_field(payload, field_name)
    path = Path(value)
    if not path.is_absolute():
        raise SqliteStateBackupViolation(f"STATE_RESTORE_{field_name.upper()}_MUST_BE_ABSOLUTE")
    return path


def _optional_path_field(payload: dict[str, Any], field_name: str) -> Path | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SqliteStateBackupViolation(f"STATE_RESTORE_{field_name.upper()}_INVALID")
    path = Path(value)
    if not path.is_absolute():
        raise SqliteStateBackupViolation(f"STATE_RESTORE_{field_name.upper()}_MUST_BE_ABSOLUTE")
    return path


def _int_field(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _non_negative_int_field(payload: dict[str, Any], field_name: str) -> int:
    value = _int_field(payload, field_name)
    if value < 0:
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value


def _hex_field(payload: dict[str, Any], field_name: str) -> str:
    value = _str_field(payload, field_name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SqliteStateBackupViolation(f"STATE_BACKUP_{field_name.upper()}_INVALID")
    return value
