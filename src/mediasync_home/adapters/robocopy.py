from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediasync_home.adapters.endpoint_leases import EndpointRootResolver
from mediasync_home.adapters.process_supervisor import Win32JobObjectTransferSupervisor
from mediasync_home.adapters.reparse_guard import ReparseGuard
from mediasync_home.adapters.staging import (
    LocalFileStagingError,
    LocalFileStagingTransferAdapter,
    _expected_fingerprint,
    _fingerprint_file,
)
from mediasync_home.adapters.windows_argv import (
    WindowsCommandLineError,
    build_windows_command_line,
    parse_windows_command_line,
)
from mediasync_home.application.process_supervision import (
    ProcessLaunchPlan,
    ProcessLaunchViolation,
    build_transfer_child_launch_plan,
)
from mediasync_home.application.recovery_operations import RecoveryOperation
from mediasync_home.application.run_staging import StagingTransferEvidence


FORBIDDEN_ROBOCOPY_SWITCH_NAMES = frozenset(("MIR", "PURGE", "MOVE", "MOV"))
DEFAULT_ROBOCOPY_SWITCHES = (
    "/E",
    "/Z",
    "/R:1",
    "/W:1",
    "/COPY:DAT",
    "/DCOPY:DA",
    "/NP",
    "/NFL",
    "/NDL",
)
ROBOCOPY_SUCCESS_MAX_EXIT_CODE = 7
ROBOCOPY_CONTAINMENT_TIMEOUT_EXIT_CODE = 98
ROBOCOPY_MANIFEST_SCHEMA_VERSION = 1
ROBOCOPY_CONSERVATIVE_COMMAND_LINE_LIMIT = 24_000
ROBOCOPY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DISCARD_ROBOCOPY_INBOX_ERROR_CODES = frozenset(
    (
        "ROBOCOPY_PROCESS_CONTAINMENT_FAILED",
        "ROBOCOPY_STAGING_SOURCE_CHANGED",
        "ROBOCOPY_TRANSFER_FAILED",
        "ROBOCOPY_TRANSFER_TIMED_OUT",
        "STAGING_MANIFEST_MISMATCH",
    )
)


class RobocopyConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedSystemExecutable:
    requested_name: str
    system_directory: Path
    executable_path: Path
    final_path: str
    sha256: str
    file_version: str | None = None


@dataclass(frozen=True)
class RobocopyTransferProfile:
    switches: tuple[str, ...] = DEFAULT_ROBOCOPY_SWITCHES
    timeout_seconds: float | None = None
    success_max_exit_code: int = ROBOCOPY_SUCCESS_MAX_EXIT_CODE


@dataclass(frozen=True)
class RobocopyExitClassification:
    exit_code: int
    category: str
    copied: bool
    extras_reported: bool
    mismatches_reported: bool
    failed: bool


@dataclass(frozen=True)
class RobocopyBatchManifestEntry:
    operation_id: str
    staging_object_id: str
    source_file_name: str
    source_relative_path: str
    final_relative_path: str
    payload_path: Path
    expected_byte_count: int
    expected_content_hash: str


@dataclass(frozen=True)
class RobocopyBatchManifest:
    batch_id: str
    source_parent: Path
    staging_inbox: Path
    log_path: Path
    entries: tuple[RobocopyBatchManifestEntry, ...]
    profile_hash: str
    canonical_json: str
    manifest_hash: str


@dataclass(frozen=True)
class RobocopyCommandPlan:
    executable: ResolvedSystemExecutable
    launch_plan: ProcessLaunchPlan
    argv: tuple[str, ...]
    parsed_argv: tuple[str, ...]
    command_line_sha256: str
    source_parent: Path
    staging_inbox: Path
    file_name: str
    log_path: Path
    file_names: tuple[str, ...] = ()
    batch_manifest_hash: str | None = None
    manifest_path: Path | None = None


@dataclass(frozen=True)
class RobocopyResult:
    exit_code: int
    category: str
    copied: bool
    extras_reported: bool
    mismatches_reported: bool
    failed: bool
    terminated_by_supervisor: bool
    executable_path: Path
    executable_version: str | None
    arguments_hash: str
    environment_hash: str
    manifest_hash: str | None
    log_path: Path


class SystemExecutableResolver(Protocol):
    def resolve(self, requested_name: str) -> ResolvedSystemExecutable: ...


