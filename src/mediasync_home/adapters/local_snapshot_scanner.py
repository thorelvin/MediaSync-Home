from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, SupportsInt, cast
from ctypes import wintypes

from mediasync_home.adapters.local_endpoint_classifier import CONTROL_DIRECTORY_NAME
from mediasync_home.adapters.reparse_guard import (
    LocalFilesystemReparsePathProbe,
    ReparseGuardError,
    ReparseInspection,
    ReparsePathProbe,
)
from mediasync_home.application.snapshot_scanning import (
    DirectoryCaseContext,
    DirectoryCaseModeProbe,
    FilesystemSnapshotScan,
)
from mediasync_home.application.snapshots import (
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotIssue,
)


FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_READ_ATTRIBUTES = 0x00000080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_CASE_SENSITIVE_INFO_CLASS = 23
FILE_CS_FLAG_CASE_SENSITIVE_DIR = 0x00000001
INVALID_HANDLE_VALUE = int(wintypes.HANDLE(-1).value or -1)
MAX_LOCAL_SNAPSHOT_ENTRIES = 100_000
MAX_LOCAL_SNAPSHOT_DIRECTORIES = 25_000


class LocalSnapshotScanError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True, slots=True)
class _QueuedDirectory:
    path: Path
    relative_path: str
    comparison_key: str


@dataclass(frozen=True, slots=True)
class _DirectorySignature:
    device: int
    inode: int
    modified_ns: int
    size: int


class LocalDirectoryCaseModeProbe(DirectoryCaseModeProbe):
    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext:
        if os.name != "nt":
            return DirectoryCaseContext(
                case_mode="CASE_SENSITIVE",
                evidence="POSIX_CASE_SENSITIVE_DEFAULT_V1",
            )
        kernel32 = _case_kernel32()
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
            return DirectoryCaseContext(
                case_mode="UNKNOWN",
                evidence="WIN32_FILE_CASE_SENSITIVE_INFO_V1",
                error_code=f"WIN32_CASE_MODE_OPEN_FAILED_{ctypes.get_last_error()}",
            )
        try:
            information = _FileCaseSensitiveInfo()
            if not kernel32.GetFileInformationByHandleEx(
                wintypes.HANDLE(handle_value),
                FILE_CASE_SENSITIVE_INFO_CLASS,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                return DirectoryCaseContext(
                    case_mode="UNKNOWN",
                    evidence="WIN32_FILE_CASE_SENSITIVE_INFO_V1",
                    error_code=f"WIN32_CASE_MODE_QUERY_FAILED_{ctypes.get_last_error()}",
                )
            return DirectoryCaseContext(
                case_mode=(
                    "CASE_SENSITIVE"
                    if int(information.flags) & FILE_CS_FLAG_CASE_SENSITIVE_DIR
                    else "CASE_INSENSITIVE"
                ),
                evidence="WIN32_FILE_CASE_SENSITIVE_INFO_V1",
            )
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle_value))


