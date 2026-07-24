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
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore, SqliteRunStoreError
from mediasync_home.adapters.sqlite.schedules import SqliteScheduleStore
from mediasync_home.adapters.sqlite.transactions import SqliteImmediateTransactionRunner
from mediasync_home.adapters.sqlite.trigger_occurrences import SqliteTriggerOccurrenceStore
from mediasync_home.application.command_receipts import CommandReceipt
from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.run_executor import (
    HeldRunTargetLeaseRegistry,
    execute_one_executing_run_target_lease_reacquire_step,
    execute_one_run_target_execution_start_step,
    execute_one_run_target_preflight_step,
)
from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    RunIdFactory,
    RunIds,
    RunCommandName,
    RunState,
    RunTargetState,
    StartedRun,
    acquire_run_target_lease,
    begin_next_run_target_preflight,
    complete_run_target_success,
    parse_start_run_command,
    start_run_from_sealed_plan,
)
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.trigger_occurrences import (
    TriggerCommandName,
    TriggerDeliveryContext,
    TriggerKind,
    TriggerOccurrenceState,
    build_enqueue_trigger_occurrence_payload,
    payload_hash,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit
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


class FixedLeaseAuthority(EndpointLeaseAuthority):
    def __init__(self, lease: FixedLiveLease) -> None:
        self.lease = lease
        self.requests: list[EndpointLeaseRequest] = []

    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt:
        self.requests.append(request)
        return EndpointLeaseAttempt(
            acquired=True,
            lease=self.lease,
            validation_codes=(),
            next_action="Lease acquired.",
        )


class FixedLiveLease:
    owner_installation_id = "owner-a"
    ownership_epoch = 1

    def __init__(self, lease_id: str = "lease-a", *, fencing_token: int = 42) -> None:
        self.lease_id = lease_id
        self.fencing_token = fencing_token
        self.released = False

    def issue_mutation_permit(self) -> MutationPermit:
        return _issue_mutation_permit(
            lease_id=self.lease_id,
            resource_key="endpoint:target-a",
            owner_installation_id=self.owner_installation_id,
            ownership_epoch=self.ownership_epoch,
            fencing_token=self.fencing_token,
            run_id="run-a",
            run_target_id="run-a-target-0000",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
        )

    def release(self) -> None:
        self.released = True


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
        assert len(loaded.targets) == 1
        assert loaded.targets[0].endpoint_id == "target-a"
        assert loaded.targets[0].endpoint_revision_id == "target-rev-a"
        assert loaded.targets[0].required_owner_installation_id == "owner-a"
        assert loaded.targets[0].required_ownership_epoch == 1
        assert loaded.targets[0].lease_resource_key == "endpoint:target-a"
        assert loaded.targets[0].planned_operations == 1
        assert loaded.targets[0].planned_bytes == 128
        assert _row_count(connection, "runs") == 1
        assert _row_count(connection, "run_targets") == 1


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


def test_sqlite_run_store_begins_run_target_preflight(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )

        outcome = begin_next_run_target_preflight(run_id="run-a", runs=runs)

        loaded = runs.load_started_run("run-a")
        assert outcome.claimed is True
        assert outcome.validation_codes == ()
        assert outcome.target is not None
        assert outcome.target.state is RunTargetState.ACQUIRING_LEASE
        assert loaded is not None
        assert loaded.state is RunState.PREFLIGHT
        assert loaded.targets[0].state is RunTargetState.ACQUIRING_LEASE
        assert _run_target_started_utc(connection, "run-a-target-0000") is not None

        repeated = begin_next_run_target_preflight(run_id="run-a", runs=runs)

        assert repeated.claimed is False
        assert repeated.validation_codes == ("RUN_HAS_NO_PENDING_TARGETS",)


def test_sqlite_run_target_preflight_requires_lease_key_without_mutating(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        connection.execute(
            "UPDATE run_targets SET lease_resource_key = NULL WHERE id = 'run-a-target-0000'"
        )

        outcome = begin_next_run_target_preflight(run_id="run-a", runs=runs)

        loaded = runs.load_started_run("run-a")
        assert outcome.claimed is False
        assert outcome.run_target_id == "run-a-target-0000"
        assert outcome.validation_codes == ("RUN_TARGET_REQUIRES_LEASE_RESOURCE_KEY",)
        assert loaded is not None
        assert loaded.state is RunState.QUEUED
        assert loaded.targets[0].state is RunTargetState.PENDING
        assert _run_target_started_utc(connection, "run-a-target-0000") is None


def test_sqlite_run_store_records_acquired_run_target_lease(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        lease = FixedLiveLease()
        leases = FixedLeaseAuthority(lease)
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        begin_next_run_target_preflight(run_id="run-a", runs=runs)

        outcome = acquire_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            runs=runs,
            leases=leases,
        )

        loaded = runs.load_started_run("run-a")
        assert outcome.acquired is True
        assert outcome.validation_codes == ()
        assert outcome.lease is lease
        assert outcome.target is not None
        assert outcome.target.state is RunTargetState.REVALIDATING
        assert outcome.target.last_lease_id == "lease-a"
        assert outcome.target.last_ownership_epoch == 1
        assert outcome.target.last_fencing_token == 42
        assert loaded is not None
        assert loaded.state is RunState.PREFLIGHT
        assert loaded.targets[0] == outcome.target
        assert lease.released is False
        assert leases.requests == [
            EndpointLeaseRequest(
                run_id="run-a",
                run_target_id="run-a-target-0000",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                resource_key="endpoint:target-a",
                required_owner_installation_id="owner-a",
                required_ownership_epoch=1,
            )
        ]


def test_sqlite_run_executor_step_selects_next_run_and_records_live_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        lease = FixedLiveLease()
        leases = FixedLeaseAuthority(lease)
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )

        selected = runs.load_next_runnable_run()
        outcome = execute_one_run_target_preflight_step(runs=runs, leases=leases)
        idle = execute_one_run_target_preflight_step(runs=runs, leases=leases)

        loaded = runs.load_started_run("run-a")
        assert selected is not None
        assert selected.run_id == "run-a"
        assert outcome.idle is False
        assert outcome.claimed is True
        assert outcome.lease_acquired is True
        assert outcome.lease is lease
        assert outcome.target is not None
        assert outcome.target.state is RunTargetState.REVALIDATING
        assert loaded is not None
        assert loaded.state is RunState.PREFLIGHT
        assert loaded.targets[0] == outcome.target
        assert runs.load_next_runnable_run() is None
        assert idle.idle is True
        assert len(leases.requests) == 1


def test_sqlite_run_executor_execution_start_step_revalidates_retained_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        lease = FixedLiveLease()
        leases = FixedLeaseAuthority(lease)
        registry = HeldRunTargetLeaseRegistry()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        preflight = execute_one_run_target_preflight_step(runs=runs, leases=leases)
        assert preflight.lease is lease
        registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            lease=lease,
        )

        selected = runs.load_next_revalidating_run_target_key()
        outcome = execute_one_run_target_execution_start_step(
            runs=runs,
            lease_registry=registry,
        )

        loaded = runs.load_started_run("run-a")
        assert selected == ("run-a", "run-a-target-0000")
        assert outcome.execution_started is True
        assert outcome.validation_codes == ()
        assert outcome.mutation_permit is not None
        assert outcome.target is not None
        assert outcome.target.state is RunTargetState.EXECUTING
        assert loaded is not None
        assert loaded.state is RunState.EXECUTING
        assert loaded.targets[0] == outcome.target
        assert runs.load_next_revalidating_run_target_key() is None
        assert registry.retained_count == 1
        assert lease.released is False


