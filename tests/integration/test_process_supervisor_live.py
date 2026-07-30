from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import textwrap
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from mediasync_home.adapters.process_supervisor import Win32JobObjectTransferSupervisor
from mediasync_home.application.process_supervision import build_transfer_child_launch_plan


pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="live transfer-child containment evidence requires Windows",
)


_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259


def test_win32_transfer_supervisor_kill_on_close_prevents_orphan_child(
    tmp_path: Path,
) -> None:
    plan = build_transfer_child_launch_plan(
        executable=Path(sys.executable).resolve(),
        arguments=("-c", "import time; time.sleep(60)"),
        working_directory=tmp_path,
        working_directory_root=tmp_path,
        environment={
            "PATH": "must-not-pass-through",
            "PYTHONUTF8": "1",
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
        },
    )
    process = Win32JobObjectTransferSupervisor().start(plan)
    monitor_handle = _open_process_monitor(process.process_id)

    try:
        assert process.wait(timeout_seconds=0.1) is None

        process.close()

        assert _wait_for_process_exit(monitor_handle, timeout_ms=5_000)
        assert _get_exit_code_process(monitor_handle) != _STILL_ACTIVE
    finally:
        _terminate_if_still_active(monitor_handle)
        _close_handle(monitor_handle)
        process.close()


def test_win32_transfer_child_dies_when_owning_host_process_exits(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    pid_path = tmp_path / "child.pid"
    release_path = tmp_path / "release-host-exit"
    helper_code = textwrap.dedent(
        """
        import os
        import sys
        import time
        from pathlib import Path

        from mediasync_home.adapters.process_supervisor import Win32JobObjectTransferSupervisor
        from mediasync_home.application.process_supervision import build_transfer_child_launch_plan

        work_root = Path(sys.argv[1])
        pid_path = Path(sys.argv[2])
        release_path = Path(sys.argv[3])
        python_executable = Path(sys.argv[4])
        plan = build_transfer_child_launch_plan(
            executable=python_executable,
            arguments=("-c", "import time; time.sleep(60)"),
            working_directory=work_root,
            working_directory_root=work_root,
            environment={
                "PYTHONUTF8": "1",
                "SystemRoot": os.environ.get("SystemRoot", r"C:\\Windows"),
                "TEMP": str(work_root),
                "TMP": str(work_root),
            },
        )
        process = Win32JobObjectTransferSupervisor().start(plan)
        pid_path.write_text(str(process.process_id), encoding="utf-8")
        deadline = time.monotonic() + 20
        while not release_path.exists():
            if time.monotonic() > deadline:
                os._exit(88)
            time.sleep(0.05)
        os._exit(77)
        """
    )
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    existing_python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(repo_root / "src")
        if not existing_python_path
        else f"{repo_root / 'src'}{os.pathsep}{existing_python_path}"
    )
    helper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            helper_code,
            str(tmp_path),
            str(pid_path),
            str(release_path),
            str(Path(sys.executable).resolve()),
        ],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    monitor_handle: object | None = None
    try:
        _wait_for_helper_file(pid_path, helper, timeout_seconds=10.0)
        child_process_id = int(pid_path.read_text(encoding="utf-8"))
        monitor_handle = _open_process_monitor(child_process_id)

        assert _wait_for_process_exit(monitor_handle, timeout_ms=100) is False

        release_path.write_text("exit", encoding="utf-8")
        stdout, stderr = helper.communicate(timeout=10)

        assert stdout == ""
        assert stderr == ""
        assert helper.returncode == 77
        assert _wait_for_process_exit(monitor_handle, timeout_ms=5_000)
        assert _get_exit_code_process(monitor_handle) != _STILL_ACTIVE
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.communicate(timeout=5)
        if monitor_handle is not None:
            _terminate_if_still_active(monitor_handle)
            _close_handle(monitor_handle)


def _kernel32() -> object:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _open_process_monitor(process_id: int) -> object:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE,
        False,
        process_id,
    )
    if not handle:
        _raise_last_win32_error("OPEN_PROCESS")
    return handle


def _wait_for_process_exit(handle: object, *, timeout_ms: int) -> bool:
    kernel32 = _kernel32()
    result = kernel32.WaitForSingleObject(handle, timeout_ms)
    if result == _WAIT_TIMEOUT:
        return False
    if result != 0:
        _raise_last_win32_error("WAIT_FOR_PROCESS_EXIT")
    return True


def _get_exit_code_process(handle: object) -> int:
    kernel32 = _kernel32()
    exit_code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
        _raise_last_win32_error("GET_EXIT_CODE_PROCESS")
    return int(exit_code.value)


def _terminate_if_still_active(handle: object) -> None:
    if _get_exit_code_process(handle) != _STILL_ACTIVE:
        return
    kernel32 = _kernel32()
    kernel32.TerminateProcess(handle, 97)
    _wait_for_process_exit(handle, timeout_ms=5_000)


def _wait_for_helper_file(
    path: Path,
    helper: subprocess.Popen[str],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if helper.poll() is not None:
            stdout, stderr = helper.communicate(timeout=1)
            raise AssertionError(
                f"helper exited before creating {path.name}: "
                f"returncode={helper.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for helper file {path}")


def _close_handle(handle: object) -> None:
    kernel32 = _kernel32()
    if not kernel32.CloseHandle(handle):
        _raise_last_win32_error("CLOSE_HANDLE")


def _raise_last_win32_error(operation: str) -> None:
    code = ctypes.get_last_error()
    raise AssertionError(f"{operation}_FAILED:{code}:{ctypes.FormatError(code)}")
