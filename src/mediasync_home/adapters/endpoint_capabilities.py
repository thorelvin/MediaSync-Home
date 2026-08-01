from __future__ import annotations

import ctypes
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from ctypes import wintypes

from mediasync_home.application.endpoint_capabilities import (
    CaseMode,
    DurabilityLevel,
    EndpointCapabilities,
    EndpointCapabilityEvidence,
    EndpointCapabilityProbeError,
    EndpointCapabilityProbeScope,
    FileIdReliability,
    LockScope,
    SourceReadGuardLevel,
)
from mediasync_home.adapters.windows_durability import move_path_write_through


_FILE_CASE_SENSITIVE_SEARCH = 0x00000001
_FILE_CASE_PRESERVED_NAMES = 0x00000002
_FILE_SUPPORTS_SPARSE_FILES = 0x00000040
_FILE_SUPPORTS_ENCRYPTION = 0x00020000
_FILE_NAMED_STREAMS = 0x00040000
_FILE_SUPPORTS_HARD_LINKS = 0x00400000
_DRIVE_REMOVABLE = 2
_DRIVE_REMOTE = 4


class LocalWindowsEndpointCapabilitiesProbe:
    def probe_read_only(self, root: Path) -> EndpointCapabilityEvidence:
        return EndpointCapabilityEvidence.from_profile(self._base_profile(root))

    def probe_controlled_writable(
        self,
        root: Path,
        *,
        probe_directory: Path,
        probe_token: str,
    ) -> EndpointCapabilityEvidence:
        profile = self._base_profile(root)
        prefix = f"capabilities-{probe_token}"
        paths = tuple(probe_directory / f"{prefix}-{suffix}" for suffix in range(8))
        self._cleanup(paths)
        named_streams = False
        try:
            self._write_new(paths[0], b"rename-source\n")
            move_path_write_through(
                paths[0],
                paths[1],
                replace_existing=False,
            )
            atomic_rename = paths[1].read_bytes() == b"rename-source\n"

            self._write_new(paths[2], b"no-overwrite-source\n")
            self._write_new(paths[3], b"no-overwrite-target\n")
            try:
                move_path_write_through(
                    paths[2],
                    paths[3],
                    replace_existing=False,
                )
            except FileExistsError:
                no_overwrite = paths[3].read_bytes() == b"no-overwrite-target\n"
            else:
                no_overwrite = False

            self._write_new(paths[4], b"replace-old\n")
            self._write_new(paths[5], b"replace-new\n")
            move_path_write_through(
                paths[5],
                paths[4],
                replace_existing=True,
            )
            atomic_replace = paths[4].read_bytes() == b"replace-new\n"

            self._write_new(paths[6], b"stream-base\n")
            if profile.supports_named_streams:
                stream_path = Path(f"{paths[6]}:mediasync-capabilities")
                self._write_new(stream_path, b"stream-evidence\n")
                named_streams = stream_path.read_bytes() == b"stream-evidence\n"
                stream_path.unlink()
        except OSError as exc:
            raise EndpointCapabilityProbeError(
                "ENDPOINT_CONTROLLED_CAPABILITY_PROBE_FAILED",
                "Restore target create, rename, replace, flush and delete access, then retry.",
            ) from exc
        finally:
            self._cleanup(paths)

        if not atomic_rename or not no_overwrite or not atomic_replace:
            raise EndpointCapabilityProbeError(
                "ENDPOINT_REQUIRED_WRITE_CAPABILITY_MISSING",
                "Use a local target that supports safe rename, no-overwrite and replace operations.",
            )
        return EndpointCapabilityEvidence.from_profile(
            replace(
                profile,
                probe_scope=EndpointCapabilityProbeScope.CONTROLLED_WRITABLE,
                supports_atomic_rename=True,
                supports_no_overwrite_insert=True,
                supports_atomic_replace=True,
                supports_file_flush=True,
                supports_write_through_move=True,
                durability_level=(
                    DurabilityLevel.FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED
                ),
                supports_named_streams=named_streams,
            )
        )

    def _base_profile(self, root: Path) -> EndpointCapabilities:
        volume = _read_volume_evidence(root)
        flags = volume.flags
        return EndpointCapabilities(
            probe_scope=EndpointCapabilityProbeScope.READ_ONLY,
            filesystem_name=volume.filesystem_name,
            maximum_file_size=_maximum_file_size(volume.filesystem_name),
            maximum_component_length=volume.maximum_component_length,
            maximum_path_length=32_760,
            timestamp_precision_ns=_timestamp_precision_ns(volume.filesystem_name),
            default_case_mode=(
                CaseMode.INSENSITIVE
                if flags & _FILE_CASE_PRESERVED_NAMES
                else CaseMode.UNKNOWN
            ),
            supports_per_directory_case_query=bool(flags & _FILE_CASE_SENSITIVE_SEARCH),
            supports_reparse_inspection=True,
            supports_final_path_resolution=True,
            supports_directory_identity_handles=True,
            supports_atomic_rename=False,
            supports_no_overwrite_insert=False,
            supports_atomic_replace=False,
            supports_file_flush=False,
            supports_write_through_move=False,
            durability_level=DurabilityLevel.UNKNOWN,
            lock_scope=(LockScope.UNKNOWN if volume.is_network else LockScope.LOCAL_MACHINE),
            supports_exclusive_control_lock=False,
            source_read_guard_level=SourceReadGuardLevel.POST_TRANSFER_HASH_ONLY,
            supports_file_ids=True,
            file_id_reliability=FileIdReliability.STABLE,
            supports_birthtime=True,
            supports_attributes=True,
            supports_named_streams=bool(flags & _FILE_NAMED_STREAMS),
            supports_sparse_files=bool(flags & _FILE_SUPPORTS_SPARSE_FILES),
            supports_hardlinks=bool(flags & _FILE_SUPPORTS_HARD_LINKS),
            supports_encryption=bool(flags & _FILE_SUPPORTS_ENCRYPTION),
            supports_long_paths=True,
            is_network=volume.is_network,
            is_removable=volume.is_removable,
            likely_rotational=None,
        )

    @staticmethod
    def _write_new(path: Path, payload: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _cleanup(paths: tuple[Path, ...]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise EndpointCapabilityProbeError(
                    "ENDPOINT_CAPABILITY_PROBE_CLEANUP_FAILED",
                    "Restore target delete access and retry the controlled probe.",
                ) from exc


class _VolumeEvidence:
    def __init__(
        self,
        *,
        filesystem_name: str,
        maximum_component_length: int,
        flags: int,
        is_network: bool,
        is_removable: bool,
    ) -> None:
        self.filesystem_name = filesystem_name
        self.maximum_component_length = maximum_component_length
        self.flags = flags
        self.is_network = is_network
        self.is_removable = is_removable


def _read_volume_evidence(root: Path) -> _VolumeEvidence:
    if os.name != "nt":
        raise EndpointCapabilityProbeError(
            "ENDPOINT_CAPABILITY_PROBE_REQUIRES_WINDOWS",
            "Run the local endpoint probe on Windows.",
        )
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    get_volume_path = kernel32.GetVolumePathNameW
    get_volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_volume_path.restype = wintypes.BOOL
    get_volume_information = kernel32.GetVolumeInformationW
    get_volume_information.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_volume_information.restype = wintypes.BOOL
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [wintypes.LPCWSTR]
    get_drive_type.restype = wintypes.UINT

    volume_path = ctypes.create_unicode_buffer(32_768)
    if not get_volume_path(str(root.resolve()), volume_path, len(volume_path)):
        raise _win32_probe_error("ENDPOINT_VOLUME_PATH_QUERY_FAILED")
    filesystem_name = ctypes.create_unicode_buffer(256)
    serial = wintypes.DWORD()
    maximum_component_length = wintypes.DWORD()
    flags = wintypes.DWORD()
    if not get_volume_information(
        volume_path.value,
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(maximum_component_length),
        ctypes.byref(flags),
        filesystem_name,
        len(filesystem_name),
    ):
        raise _win32_probe_error("ENDPOINT_VOLUME_INFORMATION_QUERY_FAILED")
    drive_type = int(get_drive_type(volume_path.value))
    return _VolumeEvidence(
        filesystem_name=filesystem_name.value,
        maximum_component_length=int(maximum_component_length.value),
        flags=int(flags.value),
        is_network=drive_type == _DRIVE_REMOTE,
        is_removable=drive_type == _DRIVE_REMOVABLE,
    )


def _maximum_file_size(filesystem_name: str) -> int | None:
    normalized = filesystem_name.upper()
    if normalized in {"FAT", "FAT32"}:
        return (1 << 32) - 1
    if normalized in {"NTFS", "EXFAT", "UDF"}:
        return (1 << 64) - 1
    if normalized == "REFS":
        return 35 * (1000**5)
    return None


def _timestamp_precision_ns(filesystem_name: str) -> int:
    if filesystem_name.upper() in {"FAT", "FAT32"}:
        return 2_000_000_000
    return 100


def _win32_probe_error(code: str) -> EndpointCapabilityProbeError:
    error = ctypes.get_last_error()
    return EndpointCapabilityProbeError(
        code,
        f"Reconnect the endpoint and retry the read-only volume query (Win32 {error}).",
    )
