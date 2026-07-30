from __future__ import annotations

import ctypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol, SupportsInt, cast
from ctypes import wintypes


FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_READ_ATTRIBUTES = 0x00000080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
INVALID_HANDLE_VALUE = int(wintypes.HANDLE(-1).value or -1)


class ReparseGuardError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True)
class FileIdentityEvidence:
    kind: str
    value: str


@dataclass(frozen=True)
class ReparseInspection:
    path: Path
    exists: bool
    is_reparse_point: bool
    identity: FileIdentityEvidence | None = None
    final_path: str | None = None


@dataclass(frozen=True)
class ReparseGuardEvidence:
    root: Path
    checked_path: Path
    inspected_paths: tuple[Path, ...]
    inspected_identities: tuple[FileIdentityEvidence, ...] = ()


class ReparsePathProbe(Protocol):
    def inspect_path(self, path: Path) -> ReparseInspection: ...


class ReparseGuard(Protocol):
    def resolve_existing_root(
        self,
        root: Path,
        *,
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
    ) -> Path: ...

    def reject_reparse_chain(
        self,
        *,
        root: Path,
        relative_parts: tuple[str, ...],
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
        allow_missing_suffix: bool = False,
    ) -> ReparseGuardEvidence: ...

    def require_resolved_under_root(
        self,
        *,
        root: Path,
        path: Path,
        strict: bool,
        escape_code: str,
        escape_next_action: str,
    ) -> None: ...


class LocalFilesystemReparsePathProbe:
    def inspect_path(self, path: Path) -> ReparseInspection:
        if os.name == "nt":
            return _inspect_path_with_windows_handle(path)
        try:
            stat_result = path.lstat()
        except FileNotFoundError:
            return ReparseInspection(path=path, exists=False, is_reparse_point=False)
        except OSError as exc:
            raise ReparseGuardError(
                "REPARSE_GUARD_INSPECTION_FAILED",
                "Retry after the filesystem path can be inspected without errors.",
            ) from exc
        identity = FileIdentityEvidence(
            kind="POSIX_LSTAT_DEVICE_INODE",
            value=f"{int(stat_result.st_dev)}:{int(stat_result.st_ino)}",
        )
        attributes = int(getattr(stat_result, "st_file_attributes", 0))
        return ReparseInspection(
            path=path,
            exists=True,
            is_reparse_point=bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
            or stat.S_ISLNK(stat_result.st_mode),
            identity=identity,
            final_path=str(path.resolve(strict=False)),
        )


class LocalReparseGuard:
    def __init__(self, *, probe: ReparsePathProbe | None = None) -> None:
        self._probe = probe or LocalFilesystemReparsePathProbe()

    def resolve_existing_root(
        self,
        root: Path,
        *,
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
    ) -> Path:
        inspection = self._probe.inspect_path(root)
        if not inspection.exists:
            raise ReparseGuardError(missing_code, missing_next_action)
        if inspection.is_reparse_point:
            raise ReparseGuardError(reparse_code, reparse_next_action)
        try:
            return root.resolve(strict=True)
        except OSError as exc:
            raise ReparseGuardError(missing_code, missing_next_action) from exc

    def reject_reparse_chain(
        self,
        *,
        root: Path,
        relative_parts: tuple[str, ...],
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
        allow_missing_suffix: bool = False,
    ) -> ReparseGuardEvidence:
        inspected_paths: list[Path] = []
        inspected_identities: list[FileIdentityEvidence] = []
        root_inspection = self._probe.inspect_path(root)
        if not root_inspection.exists:
            raise ReparseGuardError(missing_code, missing_next_action)
        if root_inspection.is_reparse_point:
            raise ReparseGuardError(reparse_code, reparse_next_action)
        inspected_paths.append(root)
        if root_inspection.identity is not None:
            inspected_identities.append(root_inspection.identity)

        current = root
        for part in relative_parts:
            current = current / part
            inspection = self._probe.inspect_path(current)
            if not inspection.exists:
                if allow_missing_suffix:
                    break
                raise ReparseGuardError(missing_code, missing_next_action)
            if inspection.is_reparse_point:
                raise ReparseGuardError(reparse_code, reparse_next_action)
            inspected_paths.append(current)
            if inspection.identity is not None:
                inspected_identities.append(inspection.identity)
        return ReparseGuardEvidence(
            root=root,
            checked_path=current,
            inspected_paths=tuple(inspected_paths),
            inspected_identities=tuple(inspected_identities),
        )

    def require_resolved_under_root(
        self,
        *,
        root: Path,
        path: Path,
        strict: bool,
        escape_code: str,
        escape_next_action: str,
    ) -> None:
        if os.name == "nt":
            self._require_windows_final_path_under_root(
                root=root,
                path=path,
                strict=strict,
                escape_code=escape_code,
                escape_next_action=escape_next_action,
            )
            return
        try:
            path.resolve(strict=strict).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ReparseGuardError(escape_code, escape_next_action) from exc

    def _require_windows_final_path_under_root(
        self,
        *,
        root: Path,
        path: Path,
        strict: bool,
        escape_code: str,
        escape_next_action: str,
    ) -> None:
        root_inspection = self._probe.inspect_path(root)
        path_inspection = self._probe.inspect_path(path)
        if (
            root_inspection.exists
            and path_inspection.exists
            and root_inspection.final_path is not None
            and path_inspection.final_path is not None
        ):
            if _windows_final_path_is_under_root(
                child=path_inspection.final_path,
                root=root_inspection.final_path,
            ):
                return
            raise ReparseGuardError(escape_code, escape_next_action)
        try:
            path.resolve(strict=strict).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ReparseGuardError(escape_code, escape_next_action) from exc