class RobocopyTransferProcess(Protocol):
    def wait(self, *, timeout_seconds: float | None = None) -> int | None: ...

    def terminate(self, *, exit_code: int = 1) -> None: ...

    def close(self) -> None: ...


class RobocopyTransferSupervisor(Protocol):
    def start(self, plan: ProcessLaunchPlan) -> RobocopyTransferProcess: ...


class WindowsSystemExecutableResolver:
    def __init__(self, *, api: "_WindowsSystemExecutableApi | None" = None) -> None:
        self._api = api or _WindowsSystemExecutableApi()

    def resolve(self, requested_name: str) -> ResolvedSystemExecutable:
        if Path(requested_name).name != requested_name or any(
            separator in requested_name for separator in ("/", "\\")
        ):
            raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_NAME_MUST_BE_BASENAME")
        system_directory = self._api.get_system_directory()
        candidate = system_directory / requested_name
        if not candidate.is_file():
            raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_NOT_FOUND")
        final_path = self._api.get_final_path(candidate)
        normalized_system = normalize_dos_path(str(system_directory))
        normalized_final = normalize_dos_path(final_path)
        if os.path.dirname(normalized_final) != normalized_system:
            raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_ESCAPED_SYSTEM_DIRECTORY")
        if os.path.basename(normalized_final).lower() != requested_name.lower():
            raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_BASENAME_MISMATCH")
        return ResolvedSystemExecutable(
            requested_name=requested_name,
            system_directory=system_directory,
            executable_path=candidate,
            final_path=final_path,
            sha256=_sha256_file(candidate),
            file_version=self._api.get_file_version(candidate),
        )