def test_sqlite_run_store_records_revalidating_target_lease_reacquired(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        lease = FixedLiveLease()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        execute_one_run_target_preflight_step(runs=runs, leases=FixedLeaseAuthority(lease))

        updated = runs.record_run_target_lease_reacquired(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            expected_lease_id="lease-a",
            expected_ownership_epoch=1,
            expected_fencing_token=42,
            lease_id="lease-b",
            owner_installation_id="owner-a",
            ownership_epoch=1,
            fencing_token=43,
        )

        loaded = runs.load_started_run("run-a")
        assert updated is not None
        assert updated.state is RunTargetState.REVALIDATING
        assert updated.last_lease_id == "lease-b"
        assert updated.last_fencing_token == 43
        assert loaded is not None
        assert loaded.state is RunState.PREFLIGHT
        assert loaded.targets[0] == updated


def test_sqlite_run_executor_execution_start_step_reacquires_missing_retained_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        old_lease = FixedLiveLease()
        new_lease = FixedLiveLease("lease-b", fencing_token=43)
        leases = FixedLeaseAuthority(new_lease)
        registry = HeldRunTargetLeaseRegistry()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        execute_one_run_target_preflight_step(runs=runs, leases=FixedLeaseAuthority(old_lease))

        outcome = execute_one_run_target_execution_start_step(
            runs=runs,
            lease_registry=registry,
            leases=leases,
        )

        loaded = runs.load_started_run("run-a")
        assert outcome.execution_started is True
        assert outcome.validation_codes == ()
        assert outcome.mutation_permit is not None
        assert outcome.mutation_permit.lease_id == "lease-b"
        assert loaded is not None
        assert loaded.state is RunState.EXECUTING
        assert loaded.targets[0].state is RunTargetState.EXECUTING
        assert loaded.targets[0].last_lease_id == "lease-b"
        assert loaded.targets[0].last_fencing_token == 43
        assert registry.load_retained_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        ) is new_lease
        assert leases.requests == [
            EndpointLeaseRequest(
                run_id="run-a",
                run_target_id="run-a-target-0000",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                resource_key="endpoint:target-a",
                required_owner_installation_id="owner-a",
                required_ownership_epoch=1,
            )
        ]


