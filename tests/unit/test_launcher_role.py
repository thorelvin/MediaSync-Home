from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mediasync_home.adapters.local_host_locator import (
    load_local_engine_host_publication,
    publish_local_engine_host_publication,
)
from mediasync_home.adapters.process_supervisor import CompletedRoleProcess
from mediasync_home.application.host_locator import (
    build_local_engine_host_descriptor,
    build_local_engine_host_publication,
    format_host_locator_heartbeat_utc,
)
from mediasync_home.composition import engine_host as engine_host_module
from mediasync_home.composition import launcher as launcher_module
from mediasync_home.composition.launcher import (
    DesktopHostProbeStatus,
    DesktopLaunchError,
    build_local_preview_host_run,
    build_local_preview_desktop_launch,
    build_local_preview_status_launch,
    run_launcher,
    run_local_preview_desktop,
    run_local_preview_status,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason, IpcStatus


def test_local_preview_host_run_uses_long_running_published_descriptor(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=state_root,
    )

    host_run = build_local_preview_host_run(host_descriptor=descriptor)

    assert host_run.pipe_name == descriptor.pipe_name
    assert host_run.host_descriptor is descriptor
    assert host_run.engine_host_args == (
        "--pipe-name",
        descriptor.pipe_name,
        "--serve-forever",
        "--installation-id",
        "preview-a",
        "--host-mutex-name",
        descriptor.mutex_name,
        "--publish-host-locator",
        "--state-root",
        str(state_root.resolve()),
        "--enable-local-mutations",
        "--run-executor-cycle-after-request",
    )


def test_desktop_launch_builds_source_and_packaged_host_argv(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=state_root,
    )
    source = build_local_preview_desktop_launch(
        host_descriptor=descriptor,
        executable=Path(sys.executable).resolve(),
        role_runner=Path(__file__).resolve().parents[2] / "scripts/run_role.py",
        application_root=Path(__file__).resolve().parents[2],
    )
    packaged_executable = (tmp_path / "package" / "MediaSyncHome.exe").resolve()
    packaged = build_local_preview_desktop_launch(
        host_descriptor=descriptor,
        executable=packaged_executable,
        role_runner=None,
        application_root=packaged_executable.parent,
    )

    expected_role_args = (
        "--role",
        "launcher",
        "--local-preview-host",
        "--installation-id",
        "preview-a",
        "--state-root",
        str(state_root.resolve()),
        "--run-executor-cycle-interval-ms",
        "5000",
        "--run-executor-cycle-max-interval-ms",
        "60000",
        "--run-executor-staging-backend",
        "local-file",
    )
    assert source.engine_host.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve().parents[2] / "scripts/run_role.py"),
        *expected_role_args,
    )
    assert packaged.engine_host.command_line_vector() == (
        str(packaged_executable),
        *expected_role_args,
    )


def test_desktop_adopts_ready_host_without_starting_another(tmp_path: Path) -> None:
    launch = _desktop_launch(tmp_path)
    supervisor = _FakeDesktopSupervisor()
    gui_calls: list[str] = []

    exit_code = run_local_preview_desktop(
        launch,
        supervisor=supervisor,
        probe_host=lambda: DesktopHostProbeStatus.READY,
        run_gui=lambda: gui_calls.append("gui") or 0,
        timeout_seconds=1.0,
    )

    assert exit_code == 0
    assert supervisor.started == []
    assert gui_calls == ["gui"]


def test_desktop_starts_host_and_waits_for_readiness(tmp_path: Path) -> None:
    launch = _desktop_launch(tmp_path)
    supervisor = _FakeDesktopSupervisor()
    statuses = iter(
        (
            DesktopHostProbeStatus.UNAVAILABLE,
            DesktopHostProbeStatus.UNAVAILABLE,
            DesktopHostProbeStatus.READY,
        )
    )
    sleeps: list[float] = []

    exit_code = run_local_preview_desktop(
        launch,
        supervisor=supervisor,
        probe_host=lambda: next(statuses),
        run_gui=lambda: 7,
        timeout_seconds=2.0,
        sleep=sleeps.append,
    )

    assert exit_code == 7
    assert supervisor.started == [launch.engine_host]
    assert supervisor.process.killed is False
    assert sleeps == [0.1]


def test_desktop_waits_for_winning_host_after_singleton_race(tmp_path: Path) -> None:
    launch = _desktop_launch(tmp_path)
    supervisor = _FakeDesktopSupervisor(process_exited=True)
    statuses = iter(
        (
            DesktopHostProbeStatus.UNAVAILABLE,
            DesktopHostProbeStatus.UNAVAILABLE,
            DesktopHostProbeStatus.READY,
        )
    )

    exit_code = run_local_preview_desktop(
        launch,
        supervisor=supervisor,
        probe_host=lambda: next(statuses),
        run_gui=lambda: 0,
        timeout_seconds=2.0,
        sleep=lambda _seconds: None,
    )

    assert exit_code == 0
    assert supervisor.started == [launch.engine_host]


def test_desktop_reports_host_process_start_failure(tmp_path: Path) -> None:
    supervisor = _FailingDesktopSupervisor()

    with pytest.raises(DesktopLaunchError, match="DESKTOP_ENGINE_HOST_START_FAILED"):
        run_local_preview_desktop(
            _desktop_launch(tmp_path),
            supervisor=supervisor,
            probe_host=lambda: DesktopHostProbeStatus.UNAVAILABLE,
            run_gui=lambda: 0,
            timeout_seconds=1.0,
        )


def test_desktop_kills_unready_owned_host_after_timeout(tmp_path: Path) -> None:
    launch = _desktop_launch(tmp_path)
    supervisor = _FakeDesktopSupervisor()
    times = iter((0.0, 1.0))

    with pytest.raises(DesktopLaunchError, match="DESKTOP_ENGINE_HOST_START_TIMEOUT"):
        run_local_preview_desktop(
            launch,
            supervisor=supervisor,
            probe_host=lambda: DesktopHostProbeStatus.UNAVAILABLE,
            run_gui=lambda: 0,
            timeout_seconds=0.5,
            monotonic=lambda: next(times),
        )

    assert supervisor.process.killed is True


def test_desktop_refuses_incompatible_existing_host(tmp_path: Path) -> None:
    supervisor = _FakeDesktopSupervisor()

    with pytest.raises(DesktopLaunchError, match="DESKTOP_ENGINE_HOST_INCOMPATIBLE"):
        run_local_preview_desktop(
            _desktop_launch(tmp_path),
            supervisor=supervisor,
            probe_host=lambda: DesktopHostProbeStatus.BLOCKED,
            run_gui=lambda: 0,
            timeout_seconds=1.0,
        )

    assert supervisor.started == []


def test_run_launcher_desktop_delegates_to_desktop_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_desktop(args: object, *, emit: object | None = None) -> int:
        captured["args"] = args
        captured["emit"] = emit
        return 41

    monkeypatch.setattr(
        launcher_module,
        "_run_local_preview_desktop_from_args",
        fake_desktop,
    )
    output: list[str] = []

    exit_code = run_launcher(["--desktop", "--installation-id", "preview-a"], emit=output.append)

    assert exit_code == 41
    assert getattr(captured["args"], "installation_id") == "preview-a"
    assert captured["emit"] == output.append


def test_product_process_layout_uses_packaged_module_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packaged_executable = tmp_path / "MediaSyncHome0B.exe"
    packaged_executable.touch()
    monkeypatch.setattr(
        launcher_module,
        "current_process_executable_path",
        lambda: packaged_executable,
    )
    monkeypatch.setattr(launcher_module, "RUNNER", tmp_path / "missing-run-role.py")

    executable, role_runner, application_root = (
        launcher_module._current_product_process_layout()
    )

    assert executable == packaged_executable
    assert role_runner is None
    assert application_root == tmp_path


