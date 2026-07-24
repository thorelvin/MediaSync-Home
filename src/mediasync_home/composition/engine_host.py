from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mediasync_home.adapters.endpoint_leases import LocalResolvingEndpointLeaseAuthority
from mediasync_home.adapters.runtime_policy import current_process_runtime_policy
from mediasync_home.adapters.sqlite.catalog_handoffs import SqliteFinalFileCatalogHandoffStore
from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    StateStoreLayout,
    apply_sqlite_connection_policy,
    build_state_store_layout,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.endpoint_roots import SqliteEndpointRootResolver
from mediasync_home.adapters.sqlite.job_catalog import SqliteStandardBackupJobCatalog
from mediasync_home.adapters.sqlite.job_draft_store import SqliteJobDraftStore
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore
from mediasync_home.adapters.sqlite.schedules import SqliteScheduleStore
from mediasync_home.adapters.sqlite.snapshots import SqliteSnapshotEntryStore
from mediasync_home.adapters.sqlite.trigger_occurrences import SqliteTriggerOccurrenceStore
from mediasync_home.application.run_executor import (
    HeldRunTargetLeaseRegistry,
    RunExecutorExecutionStartStepOutcome,
    RunExecutorPumpOutcome,
    RunExecutorQueueStore,
    execute_bounded_run_executor_preflight_pump,
    execute_one_run_target_execution_start_step,
)
from mediasync_home.application.run_operation_planning import (
    RunTargetOperationPlanningOutcome,
    plan_run_target_recovery_operations,
)
from mediasync_home.application.run_intent_segments import (
    RunTargetIntentOperationStore,
    RunTargetIntentSegmentOutcome,
    publish_run_target_recovery_intent_segment,
)
from mediasync_home.application.plans import PlanStore
from mediasync_home.application.recovery_intents import RecoveryIntentSegmentStore
from mediasync_home.application.runs import EndpointLeaseAuthority, RunIds
from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationReport,
    EngineHostStartupReconciliationRequest,
    reconcile_engine_host_after_startup,
)
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.domain.capabilities import MutationPermit
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy
from mediasync_home.ipc.server import EngineHostIpcService


ROOT = Path(__file__).resolve().parents[3]


class PipeServer(Protocol):
    def serve_once(self) -> None:
        pass


class EngineHostMutexGuard(Protocol):
    name: str

    def close(self) -> None: ...


class UuidRunIdFactory:
    def new_run_ids(self) -> RunIds:
        token = uuid4().hex
        return RunIds(run_id=f"run-{token}", logical_run_group_id=f"run-group-{token}")


@dataclass(frozen=True)
class PipeLoopResult:
    served_requests: int
    completed: bool
    error_type: str | None = None


