from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.directory_reconciliation import (
    LocalDirectoryRecoveryObservationAdapter,
)
from mediasync_home.adapters.endpoint_leases import EndpointRootDescriptor
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.directory_metadata import (
    SqliteDirectoryMetadataCatalogStore,
)
from mediasync_home.adapters.sqlite.directory_recovery import (
    SqliteDirectoryRecoveryStore,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.application.directory_artifacts import (
    DIRECTORY_MARKER_NAME,
    directory_marker_bytes,
)
from mediasync_home.application.directory_reconciliation import (
    reconcile_directory_recovery_after_startup,
)
from mediasync_home.application.directory_recovery import (
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryTransition,
    planned_directory_recovery_operation,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.version_objects import (
    THIRTY_DAY_RETENTION_POLICY,
    create_quarantine_object_manifest,
)


def test_create_reconciliation_proves_identity_then_catalog_terminal(
    tmp_path: Path,
) -> None:
    catalog, recovery_connection, root = _environment(tmp_path)
    try:
        recovery = SqliteDirectoryRecoveryStore(recovery_connection)
        operation = _record_at_intent(recovery, DirectoryRecoveryKind.CREATE)
        final_path = root / "Parent" / "Folder"
        final_path.mkdir(parents=True)
        (final_path / DIRECTORY_MARKER_NAME).write_bytes(
            directory_marker_bytes(
                run_id=operation.run_id,
                run_target_id=operation.run_target_id,
                operation_id=operation.operation_id,
                final_relative_path=operation.final_relative_path,
            )
        )
        generic = _GenericLookup(_generic_operation(RecoveryOperationPhase.FINAL_VERIFIED))
        observer = _observer(root=root, catalog=catalog, generic=generic)

        reconciled = reconcile_directory_recovery_after_startup(
            store=recovery,
            observer=observer,
            process_instance_id="host-new",
        )

        assert reconciled.mutation_safe is True
        loaded = recovery.load_directory_recovery_operation(operation.recovery_id)
        assert loaded is not None
        assert loaded.state is SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.CREATE][-2]

        generic.operation = replace(
            generic.operation,
            phase=RecoveryOperationPhase.CATALOG_RECORDED,
        )
        reconcile_directory_recovery_after_startup(
            store=recovery,
            observer=observer,
            process_instance_id="host-new",
        )
        loaded = recovery.load_directory_recovery_operation(operation.recovery_id)
        assert loaded is not None
        assert loaded.state is SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.CREATE][-1]
    finally:
        catalog.close()
        recovery_connection.close()


@pytest.mark.parametrize(
    ("kind", "catalog_phase"),
    (
        (DirectoryRecoveryKind.QUARANTINE, RecoveryOperationPhase.CATALOG_RECORDED),
        (DirectoryRecoveryKind.RESTORE, RecoveryOperationPhase.CANCELLED),
    ),
)
def test_quarantine_and_restore_reconcile_real_manifest_crash_windows(
    tmp_path: Path,
    kind: DirectoryRecoveryKind,
    catalog_phase: RecoveryOperationPhase,
) -> None:
    catalog, recovery_connection, root = _environment(tmp_path)
    try:
        recovery = SqliteDirectoryRecoveryStore(recovery_connection)
        operation = _record_at_intent(recovery, kind)
        generic_operation = _generic_operation(RecoveryOperationPhase.OLD_TARGET_PRESERVED)
        _write_quarantine_object(root, generic_operation)
        final_path = root / "Parent" / "Folder"
        if kind is DirectoryRecoveryKind.RESTORE:
            final_path.mkdir(parents=True)
        generic = _GenericLookup(generic_operation)
        observer = _observer(root=root, catalog=catalog, generic=generic)

        reconciled = reconcile_directory_recovery_after_startup(
            store=recovery,
            observer=observer,
            process_instance_id="host-new",
        )

        assert reconciled.mutation_safe is True
        loaded = recovery.load_directory_recovery_operation(operation.recovery_id)
        assert loaded is not None
        assert loaded.state is SUCCESS_PATH_BY_KIND[kind][-2]
        assert loaded.managed_object_id == operation.operation_id

        generic.operation = replace(generic.operation, phase=catalog_phase)
        reconcile_directory_recovery_after_startup(
            store=recovery,
            observer=observer,
            process_instance_id="host-new",
        )
        loaded = recovery.load_directory_recovery_operation(operation.recovery_id)
        assert loaded is not None
        assert loaded.state is SUCCESS_PATH_BY_KIND[kind][-1]
    finally:
        catalog.close()
        recovery_connection.close()