def test_sqlite_run_executor_reacquires_executing_target_lease_after_registry_loss(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        old_lease = FixedLiveLease()
        new_lease = FixedLiveLease("lease-b", fencing_token=43)
        old_registry = HeldRunTargetLeaseRegistry()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        preflight = execute_one_run_target_preflight_step(
            runs=runs,
            leases=FixedLeaseAuthority(old_lease),
        )
        assert preflight.lease is old_lease
        old_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            lease=old_lease,
        )
        execution = execute_one_run_target_execution_start_step(
            runs=runs,
            lease_registry=old_registry,
        )
        assert execution.execution_started is True
        restart_registry = HeldRunTargetLeaseRegistry()
        leases = FixedLeaseAuthority(new_lease)

        selected = runs.load_next_executing_run_target_key()
        outcome = execute_one_executing_run_target_lease_reacquire_step(
            runs=runs,
            leases=leases,
            lease_registry=restart_registry,
        )

        loaded = runs.load_started_run("run-a")
        assert selected == ("run-a", "run-a-target-0000")
        assert outcome.idle is False
        assert outcome.reacquired is True
        assert outcome.validation_codes == ()
        assert outcome.lease is new_lease
        assert loaded is not None
        assert loaded.state is RunState.EXECUTING
        assert loaded.targets[0].state is RunTargetState.EXECUTING
        assert loaded.targets[0].last_lease_id == "lease-b"
        assert loaded.targets[0].last_fencing_token == 43
        assert restart_registry.load_retained_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
        ) is new_lease
        assert leases.requests == [
            EndpointLeaseRequest(
                run_id="run-a",
                run_target_id="run-a-target-0000",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                resource_key="endpoint:target-a",
                required_owner_installation_id="owner-a",
                required_ownership_epoch=1,
            )
        ]


def test_sqlite_run_store_rejects_execution_start_with_stale_lease_metadata(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        lease = FixedLiveLease()
        leases = FixedLeaseAuthority(lease)
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        execute_one_run_target_preflight_step(runs=runs, leases=leases)

        outcome = runs.record_run_target_execution_started(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            lease_id="stale-lease",
            owner_installation_id="owner-a",
            ownership_epoch=1,
            fencing_token=42,
        )

        loaded = runs.load_started_run("run-a")
        assert outcome is None
        assert loaded is not None
        assert loaded.state is RunState.PREFLIGHT
        assert loaded.targets[0].state is RunTargetState.REVALIDATING


def test_sqlite_run_store_records_successful_run_target_completion(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        lease = FixedLiveLease()
        leases = FixedLeaseAuthority(lease)
        registry = HeldRunTargetLeaseRegistry()
        plans.save_sealed_plan(plan)
        start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        preflight = execute_one_run_target_preflight_step(runs=runs, leases=leases)
        assert preflight.lease is lease
        registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            lease=lease,
        )
        execute_one_run_target_execution_start_step(runs=runs, lease_registry=registry)

        outcome = complete_run_target_success(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            runs=runs,
            completed_operations=1,
            completed_bytes=128,
        )

        loaded = runs.load_started_run("run-a")
        assert outcome.completed is True
        assert outcome.run_completed is True
        assert outcome.target is not None
        assert outcome.target.state is RunTargetState.SUCCEEDED
        assert outcome.target.completed_operations == 1
        assert outcome.target.completed_bytes == 128
        assert loaded is not None
        assert loaded.state is RunState.COMPLETED
        assert loaded.targets[0] == outcome.target
        assert _run_target_finished_utc(connection, "run-a-target-0000") is not None


def test_sqlite_run_store_lists_recent_run_activity_summaries(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        _insert_receipt(connection)
        plans = SqlitePlanStore(connection)
        runs = SqliteRunStore(connection)
        plan = _sealed_plan()
        plans.save_sealed_plan(plan)
        first = start_run_from_sealed_plan(
            command=parse_start_run_command(
                request_id="request-a",
                idempotency_key="idempotency-a",
                payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
            ),
            plans=plans,
            runs=runs,
            id_factory=FixedRunIdFactory(),
        )
        assert first.run is not None
        _insert_receipt_with_id(connection, idempotency_key="idempotency-b", request_id="request-b")
        second = replace(
            first.run,
            run_id="run-b",
            command_request_id="request-b",
            idempotency_key="idempotency-b",
            command_receipt_id="idempotency-b",
            logical_run_group_id="run-group-b",
            targets=(
                replace(
                    first.run.targets[0],
                    run_target_id="run-b-target-0000",
                    state=RunTargetState.REVALIDATING,
                ),
            ),
        )
        runs.save_started_run(second)
        connection.execute(
            "UPDATE runs SET started_utc = '2026-07-20T10:00:00.000Z' WHERE id = 'run-a'"
        )
        connection.execute(
            "UPDATE runs SET started_utc = '2026-07-20T11:00:00.000Z' WHERE id = 'run-b'"
        )
        connection.execute(
            """
            UPDATE run_targets
            SET completed_operations = 1,
                completed_bytes = 128,
                warning_count = 1
            WHERE id = 'run-b-target-0000'
            """
        )

        page = runs.list_recent_run_activity_summaries(limit=1, offset=0, job_id="job-a")

        assert [run.run_id for run in page] == ["run-b"]
        assert page[0].started_utc == "2026-07-20T11:00:00.000Z"
        assert page[0].targets[0].state is RunTargetState.REVALIDATING
        assert page[0].targets[0].completed_operations == 1
        assert page[0].targets[0].completed_bytes == 128
        assert page[0].targets[0].warning_count == 1


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
        outbox_store = SqliteOutboxStore(connection)
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
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
            outbox_store=outbox_store,
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
        loaded_outbox = outbox_store.load_outbox_message(
            "command-effect:66666666-6666-4666-8666-666666666666"
        )
        assert response.status is IpcStatus.ACCEPTED
        assert response.reason is None
        assert response.payload["run"]["run_id"] == "run-a"
        assert response.payload["receipt"]["state"] == "SUCCEEDED"
        assert loaded_run is not None
        assert loaded_run.state is RunState.QUEUED
        assert loaded_receipt is not None
        assert loaded_receipt.result_entity_type == "run"
        assert loaded_receipt.result_entity_id == "run-a"
        assert loaded_outbox is not None
        assert loaded_outbox.aggregate_type == "run"
        assert loaded_outbox.aggregate_id == "run-a"
        assert _row_count(connection, "runs") == 1
        assert _row_count(connection, "command_receipts") == 1
        assert _row_count(connection, "outbox_messages") == 1
        assert id_factory.calls == 1


def test_sqlite_enabled_trigger_occurrence_ipc_records_occurrence_and_queues_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_plan_parent_rows(connection)
        plan_store = SqlitePlanStore(connection)
        run_store = SqliteRunStore(connection)
        receipt_store = SqliteCommandReceiptStore(connection)
        outbox_store = SqliteOutboxStore(connection)
        schedule_store = SqliteScheduleStore(connection)
        occurrence_store = SqliteTriggerOccurrenceStore(connection)
        id_factory = FixedRunIdFactory()
        plan = _sealed_plan()
        plan_store.save_sealed_plan(plan)
        schedule_store.save_schedule(_schedule(plan))
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
            installation_id="preview-a",
            plan_store=plan_store,
            run_store=run_store,
            run_id_factory=id_factory,
            schedule_store=schedule_store,
            trigger_occurrence_store=occurrence_store,
            command_receipt_store=receipt_store,
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
            outbox_store=outbox_store,
        )
        ipc_client = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="same-user",
                session_id=42,
                is_remote=False,
                transport="sqlite-trigger-run-ipc-test",
            ),
            role=ProcessRole.TRIGGER_CLIENT,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        ipc_client.connect()
        delivery_id = "11111111-1111-4111-8111-111111111111"
        trigger_payload = build_enqueue_trigger_occurrence_payload(
            schedule_id="schedule-a",
            schedule_revision_hash="b" * 64,
            delivery=TriggerDeliveryContext(
                delivery_id=delivery_id,
                observed_start_utc="2026-07-20T12:00:02.000Z",
                trigger_kind=TriggerKind.SCHEDULED_TIME,
                task_definition_hash="b" * 64,
                scheduled_slot_utc="2026-07-20T12:00:00.000Z",
            ),
        )

        response = ipc_client.submit_command(
            TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
            request_id=delivery_id,
            idempotency_key=delivery_id,
            payload=trigger_payload,
            payload_hash=payload_hash(trigger_payload),
        )

        loaded_receipt = receipt_store.load_command_receipt(delivery_id)
        loaded_run = run_store.load_started_run("run-a")
        assert response.status is IpcStatus.ACCEPTED
        assert response.reason is None
        assert response.payload["enqueued"] is True
        assert response.payload["run"]["run_id"] == "run-a"
        assert response.payload["receipt"]["state"] == "SUCCEEDED"
        assert loaded_receipt is not None
        assert loaded_receipt.result_entity_type == "run"
        assert loaded_receipt.result_entity_id == "run-a"
        assert loaded_run is not None
        assert loaded_run.trigger_occurrence_id == response.payload["occurrence"]["occurrence_id"]
        loaded_occurrence = occurrence_store.load_trigger_occurrence(
            loaded_run.trigger_occurrence_id
        )
        loaded_outbox = outbox_store.load_outbox_message(f"command-effect:{delivery_id}")
        assert loaded_occurrence is not None
        assert loaded_occurrence.state is TriggerOccurrenceState.RUN_ENQUEUED
        assert loaded_occurrence.run_id == "run-a"
        assert loaded_outbox is not None
        assert loaded_outbox.aggregate_type == "run"
        assert loaded_outbox.aggregate_id == "run-a"
        assert _row_count(connection, "trigger_occurrences") == 1
        assert _row_count(connection, "runs") == 1
        assert _row_count(connection, "command_receipts") == 1
        assert _row_count(connection, "outbox_messages") == 1
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


def _insert_receipt(connection: sqlite3.Connection) -> None:
    _insert_receipt_with_id(
        connection,
        idempotency_key="idempotency-a",
        request_id="request-a",
    )


def _insert_receipt_with_id(
    connection: sqlite3.Connection,
    *,
    idempotency_key: str,
    request_id: str,
) -> None:
    receipt = CommandReceipt(
        request_id=request_id,
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key=idempotency_key,
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
        ),
    )


def _schedule(plan: SealedPlan) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id=plan.job_id,
        plan_id=plan.plan_id,
        plan_checksum=plan.plan_checksum,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash="b" * 64,
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


def _run_target_started_utc(connection: sqlite3.Connection, run_target_id: str) -> str | None:
    row = connection.execute(
        "SELECT started_utc FROM run_targets WHERE id = ?",
        (run_target_id,),
    ).fetchone()
    assert row is not None
    return None if row[0] is None else str(row[0])


def _run_target_finished_utc(connection: sqlite3.Connection, run_target_id: str) -> str | None:
    row = connection.execute(
        "SELECT finished_utc FROM run_targets WHERE id = ?",
        (run_target_id,),
    ).fetchone()
    assert row is not None
    return None if row[0] is None else str(row[0])
