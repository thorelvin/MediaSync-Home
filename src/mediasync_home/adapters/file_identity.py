from __future__ import annotations

import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from typing import Any


_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_INVALID_HANDLE_VALUE = int(wintypes.HANDLE(-1).value or -1)
_WINDOWS_TO_UNIX_EPOCH_100NS = 116_444_736_000_000_000


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("low_date_time", wintypes.DWORD),
        ("high_date_time", wintypes.DWORD),
    ]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", _FileTime),
        ("last_access_time", _FileTime),
        ("last_write_time", _FileTime),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


def file_birthtime_ns(
    path: Path,
    *,
    stat_result: os.stat_result | None = None,
) -> int | None:
    """Return filesystem creation time without treating POSIX ctime as birth time."""
    if os.name == "nt":
        return _windows_file_birthtime_ns(path)
    value = stat_result if stat_result is not None else path.stat(follow_symlinks=False)
    birthtime_ns = getattr(value, "st_birthtime_ns", None)
    if isinstance(birthtime_ns, bool) or not isinstance(birthtime_ns, int):
        return None
    return birthtime_ns if birthtime_ns >= 0 else None


def _windows_file_birthtime_ns(path: Path) -> int | None:
    create_file, get_information, close_handle = _windows_file_api()
    handle = create_file(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if int(handle or 0) == _INVALID_HANDLE_VALUE:
        return None
    try:
        information = _ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            return None
        ticks = (
            int(information.creation_time.high_date_time) << 32
        ) | int(information.creation_time.low_date_time)
        if ticks < _WINDOWS_TO_UNIX_EPOCH_100NS:
            return None
        return (ticks - _WINDOWS_TO_UNIX_EPOCH_100NS) * 100
    finally:
        close_handle(handle)


@lru_cache(maxsize=1)
def _windows_file_api() -> tuple[Any, Any, Any]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return create_file, get_information, close_handle


def stable_file_identity_hash(value: os.stat_result) -> str:
    payload = {
        "attributes": int(getattr(value, "st_file_attributes", 0)),
        "birthtime_ns": int(getattr(value, "st_birthtime_ns", 0)),
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": int(value.st_mode),
        "modified_ns": int(value.st_mtime_ns),
        "size_bytes": int(value.st_size),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