def _environment(
    tmp_path: Path,
) -> tuple[sqlite3.Connection, sqlite3.Connection, Path]:
    root = tmp_path / "target"
    (root / ".mediasync").mkdir(parents=True)
    (root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(
            {
                "endpoint_id": "target-a",
                "owner_installation_id": "owner-a",
                "ownership_epoch": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.sqlite"
    recovery_path = tmp_path / "recovery.sqlite"
    catalog = sqlite3.connect(catalog_path)
    recovery = sqlite3.connect(recovery_path)
    apply_sqlite_connection_policy(catalog, catalog_critical_writer_policy(catalog_path))
    apply_sqlite_connection_policy(recovery, recovery_writer_policy(recovery_path))
    apply_sqlite_migrations(catalog, catalog_migration_plan())
    apply_sqlite_migrations(recovery, recovery_migration_plan())
    return catalog, recovery, root


def _record_at_intent(
    store: SqliteDirectoryRecoveryStore,
    kind: DirectoryRecoveryKind,
) -> DirectoryRecoveryOperation:
    operation = store.record_directory_recovery_operation(
        planned_directory_recovery_operation(
            recovery_id=f"directory-{kind.value.lower()}-restart",
            operation_id="operation-a",
            run_id="run-a",
            run_target_id="run-a-target-0000",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            owner_installation_id="owner-a",
            ownership_epoch=1,
            kind=kind,
            final_relative_path="Parent/Folder",
            expected_precondition_json=(
                '{"kind":"ABSENT"}'
                if kind is DirectoryRecoveryKind.CREATE
                else '{"entry_count":0,"kind":"DIRECTORY_EMPTY"}'
            ),
        ),
        process_instance_id="host-old",
    )
    path = SUCCESS_PATH_BY_KIND[kind]
    for next_state in path[1:3]:
        advanced = store.transition_directory_recovery_operation(
            DirectoryRecoveryTransition(
                recovery_id=operation.recovery_id,
                expected_state=operation.state,
                next_state=next_state,
                process_instance_id="host-old",
                payload={"crash_window": next_state.value},
            )
        )
        assert advanced is not None
        operation = advanced
    return operation


def _generic_operation(phase: RecoveryOperationPhase) -> RecoveryOperation:
    return RecoveryOperation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="operation-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        phase=phase,
        final_relative_path="Parent/Folder",
        target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        operation_kind=RecoveryOperationKind.COPY_NEW,
        job_id="job-a",
        job_revision_id="job-rev-a",
        retention_policy=THIRTY_DAY_RETENTION_POLICY,
        quarantine_object_id="operation-a",
        expected_target_fingerprint_json=(
            '{"entry_count":0,"kind":"DIRECTORY_EMPTY"}'
        ),
    )


def _write_quarantine_object(root: Path, operation: RecoveryOperation) -> None:
    object_root = root / ".mediasync" / "objects" / "quarantine"
    object_root.mkdir(parents=True)
    (object_root / "operation-a.payload").mkdir()
    manifest = create_quarantine_object_manifest(
        version_object_id="operation-a",
        operation_id=operation.operation_id,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        job_id="job-a",
        job_revision_id="job-rev-a",
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        endpoint_generation=1,
        owner_installation_id=operation.owner_installation_id,
        ownership_epoch=operation.ownership_epoch,
        final_relative_path=operation.final_relative_path,
        fingerprint={"entry_count": 0, "kind": "DIRECTORY_EMPTY"},
        created_utc="2026-08-09T10:00:00.000Z",
        retention_policy=THIRTY_DAY_RETENTION_POLICY,
    )
    (object_root / "operation-a.manifest.json").write_text(
        manifest.canonical_json,
        encoding="utf-8",
    )


def _observer(
    *,
    root: Path,
    catalog: sqlite3.Connection,
    generic: "_GenericLookup",
) -> LocalDirectoryRecoveryObservationAdapter:
    return LocalDirectoryRecoveryObservationAdapter(
        root_resolver=_RootResolver(root),
        recovery_operations=generic,
        metadata_catalog=SqliteDirectoryMetadataCatalogStore(catalog),
    )


class _RootResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve_endpoint_root_descriptor(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> EndpointRootDescriptor:
        assert resource_key == "endpoint:target-a"
        assert endpoint_id == "target-a"
        assert endpoint_revision_id == "target-rev-a"
        return EndpointRootDescriptor(
            root=self._root,
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
        )

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path:
        return self.resolve_endpoint_root_descriptor(
            resource_key=resource_key,
            endpoint_id=endpoint_id,
            endpoint_revision_id=endpoint_revision_id,
        ).root


class _GenericLookup:
    def __init__(self, operation: RecoveryOperation) -> None:
        self.operation = operation

    def load_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> RecoveryOperation | None:
        if (
            run_id == self.operation.run_id
            and operation_id == self.operation.operation_id
        ):
            return self.operation
        return None
