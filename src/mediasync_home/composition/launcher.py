from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mediasync_home.adapters.process_supervisor import (
    CompletedRoleProcess,
    LocalSubprocessSupervisor,
    RunningRoleProcess,
)
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.application.process_supervision import (
    ProcessLaunchPlan,
    build_internal_role_launch_plan,
)
from mediasync_home.application.host_locator import LocalEngineHostDescriptor
from mediasync_home.domain.process_roles import ProcessRole


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "scripts/run_role.py"


class RoleProcessSupervisor(Protocol):
    def start(self, plan: ProcessLaunchPlan) -> RunningRoleProcess: ...

    def run(self, plan: ProcessLaunchPlan, *, timeout_seconds: float) -> CompletedRoleProcess: ...


@dataclass(frozen=True)
class LocalPreviewStatusLaunch:
    pipe_name: str
    engine_host: ProcessLaunchPlan
    gui_status: ProcessLaunchPlan
    host_descriptor: LocalEngineHostDescriptor | None = None


@dataclass(frozen=True)
class LocalPreviewStatusResult:
    pipe_name: str
    engine_host_returncode: int | None
    engine_host_events: tuple[dict[str, object], ...]
    engine_host_stderr: str
    gui_returncode: int | None
    gui_response: dict[str, object] | None
    gui_stderr: str
    host_locator: dict[str, object] | None = None
    killed_engine_host: bool = False
    error_type: str | None = None

    @property
    def accepted(self) -> bool:
        event_names = tuple(str(event.get("event", "")) for event in self.engine_host_events)
        return (
            self.error_type is None
            and self.engine_host_returncode == 0
            and self.gui_returncode == 0
            and self.gui_response is not None
            and self.gui_response.get("status") == "ACCEPTED"
            and event_names == ("ENGINE_HOST_PIPE_STARTING", "ENGINE_HOST_PIPE_STOPPED")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "engine_host": {
                "events": list(self.engine_host_events),
                "killed": self.killed_engine_host,
                "returncode": self.engine_host_returncode,
                "stderr": self.engine_host_stderr,
            },
            "error_type": self.error_type,
            "event": "LAUNCHER_LOCAL_PREVIEW_STATUS",
            "gui": {
                "response": self.gui_response,
                "returncode": self.gui_returncode,
                "stderr": self.gui_stderr,
            },
            "host_locator": self.host_locator,
            "pipe_name": self.pipe_name,
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home launcher role")
    parser.add_argument(
        "--local-preview-status",
        action="store_true",
        help="start a bounded Engine Host and verify readiness through a GUI status query",
    )
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--pipe-name")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=10.0)
    return parser


def run_launcher(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.local_preview_status:
        result = _run_local_preview_status_from_args(args)
        output = emit or print
        output(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
        return 0 if result.accepted else 2
    return run_role(ProcessRole.LAUNCHER, argv, emit=emit)


def build_local_preview_status_launch(
    *,
    pipe_name: str | None = None,
    state_root: Path | None = None,
    host_descriptor: LocalEngineHostDescriptor | None = None,
    executable: Path | None = None,
    environment: dict[str, str] | None = None,
) -> LocalPreviewStatusLaunch:
    if host_descriptor is not None:
        if pipe_name is not None and pipe_name != host_descriptor.pipe_name:
            raise ValueError("HOST_LOCATOR_PIPE_NAME_MISMATCH")
        pipe_name = host_descriptor.pipe_name
        if state_root is None:
            state_root = host_descriptor.state_root
    if pipe_name is None:
        raise ValueError("PIPE_NAME_REQUIRED")

    engine_args = ["--pipe-name", pipe_name, "--serve-requests", "2"]
    if host_descriptor is not None:
        engine_args.extend(
            (
                "--installation-id",
                host_descriptor.installation_id,
                "--host-mutex-name",
                host_descriptor.mutex_name,
            )
        )
    if host_descriptor is not None and state_root is not None:
        engine_args.append("--publish-host-locator")
    if state_root is not None:
        engine_args.extend(("--state-root", str(state_root.resolve())))
    engine_host = build_internal_role_launch_plan(
        role=ProcessRole.ENGINE_HOST,
        executable=executable or Path(sys.executable).resolve(),
        role_runner=RUNNER,
        repo_root=ROOT,
        extra_args=tuple(engine_args),
        environment=environment,
    )
    gui_status = build_internal_role_launch_plan(
        role=ProcessRole.GUI,
        executable=executable or Path(sys.executable).resolve(),
        role_runner=RUNNER,
        repo_root=ROOT,
        extra_args=("--pipe-name", pipe_name, "--query-status"),
        environment=environment,
    )
    return LocalPreviewStatusLaunch(
        pipe_name=pipe_name,
        engine_host=engine_host,
        gui_status=gui_status,
        host_descriptor=host_descriptor,
    )


def run_local_preview_status(
    launch: LocalPreviewStatusLaunch,
    *,
    supervisor: RoleProcessSupervisor,
    timeout_seconds: float,
) -> LocalPreviewStatusResult:
    host = supervisor.start(launch.engine_host)
    host_completed: CompletedRoleProcess | None = None
    gui_completed: CompletedRoleProcess | None = None
    killed_engine_host = False
    error_type = None
    try:
        gui_completed = supervisor.run(launch.gui_status, timeout_seconds=timeout_seconds)
        if gui_completed.returncode == 0:
            host_completed = host.communicate(timeout_seconds=timeout_seconds)
        else:
            killed_engine_host = True
            host_completed = host.kill()
    except subprocess.TimeoutExpired as exc:
        error_type = type(exc).__name__
        killed_engine_host = True
        host_completed = host.kill()
    finally:
        if host.poll() is None:
            killed_engine_host = True
            host_completed = host.kill()

    return LocalPreviewStatusResult(
        pipe_name=launch.pipe_name,
        engine_host_returncode=None if host_completed is None else host_completed.returncode,
        engine_host_events=()
        if host_completed is None
        else _parse_json_object_lines(host_completed.stdout),
        engine_host_stderr="" if host_completed is None else host_completed.stderr,
        gui_returncode=None if gui_completed is None else gui_completed.returncode,
        gui_response=None if gui_completed is None else _parse_json_object(gui_completed.stdout),
        gui_stderr="" if gui_completed is None else gui_completed.stderr,
        host_locator=None
        if launch.host_descriptor is None
        else launch.host_descriptor.to_payload(),
        killed_engine_host=killed_engine_host,
        error_type=error_type,
    )


def _run_local_preview_status_from_args(args: argparse.Namespace) -> LocalPreviewStatusResult:
    if os.name != "nt":
        raise RuntimeError("local preview launcher IPC mode is Windows-only")

    from mediasync_home.ipc.win32_named_pipe import current_process_identity, make_pipe_name

    host_descriptor = None
    if args.pipe_name is None:
        from mediasync_home.adapters.local_host_locator import (
            build_local_engine_host_descriptor_for_user,
        )

        identity = current_process_identity()
        host_descriptor = build_local_engine_host_descriptor_for_user(
            installation_id=args.installation_id,
            user_scope_hash=identity.user_sid_hash,
            state_root=args.state_root,
            environ=os.environ,
        )
        pipe_name = host_descriptor.pipe_name
        state_root = host_descriptor.state_root
    else:
        pipe_name = args.pipe_name or make_pipe_name(installation_id=args.installation_id)
        state_root = args.state_root
    launch = build_local_preview_status_launch(
        pipe_name=pipe_name,
        state_root=state_root,
        host_descriptor=host_descriptor,
        environment=dict(os.environ),
    )
    return run_local_preview_status(
        launch,
        supervisor=LocalSubprocessSupervisor(),
        timeout_seconds=args.timeout_seconds,
    )


def _parse_json_object_lines(value: str) -> tuple[dict[str, object], ...]:
    objects: list[dict[str, object]] = []
    for line in value.splitlines():
        payload = _parse_json_object(line)
        if payload is not None:
            objects.append(payload)
    return tuple(objects)


def _parse_json_object(value: str) -> dict[str, object] | None:
    try:
        payload: Any = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): item for key, item in payload.items()}


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed
