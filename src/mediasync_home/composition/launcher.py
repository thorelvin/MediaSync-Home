from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from mediasync_home.adapters.process_supervisor import (
    CompletedRoleProcess,
    LocalSubprocessSupervisor,
    RunningRoleProcess,
    current_process_executable_path,
)
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.application.process_supervision import (
    ProcessLaunchPlan,
    build_internal_role_launch_plan,
    build_product_role_launch_plan,
)
from mediasync_home.application.host_locator import (
    LocalEngineHostDescriptor,
    LocalEngineHostPublication,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason, IpcStatus


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "scripts/run_role.py"


class RoleProcessSupervisor(Protocol):
    def start(self, plan: ProcessLaunchPlan) -> RunningRoleProcess: ...

    def run(self, plan: ProcessLaunchPlan, *, timeout_seconds: float) -> CompletedRoleProcess: ...


class DesktopHostProcess(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def kill(self) -> CompletedRoleProcess: ...


class DesktopProcessSupervisor(Protocol):
    def start_detached(self, plan: ProcessLaunchPlan) -> DesktopHostProcess: ...


class DesktopHostProbeStatus(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class DesktopLaunchError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LocalPreviewStatusLaunch:
    pipe_name: str
    engine_host: ProcessLaunchPlan
    gui_status: ProcessLaunchPlan
    host_descriptor: LocalEngineHostDescriptor | None = None


@dataclass(frozen=True)
class LocalPreviewDesktopLaunch:
    host_descriptor: LocalEngineHostDescriptor
    engine_host: ProcessLaunchPlan


@dataclass(frozen=True)
class LocalPreviewHostRun:
    pipe_name: str
    engine_host_args: tuple[str, ...]
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
    host_locator_publication: dict[str, object] | None = None
    adoption_attempted: bool = False
    adopted_existing_host: bool = False
    stale_host_locator_publication_cleared: bool = False
    killed_engine_host: bool = False
    error_type: str | None = None

    @property
    def accepted(self) -> bool:
        if self.adopted_existing_host:
            return (
                self.error_type is None
                and self.engine_host_returncode is None
                and self.engine_host_events == ()
                and self.gui_returncode == 0
                and self.gui_response is not None
                and self.gui_response.get("status") == "ACCEPTED"
            )
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
            "adopted_existing_host": self.adopted_existing_host,
            "adoption_attempted": self.adoption_attempted,
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
            "host_locator_publication": self.host_locator_publication,
            "pipe_name": self.pipe_name,
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
            "stale_host_locator_publication_cleared": (
                self.stale_host_locator_publication_cleared
            ),
        }


@dataclass(frozen=True)
class _ExistingHostAdoptionAttempt:
    result: LocalPreviewStatusResult
    replacement_candidate: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home launcher role")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--desktop",
        action="store_true",
        help="open the desktop app and adopt or start its same-user Engine Host",
    )
    mode.add_argument(
        "--local-preview-status",
        action="store_true",
        help="start a bounded Engine Host and verify readiness through a GUI status query",
    )
    mode.add_argument(
        "--local-preview-host",
        action="store_true",
        help="run the persistent same-user local-preview Engine Host",
    )
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--pipe-name")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument(
        "--reconcile-task-scheduler-resources",
        action="store_true",
        help="ask the bounded Engine Host to reconcile Task Scheduler desired state at startup",
    )
    parser.add_argument(
        "--task-scheduler-executable-path",
        type=Path,
        help="absolute local unsigned app executable registered in Task Scheduler actions",
    )
    parser.add_argument(
        "--run-executor-cycle-interval-ms",
        type=_positive_int,
        default=5000,
        help="persistent host executor maintenance interval",
    )
    parser.add_argument(
        "--run-executor-cycle-max-interval-ms",
        type=_positive_int,
        default=60_000,
        help="maximum backed-off interval for persistent host executor maintenance",
    )
    parser.add_argument(
        "--run-executor-staging-backend",
        choices=("local-file", "robocopy"),
        default="robocopy",
        help="staging transfer backend for the persistent Engine Host",
    )
    parser.add_argument(
        "--task-scheduler-reconciliation-interval-ms",
        type=_positive_int,
        default=300_000,
        help="persistent host Task Scheduler reconciliation interval",
    )
    parser.add_argument(
        "--task-scheduler-reconciliation-max-interval-ms",
        type=_positive_int,
        default=3_600_000,
        help="maximum backed-off interval for persistent host Task Scheduler reconciliation",
    )
    parser.add_argument("--timeout-seconds", type=_positive_float, default=10.0)
    return parser


