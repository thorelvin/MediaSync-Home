from __future__ import annotations

import json
import sqlite3
import sys
import time
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters import local_host_locator as local_host_locator_module
from mediasync_home.adapters.local_host_locator import (
    clear_stale_local_engine_host_publication,
    load_local_engine_host_publication,
    publish_local_engine_host_publication,
)
from mediasync_home.adapters.robocopy import RobocopyStagingTransferAdapter
from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteStore,
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigrationPlan,
    apply_sqlite_migrations,
    catalog_migration_plan,
    current_schema_version,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.state_backup import (
    SqliteStateCompactionEpochRecoveryReport,
    SqliteStateMaintenanceRetentionPolicy,
    SqliteStateRestoreEpochRecoveryReport,
    create_sqlite_state_backup_set,
)
from mediasync_home.application.external_resources import (
    ExternalResourceState,
    ExternalResourceType,
)
from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.backup_analysis import BackupAnalysisCommandName
from mediasync_home.application.host_locator import build_local_engine_host_publication
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.run_executor import RunExecutorPumpStopReason
from mediasync_home.application.run_executor_cycle import (
    RunExecutorCycleAction,
    RunExecutorCycleOutcome,
    RunExecutorCyclePumpOutcome,
)
from mediasync_home.application.runtime_status import (
    local_writable_status,
    startup_status,
)
from mediasync_home.application.state_maintenance import StateMaintenanceCommandName
from mediasync_home.application.state_capacity import (
    StateCapacityObservation,
    StateCapacityPolicy,
    StateCapacityStatus,
)
from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.task_scheduler import (
    TaskSchedulerDefinition,
    ObservedTaskSchedulerDefinition,
    TaskSchedulerResourcePumpReport,
    TaskSchedulerReconciliationAction,
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
)
from mediasync_home.application.trigger_occurrences import TriggerKind, payload_hash
from mediasync_home.composition import engine_host as engine_host_module
from mediasync_home.composition.engine_host import (
    EngineHostStateCompactionNotAdmitted,
    EngineHostStateRetentionNotAdmitted,
    EngineHostStateRestoreNotAdmitted,
    ExecutorMaintenanceLoop,
    HostLocatorHeartbeatLoop,
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
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcReason, IpcStatus


EXPECTED_USER = "same-user"
EXPECTED_SESSION = 42
TASK_SCHEDULER_EXECUTABLE = r"C:\Program Files\MediaSync Home\MediaSyncHome.exe"
HOST_LOCATOR_KEY = "1234567890abcdef12345678"
HOST_LOCATOR_PIPE = f"MediaSyncHome-0B-{HOST_LOCATOR_KEY}"
HOST_LOCATOR_MUTEX = f"Local\\MediaSyncHome-0B-{HOST_LOCATOR_KEY}"


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


def test_engine_host_parser_accepts_explicit_local_writable_mode() -> None:
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--state-root",
            "C:/MediaSyncState",
            "--enable-local-mutations",
        ]
    )

    assert args.enable_local_mutations is True
    assert args.state_root == Path("C:/MediaSyncState")
    assert args.run_executor_staging_backend == "robocopy"


def test_engine_host_rejects_local_writable_mode_without_state_root() -> None:
    with pytest.raises(RuntimeError, match="LOCAL_MUTATIONS_REQUIRE_STATE_ROOT"):
        run_engine_host(
            [
                "--pipe-name",
                "pipe-a",
                "--enable-local-mutations",
            ]
        )


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


def test_task_scheduler_maintenance_loop_runs_interval_reconciliation_and_closes_runtime() -> (
    None
):
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


def test_task_scheduler_maintenance_loop_carries_stage_cursor_until_scan_completes() -> (
    None
):
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


def test_task_scheduler_maintenance_loop_carries_orphan_cursor_until_scan_completes() -> (
    None
):
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
    assert [
        call["after_orphan_task_name"] for call in runtime.task_scheduler_calls[:2]
    ] == [
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


def test_host_locator_heartbeat_loop_tracks_refreshed_publication_for_cleanup(
    tmp_path: Path,
) -> None:
    publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name=HOST_LOCATOR_PIPE,
        mutex_name=HOST_LOCATOR_MUTEX,
        state_root=tmp_path / "state",
        process_id=1111,
        heartbeat_utc="2026-07-30T10:11:55.000Z",
    )
    publish_local_engine_host_publication(publication)
    loop = HostLocatorHeartbeatLoop(
        publication=publication,
        heartbeat_clock=lambda: "2026-07-30T10:12:00.000Z",
    )

    assert loop.tick() is True
    assert loop.publication.heartbeat_utc == "2026-07-30T10:12:00.000Z"
    assert load_local_engine_host_publication(tmp_path / "state") == loop.publication
    assert clear_stale_local_engine_host_publication(publication) is False
    assert clear_stale_local_engine_host_publication(loop.publication) is True
    assert load_local_engine_host_publication(tmp_path / "state") is None


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


def test_engine_host_run_uses_long_running_pipe_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setitem(
        sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe
    )
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(
        engine_host_module, "current_process_runtime_policy", lambda root: None
    )
    monkeypatch.setattr(
        engine_host_module, "build_engine_host_runtime", lambda **kwargs: runtime
    )

    code = run_engine_host(
        ["--pipe-name", "pipe-a", "--serve-forever"], emit=lines.append
    )

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


