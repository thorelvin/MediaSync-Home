from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_WINDOWS_COMMAND_LINE = 32760
FORBIDDEN_ROBOCOPY_SWITCHES = frozenset({"/MIR", "/PURGE", "/MOVE", "/MOV"})
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


class WindowsPackagingError(RuntimeError):
    pass


class RobocopyArgumentError(WindowsPackagingError):
    pass


@dataclass(frozen=True)
class ResolvedExecutable:
    requested_name: str
    system_directory: str
    executable_path: str
    final_path: str
    sha256: str
    file_version: str | None


@dataclass(frozen=True)
class RobocopyLaunchPlan:
    executable: ResolvedExecutable
    argv: tuple[str, ...]
    command_line: str
    command_line_sha256: str
    parsed_argv: tuple[str, ...]
    working_directory: str
    environment: dict[str, str]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_windows() -> None:
    if os.name != "nt":
        raise WindowsPackagingError("0A.5 Windows argv probe requires Windows")


def get_system_directory() -> str:
    require_windows()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if size == 0:
        raise WindowsPackagingError(f"GetSystemDirectoryW failed: {ctypes.get_last_error()}")
    if size >= len(buffer):
        raise WindowsPackagingError("GetSystemDirectoryW output exceeded probe buffer")
    return str(Path(buffer.value))


def normalize_dos_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.abspath(path))


def get_final_path(path: Path) -> str:
    require_windows()
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
    kernel32.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080

    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, 0, invalid):
        raise WindowsPackagingError(f"CreateFileW failed for {path.name}: {ctypes.get_last_error()}")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if size == 0:
            raise WindowsPackagingError(f"GetFinalPathNameByHandleW failed: {ctypes.get_last_error()}")
        if size >= len(buffer):
            raise WindowsPackagingError("final path exceeded probe buffer")
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def get_file_version(path: Path) -> str | None:
    require_windows()
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
        version.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = ctypes.c_int
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint),
        ]
        version.VerQueryValueW.restype = ctypes.c_int

        class VSFixedFileInfo(ctypes.Structure):
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
        info = ctypes.cast(pointer, ctypes.POINTER(VSFixedFileInfo)).contents
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


def resolve_system_executable(name: str = "Robocopy.exe") -> ResolvedExecutable:
    if Path(name).name != name or any(separator in name for separator in ("/", "\\")):
        raise WindowsPackagingError("system executable name must be a basename")
    system_directory = get_system_directory()
    candidate = Path(system_directory) / name
    if not candidate.is_file():
        raise WindowsPackagingError(f"{name} was not found under the Windows system directory")
    final_path = get_final_path(candidate)
    normalized_system = normalize_dos_path(system_directory)
    normalized_final = normalize_dos_path(final_path)
    if os.path.dirname(normalized_final) != normalized_system:
        raise WindowsPackagingError("resolved executable escaped the Windows system directory")
    if os.path.basename(normalized_final).lower() != name.lower():
        raise WindowsPackagingError("resolved executable basename mismatch")
    return ResolvedExecutable(
        requested_name=name,
        system_directory=system_directory,
        executable_path=str(candidate),
        final_path=final_path,
        sha256=sha256_file(candidate),
        file_version=get_file_version(candidate),
    )


def quote_windows_arg(argument: str) -> str:
    if "\x00" in argument:
        raise ValueError("Windows argv cannot contain NUL")
    if argument == "":
        return '""'
    if not any(character.isspace() or character == '"' for character in argument):
        return argument

    result = ['"']
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            result.append("\\" * (backslashes * 2 + 1))
            result.append('"')
            backslashes = 0
            continue
        result.append("\\" * backslashes)
        result.append(character)
        backslashes = 0
    result.append("\\" * (backslashes * 2))
    result.append('"')
    return "".join(result)


def build_windows_command_line(argv: list[str] | tuple[str, ...]) -> str:
    if not argv:
        raise ValueError("argv must contain at least the executable")
    command_line = " ".join(quote_windows_arg(str(argument)) for argument in argv)
    if len(command_line) > MAX_WINDOWS_COMMAND_LINE:
        raise ValueError(f"command line exceeds {MAX_WINDOWS_COMMAND_LINE} characters")
    return command_line