class RobocopyStagingTransferAdapter(LocalFileStagingTransferAdapter):
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        staging_root: Path | None = None,
        robocopy_work_root: Path | None = None,
        process_supervisor: RobocopyTransferSupervisor | None = None,
        executable_resolver: SystemExecutableResolver | None = None,
        reparse_guard: ReparseGuard | None = None,
        profile: RobocopyTransferProfile = RobocopyTransferProfile(),
    ) -> None:
        super().__init__(
            root_resolver=root_resolver,
            staging_root=staging_root,
            reparse_guard=reparse_guard,
        )
        validate_robocopy_profile(profile)
        self._robocopy_work_root = None if robocopy_work_root is None else Path(robocopy_work_root)
        self._process_supervisor = process_supervisor or Win32JobObjectTransferSupervisor()
        self._executable_resolver = executable_resolver or WindowsSystemExecutableResolver()
        self._profile = profile

    def transfer_to_staging(self, operation: RecoveryOperation) -> StagingTransferEvidence:
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        payload_path = self._staging_payload_path(operation)
        if payload_path.exists():
            existing = _fingerprint_file(payload_path)
            if existing == expected:
                return StagingTransferEvidence(
                    transfer_state="ROBOCOPY_TRANSFERRED_EXISTING_MATCH"
                )
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_EXISTING_PAYLOAD_MISMATCH",
                "Discard the stale staging payload and retry the transfer.",
            )

        source_path = self._source_path(operation)
        if not source_path.is_file() or source_path.is_symlink():
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_SOURCE_FILE_MISSING",
                "Refresh analysis because the planned source file is no longer readable.",
            )
        inbox = self._robocopy_inbox_path(operation)
        if inbox.exists():
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_INBOX_EXISTS",
                "Inspect and remove the stale Robocopy staging inbox before retrying.",
            )
        inbox.mkdir(parents=True)
        log_path = self._robocopy_log_path(operation)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        manifest: RobocopyBatchManifest | None = None

        try:
            manifest = build_robocopy_batch_manifest(
                batch_id=_safe_staging_name(operation),
                source_parent=source_path.parent,
                staging_inbox=inbox,
                log_path=log_path,
                entries=(
                    build_robocopy_manifest_entry(
                        operation=operation,
                        source_file=source_path,
                        payload_path=payload_path,
                        expected_fingerprint=expected,
                    ),
                ),
                profile=self._profile,
            )
            manifest_path = self._robocopy_manifest_path(operation)
            write_robocopy_batch_manifest(manifest=manifest, manifest_path=manifest_path)
            command_plan = build_robocopy_directory_manifest_command_plan(
                executable=self._executable_resolver.resolve("Robocopy.exe"),
                manifest=manifest,
                manifest_path=manifest_path,
                working_directory=self._robocopy_work_root_for(operation),
                working_directory_root=self._robocopy_work_root_for(operation),
                profile=self._profile,
            )
            result = build_robocopy_result(
                command_plan=command_plan,
                exit_code=self._run_robocopy(command_plan.launch_plan),
            )
            if result.failed or result.exit_code > self._profile.success_max_exit_code:
                raise LocalFileStagingError(
                    "ROBOCOPY_TRANSFER_FAILED",
                    "Retry the transfer after reviewing the Robocopy batch log.",
                )
            publish_robocopy_batch_inbox(manifest)
        except LocalFileStagingError as exc:
            if exc.validation_code in _DISCARD_ROBOCOPY_INBOX_ERROR_CODES:
                discard_robocopy_batch_inbox(inbox=inbox, manifest=manifest)
            raise
        except (RobocopyConfigurationError, WindowsCommandLineError) as exc:
            discard_robocopy_batch_inbox(inbox=inbox, manifest=manifest)
            raise LocalFileStagingError(
                "ROBOCOPY_TRANSFER_CONFIGURATION_INVALID",
                "Fix the Robocopy executable/profile configuration before retrying.",
            ) from exc

        return StagingTransferEvidence(
            transfer_state=_robocopy_transfer_state(result)
        )

    def _run_robocopy(self, launch_plan: ProcessLaunchPlan) -> int:
        try:
            process = self._process_supervisor.start(launch_plan)
        except ProcessLaunchViolation as exc:
            raise LocalFileStagingError(
                "ROBOCOPY_PROCESS_CONTAINMENT_FAILED",
                "Retry only after the transfer child can be contained before resume.",
            ) from exc
        try:
            exit_code = process.wait(timeout_seconds=self._profile.timeout_seconds)
            if exit_code is None:
                with suppress(Exception):
                    process.terminate(exit_code=ROBOCOPY_CONTAINMENT_TIMEOUT_EXIT_CODE)
                raise LocalFileStagingError(
                    "ROBOCOPY_TRANSFER_TIMED_OUT",
                    "Retry the transfer with a smaller batch or after storage responsiveness recovers.",
                )
            return exit_code
        finally:
            process.close()

    def _robocopy_work_root_for(self, operation: RecoveryOperation) -> Path:
        if self._robocopy_work_root is not None:
            return self._robocopy_work_root
        return self._staging_root_for(operation) / "robocopy"

    def _robocopy_inbox_path(self, operation: RecoveryOperation) -> Path:
        return self._robocopy_work_root_for(operation) / "inbox" / _safe_staging_name(operation)

    def _robocopy_log_path(self, operation: RecoveryOperation) -> Path:
        return (
            self._robocopy_work_root_for(operation)
            / "logs"
            / f"{_safe_staging_name(operation)}.robocopy.log"
        )

    def _robocopy_manifest_path(self, operation: RecoveryOperation) -> Path:
        return (
            self._robocopy_work_root_for(operation)
            / "manifests"
            / f"{_safe_staging_name(operation)}.manifest.json"
        )


def build_robocopy_manifest_entry(
    *,
    operation: RecoveryOperation,
    source_file: Path,
    payload_path: Path,
    expected_fingerprint: dict[str, object],
) -> RobocopyBatchManifestEntry:
    byte_count = expected_fingerprint.get("byte_count")
    content_hash = expected_fingerprint.get("content_hash")
    if not isinstance(byte_count, int) or byte_count < 0:
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_FINGERPRINT_INVALID")
    if not isinstance(content_hash, str) or HASH_PATTERN.fullmatch(content_hash) is None:
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_FINGERPRINT_INVALID")
    staging_object_id = operation.staging_object_id
    if staging_object_id is None or ROBOCOPY_ID_PATTERN.fullmatch(staging_object_id) is None:
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_REQUIRES_SAFE_STAGING_OBJECT_ID")
    _validate_manifest_id(operation.operation_id, "ROBOCOPY_MANIFEST_REQUIRES_SAFE_OPERATION_ID")
    return RobocopyBatchManifestEntry(
        operation_id=operation.operation_id,
        staging_object_id=staging_object_id,
        source_file_name=_robocopy_source_file_name(source_file),
        source_relative_path=operation.source_relative_path or "",
        final_relative_path=operation.final_relative_path,
        payload_path=payload_path,
        expected_byte_count=byte_count,
        expected_content_hash=content_hash,
    )


