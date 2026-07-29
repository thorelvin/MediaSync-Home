from __future__ import annotations

import json
import sqlite3
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path

import pytest

from mediasync_home.adapters.robocopy import RobocopyStagingTransferAdapter
from mediasync_home.adapters.sqlite.connection_policy import SqliteStore
from mediasync_home.adapters.sqlite.migrations import current_schema_version
from mediasync_home.application.external_resources import ExternalResourceState, ExternalResourceType
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.run_executor import RunExecutorPumpStopReason
from mediasync_home.application.run_executor_cycle import (
    RunExecutorCycleAction,
    RunExecutorCycleOutcome,
    RunExecutorCyclePumpOutcome,
)
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.task_scheduler import (
    TaskSchedulerDefinition,
    ObservedTaskSchedulerDefinition,
    TaskSchedulerResourcePumpReport,
    TaskSchedulerReconciliationAction,
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
)
from mediasync_home.application.trigger_occurrences import TriggerKind
from mediasync_home.composition import engine_host as engine_host_module
from mediasync_home.composition.engine_host import (
    ExecutorMaintenanceLoop,
    TaskSchedulerMaintenanceLoop,
    TaskSchedulerStartupReconciliationOptions,
    build_engine_host_runtime,
    build_parser,
    reconcile_task_scheduler_resources_for_engine_host_startup,
    run_engine_host,
    serve_bounded_pipe_requests,
    serve_pipe_requests_until_interrupted,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import IpcReason, IpcStatus


EXPECTED_USER = "same-user"
EXPECTED_SESSION = 42
TASK_SCHEDULER_EXECUTABLE = r"C:\Program Files\MediaSync Home\MediaSyncHome.exe"


def test_bounded_pipe_loop_serves_exact_request_limit() -> None:
    server = _FakePipeServer()

    result = serve_bounded_pipe_requests(server, request_limit=3)

    assert result.completed is True
    assert result.error_type is None
    assert result.served_requests == 3
    assert server.calls == 3


def test_bounded_pipe_loop_reports_sanitized_failure() -> None:
    server = _FakePipeServer(fail_on_call=2)

    result = serve_bounded_pipe_requests(server, request_limit=4)

    assert result.completed is False
    assert result.error_type == "RuntimeError"
    assert result.served_requests == 1
    assert server.calls == 2


def test_bounded_pipe_loop_runs_after_request_callback() -> None:
    server = _FakePipeServer()
    callbacks: list[int] = []

    result = serve_bounded_pipe_requests(
        server,
        request_limit=3,
        after_request=lambda: callbacks.append(server.calls),
    )

    assert result.completed is True
    assert result.served_requests == 3
    assert callbacks == [1, 2, 3]


def test_engine_host_parser_requires_positive_serve_request_limit() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--serve-requests", "0"])


def test_engine_host_parser_requires_bounded_executor_cycle_step_limit() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--run-executor-cycle-max-steps", "101"])


def test_engine_host_parser_accepts_explicit_long_running_pipe_mode() -> None:
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--serve-forever",
            "--run-executor-cycle-after-request",
            "--run-executor-cycle-max-steps",
            "7",
            "--run-executor-cycle-interval-ms",
            "50",
            "--run-executor-cycle-max-interval-ms",
            "400",
            "--run-executor-staging-backend",
            "robocopy",
        ]
    )

    assert args.pipe_name == "pipe-a"
    assert args.serve_forever is True
    assert args.serve_requests == 1
    assert args.run_executor_cycle_after_request is True
    assert args.run_executor_cycle_max_steps == 7
    assert args.run_executor_cycle_interval_ms == 50
    assert args.run_executor_cycle_max_interval_ms == 400
    assert args.run_executor_staging_backend == "robocopy"


def test_executor_maintenance_loop_runs_interval_cycle_and_closes_runtime() -> None:
    runtime = _FakeRuntime()
    lines: list[str] = []

    loop = ExecutorMaintenanceLoop(
        runtime_factory=lambda: runtime,
        interval_ms=1,
        max_interval_ms=4,
        max_steps=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    _wait_until(lambda: runtime.cycle_max_steps != [])
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert runtime.cycle_max_steps[0] == 4
    assert runtime.closed is True
    assert events[0]["event"] == "ENGINE_HOST_RUN_EXECUTOR_CYCLE"
    assert events[0]["cycle_trigger"] == "INTERVAL"
    assert events[0]["next_interval_ms"] == 2
    assert events[0]["run_executor_cycle"]["stopped_reason"] == "IDLE"


def test_executor_maintenance_loop_resets_interval_after_non_idle_work() -> None:
    runtime = _ScriptedRuntime(
        (
            RunExecutorPumpStopReason.IDLE,
            RunExecutorPumpStopReason.STEP_LIMIT_REACHED,
        )
    )
    lines: list[str] = []

    loop = ExecutorMaintenanceLoop(
        runtime_factory=lambda: runtime,
        interval_ms=1,
        max_interval_ms=4,
        max_steps=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    _wait_until(lambda: len(runtime.cycle_max_steps) >= 2)
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert [event["next_interval_ms"] for event in events[:2]] == [2, 1]
    assert [event["run_executor_cycle"]["stopped_reason"] for event in events[:2]] == [
        "IDLE",
        "STEP_LIMIT_REACHED",
    ]


def test_executor_maintenance_loop_reports_sanitized_runtime_failure() -> None:
    lines: list[str] = []

    loop = ExecutorMaintenanceLoop(
        runtime_factory=lambda: (_ for _ in ()).throw(RuntimeError("detail")),
        interval_ms=1,
        max_interval_ms=4,
        max_steps=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert events == [
        {
            "cycle_trigger": "INTERVAL",
            "error_type": "RuntimeError",
            "event": "ENGINE_HOST_RUN_EXECUTOR_CYCLE_FAILED",
            "next_interval_ms": None,
            "pipe_name": "pipe-a",
        }
    ]


def test_task_scheduler_maintenance_loop_runs_interval_reconciliation_and_closes_runtime() -> None:
    runtime = _TaskSchedulerRuntime((_task_scheduler_pump_report(),))
    registry = _TaskSchedulerRegistry()
    lines: list[str] = []

    loop = TaskSchedulerMaintenanceLoop(
        runtime_factory=lambda: runtime,
        registry_factory=lambda: registry,
        options=TaskSchedulerStartupReconciliationOptions(
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
            schedule_page_limit=5,
            max_schedule_pages=2,
            max_claims=3,
            claim_ttl_ms=12_000,
            claim_token_prefix="maint-a",
        ),
        interval_ms=1,
        max_interval_ms=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    _wait_until(lambda: runtime.task_scheduler_calls != [])
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert runtime.closed is True
    assert runtime.task_scheduler_calls[0] == {
        "after_schedule_id": None,
        "after_orphan_task_name": None,
        "claim_token_prefix": "maint-a",
        "claim_ttl_ms": 12_000,
        "executable_path": TASK_SCHEDULER_EXECUTABLE,
        "installation_id": "install-a",
        "max_claims": 3,
        "max_schedule_pages": 2,
        "orphan_task_page_limit": 100,
        "registry": registry,
        "schedule_page_limit": 5,
    }
    assert events[0]["event"] == "ENGINE_HOST_TASK_SCHEDULER_RECONCILIATION"
    assert events[0]["cycle_trigger"] == "INTERVAL"
    assert events[0]["next_interval_ms"] == 2
    assert events[0]["task_scheduler_reconciliation"]["claim_idle"] is True
    assert events[0]["task_scheduler_reconciliation"]["resources_reconciled"] == 0


def test_task_scheduler_maintenance_loop_carries_stage_cursor_until_scan_completes() -> None:
    runtime = _TaskSchedulerRuntime(
        (
            _task_scheduler_pump_report(
                resources_reconciled=1,
                stage_completed=False,
                stage_next_cursor="schedule-a",
            ),
            _task_scheduler_pump_report(),
        )
    )
    lines: list[str] = []

    loop = TaskSchedulerMaintenanceLoop(
        runtime_factory=lambda: runtime,
        registry_factory=_TaskSchedulerRegistry,
        options=TaskSchedulerStartupReconciliationOptions(
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
            schedule_page_limit=1,
            max_schedule_pages=1,
            max_claims=1,
            claim_token_prefix="maint-a",
        ),
        interval_ms=1,
        max_interval_ms=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    _wait_until(lambda: len(runtime.task_scheduler_calls) >= 2)
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert [call["after_schedule_id"] for call in runtime.task_scheduler_calls[:2]] == [
        None,
        "schedule-a",
    ]
    assert [event["next_interval_ms"] for event in events[:2]] == [1, 2]


def test_task_scheduler_maintenance_loop_carries_orphan_cursor_until_scan_completes() -> None:
    runtime = _TaskSchedulerRuntime(
        (
            _task_scheduler_pump_report(
                orphan_tasks_scanned=1,
                orphan_next_cursor="schedule-a",
            ),
            _task_scheduler_pump_report(),
        )
    )
    lines: list[str] = []

    loop = TaskSchedulerMaintenanceLoop(
        runtime_factory=lambda: runtime,
        registry_factory=_TaskSchedulerRegistry,
        options=TaskSchedulerStartupReconciliationOptions(
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
            schedule_page_limit=1,
            max_schedule_pages=1,
            max_claims=1,
            orphan_task_page_limit=1,
            claim_token_prefix="maint-a",
        ),
        interval_ms=1,
        max_interval_ms=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    _wait_until(lambda: len(runtime.task_scheduler_calls) >= 2)
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert [call["after_orphan_task_name"] for call in runtime.task_scheduler_calls[:2]] == [
        None,
        "schedule-a",
    ]
    assert [event["next_interval_ms"] for event in events[:2]] == [1, 2]


def test_task_scheduler_maintenance_loop_reports_sanitized_runtime_failure() -> None:
    lines: list[str] = []

    loop = TaskSchedulerMaintenanceLoop(
        runtime_factory=lambda: (_ for _ in ()).throw(RuntimeError("detail")),
        registry_factory=_TaskSchedulerRegistry,
        options=TaskSchedulerStartupReconciliationOptions(
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
        ),
        interval_ms=1,
        max_interval_ms=4,
        output=lines.append,
        pipe_name="pipe-a",
    )

    loop.start()
    loop.stop()

    events = [json.loads(line) for line in lines]
    assert events == [
        {
            "cycle_trigger": "INTERVAL",
            "error_type": "RuntimeError",
            "event": "ENGINE_HOST_TASK_SCHEDULER_RECONCILIATION_FAILED",
            "next_interval_ms": None,
            "pipe_name": "pipe-a",
        }
    ]


def test_long_running_pipe_loop_stops_cleanly_when_interrupted() -> None:
    server = _FakePipeServer(interrupt_on_call=4)

    result = serve_pipe_requests_until_interrupted(server)

    assert result.completed is True
    assert result.error_type is None
    assert result.stop_reason == "INTERRUPTED"
    assert result.served_requests == 3
    assert server.calls == 4


def test_long_running_pipe_loop_runs_after_request_until_interrupted() -> None:
    server = _FakePipeServer(interrupt_on_call=3)
    callbacks: list[int] = []

    result = serve_pipe_requests_until_interrupted(
        server,
        after_request=lambda: callbacks.append(server.calls),
    )

    assert result.completed is True
    assert result.stop_reason == "INTERRUPTED"
    assert result.served_requests == 2
    assert callbacks == [1, 2]


def test_long_running_pipe_loop_reports_sanitized_failure() -> None:
    server = _FakePipeServer(fail_on_call=3)

    result = serve_pipe_requests_until_interrupted(server)

    assert result.completed is False
    assert result.error_type == "RuntimeError"
    assert result.stop_reason == "SERVER_ERROR"
    assert result.served_requests == 2
    assert server.calls == 3


def test_engine_host_run_uses_long_running_pipe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_win32_pipe = types.ModuleType("mediasync_home.ipc.win32_named_pipe")
    runtime = _FakeRuntime()
    lines: list[str] = []

    class FakeWin32NamedPipeServer(_FakePipeServer):
        def __init__(self, *, pipe_name: str, service: object) -> None:
            super().__init__(interrupt_on_call=3)
            self.pipe_name = pipe_name
            self.service = service

    fake_win32_pipe.Win32NamedPipeServer = FakeWin32NamedPipeServer
    fake_win32_pipe.current_user_policy = _authorization
    monkeypatch.setitem(sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe)
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(engine_host_module, "current_process_runtime_policy", lambda root: None)
    monkeypatch.setattr(engine_host_module, "build_engine_host_runtime", lambda **kwargs: runtime)

    code = run_engine_host(["--pipe-name", "pipe-a", "--serve-forever"], emit=lines.append)

    events = [json.loads(line) for line in lines]
    assert code == 0
    assert runtime.closed is True
    assert events[0]["event"] == "ENGINE_HOST_PIPE_STARTING"
    assert events[0]["serve_forever"] is True
    assert events[0]["serve_requests"] == 1
    assert events[-1] == {
        "event": "ENGINE_HOST_PIPE_STOPPED",
        "pipe_name": "pipe-a",
        "served_requests": 2,
        "stop_reason": "INTERRUPTED",
    }


def test_engine_host_run_emits_executor_cycle_after_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_win32_pipe = types.ModuleType("mediasync_home.ipc.win32_named_pipe")
    runtime = _FakeRuntime()
    lines: list[str] = []

    class FakeWin32NamedPipeServer(_FakePipeServer):
        def __init__(self, *, pipe_name: str, service: object) -> None:
            super().__init__()
            self.pipe_name = pipe_name
            self.service = service

    fake_win32_pipe.Win32NamedPipeServer = FakeWin32NamedPipeServer
    fake_win32_pipe.current_user_policy = _authorization
    monkeypatch.setitem(sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe)
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(engine_host_module, "current_process_runtime_policy", lambda root: None)
    monkeypatch.setattr(engine_host_module, "build_engine_host_runtime", lambda **kwargs: runtime)

    code = run_engine_host(
        [
            "--pipe-name",
            "pipe-a",
            "--state-root",
            str(tmp_path),
            "--run-executor-cycle-after-request",
            "--run-executor-cycle-max-steps",
            "7",
        ],
        emit=lines.append,
    )

    events = [json.loads(line) for line in lines]
    assert code == 0
    assert runtime.cycle_max_steps == [7]
    assert [event["event"] for event in events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_RUN_EXECUTOR_CYCLE",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert events[0]["run_executor_cycle_after_request"] is True
    assert events[0]["run_executor_cycle_max_steps"] == 7
    assert events[1]["run_executor_cycle"] == {
        "last_step": {
            "action": "IDLE",
            "advanced": False,
            "idle": True,
            "next_action": "No runnable work.",
            "run_id": None,
            "run_target_id": None,
            "validation_codes": [],
        },
        "next_action": "No runnable work.",
        "steps_attempted": 1,
        "stopped_reason": "IDLE",
        "validation_codes": [],
    }
    assert events[1]["cycle_trigger"] == "AFTER_REQUEST"


def test_engine_host_parser_accepts_optional_state_root_and_inactive_outbox_owner(
    tmp_path: Path,
) -> None:
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--state-root",
            str(tmp_path),
            "--host-mutex-name",
            "Local\\MediaSyncHome-0B-1234567890abcdef12345678",
            "--publish-host-locator",
            "--inactive-outbox-owner-instance-id",
            "host-old",
            "--inactive-external-resource-owner-instance-id",
            "scheduler-old",
        ]
    )

    assert args.state_root == tmp_path
    assert args.host_mutex_name == "Local\\MediaSyncHome-0B-1234567890abcdef12345678"
    assert args.publish_host_locator is True
    assert args.inactive_outbox_owner_instance_id == ["host-old"]
    assert args.inactive_external_resource_owner_instance_id == ["scheduler-old"]


def test_engine_host_parser_accepts_bounded_task_scheduler_startup_pump(
    tmp_path: Path,
) -> None:
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--state-root",
            str(tmp_path),
            "--reconcile-task-scheduler-resources",
            "--task-scheduler-executable-path",
            TASK_SCHEDULER_EXECUTABLE,
            "--task-scheduler-schedule-page-limit",
            "5",
            "--task-scheduler-max-schedule-pages",
            "2",
            "--task-scheduler-max-claims",
            "7",
            "--task-scheduler-orphan-task-page-limit",
            "9",
            "--task-scheduler-claim-ttl-ms",
            "12000",
            "--task-scheduler-claim-token-prefix",
            "startup-a",
            "--task-scheduler-reconciliation-interval-ms",
            "300",
            "--task-scheduler-reconciliation-max-interval-ms",
            "1200",
        ]
    )

    assert args.reconcile_task_scheduler_resources is True
    assert args.task_scheduler_backend == "com"
    assert args.task_scheduler_executable_path == TASK_SCHEDULER_EXECUTABLE
    assert args.task_scheduler_schedule_page_limit == 5
    assert args.task_scheduler_max_schedule_pages == 2
    assert args.task_scheduler_max_claims == 7
    assert args.task_scheduler_orphan_task_page_limit == 9
    assert args.task_scheduler_claim_ttl_ms == 12000
    assert args.task_scheduler_claim_token_prefix == "startup-a"
    assert args.task_scheduler_reconciliation_interval_ms == 300
    assert args.task_scheduler_reconciliation_max_interval_ms == 1200


def test_engine_host_infers_inactive_scheduler_maintenance_owner_with_mutex(
    tmp_path: Path,
) -> None:
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--state-root",
            str(tmp_path),
            "--installation-id",
            "install-a",
            "--host-mutex-name",
            "Local\\MediaSyncHome-0B-1234567890abcdef12345678",
            "--task-scheduler-executable-path",
            TASK_SCHEDULER_EXECUTABLE,
            "--task-scheduler-reconciliation-interval-ms",
            "300",
        ]
    )

    assert engine_host_module._inactive_external_resource_owner_instance_ids(args) == (
        "install-a-task-scheduler-maintenance",
    )


def test_engine_host_runtime_without_state_root_preserves_non_persistent_service() -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
    )

    try:
        assert runtime.state_layout is None
        assert runtime.startup_reconciliation is None
        assert runtime.catalog_connection is None
        assert runtime.recovery_connection is None
        assert runtime.service.command_receipt_store is None
        assert runtime.service.outbox_store is None
    finally:
        runtime.close()


def test_engine_host_runtime_state_root_initializes_sqlite_and_persists_receipts(
    tmp_path: Path,
) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        reconciler_instance_id="host-new",
    )

    try:
        assert runtime.state_layout is not None
        assert runtime.state_layout.catalog.is_file()
        assert runtime.state_layout.recovery.is_file()
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        assert runtime.service.job_draft_store is not None
        assert runtime.service.standard_backup_job_read_store is not None
        assert runtime.service.standard_backup_job_detail_store is not None
        assert runtime.service.snapshot_entry_read_store is not None
        assert runtime.service.snapshot_coverage_read_store is not None
        assert runtime.service.snapshot_issue_read_store is not None
        assert runtime.service.plan_store is not None
        assert runtime.service.plan_operation_read_store is not None
        assert runtime.service.plan_endpoint_read_store is not None
        assert runtime.service.run_activity_read_store is not None
        assert runtime.service.schedule_store is not None
        assert runtime.service.trigger_occurrence_store is not None
        assert runtime.service.external_resource_state_store is not None
        assert runtime.reconciler_instance_id == "host-new"
        assert runtime.run_executor_lease_authority is not None
        assert runtime.run_executor_catalog_handoff_store is not None
        assert runtime.run_executor_staging_transfer_port is not None
        assert runtime.run_executor_final_commit_port is not None
        assert (
            runtime.run_executor_old_target_preservation_port
            is runtime.run_executor_final_commit_port
        )
        assert (
            runtime.run_executor_recovery_object_cleanup_port
            is runtime.run_executor_final_commit_port
        )
        assert current_schema_version(runtime.catalog_connection, SqliteStore.CATALOG) == 21
        assert current_schema_version(runtime.recovery_connection, SqliteStore.RECOVERY) == 5
        assert runtime.startup_reconciliation is not None
        assert runtime.startup_reconciliation.reconciler_instance_id == "host-new"
        assert runtime.startup_reconciliation.recovery_operations is not None
        assert runtime.startup_reconciliation.recovery_operations.scanned == 0
        assert runtime.startup_reconciliation.recovery_resume is not None
        assert runtime.startup_reconciliation.recovery_resume.scanned == 0
        assert runtime.startup_reconciliation.skipped_outbox_requeue_reason == (
            "OUTBOX_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF"
        )
        assert runtime.startup_reconciliation.skipped_external_resource_requeue_reason == (
            "EXTERNAL_RESOURCE_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF"
        )

        ipc_client = InProcessIpcClient(
            service=runtime.service,
            identity=_identity(),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        assert ipc_client.connect().status is IpcStatus.ACCEPTED
        runtime.service.job_draft_store.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-a")
            .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
            .with_added_target(name="USB 1", path_label="E:/Backup")
        )

        overview = ipc_client.query_backup_overview(draft_id="draft-a")
        backup_job_detail = ipc_client.query_backup_job_detail(job_id="job-a")
        activity = ipc_client.query_activity_overview(limit=5)
        plan_operations = ipc_client.query_plan_operations(plan_id="plan-a", limit=5)
        plan_endpoints = ipc_client.query_plan_endpoints(plan_id="plan-a", limit=5)
        snapshot_entries = ipc_client.query_snapshot_entries(snapshot_id="snapshot-a", limit=5)
        snapshot_coverage = ipc_client.query_snapshot_coverage(
            snapshot_id="snapshot-a",
            limit=5,
        )
        snapshot_issues = ipc_client.query_snapshot_issues(snapshot_id="snapshot-a", limit=5)

        response = ipc_client.submit_command(
            "UNKNOWN_MUTATION",
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
        )

        row = runtime.catalog_connection.execute(
            """
            SELECT state, rejection_reason
            FROM command_receipts
            WHERE idempotency_key = ?
            """,
            ("66666666-6666-4666-8666-666666666666",),
        ).fetchone()
        assert overview.status is IpcStatus.ACCEPTED
        assert overview.payload["backup_overview"]["read_model_available"] is True
        assert overview.payload["backup_overview"]["draft"]["can_create"] is True
        assert backup_job_detail.status is IpcStatus.ACCEPTED
        assert backup_job_detail.payload["backup_job_detail"]["read_model_available"] is True
        assert backup_job_detail.payload["backup_job_detail"]["found"] is False
        assert backup_job_detail.payload["backup_job_detail"]["job"] is None
        assert activity.status is IpcStatus.ACCEPTED
        assert activity.payload["activity_overview"]["read_model_available"] is True
        assert activity.payload["activity_overview"]["limit"] == 5
        assert activity.payload["activity_overview"]["runs"] == []
        assert plan_operations.status is IpcStatus.ACCEPTED
        assert plan_operations.payload["plan_operations"]["read_model_available"] is True
        assert plan_operations.payload["plan_operations"]["limit"] == 5
        assert plan_operations.payload["plan_operations"]["operations"] == []
        assert plan_endpoints.status is IpcStatus.ACCEPTED
        assert plan_endpoints.payload["plan_endpoints"]["read_model_available"] is True
        assert plan_endpoints.payload["plan_endpoints"]["limit"] == 5
        assert plan_endpoints.payload["plan_endpoints"]["endpoints"] == []
        assert snapshot_entries.status is IpcStatus.ACCEPTED
        assert snapshot_entries.payload["snapshot_entries"]["read_model_available"] is True
        assert snapshot_entries.payload["snapshot_entries"]["limit"] == 5
        assert snapshot_entries.payload["snapshot_entries"]["entries"] == []
        assert snapshot_coverage.status is IpcStatus.ACCEPTED
        assert snapshot_coverage.payload["snapshot_coverage"]["read_model_available"] is True
        assert snapshot_coverage.payload["snapshot_coverage"]["limit"] == 5
        assert snapshot_coverage.payload["snapshot_coverage"]["coverage"] == []
        assert snapshot_issues.status is IpcStatus.ACCEPTED
        assert snapshot_issues.payload["snapshot_issues"]["read_model_available"] is True
        assert snapshot_issues.payload["snapshot_issues"]["limit"] == 5
        assert snapshot_issues.payload["snapshot_issues"]["issues"] == []
        assert response.status is IpcStatus.REJECTED
        assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
        assert row == (
            "REJECTED",
            IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
    finally:
        runtime.close()


def test_engine_host_runtime_can_select_robocopy_staging_backend(tmp_path: Path) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        run_executor_staging_backend="robocopy",
    )

    try:
        assert isinstance(
            runtime.run_executor_staging_transfer_port,
            RobocopyStagingTransferAdapter,
        )
    finally:
        runtime.close()


