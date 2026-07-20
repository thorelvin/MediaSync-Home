from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mediasync_home.adapters.process_supervisor import LocalSubprocessSupervisor
from mediasync_home.application.process_supervision import (
    ChildContainmentPolicy,
    DllSearchPolicy,
    HandleInheritancePolicy,
    ProcessLaunchPlan,
    ProcessLaunchViolation,
    WindowMode,
    build_internal_role_launch_plan,
    validate_process_launch_plan,
)
from mediasync_home.domain.process_roles import ProcessRole


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run_role.py"


def test_internal_role_launch_plan_is_safe_by_default() -> None:
    plan = build_internal_role_launch_plan(
        role=ProcessRole.ENGINE_HOST,
        executable=Path(sys.executable).resolve(),
        role_runner=RUNNER,
        repo_root=ROOT,
        environment={
            "PATH": "must-not-pass-through",
            "PYTHONUTF8": "1",
            "SystemRoot": "C:\\Windows",
        },
    )

    assert plan.command_line_vector() == (
        str(Path(sys.executable).resolve()),
        str(RUNNER),
        "--role",
        "engine-host",
    )
    assert plan.shell is False
    assert plan.requires_elevation is False
    assert plan.window_mode is WindowMode.HIDDEN
    assert plan.dll_search_policy is DllSearchPolicy.SAFE_SYSTEM32_AND_APPLICATION_DIR
    assert plan.handle_inheritance_policy is HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST
    assert plan.inherited_handles == ()
    assert plan.containment_policy is ChildContainmentPolicy.ROLE_PROCESS_NO_TRANSFER_CHILD
    assert dict(plan.environment) == {"PYTHONUTF8": "1", "SystemRoot": "C:\\Windows"}


def test_internal_role_launch_plan_preserves_systemroot_case_insensitively() -> None:
    plan = build_internal_role_launch_plan(
        role=ProcessRole.ENGINE_HOST,
        executable=Path(sys.executable).resolve(),
        role_runner=RUNNER,
        repo_root=ROOT,
        environment={
            "PATH": "must-not-pass-through",
            "SYSTEMROOT": "C:\\Windows",
            "TEMP": "C:\\Temp",
        },
    )

    assert dict(plan.environment) == {
        "SystemRoot": "C:\\Windows",
        "TEMP": "C:\\Temp",
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"executable": Path("python.exe")}, "PROCESS_EXECUTABLE_MUST_BE_ABSOLUTE"),
        ({"working_directory": Path("relative")}, "WORKING_DIRECTORY_MUST_BE_ABSOLUTE"),
        ({"working_directory": Path("C:/")}, "WORKING_DIRECTORY_OUTSIDE_REPO_ROOT"),
        ({"shell": True}, "SHELL_EXECUTION_FORBIDDEN"),
        ({"requires_elevation": True}, "ELEVATION_FORBIDDEN"),
        ({"inherited_handles": ("pipe-handle",)}, "HANDLE_LIST_MUST_BE_EMPTY"),
        ({"environment": (("PATH", "C:/Windows/System32"),)}, "PATH_ENVIRONMENT_FORBIDDEN"),
        ({"environment": (("Path", "C:/Windows/System32"),)}, "PATH_ENVIRONMENT_FORBIDDEN"),
    ],
)
def test_launch_plan_rejects_unsafe_process_policy(
    mutation: dict[str, object],
    reason: str,
) -> None:
    baseline = _safe_plan()
    plan = ProcessLaunchPlan(**(baseline.__dict__ | mutation))

    with pytest.raises(ProcessLaunchViolation, match=reason):
        validate_process_launch_plan(plan, repo_root=ROOT)


def test_transfer_child_policy_is_reserved_until_job_object_adapter_exists() -> None:
    plan = ProcessLaunchPlan(
        **(
            _safe_plan().__dict__
            | {
                "containment_policy": ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT
            }
        )
    )

    with pytest.raises(ProcessLaunchViolation, match="TRANSFER_CHILD_SUPERVISION_NOT_IMPLEMENTED"):
        validate_process_launch_plan(plan, repo_root=ROOT)


def test_local_subprocess_supervisor_rejects_unvalidated_shell_plan_before_spawn() -> None:
    plan = ProcessLaunchPlan(**(_safe_plan().__dict__ | {"shell": True}))

    with pytest.raises(ProcessLaunchViolation, match="SHELL_EXECUTION_FORBIDDEN"):
        LocalSubprocessSupervisor().start(plan)


def _safe_plan() -> ProcessLaunchPlan:
    return build_internal_role_launch_plan(
        role=ProcessRole.GUI,
        executable=Path(sys.executable).resolve(),
        role_runner=RUNNER,
        repo_root=ROOT,
        environment={"PYTHONUTF8": "1"},
    )
