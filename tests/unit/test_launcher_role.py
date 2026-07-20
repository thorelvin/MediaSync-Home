from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mediasync_home.adapters.process_supervisor import CompletedRoleProcess
from mediasync_home.application.host_locator import (
    build_local_engine_host_descriptor,
    build_local_engine_host_publication,
)
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
    assert result.engine_host_returncode is None
    assert result.engine_host_events == ()
    assert result.host_locator_publication == publication.to_payload()


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
    assert result.engine_host_returncode == 0
    assert result.host_locator_publication == publication.to_payload()


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
                stdout='{"payload":{},"reason":"INVALID_FRAME","status":"REJECTED"}',
                stderr="",
            )
        return CompletedRoleProcess(
            returncode=0,
            stdout='{"payload":{"host_status":{"role":"engine-host"}},"reason":null,"status":"ACCEPTED"}',
            stderr="",
        )
