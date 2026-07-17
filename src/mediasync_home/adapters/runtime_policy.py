from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from mediasync_home.application.runtime_policy import (
    RuntimePolicyStatus,
    evaluate_runtime_policy,
)


TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


class TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


def current_process_runtime_policy(repo_root: Path) -> RuntimePolicyStatus:
    return evaluate_runtime_policy(
        elevated=_current_process_is_elevated(),
        controlled_current_directory=_is_relative_to(Path.cwd().resolve(), repo_root.resolve()),
        dll_search_policy="LOCAL_DEV_NO_CHILD_PROCESS_DLL_SEARCH_SURFACE",
        handle_inheritance_policy="NO_CHILD_PROCESS_SPAWNED_BY_ROLE_RUNNER",
    )


def _current_process_is_elevated() -> bool | None:
    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL

    process = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        elevation = TokenElevation()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_ELEVATION,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            return None
        return bool(elevation.TokenIsElevated)
    finally:
        kernel32.CloseHandle(token)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
