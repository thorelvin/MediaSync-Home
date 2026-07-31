from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore, SqlitePlanStoreError
from mediasync_home.application.plans import (
    PlanDependency,
    PlanEndpoint,
    PlanEndpointPageQuery,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationPageQuery,
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


def test_sqlite_plan_store_joins_and_rolls_back_with_outer_transaction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        connection.commit()
        store = SqlitePlanStore(connection)

        connection.execute("BEGIN IMMEDIATE")
        store.save_sealed_plan(_sealed_plan())
        connection.execute("ROLLBACK")

        assert _row_count(connection, "plans") == 0
        assert _row_count(connection, "plan_seal_details") == 0


def test_sqlite_plan_store_pages_operations_by_stable_order(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqlitePlanStore(connection)
        store.save_sealed_plan(_operation_page_plan())

        first_page = store.page_plan_operations(PlanOperationPageQuery(plan_id="plan-page", limit=2))

        assert [operation.operation_id for operation in first_page.operations] == ["op-a", "op-b"]
        assert [operation.sequence_no for operation in first_page.operations] == [30, 10]
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert first_page.next_cursor.execution_phase == 10
        assert first_page.next_cursor.stable_order_key == "010:Pictures/A.jpg"
        assert first_page.next_cursor.operation_id == "op-b"

        second_page = store.page_plan_operations(
            PlanOperationPageQuery(
                plan_id="plan-page",
                limit=2,
                after=first_page.next_cursor,
            )
        )

        assert [operation.operation_id for operation in second_page.operations] == ["op-c"]
        assert second_page.has_more is False
        assert second_page.next_cursor is None


def test_sqlite_plan_store_pages_endpoints_by_role_and_target_order(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        store = SqlitePlanStore(connection)
        store.save_sealed_plan(_endpoint_page_plan())

        first_page = store.page_plan_endpoints(PlanEndpointPageQuery(plan_id="plan-endpoints", limit=1))

        assert [endpoint.endpoint_id for endpoint in first_page.endpoints] == ["source-a"]
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert first_page.next_cursor.role is PlanEndpointRole.SOURCE
        assert first_page.next_cursor.target_ordinal is None
        assert first_page.next_cursor.endpoint_id == "source-a"

        second_page = store.page_plan_endpoints(
            PlanEndpointPageQuery(
                plan_id="plan-endpoints",
                limit=2,
                after=first_page.next_cursor,
            )
        )

        assert [endpoint.endpoint_id for endpoint in second_page.endpoints] == ["target-a"]
        assert [endpoint.snapshot_id for endpoint in second_page.endpoints] == ["target-snapshot-a"]
        assert second_page.has_more is False
        assert second_page.next_cursor is None


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
    insert_default_filter_set_version(
        connection,
        job_id="job-a",
        filter_set_id="filter-a",
    )
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
    connection.execute("INSERT INTO endpoints (id) VALUES ('source-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('source-a', 'source-rev-a', 'Pictures', 'file:///C:/Users/Ada/Pictures')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'source-a', 'source-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('source-snapshot-a', 'analysis-a', 'source-a', 'source-rev-a')
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


def _operation_page_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-page",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        operations=(
            _read_model_operation(
                operation_id="op-b",
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:Pictures/A.jpg",
                target_relative_path="Pictures/B.jpg",
            ),
            _read_model_operation(
                operation_id="op-c",
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:Pictures/C.jpg",
                target_relative_path="Pictures/C.jpg",
            ),
            _read_model_operation(
                operation_id="op-a",
                sequence_no=30,
                execution_phase=10,
                stable_order_key="010:Pictures/A.jpg",
                target_relative_path="Pictures/A.jpg",
            ),
        ),
    )


def _endpoint_page_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-endpoints",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(), _source_endpoint()),
        operations=(
            _read_model_operation(
                operation_id="op-a",
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:Pictures/A.jpg",
                target_relative_path="Pictures/A.jpg",
            ),
        ),
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
        endpoint_generation=1,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )


def _source_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="source-a",
        endpoint_revision_id="source-rev-a",
        snapshot_id="source-snapshot-a",
        role=PlanEndpointRole.SOURCE,
        capabilities_hash="capabilities-source",
        root_case_context_hash="case-source",
        endpoint_generation=1,
    )


def _read_model_operation(
    *,
    operation_id: str,
    sequence_no: int,
    execution_phase: int,
    stable_order_key: str,
    target_relative_path: str,
) -> PlanOperation:
    return PlanOperation(
        operation_id=operation_id,
        operation_type=PlanOperationType.SKIP_IDENTICAL,
        sequence_no=sequence_no,
        execution_phase=execution_phase,
        stable_order_key=stable_order_key,
        target_precondition_kind=TargetPreconditionKind.NONE,
        target_relative_path=target_relative_path,
        reason_code="SKIP_IDENTICAL",
        risk_level=PlanRiskLevel.LOW,
    )


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
