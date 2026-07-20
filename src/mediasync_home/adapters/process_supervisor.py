from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from mediasync_home.application.process_supervision import (
    ChildContainmentPolicy,
    HandleInheritancePolicy,
    ProcessLaunchPlan,
    ProcessLaunchViolation,
    WindowMode,
)


_CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class CompletedRoleProcess:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class RunningRoleProcess:
    _process: subprocess.Popen[str]

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def communicate(self, *, timeout_seconds: float | None = None) -> CompletedRoleProcess:
        stdout, stderr = self._process.communicate(timeout=timeout_seconds)
        return CompletedRoleProcess(
            returncode=self._process.returncode if self._process.returncode is not None else -1,
            stdout=stdout or "",
            stderr=stderr or "",
        )

    def kill(self) -> CompletedRoleProcess:
        self._process.kill()
        return self.communicate(timeout_seconds=5.0)


class LocalSubprocessSupervisor:
    def start(self, plan: ProcessLaunchPlan) -> RunningRoleProcess:
        _assert_internal_role_plan_safe(plan)
        process = subprocess.Popen(
            list(plan.command_line_vector()),
            cwd=str(plan.working_directory),
            env=_process_environment(plan),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=plan.shell,
            close_fds=True,
            creationflags=_creation_flags(plan),
        )
        return RunningRoleProcess(process)

    def run(self, plan: ProcessLaunchPlan, *, timeout_seconds: float) -> CompletedRoleProcess:
        _assert_internal_role_plan_safe(plan)
        process = subprocess.run(
            list(plan.command_line_vector()),
            cwd=str(plan.working_directory),
            env=_process_environment(plan),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=plan.shell,
            timeout=timeout_seconds,
            check=False,
            creationflags=_creation_flags(plan),
        )
        return CompletedRoleProcess(
            returncode=process.returncode,
            stdout=process.stdout or "",
            stderr=process.stderr or "",
        )


def _process_environment(plan: ProcessLaunchPlan) -> dict[str, str]:
    return {name: value for name, value in plan.environment}


def _assert_internal_role_plan_safe(plan: ProcessLaunchPlan) -> None:
    if plan.shell:
        raise ProcessLaunchViolation("SHELL_EXECUTION_FORBIDDEN")
    if plan.requires_elevation:
        raise ProcessLaunchViolation("ELEVATION_FORBIDDEN")
    if plan.handle_inheritance_policy is not HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST:
        raise ProcessLaunchViolation("INTERNAL_ROLE_HANDLE_INHERITANCE_FORBIDDEN")
    if plan.inherited_handles:
        raise ProcessLaunchViolation("HANDLE_LIST_MUST_BE_EMPTY")
    if plan.containment_policy is not ChildContainmentPolicy.ROLE_PROCESS_NO_TRANSFER_CHILD:
        raise ProcessLaunchViolation("TRANSFER_CHILD_SUPERVISION_NOT_IMPLEMENTED_IN_0B")
    if any(name.upper() == "PATH" for name, _value in plan.environment):
        raise ProcessLaunchViolation("PATH_ENVIRONMENT_FORBIDDEN_IN_MINIMAL_ROLE_PLAN")


def _creation_flags(plan: ProcessLaunchPlan) -> int:
    if os.name == "nt" and plan.window_mode is WindowMode.HIDDEN:
        return _CREATE_NO_WINDOW
    return 0