class _ByHandleFileInformation(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _inspect_path_with_windows_handle(path: Path) -> ReparseInspection:
    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        str(path),
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    handle_value = _handle_value(handle)
    if handle_value in {0, INVALID_HANDLE_VALUE}:
        error_code = ctypes.get_last_error()
        if error_code in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}:
            return ReparseInspection(path=path, exists=False, is_reparse_point=False)
        raise ReparseGuardError(
            "REPARSE_GUARD_INSPECTION_FAILED",
            f"Retry after the filesystem path can be inspected; win32_error={error_code}.",
        )
    try:
        information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            wintypes.HANDLE(handle_value),
            ctypes.byref(information),
        ):
            error_code = ctypes.get_last_error()
            raise ReparseGuardError(
                "REPARSE_GUARD_INSPECTION_FAILED",
                f"Retry after file identity can be inspected; win32_error={error_code}.",
            )
        final_path = _get_final_path_by_handle(kernel32=kernel32, handle_value=handle_value)
        file_index = (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow)
        identity = FileIdentityEvidence(
            kind="WIN32_HANDLE_VOLUME_FILE_ID",
            value=f"{int(information.dwVolumeSerialNumber):08x}:{file_index:016x}",
        )
        attributes = int(information.dwFileAttributes)
        return ReparseInspection(
            path=path,
            exists=True,
            is_reparse_point=bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT),
            identity=identity,
            final_path=final_path,
        )
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle_value))


def _get_final_path_by_handle(*, kernel32: Any, handle_value: int) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel32.GetFinalPathNameByHandleW(
        wintypes.HANDLE(handle_value),
        buffer,
        len(buffer),
        0,
    )
    if size == 0:
        error_code = ctypes.get_last_error()
        raise ReparseGuardError(
            "REPARSE_GUARD_FINAL_PATH_FAILED",
            f"Retry after the filesystem path can be resolved; win32_error={error_code}.",
        )
    if size >= len(buffer):
        raise ReparseGuardError(
            "REPARSE_GUARD_FINAL_PATH_EXCEEDED_BUFFER",
            "Retry after endpoint paths fit the supported final-path buffer.",
        )
    return buffer.value


def _windows_final_path_is_under_root(*, child: str, root: str) -> bool:
    normalized_child = os.path.normcase(_strip_windows_device_prefix(child).rstrip("\\/"))
    normalized_root = os.path.normcase(_strip_windows_device_prefix(root).rstrip("\\/"))
    if normalized_child == normalized_root:
        return True
    return normalized_child.startswith(f"{normalized_root}\\")


def _strip_windows_device_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _kernel32() -> Any:
    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _handle_value(handle: object) -> int:
    value: object | None = getattr(handle, "value", None)
    if isinstance(value, int):
        return value
    if value is None:
        if isinstance(handle, int):
            return handle
        return 0
    return int(cast(SupportsInt, value))