def parse_windows_command_line(command_line: str) -> tuple[str, ...]:
    require_windows()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    argc = ctypes.c_int()
    argv_pointer = shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
    if not argv_pointer:
        raise WindowsPackagingError(f"CommandLineToArgvW failed: {ctypes.get_last_error()}")
    try:
        return tuple(argv_pointer[index] for index in range(argc.value))
    finally:
        kernel32.LocalFree(argv_pointer)


def validate_no_forbidden_robocopy_switches(argv: tuple[str, ...] | list[str]) -> None:
    for argument in argv[1:]:
        upper = argument.upper()
        if upper in FORBIDDEN_ROBOCOPY_SWITCHES:
            raise RobocopyArgumentError(f"forbidden Robocopy switch: {argument}")


def validate_robocopy_command_line(command_line: str, expected_executable: str) -> tuple[str, ...]:
    parsed = parse_windows_command_line(command_line)
    if not parsed:
        raise RobocopyArgumentError("empty command line")
    if normalize_dos_path(parsed[0]) != normalize_dos_path(expected_executable):
        raise RobocopyArgumentError("Robocopy executable did not match resolved system executable")
    validate_no_forbidden_robocopy_switches(parsed)
    return parsed


def minimal_unicode_environment(system_directory: str) -> dict[str, str]:
    system_root = str(Path(system_directory).parent)
    return {
        "SystemRoot": system_root,
        "WINDIR": system_root,
        "PATH": os.pathsep.join([system_directory, system_root]),
        "TEMP": os.environ.get("TEMP", system_directory),
        "TMP": os.environ.get("TMP", system_directory),
    }


def build_robocopy_launch_plan(
    executable: ResolvedExecutable,
    source_root: Path,
    staging_root: Path,
    log_path: Path,
    *,
    switches: tuple[str, ...] = DEFAULT_ROBOCOPY_SWITCHES,
    working_directory: Path | None = None,
) -> RobocopyLaunchPlan:
    if not source_root.is_absolute() or not staging_root.is_absolute() or not log_path.is_absolute():
        raise RobocopyArgumentError("Robocopy source, staging and log roots must be absolute")
    typed_argv = [
        executable.executable_path,
        str(source_root),
        str(staging_root),
        *switches,
        f"/UNILOG:{log_path}",
    ]
    validate_no_forbidden_robocopy_switches(typed_argv)
    command_line = build_windows_command_line(typed_argv)
    parsed = validate_robocopy_command_line(command_line, executable.executable_path)
    environment = minimal_unicode_environment(executable.system_directory)
    workdir = str(working_directory or Path(executable.system_directory))
    return RobocopyLaunchPlan(
        executable=executable,
        argv=tuple(typed_argv),
        command_line=command_line,
        command_line_sha256=sha256_bytes(command_line),
        parsed_argv=parsed,
        working_directory=workdir,
        environment=environment,
    )


def argv_round_trip_payloads() -> list[list[str]]:
    near_limit = "x" * 30_000
    return [
        ["plain", "two"],
        ["contains spaces", "tab\tseparated"],
        ["", "empty-first"],
        ["C:\\path with spaces\\trailing\\", "quote\"inside"],
        ["\\\\server\\share name\\folder\\", "unicode-æøå-雪"],
        ["/looks-like-switch", "literal-user-name"],
        [near_limit],
    ]


def child_round_trip(arguments: list[str]) -> dict[str, Any]:
    script = Path(__file__).resolve()
    argv = [sys.executable, str(script), "echo-argv-json", *arguments]
    command_line = build_windows_command_line(argv)
    parsed = parse_windows_command_line(command_line)
    if parsed != tuple(argv):
        return {"status": "FAIL", "reason": "COMMAND_LINE_TO_ARGV_MISMATCH", "length": len(command_line)}
    completed = subprocess.run(
        command_line,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        return {
            "status": "FAIL",
            "reason": "CHILD_NONZERO",
            "returncode": completed.returncode,
            "length": len(command_line),
            "stderr": completed.stderr[-500:],
        }
    received = json.loads(completed.stdout)
    return {
        "status": "PASS" if received == arguments else "FAIL",
        "length": len(command_line),
        "received_count": len(received),
    }


def package_toolchain_probe() -> dict[str, Any]:
    module_names = ("PySide6", "blake3", "nuitka")
    modules = {name: importlib.util.find_spec(name) is not None for name in module_names}
    tools = {
        "pyside6-deploy": shutil.which("pyside6-deploy") is not None,
        "nuitka": shutil.which("nuitka") is not None,
        "cl": shutil.which("cl") is not None,
        "rc": shutil.which("rc") is not None,
        "signtool": shutil.which("signtool") is not None,
    }
    missing_modules = [name for name, found in modules.items() if not found]
    missing_tools = [name for name, found in tools.items() if not found]
    status = "PASS" if not missing_modules and not missing_tools else "BLOCKED_BY_ENVIRONMENT"
    return {
        "status": status,
        "python_executable": "<current-python>",
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "modules": modules,
        "tools": tools,
        "missing_modules": missing_modules,
        "missing_tools": missing_tools,
        "clean_windows_vm": "BLOCKED_BY_ENVIRONMENT",
    }


def sanitized_launch_plan(plan: RobocopyLaunchPlan) -> dict[str, Any]:
    parsed_switches = [
        argument
        for argument in plan.parsed_argv[3:]
        if not argument.upper().startswith("/UNILOG:")
    ]
    return {
        "executable": {
            "requested_name": plan.executable.requested_name,
            "system_directory": plan.executable.system_directory,
            "final_path": plan.executable.final_path,
            "sha256": plan.executable.sha256,
            "file_version": plan.executable.file_version,
        },
        "argv_shape": [
            "<resolved-robocopy>",
            "<absolute-source-root>",
            "<absolute-staging-root>",
            *parsed_switches,
            "/UNILOG:<absolute-local-log-file>",
        ],
        "parsed_equals_built": plan.parsed_argv == plan.argv,
        "command_line_sha256": plan.command_line_sha256,
        "command_line_length": len(plan.command_line),
        "working_directory": "<windows-system-directory>",
        "environment_keys": sorted(plan.environment),
    }


def run_demo(output: Path) -> int:
    executable = resolve_system_executable("Robocopy.exe")
    plan = build_robocopy_launch_plan(
        executable,
        Path("C:/MediaSyncHome-Spike/source root"),
        Path("C:/MediaSyncHome-Spike/staging inbox"),
        Path("C:/MediaSyncHome-Spike/logs/batch-000001.robocopy.log"),
    )
    round_trips = [child_round_trip(payload) for payload in argv_round_trip_payloads()]
    negative_switches: dict[str, str] = {}
    for forbidden in sorted(FORBIDDEN_ROBOCOPY_SWITCHES):
        try:
            build_robocopy_launch_plan(
                executable,
                Path("C:/MediaSyncHome-Spike/source"),
                Path("C:/MediaSyncHome-Spike/staging"),
                Path("C:/MediaSyncHome-Spike/logs/log.txt"),
                switches=DEFAULT_ROBOCOPY_SWITCHES + (forbidden,),
            )
            negative_switches[forbidden] = "NOT_REJECTED"
        except RobocopyArgumentError:
            negative_switches[forbidden] = "REJECTED"

    summary = {
        "created_utc": utc_now(),
        "resolved_robocopy": sanitized_launch_plan(plan)["executable"],
        "launch_plan": sanitized_launch_plan(plan),
        "round_trip": {
            "payload_count": len(round_trips),
            "all_passed": all(item["status"] == "PASS" for item in round_trips),
            "max_command_line_length": max(item["length"] for item in round_trips),
            "results": round_trips,
        },
        "forbidden_switch_validation": negative_switches,
        "packaging_preflight": package_toolchain_probe(),
        "real_robocopy_started": False,
        "clean_vm_smoke_test": "BLOCKED_BY_ENVIRONMENT",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MediaSync Home 0A.5 Windows argv and packaging spike")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--output", required=True)
    sub.add_parser("echo-argv-json")
    args, rest = parser.parse_known_args(argv)
    if args.command == "echo-argv-json":
        sys.stdout.write(json.dumps(rest))
        return 0
    if args.command == "demo":
        return run_demo(Path(args.output))
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