def test_engine_host_runtime_stages_and_reconciles_task_scheduler_resources(
    tmp_path: Path,
) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        installation_id="install-a",
        reconciler_instance_id="host-new",
    )

    try:
        schedule = _task_scheduler_schedule()
        assert runtime.service.schedule_store is not None
        assert runtime.service.external_resource_state_store is not None
        assert runtime.catalog_connection is not None
        _insert_task_scheduler_plan_parent_rows(runtime.catalog_connection)
        runtime.service.schedule_store.save_schedule(schedule)

        definition = build_same_user_task_scheduler_definition(
            schedule,
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
        )
        report = runtime.task_scheduler_reconcile_resources_bounded(
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
            registry=_TaskSchedulerRegistry(_observed_task_scheduler_definition(definition)),
            schedule_page_limit=10,
            max_schedule_pages=1,
            max_claims=2,
            claim_token_prefix="pump-a",
        )
        stored_resource = runtime.service.external_resource_state_store.load_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id=schedule.schedule_id,
        )

        assert report.schedules_scanned == 1
        assert report.resources_staged == 1
        assert report.resources_reconciled == 1
        assert report.claims_attempted == 2
        assert report.claim_idle is True
        assert report.claim_findings[0].action is TaskSchedulerReconciliationAction.IN_SYNC
        assert report.claim_findings[0].completed is True
        assert stored_resource is not None
        assert stored_resource.state is ExternalResourceState.IN_SYNC
        assert stored_resource.observed_generation == schedule.definition_generation
        assert stored_resource.observed_hash == schedule.desired_definition_hash
        assert stored_resource.claim_token is None
    finally:
        runtime.close()


