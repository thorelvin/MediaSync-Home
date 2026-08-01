from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any, ClassVar

from ctypes import wintypes

from mediasync_home.application.named_streams import (
    NamedStreamInspection,
    NamedStreamState,
)


ERROR_HANDLE_EOF = 38
FIND_STREAM_INFO_STANDARD = 0
MAX_STREAM_NAME_LENGTH = 260 + 36
MAX_STREAM_RECORDS = 64
INVALID_HANDLE_VALUE = int(wintypes.HANDLE(-1).value or -1)


class NoNamedStreamProbe:
    def inspect_named_streams(self, path: Path) -> NamedStreamInspection:
        del path
        return NamedStreamInspection(state=NamedStreamState.NONE)


class Win32NamedStreamProbe:
    def inspect_named_streams(self, path: Path) -> NamedStreamInspection:
        if os.name != "nt":
            return NamedStreamInspection(
                state=NamedStreamState.UNKNOWN,
                error_code="SNAPSHOT_NAMED_STREAM_ENUMERATION_UNCONFIRMED",
            )
        kernel32 = _stream_kernel32()
        data = _Win32FindStreamData()
        handle = kernel32.FindFirstStreamW(
            str(path),
            FIND_STREAM_INFO_STANDARD,
            ctypes.byref(data),
            0,
        )
        handle_value = _handle_value(handle)
        if handle_value == INVALID_HANDLE_VALUE:
            error = ctypes.get_last_error()
            if error == ERROR_HANDLE_EOF:
                return NamedStreamInspection(state=NamedStreamState.NONE)
            return NamedStreamInspection(
                state=NamedStreamState.UNKNOWN,
                error_code="SNAPSHOT_NAMED_STREAM_ENUMERATION_UNCONFIRMED",
            )

        try:
            for record_index in range(MAX_STREAM_RECORDS):
                stream_name = str(data.stream_name)
                if not _is_default_data_stream(stream_name):
                    return NamedStreamInspection(
                        state=NamedStreamState.PRESENT,
                        observed_named_stream_count=1,
                    )
                if record_index == MAX_STREAM_RECORDS - 1:
                    return NamedStreamInspection(
                        state=NamedStreamState.UNKNOWN,
                        error_code="SNAPSHOT_NAMED_STREAM_ENUMERATION_UNCONFIRMED",
                    )
                if kernel32.FindNextStreamW(handle, ctypes.byref(data)):
                    continue
                error = ctypes.get_last_error()
                if error == ERROR_HANDLE_EOF:
                    return NamedStreamInspection(state=NamedStreamState.NONE)
                return NamedStreamInspection(
                    state=NamedStreamState.UNKNOWN,
                    error_code="SNAPSHOT_NAMED_STREAM_ENUMERATION_UNCONFIRMED",
                )
        finally:
            kernel32.FindClose(handle)

        raise AssertionError("unreachable named-stream enumeration state")


class _LargeInteger(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [("value", ctypes.c_longlong)]


class _Win32FindStreamData(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("stream_size", _LargeInteger),
        ("stream_name", wintypes.WCHAR * MAX_STREAM_NAME_LENGTH),
    ]


def _stream_kernel32() -> Any:
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FindFirstStreamW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.INT,
        ctypes.POINTER(_Win32FindStreamData),
        wintypes.DWORD,
    ]
    kernel32.FindFirstStreamW.restype = wintypes.HANDLE
    kernel32.FindNextStreamW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Win32FindStreamData),
    ]
    kernel32.FindNextStreamW.restype = wintypes.BOOL
    kernel32.FindClose.argtypes = [wintypes.HANDLE]
    kernel32.FindClose.restype = wintypes.BOOL
    return kernel32


def _handle_value(handle: object) -> int:
    if isinstance(handle, int):
        return handle
    value = getattr(handle, "value", None)
    return 0 if value is None else int(value)


def _is_default_data_stream(stream_name: str) -> bool:
    return stream_name.upper() == "::$DATA"