class LocalFilesystemSnapshotScanner:
    def __init__(
        self,
        *,
        path_probe: ReparsePathProbe | None = None,
        case_mode_probe: DirectoryCaseModeProbe | None = None,
        max_entries: int = MAX_LOCAL_SNAPSHOT_ENTRIES,
        max_directories: int = MAX_LOCAL_SNAPSHOT_DIRECTORIES,
    ) -> None:
        if max_entries < 1 or max_directories < 1:
            raise ValueError("snapshot scanner limits must be positive")
        self._path_probe = path_probe or LocalFilesystemReparsePathProbe()
        self._case_mode_probe = case_mode_probe or LocalDirectoryCaseModeProbe()
        self._max_entries = max_entries
        self._max_directories = max_directories

    def scan(
        self,
        root: Path,
        *,
        snapshot_id: str,
        exclude_control_area: bool,
    ) -> FilesystemSnapshotScan:
        root = Path(root)
        self._validate_root(root)
        entries: list[SnapshotFileEntry] = []
        coverage: list[SnapshotDirectoryCoverage] = []
        issues: list[SnapshotIssue] = []
        queue = deque((_QueuedDirectory(root, ".", "."),))
        scanned_directories = 0
        control_area_excluded = False

        while queue:
            queued = queue.popleft()
            if scanned_directories >= self._max_directories:
                _append_limit_block(
                    coverage,
                    issues,
                    queued=queued,
                    reason_code="SNAPSHOT_DIRECTORY_LIMIT_EXCEEDED",
                )
                break
            scanned_directories += 1
            before_inspection = self._inspect_queued_directory(queued)
            if before_inspection is None:
                coverage.append(
                    _blocked_directory_coverage(
                        queued,
                        coverage_state="UNREADABLE",
                        error_code="SNAPSHOT_DIRECTORY_REVALIDATION_FAILED",
                    )
                )
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_UNREADABLE",
                        "SNAPSHOT_DIRECTORY_REVALIDATION_FAILED",
                    )
                )
                continue
            if not before_inspection.exists:
                coverage.append(
                    _blocked_directory_coverage(
                        queued,
                        coverage_state="DISAPPEARED",
                        error_code="SNAPSHOT_DIRECTORY_DISAPPEARED",
                    )
                )
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_DISAPPEARED",
                        "SNAPSHOT_DIRECTORY_DISAPPEARED",
                    )
                )
                continue
            if before_inspection.is_reparse_point:
                coverage.append(
                    _blocked_directory_coverage(
                        queued,
                        coverage_state="REPARSE_BLOCKED",
                        error_code="SNAPSHOT_REPARSE_POINT_BLOCKED",
                    )
                )
                issues.append(
                    _issue(
                        queued.relative_path,
                        "REPARSE_POINT_NOT_TRAVERSED",
                        "SNAPSHOT_REPARSE_POINT_BLOCKED",
                    )
                )
                continue
            case_context = self._case_mode_probe.inspect_directory_case_context(queued.path)
            before = _directory_signature(queued.path)
            if before is None:
                coverage.append(
                    _coverage(
                        queued,
                        case_context,
                        coverage_state="DISAPPEARED",
                    )
                )
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_DISAPPEARED",
                        "SNAPSHOT_DIRECTORY_DISAPPEARED",
                    )
                )
                continue
            try:
                with os.scandir(queued.path) as iterator:
                    children = sorted(
                        iterator,
                        key=lambda item: (item.name.casefold(), item.name),
                    )
            except OSError:
                coverage.append(
                    _coverage(
                        queued,
                        case_context,
                        coverage_state="UNREADABLE",
                    )
                )
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_UNREADABLE",
                        "SNAPSHOT_DIRECTORY_ENUMERATION_FAILED",
                    )
                )
                continue

            limit_hit = False
            for child in children:
                if (
                    queued.relative_path == "."
                    and child.name == CONTROL_DIRECTORY_NAME
                    and exclude_control_area
                ):
                    control_area_excluded = True
                    continue
                if len(entries) >= self._max_entries:
                    _append_limit_block(
                        coverage,
                        issues,
                        queued=queued,
                        reason_code="SNAPSHOT_ENTRY_LIMIT_EXCEEDED",
                    )
                    limit_hit = True
                    break
                relative_path = _child_relative_path(queued.relative_path, child.name)
                comparison_key = _child_comparison_key(
                    queued.comparison_key,
                    child.name,
                    case_context.case_mode,
                )
                try:
                    child_stat = child.stat(follow_symlinks=False)
                except OSError:
                    issues.append(
                        _issue(
                            relative_path,
                            "ENTRY_UNREADABLE",
                            "SNAPSHOT_ENTRY_STAT_FAILED",
                        )
                    )
                    continue
                is_reparse = child.is_symlink() or bool(
                    int(getattr(child_stat, "st_file_attributes", 0))
                    & FILE_ATTRIBUTE_REPARSE_POINT
                )
                if is_reparse:
                    entries.append(
                        _entry(
                            snapshot_id,
                            relative_path,
                            comparison_key,
                            object_type="reparse",
                        )
                    )
                    issues.append(
                        _issue(
                            relative_path,
                            "REPARSE_POINT_NOT_TRAVERSED",
                            "SNAPSHOT_REPARSE_POINT_BLOCKED",
                        )
                    )
                    if stat.S_ISDIR(child_stat.st_mode):
                        coverage.append(
                            SnapshotDirectoryCoverage(
                                relative_path=relative_path,
                                comparison_key=comparison_key,
                                coverage_state="REPARSE_BLOCKED",
                                case_mode="UNKNOWN",
                                case_mode_evidence="REPARSE_POINT_NOT_TRAVERSED_V1",
                                case_context_hash=_case_context_hash(
                                    relative_path,
                                    "UNKNOWN",
                                    "REPARSE_POINT_NOT_TRAVERSED_V1",
                                ),
                                case_probe_error="SNAPSHOT_REPARSE_POINT_BLOCKED",
                            )
                        )
                    continue
                if stat.S_ISDIR(child_stat.st_mode):
                    entries.append(
                        _entry(
                            snapshot_id,
                            relative_path,
                            comparison_key,
                            object_type="directory",
                        )
                    )
                    queue.append(
                        _QueuedDirectory(
                            path=queued.path / child.name,
                            relative_path=relative_path,
                            comparison_key=comparison_key,
                        )
                    )
                    continue
                if stat.S_ISREG(child_stat.st_mode):
                    entries.append(
                        _entry(
                            snapshot_id,
                            relative_path,
                            comparison_key,
                            object_type="file",
                            size_bytes=int(child_stat.st_size),
                        )
                    )
                    continue
                entries.append(
                    _entry(
                        snapshot_id,
                        relative_path,
                        comparison_key,
                        object_type="other",
                    )
                )
                issues.append(
                    _issue(
                        relative_path,
                        "UNSUPPORTED_OBJECT_TYPE",
                        "SNAPSHOT_OBJECT_TYPE_UNSUPPORTED",
                    )
                )
            if limit_hit:
                break
            after = _directory_signature(queued.path)
            after_inspection = self._inspect_queued_directory(queued)
            coverage_state = "COMPLETE"
            if case_context.case_mode == "UNKNOWN":
                coverage_state = "CASE_CONTEXT_UNKNOWN"
                issues.append(
                    _issue(
                        queued.relative_path,
                        "CASE_CONTEXT_UNKNOWN",
                        case_context.error_code or "SNAPSHOT_CASE_CONTEXT_UNKNOWN",
                    )
                )
            elif after_inspection is None:
                coverage_state = "UNREADABLE"
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_UNREADABLE",
                        "SNAPSHOT_DIRECTORY_REVALIDATION_FAILED",
                    )
                )
            elif not after_inspection.exists or after is None:
                coverage_state = "DISAPPEARED"
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_DISAPPEARED",
                        "SNAPSHOT_DIRECTORY_DISAPPEARED",
                    )
                )
            elif after_inspection.is_reparse_point:
                coverage_state = "REPARSE_BLOCKED"
                issues.append(
                    _issue(
                        queued.relative_path,
                        "REPARSE_POINT_NOT_TRAVERSED",
                        "SNAPSHOT_REPARSE_POINT_BLOCKED",
                    )
                )
            elif (
                after != before
                or _inspection_changed(before_inspection, after_inspection)
            ):
                coverage_state = "VOLATILE"
                issues.append(
                    _issue(
                        queued.relative_path,
                        "DIRECTORY_CHANGED_DURING_SCAN",
                        "SNAPSHOT_DIRECTORY_VOLATILE",
                    )
                )
            coverage.append(
                _coverage(
                    queued,
                    case_context,
                    coverage_state=coverage_state,
                )
            )

        return FilesystemSnapshotScan(
            snapshot_id=snapshot_id,
            root=root,
            entries=tuple(entries),
            coverage=tuple(coverage),
            issues=tuple(issues),
            control_area_excluded=control_area_excluded,
        )

    def _validate_root(self, root: Path) -> None:
        try:
            inspection = self._path_probe.inspect_path(root)
        except ReparseGuardError as exc:
            raise LocalSnapshotScanError(exc.validation_code, exc.next_action) from exc
        if not inspection.exists:
            raise LocalSnapshotScanError(
                "SNAPSHOT_ROOT_MISSING",
                "Reconnect the selected endpoint before scanning it.",
            )
        if inspection.is_reparse_point:
            raise LocalSnapshotScanError(
                "SNAPSHOT_ROOT_REPARSE_UNSUPPORTED",
                "Select an ordinary non-reparse endpoint root before scanning it.",
            )
        try:
            mode = root.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise LocalSnapshotScanError(
                "SNAPSHOT_ROOT_INSPECTION_FAILED",
                "Retry after the selected endpoint root can be inspected.",
            ) from exc
        if not stat.S_ISDIR(mode):
            raise LocalSnapshotScanError(
                "SNAPSHOT_ROOT_NOT_DIRECTORY",
                "Select a directory endpoint root before scanning it.",
            )

    def _inspect_queued_directory(
        self,
        queued: _QueuedDirectory,
    ) -> ReparseInspection | None:
        try:
            return self._path_probe.inspect_path(queued.path)
        except ReparseGuardError:
            return None