def test_engine_host_startup_task_scheduler_reconciliation_uses_injected_registry(
    tmp_path: Path,
) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        installation_id="install-a",
        reconciler_instance_id="host-new",
    )

    try:
        schedule = _task_scheduler_schedule()
        assert runtime.service.schedule_store is not None
        assert runtime.service.external_resource_state_store is not None
        assert runtime.catalog_connection is not None
        _insert_task_scheduler_plan_parent_rows(runtime.catalog_connection)
        runtime.service.schedule_store.save_schedule(schedule)

        definition = build_same_user_task_scheduler_definition(
            schedule,
            installation_id="install-a",
            executable_path=TASK_SCHEDULER_EXECUTABLE,
        )
        registry = _TaskSchedulerRegistry(_observed_task_scheduler_definition(definition))

        report = reconcile_task_scheduler_resources_for_engine_host_startup(
            runtime,
            options=TaskSchedulerStartupReconciliationOptions(
                installation_id="install-a",
                executable_path=TASK_SCHEDULER_EXECUTABLE,
                schedule_page_limit=10,
                max_schedule_pages=1,
                max_claims=2,
                claim_token_prefix="startup-a",
            ),
            registry=registry,
        )

        assert report.schedules_scanned == 1
        assert report.resources_staged == 1
        assert report.resources_reconciled == 1
        assert report.claims_attempted == 2
        assert report.claim_idle is True
        assert report.claim_findings[0].action is TaskSchedulerReconciliationAction.IN_SYNC
    finally:
        runtime.close()


def test_engine_host_runtime_releases_executor_leases_on_close(tmp_path: Path) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        reconciler_instance_id="host-new",
    )
    closed = False
    try:
        assert runtime.run_executor_lease_registry is not None
        lease = _FakeLiveLease()
        runtime.run_executor_lease_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            lease=lease,
        )

        runtime.close()
        closed = True

        assert lease.released is True
        assert runtime.run_executor_lease_registry.retained_count == 0
    finally:
        if not closed:
            runtime.close()


class _FakePipeServer:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        interrupt_on_call: int | None = None,
    ) -> None:
        self.calls = 0
        self._fail_on_call = fail_on_call
        self._interrupt_on_call = interrupt_on_call

    def serve_once(self) -> None:
        self.calls += 1
        if self.calls == self._interrupt_on_call:
            raise KeyboardInterrupt
        if self.calls == self._fail_on_call:
            raise RuntimeError("internal detail must not leak")


class _FakeLiveLease:
    lease_id = "lease-a"
    owner_installation_id = "owner-a"
    ownership_epoch = 1
    fencing_token = 42

    def __init__(self) -> None:
        self.released = False

    def issue_mutation_permit(self) -> object:
        raise AssertionError("permit issuance is not used by this test")

    def release(self) -> None:
        self.released = True