def test_engine_host_startup_failure_publishes_no_locator_or_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_win32_pipe = types.ModuleType("mediasync_home.ipc.win32_named_pipe")
    published: list[object] = []
    lines: list[str] = []

    fake_win32_pipe.Win32NamedPipeServer = _FakePipeServer
    fake_win32_pipe.current_user_policy = _authorization
    monkeypatch.setitem(
        sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe
    )
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(
        engine_host_module, "current_process_runtime_policy", lambda root: None
    )
    monkeypatch.setattr(
        engine_host_module,
        "build_engine_host_runtime",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("migration failed")),
    )
    monkeypatch.setattr(
        engine_host_module,
        "_publish_local_host_locator",
        lambda **kwargs: published.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        run_engine_host(
            [
                "--pipe-name",
                HOST_LOCATOR_PIPE,
                "--state-root",
                str(tmp_path / "state"),
                "--publish-host-locator",
            ],
            emit=lines.append,
        )

    assert published == []
    assert lines == []


def test_engine_host_run_clears_own_host_locator_on_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_win32_pipe = types.ModuleType("mediasync_home.ipc.win32_named_pipe")
    runtime = _FakeRuntime()
    host_mutex = _FakeHostMutex(HOST_LOCATOR_MUTEX)
    state_root = tmp_path / "state"
    lines: list[str] = []

    class FakeWin32NamedPipeServer(_FakePipeServer):
        def __init__(self, *, pipe_name: str, service: object) -> None:
            super().__init__()
            self.pipe_name = pipe_name
            self.service = service

    fake_win32_pipe.Win32NamedPipeServer = FakeWin32NamedPipeServer
    fake_win32_pipe.current_user_policy = _authorization
    monkeypatch.setitem(
        sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe
    )
    monkeypatch.setattr(
        local_host_locator_module, "LocalReparseGuard", _PermissiveReparseGuard
    )
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(engine_host_module.os, "getpid", lambda: 1111)
    monkeypatch.setattr(
        engine_host_module, "current_process_runtime_policy", lambda root: None
    )
    monkeypatch.setattr(
        engine_host_module, "build_engine_host_runtime", lambda **kwargs: runtime
    )
    monkeypatch.setattr(
        engine_host_module, "_acquire_host_mutex", lambda *args, **kwargs: host_mutex
    )

    code = run_engine_host(
        [
            "--pipe-name",
            HOST_LOCATOR_PIPE,
            "--state-root",
            str(state_root),
            "--host-mutex-name",
            HOST_LOCATOR_MUTEX,
            "--publish-host-locator",
        ],
        emit=lines.append,
    )

    events = [json.loads(line) for line in lines]
    assert code == 0
    assert runtime.closed is True
    assert host_mutex.closed is True
    assert events[0]["host_locator"]["process_id"] == 1111
    assert events[0]["host_locator_path"] == str(
        state_root / "engine-host.locator.json"
    )
    assert load_local_engine_host_publication(state_root) is None


def test_engine_host_run_preserves_replaced_host_locator_on_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_win32_pipe = types.ModuleType("mediasync_home.ipc.win32_named_pipe")
    runtime = _FakeRuntime()
    host_mutex = _FakeHostMutex(HOST_LOCATOR_MUTEX)
    state_root = tmp_path / "state"
    newer_publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name=HOST_LOCATOR_PIPE,
        mutex_name=HOST_LOCATOR_MUTEX,
        state_root=state_root,
        process_id=2222,
    )

    class FakeWin32NamedPipeServer(_FakePipeServer):
        def __init__(self, *, pipe_name: str, service: object) -> None:
            super().__init__()
            self.pipe_name = pipe_name
            self.service = service

        def serve_once(self) -> None:
            publish_local_engine_host_publication(newer_publication)
            super().serve_once()

    fake_win32_pipe.Win32NamedPipeServer = FakeWin32NamedPipeServer
    fake_win32_pipe.current_user_policy = _authorization
    monkeypatch.setitem(
        sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe
    )
    monkeypatch.setattr(
        local_host_locator_module, "LocalReparseGuard", _PermissiveReparseGuard
    )
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(engine_host_module.os, "getpid", lambda: 1111)
    monkeypatch.setattr(
        engine_host_module, "current_process_runtime_policy", lambda root: None
    )
    monkeypatch.setattr(
        engine_host_module, "build_engine_host_runtime", lambda **kwargs: runtime
    )
    monkeypatch.setattr(
        engine_host_module, "_acquire_host_mutex", lambda *args, **kwargs: host_mutex
    )

    code = run_engine_host(
        [
            "--pipe-name",
            HOST_LOCATOR_PIPE,
            "--state-root",
            str(state_root),
            "--host-mutex-name",
            HOST_LOCATOR_MUTEX,
            "--publish-host-locator",
        ],
    )

    assert code == 0
    assert load_local_engine_host_publication(state_root) == newer_publication


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
    monkeypatch.setitem(
        sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_win32_pipe
    )
    monkeypatch.setattr(engine_host_module.os, "name", "nt")
    monkeypatch.setattr(
        engine_host_module, "current_process_runtime_policy", lambda root: None
    )
    monkeypatch.setattr(
        engine_host_module, "build_engine_host_runtime", lambda **kwargs: runtime
    )

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


def test_engine_host_runtime_without_state_root_preserves_non_persistent_service() -> (
    None
):
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
    )

    try:
        assert runtime.state_layout is None
        assert runtime.state_restore_recovery is None
        assert runtime.state_restore_startup_reconciliation is None
        assert runtime.state_compaction_recovery is None
        assert runtime.state_migration is None
        assert runtime.installation_state is None
        assert runtime.startup_reconciliation is None
        assert runtime.catalog_connection is None
        assert runtime.recovery_connection is None
        assert runtime.service.command_receipt_store is None
        assert runtime.service.outbox_store is None
        with pytest.raises(RuntimeError, match="STATE_RESTORE_RUNTIME_NOT_CONFIGURED"):
            runtime.admit_state_restore_maintenance()
    finally:
        runtime.close()


