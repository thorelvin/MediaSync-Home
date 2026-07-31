from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

import mediasync_home.adapters.process_supervisor as process_supervisor_module
from mediasync_home.adapters.process_supervisor import (
    LocalSubprocessSupervisor,
    Win32JobObjectTransferSupervisor,
    Win32ProcessHandles,
    current_process_executable_path,
)
from mediasync_home.application.process_supervision import (
    ChildContainmentPolicy,
    DllSearchPolicy,
    HandleInheritancePolicy,
    ProcessLaunchPlan,
    ProcessLaunchViolation,
    WindowMode,
    build_internal_role_launch_plan,
    build_product_role_launch_plan,
    build_transfer_child_launch_plan,
    validate_process_launch_plan,
)
from mediasync_home.domain.process_roles import ProcessRole


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run_role.py"


def test_current_process_executable_path_resolves_running_module() -> None:
    executable = current_process_executable_path()

    assert executable.is_absolute()
    assert executable.is_file()


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


def test_packaged_product_role_launch_plan_invokes_same_executable() -> None:
    executable = (ROOT / "dist" / "MediaSyncHome.exe").resolve()
    application_root = executable.parent

    plan = build_product_role_launch_plan(
        role=ProcessRole.LAUNCHER,
        executable=executable,
        role_runner=None,
        application_root=application_root,
        extra_args=("--local-preview-host",),
        environment={"PATH": "must-not-pass-through", "PYTHONUTF8": "1"},
    )

    assert plan.command_line_vector() == (
        str(executable),
        "--role",
        "launcher",
        "--local-preview-host",
    )
    assert plan.working_directory == application_root
    assert dict(plan.environment) == {"PYTHONUTF8": "1"}


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


def test_transfer_child_launch_plan_uses_suspended_job_object_policy() -> None:
    plan = _safe_transfer_plan()

    validate_process_launch_plan(plan, repo_root=ROOT)

    assert plan.role is ProcessRole.ENGINE_HOST
    assert plan.shell is False
    assert plan.requires_elevation is False
    assert plan.window_mode is WindowMode.HIDDEN
    assert plan.dll_search_policy is DllSearchPolicy.SAFE_SYSTEM32_AND_APPLICATION_DIR
    assert plan.handle_inheritance_policy is HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST
    assert plan.inherited_handles == ()
    assert plan.containment_policy is ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT
    assert dict(plan.environment) == {"PYTHONUTF8": "1", "SystemRoot": "C:\\Windows"}


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            {
                "handle_inheritance_policy": HandleInheritancePolicy.EXPLICIT_ALLOWLIST,
                "inherited_handles": ("pipe-handle",),
            },
            "TRANSFER_CHILD_HANDLE_INHERITANCE_FORBIDDEN",
        ),
        (
            {"environment": (("COMSPEC", "cmd.exe"),)},
            "COMSPEC_ENVIRONMENT_FORBIDDEN",
        ),
    ],
)
def test_transfer_child_launch_plan_rejects_unsafe_transfer_policy(
    mutation: dict[str, object],
    reason: str,
) -> None:
    plan = ProcessLaunchPlan(**(_safe_transfer_plan().__dict__ | mutation))

    with pytest.raises(ProcessLaunchViolation, match=reason):
        validate_process_launch_plan(plan, repo_root=ROOT)


def test_local_subprocess_supervisor_rejects_transfer_child_plan_before_spawn() -> None:
    with pytest.raises(ProcessLaunchViolation, match="LOCAL_SUBPROCESS_TRANSFER_CHILD_FORBIDDEN"):
        LocalSubprocessSupervisor().start(_safe_transfer_plan())


def test_local_subprocess_supervisor_detaches_without_output_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self) -> int | None:
            return self.returncode

    def fake_popen(command: object, **kwargs: object) -> FakeProcess:
        captured["command"] = command
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(process_supervisor_module.subprocess, "Popen", fake_popen)

    process = LocalSubprocessSupervisor().start_detached(_safe_plan())

    assert process.pid == 4321
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["shell"] is False
    assert captured["close_fds"] is True


def test_win32_transfer_supervisor_assigns_job_before_resume() -> None:
    api = _FakeWin32ProcessApi()

    process = Win32JobObjectTransferSupervisor(api=api).start(_safe_transfer_plan())

    assert process.process_id == 1234
    assert api.calls == [
        "create_suspended",
        "create_job",
        "assign",
        "resume",
        "close:thread",
    ]

    process.close()

    assert api.calls[-2:] == ["close:job", "close:process"]


def test_win32_transfer_supervisor_terminates_suspended_child_on_assignment_failure() -> None:
    api = _FakeWin32ProcessApi(fail_assign=True)

    with pytest.raises(ProcessLaunchViolation, match="CHILD_PROCESS_CONTAINMENT_FAILED"):
        Win32JobObjectTransferSupervisor(api=api).start(_safe_transfer_plan())

    assert api.calls == [
        "create_suspended",
        "create_job",
        "assign",
        "terminate:process:99",
        "close:thread",
        "close:job",
        "close:process",
    ]


def test_win32_transfer_supervisor_rejects_internal_role_plan() -> None:
    api = _FakeWin32ProcessApi()

    with pytest.raises(ProcessLaunchViolation, match="TRANSFER_CHILD_JOB_OBJECT_POLICY_REQUIRED"):
        Win32JobObjectTransferSupervisor(api=api).start(_safe_plan())

    assert api.calls == []


def test_contained_transfer_process_exposes_exit_status_and_terminate() -> None:
    api = _FakeWin32ProcessApi(exit_code=7)
    process = Win32JobObjectTransferSupervisor(api=api).start(_safe_transfer_plan())

    assert process.poll() == 7
    assert process.wait(timeout_seconds=0.25) == 7

    process.terminate(exit_code=17)

    assert api.calls[-3:] == ["terminate:process:17", "close:job", "close:process"]


def test_contained_transfer_process_wait_timeout_returns_none() -> None:
    api = _FakeWin32ProcessApi(wait_result=False)
    process = Win32JobObjectTransferSupervisor(api=api).start(_safe_transfer_plan())

    assert process.wait(timeout_seconds=0.25) is None

    process.close()


@pytest.mark.parametrize("failed_handle", ("job", "process"))
def test_contained_transfer_process_close_attempts_all_handles_and_can_retry(
    failed_handle: str,
) -> None:
    api = _FakeWin32ProcessApi(fail_close_handles={failed_handle})
    process = Win32JobObjectTransferSupervisor(api=api).start(_safe_transfer_plan())

    with pytest.raises(
        ProcessLaunchViolation,
        match=f"TRANSFER_CHILD_HANDLE_CLOSE_FAILED:{failed_handle}",
    ):
        process.close()

    assert api.calls[-2:] == ["close:job", "close:process"]

    api.fail_close_handles.clear()
    process.close()

    assert api.calls[-1] == f"close:{failed_handle}"


def test_contained_transfer_process_closes_handles_when_termination_fails() -> None:
    api = _FakeWin32ProcessApi(fail_terminate=True)
    process = Win32JobObjectTransferSupervisor(api=api).start(_safe_transfer_plan())

    with pytest.raises(OSError, match="termination failed"):
        process.terminate(exit_code=17)

    assert api.calls[-3:] == ["terminate:process:17", "close:job", "close:process"]


def test_local_subprocess_supervisor_rejects_unvalidated_shell_plan_before_spawn() -> None:
    plan = ProcessLaunchPlan(**(_safe_plan().__dict__ | {"shell": True}))

    with pytest.raises(ProcessLaunchViolation, match="SHELL_EXECUTION_FORBIDDEN"):
        LocalSubprocessSupervisor().start(plan)


class _FakeWin32ProcessApi:
    def __init__(
        self,
        *,
        fail_assign: bool = False,
        fail_close_handles: set[str] | None = None,
        fail_terminate: bool = False,
        wait_result: bool = True,
        exit_code: int = 259,
    ) -> None:
        self.calls: list[str] = []
        self.fail_assign = fail_assign
        self.fail_close_handles = fail_close_handles or set()
        self.fail_terminate = fail_terminate
        self.wait_result = wait_result
        self.exit_code = exit_code

    def create_suspended_process(
        self,
        plan: ProcessLaunchPlan,
        *,
        command_line: str,
    ) -> Win32ProcessHandles:
        assert str(plan.executable) in command_line
        self.calls.append("create_suspended")
        return Win32ProcessHandles(
            process_handle="process",
            thread_handle="thread",
            process_id=1234,
        )

    def create_kill_on_close_job(self) -> object:
        self.calls.append("create_job")
        return "job"

    def assign_process_to_job(self, job_handle: object, process_handle: object) -> None:
        assert job_handle == "job"
        assert process_handle == "process"
        self.calls.append("assign")
        if self.fail_assign:
            raise OSError("assignment failed")

    def resume_thread(self, thread_handle: object) -> None:
        assert thread_handle == "thread"
        self.calls.append("resume")

    def wait_for_process(self, process_handle: object, *, timeout_ms: int) -> bool:
        assert process_handle == "process"
        assert timeout_ms == 250
        self.calls.append("wait")
        return self.wait_result

    def get_exit_code_process(self, process_handle: object) -> int:
        assert process_handle == "process"
        self.calls.append("exit_code")
        return self.exit_code

    def terminate_process(self, process_handle: object, *, exit_code: int) -> None:
        assert process_handle == "process"
        self.calls.append(f"terminate:process:{exit_code}")
        if self.fail_terminate:
            raise OSError("termination failed")

    def close_handle(self, handle: object) -> None:
        assert handle in {"thread", "job", "process"}
        self.calls.append(f"close:{handle}")
        if handle in self.fail_close_handles:
            raise OSError(f"{handle} close failed")


def _safe_transfer_plan() -> ProcessLaunchPlan:
    return build_transfer_child_launch_plan(
        executable=Path(sys.executable).resolve(),
        arguments=("-c", "pass"),
        working_directory=ROOT,
        working_directory_root=ROOT,
        environment={
            "COMSPEC": "must-not-pass-through",
            "PATH": "must-not-pass-through",
            "PYTHONUTF8": "1",
            "SystemRoot": "C:\\Windows",
        },
    )


def _safe_plan() -> ProcessLaunchPlan:
    return build_internal_role_launch_plan(
        role=ProcessRole.GUI,
        executable=Path(sys.executable).resolve(),
        role_runner=RUNNER,
        repo_root=ROOT,
        environment={"PYTHONUTF8": "1"},
    )
