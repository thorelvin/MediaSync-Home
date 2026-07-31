from __future__ import annotations

import ctypes
import os
import subprocess
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Protocol

from mediasync_home.adapters.windows_argv import WindowsCommandLineError, build_windows_command_line
from mediasync_home.application.process_supervision import (
    ChildContainmentPolicy,
    DllSearchPolicy,
    HandleInheritancePolicy,
    ProcessLaunchPlan,
    ProcessLaunchViolation,
    WindowMode,
)


_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_INFINITE = 0xFFFFFFFF
_WAIT_TIMEOUT = 0x00000102
_WIN32_UNSIGNED_FAILURE = 0xFFFFFFFF
_STILL_ACTIVE = 259
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_TRANSFER_CHILD_CONTAINMENT_FAILURE_EXIT_CODE = 99


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", wintypes.LPVOID),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


@dataclass(frozen=True)
class Win32ProcessHandles:
    process_handle: object
    thread_handle: object
    process_id: int


class Win32ProcessApi(Protocol):
    def create_suspended_process(
        self,
        plan: ProcessLaunchPlan,
        *,
        command_line: str,
    ) -> Win32ProcessHandles: ...

    def create_kill_on_close_job(self) -> object: ...

    def assign_process_to_job(self, job_handle: object, process_handle: object) -> None: ...

    def resume_thread(self, thread_handle: object) -> None: ...

    def wait_for_process(self, process_handle: object, *, timeout_ms: int) -> bool: ...

    def get_exit_code_process(self, process_handle: object) -> int: ...

    def terminate_process(self, process_handle: object, *, exit_code: int) -> None: ...

    def close_handle(self, handle: object) -> None: ...


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


@dataclass
class ContainedTransferProcess:
    process_id: int
    _api: Win32ProcessApi
    _process_handle: object | None
    _job_handle: object | None

    def poll(self) -> int | None:
        if self._process_handle is None:
            return None
        exit_code = self._api.get_exit_code_process(self._process_handle)
        if exit_code == _STILL_ACTIVE:
            return None
        return exit_code

    def wait(self, *, timeout_seconds: float | None = None) -> int | None:
        if self._process_handle is None:
            return None
        timeout_ms = _INFINITE if timeout_seconds is None else int(timeout_seconds * 1000)
        if not self._api.wait_for_process(self._process_handle, timeout_ms=timeout_ms):
            return None
        return self._api.get_exit_code_process(self._process_handle)

    def terminate(self, *, exit_code: int = 1) -> None:
        try:
            if self._process_handle is not None:
                self._api.terminate_process(self._process_handle, exit_code=exit_code)
        finally:
            self.close()

    def close(self) -> None:
        failures: list[tuple[str, Exception]] = []
        if self._job_handle is not None:
            job_handle = self._job_handle
            try:
                self._api.close_handle(job_handle)
            except Exception as exc:
                failures.append(("job", exc))
            else:
                self._job_handle = None
        if self._process_handle is not None:
            process_handle = self._process_handle
            try:
                self._api.close_handle(process_handle)
            except Exception as exc:
                failures.append(("process", exc))
            else:
                self._process_handle = None
        if failures:
            failed_handles = ",".join(name for name, _exc in failures)
            raise ProcessLaunchViolation(
                f"TRANSFER_CHILD_HANDLE_CLOSE_FAILED:{failed_handles}"
            ) from failures[0][1]


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


class Win32JobObjectTransferSupervisor:
    def __init__(self, *, api: Win32ProcessApi | None = None) -> None:
        self._api = api

    def start(self, plan: ProcessLaunchPlan) -> ContainedTransferProcess:
        _assert_transfer_child_plan_safe(plan)
        api = self._api or _DefaultWin32ProcessApi()
        try:
            command_line = build_windows_command_line(plan.command_line_vector())
        except WindowsCommandLineError as exc:
            raise ProcessLaunchViolation("WINDOWS_COMMAND_LINE_INVALID") from exc

        handles = api.create_suspended_process(plan, command_line=command_line)
        job_handle: object | None = None
        try:
            job_handle = api.create_kill_on_close_job()
            api.assign_process_to_job(job_handle, handles.process_handle)
            api.resume_thread(handles.thread_handle)
        except Exception as exc:
            _cleanup_failed_transfer_child(api, handles, job_handle)
            raise ProcessLaunchViolation("CHILD_PROCESS_CONTAINMENT_FAILED") from exc

        try:
            api.close_handle(handles.thread_handle)
        except Exception as exc:
            with suppress(Exception):
                api.terminate_process(
                    handles.process_handle,
                    exit_code=_TRANSFER_CHILD_CONTAINMENT_FAILURE_EXIT_CODE,
                )
            _close_handle_safely(api, job_handle)
            _close_handle_safely(api, handles.process_handle)
            raise ProcessLaunchViolation("TRANSFER_CHILD_THREAD_HANDLE_CLOSE_FAILED") from exc

        return ContainedTransferProcess(
            process_id=handles.process_id,
            _api=api,
            _process_handle=handles.process_handle,
            _job_handle=job_handle,
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
        raise ProcessLaunchViolation("LOCAL_SUBPROCESS_TRANSFER_CHILD_FORBIDDEN")
    if any(name.upper() == "PATH" for name, _value in plan.environment):
        raise ProcessLaunchViolation("PATH_ENVIRONMENT_FORBIDDEN_IN_MINIMAL_ROLE_PLAN")


def _assert_transfer_child_plan_safe(plan: ProcessLaunchPlan) -> None:
    if plan.containment_policy is not ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT:
        raise ProcessLaunchViolation("TRANSFER_CHILD_JOB_OBJECT_POLICY_REQUIRED")
    if plan.shell:
        raise ProcessLaunchViolation("SHELL_EXECUTION_FORBIDDEN")
    if plan.requires_elevation:
        raise ProcessLaunchViolation("ELEVATION_FORBIDDEN")
    if plan.window_mode is not WindowMode.HIDDEN:
        raise ProcessLaunchViolation("TRANSFER_CHILD_WINDOW_MUST_BE_HIDDEN")
    if plan.dll_search_policy is not DllSearchPolicy.SAFE_SYSTEM32_AND_APPLICATION_DIR:
        raise ProcessLaunchViolation("TRANSFER_CHILD_DLL_SEARCH_POLICY_UNSAFE")
    if plan.handle_inheritance_policy is not HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST:
        raise ProcessLaunchViolation("TRANSFER_CHILD_HANDLE_INHERITANCE_FORBIDDEN")
    if plan.inherited_handles:
        raise ProcessLaunchViolation("HANDLE_LIST_MUST_BE_EMPTY")
    if any(name.upper() == "PATH" for name, _value in plan.environment):
        raise ProcessLaunchViolation("PATH_ENVIRONMENT_FORBIDDEN_IN_MINIMAL_ROLE_PLAN")
    if any(name.upper() == "COMSPEC" for name, _value in plan.environment):
        raise ProcessLaunchViolation("COMSPEC_ENVIRONMENT_FORBIDDEN_IN_TRANSFER_PLAN")


def _cleanup_failed_transfer_child(
    api: Win32ProcessApi,
    handles: Win32ProcessHandles,
    job_handle: object | None,
) -> None:
    with suppress(Exception):
        api.terminate_process(
            handles.process_handle,
            exit_code=_TRANSFER_CHILD_CONTAINMENT_FAILURE_EXIT_CODE,
        )
    _close_handle_safely(api, handles.thread_handle)
    _close_handle_safely(api, job_handle)
    _close_handle_safely(api, handles.process_handle)


def _close_handle_safely(api: Win32ProcessApi, handle: object | None) -> None:
    if handle is None:
        return
    with suppress(Exception):
        api.close_handle(handle)


def _creation_flags(plan: ProcessLaunchPlan) -> int:
    if os.name == "nt" and plan.window_mode is WindowMode.HIDDEN:
        return _CREATE_NO_WINDOW
    return 0


class _DefaultWin32ProcessApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise ProcessLaunchViolation("WIN32_TRANSFER_SUPERVISION_REQUIRES_WINDOWS")
        self._kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def create_suspended_process(
        self,
        plan: ProcessLaunchPlan,
        *,
        command_line: str,
    ) -> Win32ProcessHandles:
        startup_info = _StartupInfoW()
        startup_info.cb = ctypes.sizeof(_StartupInfoW)
        startup_info.dwFlags = _STARTF_USESHOWWINDOW
        startup_info.wShowWindow = _SW_HIDE
        process_info = _ProcessInformation()
        command_buffer = ctypes.create_unicode_buffer(command_line)
        environment_buffer = ctypes.create_unicode_buffer(_environment_block(plan.environment))
        creation_flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT

        if not self._kernel32.CreateProcessW(
            str(plan.executable),
            command_buffer,
            None,
            None,
            False,
            creation_flags,
            environment_buffer,
            str(plan.working_directory),
            ctypes.byref(startup_info),
            ctypes.byref(process_info),
        ):
            _raise_last_win32_error("CREATE_PROCESS")

        return Win32ProcessHandles(
            process_handle=process_info.hProcess,
            thread_handle=process_info.hThread,
            process_id=int(process_info.dwProcessId),
        )

    def create_kill_on_close_job(self) -> object:
        attributes = _SecurityAttributes()
        attributes.nLength = ctypes.sizeof(_SecurityAttributes)
        attributes.bInheritHandle = False
        job_handle = self._kernel32.CreateJobObjectW(ctypes.byref(attributes), None)
        if not job_handle:
            _raise_last_win32_error("CREATE_JOB_OBJECT")

        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            with suppress(Exception):
                self.close_handle(job_handle)
            _raise_last_win32_error("SET_JOB_OBJECT_LIMITS")
        return job_handle

    def assign_process_to_job(self, job_handle: object, process_handle: object) -> None:
        if not self._kernel32.AssignProcessToJobObject(job_handle, process_handle):
            _raise_last_win32_error("ASSIGN_PROCESS_TO_JOB")

    def resume_thread(self, thread_handle: object) -> None:
        if self._kernel32.ResumeThread(thread_handle) == _WIN32_UNSIGNED_FAILURE:
            _raise_last_win32_error("RESUME_THREAD")

    def wait_for_process(self, process_handle: object, *, timeout_ms: int) -> bool:
        result = self._kernel32.WaitForSingleObject(process_handle, timeout_ms)
        if result == _WAIT_TIMEOUT:
            return False
        if result != 0:
            _raise_last_win32_error("WAIT_FOR_PROCESS")
        return True

    def get_exit_code_process(self, process_handle: object) -> int:
        exit_code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
            _raise_last_win32_error("GET_EXIT_CODE_PROCESS")
        return int(exit_code.value)

    def terminate_process(self, process_handle: object, *, exit_code: int) -> None:
        if not self._kernel32.TerminateProcess(process_handle, exit_code):
            _raise_last_win32_error("TERMINATE_PROCESS")

    def close_handle(self, handle: object) -> None:
        if not self._kernel32.CloseHandle(handle):
            _raise_last_win32_error("CLOSE_HANDLE")

    def _configure_signatures(self) -> None:
        self._kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW),
            ctypes.POINTER(_ProcessInformation),
        ]
        self._kernel32.CreateProcessW.restype = wintypes.BOOL
        self._kernel32.CreateJobObjectW.argtypes = [
            ctypes.POINTER(_SecurityAttributes),
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self._kernel32.ResumeThread.restype = wintypes.DWORD
        self._kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        self._kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateProcess.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL


def _environment_block(environment: tuple[tuple[str, str], ...]) -> str:
    if not environment:
        return "\0"

    entries: list[str] = []
    for name, value in sorted(environment, key=lambda item: item[0].upper()):
        if not name or "=" in name or "\0" in name or "\0" in value:
            raise ProcessLaunchViolation("PROCESS_ENVIRONMENT_ENTRY_INVALID")
        entries.append(f"{name}={value}")
    return "\0".join(entries) + "\0\0"


def _raise_last_win32_error(operation: str) -> None:
    code = ctypes.get_last_error()
    raise ProcessLaunchViolation(f"{operation}_FAILED:{code}:{ctypes.FormatError(code)}")
