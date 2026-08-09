from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.directory_recovery import (
    SqliteDirectoryRecoveryStore,
    SqliteDirectoryRecoveryStoreError,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.recovery_operations import (
    SqliteRecoveryOperationStore,
)
from mediasync_home.application.directory_recovery import (
    CONFLICT_STATE_BY_KIND,
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryTransition,
    planned_directory_recovery_operation,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperationKind,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


@pytest.mark.parametrize("kind", list(DirectoryRecoveryKind))
def test_sqlite_directory_recovery_persists_hash_chained_success_lifecycle(
    tmp_path: Path,
    kind: DirectoryRecoveryKind,
) -> None:
    connection = _prepared_connection(tmp_path)
    try:
        operation = _record_directory_operation(connection, kind)
        store = SqliteDirectoryRecoveryStore(connection)
        recorded = store.record_directory_recovery_operation(
            operation,
            process_instance_id="host-a",
            payload={"event": "planned"},
        )
        assert recorded.event_sequence == 1
        assert recorded.event_hash is not None

        for next_state in SUCCESS_PATH_BY_KIND[kind][1:]:
            transition = DirectoryRecoveryTransition(
                recovery_id=recorded.recovery_id,
                expected_state=recorded.state,
                next_state=next_state,
                process_instance_id="host-a",
                payload={"evidence": next_state.value},
                managed_object_id=(
                    f"managed-{kind.value.lower()}"
                    if "PRESERVED" in next_state.value
                    else None
                ),
            )
            updated = store.transition_directory_recovery_operation(transition)
            assert updated is not None
            recorded = updated

        assert recorded.state is SUCCESS_PATH_BY_KIND[kind][-1]
        assert store.list_unresolved_directory_recovery_operations(limit=10) == ()
        rows = connection.execute(
            """
            SELECT event_sequence, previous_event_hash, event_hash
            FROM directory_recovery_events
            WHERE recovery_id = ?
            ORDER BY event_sequence
            """,
            (recorded.recovery_id,),
        ).fetchall()
        assert [int(row[0]) for row in rows] == list(
            range(1, len(SUCCESS_PATH_BY_KIND[kind]) + 1)
        )
        assert rows[0][1] is None
        assert all(rows[index][1] == rows[index - 1][2] for index in range(1, len(rows)))

        replayed = store.transition_directory_recovery_operation(transition)
        assert replayed == recorded
        assert store.record_directory_recovery_operation(
            operation,
            process_instance_id="host-a",
        ) == recorded
    finally:
        connection.close()


def test_sqlite_directory_recovery_conflict_is_terminal_and_requires_evidence(
    tmp_path: Path,
) -> None:
    connection = _prepared_connection(tmp_path)
    try:
        operation = _record_directory_operation(connection, DirectoryRecoveryKind.CREATE)
        store = SqliteDirectoryRecoveryStore(connection)
        recorded = store.record_directory_recovery_operation(
            operation,
            process_instance_id="host-a",
        )
        conflict = store.transition_directory_recovery_operation(
            DirectoryRecoveryTransition(
                recovery_id=recorded.recovery_id,
                expected_state=recorded.state,
                next_state=CONFLICT_STATE_BY_KIND[recorded.kind],
                process_instance_id="host-a",
                payload={"observed_type": "file"},
                last_error_code="DIRECTORY_TYPE_CONFLICT",
            )
        )
        assert conflict is not None
        assert conflict.last_error_code == "DIRECTORY_TYPE_CONFLICT"
        assert store.list_unresolved_directory_recovery_operations(limit=10) == ()
        assert store.list_conflicted_directory_recovery_operations(limit=10) == (
            conflict,
        )

        with pytest.raises(sqlite3.IntegrityError, match="STATE_NOT_MONOTONE"):
            connection.execute(
                """
                UPDATE directory_recovery_operations
                SET state = 'DIRECTORY_PARENT_VALIDATED'
                WHERE recovery_id = ?
                """,
                (recorded.recovery_id,),
            )
    finally:
        connection.close()


def test_sqlite_directory_recovery_protects_bindings_and_events(
    tmp_path: Path,
) -> None:
    connection = _prepared_connection(tmp_path)
    try:
        operation = _record_directory_operation(connection, DirectoryRecoveryKind.CREATE)
        store = SqliteDirectoryRecoveryStore(connection)
        recorded = store.record_directory_recovery_operation(
            operation,
            process_instance_id="host-a",
        )
        with pytest.raises(sqlite3.IntegrityError, match="BINDING_IMMUTABLE"):
            connection.execute(
                """
                UPDATE directory_recovery_operations
                SET final_relative_path = 'Other'
                WHERE recovery_id = ?
                """,
                (recorded.recovery_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="EVENT_IMMUTABLE"):
            connection.execute(
                """
                DELETE FROM directory_recovery_events
                WHERE recovery_id = ?
                """,
                (recorded.recovery_id,),
            )
        with pytest.raises(
            SqliteDirectoryRecoveryStoreError,
            match="IDEMPOTENCY_CONFLICT",
        ):
            store.record_directory_recovery_operation(
                planned_directory_recovery_operation(
                    recovery_id=operation.recovery_id,
                    operation_id=operation.operation_id,
                    run_id=operation.run_id,
                    run_target_id=operation.run_target_id,
                    target_endpoint_id=operation.target_endpoint_id,
                    target_endpoint_revision_id=operation.target_endpoint_revision_id,
                    owner_installation_id=operation.owner_installation_id,
                    ownership_epoch=operation.ownership_epoch,
                    kind=operation.kind,
                    final_relative_path="Different",
                ),
                process_instance_id="host-a",
            )
    finally:
        connection.close()


def _prepared_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    )
    return connection


def _record_directory_operation(
    connection: sqlite3.Connection,
    kind: DirectoryRecoveryKind,
):
    suffix = kind.value.lower()
    generic = planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id=f"operation-{suffix}",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path=f"Photos/{suffix}",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        operation_kind=RecoveryOperationKind.CREATE_DIRECTORY,
    )
    SqliteRecoveryOperationStore(connection).record_planned_operation(
        generic,
        process_instance_id="host-a",
    )
    return planned_directory_recovery_operation(
        recovery_id=f"directory-{suffix}",
        operation_id=generic.operation_id,
        run_id=generic.run_id,
        run_target_id=generic.run_target_id,
        target_endpoint_id=generic.target_endpoint_id,
        target_endpoint_revision_id=generic.target_endpoint_revision_id,
        owner_installation_id=generic.owner_installation_id,
        ownership_epoch=generic.ownership_epoch,
        kind=kind,
        final_relative_path=generic.final_relative_path,
        expected_precondition_json='{"object_type":"directory"}',
        desired_metadata_json='{"modified_ns":123}',
    )
