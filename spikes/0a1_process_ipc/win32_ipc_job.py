from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any


if os.name != "nt":
    raise SystemExit("0A.1 process/IPC spike is Windows-only")


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
LPVOID = wintypes.LPVOID
LPWSTR = wintypes.LPWSTR
LPCWSTR = wintypes.LPCWSTR
SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t

INVALID_HANDLE_VALUE = HANDLE(-1).value
ERROR_PIPE_CONNECTED = 535
ERROR_PIPE_BUSY = 231
WAIT_TIMEOUT = 258
WAIT_OBJECT_0 = 0
STILL_ACTIVE = 259

TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TOKEN_SESSION_ID = 12
TOKEN_ELEVATION = 20
TOKEN_INTEGRITY_LEVEL = 25

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

PROTOCOL_VERSION = 1
MAX_COMMAND_FRAME = 1024 * 1024

class SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", DWORD),
        ("lpSecurityDescriptor", LPVOID),
        ("bInheritHandle", BOOL),
    ]


class SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", LPVOID), ("Attributes", DWORD)]


class TokenUser(ctypes.Structure):
    _fields_ = [("User", SidAndAttributes)]


class TokenMandatoryLabel(ctypes.Structure):
    _fields_ = [("Label", SidAndAttributes)]


class TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", DWORD)]


class StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", LPWSTR),
        ("lpDesktop", LPWSTR),
        ("lpTitle", LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", LPVOID),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JobObjectBasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


def configure_win32_signatures() -> None:
    kernel32.GetCurrentProcess.restype = HANDLE
    kernel32.GetCurrentThread.restype = HANDLE
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL
    kernel32.LocalFree.argtypes = [LPVOID]
    kernel32.LocalFree.restype = LPVOID
    kernel32.CreateNamedPipeW.argtypes = [
        LPCWSTR,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        ctypes.POINTER(SecurityAttributes),
    ]
    kernel32.CreateNamedPipeW.restype = HANDLE
    kernel32.ConnectNamedPipe.argtypes = [HANDLE, LPVOID]
    kernel32.ConnectNamedPipe.restype = BOOL
    kernel32.DisconnectNamedPipe.argtypes = [HANDLE]
    kernel32.FlushFileBuffers.argtypes = [HANDLE]
    kernel32.CreateFileW.argtypes = [LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE]
    kernel32.CreateFileW.restype = HANDLE
    kernel32.ReadFile.argtypes = [HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID]
    kernel32.ReadFile.restype = BOOL
    kernel32.WriteFile.argtypes = [HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID]
    kernel32.WriteFile.restype = BOOL
    kernel32.WaitNamedPipeW.argtypes = [LPCWSTR, DWORD]
    kernel32.WaitNamedPipeW.restype = BOOL
    kernel32.CreateProcessW.argtypes = [
        LPCWSTR,
        LPWSTR,
        LPVOID,
        LPVOID,
        BOOL,
        DWORD,
        LPVOID,
        LPCWSTR,
        ctypes.POINTER(StartupInfoW),
        ctypes.POINTER(ProcessInformation),
    ]
    kernel32.CreateProcessW.restype = BOOL
    kernel32.CreateJobObjectW.argtypes = [LPVOID, LPCWSTR]
    kernel32.CreateJobObjectW.restype = HANDLE
    kernel32.SetInformationJobObject.argtypes = [HANDLE, DWORD, LPVOID, DWORD]
    kernel32.SetInformationJobObject.restype = BOOL
    kernel32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
    kernel32.AssignProcessToJobObject.restype = BOOL
    kernel32.ResumeThread.argtypes = [HANDLE]
    kernel32.ResumeThread.restype = DWORD
    kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
    kernel32.WaitForSingleObject.restype = DWORD
    kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
    kernel32.GetExitCodeProcess.restype = BOOL
    kernel32.TerminateProcess.argtypes = [HANDLE, DWORD]
    kernel32.TerminateProcess.restype = BOOL

    advapi32.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
    advapi32.OpenProcessToken.restype = BOOL
    advapi32.OpenThreadToken.argtypes = [HANDLE, DWORD, BOOL, ctypes.POINTER(HANDLE)]
    advapi32.OpenThreadToken.restype = BOOL
    advapi32.GetTokenInformation.argtypes = [HANDLE, DWORD, LPVOID, DWORD, ctypes.POINTER(DWORD)]
    advapi32.GetTokenInformation.restype = BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [LPVOID, ctypes.POINTER(LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        LPCWSTR,
        DWORD,
        ctypes.POINTER(LPVOID),
        LPVOID,
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = BOOL
    advapi32.ImpersonateNamedPipeClient.argtypes = [HANDLE]
    advapi32.ImpersonateNamedPipeClient.restype = BOOL
    advapi32.RevertToSelf.restype = BOOL


configure_win32_signatures()


def _handle_value(handle: int | HANDLE) -> int:
    if isinstance(handle, HANDLE):
        return int(handle.value or 0)
    return int(handle)


def _raise_last_error(prefix: str) -> None:
    code = ctypes.get_last_error()
    raise OSError(code, f"{prefix}: {ctypes.FormatError(code)}")


def _checked_handle(handle: int | HANDLE, prefix: str) -> HANDLE:
    value = _handle_value(handle)
    if value == 0 or value == INVALID_HANDLE_VALUE:
        _raise_last_error(prefix)
    return HANDLE(value)


def close_handle(handle: HANDLE | int | None) -> None:
    if not handle:
        return
    kernel32.CloseHandle(HANDLE(_handle_value(handle)))


def _get_token_information(token: HANDLE, info_class: int) -> ctypes.Array[Any]:
    needed = DWORD(0)
    advapi32.GetTokenInformation(token, info_class, None, 0, ctypes.byref(needed))
    if needed.value == 0:
        _raise_last_error("GetTokenInformation(size)")
    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetTokenInformation(
        token, info_class, buffer, needed, ctypes.byref(needed)
    ):
        _raise_last_error("GetTokenInformation")
    return buffer


def _sid_to_string(sid: LPVOID) -> str:
    output = LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
        _raise_last_error("ConvertSidToStringSidW")
    try:
        return output.value
    finally:
        kernel32.LocalFree(output)


def _open_process_token() -> HANDLE:
    token = HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        _raise_last_error("OpenProcessToken")
    return token


def _open_thread_token() -> HANDLE:
    token = HANDLE()
    if not advapi32.OpenThreadToken(kernel32.GetCurrentThread(), TOKEN_QUERY, True, ctypes.byref(token)):
        _raise_last_error("OpenThreadToken")
    return token


def token_snapshot(token: HANDLE) -> dict[str, Any]:
    user_buffer = _get_token_information(token, TOKEN_USER)
    user = ctypes.cast(user_buffer, ctypes.POINTER(TokenUser)).contents
    session_buffer = _get_token_information(token, TOKEN_SESSION_ID)
    session_id = ctypes.cast(session_buffer, ctypes.POINTER(DWORD)).contents.value
    elevation_buffer = _get_token_information(token, TOKEN_ELEVATION)
    elevation = ctypes.cast(elevation_buffer, ctypes.POINTER(TokenElevation)).contents

    integrity_sid = ""
    try:
        integrity_buffer = _get_token_information(token, TOKEN_INTEGRITY_LEVEL)
        label = ctypes.cast(integrity_buffer, ctypes.POINTER(TokenMandatoryLabel)).contents
        integrity_sid = _sid_to_string(label.Label.Sid)
    except OSError:
        integrity_sid = "UNAVAILABLE"

    sid = _sid_to_string(user.User.Sid)
    return {
        "sid": sid,
        "sid_hash": hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16],
        "session_id": session_id,
        "integrity_sid": integrity_sid,
        "is_elevated": bool(elevation.TokenIsElevated),
    }


def current_token_snapshot() -> dict[str, Any]:
    token = _open_process_token()
    try:
        return token_snapshot(token)
    finally:
        close_handle(token)


def make_pipe_security_attributes(current_sid: str) -> tuple[SecurityAttributes, LPVOID]:
    # Only the current user and LocalSystem receive generic-all on the spike pipe.
    sddl = f"D:P(A;;GA;;;{current_sid})(A;;GA;;;SY)"
    descriptor = LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        _raise_last_error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    attrs = SecurityAttributes()
    attrs.nLength = ctypes.sizeof(SecurityAttributes)
    attrs.lpSecurityDescriptor = descriptor
    attrs.bInheritHandle = False
    return attrs, descriptor


def pipe_path(pipe_name: str) -> str:
    return rf"\\.\pipe\{pipe_name}"


def make_pipe_name(run_id: str | None = None) -> str:
    current = current_token_snapshot()
    suffix = run_id or uuid.uuid4().hex
    return f"MediaSyncHome-0A1-{current['sid_hash']}-{suffix}"


def create_pipe(pipe_name: str, attrs: SecurityAttributes) -> HANDLE:
    handle = kernel32.CreateNamedPipeW(
        pipe_path(pipe_name),
        PIPE_ACCESS_DUPLEX,
        PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1,
        65536,
        65536,
        3000,
        ctypes.byref(attrs),
    )
    return _checked_handle(handle, "CreateNamedPipeW")


def connect_pipe(pipe: HANDLE) -> None:
    ok = kernel32.ConnectNamedPipe(pipe, None)
    if not ok:
        code = ctypes.get_last_error()
        if code != ERROR_PIPE_CONNECTED:
            raise OSError(code, f"ConnectNamedPipe: {ctypes.FormatError(code)}")


def read_exact(handle: HANDLE, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk_size = min(remaining, 65536)
        buffer = ctypes.create_string_buffer(chunk_size)
        read = DWORD(0)
        if not kernel32.ReadFile(handle, buffer, chunk_size, ctypes.byref(read), None):
            _raise_last_error("ReadFile")
        if read.value == 0:
            raise ConnectionError("pipe closed while reading frame")
        chunks.append(buffer.raw[: read.value])
        remaining -= read.value
    return b"".join(chunks)


def write_all(handle: HANDLE, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = DWORD(0)
        chunk = payload[offset : offset + 65536]
        buffer = ctypes.create_string_buffer(chunk)
        if not kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            _raise_last_error("WriteFile")
        offset += written.value


def read_frame(handle: HANDLE) -> dict[str, Any]:
    header = read_exact(handle, 4)
    (length,) = struct.unpack("<I", header)
    if length > MAX_COMMAND_FRAME:
        raise ValueError("FRAME_TOO_LARGE")
    payload = read_exact(handle, length)
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("INVALID_UTF8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("INVALID_JSON") from exc


def write_frame(handle: HANDLE, message: dict[str, Any]) -> None:
    payload = json.dumps(message, sort_keys=True, separators=(",", ":")).encode("utf-8")
    write_all(handle, struct.pack("<I", len(payload)) + payload)


def open_pipe_client(pipe_name: str, timeout_ms: int = 5000) -> HANDLE:
    deadline = time.monotonic() + timeout_ms / 1000
    path = pipe_path(pipe_name)
    while True:
        handle = kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        value = _handle_value(handle)
        if value not in (0, INVALID_HANDLE_VALUE):
            return HANDLE(value)
        code = ctypes.get_last_error()
        if code == ERROR_PIPE_BUSY:
            kernel32.WaitNamedPipeW(path, 250)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out opening pipe {pipe_name}; last_error={code}")
        time.sleep(0.05)


def send_client_message(pipe_name: str, message: dict[str, Any]) -> dict[str, Any]:
    handle = open_pipe_client(pipe_name)
    try:
        write_frame(handle, message)
        return read_frame(handle)
    finally:
        close_handle(handle)


def _canonical_payload_hash(message: dict[str, Any]) -> str:
    material = {
        "protocol_version": message.get("protocol_version"),
        "schema_version": message.get("schema_version"),
        "command_name": message.get("command_name"),
        "payload": message.get("payload", {}),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_receipts(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_receipts(path: Path, receipts: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipts, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _client_identity_from_pipe(pipe: HANDLE) -> dict[str, Any]:
    if not advapi32.ImpersonateNamedPipeClient(pipe):
        _raise_last_error("ImpersonateNamedPipeClient")
    token: HANDLE | None = None
    try:
        token = _open_thread_token()
        return token_snapshot(token)
    finally:
        if token:
            close_handle(token)
        advapi32.RevertToSelf()


def handle_message(
    message: dict[str, Any],
    client_identity: dict[str, Any],
    host_identity: dict[str, Any],
    receipt_store: Path,
) -> dict[str, Any]:
    if message.get("protocol_version") != PROTOCOL_VERSION:
        return {"status": "REJECTED", "reason": "PROTOCOL_MISMATCH"}
    if client_identity["sid"] != host_identity["sid"]:
        return {"status": "REJECTED", "reason": "SID_MISMATCH"}
    if message.get("role") not in {"gui", "trigger"}:
        return {"status": "REJECTED", "reason": "ROLE_NOT_ALLOWED"}

    message_type = message.get("message_type")
    if message_type == "HANDSHAKE":
        return {
            "status": "READY",
            "authenticated_sid_hash": client_identity["sid_hash"],
            "session_id": client_identity["session_id"],
            "claimed_sid_ignored": bool(message.get("claimed_sid")),
        }

    if message_type != "COMMAND":
        return {"status": "REJECTED", "reason": "UNKNOWN_MESSAGE_TYPE"}

    key = message.get("idempotency_key")
    if not isinstance(key, str) or not key:
        return {"status": "REJECTED", "reason": "MISSING_IDEMPOTENCY_KEY"}

    payload_hash = _canonical_payload_hash(message)
    principal_hash = client_identity["sid_hash"]
    receipts = _load_receipts(receipt_store)
    existing = receipts.get(key)
    fingerprint = {
        "payload_hash": payload_hash,
        "principal_hash": principal_hash,
        "schema_version": message.get("schema_version"),
        "command_name": message.get("command_name"),
    }
    if existing:
        if existing["fingerprint"] != fingerprint:
            return {"status": "REJECTED", "reason": "IDEMPOTENCY_CONFLICT"}
        return {
            "status": "ACCEPTED",
            "receipt_id": existing["receipt_id"],
            "deduplicated": True,
        }

    receipt_id = str(uuid.uuid4())
    receipts[key] = {"receipt_id": receipt_id, "fingerprint": fingerprint}
    _save_receipts(receipt_store, receipts)
    return {"status": "ACCEPTED", "receipt_id": receipt_id, "deduplicated": False}


def run_host(pipe_name: str, receipt_store: Path, ready_file: Path | None, connections: int) -> None:
    host_identity = current_token_snapshot()
    attrs, descriptor = make_pipe_security_attributes(host_identity["sid"])
    try:
        for index in range(connections):
            pipe = create_pipe(pipe_name, attrs)
            if ready_file:
                ready_file.parent.mkdir(parents=True, exist_ok=True)
                ready_file.write_text(
                    json.dumps(
                        {
                            "pipe_name": pipe_name,
                            "host_sid_hash": host_identity["sid_hash"],
                            "connection_index": index,
                            "hostname": socket.gethostname(),
                            "ready": True,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            try:
                connect_pipe(pipe)
                try:
                    message = read_frame(pipe)
                    client_identity = _client_identity_from_pipe(pipe)
                    response = handle_message(message, client_identity, host_identity, receipt_store)
                except OSError as exc:
                    response = {"status": "REJECTED", "reason": f"WIN32_ERROR_{exc.errno}"}
                except Exception as exc:  # noqa: BLE001 - spike server returns sanitized errors
                    response = {"status": "REJECTED", "reason": type(exc).__name__}
                write_frame(pipe, response)
            finally:
                kernel32.FlushFileBuffers(pipe)
                kernel32.DisconnectNamedPipe(pipe)
                close_handle(pipe)
    finally:
        kernel32.LocalFree(descriptor)


def create_suspended_process(args: list[str], cwd: Path) -> ProcessInformation:
    command_line = subprocess.list2cmdline(args)
    mutable_command = ctypes.create_unicode_buffer(command_line)
    startup = StartupInfoW()
    startup.cb = ctypes.sizeof(StartupInfoW)
    proc_info = ProcessInformation()
    if not kernel32.CreateProcessW(
        None,
        mutable_command,
        None,
        None,
        False,
        CREATE_SUSPENDED,
        None,
        str(cwd),
        ctypes.byref(startup),
        ctypes.byref(proc_info),
    ):
        _raise_last_error("CreateProcessW")
    return proc_info


def assign_kill_on_close_job(process_handle: HANDLE) -> HANDLE:
    job = _checked_handle(kernel32.CreateJobObjectW(None, None), "CreateJobObjectW")
    info = JobObjectExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        close_handle(job)
        _raise_last_error("SetInformationJobObject")
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        close_handle(job)
        _raise_last_error("AssignProcessToJobObject")
    return job


def prove_job_object_containment(work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    marker = work_dir / "child_started.marker"
    if marker.exists():
        marker.unlink()
    child_args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "child",
        "--marker",
        str(marker),
        "--hold-seconds",
        "30",
    ]
    proc = create_suspended_process(child_args, Path.cwd())
    job: HANDLE | None = None
    try:
        time.sleep(0.35)
        precontain_marker_seen = marker.exists()
        job = assign_kill_on_close_job(proc.hProcess)
        resumed = kernel32.ResumeThread(proc.hThread)
        if resumed == 0xFFFFFFFF:
            _raise_last_error("ResumeThread")

        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        marker_seen_after_resume = marker.exists()

        close_handle(job)
        job = None
        wait_result = kernel32.WaitForSingleObject(proc.hProcess, 5000)
        exit_code = DWORD(0)
        kernel32.GetExitCodeProcess(proc.hProcess, ctypes.byref(exit_code))
        killed_by_job_close = wait_result == WAIT_OBJECT_0 and exit_code.value != STILL_ACTIVE
        if not killed_by_job_close:
            kernel32.TerminateProcess(proc.hProcess, 99)
        return {
            "precontain_marker_seen": precontain_marker_seen,
            "marker_seen_after_resume": marker_seen_after_resume,
            "kill_on_close_observed": killed_by_job_close,
            "child_pid": int(proc.dwProcessId),
            "exit_code_after_job_close": int(exit_code.value),
        }
    finally:
        if job:
            close_handle(job)
        close_handle(proc.hThread)
        close_handle(proc.hProcess)


def run_child(marker: Path, hold_seconds: float) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("started\n", encoding="utf-8")
    time.sleep(hold_seconds)


def build_message(
    *,
    message_type: str = "COMMAND",
    role: str = "gui",
    protocol_version: int = PROTOCOL_VERSION,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": protocol_version,
        "schema_version": 1,
        "message_type": message_type,
        "request_id": str(uuid.uuid4()),
        "client_instance_id": str(uuid.uuid4()),
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "command_name": "SPIKE_NOOP",
        "role": role,
        "claimed_sid": "S-1-5-21-payload-identity-must-not-authorize",
        "payload": payload or {"noop": True},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MediaSync Home 0A.1 Windows process/IPC spike")
    sub = parser.add_subparsers(dest="command", required=True)

    host = sub.add_parser("host")
    host.add_argument("--pipe-name", required=True)
    host.add_argument("--receipt-store", required=True)
    host.add_argument("--ready-file")
    host.add_argument("--connections", type=int, default=1)

    client = sub.add_parser("client")
    client.add_argument("--pipe-name", required=True)
    client.add_argument("--message-json", required=True)

    child = sub.add_parser("child")
    child.add_argument("--marker", required=True)
    child.add_argument("--hold-seconds", type=float, default=30)

    job = sub.add_parser("prove-job")
    job.add_argument("--work-dir", required=True)

    args = parser.parse_args(argv)
    if args.command == "host":
        run_host(
            pipe_name=args.pipe_name,
            receipt_store=Path(args.receipt_store),
            ready_file=Path(args.ready_file) if args.ready_file else None,
            connections=args.connections,
        )
        return 0
    if args.command == "client":
        response = send_client_message(args.pipe_name, json.loads(args.message_json))
        print(json.dumps(response, sort_keys=True))
        return 0
    if args.command == "child":
        run_child(Path(args.marker), args.hold_seconds)
        return 0
    if args.command == "prove-job":
        print(json.dumps(prove_job_object_containment(Path(args.work_dir)), sort_keys=True))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