def build_robocopy_batch_manifest(
    *,
    batch_id: str,
    source_parent: Path,
    staging_inbox: Path,
    log_path: Path,
    entries: tuple[RobocopyBatchManifestEntry, ...],
    profile: RobocopyTransferProfile = RobocopyTransferProfile(),
) -> RobocopyBatchManifest:
    _validate_manifest_id(batch_id, "ROBOCOPY_MANIFEST_REQUIRES_SAFE_BATCH_ID")
    if not source_parent.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_SOURCE_PARENT_MUST_BE_ABSOLUTE")
    if not staging_inbox.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_INBOX_MUST_BE_ABSOLUTE")
    if not log_path.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_LOG_PATH_MUST_BE_ABSOLUTE")
    if not entries:
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_REQUIRES_ENTRIES")
    validate_robocopy_profile(profile)
    _validate_manifest_entries(entries)
    profile_hash = _sha256_text(
        _canonical_json(
            {
                "success_max_exit_code": profile.success_max_exit_code,
                "switches": list(profile.switches),
                "timeout_seconds": profile.timeout_seconds,
            }
        )
    )
    payload = _robocopy_manifest_payload(
        batch_id=batch_id,
        source_parent=source_parent,
        staging_inbox=staging_inbox,
        log_path=log_path,
        entries=entries,
        profile=profile,
        profile_hash=profile_hash,
        manifest_hash=None,
    )
    manifest_hash = _manifest_hash(payload)
    canonical_json = _canonical_json({**payload, "canonical_manifest_hash": manifest_hash})
    return RobocopyBatchManifest(
        batch_id=batch_id,
        source_parent=source_parent,
        staging_inbox=staging_inbox,
        log_path=log_path,
        entries=entries,
        profile_hash=profile_hash,
        canonical_json=canonical_json,
        manifest_hash=manifest_hash,
    )


def write_robocopy_batch_manifest(
    *,
    manifest: RobocopyBatchManifest,
    manifest_path: Path,
) -> None:
    if not manifest_path.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_PATH_MUST_BE_ABSOLUTE")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with manifest_path.open("x", encoding="utf-8") as handle:
            handle.write(manifest.canonical_json)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        try:
            existing = manifest_path.read_text(encoding="utf-8")
        except OSError as read_exc:
            raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_EXISTING_UNREADABLE") from read_exc
        if existing == manifest.canonical_json:
            return
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_CONFLICT") from exc


def build_robocopy_directory_manifest_command_plan(
    *,
    executable: ResolvedSystemExecutable,
    manifest: RobocopyBatchManifest,
    manifest_path: Path,
    working_directory: Path,
    working_directory_root: Path,
    profile: RobocopyTransferProfile = RobocopyTransferProfile(),
) -> RobocopyCommandPlan:
    if not working_directory.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_WORKING_DIRECTORY_MUST_BE_ABSOLUTE")
    if not manifest_path.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_PATH_MUST_BE_ABSOLUTE")
    validate_robocopy_profile(profile)
    file_names = tuple(entry.source_file_name for entry in manifest.entries)
    argv = (
        str(executable.executable_path),
        str(manifest.source_parent),
        str(manifest.staging_inbox),
        *file_names,
        *profile.switches,
        f"/UNILOG:{manifest.log_path}",
    )
    validate_robocopy_switches(argv[3:])
    command_line = build_windows_command_line(argv)
    if len(command_line) > ROBOCOPY_CONSERVATIVE_COMMAND_LINE_LIMIT:
        raise RobocopyConfigurationError("ROBOCOPY_COMMAND_LINE_TOO_LONG")
    parsed_argv = validate_robocopy_command_line(
        command_line,
        executable_path=executable.executable_path,
    )
    if parsed_argv != argv:
        raise RobocopyConfigurationError("ROBOCOPY_COMMAND_LINE_ROUND_TRIP_MISMATCH")

    launch_plan = build_transfer_child_launch_plan(
        executable=executable.executable_path,
        arguments=argv[1:],
        working_directory=working_directory,
        working_directory_root=working_directory_root,
        environment=_robocopy_environment(executable.system_directory),
    )
    return RobocopyCommandPlan(
        executable=executable,
        launch_plan=launch_plan,
        argv=argv,
        parsed_argv=parsed_argv,
        command_line_sha256=_sha256_text(command_line),
        source_parent=manifest.source_parent,
        staging_inbox=manifest.staging_inbox,
        file_name=file_names[0],
        log_path=manifest.log_path,
        file_names=file_names,
        batch_manifest_hash=manifest.manifest_hash,
        manifest_path=manifest_path,
    )


