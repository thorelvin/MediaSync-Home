from __future__ import annotations

import ctypes
import hashlib
import os
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
        validate_robocopy_switches(profile.switches)
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

        try:
            command_plan = build_robocopy_single_file_command_plan(
                executable=self._executable_resolver.resolve("Robocopy.exe"),
                source_file=source_path,
                staging_inbox=inbox,
                log_path=log_path,
                working_directory=self._robocopy_work_root_for(operation),
                working_directory_root=self._robocopy_work_root_for(operation),
                profile=self._profile,
            )
            exit_code = self._run_robocopy(command_plan.launch_plan)
            if exit_code > self._profile.success_max_exit_code:
                raise LocalFileStagingError(
                    "ROBOCOPY_TRANSFER_FAILED",
                    "Retry the transfer after reviewing the Robocopy batch log.",
                )
            _publish_robocopy_inbox_file(
                inbox=inbox,
                source_file_name=source_path.name,
                payload_path=payload_path,
                expected_fingerprint=expected,
            )
        except (RobocopyConfigurationError, WindowsCommandLineError) as exc:
            raise LocalFileStagingError(
                "ROBOCOPY_TRANSFER_CONFIGURATION_INVALID",
                "Fix the Robocopy executable/profile configuration before retrying.",
            ) from exc

        return StagingTransferEvidence(
            transfer_state=f"ROBOCOPY_EXIT_{exit_code}_TRANSFERRED_TO_STAGING"
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
    validate_robocopy_switches(profile.switches)

    argv = (
        str(executable.executable_path),
        str(source_file.parent),
        str(staging_inbox),
        source_file.name,
        *profile.switches,
        f"/UNILOG:{log_path}",
    )
    validate_robocopy_switches(argv[3:])
    command_line = build_windows_command_line(argv)
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
        file_name=source_file.name,
        log_path=log_path,
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


def classify_robocopy_exit_code(exit_code: int) -> str:
    if exit_code < 0:
        return "INVALID"
    if exit_code <= ROBOCOPY_SUCCESS_MAX_EXIT_CODE:
        return "NON_FATAL"
    return "FATAL"


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


def _publish_robocopy_inbox_file(
    *,
    inbox: Path,
    source_file_name: str,
    payload_path: Path,
    expected_fingerprint: dict[str, object],
) -> None:
    copied_path = inbox / source_file_name
    if not copied_path.is_file() or copied_path.is_symlink():
        raise LocalFileStagingError(
            "ROBOCOPY_STAGING_INBOX_FILE_MISSING",
            "Retry the transfer because Robocopy did not produce the expected staging file.",
        )
    extras = [path.name for path in inbox.iterdir() if path.name != source_file_name]
    if extras:
        raise LocalFileStagingError(
            "ROBOCOPY_STAGING_INBOX_CONTAINS_EXTRA_ENTRIES",
            "Quarantine the unexpected staging inbox and retry the transfer.",
        )
    observed = _fingerprint_file(copied_path)
    if observed != expected_fingerprint:
        raise LocalFileStagingError(
            "ROBOCOPY_STAGING_SOURCE_CHANGED",
            "Refresh analysis because the source file changed during transfer.",
        )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
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
        inbox.rmdir()


def _robocopy_environment(system_directory: Path) -> dict[str, str]:
    system_root = str(system_directory.parent)
    return {
        "SystemRoot": system_root,
        "TEMP": os.environ.get("TEMP", str(system_directory)),
        "TMP": os.environ.get("TMP", str(system_directory)),
    }


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
