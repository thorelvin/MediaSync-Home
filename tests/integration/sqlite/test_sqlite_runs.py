from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore, SqliteRunStoreError
from mediasync_home.application.command_receipts import CommandReceipt
from mediasync_home.application.plans import (
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.runs import (
    RunIdFactory,
    RunIds,
    RunCommandName,
    RunState,
    StartedRun,
    parse_start_run_command,
    start_run_from_sealed_plan,
)
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService


class FixedRunIdFactory(RunIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_run_ids(self) -> RunIds:
        self.calls += 1
        return RunIds(run_id="run-a", logical_run_group_id="run-group-a")


def test_sqlite_run_store_persists_started_run_from_sealed_plan(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        plans.save_sealed_plan(plan)

        outcome = start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )

        loaded = runs.load_started_run("run-a")
        assert outcome.created is True
        assert loaded == outcome.run
        assert loaded is not None
        assert loaded.state is RunState.QUEUED
        assert loaded.plan_checksum == plan.plan_checksum
        assert loaded.planned_operations == 1
        assert loaded.planned_bytes == 128
        assert _row_count(connection, "runs") == 1
        assert _row_count(connection, "run_targets") == 0


def test_sqlite_run_store_replays_run_idempotency_key(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        ids = FixedRunIdFactory()
        plan = _sealed_plan()
        plans.save_sealed_plan(plan)
        command = parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        )

        first = start_run_from_sealed_plan(command=command, plans=plans, runs=runs, id_factory=ids)
        second = start_run_from_sealed_plan(command=command, plans=plans, runs=runs, id_factory=ids)

        assert first.created is True
        assert second.created is False
        assert second.idempotent_replay is True
        assert second.run == first.run
        assert _row_count(connection, "runs") == 1
        assert ids.calls == 1


def test_sqlite_run_store_requires_sealed_plan_binding(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        run = _started_run_without_plan()

        with pytest.raises(SqliteRunStoreError, match="RUN_PERSISTENCE_FAILED"):
            SqliteRunStore(connection).save_started_run(run)


def test_sqlite_run_store_requires_command_receipt_binding(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        plans = SqlitePlanStore(connection)
        plan = _sealed_plan()
        plans.save_sealed_plan(plan)
        command = parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        )

        with pytest.raises(SqliteRunStoreError, match="RUN_PERSISTENCE_FAILED"):
            start_run_from_sealed_plan(
                command=command,
                plans=plans,
                runs=SqliteRunStore(connection),
                id_factory=FixedRunIdFactory(),
            )


def test_sqlite_enabled_start_run_ipc_persists_run_and_success_receipt(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        plan_store = SqlitePlanStore(connection)
        run_store = SqliteRunStore(connection)
        receipt_store = SqliteCommandReceiptStore(connection)
        id_factory = FixedRunIdFactory()
        plan = _sealed_plan()
        plan_store.save_sealed_plan(plan)
        service = EngineHostIpcService(
            ClientAuthorizationPolicy(
                expected_user_sid_hash="same-user",
                expected_session_id=42,
            ),
            status=replace(
                startup_status(ProcessRole.ENGINE_HOST),
                mutations_enabled=True,
                scope="0B_LOCAL_MUTATION_PREVIEW",
            ),
            plan_store=plan_store,
            run_store=run_store,
            run_id_factory=id_factory,
            command_receipt_store=receipt_store,
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-run-ipc-test",
            ),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        ipc_client.connect()

        response = ipc_client.submit_command(
            RunCommandName.START_RUN.value,
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
        )

        loaded_run = run_store.load_started_run("run-a")
        loaded_receipt = receipt_store.load_command_receipt("66666666-6666-4666-8666-666666666666")
        assert response.status is IpcStatus.ACCEPTED
        assert response.reason is None
        assert response.payload["run"]["run_id"] == "run-a"
        assert response.payload["receipt"]["state"] == "SUCCEEDED"
        assert loaded_run is not None
        assert loaded_run.state is RunState.QUEUED
        assert loaded_receipt is not None
        assert loaded_receipt.result_entity_type == "run"
        assert loaded_receipt.result_entity_id == "run-a"
        assert _row_count(connection, "runs") == 1
        assert _row_count(connection, "command_receipts") == 1
        assert id_factory.calls == 1


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
    connection.commit()


def _insert_receipt(connection: sqlite3.Connection) -> None:
    receipt = CommandReceipt(
        request_id="request-a",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key="idempotency-a",
        command_name="START_RUN",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
    )
    connection.execute(
        """
        INSERT INTO command_receipts (
            idempotency_key,
            request_id,
            client_instance_id,
            principal_fingerprint,
            command_name,
            payload_hash,
            protocol_version,
            schema_version,
            state,
            expected_entity_revision,
            payload_hash_scope,
            payload_canonicalization_algorithm,
            payload_hash_algorithm,
            result_entity_type,
            result_entity_id,
            rejection_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt.idempotency_key,
            receipt.request_id,
            receipt.client_instance_id,
            receipt.principal_fingerprint,
            receipt.command_name,
            receipt.payload_hash,
            receipt.protocol_version,
            receipt.schema_version,
            receipt.state.value,
            receipt.expected_entity_revision,
            receipt.payload_hash_scope,
            receipt.payload_canonicalization_algorithm,
            receipt.payload_hash_algorithm,
            receipt.result_entity_type,
            receipt.result_entity_id,
            receipt.rejection_reason,
        ),
    )
    connection.commit()


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
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
        ),
    )


def _started_run_without_plan() -> StartedRun:
    plan = _sealed_plan()
    outcome = start_run_from_sealed_plan(
        command=parse_start_run_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        ),
        plans=_SinglePlanStore(plan),
        runs=_MemoryRunStore(),
        id_factory=FixedRunIdFactory(),
    )
    assert outcome.run is not None
    return outcome.run


class _SinglePlanStore:
    def __init__(self, plan: SealedPlan) -> None:
        self._plan = plan

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self._plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        if plan_id == self._plan.plan_id:
            return self._plan
        return None


class _MemoryRunStore:
    def __init__(self) -> None:
        self.run: StartedRun | None = None

    def save_started_run(self, run: StartedRun) -> None:
        self.run = run

    def load_started_run(self, run_id: str) -> StartedRun | None:
        if self.run is not None and self.run.run_id == run_id:
            return self.run
        return None

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
        if self.run is not None and self.run.idempotency_key == idempotency_key:
            return self.run
        return None


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