def test_engine_host_runtime_migration_failure_opens_no_repository_connections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    connect_calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        engine_host_module,
        "migrate_sqlite_state_stores",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("migration failed")),
    )
    monkeypatch.setattr(
        engine_host_module,
        "sqlite3",
        types.SimpleNamespace(
            connect=lambda *args, **kwargs: connect_calls.append(args),
        ),
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        build_engine_host_runtime(
            authorization=_authorization(),
            service_status=startup_status(ProcessRole.ENGINE_HOST),
            state_root=tmp_path / "state",
        )

    assert connect_calls == []


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
        assert runtime.state_restore_recovery is not None
        assert runtime.state_restore_recovery.scanned_epoch_count == 0
        assert runtime.state_restore_startup_reconciliation is not None
        assert runtime.state_restore_startup_reconciliation.scanned_epoch_count == 0
        assert (
            runtime.state_restore_startup_reconciliation.latest_committed_epoch is None
        )
        assert runtime.state_compaction_recovery is not None
        assert runtime.state_compaction_recovery.scanned_epoch_count == 0
        assert runtime.state_migration is not None
        assert runtime.state_migration.migration_performed
        assert runtime.state_migration.created_epoch_count == 1
        assert runtime.state_migration.resumed_epoch_count == 0
        assert len(runtime.state_migration.committed_epoch_ids) == 1
        assert runtime.state_migration.latest_backup_set_path is None
        assert runtime.state_layout.catalog.is_file()
        assert runtime.state_layout.recovery.is_file()
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        assert runtime.installation_state is not None
        assert runtime.installation_state.product_channel == "local-preview"
        assert runtime.installation_state.catalog_schema_version == 45
        assert runtime.installation_state.recovery_schema_version == 11
        assert runtime.installation_state.ipc_protocol_major == 1
        assert runtime.snapshot_materialization_refresh is not None
        assert runtime.snapshot_materialization_refresh.scanned_job_count == 0
        assert runtime.initial_backup_plan_refresh is not None
        assert runtime.initial_backup_plan_refresh.sealed_plan_count == 0
        assert runtime.service.job_draft_store is not None
        assert runtime.service.standard_backup_job_read_store is not None
        assert runtime.service.standard_backup_job_detail_store is not None
        assert runtime.service.standard_backup_job_endpoint_registrar is not None
        assert runtime.service.job_snapshot_refresh is not None
        assert runtime.service.initial_backup_plan_refresh is not None
        assert runtime.service.snapshot_entry_read_store is not None
        assert runtime.service.snapshot_coverage_read_store is not None
        assert runtime.service.snapshot_issue_read_store is not None
        assert runtime.service.plan_store is not None
        assert runtime.service.plan_operation_read_store is not None
        assert runtime.service.plan_endpoint_read_store is not None
        assert runtime.service.history_timeline_read_store is not None
        assert runtime.service.run_activity_read_store is not None
        assert runtime.service.run_progress_snapshot_store is not None
        assert runtime.service.schedule_store is not None
        assert runtime.service.trigger_occurrence_store is not None
        assert runtime.service.external_resource_state_store is not None
        assert runtime.service.command_effect_transaction is not None
        assert runtime.reconciler_instance_id == "host-new"
        assert runtime.run_executor_lease_authority is not None
        assert runtime.run_executor_catalog_handoff_store is not None
        assert runtime.run_executor_staging_transfer_port is not None
        assert runtime.run_executor_final_commit_port is not None
        assert runtime.version_retention_store is not None
        assert runtime.version_retention_recovery_references is not None
        assert runtime.version_retention_lease_authority is not None
        assert runtime.version_retention_deletion_port is not None
        assert (
            runtime.run_executor_old_target_preservation_port
            is runtime.run_executor_final_commit_port
        )
        assert (
            runtime.run_executor_recovery_object_cleanup_port
            is runtime.run_executor_final_commit_port
        )
        assert (
            current_schema_version(runtime.catalog_connection, SqliteStore.CATALOG)
            == 45
        )
        assert (
            current_schema_version(runtime.recovery_connection, SqliteStore.RECOVERY)
            == 11
        )
        retention = runtime.run_version_retention_cycle()
        assert retention.planning.plan is None
        assert retention.planning.scanned == 0
        assert retention.apply.idle is True
        assert runtime.startup_reconciliation is not None
        assert runtime.startup_reconciliation.reconciler_instance_id == "host-new"
        assert runtime.startup_reconciliation.recovery_operations is not None
        assert runtime.startup_reconciliation.recovery_operations.scanned == 0
        assert runtime.startup_reconciliation.recovery_resume is not None
        assert runtime.startup_reconciliation.recovery_resume.scanned == 0
        assert runtime.startup_reconciliation.skipped_outbox_requeue_reason == (
            "OUTBOX_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF"
        )
        assert (
            runtime.startup_reconciliation.skipped_external_resource_requeue_reason
            == ("EXTERNAL_RESOURCE_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF")
        )
        admission = runtime.admit_state_restore_maintenance()
        assert admission.admitted is True
        assert admission.blockers == ()

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
        history = ipc_client.query_history_timeline(limit=5)
        operation_audit = ipc_client.query_operation_audit(
            run_id="run-a",
            operation_id="op-a",
            limit=5,
        )
        plan_operations = ipc_client.query_plan_operations(plan_id="plan-a", limit=5)
        plan_endpoints = ipc_client.query_plan_endpoints(plan_id="plan-a", limit=5)
        snapshot_entries = ipc_client.query_snapshot_entries(
            snapshot_id="snapshot-a", limit=5
        )
        snapshot_coverage = ipc_client.query_snapshot_coverage(
            snapshot_id="snapshot-a",
            limit=5,
        )
        snapshot_issues = ipc_client.query_snapshot_issues(
            snapshot_id="snapshot-a", limit=5
        )

        assert operation_audit.status is IpcStatus.ACCEPTED
        assert operation_audit.payload["operation_audit"]["read_model_available"] is True
        assert operation_audit.payload["operation_audit"]["found"] is False

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
        assert (
            backup_job_detail.payload["backup_job_detail"]["read_model_available"]
            is True
        )
        assert backup_job_detail.payload["backup_job_detail"]["found"] is False
        assert backup_job_detail.payload["backup_job_detail"]["job"] is None
        assert activity.status is IpcStatus.ACCEPTED
        assert activity.payload["activity_overview"]["read_model_available"] is True
        assert activity.payload["activity_overview"]["limit"] == 5
        assert activity.payload["activity_overview"]["runs"] == []
        assert history.status is IpcStatus.ACCEPTED
        assert history.payload["history_timeline"]["read_model_available"] is True
        assert history.payload["history_timeline"]["limit"] == 5
        assert history.payload["history_timeline"]["activities"] == []
        assert plan_operations.status is IpcStatus.ACCEPTED
        assert (
            plan_operations.payload["plan_operations"]["read_model_available"] is True
        )
        assert plan_operations.payload["plan_operations"]["limit"] == 5
        assert plan_operations.payload["plan_operations"]["operations"] == []
        assert plan_endpoints.status is IpcStatus.ACCEPTED
        assert plan_endpoints.payload["plan_endpoints"]["read_model_available"] is True
        assert plan_endpoints.payload["plan_endpoints"]["limit"] == 5
        assert plan_endpoints.payload["plan_endpoints"]["endpoints"] == []
        assert snapshot_entries.status is IpcStatus.ACCEPTED
        assert (
            snapshot_entries.payload["snapshot_entries"]["read_model_available"] is True
        )
        assert snapshot_entries.payload["snapshot_entries"]["limit"] == 5
        assert snapshot_entries.payload["snapshot_entries"]["entries"] == []
        assert snapshot_coverage.status is IpcStatus.ACCEPTED
        assert (
            snapshot_coverage.payload["snapshot_coverage"]["read_model_available"]
            is True
        )
        assert snapshot_coverage.payload["snapshot_coverage"]["limit"] == 5
        assert snapshot_coverage.payload["snapshot_coverage"]["coverage"] == []
        assert snapshot_issues.status is IpcStatus.ACCEPTED
        assert (
            snapshot_issues.payload["snapshot_issues"]["read_model_available"] is True
        )
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


def test_engine_host_hard_capacity_stop_is_published_and_starts_no_executor_step(
    tmp_path: Path,
) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        state_capacity_policy=StateCapacityPolicy(
            soft_quota_bytes=1,
            hard_stop_quota_bytes=2,
            minimum_free_space_bytes=0,
            internal_backup_reserve_bytes=0,
        ),
        state_capacity_probe=_FixedStateCapacityProbe(
            StateCapacityObservation(
                state_size_bytes=2,
                local_free_space_bytes=1_000_000_000,
                measurement_complete=True,
                scanned_entry_count=0,
            )
        ),
    )

    try:
        ipc_client = InProcessIpcClient(
            service=runtime.service,
            identity=_identity(),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555557",
        )
        handshake = ipc_client.connect()
        outcome = runtime.run_executor_cycle(max_steps=1)

        assert runtime.state_capacity_report is not None
        assert runtime.state_capacity_report.status is StateCapacityStatus.HARD_STOP
        assert handshake.payload["state_capacity"]["status"] == "HARD_STOP"
        assert outcome.steps_attempted == 0
        assert outcome.stopped_reason is RunExecutorPumpStopReason.BLOCKED
        assert outcome.validation_codes == ("STATE_CAPACITY_HARD_QUOTA",)
    finally:
        runtime.close()