class _FileCaseSensitiveInfo(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [("flags", wintypes.ULONG)]


def _entry(
    snapshot_id: str,
    relative_path: str,
    comparison_key: str,
    *,
    object_type: str,
    size_bytes: int | None = None,
) -> SnapshotFileEntry:
    digest = hashlib.sha256(
        f"{snapshot_id}\0{relative_path}".encode("utf-8")
    ).hexdigest()
    return SnapshotFileEntry(
        entry_id=f"entry:{digest[:32]}",
        relative_path=relative_path,
        comparison_key=comparison_key,
        object_type=object_type,
        size_bytes=size_bytes,
    )


def _coverage(
    queued: _QueuedDirectory,
    case_context: DirectoryCaseContext,
    *,
    coverage_state: str,
) -> SnapshotDirectoryCoverage:
    return SnapshotDirectoryCoverage(
        relative_path=queued.relative_path,
        comparison_key=queued.comparison_key,
        coverage_state=coverage_state,
        case_mode=case_context.case_mode,
        case_mode_evidence=case_context.evidence,
        case_context_hash=_case_context_hash(
            queued.relative_path,
            case_context.case_mode,
            case_context.evidence,
        ),
        case_probe_error=case_context.error_code,
    )


def _issue(relative_path: str, issue_type: str, error_code: str) -> SnapshotIssue:
    return SnapshotIssue(
        relative_path=relative_path,
        issue_type=issue_type,
        blocks_destructive_actions=True,
        error_code=error_code,
        sanitized_message="The endpoint could not be completely and safely enumerated.",
    )


def _append_limit_block(
    coverage: list[SnapshotDirectoryCoverage],
    issues: list[SnapshotIssue],
    *,
    queued: _QueuedDirectory,
    reason_code: str,
) -> None:
    evidence = "BOUNDED_LOCAL_SNAPSHOT_SCANNER_V1"
    coverage.append(
        SnapshotDirectoryCoverage(
            relative_path=queued.relative_path,
            comparison_key=queued.comparison_key,
            coverage_state="CANCELLED",
            case_mode="UNKNOWN",
            case_mode_evidence=evidence,
            case_context_hash=_case_context_hash(
                queued.relative_path,
                "UNKNOWN",
                evidence,
            ),
            case_probe_error=reason_code,
        )
    )
    issues.append(_issue(queued.relative_path, "SCAN_LIMIT_EXCEEDED", reason_code))


def _blocked_directory_coverage(
    queued: _QueuedDirectory,
    *,
    coverage_state: str,
    error_code: str,
) -> SnapshotDirectoryCoverage:
    evidence = "QUEUED_DIRECTORY_REVALIDATION_V1"
    return SnapshotDirectoryCoverage(
        relative_path=queued.relative_path,
        comparison_key=queued.comparison_key,
        coverage_state=coverage_state,
        case_mode="UNKNOWN",
        case_mode_evidence=evidence,
        case_context_hash=_case_context_hash(
            queued.relative_path,
            "UNKNOWN",
            evidence,
        ),
        case_probe_error=error_code,
    )


def _inspection_changed(
    before: ReparseInspection,
    after: ReparseInspection,
) -> bool:
    return (
        before.identity != after.identity
        or before.final_path != after.final_path
    )


def _child_relative_path(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _child_comparison_key(parent: str, name: str, case_mode: str) -> str:
    component = name if case_mode != "CASE_INSENSITIVE" else name.casefold()
    return component if parent == "." else f"{parent}/{component}"


def _case_context_hash(relative_path: str, case_mode: str, evidence: str) -> str:
    material = json.dumps(
        {
            "case_mode": case_mode,
            "evidence": evidence,
            "relative_path": relative_path,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _directory_signature(path: Path) -> _DirectorySignature | None:
    try:
        value = path.stat(follow_symlinks=False)
    except OSError:
        return None
    return _DirectorySignature(
        device=int(value.st_dev),
        inode=int(value.st_ino),
        modified_ns=int(value.st_mtime_ns),
        size=int(value.st_size),
    )


def _case_kernel32() -> Any:
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
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
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
