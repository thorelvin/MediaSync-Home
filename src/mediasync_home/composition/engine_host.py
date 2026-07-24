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
from mediasync_home.adapters.final_verification import LocalFinalArtifactVerificationAdapter
from mediasync_home.adapters.runtime_policy import current_process_runtime_policy
from mediasync_home.adapters.staging import LocalFileStagingTransferAdapter
from mediasync_home.adapters.task_scheduler import (
    Pywin32TaskSchedulerGateway,
    WindowsTaskSchedulerRegistry,
)
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
from mediasync_home.adapters.sqlite.external_resources import SqliteExternalResourceStateStore
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
from mediasync_home.application.run_executor_cycle import (
    RunExecutorCyclePumpOutcome,
    execute_bounded_run_executor_cycle,
)
from mediasync_home.application.run_operation_planning import (
    RunTargetOperationPlanningOutcome,
    plan_run_target_recovery_operations,
)
from mediasync_home.application.run_staging import RunTargetStagingPort
from mediasync_home.application.run_intent_segments import (
    RunTargetIntentOperationStore,
    RunTargetIntentSegmentOutcome,
    publish_run_target_recovery_intent_segment,
)
from mediasync_home.application.run_catalog_handoffs import (
    RunTargetCatalogHandoffStepOutcome,
    record_next_run_target_catalog_handoff,
)
from mediasync_home.application.run_completion import complete_run_target_after_catalog_handoffs
from mediasync_home.application.catalog_handoff import FinalFileCatalogHandoffStore
from mediasync_home.application.plans import PlanStore
from mediasync_home.application.ports import FinalCommitPort
from mediasync_home.application.recovery_intents import RecoveryIntentSegmentStore
from mediasync_home.application.recovery_reconciliation import (
    RecoveryOperationStartupReconciliationReport,
)
from mediasync_home.application.recovery_resume import RecoveryResumeStartupReport
from mediasync_home.application.runs import EndpointLeaseAuthority, RunIds, RunTargetCompletionOutcome
from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationReport,
    EngineHostStartupReconciliationRequest,
    reconcile_engine_host_after_startup,
)
from mediasync_home.application.task_scheduler import (
    TaskSchedulerClaimedResourceReconciliation,
    TaskSchedulerDesiredResourceReport,
    TaskSchedulerPendingResourceReconciliationRequest,
    TaskSchedulerResourcePumpReport,
    TaskSchedulerResourcePumpRequest,
    TaskSchedulerReconciliationRequest,
    TaskSchedulerRegistryPort,
    reconcile_next_pending_task_scheduler_resource,
    reconcile_task_scheduler_resources_bounded,
    stage_task_scheduler_desired_resource_page,
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
    stop_reason: str | None = None


@dataclass(frozen=True)
class TaskSchedulerStartupReconciliationOptions:
    installation_id: str
    executable_path: str
    backend: str = "com"
    schedule_page_limit: int = 100
    max_schedule_pages: int = 10
    max_claims: int = 100
    claim_ttl_ms: int = 30_000
    claim_token_prefix: str | None = None
    after_schedule_id: str | None = None


@dataclass
class EngineHostRuntime:
    service: EngineHostIpcService
    state_layout: StateStoreLayout | None = None
    startup_reconciliation: EngineHostStartupReconciliationReport | None = None
    reconciler_instance_id: str | None = None
    run_executor_queue_store: RunExecutorQueueStore | None = None
    run_executor_lease_authority: EndpointLeaseAuthority | None = None
    run_executor_lease_registry: HeldRunTargetLeaseRegistry | None = None
    run_executor_plan_store: PlanStore | None = None
    run_executor_recovery_operation_store: RunTargetIntentOperationStore | None = None
    run_executor_recovery_intent_segment_store: RecoveryIntentSegmentStore | None = None
    run_executor_catalog_handoff_store: FinalFileCatalogHandoffStore | None = None
    run_executor_staging_transfer_port: RunTargetStagingPort | None = None
    run_executor_process_instance_id: str | None = None
    catalog_connection: sqlite3.Connection | None = None
    recovery_connection: sqlite3.Connection | None = None

    def task_scheduler_stage_desired_resource_page(
        self,
        *,
        installation_id: str,
        executable_path: str,
        limit: int,
        after_schedule_id: str | None = None,
    ) -> TaskSchedulerDesiredResourceReport:
        if (
            self.service.schedule_store is None
            or self.service.external_resource_state_store is None
        ):
            raise RuntimeError("TASK_SCHEDULER_RUNTIME_NOT_CONFIGURED")
        return stage_task_scheduler_desired_resource_page(
            TaskSchedulerReconciliationRequest(
                installation_id=installation_id,
                executable_path=executable_path,
                limit=limit,
                after_schedule_id=after_schedule_id,
            ),
            schedules=self.service.schedule_store,
            external_resources=self.service.external_resource_state_store,
        )

    def task_scheduler_reconcile_next_pending_resource(
        self,
        *,
        installation_id: str,
        executable_path: str,
        registry: TaskSchedulerRegistryPort,
        claim_token: str | None = None,
        claim_ttl_ms: int = 30_000,
    ) -> TaskSchedulerClaimedResourceReconciliation | None:
        if (
            self.service.schedule_store is None
            or self.service.external_resource_state_store is None
            or self.reconciler_instance_id is None
        ):
            raise RuntimeError("TASK_SCHEDULER_RUNTIME_NOT_CONFIGURED")
        return reconcile_next_pending_task_scheduler_resource(
            TaskSchedulerPendingResourceReconciliationRequest(
                installation_id=installation_id,
                executable_path=executable_path,
                owner_instance_id=self.reconciler_instance_id,
                claim_token=claim_token or uuid4().hex,
                claim_ttl_ms=claim_ttl_ms,
            ),
            schedules=self.service.schedule_store,
            registry=registry,
            external_resources=self.service.external_resource_state_store,
        )

    def task_scheduler_reconcile_resources_bounded(
        self,
        *,
        installation_id: str,
        executable_path: str,
        registry: TaskSchedulerRegistryPort,
        schedule_page_limit: int,
        max_schedule_pages: int,
        max_claims: int,
        claim_token_prefix: str | None = None,
        claim_ttl_ms: int = 30_000,
        after_schedule_id: str | None = None,
    ) -> TaskSchedulerResourcePumpReport:
        if (
            self.service.schedule_store is None
            or self.service.external_resource_state_store is None
            or self.reconciler_instance_id is None
        ):
            raise RuntimeError("TASK_SCHEDULER_RUNTIME_NOT_CONFIGURED")
        return reconcile_task_scheduler_resources_bounded(
            TaskSchedulerResourcePumpRequest(
                installation_id=installation_id,
                executable_path=executable_path,
                owner_instance_id=self.reconciler_instance_id,
                claim_token_prefix=claim_token_prefix or uuid4().hex,
                claim_ttl_ms=claim_ttl_ms,
                schedule_page_limit=schedule_page_limit,
                max_schedule_pages=max_schedule_pages,
                max_claims=max_claims,
                after_schedule_id=after_schedule_id,
            ),
            schedules=self.service.schedule_store,
            registry=registry,
            external_resources=self.service.external_resource_state_store,
        )

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
        if (
            self.run_executor_queue_store is None
            or self.run_executor_lease_authority is None
            or self.run_executor_lease_registry is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return execute_one_run_target_execution_start_step(
            runs=self.run_executor_queue_store,
            lease_registry=self.run_executor_lease_registry,
            leases=self.run_executor_lease_authority,
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

    def run_executor_record_catalog_handoff(
        self,
        *,
        permit: MutationPermit,
    ) -> RunTargetCatalogHandoffStepOutcome:
        if (
            self.run_executor_recovery_operation_store is None
            or self.run_executor_catalog_handoff_store is None
            or self.run_executor_process_instance_id is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return record_next_run_target_catalog_handoff(
            permit=permit,
            recovery_operations=self.run_executor_recovery_operation_store,
            catalog_handoffs=self.run_executor_catalog_handoff_store,
            process_instance_id=self.run_executor_process_instance_id,
        )

    def run_executor_complete_catalog_recorded_target(
        self,
        *,
        permit: MutationPermit,
    ) -> RunTargetCompletionOutcome:
        if (
            self.run_executor_queue_store is None
            or self.run_executor_recovery_operation_store is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return complete_run_target_after_catalog_handoffs(
            permit=permit,
            runs=self.run_executor_queue_store,
            recovery_operations=self.run_executor_recovery_operation_store,
        )

    def run_executor_cycle(
        self,
        *,
        max_steps: int,
        final_commit_port: FinalCommitPort | None = None,
        staging_transfer_port: RunTargetStagingPort | None = None,
    ) -> RunExecutorCyclePumpOutcome:
        if (
            self.run_executor_queue_store is None
            or self.run_executor_lease_authority is None
            or self.run_executor_lease_registry is None
            or self.run_executor_plan_store is None
            or self.run_executor_recovery_operation_store is None
            or self.run_executor_recovery_intent_segment_store is None
            or self.run_executor_catalog_handoff_store is None
            or self.run_executor_process_instance_id is None
        ):
            raise RuntimeError("RUN_EXECUTOR_RUNTIME_NOT_CONFIGURED")
        return execute_bounded_run_executor_cycle(
            runs=self.run_executor_queue_store,
            leases=self.run_executor_lease_authority,
            lease_registry=self.run_executor_lease_registry,
            plans=self.run_executor_plan_store,
            recovery_operations=self.run_executor_recovery_operation_store,
            intent_segments=self.run_executor_recovery_intent_segment_store,
            catalog_handoffs=self.run_executor_catalog_handoff_store,
            process_instance_id=self.run_executor_process_instance_id,
            max_steps=max_steps,
            final_commit_port=final_commit_port,
            staging_transfer_port=staging_transfer_port or self.run_executor_staging_transfer_port,
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
    parser.add_argument(
        "--serve-forever",
        action="store_true",
        help="serve local IPC until the Engine Host process is interrupted",
    )
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
    parser.add_argument(
        "--reconcile-task-scheduler-resources",
        action="store_true",
        help="run one bounded Task Scheduler desired-state pump before serving the pipe",
    )
    parser.add_argument(
        "--task-scheduler-backend",
        choices=("com",),
        default="com",
        help="Task Scheduler registry backend used by the bounded startup pump",
    )
    parser.add_argument(
        "--task-scheduler-executable-path",
        help="absolute executable path registered in Task Scheduler actions",
    )
    parser.add_argument(
        "--task-scheduler-schedule-page-limit",
        type=_positive_int,
        default=100,
        help="schedule page size for the bounded Task Scheduler startup pump",
    )
    parser.add_argument(
        "--task-scheduler-max-schedule-pages",
        type=_positive_int,
        default=10,
        help="maximum schedule pages staged by the bounded Task Scheduler startup pump",
    )
    parser.add_argument(
        "--task-scheduler-max-claims",
        type=_positive_int,
        default=100,
        help="maximum pending Task Scheduler resources reconciled by the startup pump",
    )
    parser.add_argument(
        "--task-scheduler-claim-ttl-ms",
        type=_positive_int,
        default=30_000,
        help="claim TTL used by the bounded Task Scheduler startup pump",
    )
    parser.add_argument(
        "--task-scheduler-claim-token-prefix",
        help="optional deterministic claim-token prefix for the bounded startup pump",
    )
    return parser


def run_engine_host(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.reconcile_task_scheduler_resources and not args.pipe_name:
        raise RuntimeError("TASK_SCHEDULER_RECONCILIATION_REQUIRES_PIPE_MODE")
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
    task_scheduler_reconciliation: TaskSchedulerResourcePumpReport | None = None
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
        if args.reconcile_task_scheduler_resources:
            try:
                task_scheduler_reconciliation = (
                    reconcile_task_scheduler_resources_for_engine_host_startup(
                        runtime,
                        options=_task_scheduler_startup_options(args),
                    )
                )
            except Exception as exc:
                output(
                    json.dumps(
                        {
                            "error_type": type(exc).__name__,
                            "event": "ENGINE_HOST_TASK_SCHEDULER_RECONCILIATION_FAILED",
                            "pipe_name": args.pipe_name,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                return 4
        output(
            json.dumps(
                {
                    "event": "ENGINE_HOST_PIPE_STARTING",
                    "pipe_name": args.pipe_name,
                    "serve_forever": args.serve_forever,
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
                    "task_scheduler_reconciliation": _task_scheduler_resource_pump_payload(
                        task_scheduler_reconciliation
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        server = Win32NamedPipeServer(
            pipe_name=args.pipe_name,
            service=runtime.service,
        )
        if args.serve_forever:
            result = serve_pipe_requests_until_interrupted(server)
        else:
            result = serve_bounded_pipe_requests(server, request_limit=args.serve_requests)
        if result.completed:
            output(
                json.dumps(
                    {
                        "event": "ENGINE_HOST_PIPE_STOPPED",
                        "pipe_name": args.pipe_name,
                        "served_requests": result.served_requests,
                        "stop_reason": result.stop_reason,
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
                    "stop_reason": result.stop_reason,
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
        external_resource_state = SqliteExternalResourceStateStore(catalog_connection)
        catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
        resource_leases = SqliteResourceLeaseStore(recovery_connection)
        recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
        recovery_intent_segments = SqliteRecoveryIntentSegmentStore(recovery_connection)
        endpoint_root_resolver = SqliteEndpointRootResolver(catalog_connection)
        run_executor_lease_authority = LocalResolvingEndpointLeaseAuthority(
            root_resolver=endpoint_root_resolver,
            resource_lease_store=resource_leases,
        )
        run_executor_staging_transfer_port = LocalFileStagingTransferAdapter(
            root_resolver=endpoint_root_resolver,
        )
        final_artifact_verifier = LocalFinalArtifactVerificationAdapter(
            root_resolver=endpoint_root_resolver,
        )
        run_executor_lease_registry = HeldRunTargetLeaseRegistry()
        startup_reconciliation = reconcile_engine_host_after_startup(
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id=reconciler_instance_id,
                inactive_outbox_owner_instance_ids=inactive_outbox_owner_instance_ids,
            ),
            command_receipts=command_receipts,
            outbox=outbox,
            recovery_operations=recovery_operations,
            recovery_resume_operations=recovery_operations,
            recovery_resume_catalog_handoffs=catalog_handoffs,
            recovery_resume_final_verifier=final_artifact_verifier,
            runs=runs,
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
            external_resource_state_store=external_resource_state,
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
        reconciler_instance_id=reconciler_instance_id,
        run_executor_queue_store=runs,
        run_executor_lease_authority=run_executor_lease_authority,
        run_executor_lease_registry=run_executor_lease_registry,
        run_executor_plan_store=plans,
        run_executor_recovery_operation_store=recovery_operations,
        run_executor_recovery_intent_segment_store=recovery_intent_segments,
        run_executor_catalog_handoff_store=catalog_handoffs,
        run_executor_staging_transfer_port=run_executor_staging_transfer_port,
        run_executor_process_instance_id=reconciler_instance_id,
        catalog_connection=catalog_connection,
        recovery_connection=recovery_connection,
    )


def build_task_scheduler_registry(backend: str) -> TaskSchedulerRegistryPort:
    if backend == "com":
        return WindowsTaskSchedulerRegistry(Pywin32TaskSchedulerGateway())
    raise RuntimeError("TASK_SCHEDULER_BACKEND_UNSUPPORTED")


def reconcile_task_scheduler_resources_for_engine_host_startup(
    runtime: EngineHostRuntime,
    *,
    options: TaskSchedulerStartupReconciliationOptions,
    registry: TaskSchedulerRegistryPort | None = None,
) -> TaskSchedulerResourcePumpReport:
    return runtime.task_scheduler_reconcile_resources_bounded(
        installation_id=options.installation_id,
        executable_path=options.executable_path,
        registry=registry or build_task_scheduler_registry(options.backend),
        schedule_page_limit=options.schedule_page_limit,
        max_schedule_pages=options.max_schedule_pages,
        max_claims=options.max_claims,
        claim_token_prefix=options.claim_token_prefix,
        claim_ttl_ms=options.claim_ttl_ms,
        after_schedule_id=options.after_schedule_id,
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
    return PipeLoopResult(
        served_requests=served_requests,
        completed=True,
        stop_reason="REQUEST_LIMIT_REACHED",
    )


def serve_pipe_requests_until_interrupted(server: PipeServer) -> PipeLoopResult:
    served_requests = 0
    try:
        while True:
            server.serve_once()
            served_requests += 1
    except KeyboardInterrupt:
        return PipeLoopResult(
            served_requests=served_requests,
            completed=True,
            stop_reason="INTERRUPTED",
        )
    except Exception as exc:
        return PipeLoopResult(
            served_requests=served_requests,
            completed=False,
            error_type=type(exc).__name__,
            stop_reason="SERVER_ERROR",
        )


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
        "recovery_operations": _recovery_operations_reconciliation_payload(
            report.recovery_operations
        ),
        "recovery_resume": _recovery_resume_payload(report.recovery_resume),
        "skipped_outbox_requeue_reason": report.skipped_outbox_requeue_reason,
    }


def _task_scheduler_startup_options(
    args: argparse.Namespace,
) -> TaskSchedulerStartupReconciliationOptions:
    if args.state_root is None:
        raise RuntimeError("TASK_SCHEDULER_RECONCILIATION_REQUIRES_STATE_ROOT")
    if args.task_scheduler_executable_path is None:
        raise RuntimeError("TASK_SCHEDULER_EXECUTABLE_PATH_REQUIRED")
    return TaskSchedulerStartupReconciliationOptions(
        installation_id=str(args.installation_id),
        executable_path=str(args.task_scheduler_executable_path),
        backend=str(args.task_scheduler_backend),
        schedule_page_limit=int(args.task_scheduler_schedule_page_limit),
        max_schedule_pages=int(args.task_scheduler_max_schedule_pages),
        max_claims=int(args.task_scheduler_max_claims),
        claim_ttl_ms=int(args.task_scheduler_claim_ttl_ms),
        claim_token_prefix=args.task_scheduler_claim_token_prefix,
    )


def _task_scheduler_resource_pump_payload(
    report: TaskSchedulerResourcePumpReport | None,
) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "claim_idle": report.claim_idle,
        "claims_attempted": report.claims_attempted,
        "resources_applied": report.resources_applied,
        "resources_blocked": report.resources_blocked,
        "resources_completed": report.resources_completed,
        "resources_reconciled": report.resources_reconciled,
        "resources_staged": report.resources_staged,
        "schedule_pages_attempted": report.schedule_pages_attempted,
        "schedules_scanned": report.schedules_scanned,
        "stage_blocked": report.stage_blocked,
        "stage_completed": report.stage_completed,
        "stage_next_cursor": report.stage_next_cursor,
        "claim_findings": [
            {
                "action": finding.action.value,
                "applied": finding.applied,
                "blocked": finding.blocked,
                "completed": finding.completed,
                "reason": finding.reason,
                "resource_id": finding.resource_id,
            }
            for finding in report.claim_findings
        ],
        "stage_findings": [
            {
                "reason": finding.reason,
                "schedule_id": finding.schedule_id,
                "staged": finding.staged,
                "task_path": finding.task_path,
            }
            for finding in report.stage_findings
        ],
    }


def _recovery_operations_reconciliation_payload(
    report: RecoveryOperationStartupReconciliationReport | None,
) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "scanned": report.scanned,
        "requires_recovery_mode": report.requires_recovery_mode,
        "manual_decision_operation_ids": report.manual_decision_operation_ids,
        "findings": [
            {
                "run_id": finding.run_id,
                "run_target_id": finding.run_target_id,
                "operation_id": finding.operation_id,
                "phase": finding.phase.value,
                "classification": finding.classification.value,
                "requires_manual_decision": finding.requires_manual_decision,
                "next_action": finding.next_action,
            }
            for finding in report.findings
        ],
    }


def _recovery_resume_payload(
    report: RecoveryResumeStartupReport | None,
) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "scanned": report.scanned,
        "completed_run_target_ids": report.completed_run_target_ids,
        "blocked_run_target_ids": report.blocked_run_target_ids,
        "findings": [
            {
                "action": finding.action.value,
                "run_id": finding.run_id,
                "run_target_id": finding.run_target_id,
                "operation_ids": finding.operation_ids,
                "validation_codes": finding.validation_codes,
                "next_action": finding.next_action,
            }
            for finding in report.findings
        ],
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