def test_local_writable_runtime_creates_job_from_inline_gui_draft(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "Pictures"
    target_root = tmp_path / "Backup"
    source_root.mkdir()
    target_root.mkdir()
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
    )
    payload: dict[str, object] = {
        "draft_id": "draft-a",
        "draft": {
            "draft_id": "draft-a",
            "schema_version": 1,
            "source_name": "Pictures",
            "source_path_label": str(source_root),
            "targets": [
                {
                    "name": "USB 1",
                    "path_label": str(target_root),
                    "independent_device_id": None,
                }
            ],
        },
    }

    try:
        ipc_client = InProcessIpcClient(
            service=runtime.service,
            identity=_identity(),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        assert ipc_client.connect().status is IpcStatus.ACCEPTED

        response = ipc_client.submit_command(
            JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload=payload,
            payload_hash=canonical_command_payload_hash(payload),
        )
        overview = ipc_client.query_backup_overview(draft_id="draft-a")
        detail = ipc_client.query_backup_job_detail(
            job_id=str(response.payload["job"]["job_id"])
        )

        assert response.status is IpcStatus.ACCEPTED
        assert response.payload["created"] is True
        endpoint_bindings = response.payload["endpoint_bindings"]
        assert endpoint_bindings["source"]["root_uri"] == source_root.as_uri()
        assert endpoint_bindings["source"]["registration_state"] == "READ_ONLY_READY"
        assert endpoint_bindings["source"]["registration_reason_code"] == (
            "ENDPOINT_SOURCE_READ_ONLY_WITHOUT_CONTROL_AREA"
        )
        assert endpoint_bindings["targets"][0]["root_uri"] == target_root.as_uri()
        assert endpoint_bindings["targets"][0]["registration_state"] == "WRITABLE_READY"
        assert endpoint_bindings["targets"][0]["registration_reason_code"] == (
            "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
        )
        registration = response.payload["writable_endpoint_registration"]
        assert registration["completed"] is True
        assert registration["state"] == "COMMITTED"
        assert registration["registered_target_count"] == 1
        assert (target_root / ".mediasync" / "endpoint.json").is_file()
        assert response.payload["endpoint_classification_refresh"] == {
            "completed": True,
            "report": {
                "classified_endpoint_count": 2,
                "failed_endpoint_count": 0,
                "pending_binding_count": 0,
                "read_only_ready_binding_count": 1,
                "writable_ready_binding_count": 1,
                "blocked_binding_count": 0,
            },
        }
        snapshot_refresh = response.payload["job_snapshot_refresh"]
        assert snapshot_refresh["completed"] is True
        snapshot_report = snapshot_refresh["report"]
        assert snapshot_report["scanned_job_count"] == 1
        assert snapshot_report["reused_job_count"] == 0
        assert snapshot_report["blocked_job_count"] == 0
        assert snapshot_report["failed_job_count"] == 0
        assert snapshot_report["sealed_snapshot_count"] == 2
        assert snapshot_report["results"][0]["state"] == "SEALED"
        assert snapshot_report["results"][0]["reason_code"] == "JOB_SNAPSHOTS_SEALED"
        assert len(snapshot_report["results"][0]["snapshot_ids"]) == 2
        assert overview.payload["backup_overview"]["draft"]["source_name"] == "Pictures"
        assert (
            overview.payload["backup_overview"]["jobs"][0]["source_name"] == "Pictures"
        )
        detail_target = detail.payload["backup_job_detail"]["job"]["targets"][0]
        assert detail_target["registration_state"] == "WRITABLE_READY"
        assert detail_target["registration_reason_code"] == (
            "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
        )
        (source_root / "new.txt").write_text("new", encoding="utf-8")
        check_payload = {
            "job_id": str(response.payload["job"]["job_id"]),
            "start_when_safe": True,
        }
        check = ipc_client.submit_command(
            BackupAnalysisCommandName.CHECK_BACKUP.value,
            request_id="77777777-7777-4777-8777-777777777777",
            idempotency_key="88888888-8888-4888-8888-888888888888",
            payload=check_payload,
            payload_hash=canonical_command_payload_hash(check_payload),
        )
        checked = runtime.run_backup_analysis_cycle()
        refreshed_detail = ipc_client.query_backup_job_detail(
            job_id=str(response.payload["job"]["job_id"])
        )

        assert check.status is IpcStatus.ACCEPTED
        assert check.payload["analysis_request"]["state"] == "QUEUED"
        assert checked is not None
        assert checked.state.value == "SUCCEEDED", checked.reason_code
        assert checked.reason_code == "BACKUP_ANALYSIS_SAFE_RUN_QUEUED"
        assert checked.started_run_id is not None
        refreshed_job = refreshed_detail.payload["backup_job_detail"]["job"]
        assert refreshed_job["latest_analysis_request"]["state"] == "SUCCEEDED"
        assert (
            refreshed_job["latest_analysis_request"]["started_run_id"]
            == checked.started_run_id
        )
        assert refreshed_job["initial_plan"]["state"] == "SEALED"
        assert refreshed_job["initial_plan"]["operation_count"] == 1
        assert runtime.catalog_connection is not None
        executor_outcome = runtime.run_executor_cycle(max_steps=100)
        completed_run = runtime.run_executor_queue_store.load_started_run(
            checked.started_run_id
        )
        assert executor_outcome.steps_attempted > 0
        assert completed_run is not None
        assert completed_run.state.value == "COMPLETED", executor_outcome
        assert (target_root / "new.txt").read_text(encoding="utf-8") == "new"

        second_check_payload = {
            "job_id": str(response.payload["job"]["job_id"]),
            "start_when_safe": True,
        }
        second_check = ipc_client.submit_command(
            BackupAnalysisCommandName.CHECK_BACKUP.value,
            request_id="99999999-9999-4999-8999-999999999999",
            idempotency_key="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            payload=second_check_payload,
            payload_hash=canonical_command_payload_hash(second_check_payload),
        )
        second_checked = runtime.run_backup_analysis_cycle()

        assert second_check.status is IpcStatus.ACCEPTED
        assert second_checked is not None
        assert second_checked.state.value == "NO_CHANGES"
        assert second_checked.started_run_id is None
        assert runtime.catalog_connection.execute(
            "SELECT count(*) FROM runs"
        ).fetchone() == (1,)
        endpoint_count = runtime.catalog_connection.execute(
            "SELECT count(*) FROM endpoints"
        ).fetchone()
        binding_count = runtime.catalog_connection.execute(
            "SELECT count(*) FROM standard_backup_job_endpoint_bindings"
        ).fetchone()
        snapshot_materialization = runtime.catalog_connection.execute(
            """
            SELECT state, snapshot_count, sealed_snapshot_count
            FROM standard_backup_job_snapshot_materializations
            """
        ).fetchone()
        sealed_snapshot_count = runtime.catalog_connection.execute(
            """
            SELECT count(*)
            FROM snapshots
            WHERE complete = 1
                AND immutable = 1
            """
        ).fetchone()
        assert endpoint_count == (2,)
        assert binding_count == (4,)
        assert snapshot_materialization == ("SEALED", 2, 2)
        assert sealed_snapshot_count == (6,)
        assert not (source_root / ".mediasync").exists()
        assert (target_root / ".mediasync" / "endpoint.json").is_file()
    finally:
        runtime.close()


def test_engine_host_runtime_backfills_endpoint_bindings_for_existing_job(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    database = state_root / "catalog.sqlite"
    full_plan = catalog_migration_plan()
    legacy_plan = SqliteMigrationPlan(
        store=full_plan.store,
        migrations=full_plan.migrations[:22],
    )
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, legacy_plan)
        connection.execute(
            """
            INSERT INTO standard_backup_job_drafts (
                draft_id,
                schema_version,
                source_name,
                source_path_label,
                defaults_json,
                targets_json
            )
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            (
                "draft-backfill",
                "Pictures",
                "C:/Users/Ada/Pictures",
                json.dumps(
                    {
                        "behavior": "UPDATE_BACKUP",
                        "extra_files": "KEEP_ON_TARGET",
                        "file_selection": "ALL_USER_FILES",
                        "performance": "AUTO",
                        "retention": "THIRTY_DAYS",
                        "verification": "STANDARD",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    [
                        {
                            "independent_device_id": None,
                            "name": "USB 1",
                            "path_label": "E:/Backup",
                        }
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        connection.execute(
            "INSERT INTO jobs (id, kind) VALUES ('job-backfill', 'multi_target_backup')"
        )
        connection.execute(
            """
            INSERT INTO filter_sets (job_id, id, description)
            VALUES ('job-backfill', 'filter-backfill', 'standard backup defaults')
            """
        )
        connection.execute(
            """
            INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-backfill', 'job-revision-backfill', 'filter-backfill')
            """
        )
        connection.execute(
            """
            INSERT INTO standard_backup_job_revision_details (
                job_id,
                job_revision_id,
                draft_id,
                command_request_id,
                idempotency_key,
                source_name,
                source_path_label,
                defaults_json,
                targets_json
            )
            SELECT
                'job-backfill',
                'job-revision-backfill',
                draft_id,
                'request-backfill',
                'idempotency-backfill',
                source_name,
                source_path_label,
                defaults_json,
                targets_json
            FROM standard_backup_job_drafts
            WHERE draft_id = 'draft-backfill'
            """
        )
        connection.execute(
            """
            INSERT INTO job_heads (job_id, active_revision_id)
            VALUES ('job-backfill', 'job-revision-backfill')
            """
        )
        connection.commit()
    recovery_database = state_root / "recovery.sqlite"
    with sqlite3.connect(recovery_database) as connection:
        apply_sqlite_connection_policy(
            connection,
            recovery_writer_policy(recovery_database),
        )
        apply_sqlite_migrations(connection, recovery_migration_plan())

    restarted = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
    )
    try:
        assert restarted.catalog_connection is not None
        endpoint_count = restarted.catalog_connection.execute(
            "SELECT count(*) FROM endpoints"
        ).fetchone()
        binding_states = restarted.catalog_connection.execute(
            """
            SELECT role, ordinal, registration_state
            FROM standard_backup_job_endpoint_bindings
            ORDER BY ordinal
            """
        ).fetchall()
        assert endpoint_count == (2,)
        assert binding_states == [
            ("SOURCE", 0, "BLOCKED"),
            ("TARGET", 1, "BLOCKED"),
        ]
        assert restarted.endpoint_classification_refresh is not None
        assert restarted.endpoint_classification_refresh.failed_endpoint_count == 2
        assert restarted.endpoint_classification_refresh.blocked_binding_count == 2
    finally:
        restarted.close()


def test_engine_host_runtime_reuses_stable_installation_identity(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
    )
    try:
        assert runtime.installation_state is not None
        installation_id = runtime.installation_state.installation_id
        created_utc = runtime.installation_state.created_utc
    finally:
        runtime.close()

    restarted = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
    )
    try:
        assert restarted.installation_state is not None
        assert restarted.installation_state.installation_id == installation_id
        assert restarted.installation_state.created_utc == created_utc
        assert restarted.installation_state.row_version == 1
    finally:
        restarted.close()


def test_engine_host_runtime_recovers_restore_epochs_before_sqlite_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, str]] = []

    def fake_restore_recovery(
        layout: object,
        *,
        recovered_utc: str,
    ) -> SqliteStateRestoreEpochRecoveryReport:
        assert hasattr(layout, "catalog")
        catalog = getattr(layout, "catalog")
        assert isinstance(catalog, Path)
        assert not catalog.exists()
        calls.append((catalog, recovered_utc))
        return SqliteStateRestoreEpochRecoveryReport(
            scanned_epoch_count=0,
            committed_epoch_count=0,
            previously_rolled_back_epoch_count=0,
            recovered_epochs=(),
        )

    monkeypatch.setattr(
        engine_host_module,
        "recover_incomplete_sqlite_state_restore_epochs",
        fake_restore_recovery,
    )

    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
    )

    try:
        assert calls
        assert calls[0][0] == tmp_path / "state" / "catalog.sqlite"
        assert calls[0][1].endswith("Z")
        assert runtime.state_restore_recovery is not None
    finally:
        runtime.close()


def test_engine_host_runtime_recovers_compaction_epochs_before_sqlite_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, str]] = []

    def fake_compaction_recovery(
        layout: object,
        *,
        recovered_utc: str,
    ) -> SqliteStateCompactionEpochRecoveryReport:
        assert hasattr(layout, "catalog")
        catalog = getattr(layout, "catalog")
        assert isinstance(catalog, Path)
        assert not catalog.exists()
        calls.append((catalog, recovered_utc))
        return SqliteStateCompactionEpochRecoveryReport(
            scanned_epoch_count=0,
            committed_epoch_count=0,
            previously_rolled_back_epoch_count=0,
            recovered_epochs=(),
        )

    monkeypatch.setattr(
        engine_host_module,
        "recover_incomplete_sqlite_state_compaction_epochs",
        fake_compaction_recovery,
    )

    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
    )

    try:
        assert calls
        assert calls[0][0] == tmp_path / "state" / "catalog.sqlite"
        assert calls[0][1].endswith("Z")
        assert runtime.state_compaction_recovery is not None
    finally:
        runtime.close()


def test_engine_host_runtime_can_select_robocopy_staging_backend(
    tmp_path: Path,
) -> None:
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
            registry=_TaskSchedulerRegistry(
                _observed_task_scheduler_definition(definition)
            ),
            schedule_page_limit=10,
            max_schedule_pages=1,
            max_claims=2,
            claim_token_prefix="pump-a",
        )
        stored_resource = (
            runtime.service.external_resource_state_store.load_external_resource_state(
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                resource_id=schedule.schedule_id,
            )
        )

        assert report.schedules_scanned == 1
        assert report.resources_staged == 1
        assert report.resources_reconciled == 1
        assert report.claims_attempted == 2
        assert report.claim_idle is True
        assert (
            report.claim_findings[0].action is TaskSchedulerReconciliationAction.IN_SYNC
        )
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
        registry = _TaskSchedulerRegistry(
            _observed_task_scheduler_definition(definition)
        )

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
        assert (
            report.claim_findings[0].action is TaskSchedulerReconciliationAction.IN_SYNC
        )
    finally:
        runtime.close()


def test_engine_host_runtime_state_restore_maintenance_blocks_retained_leases(
    tmp_path: Path,
) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        reconciler_instance_id="host-new",
    )
    try:
        assert runtime.run_executor_lease_registry is not None
        runtime.run_executor_lease_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-target-a",
            lease=_FakeLiveLease(),
        )

        admission = runtime.admit_state_restore_maintenance()

        assert admission.admitted is False
        assert admission.retained_run_target_lease_count == 1
        assert [blocker.code for blocker in admission.blockers] == [
            "STATE_RESTORE_MAINTENANCE_RETAINED_RUN_TARGET_LEASES"
        ]
    finally:
        runtime.close()


def test_engine_host_runtime_restore_closes_sqlite_handles_and_swaps_state(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        reconciler_instance_id="host-new",
    )
    try:
        assert runtime.state_layout is not None
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        create_sqlite_state_backup_set(
            runtime.state_layout,
            tmp_path / "state-backups",
            backup_set_id="set-a",
            created_utc="2026-07-30T12:00:00Z",
        )
        backup_catalog_version = _read_sqlite_user_version(runtime.state_layout.catalog)
        runtime.catalog_connection.execute("PRAGMA user_version = 77")
        runtime.catalog_connection.commit()
        assert _read_sqlite_user_version(runtime.state_layout.catalog) == 77

        receipt = runtime.restore_state_from_backup_set(
            tmp_path / "state-backups" / "set-a",
            restore_epoch_id="restore-runtime-a",
            started_utc="2026-07-30T12:05:00Z",
        )

        assert receipt.restore_epoch_id == "restore-runtime-a"
        assert runtime.catalog_connection is None
        assert runtime.recovery_connection is None
        assert (
            _read_sqlite_user_version(runtime.state_layout.catalog)
            == backup_catalog_version
        )

        restarted = build_engine_host_runtime(
            authorization=_authorization(),
            service_status=startup_status(ProcessRole.ENGINE_HOST),
            state_root=state_root,
            reconciler_instance_id="host-after-restore",
        )
        try:
            assert restarted.state_restore_startup_reconciliation is not None
            restore_report = restarted.state_restore_startup_reconciliation
            assert restore_report.scanned_epoch_count == 1
            assert restore_report.committed_epoch_count == 1
            assert restore_report.previously_rolled_back_epoch_count == 0
            assert restore_report.latest_committed_epoch is not None
            assert restore_report.latest_committed_epoch.restore_epoch_id == (
                "restore-runtime-a"
            )
            assert restore_report.latest_committed_epoch.backup_set_id == "set-a"
            assert restore_report.latest_committed_epoch.state_set_hash == (
                receipt.state_set_hash
            )
            assert restarted.startup_reconciliation is not None
            assert (
                restarted.startup_reconciliation.reconciler_instance_id
                == "host-after-restore"
            )
        finally:
            restarted.close()
    finally:
        runtime.close()


def test_engine_host_ipc_restore_command_runs_read_only_maintenance_restore(
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
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        create_sqlite_state_backup_set(
            runtime.state_layout,
            tmp_path / "state-backups",
            backup_set_id="set-a",
            created_utc="2026-07-30T12:00:00Z",
        )
        backup_catalog_version = _read_sqlite_user_version(runtime.state_layout.catalog)
        runtime.catalog_connection.execute("PRAGMA user_version = 77")
        runtime.catalog_connection.commit()
        assert _read_sqlite_user_version(runtime.state_layout.catalog) == 77
        ipc_client = InProcessIpcClient(
            service=runtime.service,
            identity=_identity(),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        assert ipc_client.connect().status is IpcStatus.ACCEPTED
        command_payload = {
            "backup_dir": str(tmp_path / "state-backups" / "set-a"),
            "restore_epoch_id": "restore-ipc-runtime-a",
            "started_utc": "2026-07-30T12:05:00Z",
        }

        response = ipc_client.submit_command(
            StateMaintenanceCommandName.RESTORE_STATE_FROM_BACKUP_SET.value,
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            payload=command_payload,
            payload_hash=payload_hash(command_payload),
        )

        assert response.status is IpcStatus.ACCEPTED
        assert response.reason is None
        assert response.payload["read_only_ipc_mode"] is True
        assert response.payload["mutations_enabled"] is False
        assert response.payload["restored"] is True
        assert response.payload["host_restart_required"] is True
        assert response.payload["restore_receipt"]["restore_epoch_id"] == (
            "restore-ipc-runtime-a"
        )
        assert runtime.catalog_connection is None
        assert runtime.recovery_connection is None
        assert (
            _read_sqlite_user_version(runtime.state_layout.catalog)
            == backup_catalog_version
        )
    finally:
        runtime.close()


def test_engine_host_runtime_restore_refuses_blocked_admission_without_closing_handles(
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
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        create_sqlite_state_backup_set(
            runtime.state_layout,
            tmp_path / "state-backups",
            backup_set_id="set-a",
            created_utc="2026-07-30T12:00:00Z",
        )
        assert runtime.run_executor_lease_registry is not None
        runtime.run_executor_lease_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-target-a",
            lease=_FakeLiveLease(),
        )

        with pytest.raises(
            EngineHostStateRestoreNotAdmitted,
            match="STATE_RESTORE_MAINTENANCE_NOT_ADMITTED",
        ) as exc_info:
            runtime.restore_state_from_backup_set(
                tmp_path / "state-backups" / "set-a",
                restore_epoch_id="restore-runtime-b",
                started_utc="2026-07-30T12:05:00Z",
            )

        assert exc_info.value.admission.retained_run_target_lease_count == 1
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
    finally:
        runtime.close()


def test_engine_host_runtime_compaction_closes_sqlite_handles_and_swaps_state(
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
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        runtime.catalog_connection.execute("PRAGMA user_version = 77")
        runtime.catalog_connection.commit()
        runtime.recovery_connection.execute("PRAGMA user_version = 88")
        runtime.recovery_connection.commit()

        receipt = runtime.compact_state_stores(
            compaction_epoch_id="compact-runtime-a",
            started_utc="2026-07-30T12:05:00Z",
        )

        assert receipt.compaction_epoch_id == "compact-runtime-a"
        assert runtime.catalog_connection is None
        assert runtime.recovery_connection is None
        assert _read_sqlite_user_version(runtime.state_layout.catalog) == 77
        assert _read_sqlite_user_version(runtime.state_layout.recovery) == 88
        assert receipt.intent_path.is_file()
        assert receipt.committed_path.is_file()
    finally:
        runtime.close()


def test_engine_host_runtime_compaction_refuses_blocked_admission_without_closing_handles(
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
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        assert runtime.run_executor_lease_registry is not None
        runtime.run_executor_lease_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-target-a",
            lease=_FakeLiveLease(),
        )

        with pytest.raises(
            EngineHostStateCompactionNotAdmitted,
            match="STATE_COMPACTION_MAINTENANCE_NOT_ADMITTED",
        ) as exc_info:
            runtime.compact_state_stores(
                compaction_epoch_id="compact-runtime-b",
                started_utc="2026-07-30T12:05:00Z",
            )

        assert exc_info.value.admission.retained_run_target_lease_count == 1
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
    finally:
        runtime.close()


def test_engine_host_runtime_retention_prunes_after_clean_admission_without_closing_handles(
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
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        backup_root = tmp_path / "state-backups"
        create_sqlite_state_backup_set(
            runtime.state_layout,
            backup_root,
            backup_set_id="set-a",
            created_utc="2026-07-30T12:00:00Z",
        )
        runtime.catalog_connection.execute("PRAGMA user_version = 77")
        runtime.catalog_connection.commit()
        create_sqlite_state_backup_set(
            runtime.state_layout,
            backup_root,
            backup_set_id="set-b",
            created_utc="2026-07-30T12:01:00Z",
        )

        result = runtime.prune_state_maintenance_artifacts(
            backup_root,
            policy=SqliteStateMaintenanceRetentionPolicy(
                keep_latest_backup_sets=1,
                keep_latest_restore_epochs=10,
                keep_latest_compaction_epochs=10,
            ),
        )

        assert [
            (artifact.artifact_type, artifact.artifact_id)
            for artifact in result.deleted_artifacts
        ] == [("backup_set", "set-a")]
        assert not (backup_root / "set-a").exists()
        assert (backup_root / "set-b").is_dir()
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
    finally:
        runtime.close()


def test_engine_host_runtime_retention_refuses_blocked_admission_without_deleting(
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
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        backup_root = tmp_path / "state-backups"
        create_sqlite_state_backup_set(
            runtime.state_layout,
            backup_root,
            backup_set_id="set-a",
            created_utc="2026-07-30T12:00:00Z",
        )
        runtime.catalog_connection.execute("PRAGMA user_version = 77")
        runtime.catalog_connection.commit()
        create_sqlite_state_backup_set(
            runtime.state_layout,
            backup_root,
            backup_set_id="set-b",
            created_utc="2026-07-30T12:01:00Z",
        )
        assert runtime.run_executor_lease_registry is not None
        runtime.run_executor_lease_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-target-a",
            lease=_FakeLiveLease(),
        )

        with pytest.raises(
            EngineHostStateRetentionNotAdmitted,
            match="STATE_RETENTION_MAINTENANCE_NOT_ADMITTED",
        ) as exc_info:
            runtime.prune_state_maintenance_artifacts(
                backup_root,
                policy=SqliteStateMaintenanceRetentionPolicy(
                    keep_latest_backup_sets=1,
                    keep_latest_restore_epochs=10,
                    keep_latest_compaction_epochs=10,
                ),
            )

        assert exc_info.value.admission.retained_run_target_lease_count == 1
        assert (backup_root / "set-a").is_dir()
        assert (backup_root / "set-b").is_dir()
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
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


class _PermissiveReparseGuard:
    def reject_reparse_chain(self, **kwargs: object) -> object:
        return object()


class _FakeHostMutex:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


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


def _read_sqlite_user_version(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


class _FakeRuntime:
    service = object()
    state_layout = None
    state_capacity_report = None
    state_restore_recovery = None
    state_restore_startup_reconciliation = None
    state_compaction_recovery = None
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


@dataclass(frozen=True)
class _FixedStateCapacityProbe:
    observation: StateCapacityObservation

    def measure(self) -> StateCapacityObservation:
        return self.observation


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
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')"
    )
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
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute(
        "INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')"
    )
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