class _FakeRuntime:
    service = object()
    state_layout = None
    startup_reconciliation = None

    def __init__(self) -> None:
        self.closed = False
        self.cycle_max_steps: list[int] = []

    def close(self) -> None:
        self.closed = True

    def run_executor_cycle(self, *, max_steps: int) -> RunExecutorCyclePumpOutcome:
        self.cycle_max_steps.append(max_steps)
        step = RunExecutorCycleOutcome(
            action=RunExecutorCycleAction.IDLE,
            advanced=False,
            idle=True,
            run_id=None,
            run_target_id=None,
            validation_codes=(),
            next_action="No runnable work.",
        )
        return RunExecutorCyclePumpOutcome(
            steps_attempted=1,
            stopped_reason=RunExecutorPumpStopReason.IDLE,
            last_step=step,
            validation_codes=(),
            next_action="No runnable work.",
        )


class _ScriptedRuntime(_FakeRuntime):
    def __init__(self, stopped_reasons: tuple[RunExecutorPumpStopReason, ...]) -> None:
        super().__init__()
        self._stopped_reasons = list(stopped_reasons)
        self._last_stopped_reason = stopped_reasons[-1]

    def run_executor_cycle(self, *, max_steps: int) -> RunExecutorCyclePumpOutcome:
        self.cycle_max_steps.append(max_steps)
        stopped_reason = (
            self._stopped_reasons.pop(0)
            if self._stopped_reasons
            else self._last_stopped_reason
        )
        idle = stopped_reason is RunExecutorPumpStopReason.IDLE
        step = RunExecutorCycleOutcome(
            action=RunExecutorCycleAction.IDLE
            if idle
            else RunExecutorCycleAction.PREFLIGHT_LEASE_ACQUIRED,
            advanced=not idle,
            idle=idle,
            run_id=None if idle else "run-a",
            run_target_id=None if idle else "run-a-target-0000",
            validation_codes=(),
            next_action="No runnable work." if idle else "Continue executor work.",
        )
        return RunExecutorCyclePumpOutcome(
            steps_attempted=1,
            stopped_reason=stopped_reason,
            last_step=step,
            validation_codes=(),
            next_action=step.next_action,
        )


class _TaskSchedulerRuntime(_FakeRuntime):
    def __init__(self, reports: tuple[TaskSchedulerResourcePumpReport, ...]) -> None:
        super().__init__()
        self._reports = list(reports)
        self._last_report = reports[-1]
        self.task_scheduler_calls: list[dict[str, object]] = []

    def task_scheduler_reconcile_resources_bounded(
        self,
        *,
        installation_id: str,
        executable_path: str,
        registry: object,
        schedule_page_limit: int,
        max_schedule_pages: int,
        max_claims: int,
        claim_token_prefix: str | None = None,
        claim_ttl_ms: int = 30_000,
        after_schedule_id: str | None = None,
        orphan_task_page_limit: int = 100,
        after_orphan_task_name: str | None = None,
    ) -> TaskSchedulerResourcePumpReport:
        self.task_scheduler_calls.append(
            {
                "after_schedule_id": after_schedule_id,
                "after_orphan_task_name": after_orphan_task_name,
                "claim_token_prefix": claim_token_prefix,
                "claim_ttl_ms": claim_ttl_ms,
                "executable_path": executable_path,
                "installation_id": installation_id,
                "max_claims": max_claims,
                "max_schedule_pages": max_schedule_pages,
                "orphan_task_page_limit": orphan_task_page_limit,
                "registry": registry,
                "schedule_page_limit": schedule_page_limit,
            }
        )
        if self._reports:
            return self._reports.pop(0)
        return self._last_report


class _TaskSchedulerRegistry:
    def __init__(self, *observed: ObservedTaskSchedulerDefinition) -> None:
        self.observed = {definition.task_path: definition for definition in observed}
        self.applied: list[object] = []
        self.deleted: tuple[str, ...] = ()

    def load_task(self, task_path: str) -> ObservedTaskSchedulerDefinition | None:
        return self.observed.get(task_path)

    def list_tasks(
        self,
        folder_path: str,
        *,
        limit: int,
        after_task_name: str | None = None,
    ) -> tuple[ObservedTaskSchedulerDefinition, ...]:
        folder_prefix = folder_path.rstrip("\\") + "\\"
        tasks = sorted(
            (
                definition
                for definition in self.observed.values()
                if definition.task_path.startswith(folder_prefix)
            ),
            key=lambda definition: definition.task_path.rsplit("\\", 1)[-1],
        )
        if after_task_name is not None:
            tasks = [
                definition
                for definition in tasks
                if definition.task_path.rsplit("\\", 1)[-1] > after_task_name
            ]
        return tuple(tasks[:limit])

    def apply_task_definition(self, definition: object) -> None:
        self.applied.append(definition)

    def delete_task(self, task_path: str) -> None:
        self.deleted = (*self.deleted, task_path)
        self.observed.pop(task_path, None)


def _task_scheduler_pump_report(
    *,
    resources_reconciled: int = 0,
    stage_completed: bool = True,
    stage_next_cursor: str | None = None,
    orphan_tasks_scanned: int = 0,
    orphan_next_cursor: str | None = None,
) -> TaskSchedulerResourcePumpReport:
    return TaskSchedulerResourcePumpReport(
        schedule_pages_attempted=1,
        schedules_scanned=0 if stage_next_cursor is None else 1,
        resources_staged=0,
        stage_blocked=0,
        stage_completed=stage_completed,
        stage_next_cursor=stage_next_cursor,
        claims_attempted=1,
        resources_reconciled=resources_reconciled,
        resources_applied=0,
        resources_completed=resources_reconciled,
        resources_blocked=0,
        claim_idle=True,
        stage_findings=(),
        claim_findings=(),
        orphan_tasks_scanned=orphan_tasks_scanned,
        orphan_next_cursor=orphan_next_cursor,
    )


def _wait_until(predicate: Callable[[], bool], *, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for predicate")


def _task_scheduler_schedule() -> ScheduleDefinition:
    return bind_same_user_task_scheduler_definition_hash(
        ScheduleDefinition(
            schedule_id="schedule-a",
            job_id="job-a",
            plan_id="plan-a",
            plan_checksum="a" * 64,
            trigger_type=TriggerKind.SCHEDULED_TIME,
            configuration_json='{"kind":"daily"}',
            definition_generation=1,
            desired_definition_hash="0" * 64,
            time_zone_id="Europe/Oslo",
            dst_policy="PRESERVE_WALL_TIME",
            misfire_policy="QUEUE_ONCE",
            coalescing_window_seconds=60,
            task_logon_type="INTERACTIVE_TOKEN",
            requires_network=False,
            run_only_when_logged_on=True,
            enabled=True,
            row_version=1,
        ),
        installation_id="install-a",
        executable_path=TASK_SCHEDULER_EXECUTABLE,
    )


def _observed_task_scheduler_definition(
    definition: TaskSchedulerDefinition,
) -> ObservedTaskSchedulerDefinition:
    return ObservedTaskSchedulerDefinition(
        task_path=definition.task_path,
        executable_path=definition.executable_path,
        arguments=definition.arguments,
        enabled=definition.enabled,
        trigger_type=definition.trigger_type,
        configuration_json=definition.configuration_json,
        time_zone_id=definition.time_zone_id,
        task_logon_type=definition.task_logon_type,
        run_only_when_logged_on=definition.run_only_when_logged_on,
        requires_network=definition.requires_network,
        multiple_instances_policy=definition.multiple_instances_policy,
        execution_time_limit_seconds=definition.execution_time_limit_seconds,
        stop_on_execution_time_limit=definition.stop_on_execution_time_limit,
    )


def _insert_task_scheduler_plan_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
        """
    )
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute("INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')")
    connection.execute(
        """
        INSERT INTO plan_seal_details (
            plan_id,
            analysis_id,
            job_id,
            job_revision_id,
            planner_version,
            plan_schema_version,
            operation_schema_version,
            execution_policy,
            checksum_algorithm,
            serializer_version,
            plan_checksum,
            risk_summary_json,
            operation_count,
            planned_bytes
        )
        VALUES (
            'plan-a',
            'analysis-a',
            'job-a',
            'job-rev-a',
            'planner',
            1,
            1,
            'dry-run',
            'SHA-256',
            'canonical-json',
            ?,
            '{}',
            1,
            0
        )
        """,
        ("a" * 64,),
    )
    connection.commit()


def _authorization() -> ClientAuthorizationPolicy:
    return ClientAuthorizationPolicy(
        expected_user_sid_hash=EXPECTED_USER,
        expected_session_id=EXPECTED_SESSION,
    )


def _identity() -> VerifiedClientIdentity:
    return VerifiedClientIdentity(
        user_sid_hash=EXPECTED_USER,
        session_id=EXPECTED_SESSION,
        is_remote=False,
        transport="in-process-engine-host-runtime-test",
    )
