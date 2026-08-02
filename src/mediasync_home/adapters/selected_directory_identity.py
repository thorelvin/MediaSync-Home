from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

from mediasync_home.adapters.reparse_guard import (
    LocalFilesystemReparsePathProbe,
    ReparseGuardError,
    ReparsePathProbe,
)
from mediasync_home.application.selected_directory_identity import (
    SelectedDirectoryProbeError,
    SelectedDirectoryProbeEvidence,
    StorageIdentityTrust,
)


_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_DRIVE_REMOTE = 4
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
_INVALID_HANDLE_VALUE = int(wintypes.HANDLE(-1).value or -1)


class _StorageDeviceNumber(ctypes.Structure):
    _fields_ = [
        ("device_type", wintypes.DWORD),
        ("device_number", wintypes.DWORD),
        ("partition_number", wintypes.DWORD),
    ]


class LocalSelectedDirectoryIdentityProbe:
    def __init__(self, *, path_probe: ReparsePathProbe | None = None) -> None:
        self._path_probe = path_probe or LocalFilesystemReparsePathProbe()

    def inspect_directory(
        self,
        path_label: str,
    ) -> SelectedDirectoryProbeEvidence:
        path = Path(path_label)
        if not path.is_absolute():
            raise SelectedDirectoryProbeError(
                "SELECTED_DIRECTORY_REQUIRES_ABSOLUTE_PATH"
            )
        try:
            inspection = self._path_probe.inspect_path(path)
        except ReparseGuardError as exc:
            raise SelectedDirectoryProbeError(exc.validation_code) from exc
        if not inspection.exists:
            raise SelectedDirectoryProbeError("SELECTED_DIRECTORY_NOT_FOUND")
        if inspection.is_reparse_point:
            raise SelectedDirectoryProbeError("SELECTED_DIRECTORY_REPARSE_BLOCKED")
        if inspection.identity is None or inspection.final_path is None:
            raise SelectedDirectoryProbeError(
                "SELECTED_DIRECTORY_IDENTITY_EVIDENCE_UNAVAILABLE"
            )
        try:
            if not path.is_dir():
                raise SelectedDirectoryProbeError("SELECTED_DIRECTORY_IS_NOT_DIRECTORY")
            storage_key, storage_trust = _storage_identity(path)
        except OSError as exc:
            raise SelectedDirectoryProbeError(
                "SELECTED_DIRECTORY_INSPECTION_FAILED"
            ) from exc
        return SelectedDirectoryProbeEvidence(
            object_identity_key=(
                f"{inspection.identity.kind}:{inspection.identity.value}"
            ),
            final_path=inspection.final_path,
            storage_identity_key=storage_key,
            storage_identity_trust=storage_trust,
        )


def _storage_identity(path: Path) -> tuple[str | None, StorageIdentityTrust]:
    if os.name != "nt":
        return (
            f"posix-device:{int(path.stat(follow_symlinks=False).st_dev)}",
            StorageIdentityTrust.CONFIRMED,
        )
    return _windows_storage_identity(path)


def _windows_storage_identity(
    path: Path,
) -> tuple[str | None, StorageIdentityTrust]:
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    get_volume_path = kernel32.GetVolumePathNameW
    get_volume_path.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    get_volume_path.restype = wintypes.BOOL
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = (wintypes.LPCWSTR,)
    get_drive_type.restype = wintypes.UINT
    get_volume_name = kernel32.GetVolumeNameForVolumeMountPointW
    get_volume_name.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_volume_name.restype = wintypes.BOOL
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
    device_io_control = kernel32.DeviceIoControl
    device_io_control.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    device_io_control.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    volume_path = ctypes.create_unicode_buffer(32_768)
    if not get_volume_path(str(path.resolve()), volume_path, len(volume_path)):
        return None, StorageIdentityTrust.UNKNOWN
    normalized_volume_path = volume_path.value.rstrip("\\/").casefold()
    if int(get_drive_type(volume_path.value)) == _DRIVE_REMOTE:
        return (
            f"network-volume:{normalized_volume_path}",
            StorageIdentityTrust.LOGICAL_ONLY,
        )

    volume_name = ctypes.create_unicode_buffer(128)
    if get_volume_name(volume_path.value, volume_name, len(volume_name)):
        handle_path = volume_name.value.rstrip("\\/")
        if handle_path.startswith("\\\\?\\"):
            handle_path = "\\\\.\\" + handle_path[4:]
    elif len(volume_path.value) >= 2 and volume_path.value[1] == ":":
        handle_path = f"\\\\.\\{volume_path.value[:2]}"
    else:
        return None, StorageIdentityTrust.UNKNOWN

    handle = create_file(
        handle_path,
        0,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    handle_value = int(handle or 0)
    if handle_value in {0, _INVALID_HANDLE_VALUE}:
        return None, StorageIdentityTrust.UNKNOWN
    try:
        device_number = _StorageDeviceNumber()
        returned = wintypes.DWORD()
        if not device_io_control(
            wintypes.HANDLE(handle_value),
            _IOCTL_STORAGE_GET_DEVICE_NUMBER,
            None,
            0,
            ctypes.byref(device_number),
            ctypes.sizeof(device_number),
            ctypes.byref(returned),
            None,
        ):
            return None, StorageIdentityTrust.UNKNOWN
        return (
            f"windows-storage:{int(device_number.device_type)}:"
            f"{int(device_number.device_number)}",
            StorageIdentityTrust.CONFIRMED,
        )
    finally:
        close_handle(wintypes.HANDLE(handle_value))