@dataclass
class EngineHostRuntime:
    service: EngineHostIpcService
    state_layout: StateStoreLayout | None = None
    startup_reconciliation: EngineHostStartupReconciliationReport | None = None
    run_executor_queue_store: RunExecutorQueueStore | None = None
    run_executor_lease_authority: EndpointLeaseAuthority | None = None
    run_executor_lease_registry: HeldRunTargetLeaseRegistry | None = None
    run_executor_plan_store: PlanStore | None = None
    run_executor_recovery_operation_store: RunTargetIntentOperationStore | None = None
    run_executor_recovery_intent_segment_store: RecoveryIntentSegmentStore | None = None
    run_executor_process_instance_id: str | None = None
    catalog_connection: sqlite3.Connection | None = None
    recovery_connection: sqlite3.Connection | None = None

    def run_executor_preflight_pump(self, *, max_steps: int) -> RunExecutorPumpOutcome:
        if (
            self.run_executor_queue_store is None
            or self.run_executor_lease_authority is None
            or self.run_executor_lease_registry is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return execute_bounded_run_executor_preflight_pump(
            runs=self.run_executor_queue_store,
            leases=self.run_executor_lease_authority,
            lease_registry=self.run_executor_lease_registry,
            max_steps=max_steps,
        )

    def run_executor_execution_start_step(self) -> RunExecutorExecutionStartStepOutcome:
        if self.run_executor_queue_store is None or self.run_executor_lease_registry is None:
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return execute_one_run_target_execution_start_step(
            runs=self.run_executor_queue_store,
            lease_registry=self.run_executor_lease_registry,
        )

    def run_executor_plan_target_operations(
        self,
        *,
        permit: MutationPermit,
    ) -> RunTargetOperationPlanningOutcome:
        if (
            self.run_executor_queue_store is None
            or self.run_executor_plan_store is None
            or self.run_executor_recovery_operation_store is None
            or self.run_executor_process_instance_id is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return plan_run_target_recovery_operations(
            permit=permit,
            runs=self.run_executor_queue_store,
            plans=self.run_executor_plan_store,
            recovery_operations=self.run_executor_recovery_operation_store,
            process_instance_id=self.run_executor_process_instance_id,
        )

    def run_executor_publish_recovery_intent_segment(
        self,
        *,
        permit: MutationPermit,
        segment_sequence: int = 0,
        previous_segment_hash: str | None = None,
    ) -> RunTargetIntentSegmentOutcome:
        if (
            self.run_executor_recovery_operation_store is None
            or self.run_executor_recovery_intent_segment_store is None
            or self.run_executor_process_instance_id is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return publish_run_target_recovery_intent_segment(
            permit=permit,
            recovery_operations=self.run_executor_recovery_operation_store,
            intent_segments=self.run_executor_recovery_intent_segment_store,
            process_instance_id=self.run_executor_process_instance_id,
            segment_sequence=segment_sequence,
            previous_segment_hash=previous_segment_hash,
        )

    def close(self) -> None:
        if self.run_executor_lease_registry is not None:
            self.run_executor_lease_registry.release_all()
        if self.catalog_connection is not None:
            self.catalog_connection.close()
        if self.recovery_connection is not None:
            self.recovery_connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home Engine Host")
    parser.add_argument("--pipe-name", help="serve non-mutating IPC over this local named pipe")
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--serve-requests", type=_positive_int, default=1)
    parser.add_argument("--state-root", type=Path, help="optional local preview state root")
    parser.add_argument("--host-mutex-name", help="optional local Engine Host singleton mutex")
    parser.add_argument(
        "--publish-host-locator",
        action="store_true",
        help="publish a local-preview host locator after acquiring the singleton mutex",
    )
    parser.add_argument(
        "--inactive-outbox-owner-instance-id",
        action="append",
        help="owner instance proven inactive before startup outbox requeue",
    )
    return parser


def run_engine_host(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pipe_name:
        return run_role(ProcessRole.ENGINE_HOST, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe Engine Host mode is Windows-only")

    from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeServer
    from mediasync_home.ipc.win32_named_pipe import current_user_policy

    output = emit or print
    service_status = startup_status(
        ProcessRole.ENGINE_HOST,
        runtime_policy=current_process_runtime_policy(ROOT),
    )
    host_mutex = _acquire_host_mutex(args.host_mutex_name, output=output, pipe_name=args.pipe_name)
    if args.host_mutex_name and host_mutex is None:
        return 3
    host_locator_payload: dict[str, object] | None = None
    host_locator_path: Path | None = None
    runtime: EngineHostRuntime | None = None
    try:
        if args.publish_host_locator:
            host_locator_payload, host_locator_path = _publish_local_host_locator(
                installation_id=args.installation_id,
                pipe_name=args.pipe_name,
                mutex_name=args.host_mutex_name,
                state_root=args.state_root,
                process_id=os.getpid(),
            )
        runtime = build_engine_host_runtime(
            authorization=current_user_policy(),
            service_status=service_status,
            installation_id=args.installation_id,
            state_root=args.state_root,
            reconciler_instance_id=args.installation_id,
            inactive_outbox_owner_instance_ids=tuple(args.inactive_outbox_owner_instance_id or ()),
        )
        output(
            json.dumps(
                {
                    "event": "ENGINE_HOST_PIPE_STARTING",
                    "pipe_name": args.pipe_name,
                    "serve_requests": args.serve_requests,
                    "startup_reconciliation": _startup_reconciliation_payload(
                        runtime.startup_reconciliation
                    ),
                    "state_root": None
                    if runtime.state_layout is None
                    else str(runtime.state_layout.root),
                    "host_status": service_status.to_dict(),
                    "host_mutex": None
                    if host_mutex is None
                    else {"acquired": True, "name": host_mutex.name},
                    "host_locator": host_locator_payload,
                    "host_locator_path": None
                    if host_locator_path is None
                    else str(host_locator_path),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        server = Win32NamedPipeServer(
            pipe_name=args.pipe_name,
            service=runtime.service,
        )
        result = serve_bounded_pipe_requests(server, request_limit=args.serve_requests)
        if result.completed:
            output(
                json.dumps(
                    {
                        "event": "ENGINE_HOST_PIPE_STOPPED",
                        "pipe_name": args.pipe_name,
                        "served_requests": result.served_requests,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0

        output(
            json.dumps(
                {
                    "error_type": result.error_type,
                    "event": "ENGINE_HOST_PIPE_FAILED",
                    "pipe_name": args.pipe_name,
                    "served_requests": result.served_requests,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    finally:
        if runtime is not None:
            runtime.close()
        if host_mutex is not None:
            host_mutex.close()


def build_engine_host_runtime(
    *,
    authorization: ClientAuthorizationPolicy,
    service_status: RuntimeStatus,
    installation_id: str = "local-dev",
    state_root: Path | None = None,
    reconciler_instance_id: str = "local-dev",
    inactive_outbox_owner_instance_ids: tuple[str, ...] = (),
) -> EngineHostRuntime:
    if state_root is None:
        return EngineHostRuntime(
            service=EngineHostIpcService(
                authorization,
                status=service_status,
                installation_id=installation_id,
            )
        )

    layout = build_state_store_layout(state_root)
    layout.root.mkdir(parents=True, exist_ok=True)
    catalog_connection = sqlite3.connect(layout.catalog)
    recovery_connection = sqlite3.connect(layout.recovery)
    try:
        apply_sqlite_connection_policy(
            catalog_connection,
            catalog_critical_writer_policy(layout.catalog),
        )
        apply_sqlite_connection_policy(
            recovery_connection,
            recovery_writer_policy(layout.recovery),
        )
        apply_sqlite_migrations(catalog_connection, catalog_migration_plan())
        apply_sqlite_migrations(recovery_connection, recovery_migration_plan())
        command_receipts = SqliteCommandReceiptStore(catalog_connection)
        outbox = SqliteOutboxStore(catalog_connection)
        job_drafts = SqliteJobDraftStore(catalog_connection)
        standard_backup_jobs = SqliteStandardBackupJobCatalog(catalog_connection)
        snapshots = SqliteSnapshotEntryStore(catalog_connection)
        plans = SqlitePlanStore(catalog_connection)
        runs = SqliteRunStore(catalog_connection)
        schedules = SqliteScheduleStore(catalog_connection)
        trigger_occurrences = SqliteTriggerOccurrenceStore(catalog_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        resource_leases = SqliteResourceLeaseStore(recovery_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        recovery_intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        run_executor_lease_authority = LocalResolvingEndpointLeaseAuthority(
            root_resolver=SqliteEndpointRootResolver(catalog_connection),
            resource_lease_store=resource_leases,
        )
        run_executor_lease_registry = HeldRunTargetLeaseRegistry()
        startup_reconciliation = reconcile_engine_host_after_startup(
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id=reconciler_instance_id,
                inactive_outbox_owner_instance_ids=inactive_outbox_owner_instance_ids,
            ),
            command_receipts=command_receipts,
            outbox=outbox,
        )
        service = EngineHostIpcService(
            authorization,
            status=service_status,
            installation_id=installation_id,
            job_draft_store=job_drafts,
            standard_backup_job_catalog=standard_backup_jobs,
            standard_backup_job_read_store=standard_backup_jobs,
            standard_backup_job_detail_store=standard_backup_jobs,
            snapshot_entry_read_store=snapshots,
            snapshot_coverage_read_store=snapshots,
            snapshot_issue_read_store=snapshots,
            plan_store=plans,
            plan_operation_read_store=plans,
            plan_endpoint_read_store=plans,
            run_store=runs,
            run_id_factory=UuidRunIdFactory(),
            run_activity_read_store=runs,
            schedule_store=schedules,
            trigger_occurrence_store=trigger_occurrences,
            cataloged_file_read_store=catalog_handoffs,
            command_receipt_store=command_receipts,
            outbox_store=outbox,
        )
    except Exception:
        catalog_connection.close()
        recovery_connection.close()
        raise
    return EngineHostRuntime(
        service=service,
        state_layout=layout,
        startup_reconciliation=startup_reconciliation,
        run_executor_queue_store=runs,
        run_executor_lease_authority=run_executor_lease_authority,
        run_executor_lease_registry=run_executor_lease_registry,
        run_executor_plan_store=plans,
        run_executor_recovery_operation_store=recovery_operations,
        run_executor_recovery_intent_segment_store=recovery_intent_segments,
        run_executor_process_instance_id=reconciler_instance_id,
        catalog_connection=catalog_connection,
        recovery_connection=recovery_connection,
    )


def serve_bounded_pipe_requests(server: PipeServer, *, request_limit: int) -> PipeLoopResult:
    served_requests = 0
    try:
        for _ in range(request_limit):
            server.serve_once()
            served_requests += 1
    except Exception as exc:
        return PipeLoopResult(
            served_requests=served_requests,
            completed=False,
            error_type=type(exc).__name__,
        )
    return PipeLoopResult(served_requests=served_requests, completed=True)


def _startup_reconciliation_payload(
    report: EngineHostStartupReconciliationReport | None,
) -> dict[str, object] | None:
    if report is None:
        return None
    command_receipts = None
    if report.command_receipts is not None:
        command_receipts = {
            "scanned": report.command_receipts.scanned,
            "rejected_idempotency_keys": report.command_receipts.rejected_idempotency_keys,
            "pending_effect_reconciliation_keys": (
                report.command_receipts.pending_effect_reconciliation_keys
            ),
        }
    outbox = None
    if report.outbox is not None:
        outbox = {
            "scanned": report.outbox.scanned,
            "requeued_message_ids": report.outbox.requeued_message_ids,
        }
    return {
        "reconciler_instance_id": report.reconciler_instance_id,
        "command_receipts": command_receipts,
        "outbox": outbox,
        "skipped_outbox_requeue_reason": report.skipped_outbox_requeue_reason,
    }


def _acquire_host_mutex(
    mutex_name: str | None,
    *,
    output: Emit,
    pipe_name: str,
) -> EngineHostMutexGuard | None:
    if mutex_name is None:
        return None

    from mediasync_home.adapters.host_mutex import EngineHostMutexError, LocalEngineHostMutex

    try:
        return LocalEngineHostMutex.acquire(mutex_name)
    except EngineHostMutexError as exc:
        output(
            json.dumps(
                {
                    "event": "ENGINE_HOST_SINGLETON_REJECTED",
                    "mutex_name": mutex_name,
                    "pipe_name": pipe_name,
                    "reason": exc.validation_code,
                    "scope": "0B_SAME_USER_LOCAL_PREVIEW",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return None


def _publish_local_host_locator(
    *,
    installation_id: str,
    pipe_name: str,
    mutex_name: str | None,
    state_root: Path | None,
    process_id: int,
) -> tuple[dict[str, object], Path]:
    if mutex_name is None:
        raise RuntimeError("HOST_LOCATOR_MUTEX_REQUIRED")
    if state_root is None:
        raise RuntimeError("HOST_LOCATOR_STATE_ROOT_REQUIRED")

    from mediasync_home.adapters.local_host_locator import publish_local_engine_host_publication
    from mediasync_home.application.host_locator import build_local_engine_host_publication

    publication = build_local_engine_host_publication(
        installation_id=installation_id,
        pipe_name=pipe_name,
        mutex_name=mutex_name,
        state_root=state_root,
        process_id=process_id,
    )
    path = publish_local_engine_host_publication(publication)
    return publication.to_payload(), path


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed
