from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from mediasync_home.application.host_locator import validate_local_preview_mutex_name


ERROR_ALREADY_EXISTS = 183


class EngineHostMutexError(RuntimeError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass
class LocalEngineHostMutex:
    name: str
    _handle: int | None

    @classmethod
    def acquire(cls, name: str) -> "LocalEngineHostMutex":
        validate_local_preview_mutex_name(name)
        if os.name != "nt":
            raise EngineHostMutexError("ENGINE_HOST_MUTEX_REQUIRES_WINDOWS")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]

        handle = kernel32.CreateMutexW(None, True, name)
        if not handle:
            code = ctypes.get_last_error()
            raise EngineHostMutexError(f"ENGINE_HOST_MUTEX_CREATE_FAILED_{code}")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise EngineHostMutexError("ENGINE_HOST_ALREADY_RUNNING")
        return cls(name=name, _handle=int(handle))

    def close(self) -> None:
        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex(self._handle)
        kernel32.CloseHandle(self._handle)
        self._handle = None

    def __enter__(self) -> "LocalEngineHostMutex":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
