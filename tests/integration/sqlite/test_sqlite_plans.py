from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore, SqlitePlanStoreError
from mediasync_home.application.plans import (
    PlanDependency,
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
    verify_plan_checksum,
)


def test_sqlite_plan_store_persists_sealed_plan(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqlitePlanStore(connection)
        plan = _sealed_plan()

        store.save_sealed_plan(plan)
        loaded = store.load_sealed_plan("plan-a")

        assert loaded is not None
        assert loaded == plan
        assert verify_plan_checksum(loaded) is True
        assert _row_count(connection, "plans") == 1
        assert _row_count(connection, "planned_operations") == 2
        assert _row_count(connection, "operation_dependencies") == 1
        assert _row_count(connection, "plan_endpoints") == 1
        assert _row_count(connection, "plan_seal_details") == 1
        assert _row_count(connection, "plan_operation_seal_details") == 2


def test_sqlite_plan_store_rejects_duplicate_plan_id(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqlitePlanStore(connection)
        plan = _sealed_plan()
        store.save_sealed_plan(plan)

        with pytest.raises(SqlitePlanStoreError, match="SEALED_PLAN_PERSISTENCE_FAILED"):
            store.save_sealed_plan(plan)


def test_sqlite_plan_store_requires_analysis_parent(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqlitePlanStore(connection)

        with pytest.raises(SqlitePlanStoreError, match="SEALED_PLAN_PERSISTENCE_FAILED"):
            store.save_sealed_plan(_sealed_plan())


def test_sqlite_sealed_plan_blocks_in_place_mutation(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqlitePlanStore(connection)
        store.save_sealed_plan(_sealed_plan())

        with pytest.raises(sqlite3.IntegrityError, match="PLAN_SEAL_IMMUTABLE"):
            connection.execute(
                """
                UPDATE planned_operations
                SET operation_type = 'CREATE_DIRECTORY'
                WHERE plan_id = 'plan-a' AND id = 'op-copy'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="PLAN_SEAL_IMMUTABLE"):
            connection.execute(
                """
                UPDATE plan_endpoints
                SET required_ownership_epoch = 2
                WHERE plan_id = 'plan-a' AND endpoint_id = 'target-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="PLAN_SEAL_IMMUTABLE"):
            connection.execute(
                """
                INSERT INTO planned_operations (plan_id, id, operation_type)
                    VALUES ('plan-a', 'op-extra', 'SKIP_IDENTICAL')
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="PLAN_SEAL_IMMUTABLE"):
            connection.execute(
                """
                UPDATE plan_seal_details
                SET plan_checksum = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                WHERE plan_id = 'plan-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="PLAN_SEAL_IMMUTABLE"):
            connection.execute(
                """
                INSERT INTO plan_operation_seal_details (
                    plan_id,
                    operation_id,
                    sequence_no,
                    execution_phase,
                    stable_order_key,
                    target_precondition_kind,
                    reason_code,
                    risk_level,
                    target_relative_path,
                    planned_bytes
                )
                VALUES (
                    'plan-a',
                    'op-copy',
                    99,
                    99,
                    '099:extra',
                    'NONE',
                    'EXTRA',
                    'LOW',
                    NULL,
                    0
                )
                """
            )


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_plan_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
        """
    )
    connection.execute("INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'job-rev-a')")
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('target-a', 'target-rev-a', 'USB', 'file:///E:/Backup')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('target-snapshot-a', 'analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.commit()


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-copy",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-skip",
                operation_type=PlanOperationType.SKIP_IDENTICAL,
                sequence_no=20,
                execution_phase=70,
                stable_order_key="070:Pictures/B.jpg",
                target_precondition_kind=TargetPreconditionKind.NONE,
                target_relative_path="Pictures/B.jpg",
                reason_code="SKIP_IDENTICAL",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
        dependencies=(PlanDependency(before_operation_id="op-copy", after_operation_id="op-skip"),),
    )


def _target_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        snapshot_id="target-snapshot-a",
        role=PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