def build_robocopy_single_file_command_plan(
    *,
    executable: ResolvedSystemExecutable,
    source_file: Path,
    staging_inbox: Path,
    log_path: Path,
    working_directory: Path,
    working_directory_root: Path,
    profile: RobocopyTransferProfile = RobocopyTransferProfile(),
) -> RobocopyCommandPlan:
    if not source_file.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_SOURCE_FILE_MUST_BE_ABSOLUTE")
    if not staging_inbox.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_STAGING_INBOX_MUST_BE_ABSOLUTE")
    if not log_path.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_LOG_PATH_MUST_BE_ABSOLUTE")
    if not working_directory.is_absolute():
        raise RobocopyConfigurationError("ROBOCOPY_WORKING_DIRECTORY_MUST_BE_ABSOLUTE")
    validate_robocopy_profile(profile)
    source_file_name = _validate_source_file_name(source_file.name)

    argv = (
        str(executable.executable_path),
        str(source_file.parent),
        str(staging_inbox),
        source_file_name,
        *profile.switches,
        f"/UNILOG:{log_path}",
    )
    validate_robocopy_switches(argv[3:])
    command_line = build_windows_command_line(argv)
    if len(command_line) > ROBOCOPY_CONSERVATIVE_COMMAND_LINE_LIMIT:
        raise RobocopyConfigurationError("ROBOCOPY_COMMAND_LINE_TOO_LONG")
    parsed_argv = validate_robocopy_command_line(command_line, executable_path=executable.executable_path)
    if parsed_argv != argv:
        raise RobocopyConfigurationError("ROBOCOPY_COMMAND_LINE_ROUND_TRIP_MISMATCH")

    launch_plan = build_transfer_child_launch_plan(
        executable=executable.executable_path,
        arguments=argv[1:],
        working_directory=working_directory,
        working_directory_root=working_directory_root,
        environment=_robocopy_environment(executable.system_directory),
    )
    return RobocopyCommandPlan(
        executable=executable,
        launch_plan=launch_plan,
        argv=argv,
        parsed_argv=parsed_argv,
        command_line_sha256=_sha256_text(command_line),
        source_parent=source_file.parent,
        staging_inbox=staging_inbox,
        file_name=source_file_name,
        log_path=log_path,
        file_names=(source_file_name,),
    )


def validate_robocopy_command_line(
    command_line: str,
    *,
    executable_path: Path,
) -> tuple[str, ...]:
    parsed = parse_windows_command_line(command_line)
    if not parsed:
        raise RobocopyConfigurationError("ROBOCOPY_COMMAND_LINE_EMPTY")
    if normalize_dos_path(parsed[0]) != normalize_dos_path(str(executable_path)):
        raise RobocopyConfigurationError("ROBOCOPY_EXECUTABLE_MISMATCH")
    validate_robocopy_switches(parsed[3:])
    return parsed


def validate_robocopy_switches(arguments: tuple[str, ...]) -> None:
    for argument in arguments:
        switch_name = _robocopy_switch_name(argument)
        if switch_name in FORBIDDEN_ROBOCOPY_SWITCH_NAMES:
            raise RobocopyConfigurationError("ROBOCOPY_FORBIDDEN_SWITCH")


def validate_robocopy_profile(profile: RobocopyTransferProfile) -> None:
    validate_robocopy_switches(profile.switches)
    if (
        profile.success_max_exit_code < 0
        or profile.success_max_exit_code > ROBOCOPY_SUCCESS_MAX_EXIT_CODE
    ):
        raise RobocopyConfigurationError("ROBOCOPY_PROFILE_SUCCESS_MAX_EXIT_CODE_INVALID")
    if profile.timeout_seconds is not None and profile.timeout_seconds <= 0:
        raise RobocopyConfigurationError("ROBOCOPY_PROFILE_TIMEOUT_INVALID")


def classify_robocopy_exit_code(exit_code: int) -> str:
    return decode_robocopy_exit_code(exit_code).category


def decode_robocopy_exit_code(exit_code: int) -> RobocopyExitClassification:
    if exit_code < 0:
        return RobocopyExitClassification(
            exit_code=exit_code,
            category="INVALID",
            copied=False,
            extras_reported=False,
            mismatches_reported=False,
            failed=True,
        )
    return RobocopyExitClassification(
        exit_code=exit_code,
        category="NON_FATAL" if exit_code <= ROBOCOPY_SUCCESS_MAX_EXIT_CODE else "FATAL",
        copied=bool(exit_code & 0x01),
        extras_reported=bool(exit_code & 0x02),
        mismatches_reported=bool(exit_code & 0x04),
        failed=bool(exit_code & ~ROBOCOPY_SUCCESS_MAX_EXIT_CODE),
    )


def build_robocopy_result(
    *,
    command_plan: RobocopyCommandPlan,
    exit_code: int,
    terminated_by_supervisor: bool = False,
) -> RobocopyResult:
    classification = decode_robocopy_exit_code(exit_code)
    return RobocopyResult(
        exit_code=classification.exit_code,
        category=classification.category,
        copied=classification.copied,
        extras_reported=classification.extras_reported,
        mismatches_reported=classification.mismatches_reported,
        failed=classification.failed,
        terminated_by_supervisor=terminated_by_supervisor,
        executable_path=command_plan.executable.executable_path,
        executable_version=command_plan.executable.file_version,
        arguments_hash=command_plan.command_line_sha256,
        environment_hash=_environment_hash(command_plan.launch_plan.environment),
        manifest_hash=command_plan.batch_manifest_hash,
        log_path=command_plan.log_path,
    )


def _robocopy_transfer_state(result: RobocopyResult) -> str:
    flag_names: list[str] = []
    if result.copied:
        flag_names.append("COPIED")
    if result.extras_reported:
        flag_names.append("EXTRAS_REPORTED")
    if result.mismatches_reported:
        flag_names.append("MISMATCHES_REPORTED")
    if not flag_names:
        flag_names.append("NO_CHANGES")
    return f"ROBOCOPY_EXIT_{result.exit_code}_{'_'.join(flag_names)}_TRANSFERRED_TO_STAGING"


def normalize_dos_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.abspath(path))


class _WindowsSystemExecutableApi:
    def get_system_directory(self) -> Path:
        if os.name != "nt":
            raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_RESOLUTION_REQUIRES_WINDOWS")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if size == 0:
            raise RobocopyConfigurationError("GET_SYSTEM_DIRECTORY_FAILED")
        if size >= len(buffer):
            raise RobocopyConfigurationError("GET_SYSTEM_DIRECTORY_EXCEEDED_BUFFER")
        return Path(buffer.value)

    def get_final_path(self, path: Path) -> str:
        if os.name != "nt":
            raise RobocopyConfigurationError("FINAL_PATH_RESOLUTION_REQUIRES_WINDOWS")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.GetFinalPathNameByHandleW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        generic_read = 0x80000000
        file_share_read = 0x00000001
        file_share_write = 0x00000002
        file_share_delete = 0x00000004
        open_existing = 3
        file_attribute_normal = 0x00000080
        handle = kernel32.CreateFileW(
            str(path),
            generic_read,
            file_share_read | file_share_write | file_share_delete,
            None,
            open_existing,
            file_attribute_normal,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (None, 0, invalid_handle):
            raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_OPEN_FAILED")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if size == 0:
                raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_FINAL_PATH_FAILED")
            if size >= len(buffer):
                raise RobocopyConfigurationError("SYSTEM_EXECUTABLE_FINAL_PATH_EXCEEDED_BUFFER")
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)

    def get_file_version(self, path: Path) -> str | None:
        if os.name != "nt":
            return None
        try:
            version = ctypes.WinDLL("version", use_last_error=True)
            version.GetFileVersionInfoSizeW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
            version.GetFileVersionInfoW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            version.GetFileVersionInfoW.restype = ctypes.c_int
            version.VerQueryValueW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_uint),
            ]
            version.VerQueryValueW.restype = ctypes.c_int

            class _VSFixedFileInfo(ctypes.Structure):
                _fields_ = [
                    ("dwSignature", ctypes.c_uint32),
                    ("dwStrucVersion", ctypes.c_uint32),
                    ("dwFileVersionMS", ctypes.c_uint32),
                    ("dwFileVersionLS", ctypes.c_uint32),
                    ("dwProductVersionMS", ctypes.c_uint32),
                    ("dwProductVersionLS", ctypes.c_uint32),
                    ("dwFileFlagsMask", ctypes.c_uint32),
                    ("dwFileFlags", ctypes.c_uint32),
                    ("dwFileOS", ctypes.c_uint32),
                    ("dwFileType", ctypes.c_uint32),
                    ("dwFileSubtype", ctypes.c_uint32),
                    ("dwFileDateMS", ctypes.c_uint32),
                    ("dwFileDateLS", ctypes.c_uint32),
                ]

            unused = ctypes.c_uint32()
            size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
            if not size:
                return None
            data = ctypes.create_string_buffer(size)
            if not version.GetFileVersionInfoW(str(path), 0, size, data):
                return None
            pointer = ctypes.c_void_p()
            length = ctypes.c_uint()
            if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
                return None
            info = ctypes.cast(pointer, ctypes.POINTER(_VSFixedFileInfo)).contents
            return ".".join(
                str(part)
                for part in (
                    info.dwFileVersionMS >> 16,
                    info.dwFileVersionMS & 0xFFFF,
                    info.dwFileVersionLS >> 16,
                    info.dwFileVersionLS & 0xFFFF,
                )
            )
        except (OSError, AttributeError, ValueError):
            return None


def publish_robocopy_batch_inbox(manifest: RobocopyBatchManifest) -> None:
    expected_by_name = {entry.source_file_name: entry for entry in manifest.entries}
    observed_by_name: dict[str, dict[str, object]] = {}
    try:
        actual_entries = tuple(manifest.staging_inbox.iterdir())
    except OSError as exc:
        raise LocalFileStagingError(
            "STAGING_MANIFEST_MISMATCH",
            "Retry the transfer because the Robocopy staging inbox cannot be enumerated.",
        ) from exc
    actual_names = {path.name for path in actual_entries}
    if actual_names != set(expected_by_name):
        raise LocalFileStagingError(
            "STAGING_MANIFEST_MISMATCH",
            "Quarantine the staging inbox because it does not match the batch manifest.",
        )
    for path in actual_entries:
        entry = expected_by_name[path.name]
        if not path.is_file() or path.is_symlink():
            raise LocalFileStagingError(
                "STAGING_MANIFEST_MISMATCH",
                "Quarantine the staging inbox because it contains a non-file or reparse entry.",
            )
        observed = _fingerprint_file(path)
        expected = {
            "byte_count": entry.expected_byte_count,
            "content_hash": entry.expected_content_hash,
        }
        if observed != expected:
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_SOURCE_CHANGED",
                "Refresh analysis because source bytes changed during transfer.",
            )
        observed_by_name[path.name] = observed

    for source_file_name, entry in expected_by_name.items():
        copied_path = manifest.staging_inbox / source_file_name
        payload_path = entry.payload_path
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        if payload_path.exists():
            if _fingerprint_file(payload_path) == observed_by_name[source_file_name]:
                copied_path.unlink()
                continue
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_PAYLOAD_RACE",
                "Reload staging state before retrying the transfer.",
            )
        try:
            os.link(copied_path, payload_path)
        except FileExistsError as exc:
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_PAYLOAD_RACE",
                "Reload staging state before retrying the transfer.",
            ) from exc
        except OSError as exc:
            raise LocalFileStagingError(
                "ROBOCOPY_STAGING_PAYLOAD_PUBLISH_FAILED",
                "Retry staging on a filesystem that supports controlled object publication.",
            ) from exc
        copied_path.unlink()
    with suppress(OSError):
        manifest.staging_inbox.rmdir()


def discard_robocopy_batch_inbox(
    *,
    inbox: Path,
    manifest: RobocopyBatchManifest | None,
) -> bool:
    try:
        actual_entries = tuple(inbox.iterdir())
    except FileNotFoundError:
        return True
    except OSError:
        return False

    if manifest is None:
        if actual_entries:
            return False
    else:
        expected_names = {entry.source_file_name for entry in manifest.entries}
        actual_names = {path.name for path in actual_entries}
        if not actual_names.issubset(expected_names):
            return False
        if any(not path.is_file() or path.is_symlink() for path in actual_entries):
            return False

    for path in actual_entries:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            return False
    try:
        inbox.rmdir()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _robocopy_manifest_payload(
    *,
    batch_id: str,
    source_parent: Path,
    staging_inbox: Path,
    log_path: Path,
    entries: tuple[RobocopyBatchManifestEntry, ...],
    profile: RobocopyTransferProfile,
    profile_hash: str,
    manifest_hash: str | None,
) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "batch_kind": "DIRECTORY_MANIFEST",
        "canonical_manifest_hash": manifest_hash,
        "entry_count": len(entries),
        "entries": [
            {
                "expected_fingerprint": {
                    "byte_count": entry.expected_byte_count,
                    "content_hash": entry.expected_content_hash,
                },
                "final_relative_path": entry.final_relative_path,
                "operation_id": entry.operation_id,
                "payload_name": entry.payload_path.name,
                "source_file_name": entry.source_file_name,
                "source_relative_path": entry.source_relative_path,
                "staging_object_id": entry.staging_object_id,
            }
            for entry in entries
        ],
        "log_name": log_path.name,
        "profile": {
            "success_max_exit_code": profile.success_max_exit_code,
            "switches": list(profile.switches),
            "timeout_seconds": profile.timeout_seconds,
        },
        "profile_hash": profile_hash,
        "schema_version": ROBOCOPY_MANIFEST_SCHEMA_VERSION,
        "source_parent_name": source_parent.name,
        "staging_inbox_name": staging_inbox.name,
    }


def _manifest_hash(payload: dict[str, object]) -> str:
    manifest_payload = dict(payload)
    manifest_payload["canonical_manifest_hash"] = None
    return _sha256_text(_canonical_json(manifest_payload))


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_manifest_entries(entries: tuple[RobocopyBatchManifestEntry, ...]) -> None:
    operation_ids: set[str] = set()
    staging_object_ids: set[str] = set()
    file_names: set[str] = set()
    payload_names: set[str] = set()
    for entry in entries:
        _validate_manifest_id(entry.operation_id, "ROBOCOPY_MANIFEST_REQUIRES_SAFE_OPERATION_ID")
        _validate_manifest_id(
            entry.staging_object_id,
            "ROBOCOPY_MANIFEST_REQUIRES_SAFE_STAGING_OBJECT_ID",
        )
        _validate_source_file_name(entry.source_file_name)
        if entry.expected_byte_count < 0 or HASH_PATTERN.fullmatch(entry.expected_content_hash) is None:
            raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_FINGERPRINT_INVALID")
        if not entry.payload_path.name.endswith(".payload"):
            raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_PAYLOAD_NAME_INVALID")
        _add_unique(operation_ids, entry.operation_id, "ROBOCOPY_MANIFEST_DUPLICATE_OPERATION")
        _add_unique(
            staging_object_ids,
            entry.staging_object_id,
            "ROBOCOPY_MANIFEST_DUPLICATE_STAGING_OBJECT",
        )
        _add_unique(file_names, entry.source_file_name, "ROBOCOPY_MANIFEST_DUPLICATE_SOURCE_NAME")
        _add_unique(payload_names, entry.payload_path.name, "ROBOCOPY_MANIFEST_DUPLICATE_PAYLOAD")


def _add_unique(values: set[str], value: str, validation_code: str) -> None:
    if value in values:
        raise RobocopyConfigurationError(validation_code)
    values.add(value)


def _validate_manifest_id(value: str, validation_code: str) -> None:
    if ROBOCOPY_ID_PATTERN.fullmatch(value) is None:
        raise RobocopyConfigurationError(validation_code)


def _robocopy_source_file_name(source_file: Path) -> str:
    return _validate_source_file_name(source_file.name)


def _validate_source_file_name(value: str) -> str:
    if (
        not value
        or Path(value).name != value
        or any(separator in value for separator in ("/", "\\"))
        or value.startswith("-")
        or value.startswith("/")
        or value.startswith("\\")
        or "*" in value
        or "?" in value
        or "\x00" in value
    ):
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_SOURCE_NAME_UNSAFE")
    if _robocopy_switch_name(value) is not None:
        raise RobocopyConfigurationError("ROBOCOPY_MANIFEST_SOURCE_NAME_UNSAFE")
    return value


def _robocopy_environment(system_directory: Path) -> dict[str, str]:
    system_root = str(system_directory.parent)
    return {
        "SystemRoot": system_root,
        "TEMP": os.environ.get("TEMP", str(system_directory)),
        "TMP": os.environ.get("TMP", str(system_directory)),
    }


def _environment_hash(environment: tuple[tuple[str, str], ...]) -> str:
    return _sha256_text(
        _canonical_json(
            {"environment": [[name, value] for name, value in sorted(environment)]}
        )
    )


def _robocopy_switch_name(argument: str) -> str | None:
    if not argument.startswith("/"):
        return None
    return argument[1:].split(":", maxsplit=1)[0].upper()


def _safe_staging_name(operation: RecoveryOperation) -> str:
    object_id = operation.staging_object_id or operation.operation_id
    return object_id.replace(".", "_")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
