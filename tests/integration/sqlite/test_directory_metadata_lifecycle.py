from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from mediasync_home.adapters.directory_metadata import LocalDirectoryMetadataAdapter
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
from mediasync_home.application.directory_metadata import (
    apply_directory_metadata_lifecycle,
    canonical_directory_metadata,
    directory_metadata_catalog_record,
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
from mediasync_home.application.recovery_operations import RecoveryOperation
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit
from mediasync_home.generated.contract_types import DirectoryMetadataState


def test_directory_metadata_lifecycle_applies_verifies_catalogs_and_replays(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target"
    directory = target_root / "Parent" / "Child"
    directory.mkdir(parents=True)
    (target_root / ".mediasync").mkdir()
    (target_root / ".mediasync" / "endpoint.json").write_text(
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
    desired_modified_ns = directory.stat().st_mtime_ns - 5_000_000_000
    desired = canonical_directory_metadata(modified_ns=desired_modified_ns)
    catalog_connection, recovery_connection = _connections(tmp_path)
    try:
        recovery = SqliteDirectoryRecoveryStore(recovery_connection)
        operation = recovery.record_directory_recovery_operation(
            planned_directory_recovery_operation(
                recovery_id="directory-metadata-a",
                operation_id="metadata-a",
                run_id="run-a",
                run_target_id="run-a-target-0000",
                target_endpoint_id="target-a",
                target_endpoint_revision_id="target-rev-a",
                owner_installation_id="owner-a",
                ownership_epoch=1,
                kind=DirectoryRecoveryKind.METADATA,
                final_relative_path="Parent/Child",
                expected_precondition_json='{"object_type":"directory"}',
                desired_metadata_json=desired,
            ),
            process_instance_id="host-a",
        )
        mutation = LocalDirectoryMetadataAdapter(
            root_resolver=_RootResolver(target_root),
            permit_validator=_PermitValidator(),
        )
        catalog = SqliteDirectoryMetadataCatalogStore(catalog_connection)
        permit = _permit()

        completed = apply_directory_metadata_lifecycle(
            permit=permit,
            operation=operation,
            directory_recovery=recovery,
            children=_ChildrenTerminal(),
            mutation=mutation,
            catalog=catalog,
            process_instance_id="host-a",
        )
        replayed = apply_directory_metadata_lifecycle(
            permit=permit,
            operation=operation,
            directory_recovery=recovery,
            children=_ChildrenTerminal(),
            mutation=mutation,
            catalog=catalog,
            process_instance_id="host-a",
        )

        assert completed.state is DirectoryMetadataState.DIRECTORY_CATALOG_RECORDED
        assert replayed == completed
        assert directory.stat().st_mtime_ns == desired_modified_ns
        record = catalog.load_directory_metadata(operation.recovery_id)
        assert record is not None
        assert record.desired_metadata_json == desired
        assert record.applied_metadata_json == desired
        assert recovery_connection.execute(
            """
            SELECT count(*) FROM directory_recovery_events WHERE recovery_id = ?
            """,
            (operation.recovery_id,),
        ).fetchone() == (7,)
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_directory_metadata_waits_for_children_without_mutating(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target"
    directory = target_root / "Parent"
    directory.mkdir(parents=True)
    before = directory.stat().st_mtime_ns
    catalog_connection, recovery_connection = _connections(tmp_path)
    try:
        recovery = SqliteDirectoryRecoveryStore(recovery_connection)
        operation = recovery.record_directory_recovery_operation(
            planned_directory_recovery_operation(
                recovery_id="directory-metadata-waiting",
                operation_id="metadata-waiting",
                run_id="run-a",
                run_target_id="run-a-target-0000",
                target_endpoint_id="target-a",
                target_endpoint_revision_id="target-rev-a",
                owner_installation_id="owner-a",
                ownership_epoch=1,
                kind=DirectoryRecoveryKind.METADATA,
                final_relative_path="Parent",
                desired_metadata_json=canonical_directory_metadata(
                    modified_ns=max(0, before - 1_000_000_000)
                ),
            ),
            process_instance_id="host-a",
        )

        try:
            apply_directory_metadata_lifecycle(
                permit=_permit(),
                operation=operation,
                directory_recovery=recovery,
                children=_ChildrenTerminal(terminal=False),
                mutation=LocalDirectoryMetadataAdapter(
                    root_resolver=_RootResolver(target_root),
                    permit_validator=_PermitValidator(),
                ),
                catalog=SqliteDirectoryMetadataCatalogStore(catalog_connection),
                process_instance_id="host-a",
            )
        except RuntimeError as exc:
            assert str(exc) == "DIRECTORY_METADATA_CHILDREN_NOT_TERMINAL"
        else:
            raise AssertionError("nonterminal children must block directory metadata")

        assert directory.stat().st_mtime_ns == before
        assert recovery.load_directory_recovery_operation(operation.recovery_id) == operation
    finally:
        catalog_connection.close()
        recovery_connection.close()


def test_directory_metadata_startup_reconciles_filesystem_and_catalog_crash_windows(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target"
    directory = target_root / "Parent"
    directory.mkdir(parents=True)
    (target_root / ".mediasync").mkdir()
    (target_root / ".mediasync" / "endpoint.json").write_text(
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
    desired_modified_ns = directory.stat().st_mtime_ns - 5_000_000_000
    desired = canonical_directory_metadata(modified_ns=desired_modified_ns)
    catalog_connection, recovery_connection = _connections(tmp_path)
    try:
        recovery = SqliteDirectoryRecoveryStore(recovery_connection)
        operation = recovery.record_directory_recovery_operation(
            planned_directory_recovery_operation(
                recovery_id="directory-metadata-restart",
                operation_id="metadata-restart",
                run_id="run-a",
                run_target_id="run-a-target-0000",
                target_endpoint_id="target-a",
                target_endpoint_revision_id="target-rev-a",
                owner_installation_id="owner-a",
                ownership_epoch=1,
                kind=DirectoryRecoveryKind.METADATA,
                final_relative_path="Parent",
                desired_metadata_json=desired,
            ),
            process_instance_id="host-old",
        )
        path = SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.METADATA]
        for next_state in path[1:4]:
            advanced = recovery.transition_directory_recovery_operation(
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
        current = directory.stat()
        os.utime(directory, ns=(current.st_atime_ns, desired_modified_ns))

        catalog = SqliteDirectoryMetadataCatalogStore(catalog_connection)
        observer = LocalDirectoryRecoveryObservationAdapter(
            root_resolver=_DescriptorRootResolver(target_root),
            recovery_operations=_MissingRecoveryOperations(),
            metadata_catalog=catalog,
        )
        filesystem_report = reconcile_directory_recovery_after_startup(
            store=recovery,
            observer=observer,
            process_instance_id="host-new",
        )

        assert filesystem_report.mutation_safe is True
        verified = recovery.load_directory_recovery_operation(operation.recovery_id)
        assert verified is not None
        assert verified.state is DirectoryMetadataState.METADATA_VERIFIED

        catalog.record_directory_metadata(
            directory_metadata_catalog_record(verified, desired)
        )
        catalog_report = reconcile_directory_recovery_after_startup(
            store=recovery,
            observer=observer,
            process_instance_id="host-new",
        )

        assert catalog_report.mutation_safe is True
        completed = recovery.load_directory_recovery_operation(operation.recovery_id)
        assert completed is not None
        assert completed.state is DirectoryMetadataState.DIRECTORY_CATALOG_RECORDED
    finally:
        catalog_connection.close()
        recovery_connection.close()


def _connections(tmp_path: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    catalog_path = tmp_path / "catalog.sqlite"
    recovery_path = tmp_path / "recovery.sqlite"
    catalog = sqlite3.connect(catalog_path)
    recovery = sqlite3.connect(recovery_path)
    apply_sqlite_connection_policy(catalog, catalog_critical_writer_policy(catalog_path))
    apply_sqlite_connection_policy(recovery, recovery_writer_policy(recovery_path))
    apply_sqlite_migrations(catalog, catalog_migration_plan())
    apply_sqlite_migrations(recovery, recovery_migration_plan())
    return catalog, recovery


class _RootResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path:
        assert resource_key == "endpoint:target-a"
        assert endpoint_id == "target-a"
        assert endpoint_revision_id == "target-rev-a"
        return self._root


class _DescriptorRootResolver(_RootResolver):
    def resolve_endpoint_root_descriptor(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> EndpointRootDescriptor:
        return EndpointRootDescriptor(
            root=self.resolve_endpoint_root(
                resource_key=resource_key,
                endpoint_id=endpoint_id,
                endpoint_revision_id=endpoint_revision_id,
            ),
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
        )


class _MissingRecoveryOperations:
    def load_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> RecoveryOperation | None:
        del run_id, operation_id
        return None


class _PermitValidator:
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None:
        assert permit.lease_id == "lease-a"


class _ChildrenTerminal:
    def __init__(self, *, terminal: bool = True) -> None:
        self._terminal = terminal

    def directory_children_are_terminal(
        self,
        operation: DirectoryRecoveryOperation,
    ) -> bool:
        del operation
        return self._terminal


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
