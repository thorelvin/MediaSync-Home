from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from mediasync_home.domain.process_roles import ProcessRole


class WindowMode(str, Enum):
    HIDDEN = "HIDDEN"


class DllSearchPolicy(str, Enum):
    SAFE_SYSTEM32_AND_APPLICATION_DIR = "SAFE_SYSTEM32_AND_APPLICATION_DIR"


class HandleInheritancePolicy(str, Enum):
    EXPLICIT_EMPTY_HANDLE_LIST = "EXPLICIT_EMPTY_HANDLE_LIST"
    EXPLICIT_ALLOWLIST = "EXPLICIT_ALLOWLIST"


class ChildContainmentPolicy(str, Enum):
    ROLE_PROCESS_NO_TRANSFER_CHILD = "ROLE_PROCESS_NO_TRANSFER_CHILD"
    TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT = "TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT"


class ProcessLaunchViolation(ValueError):
    pass


@dataclass(frozen=True)
class ProcessLaunchPlan:
    role: ProcessRole
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    environment: tuple[tuple[str, str], ...]
    shell: bool
    requires_elevation: bool
    window_mode: WindowMode
    dll_search_policy: DllSearchPolicy
    handle_inheritance_policy: HandleInheritancePolicy
    inherited_handles: tuple[str, ...]
    containment_policy: ChildContainmentPolicy

    def command_line_vector(self) -> tuple[str, ...]:
        return (str(self.executable), *self.arguments)


class ProcessSupervisorPort(Protocol):
    def start(self, plan: ProcessLaunchPlan) -> object: ...


def build_internal_role_launch_plan(
    *,
    role: ProcessRole,
    executable: Path,
    role_runner: Path,
    repo_root: Path,
    extra_args: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> ProcessLaunchPlan:
    plan = ProcessLaunchPlan(
        role=role,
        executable=executable,
        arguments=(str(role_runner), "--role", role.value, *extra_args),
        working_directory=repo_root,
        environment=_minimal_environment(environment or {}),
        shell=False,
        requires_elevation=False,
        window_mode=WindowMode.HIDDEN,
        dll_search_policy=DllSearchPolicy.SAFE_SYSTEM32_AND_APPLICATION_DIR,
        handle_inheritance_policy=HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST,
        inherited_handles=(),
        containment_policy=ChildContainmentPolicy.ROLE_PROCESS_NO_TRANSFER_CHILD,
    )
    validate_process_launch_plan(plan, repo_root=repo_root)
    return plan


def build_transfer_child_launch_plan(
    *,
    executable: Path,
    arguments: tuple[str, ...],
    working_directory: Path,
    working_directory_root: Path,
    owner_role: ProcessRole = ProcessRole.ENGINE_HOST,
    environment: dict[str, str] | None = None,
) -> ProcessLaunchPlan:
    plan = ProcessLaunchPlan(
        role=owner_role,
        executable=executable,
        arguments=arguments,
        working_directory=working_directory,
        environment=_minimal_environment(environment or {}),
        shell=False,
        requires_elevation=False,
        window_mode=WindowMode.HIDDEN,
        dll_search_policy=DllSearchPolicy.SAFE_SYSTEM32_AND_APPLICATION_DIR,
        handle_inheritance_policy=HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST,
        inherited_handles=(),
        containment_policy=ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT,
    )
    validate_process_launch_plan(plan, repo_root=working_directory_root)
    return plan


def validate_process_launch_plan(plan: ProcessLaunchPlan, *, repo_root: Path) -> None:
    if not plan.executable.is_absolute():
        raise ProcessLaunchViolation("PROCESS_EXECUTABLE_MUST_BE_ABSOLUTE")
    if not plan.working_directory.is_absolute():
        raise ProcessLaunchViolation("WORKING_DIRECTORY_MUST_BE_ABSOLUTE")
    if not _is_relative_to(plan.working_directory.resolve(), repo_root.resolve()):
        raise ProcessLaunchViolation("WORKING_DIRECTORY_OUTSIDE_REPO_ROOT")
    if plan.shell:
        raise ProcessLaunchViolation("SHELL_EXECUTION_FORBIDDEN")
    if plan.requires_elevation:
        raise ProcessLaunchViolation("ELEVATION_FORBIDDEN")
    if plan.handle_inheritance_policy is HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST:
        if plan.inherited_handles:
            raise ProcessLaunchViolation("HANDLE_LIST_MUST_BE_EMPTY")
    elif not plan.inherited_handles:
        raise ProcessLaunchViolation("EXPLICIT_HANDLE_ALLOWLIST_REQUIRED")
    if any(name.upper() == "PATH" for name, _value in plan.environment):
        raise ProcessLaunchViolation("PATH_ENVIRONMENT_FORBIDDEN_IN_MINIMAL_ROLE_PLAN")
    if plan.containment_policy is ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT:
        _validate_transfer_child_launch_plan(plan)


def _validate_transfer_child_launch_plan(plan: ProcessLaunchPlan) -> None:
    if plan.window_mode is not WindowMode.HIDDEN:
        raise ProcessLaunchViolation("TRANSFER_CHILD_WINDOW_MUST_BE_HIDDEN")
    if plan.dll_search_policy is not DllSearchPolicy.SAFE_SYSTEM32_AND_APPLICATION_DIR:
        raise ProcessLaunchViolation("TRANSFER_CHILD_DLL_SEARCH_POLICY_UNSAFE")
    if plan.handle_inheritance_policy is not HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST:
        raise ProcessLaunchViolation("TRANSFER_CHILD_HANDLE_INHERITANCE_FORBIDDEN")
    if plan.inherited_handles:
        raise ProcessLaunchViolation("HANDLE_LIST_MUST_BE_EMPTY")
    if any(name.upper() == "COMSPEC" for name, _value in plan.environment):
        raise ProcessLaunchViolation("COMSPEC_ENVIRONMENT_FORBIDDEN_IN_TRANSFER_PLAN")


def _minimal_environment(environment: dict[str, str]) -> tuple[tuple[str, str], ...]:
    allowed_names = {
        "PYTHONUTF8": "PYTHONUTF8",
        "QT_QPA_PLATFORM": "QT_QPA_PLATFORM",
        "SYSTEMROOT": "SystemRoot",
        "TEMP": "TEMP",
        "TMP": "TMP",
    }
    sanitized: dict[str, str] = {}
    for name, value in environment.items():
        canonical = allowed_names.get(name.upper())
        if canonical is not None:
            sanitized[canonical] = value
    return tuple(sorted(sanitized.items()))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
