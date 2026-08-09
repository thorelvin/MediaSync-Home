from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


MOVEFILE_REPLACE_EXISTING = 0x00000001
MOVEFILE_WRITE_THROUGH = 0x00000008
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183


def move_path_write_through(
    source: Path,
    destination: Path,
    *,
    replace_existing: bool,
) -> None:
    if os.name != "nt":
        raise OSError("WRITE_THROUGH_MOVE_REQUIRES_WINDOWS")
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file_ex.restype = wintypes.BOOL
    flags = MOVEFILE_WRITE_THROUGH
    if replace_existing:
        flags |= MOVEFILE_REPLACE_EXISTING
    if move_file_ex(
        _extended_windows_path(source),
        _extended_windows_path(destination),
        flags,
    ):
        return
    error_code = ctypes.get_last_error()
    message = ctypes.FormatError(error_code)
    if error_code in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
        raise FileExistsError(
            error_code,
            message,
            str(destination),
        )
    raise OSError(
        error_code,
        message,
        str(source),
        str(destination),
    )


def replace_file_with_backup(
    replacement: Path,
    replaced: Path,
    backup: Path,
) -> None:
    """Atomically replace a Windows file while retaining the replaced file."""
    if os.name != "nt":
        raise OSError("REPLACE_FILE_WITH_BACKUP_REQUIRES_WINDOWS")
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    replace_file.restype = wintypes.BOOL
    if replace_file(
        _extended_windows_path(replaced),
        _extended_windows_path(replacement),
        _extended_windows_path(backup),
        0,
        None,
        None,
    ):
        return
    error_code = ctypes.get_last_error()
    message = f"{ctypes.FormatError(error_code)} (backup: {backup})"
    raise OSError(
        error_code,
        message,
        str(replaced),
        None,
        str(replacement),
    )


def _extended_windows_path(path: Path) -> str:
    absolute = os.path.abspath(os.fspath(path))
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute
