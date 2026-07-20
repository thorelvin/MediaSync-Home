from __future__ import annotations

import sys
from pathlib import Path

from mediasync_home.adapters.process_supervisor import CompletedRoleProcess
from mediasync_home.composition.launcher import (
    build_local_preview_status_launch,
    run_local_preview_status,
)
from mediasync_home.domain.process_roles import ProcessRole


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
