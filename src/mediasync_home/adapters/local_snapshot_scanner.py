from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, SupportsInt, cast
from ctypes import wintypes

from mediasync_home.adapters.file_identity import file_birthtime_ns, stable_file_identity_hash
from mediasync_home.adapters.local_endpoint_classifier import CONTROL_DIRECTORY_NAME
from mediasync_home.adapters.named_streams import (
    NoNamedStreamProbe,
    Win32NamedStreamProbe,
)
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
from mediasync_home.application.file_filters import (
    FileFilterDecision,
    FileFilterPolicy,
    FileFilterSession,
    FileFilterSubject,
    default_file_filter_policy,
)
from mediasync_home.application.named_streams import (
    DEFAULT_NAMED_STREAM_POLICY,
    NamedStreamInspection,
    NamedStreamPolicy,
    NamedStreamProbe,
    NamedStreamState,
)
from mediasync_home.application.snapshots import (
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotFilterDecision,
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
MAX_VOLATILE_DIRECTORY_RESCANS = 2


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
    rescan_attempt: int = 0


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
        named_stream_probe: NamedStreamProbe | None = None,
        named_stream_policy: NamedStreamPolicy = DEFAULT_NAMED_STREAM_POLICY,
        filter_session_factory: Callable[
            [FileFilterPolicy], FileFilterSession
        ] = FileFilterSession,
        max_entries: int = MAX_LOCAL_SNAPSHOT_ENTRIES,
        max_directories: int = MAX_LOCAL_SNAPSHOT_DIRECTORIES,
        max_volatile_rescans: int = MAX_VOLATILE_DIRECTORY_RESCANS,
    ) -> None:
        if max_entries < 1 or max_directories < 1:
            raise ValueError("snapshot scanner limits must be positive")
        if max_volatile_rescans < 0:
            raise ValueError("volatile directory rescan limit must be non-negative")
        self._path_probe = path_probe or LocalFilesystemReparsePathProbe()
        self._case_mode_probe = case_mode_probe or LocalDirectoryCaseModeProbe()
        self._named_stream_probe = named_stream_probe or (
            Win32NamedStreamProbe() if os.name == "nt" else NoNamedStreamProbe()
        )
        self._named_stream_policy = named_stream_policy
        self._filter_session_factory = filter_session_factory
        self._max_entries = max_entries
        self._max_directories = max_directories
        self._max_volatile_rescans = max_volatile_rescans

    def scan(
        self,
        root: Path,
        *,
        snapshot_id: str,
        exclude_control_area: bool,
        filter_policy: FileFilterPolicy | None = None,
    ) -> FilesystemSnapshotScan:
        root = Path(root)
        self._validate_root(root)
        filter_session = self._filter_session_factory(
            filter_policy or default_file_filter_policy()
        )
        entries: list[SnapshotFileEntry] = []
        coverage: list[SnapshotDirectoryCoverage] = []
        issues: list[SnapshotIssue] = []
        filter_decisions: list[SnapshotFilterDecision] = []
        directory_filter_subjects: dict[str, FileFilterSubject] = {}
        reported_filter_errors: set[tuple[str | None, str]] = set()
        queue = deque((_QueuedDirectory(root, ".", "."),))
        scanned_directories = 0
        examined_entries = 0
        control_area_excluded = False
        rescan_attempt_count = 0

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
            entries_checkpoint = len(entries)
            coverage_checkpoint = len(coverage)
            issues_checkpoint = len(issues)
            filter_decisions_checkpoint = len(filter_decisions)
            queue_checkpoint = len(queue)
            examined_entries_checkpoint = examined_entries
            control_area_excluded_checkpoint = control_area_excluded
            reported_filter_errors_checkpoint = reported_filter_errors.copy()
            attempt_directory_subjects: list[str] = []
            self._append_named_stream_issue(
                issues,
                path=queued.path,
                relative_path=queued.relative_path,
                object_type="directory",
            )
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
            directory_filter_incomplete = False
            for child in children:
                if (
                    queued.relative_path == "."
                    and child.name == CONTROL_DIRECTORY_NAME
                    and exclude_control_area
                ):
                    control_area_excluded = True
                    filter_decisions.append(
                        SnapshotFilterDecision(
                            relative_path=CONTROL_DIRECTORY_NAME,
                            object_type="directory",
                            decision_state="EXCLUDED",
                            reason_code="FILTER_CONTROL_AREA_EXCLUDED",
                            matched_rule_id=None,
                            evaluation_stage="CONTROL_AREA",
                        )
                    )
                    continue
                if examined_entries >= self._max_entries:
                    _append_limit_block(
                        coverage,
                        issues,
                        queued=queued,
                        reason_code="SNAPSHOT_ENTRY_LIMIT_EXCEEDED",
                    )
                    limit_hit = True
                    break
                examined_entries += 1
                relative_path = _child_relative_path(queued.relative_path, child.name)
                comparison_key = _child_comparison_key(
                    queued.comparison_key,
                    child.name,
                    case_context.case_mode,
                )
                try:
                    pre_stat_object_type = _dir_entry_object_type(child)
                except OSError:
                    issues.append(
                        _issue(
                            relative_path,
                            "ENTRY_UNREADABLE",
                            "SNAPSHOT_ENTRY_TYPE_FAILED",
                        )
                    )
                    continue
                if filter_session.can_evaluate_before_metadata:
                    pre_stat_decision = filter_session.evaluate(
                        FileFilterSubject(
                            relative_path=relative_path,
                            object_type=pre_stat_object_type,
                        )
                    )
                    _append_filter_decision(
                        filter_decisions,
                        relative_path=relative_path,
                        object_type=pre_stat_object_type,
                        decision=pre_stat_decision,
                        evaluation_stage="PRE_METADATA",
                    )
                    if pre_stat_decision.error_code is not None:
                        directory_filter_incomplete = True
                        _append_filter_error(
                            issues,
                            reported_filter_errors,
                            relative_path=relative_path,
                            decision=pre_stat_decision,
                        )
                        continue
                    if not pre_stat_decision.included:
                        continue
                try:
                    child_stat = os.stat(
                        queued.path / child.name,
                        follow_symlinks=False,
                    )
                except OSError:
                    issues.append(
                        _issue(
                            relative_path,
                            "ENTRY_UNREADABLE",
                            "SNAPSHOT_ENTRY_STAT_FAILED",
                        )
                    )
                    continue
                file_attributes = int(
                    getattr(child_stat, "st_file_attributes", 0)
                )
                is_reparse = child.is_symlink() or bool(
                    file_attributes & FILE_ATTRIBUTE_REPARSE_POINT
                )
                object_type = _stat_object_type(child_stat, is_reparse=is_reparse)
                birthtime_ns = (
                    file_birthtime_ns(
                        queued.path / child.name,
                        stat_result=child_stat,
                    )
                    if object_type in {"file", "directory"}
                    else None
                )
                filter_subject = FileFilterSubject(
                    relative_path=relative_path,
                    object_type=object_type,
                    size_bytes=(
                        int(child_stat.st_size) if object_type == "file" else None
                    ),
                    modified_ns=(
                        int(child_stat.st_mtime_ns)
                        if object_type == "file"
                        else None
                    ),
                    created_ns=birthtime_ns if object_type == "file" else None,
                    file_attributes=file_attributes,
                )
                if not filter_session.can_evaluate_before_metadata:
                    decision = filter_session.evaluate(filter_subject)
                    if not (
                        object_type == "directory"
                        and filter_session.has_empty_directory_rules
                    ):
                        _append_filter_decision(
                            filter_decisions,
                            relative_path=relative_path,
                            object_type=object_type,
                            decision=decision,
                            evaluation_stage="METADATA",
                        )
                    if decision.error_code is not None:
                        directory_filter_incomplete = True
                        _append_filter_error(
                            issues,
                            reported_filter_errors,
                            relative_path=relative_path,
                            decision=decision,
                        )
                        continue
                    if not decision.included and not (
                        object_type == "directory"
                        and filter_session.has_empty_directory_rules
                    ):
                        continue
                if object_type == "directory":
                    directory_filter_subjects[relative_path] = filter_subject
                    attempt_directory_subjects.append(relative_path)
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
                if birthtime_ns is None:
                    issues.append(
                        _issue(
                            relative_path,
                            "BIRTHTIME_UNAVAILABLE",
                            "SNAPSHOT_BIRTHTIME_UNAVAILABLE",
                        )
                    )
                if stat.S_ISREG(child_stat.st_mode):
                    self._append_named_stream_issue(
                        issues,
                        path=queued.path / child.name,
                        relative_path=relative_path,
                        object_type="file",
                    )
                if stat.S_ISDIR(child_stat.st_mode):
                    entries.append(
                        _entry(
                            snapshot_id,
                            relative_path,
                            comparison_key,
                            object_type="directory",
                            birthtime_ns=birthtime_ns,
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
                            birthtime_ns=birthtime_ns,
                            identity_fingerprint_hash=stable_file_identity_hash(child_stat),
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
            coverage_state = (
                "FILTER_INCOMPLETE"
                if directory_filter_incomplete
                else "COMPLETE"
            )
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
                if queued.rescan_attempt < self._max_volatile_rescans:
                    del entries[entries_checkpoint:]
                    del coverage[coverage_checkpoint:]
                    del issues[issues_checkpoint:]
                    del filter_decisions[filter_decisions_checkpoint:]
                    while len(queue) > queue_checkpoint:
                        queue.pop()
                    for relative_path in attempt_directory_subjects:
                        directory_filter_subjects.pop(relative_path, None)
                    reported_filter_errors.clear()
                    reported_filter_errors.update(
                        reported_filter_errors_checkpoint
                    )
                    examined_entries = examined_entries_checkpoint
                    control_area_excluded = control_area_excluded_checkpoint
                    scanned_directories -= 1
                    rescan_attempt_count += 1
                    queue.appendleft(
                        replace(
                            queued,
                            rescan_attempt=queued.rescan_attempt + 1,
                        )
                    )
                    continue
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

        if filter_session.has_empty_directory_rules:
            entries, coverage, issues, filter_decisions = _apply_empty_directory_filters(
                entries=entries,
                coverage=coverage,
                issues=issues,
                directory_subjects=directory_filter_subjects,
                session=filter_session,
                reported_errors=reported_filter_errors,
                filter_decisions=filter_decisions,
            )

        return FilesystemSnapshotScan(
            snapshot_id=snapshot_id,
            root=root,
            entries=tuple(entries),
            coverage=tuple(coverage),
            issues=tuple(issues),
            control_area_excluded=control_area_excluded,
            filter_decisions=tuple(filter_decisions),
            rescan_attempt_count=rescan_attempt_count,
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

    def _append_named_stream_issue(
        self,
        issues: list[SnapshotIssue],
        *,
        path: Path,
        relative_path: str,
        object_type: str,
    ) -> None:
        inspection = self._named_stream_probe.inspect_named_streams(path)
        if inspection.state is NamedStreamState.NONE:
            return
        issues.append(
            _named_stream_issue(
                relative_path=relative_path,
                inspection=inspection,
                policy=self._named_stream_policy,
                object_type=object_type,
            )
        )


def _dir_entry_object_type(child: os.DirEntry[str]) -> str:
    if child.is_symlink():
        return "reparse"
    if child.is_dir(follow_symlinks=False):
        return "directory"
    if child.is_file(follow_symlinks=False):
        return "file"
    return "other"


def _stat_object_type(
    child_stat: os.stat_result,
    *,
    is_reparse: bool,
) -> str:
    if is_reparse:
        return "reparse"
    if stat.S_ISDIR(child_stat.st_mode):
        return "directory"
    if stat.S_ISREG(child_stat.st_mode):
        return "file"
    return "other"


def _append_filter_error(
    issues: list[SnapshotIssue],
    reported_errors: set[tuple[str | None, str]],
    *,
    relative_path: str,
    decision: FileFilterDecision,
) -> None:
    error_code = decision.error_code
    if error_code is None:
        return
    key = (decision.matched_rule_id, error_code)
    if key in reported_errors:
        return
    reported_errors.add(key)
    issues.append(
        _issue(
            relative_path,
            "FILTER_EVALUATION_INCOMPLETE",
            error_code,
            sanitized_message=(
                "A file-selection rule exceeded its bounded evaluation policy."
            ),
        )
    )


def _append_filter_decision(
    decisions: list[SnapshotFilterDecision],
    *,
    relative_path: str,
    object_type: str,
    decision: FileFilterDecision,
    evaluation_stage: str,
) -> None:
    if (
        decision.matched_rule_id is None
        and decision.included
        and decision.error_code is None
    ):
        return
    decisions.append(
        SnapshotFilterDecision(
            relative_path=relative_path,
            object_type=object_type,
            decision_state=(
                "ERROR"
                if decision.error_code is not None
                else "INCLUDED"
                if decision.included
                else "EXCLUDED"
            ),
            reason_code=decision.reason_code,
            matched_rule_id=decision.matched_rule_id,
            evaluation_stage=evaluation_stage,
        )
    )


def _apply_empty_directory_filters(
    *,
    entries: list[SnapshotFileEntry],
    coverage: list[SnapshotDirectoryCoverage],
    issues: list[SnapshotIssue],
    directory_subjects: dict[str, FileFilterSubject],
    session: FileFilterSession,
    reported_errors: set[tuple[str | None, str]],
    filter_decisions: list[SnapshotFilterDecision],
) -> tuple[
    list[SnapshotFileEntry],
    list[SnapshotDirectoryCoverage],
    list[SnapshotIssue],
    list[SnapshotFilterDecision],
]:
    child_counts: dict[str, int] = {}
    for entry in entries:
        if entry.object_type != "directory":
            parent = _parent_relative_path(entry.relative_path)
            child_counts[parent] = child_counts.get(parent, 0) + 1

    excluded_directories: set[str] = set()
    filter_errors: dict[str, str] = {}
    directories = sorted(
        (
            entry
            for entry in entries
            if entry.object_type == "directory"
            and entry.relative_path in directory_subjects
        ),
        key=lambda entry: (entry.relative_path.count("/"), entry.relative_path),
        reverse=True,
    )
    for entry in directories:
        relative_path = entry.relative_path
        decision = session.evaluate(
            replace(
                directory_subjects[relative_path],
                is_empty_directory=child_counts.get(relative_path, 0) == 0,
            )
        )
        _append_filter_decision(
            filter_decisions,
            relative_path=relative_path,
            object_type="directory",
            decision=decision,
            evaluation_stage="EMPTY_DIRECTORY",
        )
        if decision.error_code is not None:
            filter_errors[relative_path] = decision.error_code
            _append_filter_error(
                issues,
                reported_errors,
                relative_path=relative_path,
                decision=decision,
            )
        elif not decision.included:
            excluded_directories.add(relative_path)
            continue
        parent = _parent_relative_path(relative_path)
        child_counts[parent] = child_counts.get(parent, 0) + 1

    if excluded_directories:
        entries = [
            entry
            for entry in entries
            if not _path_is_within_any(
                entry.relative_path,
                excluded_directories,
            )
        ]
        coverage = [
            item
            for item in coverage
            if not _path_is_within_any(
                item.relative_path,
                excluded_directories,
            )
        ]
        issues = [
            issue
            for issue in issues
            if not _path_is_within_any(
                issue.relative_path,
                excluded_directories,
            )
        ]

    coverage = [
        replace(
            item,
            coverage_state="FILTER_INCOMPLETE",
            case_probe_error=filter_errors[item.relative_path],
        )
        if item.relative_path in filter_errors
        and item.coverage_state == "COMPLETE"
        else item
        for item in coverage
    ]
    return entries, coverage, issues, filter_decisions


def _parent_relative_path(relative_path: str) -> str:
    parent, separator, _name = relative_path.rpartition("/")
    return parent if separator else "."


def _path_is_within_any(
    relative_path: str,
    roots: set[str],
) -> bool:
    current = relative_path
    while current != ".":
        if current in roots:
            return True
        current = _parent_relative_path(current)
    return False


class _FileCaseSensitiveInfo(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [("flags", wintypes.ULONG)]


def _entry(
    snapshot_id: str,
    relative_path: str,
    comparison_key: str,
    *,
    object_type: str,
    size_bytes: int | None = None,
    birthtime_ns: int | None = None,
    identity_fingerprint_hash: str | None = None,
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
        birthtime_ns=birthtime_ns,
        identity_fingerprint_hash=identity_fingerprint_hash,
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


def _issue(
    relative_path: str,
    issue_type: str,
    error_code: str,
    *,
    sanitized_message: str = "The endpoint could not be completely and safely enumerated.",
) -> SnapshotIssue:
    return SnapshotIssue(
        relative_path=relative_path,
        issue_type=issue_type,
        blocks_destructive_actions=True,
        error_code=error_code,
        sanitized_message=sanitized_message,
    )


def _named_stream_issue(
    *,
    relative_path: str,
    inspection: NamedStreamInspection,
    policy: NamedStreamPolicy,
    object_type: str,
) -> SnapshotIssue:
    if inspection.state is NamedStreamState.PRESENT:
        if policy is NamedStreamPolicy.BLOCK_IF_PRESENT_OR_UNCONFIRMED:
            return _issue(
                relative_path,
                "NAMED_STREAM_PRESENT",
                "SNAPSHOT_NAMED_STREAM_PRESENT",
                sanitized_message=(
                    "The item contains a Windows named stream, and full-object "
                    "copying is not enabled."
                ),
            )
        if policy is NamedStreamPolicy.PRESERVE_WHEN_PORTABLE_BLOCK_IF_UNCONFIRMED:
            if object_type == "directory":
                return _issue(
                    relative_path,
                    "DIRECTORY_NAMED_STREAM_PRESENT",
                    "SNAPSHOT_DIRECTORY_NAMED_STREAM_PRESENT",
                    sanitized_message=(
                        "The directory contains a Windows named stream that "
                        "cannot yet be preserved."
                    ),
                )
            return SnapshotIssue(
                relative_path=relative_path,
                issue_type="NAMED_STREAM_PRESENT",
                blocks_destructive_actions=False,
                error_code="SNAPSHOT_NAMED_STREAM_PRESENT",
                sanitized_message=(
                    "The item contains Windows named streams that must be preserved "
                    "and verified on every target."
                ),
            )
        raise ValueError("NAMED_STREAM_POLICY_UNSUPPORTED")
    if policy not in {
        NamedStreamPolicy.BLOCK_IF_PRESENT_OR_UNCONFIRMED,
        NamedStreamPolicy.PRESERVE_WHEN_PORTABLE_BLOCK_IF_UNCONFIRMED,
    }:
        raise ValueError("NAMED_STREAM_POLICY_UNSUPPORTED")
    return _issue(
        relative_path,
        "NAMED_STREAM_ENUMERATION_UNCONFIRMED",
        inspection.error_code or "SNAPSHOT_NAMED_STREAM_ENUMERATION_UNCONFIRMED",
        sanitized_message=(
            "The item could not be checked completely for Windows named streams."
        ),
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