def test_local_preview_host_run_can_enable_task_scheduler_startup_pump(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    task_executable = tmp_path / "MediaSyncHome.exe"

    host_run = build_local_preview_host_run(
        pipe_name="pipe-a",
        state_root=state_root,
        reconcile_task_scheduler_resources=True,
        task_scheduler_executable_path=task_executable,
        run_executor_cycle_interval_ms=250,
        run_executor_staging_backend="robocopy",
        task_scheduler_reconciliation_interval_ms=300,
        task_scheduler_reconciliation_max_interval_ms=1200,
    )

    assert host_run.engine_host_args == (
        "--pipe-name",
        "pipe-a",
        "--serve-forever",
        "--state-root",
        str(state_root.resolve()),
        "--enable-local-mutations",
        "--run-executor-cycle-after-request",
        "--run-executor-cycle-interval-ms",
        "250",
        "--run-executor-staging-backend",
        "robocopy",
        "--reconcile-task-scheduler-resources",
        "--task-scheduler-executable-path",
        str(task_executable.resolve()),
        "--task-scheduler-reconciliation-interval-ms",
        "300",
        "--task-scheduler-reconciliation-max-interval-ms",
        "1200",
    )


def test_local_preview_host_run_rejects_scheduler_reconcile_without_state_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="TASK_SCHEDULER_RECONCILIATION_REQUIRES_STATE_ROOT"):
        build_local_preview_host_run(
            pipe_name="pipe-a",
            reconcile_task_scheduler_resources=True,
            task_scheduler_executable_path=tmp_path / "MediaSyncHome.exe",
        )


def test_run_launcher_local_preview_host_delegates_to_engine_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    output: list[str] = []

    def fake_run_engine_host(argv: object, *, emit: object | None = None) -> int:
        captured["argv"] = argv
        captured["emit"] = emit
        return 23

    monkeypatch.setattr(launcher_module.os, "name", "nt")
    monkeypatch.setattr(engine_host_module, "run_engine_host", fake_run_engine_host)

    code = run_launcher(["--local-preview-host", "--pipe-name", "pipe-a"], emit=output.append)

    assert code == 23
    assert captured == {
        "argv": ("--pipe-name", "pipe-a", "--serve-forever"),
        "emit": output.append,
    }


def test_local_preview_status_launch_builds_safe_engine_and_gui_plans(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    launch = build_local_preview_status_launch(
        pipe_name="pipe-a",
        state_root=state_root,
        environment={
            "PATH": "must-not-pass-through",
            "PYTHONUTF8": "1",
            "SystemRoot": "C:\\Windows",
        },
    )

    assert launch.pipe_name == "pipe-a"
    assert launch.engine_host.role is ProcessRole.ENGINE_HOST
    assert launch.gui_status.role is ProcessRole.GUI
    assert launch.engine_host.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve().parents[2] / "scripts/run_role.py"),
        "--role",
        "engine-host",
        "--pipe-name",
        "pipe-a",
        "--serve-requests",
        "2",
        "--state-root",
        str(state_root.resolve()),
    )
    assert launch.gui_status.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve().parents[2] / "scripts/run_role.py"),
        "--role",
        "gui",
        "--pipe-name",
        "pipe-a",
        "--query-status",
    )
    assert dict(launch.engine_host.environment) == {
        "PYTHONUTF8": "1",
        "SystemRoot": "C:\\Windows",
    }
    assert dict(launch.gui_status.environment) == {
        "PYTHONUTF8": "1",
        "SystemRoot": "C:\\Windows",
    }
    assert launch.engine_host.shell is False
    assert launch.gui_status.shell is False
    assert launch.engine_host.requires_elevation is False
    assert launch.gui_status.requires_elevation is False


def test_local_preview_status_launch_can_enable_task_scheduler_startup_pump(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    task_executable = tmp_path / "MediaSyncHome.exe"

    launch = build_local_preview_status_launch(
        pipe_name="pipe-a",
        state_root=state_root,
        reconcile_task_scheduler_resources=True,
        task_scheduler_executable_path=task_executable,
        environment={"PYTHONUTF8": "1"},
    )

    assert launch.engine_host.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve().parents[2] / "scripts/run_role.py"),
        "--role",
        "engine-host",
        "--pipe-name",
        "pipe-a",
        "--serve-requests",
        "2",
        "--state-root",
        str(state_root.resolve()),
        "--reconcile-task-scheduler-resources",
        "--task-scheduler-executable-path",
        str(task_executable.resolve()),
    )


def test_local_preview_status_launch_rejects_scheduler_reconcile_without_state_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="TASK_SCHEDULER_RECONCILIATION_REQUIRES_STATE_ROOT"):
        build_local_preview_status_launch(
            pipe_name="pipe-a",
            reconcile_task_scheduler_resources=True,
            task_scheduler_executable_path=tmp_path / "MediaSyncHome.exe",
            environment={"PYTHONUTF8": "1"},
        )


def test_local_preview_status_launch_rejects_scheduler_reconcile_without_executable(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="TASK_SCHEDULER_EXECUTABLE_PATH_REQUIRED"):
        build_local_preview_status_launch(
            pipe_name="pipe-a",
            state_root=tmp_path / "state",
            reconcile_task_scheduler_resources=True,
            environment={"PYTHONUTF8": "1"},
        )


def test_local_preview_status_launch_uses_host_locator_descriptor(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=state_root,
    )

    launch = build_local_preview_status_launch(
        host_descriptor=descriptor,
        environment={"PYTHONUTF8": "1"},
    )

    assert launch.pipe_name == descriptor.pipe_name
    assert launch.host_descriptor is descriptor
    assert launch.engine_host.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve().parents[2] / "scripts/run_role.py"),
        "--role",
        "engine-host",
        "--pipe-name",
        descriptor.pipe_name,
        "--serve-requests",
        "2",
        "--installation-id",
        "preview-a",
        "--host-mutex-name",
        descriptor.mutex_name,
        "--publish-host-locator",
        "--state-root",
        str(state_root.resolve()),
    )
    assert launch.gui_status.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve().parents[2] / "scripts/run_role.py"),
        "--role",
        "gui",
        "--pipe-name",
        descriptor.pipe_name,
        "--query-status",
    )


def test_local_preview_status_launch_rejects_pipe_name_mismatched_with_locator(
    tmp_path: Path,
) -> None:
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=tmp_path / "state",
    )

    with pytest.raises(ValueError, match="HOST_LOCATOR_PIPE_NAME_MISMATCH"):
        build_local_preview_status_launch(
            pipe_name="different-pipe",
            host_descriptor=descriptor,
            environment={"PYTHONUTF8": "1"},
        )


def test_local_preview_status_result_accepts_successful_host_and_gui_roundtrip() -> None:
    launch = build_local_preview_status_launch(pipe_name="pipe-a", environment={"PYTHONUTF8": "1"})
    supervisor = _SuccessfulPreviewSupervisor()

    result = run_local_preview_status(
        launch,
        supervisor=supervisor,
        timeout_seconds=10.0,
    )

    assert supervisor.started_plan is launch.engine_host
    assert supervisor.ran_plan is launch.gui_status
    assert result.accepted is True
    assert result.killed_engine_host is False
    assert result.to_dict()["event"] == "LAUNCHER_LOCAL_PREVIEW_STATUS"


def test_local_preview_status_adopts_live_published_host_without_starting_host(
    tmp_path: Path,
) -> None:
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=tmp_path / "state",
    )
    publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=tmp_path / "state",
        process_id=4321,
    )
    publish_local_engine_host_publication(publication)
    launch = build_local_preview_status_launch(
        host_descriptor=descriptor,
        environment={"PYTHONUTF8": "1"},
    )
    supervisor = _SuccessfulPreviewSupervisor()

    result = run_local_preview_status(
        launch,
        supervisor=supervisor,
        timeout_seconds=10.0,
        existing_publication=publication,
    )

    assert supervisor.started_plan is None
    assert supervisor.ran_plan is launch.gui_status
    assert result.accepted is True
    assert result.adoption_attempted is True
    assert result.adopted_existing_host is True
    assert result.stale_host_locator_publication_cleared is False
    assert result.engine_host_returncode is None
    assert result.engine_host_events == ()
    assert result.host_locator_publication == publication.to_payload()


def test_local_preview_status_preserves_publication_on_identity_rejected_adoption(
    tmp_path: Path,
) -> None:
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=tmp_path / "state",
    )
    publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=tmp_path / "state",
        process_id=4321,
    )
    publish_local_engine_host_publication(publication)
    launch = build_local_preview_status_launch(
        host_descriptor=descriptor,
        environment={"PYTHONUTF8": "1"},
    )
    supervisor = _RejectedIdentityPreviewSupervisor()

    result = run_local_preview_status(
        launch,
        supervisor=supervisor,
        timeout_seconds=10.0,
        existing_publication=publication,
    )

    assert supervisor.started_plan is None
    assert supervisor.ran_plan is launch.gui_status
    assert result.accepted is False
    assert result.adoption_attempted is True
    assert result.adopted_existing_host is False
    assert result.stale_host_locator_publication_cleared is False
    assert result.engine_host_returncode is None
    assert result.engine_host_events == ()
    assert result.gui_returncode == 2
    assert result.gui_response is not None
    assert result.gui_response["reason"] == IpcReason.CLIENT_IDENTITY_MISMATCH.value
    assert result.host_locator_publication == publication.to_payload()
    assert load_local_engine_host_publication(tmp_path / "state") == publication


def test_local_preview_status_falls_back_to_new_host_when_publication_is_stale(
    tmp_path: Path,
) -> None:
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=tmp_path / "state",
    )
    publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=tmp_path / "state",
        process_id=4321,
    )
    publish_local_engine_host_publication(publication)
    launch = build_local_preview_status_launch(
        host_descriptor=descriptor,
        environment={"PYTHONUTF8": "1"},
    )
    supervisor = _StaleThenSuccessfulPreviewSupervisor()

    result = run_local_preview_status(
        launch,
        supervisor=supervisor,
        timeout_seconds=10.0,
        existing_publication=publication,
    )

    assert supervisor.started_plan is launch.engine_host
    assert supervisor.ran_plans == [launch.gui_status, launch.gui_status]
    assert result.accepted is True
    assert result.adoption_attempted is True
    assert result.adopted_existing_host is False
    assert result.stale_host_locator_publication_cleared is True
    assert result.engine_host_returncode == 0
    assert result.host_locator_publication == publication.to_payload()
    assert load_local_engine_host_publication(tmp_path / "state") is None


def test_local_preview_status_preserves_fresh_live_publication_during_startup_race(
    tmp_path: Path,
) -> None:
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=tmp_path / "state",
    )
    publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=tmp_path / "state",
        process_id=os.getpid(),
        heartbeat_utc=format_host_locator_heartbeat_utc(datetime.now(timezone.utc)),
    )
    publish_local_engine_host_publication(publication)
    launch = build_local_preview_status_launch(
        host_descriptor=descriptor,
        environment={"PYTHONUTF8": "1"},
    )
    supervisor = _StaleThenSuccessfulPreviewSupervisor()

    result = run_local_preview_status(
        launch,
        supervisor=supervisor,
        timeout_seconds=10.0,
        existing_publication=publication,
    )

    assert supervisor.started_plan is None
    assert supervisor.ran_plans == [launch.gui_status]
    assert result.accepted is False
    assert result.adoption_attempted is True
    assert result.adopted_existing_host is False
    assert result.stale_host_locator_publication_cleared is False
    assert result.engine_host_returncode is None
    assert result.gui_response is not None
    assert result.gui_response["reason"] == IpcReason.ENGINE_HOST_UNAVAILABLE.value
    assert load_local_engine_host_publication(tmp_path / "state") == publication


def _desktop_launch(tmp_path: Path):
    descriptor = build_local_engine_host_descriptor(
        installation_id="preview-a",
        user_scope_hash="b" * 64,
        state_root=tmp_path / "state",
    )
    return build_local_preview_desktop_launch(
        host_descriptor=descriptor,
        executable=Path(sys.executable).resolve(),
        role_runner=Path(__file__).resolve().parents[2] / "scripts/run_role.py",
        application_root=Path(__file__).resolve().parents[2],
    )


class _FakeDesktopProcess:
    def __init__(self, *, exited: bool = False) -> None:
        self.killed = False
        self.exited = exited

    @property
    def pid(self) -> int:
        return 4321

    def poll(self) -> int | None:
        return 1 if self.killed or self.exited else None

    def kill(self) -> CompletedRoleProcess:
        self.killed = True
        return CompletedRoleProcess(returncode=1, stdout="", stderr="")


class _FakeDesktopSupervisor:
    def __init__(self, *, process_exited: bool = False) -> None:
        self.started: list[object] = []
        self.process = _FakeDesktopProcess(exited=process_exited)

    def start_detached(self, plan: object) -> _FakeDesktopProcess:
        self.started.append(plan)
        return self.process


class _FailingDesktopSupervisor:
    def start_detached(self, plan: object) -> _FakeDesktopProcess:
        del plan
        raise OSError("process creation failed")


class _SuccessfulPreviewSupervisor:
    def __init__(self) -> None:
        self.started_plan = None
        self.ran_plan = None
        self._host = _CompletedHostProcess()

    def start(self, plan: object) -> "_CompletedHostProcess":
        self.started_plan = plan
        return self._host

    def run(self, plan: object, *, timeout_seconds: float) -> CompletedRoleProcess:
        del timeout_seconds
        self.ran_plan = plan
        return CompletedRoleProcess(
            returncode=0,
            stdout='{"payload":{"host_status":{"role":"engine-host"}},"reason":null,"status":"ACCEPTED"}',
            stderr="",
        )


class _CompletedHostProcess:
    def __init__(self) -> None:
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def communicate(self, *, timeout_seconds: float | None = None) -> CompletedRoleProcess:
        del timeout_seconds
        self._returncode = 0
        return CompletedRoleProcess(
            returncode=0,
            stdout=(
                '{"event":"ENGINE_HOST_PIPE_STARTING","pipe_name":"pipe-a"}\n'
                '{"event":"ENGINE_HOST_PIPE_STOPPED","pipe_name":"pipe-a","served_requests":2}\n'
            ),
            stderr="",
        )

    def kill(self) -> CompletedRoleProcess:
        self._returncode = 1
        return CompletedRoleProcess(returncode=1, stdout="", stderr="")


class _StaleThenSuccessfulPreviewSupervisor:
    def __init__(self) -> None:
        self.started_plan = None
        self.ran_plans: list[object] = []
        self._host = _CompletedHostProcess()

    def start(self, plan: object) -> "_CompletedHostProcess":
        self.started_plan = plan
        return self._host

    def run(self, plan: object, *, timeout_seconds: float) -> CompletedRoleProcess:
        del timeout_seconds
        self.ran_plans.append(plan)
        if len(self.ran_plans) == 1:
            return CompletedRoleProcess(
                returncode=2,
                stdout='{"payload":{},"reason":"ENGINE_HOST_UNAVAILABLE","status":"REJECTED"}',
                stderr="",
            )
        return CompletedRoleProcess(
            returncode=0,
            stdout='{"payload":{"host_status":{"role":"engine-host"}},"reason":null,"status":"ACCEPTED"}',
            stderr="",
        )


class _RejectedIdentityPreviewSupervisor:
    def __init__(self) -> None:
        self.started_plan = None
        self.ran_plan = None

    def start(self, plan: object) -> "_CompletedHostProcess":
        self.started_plan = plan
        raise AssertionError("identity-rejected adoption must not start a replacement host")

    def run(self, plan: object, *, timeout_seconds: float) -> CompletedRoleProcess:
        del timeout_seconds
        self.ran_plan = plan
        return CompletedRoleProcess(
            returncode=2,
            stdout=(
                '{"payload":{"verified_user_sid_hash":"other-user"},'
                f'"reason":"{IpcReason.CLIENT_IDENTITY_MISMATCH.value}",'
                f'"status":"{IpcStatus.REJECTED.value}"}}'
            ),
            stderr="",
        )