def run_launcher(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.desktop:
        return _run_local_preview_desktop_from_args(args, emit=emit)
    if args.local_preview_host:
        return _run_local_preview_host_from_args(args, emit=emit)
    if args.local_preview_status:
        result = _run_local_preview_status_from_args(args)
        output = emit or print
        output(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
        return 0 if result.accepted else 2
    return run_role(ProcessRole.LAUNCHER, argv, emit=emit)


def build_local_preview_desktop_launch(
    *,
    host_descriptor: LocalEngineHostDescriptor,
    executable: Path,
    application_root: Path,
    role_runner: Path | None,
    environment: dict[str, str] | None = None,
    run_executor_cycle_interval_ms: int = 5000,
    run_executor_cycle_max_interval_ms: int = 60_000,
    run_executor_staging_backend: str = "robocopy",
    reconcile_task_scheduler_resources: bool = False,
    task_scheduler_executable_path: Path | None = None,
    task_scheduler_reconciliation_interval_ms: int = 300_000,
    task_scheduler_reconciliation_max_interval_ms: int = 3_600_000,
) -> LocalPreviewDesktopLaunch:
    if host_descriptor.state_root is None:
        raise ValueError("DESKTOP_STATE_ROOT_REQUIRED")
    host_args = [
        "--local-preview-host",
        "--installation-id",
        host_descriptor.installation_id,
        "--state-root",
        str(host_descriptor.state_root.resolve()),
        "--run-executor-cycle-interval-ms",
        str(run_executor_cycle_interval_ms),
        "--run-executor-cycle-max-interval-ms",
        str(run_executor_cycle_max_interval_ms),
        "--run-executor-staging-backend",
        run_executor_staging_backend,
    ]
    if reconcile_task_scheduler_resources:
        if task_scheduler_executable_path is None:
            raise ValueError("TASK_SCHEDULER_EXECUTABLE_PATH_REQUIRED")
        host_args.extend(
            (
                "--reconcile-task-scheduler-resources",
                "--task-scheduler-executable-path",
                str(task_scheduler_executable_path.resolve()),
                "--task-scheduler-reconciliation-interval-ms",
                str(task_scheduler_reconciliation_interval_ms),
                "--task-scheduler-reconciliation-max-interval-ms",
                str(task_scheduler_reconciliation_max_interval_ms),
            )
        )
    engine_host = build_product_role_launch_plan(
        role=ProcessRole.LAUNCHER,
        executable=executable,
        role_runner=role_runner,
        application_root=application_root,
        extra_args=tuple(host_args),
        environment=environment,
    )
    return LocalPreviewDesktopLaunch(
        host_descriptor=host_descriptor,
        engine_host=engine_host,
    )


def run_local_preview_desktop(
    launch: LocalPreviewDesktopLaunch,
    *,
    supervisor: DesktopProcessSupervisor,
    probe_host: Callable[[], DesktopHostProbeStatus],
    run_gui: Callable[[], int],
    timeout_seconds: float,
    poll_interval_seconds: float = 0.1,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if timeout_seconds <= 0:
        raise ValueError("DESKTOP_TIMEOUT_MUST_BE_POSITIVE")
    if poll_interval_seconds <= 0:
        raise ValueError("DESKTOP_POLL_INTERVAL_MUST_BE_POSITIVE")

    initial_status = probe_host()
    if initial_status is DesktopHostProbeStatus.READY:
        return run_gui()
    if initial_status is DesktopHostProbeStatus.BLOCKED:
        raise DesktopLaunchError("DESKTOP_ENGINE_HOST_INCOMPATIBLE")

    try:
        host = supervisor.start_detached(launch.engine_host)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise DesktopLaunchError("DESKTOP_ENGINE_HOST_START_FAILED") from exc
    deadline = monotonic() + timeout_seconds
    host_exited = False
    while True:
        status = probe_host()
        if status is DesktopHostProbeStatus.READY:
            return run_gui()
        if status is DesktopHostProbeStatus.BLOCKED:
            _kill_unready_desktop_host(host)
            raise DesktopLaunchError("DESKTOP_ENGINE_HOST_INCOMPATIBLE")
        host_exited = host_exited or host.poll() is not None
        remaining = deadline - monotonic()
        if remaining <= 0:
            _kill_unready_desktop_host(host)
            raise DesktopLaunchError(
                "DESKTOP_ENGINE_HOST_EXITED"
                if host_exited
                else "DESKTOP_ENGINE_HOST_START_TIMEOUT"
            )
        sleep(min(poll_interval_seconds, remaining))


def build_local_preview_host_run(
    *,
    pipe_name: str | None = None,
    state_root: Path | None = None,
    host_descriptor: LocalEngineHostDescriptor | None = None,
    reconcile_task_scheduler_resources: bool = False,
    task_scheduler_executable_path: Path | None = None,
    run_executor_cycle_interval_ms: int | None = None,
    run_executor_cycle_max_interval_ms: int | None = None,
    run_executor_staging_backend: str = "robocopy",
    task_scheduler_reconciliation_interval_ms: int | None = None,
    task_scheduler_reconciliation_max_interval_ms: int | None = None,
) -> LocalPreviewHostRun:
    if host_descriptor is not None:
        if pipe_name is not None and pipe_name != host_descriptor.pipe_name:
            raise ValueError("HOST_LOCATOR_PIPE_NAME_MISMATCH")
        pipe_name = host_descriptor.pipe_name
        if state_root is None:
            state_root = host_descriptor.state_root
    if pipe_name is None:
        raise ValueError("PIPE_NAME_REQUIRED")

    engine_args = ["--pipe-name", pipe_name, "--serve-forever"]
    if host_descriptor is not None:
        engine_args.extend(
            (
                "--installation-id",
                host_descriptor.installation_id,
                "--host-mutex-name",
                host_descriptor.mutex_name,
                "--publish-host-locator",
            )
        )
    if state_root is not None:
        engine_args.extend(("--state-root", str(state_root.resolve())))
        engine_args.append("--enable-local-mutations")
        engine_args.append("--run-executor-cycle-after-request")
        if run_executor_cycle_interval_ms is not None:
            engine_args.extend(
                (
                    "--run-executor-cycle-interval-ms",
                    str(run_executor_cycle_interval_ms),
                )
            )
            if run_executor_cycle_max_interval_ms is not None:
                engine_args.extend(
                    (
                        "--run-executor-cycle-max-interval-ms",
                        str(run_executor_cycle_max_interval_ms),
                    )
                )
        engine_args.extend(
            (
                "--run-executor-staging-backend",
                run_executor_staging_backend,
            )
        )
    if reconcile_task_scheduler_resources:
        if state_root is None:
            raise ValueError("TASK_SCHEDULER_RECONCILIATION_REQUIRES_STATE_ROOT")
        if task_scheduler_executable_path is None:
            raise ValueError("TASK_SCHEDULER_EXECUTABLE_PATH_REQUIRED")
        engine_args.extend(
            (
                "--reconcile-task-scheduler-resources",
                "--task-scheduler-executable-path",
                str(task_scheduler_executable_path.resolve()),
            )
        )
        if task_scheduler_reconciliation_interval_ms is not None:
            engine_args.extend(
                (
                    "--task-scheduler-reconciliation-interval-ms",
                    str(task_scheduler_reconciliation_interval_ms),
                )
            )
            if task_scheduler_reconciliation_max_interval_ms is not None:
                engine_args.extend(
                    (
                        "--task-scheduler-reconciliation-max-interval-ms",
                        str(task_scheduler_reconciliation_max_interval_ms),
                    )
                )
    return LocalPreviewHostRun(
        pipe_name=pipe_name,
        engine_host_args=tuple(engine_args),
        host_descriptor=host_descriptor,
    )


def build_local_preview_status_launch(
    *,
    pipe_name: str | None = None,
    state_root: Path | None = None,
    host_descriptor: LocalEngineHostDescriptor | None = None,
    executable: Path | None = None,
    reconcile_task_scheduler_resources: bool = False,
    task_scheduler_executable_path: Path | None = None,
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
    if reconcile_task_scheduler_resources:
        if state_root is None:
            raise ValueError("TASK_SCHEDULER_RECONCILIATION_REQUIRES_STATE_ROOT")
        if task_scheduler_executable_path is None:
            raise ValueError("TASK_SCHEDULER_EXECUTABLE_PATH_REQUIRED")
        engine_args.extend(
            (
                "--reconcile-task-scheduler-resources",
                "--task-scheduler-executable-path",
                str(task_scheduler_executable_path.resolve()),
            )
        )
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
    existing_publication: LocalEngineHostPublication | None = None,
) -> LocalPreviewStatusResult:
    stale_publication_cleared = False
    if existing_publication is not None:
        adoption_attempt = _try_adopt_existing_local_preview_host(
            launch,
            supervisor=supervisor,
            timeout_seconds=timeout_seconds,
            publication=existing_publication,
        )
        if not adoption_attempt.replacement_candidate:
            return adoption_attempt.result
        stale_publication_cleared = _clear_stale_host_publication(existing_publication)
        if not stale_publication_cleared:
            return adoption_attempt.result

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
        host_locator_publication=None
        if existing_publication is None
        else existing_publication.to_payload(),
        adoption_attempted=existing_publication is not None,
        stale_host_locator_publication_cleared=stale_publication_cleared,
        killed_engine_host=killed_engine_host,
        error_type=error_type,
    )


def _try_adopt_existing_local_preview_host(
    launch: LocalPreviewStatusLaunch,
    *,
    supervisor: RoleProcessSupervisor,
    timeout_seconds: float,
    publication: LocalEngineHostPublication,
) -> _ExistingHostAdoptionAttempt:
    try:
        gui_completed = supervisor.run(launch.gui_status, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return _ExistingHostAdoptionAttempt(
            result=_existing_host_adoption_result(
                launch=launch,
                publication=publication,
                gui_completed=None,
                error_type=type(exc).__name__,
            ),
            replacement_candidate=True,
        )

    gui_response = _parse_json_object(gui_completed.stdout)
    if gui_completed.returncode == 0 and _response_status_is(gui_response, IpcStatus.ACCEPTED):
        return _ExistingHostAdoptionAttempt(
            result=_existing_host_adoption_result(
                launch=launch,
                publication=publication,
                gui_completed=gui_completed,
                adopted_existing_host=True,
            ),
            replacement_candidate=False,
        )
    if _is_live_adoption_rejection(gui_response):
        return _ExistingHostAdoptionAttempt(
            result=_existing_host_adoption_result(
                launch=launch,
                publication=publication,
                gui_completed=gui_completed,
            ),
            replacement_candidate=False,
        )

    return _ExistingHostAdoptionAttempt(
        result=_existing_host_adoption_result(
            launch=launch,
            publication=publication,
            gui_completed=gui_completed,
        ),
        replacement_candidate=True,
    )


def _existing_host_adoption_result(
    *,
    launch: LocalPreviewStatusLaunch,
    publication: LocalEngineHostPublication,
    gui_completed: CompletedRoleProcess | None,
    adopted_existing_host: bool = False,
    error_type: str | None = None,
) -> LocalPreviewStatusResult:
    return LocalPreviewStatusResult(
        pipe_name=launch.pipe_name,
        engine_host_returncode=None,
        engine_host_events=(),
        engine_host_stderr="",
        gui_returncode=None if gui_completed is None else gui_completed.returncode,
        gui_response=None
        if gui_completed is None
        else _parse_json_object(gui_completed.stdout),
        gui_stderr="" if gui_completed is None else gui_completed.stderr,
        host_locator=None
        if launch.host_descriptor is None
        else launch.host_descriptor.to_payload(),
        host_locator_publication=publication.to_payload(),
        adoption_attempted=True,
        adopted_existing_host=adopted_existing_host,
        stale_host_locator_publication_cleared=False,
        error_type=error_type,
    )


def _response_status_is(
    response: dict[str, object] | None,
    status: IpcStatus,
) -> bool:
    return response is not None and response.get("status") == status.value


def _is_live_adoption_rejection(response: dict[str, object] | None) -> bool:
    if not _response_status_is(response, IpcStatus.REJECTED):
        return False
    assert response is not None
    reason = response.get("reason")
    return isinstance(reason, str) and reason != IpcReason.ENGINE_HOST_UNAVAILABLE.value


def _clear_stale_host_publication(
    publication: LocalEngineHostPublication,
) -> bool:
    from mediasync_home.adapters.local_host_locator import (
        clear_unreachable_local_engine_host_publication,
    )

    try:
        return clear_unreachable_local_engine_host_publication(publication)
    except (OSError, ValueError):
        return False


def _run_local_preview_desktop_from_args(
    args: argparse.Namespace,
    *,
    emit: Emit | None = None,
) -> int:
    if os.name != "nt":
        raise RuntimeError("desktop launcher mode is Windows-only")
    if args.pipe_name is not None:
        raise ValueError("DESKTOP_EXPLICIT_PIPE_UNSUPPORTED")

    from mediasync_home.adapters.local_host_locator import (
        build_local_engine_host_descriptor_for_user,
    )
    from mediasync_home.ipc.win32_named_pipe import current_process_identity

    identity = current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=args.installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=args.state_root,
        environ=os.environ,
    )
    executable, role_runner, application_root = _current_product_process_layout()
    packaged_runtime = role_runner is None
    reconcile_task_scheduler_resources = (
        args.reconcile_task_scheduler_resources or packaged_runtime
    )
    task_scheduler_executable_path = args.task_scheduler_executable_path
    if packaged_runtime and task_scheduler_executable_path is None:
        task_scheduler_executable_path = executable
    launch = build_local_preview_desktop_launch(
        host_descriptor=descriptor,
        executable=executable,
        role_runner=role_runner,
        application_root=application_root,
        environment=dict(os.environ),
        run_executor_cycle_interval_ms=args.run_executor_cycle_interval_ms,
        run_executor_cycle_max_interval_ms=args.run_executor_cycle_max_interval_ms,
        run_executor_staging_backend=args.run_executor_staging_backend,
        reconcile_task_scheduler_resources=reconcile_task_scheduler_resources,
        task_scheduler_executable_path=task_scheduler_executable_path,
        task_scheduler_reconciliation_interval_ms=(
            args.task_scheduler_reconciliation_interval_ms
        ),
        task_scheduler_reconciliation_max_interval_ms=(
            args.task_scheduler_reconciliation_max_interval_ms
        ),
    )

    def run_gui() -> int:
        from mediasync_home.composition.ui import run_ui

        assert descriptor.state_root is not None
        return run_ui(
            (
                "--qt-shell",
                "--installation-id",
                descriptor.installation_id,
                "--state-root",
                str(descriptor.state_root),
                "--timeout-seconds",
                str(args.timeout_seconds),
            )
        )

    try:
        return run_local_preview_desktop(
            launch,
            supervisor=LocalSubprocessSupervisor(),
            probe_host=lambda: _probe_local_preview_desktop_host(
                descriptor,
                timeout_seconds=min(1.0, args.timeout_seconds),
            ),
            run_gui=run_gui,
            timeout_seconds=args.timeout_seconds,
        )
    except DesktopLaunchError as exc:
        if emit is not None:
            emit(
                json.dumps(
                    {
                        "event": "DESKTOP_LAUNCH_FAILED",
                        "reason": exc.reason_code,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 2
        from mediasync_home.presentation.app import show_startup_error

        return show_startup_error(exc.reason_code)


def _current_product_process_layout() -> tuple[Path, Path | None, Path]:
    python_executable = Path(sys.executable).resolve()
    source_runtime = RUNNER.is_file() and python_executable.name.lower() in {
        "python",
        "python.exe",
        "pythonw",
        "pythonw.exe",
    }
    if source_runtime:
        return python_executable, RUNNER, ROOT
    executable = current_process_executable_path()
    return executable, None, executable.parent


def _probe_local_preview_desktop_host(
    descriptor: LocalEngineHostDescriptor,
    *,
    timeout_seconds: float,
) -> DesktopHostProbeStatus:
    publication = _load_matching_host_publication(descriptor)
    if publication is None:
        return DesktopHostProbeStatus.UNAVAILABLE
    try:
        from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeClient
        from mediasync_home.presentation.engine_client import EngineClient

        client = EngineClient(
            Win32NamedPipeClient(
                pipe_name=publication.pipe_name,
                role=ProcessRole.GUI,
                timeout_ms=max(1, int(timeout_seconds * 1000)),
            )
        )
        handshake = client.connect()
        if handshake.status is IpcStatus.REJECTED:
            return (
                DesktopHostProbeStatus.UNAVAILABLE
                if handshake.reason is IpcReason.ENGINE_HOST_UNAVAILABLE
                else DesktopHostProbeStatus.BLOCKED
            )
        status = client.get_status()
        if status.status is IpcStatus.ACCEPTED:
            return DesktopHostProbeStatus.READY
        return (
            DesktopHostProbeStatus.UNAVAILABLE
            if status.reason is IpcReason.ENGINE_HOST_UNAVAILABLE
            else DesktopHostProbeStatus.BLOCKED
        )
    except (OSError, RuntimeError, ValueError):
        return DesktopHostProbeStatus.UNAVAILABLE


def _kill_unready_desktop_host(host: DesktopHostProcess) -> None:
    if host.poll() is None:
        try:
            host.kill()
        except (OSError, subprocess.SubprocessError):
            pass


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
        existing_publication = _load_matching_host_publication(host_descriptor)
    else:
        pipe_name = args.pipe_name or make_pipe_name(installation_id=args.installation_id)
        state_root = args.state_root
        existing_publication = None
    launch = build_local_preview_status_launch(
        pipe_name=pipe_name,
        state_root=state_root,
        host_descriptor=host_descriptor,
        reconcile_task_scheduler_resources=args.reconcile_task_scheduler_resources,
        task_scheduler_executable_path=args.task_scheduler_executable_path,
        environment=dict(os.environ),
    )
    return run_local_preview_status(
        launch,
        supervisor=LocalSubprocessSupervisor(),
        timeout_seconds=args.timeout_seconds,
        existing_publication=existing_publication,
    )


def _run_local_preview_host_from_args(
    args: argparse.Namespace,
    *,
    emit: Emit | None = None,
) -> int:
    if os.name != "nt":
        raise RuntimeError("local preview host IPC mode is Windows-only")

    host_descriptor = None
    if args.pipe_name is None:
        from mediasync_home.adapters.local_host_locator import (
            build_local_engine_host_descriptor_for_user,
        )
        from mediasync_home.ipc.win32_named_pipe import current_process_identity

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
        pipe_name = args.pipe_name
        state_root = args.state_root

    host_run = build_local_preview_host_run(
        pipe_name=pipe_name,
        state_root=state_root,
        host_descriptor=host_descriptor,
        reconcile_task_scheduler_resources=args.reconcile_task_scheduler_resources,
        task_scheduler_executable_path=args.task_scheduler_executable_path,
        run_executor_cycle_interval_ms=args.run_executor_cycle_interval_ms,
        run_executor_cycle_max_interval_ms=args.run_executor_cycle_max_interval_ms,
        run_executor_staging_backend=args.run_executor_staging_backend,
        task_scheduler_reconciliation_interval_ms=(
            args.task_scheduler_reconciliation_interval_ms
            if args.reconcile_task_scheduler_resources
            else None
        ),
        task_scheduler_reconciliation_max_interval_ms=(
            args.task_scheduler_reconciliation_max_interval_ms
            if args.reconcile_task_scheduler_resources
            else None
        ),
    )
    from mediasync_home.composition.engine_host import run_engine_host

    return run_engine_host(host_run.engine_host_args, emit=emit)


def _load_matching_host_publication(
    host_descriptor: LocalEngineHostDescriptor,
) -> LocalEngineHostPublication | None:
    if host_descriptor.state_root is None:
        return None

    from mediasync_home.adapters.local_host_locator import (
        load_matching_live_local_engine_host_publication,
    )

    try:
        return load_matching_live_local_engine_host_publication(host_descriptor)
    except (OSError, ValueError):
        return None


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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed
