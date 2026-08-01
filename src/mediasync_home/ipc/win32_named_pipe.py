from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from math import ceil
from threading import Event
from typing import Any
from uuid import uuid4

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    COMMAND_SCHEMA_VERSION,
    MAX_FRAME_BYTES,
    MAX_QUERY_RESPONSE_BYTES,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
    decode_frame,
    encode_frame,
    optional_request_id_from_frame,
    request_id_from_frame,
)
from mediasync_home.ipc.server import EngineHostIpcService


if os.name != "nt":  # pragma: no cover - import guard for non-Windows tooling
    raise ImportError("win32_named_pipe is available only on Windows")


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
LPVOID = wintypes.LPVOID
LPWSTR = wintypes.LPWSTR
LPCWSTR = wintypes.LPCWSTR

INVALID_HANDLE_VALUE = HANDLE(-1).value
ERROR_FILE_NOT_FOUND = 2
ERROR_BROKEN_PIPE = 109
ERROR_OPERATION_ABORTED = 995
ERROR_IO_PENDING = 997
ERROR_NOT_FOUND = 1168
ERROR_NO_DATA = 232
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_PIPE_BUSY = 231
ERROR_PIPE_CONNECTED = 535

TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TOKEN_SESSION_ID = 12

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
PIPE_MODE = PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
SECURITY_DESCRIPTOR_REVISION = 1
DEFAULT_BUFFER_SIZE = 65_536
DEFAULT_REQUEST_TIMEOUT_MS = 5_000
DEFAULT_RESPONSE_TIMEOUT_MS = 5_000
DEFAULT_ACK_TIMEOUT_MS = 1_000
CANCELLATION_POLL_MS = 25
RESPONSE_ACK = b"\x06"
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF


class Win32PipeError(OSError):
    pass


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


class Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", DWORD),
        ("OffsetHigh", DWORD),
        ("hEvent", HANDLE),
    ]


def _configure_signatures() -> None:
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
    kernel32.ConnectNamedPipe.argtypes = [HANDLE, ctypes.POINTER(Overlapped)]
    kernel32.ConnectNamedPipe.restype = BOOL
    kernel32.DisconnectNamedPipe.argtypes = [HANDLE]
    kernel32.DisconnectNamedPipe.restype = BOOL
    kernel32.CreateFileW.argtypes = [LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE]
    kernel32.CreateFileW.restype = HANDLE
    kernel32.ReadFile.argtypes = [
        HANDLE,
        LPVOID,
        DWORD,
        ctypes.POINTER(DWORD),
        ctypes.POINTER(Overlapped),
    ]
    kernel32.ReadFile.restype = BOOL
    kernel32.WriteFile.argtypes = [
        HANDLE,
        LPVOID,
        DWORD,
        ctypes.POINTER(DWORD),
        ctypes.POINTER(Overlapped),
    ]
    kernel32.WriteFile.restype = BOOL
    kernel32.WaitNamedPipeW.argtypes = [LPCWSTR, DWORD]
    kernel32.WaitNamedPipeW.restype = BOOL
    kernel32.CreateEventW.argtypes = [LPVOID, BOOL, BOOL, LPCWSTR]
    kernel32.CreateEventW.restype = HANDLE
    kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
    kernel32.WaitForSingleObject.restype = DWORD
    kernel32.CancelIoEx.argtypes = [HANDLE, ctypes.POINTER(Overlapped)]
    kernel32.CancelIoEx.restype = BOOL
    kernel32.GetOverlappedResult.argtypes = [
        HANDLE,
        ctypes.POINTER(Overlapped),
        ctypes.POINTER(DWORD),
        BOOL,
    ]
    kernel32.GetOverlappedResult.restype = BOOL

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


_configure_signatures()


def _handle_value(handle: int | HANDLE) -> int:
    if isinstance(handle, HANDLE):
        return int(handle.value or 0)
    return int(handle)


def _raise_last_error(prefix: str) -> None:
    code = ctypes.get_last_error()
    raise Win32PipeError(code, f"{prefix}: {ctypes.FormatError(code)}")


def _checked_handle(handle: int | HANDLE, prefix: str) -> HANDLE:
    value = _handle_value(handle)
    if value == 0 or value == INVALID_HANDLE_VALUE:
        _raise_last_error(prefix)
    return HANDLE(value)


def _close_handle(handle: HANDLE | int | None) -> None:
    if handle:
        kernel32.CloseHandle(HANDLE(_handle_value(handle)))


def _get_token_information(token: HANDLE, info_class: int) -> ctypes.Array[Any]:
    needed = DWORD(0)
    advapi32.GetTokenInformation(token, info_class, None, 0, ctypes.byref(needed))
    if needed.value == 0:
        _raise_last_error("GetTokenInformation(size)")
    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetTokenInformation(
        token,
        info_class,
        buffer,
        needed,
        ctypes.byref(needed),
    ):
        _raise_last_error("GetTokenInformation")
    return buffer


def _sid_to_string(sid: LPVOID) -> str:
    output = LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
        _raise_last_error("ConvertSidToStringSidW")
    try:
        if output.value is None:
            raise Win32PipeError(0, "ConvertSidToStringSidW returned no SID")
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


def _token_sid_and_session(token: HANDLE) -> tuple[str, str, int]:
    user_buffer = _get_token_information(token, TOKEN_USER)
    user = ctypes.cast(user_buffer, ctypes.POINTER(TokenUser)).contents
    session_buffer = _get_token_information(token, TOKEN_SESSION_ID)
    session_id = ctypes.cast(session_buffer, ctypes.POINTER(DWORD)).contents.value
    sid = _sid_to_string(user.User.Sid)
    sid_hash = hashlib.sha256(sid.encode("utf-8")).hexdigest()
    return sid, sid_hash, session_id


def current_process_identity() -> VerifiedClientIdentity:
    token = _open_process_token()
    try:
        _, sid_hash, session_id = _token_sid_and_session(token)
        return VerifiedClientIdentity(
            user_sid_hash=sid_hash,
            session_id=session_id,
            is_remote=False,
            transport="win32-process-token",
        )
    finally:
        _close_handle(token)


def current_user_policy() -> ClientAuthorizationPolicy:
    identity = current_process_identity()
    return ClientAuthorizationPolicy(
        expected_user_sid_hash=identity.user_sid_hash,
        expected_session_id=identity.session_id,
    )


def _current_raw_sid() -> str:
    token = _open_process_token()
    try:
        sid, _, _ = _token_sid_and_session(token)
        return sid
    finally:
        _close_handle(token)


def _client_identity_from_pipe(pipe: HANDLE) -> VerifiedClientIdentity:
    if not advapi32.ImpersonateNamedPipeClient(pipe):
        _raise_last_error("ImpersonateNamedPipeClient")
    token: HANDLE | None = None
    try:
        token = _open_thread_token()
        _, sid_hash, session_id = _token_sid_and_session(token)
        return VerifiedClientIdentity(
            user_sid_hash=sid_hash,
            session_id=session_id,
            is_remote=False,
            transport="win32-named-pipe",
        )
    finally:
        if token:
            _close_handle(token)
        advapi32.RevertToSelf()


def _pipe_path(pipe_name: str) -> str:
    return rf"\\.\pipe\{pipe_name}"


def make_pipe_name(*, installation_id: str, suffix: str | None = None) -> str:
    identity = current_process_identity()
    stable = hashlib.sha256(
        f"{identity.user_sid_hash}:{installation_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"MediaSyncHome-0B-{stable}-{suffix or uuid4().hex}"


@dataclass
class _SecurityDescriptor:
    attributes: SecurityAttributes
    descriptor: LPVOID

    @classmethod
    def for_current_user(cls) -> "_SecurityDescriptor":
        raw_sid = _current_raw_sid()
        sddl = f"D:P(A;;GA;;;{raw_sid})(A;;GA;;;SY)"
        descriptor = LPVOID()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            SECURITY_DESCRIPTOR_REVISION,
            ctypes.byref(descriptor),
            None,
        ):
            _raise_last_error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        attrs = SecurityAttributes()
        attrs.nLength = ctypes.sizeof(SecurityAttributes)
        attrs.lpSecurityDescriptor = descriptor
        attrs.bInheritHandle = False
        return cls(attributes=attrs, descriptor=descriptor)

    def close(self) -> None:
        if self.descriptor:
            kernel32.LocalFree(self.descriptor)
            self.descriptor = LPVOID()


def _new_overlapped() -> tuple[HANDLE, Overlapped]:
    event = _checked_handle(
        kernel32.CreateEventW(None, True, False, None),
        "CreateEventW",
    )
    operation = Overlapped()
    operation.hEvent = event
    return event, operation


def _remaining_timeout_ms(deadline: float, operation_name: str) -> int:
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        raise TimeoutError(f"{operation_name} timed out")
    return max(1, ceil(remaining_seconds * 1000))


def _complete_overlapped(
    handle: HANDLE,
    operation: Overlapped,
    *,
    timeout_ms: int,
    operation_name: str,
    cancellation: Event | None = None,
) -> int:
    if cancellation is None:
        wait_result = kernel32.WaitForSingleObject(operation.hEvent, timeout_ms)
    else:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if cancellation.is_set():
                _cancel_overlapped_for_background_request(
                    handle,
                    operation,
                    operation_name=operation_name,
                )
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                wait_result = WAIT_TIMEOUT
                break
            remaining_ms = max(1, ceil(remaining_seconds * 1000))
            wait_result = kernel32.WaitForSingleObject(
                operation.hEvent,
                min(CANCELLATION_POLL_MS, remaining_ms),
            )
            if wait_result != WAIT_TIMEOUT:
                break
            if time.monotonic() >= deadline:
                break
    if wait_result == WAIT_TIMEOUT:
        cancelled = kernel32.CancelIoEx(handle, ctypes.byref(operation))
        if not cancelled:
            code = ctypes.get_last_error()
            if code != ERROR_NOT_FOUND:
                raise Win32PipeError(
                    code,
                    f"CancelIoEx({operation_name}): {ctypes.FormatError(code)}",
                )
        else:
            kernel32.WaitForSingleObject(operation.hEvent, INFINITE)
        transferred = DWORD(0)
        if kernel32.GetOverlappedResult(
            handle,
            ctypes.byref(operation),
            ctypes.byref(transferred),
            False,
        ):
            if not cancelled:
                return int(transferred.value)
        else:
            code = ctypes.get_last_error()
            if code not in {ERROR_OPERATION_ABORTED, ERROR_BROKEN_PIPE, ERROR_NO_DATA}:
                raise Win32PipeError(
                    code,
                    f"GetOverlappedResult({operation_name}): {ctypes.FormatError(code)}",
                )
        raise TimeoutError(f"{operation_name} timed out")
    if wait_result != WAIT_OBJECT_0:
        raise Win32PipeError(
            int(wait_result),
            f"WaitForSingleObject({operation_name}) returned {wait_result}",
        )

    transferred = DWORD(0)
    if not kernel32.GetOverlappedResult(
        handle,
        ctypes.byref(operation),
        ctypes.byref(transferred),
        False,
    ):
        _raise_last_error(f"GetOverlappedResult({operation_name})")
    return int(transferred.value)


def _cancel_overlapped_for_background_request(
    handle: HANDLE,
    operation: Overlapped,
    *,
    operation_name: str,
) -> None:
    cancelled = kernel32.CancelIoEx(handle, ctypes.byref(operation))
    if not cancelled:
        code = ctypes.get_last_error()
        if code != ERROR_NOT_FOUND:
            raise Win32PipeError(
                code,
                f"CancelIoEx({operation_name}): {ctypes.FormatError(code)}",
            )
    wait_result = kernel32.WaitForSingleObject(operation.hEvent, INFINITE)
    if wait_result != WAIT_OBJECT_0:
        raise Win32PipeError(
            int(wait_result),
            f"WaitForSingleObject({operation_name}) returned {wait_result}",
        )
    transferred = DWORD(0)
    if not kernel32.GetOverlappedResult(
        handle,
        ctypes.byref(operation),
        ctypes.byref(transferred),
        False,
    ):
        code = ctypes.get_last_error()
        if code not in {
            ERROR_OPERATION_ABORTED,
            ERROR_BROKEN_PIPE,
            ERROR_NO_DATA,
        }:
            raise Win32PipeError(
                code,
                f"GetOverlappedResult({operation_name}): {ctypes.FormatError(code)}",
            )
    raise InterruptedError(f"{operation_name} cancelled")


def _read_chunk(
    handle: HANDLE,
    size: int,
    *,
    timeout_ms: int,
    operation_name: str,
    cancellation: Event | None = None,
) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    immediate_bytes = DWORD(0)
    event, operation = _new_overlapped()
    try:
        if not kernel32.ReadFile(
            handle,
            buffer,
            size,
            ctypes.byref(immediate_bytes),
            ctypes.byref(operation),
        ):
            code = ctypes.get_last_error()
            if code != ERROR_IO_PENDING:
                raise Win32PipeError(
                    code,
                    f"ReadFile({operation_name}): {ctypes.FormatError(code)}",
                )
        transferred = _complete_overlapped(
            handle,
            operation,
            timeout_ms=timeout_ms,
            operation_name=operation_name,
            cancellation=cancellation,
        )
        if transferred == 0:
            raise ConnectionError(f"pipe closed during {operation_name}")
        return buffer.raw[:transferred]
    finally:
        _close_handle(event)


def _read_exact(
    handle: HANDLE,
    size: int,
    *,
    timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
    operation_name: str = "pipe read",
    cancellation: Event | None = None,
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    deadline = time.monotonic() + timeout_ms / 1000
    while remaining:
        chunk_size = min(remaining, DEFAULT_BUFFER_SIZE)
        chunk = _read_chunk(
            handle,
            chunk_size,
            timeout_ms=_remaining_timeout_ms(deadline, operation_name),
            operation_name=operation_name,
            cancellation=cancellation,
        )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_chunk(
    handle: HANDLE,
    payload: bytes,
    *,
    timeout_ms: int,
    operation_name: str,
    cancellation: Event | None = None,
) -> int:
    immediate_bytes = DWORD(0)
    buffer = ctypes.create_string_buffer(payload)
    event, operation = _new_overlapped()
    try:
        if not kernel32.WriteFile(
            handle,
            buffer,
            len(payload),
            ctypes.byref(immediate_bytes),
            ctypes.byref(operation),
        ):
            code = ctypes.get_last_error()
            if code != ERROR_IO_PENDING:
                raise Win32PipeError(
                    code,
                    f"WriteFile({operation_name}): {ctypes.FormatError(code)}",
                )
        transferred = _complete_overlapped(
            handle,
            operation,
            timeout_ms=timeout_ms,
            operation_name=operation_name,
            cancellation=cancellation,
        )
        if transferred == 0:
            raise ConnectionError(f"pipe closed during {operation_name}")
        return transferred
    finally:
        _close_handle(event)


def _write_all(
    handle: HANDLE,
    payload: bytes,
    *,
    timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
    operation_name: str = "pipe write",
    cancellation: Event | None = None,
) -> None:
    offset = 0
    deadline = time.monotonic() + timeout_ms / 1000
    while offset < len(payload):
        chunk = payload[offset : offset + DEFAULT_BUFFER_SIZE]
        offset += _write_chunk(
            handle,
            chunk,
            timeout_ms=_remaining_timeout_ms(deadline, operation_name),
            operation_name=operation_name,
            cancellation=cancellation,
        )


def _read_message(
    handle: HANDLE,
    *,
    limit: int = MAX_FRAME_BYTES,
    timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
    cancellation: Event | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000
    header = _read_exact(
        handle,
        4,
        timeout_ms=_remaining_timeout_ms(deadline, "pipe frame header read"),
        operation_name="pipe frame header read",
        cancellation=cancellation,
    )
    (length,) = struct.unpack("<I", header)
    if length > limit:
        raise IpcProtocolError(f"frame exceeds limit: {length} > {limit}")
    return decode_frame(
        _read_exact(
            handle,
            length,
            timeout_ms=_remaining_timeout_ms(deadline, "pipe frame body read"),
            operation_name="pipe frame body read",
            cancellation=cancellation,
        ),
        limit=limit,
    )


def _write_message(
    handle: HANDLE,
    message: dict[str, Any],
    *,
    limit: int = MAX_FRAME_BYTES,
    timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
    cancellation: Event | None = None,
) -> None:
    payload = encode_frame(message, limit=limit)
    _write_all(
        handle,
        struct.pack("<I", len(payload)) + payload,
        timeout_ms=timeout_ms,
        operation_name="pipe frame write",
        cancellation=cancellation,
    )


def _connect_overlapped(handle: HANDLE) -> None:
    event, operation = _new_overlapped()
    try:
        if kernel32.ConnectNamedPipe(handle, ctypes.byref(operation)):
            return
        code = ctypes.get_last_error()
        if code == ERROR_PIPE_CONNECTED:
            return
        if code != ERROR_IO_PENDING:
            raise Win32PipeError(
                code,
                f"ConnectNamedPipe: {ctypes.FormatError(code)}",
            )
        _complete_overlapped(
            handle,
            operation,
            timeout_ms=INFINITE,
            operation_name="pipe client connection",
        )
    finally:
        _close_handle(event)


def _is_client_disconnect_error(exc: Win32PipeError) -> bool:
    return exc.errno in {
        ERROR_BROKEN_PIPE,
        ERROR_NO_DATA,
        ERROR_PIPE_NOT_CONNECTED,
    }


@dataclass
class Win32NamedPipeServer:
    pipe_name: str
    service: EngineHostIpcService = field(
        default_factory=lambda: EngineHostIpcService(current_user_policy())
    )
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS
    response_timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS
    ack_timeout_ms: int = DEFAULT_ACK_TIMEOUT_MS

    def __post_init__(self) -> None:
        if self.request_timeout_ms < 1:
            raise ValueError("request_timeout_ms must be positive")
        if self.response_timeout_ms < 1:
            raise ValueError("response_timeout_ms must be positive")
        if self.ack_timeout_ms < 1:
            raise ValueError("ack_timeout_ms must be positive")

    def serve_once(self) -> None:
        security = _SecurityDescriptor.for_current_user()
        pipe = HANDLE()
        try:
            pipe = _checked_handle(
                kernel32.CreateNamedPipeW(
                    _pipe_path(self.pipe_name),
                    PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                    PIPE_MODE,
                    1,
                    DEFAULT_BUFFER_SIZE,
                    DEFAULT_BUFFER_SIZE,
                    3000,
                    ctypes.byref(security.attributes),
                ),
                "CreateNamedPipeW",
            )
            _connect_overlapped(pipe)
            request: dict[str, Any] = {}
            try:
                request = _read_message(
                    pipe,
                    timeout_ms=self.request_timeout_ms,
                )
                identity = _client_identity_from_pipe(pipe)
                response = self._dispatch(request, identity)
            except (IpcProtocolError, KeyError, TypeError, ValueError):
                response = IpcResponse.rejected(
                    IpcReason.INVALID_FRAME,
                    request_id=optional_request_id_from_frame(request),
                )
            except (ConnectionError, TimeoutError):
                return
            except Win32PipeError as exc:
                if _is_client_disconnect_error(exc):
                    return
                raise
            try:
                _write_message(
                    pipe,
                    response.to_dict(),
                    limit=MAX_QUERY_RESPONSE_BYTES,
                    timeout_ms=self.response_timeout_ms,
                )
            except IpcProtocolError:
                _write_message(
                    pipe,
                    IpcResponse.rejected(
                        IpcReason.INVALID_FRAME,
                        request_id=response.request_id,
                    ).to_dict(),
                    limit=MAX_QUERY_RESPONSE_BYTES,
                    timeout_ms=self.response_timeout_ms,
                )
            except (ConnectionError, TimeoutError):
                return
            except Win32PipeError as exc:
                if _is_client_disconnect_error(exc):
                    return
                raise
            try:
                acknowledgment = _read_exact(
                    pipe,
                    len(RESPONSE_ACK),
                    timeout_ms=self.ack_timeout_ms,
                    operation_name="pipe response acknowledgment read",
                )
                if acknowledgment != RESPONSE_ACK:
                    return
            except (ConnectionError, TimeoutError):
                return
            except Win32PipeError as exc:
                if _is_client_disconnect_error(exc):
                    return
                raise
        finally:
            if pipe:
                kernel32.DisconnectNamedPipe(pipe)
                _close_handle(pipe)
            security.close()

    def _dispatch(
        self,
        request: dict[str, Any],
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        request_id = request_id_from_frame(request)
        return self._dispatch_request(request, identity).correlated(request_id)

    def _dispatch_request(
        self,
        request: dict[str, Any],
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        message_type = request.get("message_type")
        if message_type == "HANDSHAKE":
            return self.service.handshake(request, identity)
        if message_type == "QUERY_STATUS":
            return self.service.query_status(str(request["client_instance_id"]))
        if message_type == "QUERY_BACKUP_OVERVIEW":
            return self.service.query_backup_overview(
                str(request["client_instance_id"]),
                draft_id=_optional_query_str(request.get("draft_id")),
                limit=_optional_query_int(request.get("limit")),
                offset=_optional_query_int(request.get("offset")),
            )
        if message_type == "QUERY_BACKUP_JOB_DETAIL":
            return self.service.query_backup_job_detail(
                str(request["client_instance_id"]),
                job_id=str(request["job_id"]),
            )
        if message_type == "QUERY_ACTIVITY_OVERVIEW":
            return self.service.query_activity_overview(
                str(request["client_instance_id"]),
                job_id=_optional_query_str(request.get("job_id")),
                limit=_optional_query_int(request.get("limit")),
                offset=_optional_query_int(request.get("offset")),
            )
        if message_type == "QUERY_HISTORY_TIMELINE":
            return self.service.query_history_timeline(
                str(request["client_instance_id"]),
                activity_filter=_optional_query_str(request.get("activity_filter")),
                job_id=_optional_query_str(request.get("job_id")),
                limit=_optional_query_int(request.get("limit")),
                after=_optional_query_object(request.get("after")),
                offset=_optional_query_int(request.get("offset")),
            )
        if message_type == "QUERY_RUN_PROGRESS":
            return self.service.query_run_progress(
                str(request["client_instance_id"]),
                run_id=str(request["run_id"]),
                after_sequence_no=_optional_query_int(request.get("after_sequence_no")),
            )
        if message_type == "QUERY_OPERATION_AUDIT":
            return self.service.query_operation_audit(
                str(request["client_instance_id"]),
                run_id=str(request["run_id"]),
                operation_id=str(request["operation_id"]),
                limit=_optional_query_int(request.get("limit")),
            )
        if message_type == "QUERY_PLAN_OPERATIONS":
            return self.service.query_plan_operations(
                str(request["client_instance_id"]),
                plan_id=str(request["plan_id"]),
                limit=_optional_query_int(request.get("limit")),
                after=_optional_query_object(request.get("after")),
                target_endpoint_id=_optional_query_str(
                    request.get("target_endpoint_id")
                ),
                risk_levels=_optional_query_str_tuple(request.get("risk_levels")),
            )
        if message_type == "QUERY_PLAN_ENDPOINTS":
            return self.service.query_plan_endpoints(
                str(request["client_instance_id"]),
                plan_id=str(request["plan_id"]),
                limit=_optional_query_int(request.get("limit")),
                after=_optional_query_object(request.get("after")),
            )
        if message_type == "QUERY_SNAPSHOT_ENTRIES":
            return self.service.query_snapshot_entries(
                str(request["client_instance_id"]),
                snapshot_id=str(request["snapshot_id"]),
                limit=_optional_query_int(request.get("limit")),
                after=_optional_query_object(request.get("after")),
            )
        if message_type == "QUERY_SNAPSHOT_COVERAGE":
            return self.service.query_snapshot_coverage(
                str(request["client_instance_id"]),
                snapshot_id=str(request["snapshot_id"]),
                limit=_optional_query_int(request.get("limit")),
                after=_optional_query_object(request.get("after")),
                coverage_states=_optional_query_str_tuple(request.get("coverage_states")),
            )
        if message_type == "QUERY_SNAPSHOT_ISSUES":
            return self.service.query_snapshot_issues(
                str(request["client_instance_id"]),
                snapshot_id=str(request["snapshot_id"]),
                limit=_optional_query_int(request.get("limit")),
                after=_optional_query_object(request.get("after")),
                blocking_only=_optional_query_bool(request.get("blocking_only")),
            )
        if message_type == "QUERY_CATALOGED_FILES":
            return self.service.query_cataloged_files(
                str(request["client_instance_id"]),
                run_id=_optional_query_str(request.get("run_id")),
                target_endpoint_id=_optional_query_str(request.get("target_endpoint_id")),
                limit=_optional_query_int(request.get("limit")),
                offset=_optional_query_int(request.get("offset")),
            )
        if message_type == "COMMAND":
            return self.service.submit_command_envelope(request)
        return IpcResponse.rejected(IpcReason.INVALID_FRAME)


@dataclass
class Win32NamedPipeClient:
    pipe_name: str
    role: ProcessRole = ProcessRole.GUI
    client_instance_id: str = field(default_factory=lambda: str(uuid4()))
    timeout_ms: int = 5000
    _background_cancellation: Event | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def bind_background_cancellation(self, cancellation: Event | None) -> None:
        self._background_cancellation = cancellation

    def connect(
        self,
        *,
        protocol_version: int = PROTOCOL_VERSION,
        schema_version: int = SCHEMA_VERSION,
        claimed_user_sid_hash: str | None = None,
    ) -> IpcResponse:
        request = HandshakeRequest(
            protocol_version=protocol_version,
            schema_version=schema_version,
            role=self.role,
            client_instance_id=self.client_instance_id,
            app_build="0B-dev",
            launch_nonce=str(uuid4()),
            claimed_user_sid_hash=claimed_user_sid_hash,
        ).to_dict()
        request["message_type"] = "HANDSHAKE"
        return self._roundtrip(request)

    def query_status(self) -> IpcResponse:
        return self._roundtrip(
            {
                "message_type": "QUERY_STATUS",
                "client_instance_id": self.client_instance_id,
            }
        )

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_BACKUP_OVERVIEW",
            "client_instance_id": self.client_instance_id,
        }
        if draft_id is not None:
            request["draft_id"] = draft_id
        if limit is not None:
            request["limit"] = limit
        if offset is not None:
            request["offset"] = offset
        return self._roundtrip(request)

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        return self._roundtrip(
            {
                "message_type": "QUERY_BACKUP_JOB_DETAIL",
                "client_instance_id": self.client_instance_id,
                "job_id": job_id,
            }
        )

    def query_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_ACTIVITY_OVERVIEW",
            "client_instance_id": self.client_instance_id,
        }
        if job_id is not None:
            request["job_id"] = job_id
        if limit is not None:
            request["limit"] = limit
        if offset is not None:
            request["offset"] = offset
        return self._roundtrip(request)

    def query_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_HISTORY_TIMELINE",
            "client_instance_id": self.client_instance_id,
        }
        if activity_filter is not None:
            request["activity_filter"] = activity_filter
        if job_id is not None:
            request["job_id"] = job_id
        if limit is not None:
            request["limit"] = limit
        if after is not None:
            request["after"] = after
        if offset is not None:
            request["offset"] = offset
        return self._roundtrip(request)

    def query_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_RUN_PROGRESS",
            "client_instance_id": self.client_instance_id,
            "run_id": run_id,
        }
        if after_sequence_no is not None:
            request["after_sequence_no"] = after_sequence_no
        return self._roundtrip(request)

    def query_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_OPERATION_AUDIT",
            "client_instance_id": self.client_instance_id,
            "run_id": run_id,
            "operation_id": operation_id,
        }
        if limit is not None:
            request["limit"] = limit
        return self._roundtrip(request)

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_PLAN_OPERATIONS",
            "client_instance_id": self.client_instance_id,
            "plan_id": plan_id,
        }
        if limit is not None:
            request["limit"] = limit
        if after is not None:
            request["after"] = after
        if target_endpoint_id is not None:
            request["target_endpoint_id"] = target_endpoint_id
        if risk_levels:
            request["risk_levels"] = list(risk_levels)
        return self._roundtrip(request)

    def query_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_PLAN_ENDPOINTS",
            "client_instance_id": self.client_instance_id,
            "plan_id": plan_id,
        }
        if limit is not None:
            request["limit"] = limit
        if after is not None:
            request["after"] = after
        return self._roundtrip(request)

    def query_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_SNAPSHOT_ENTRIES",
            "client_instance_id": self.client_instance_id,
            "snapshot_id": snapshot_id,
        }
        if limit is not None:
            request["limit"] = limit
        if after is not None:
            request["after"] = after
        return self._roundtrip(request)

    def query_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_SNAPSHOT_COVERAGE",
            "client_instance_id": self.client_instance_id,
            "snapshot_id": snapshot_id,
        }
        if limit is not None:
            request["limit"] = limit
        if after is not None:
            request["after"] = after
        if coverage_states:
            request["coverage_states"] = list(coverage_states)
        return self._roundtrip(request)

    def query_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_SNAPSHOT_ISSUES",
            "client_instance_id": self.client_instance_id,
            "snapshot_id": snapshot_id,
        }
        if limit is not None:
            request["limit"] = limit
        if after is not None:
            request["after"] = after
        if blocking_only:
            request["blocking_only"] = True
        return self._roundtrip(request)

    def query_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        request: dict[str, Any] = {
            "message_type": "QUERY_CATALOGED_FILES",
            "client_instance_id": self.client_instance_id,
        }
        if run_id is not None:
            request["run_id"] = run_id
        if target_endpoint_id is not None:
            request["target_endpoint_id"] = target_endpoint_id
        if limit is not None:
            request["limit"] = limit
        if offset is not None:
            request["offset"] = offset
        return self._roundtrip(request)

    def submit_command(
        self,
        command_name: str,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
        payload_hash: str | None = None,
    ) -> IpcResponse:
        command_payload = payload or {}
        if payload_hash is None:
            if command_payload:
                raise IpcProtocolError("payload_hash is required for non-empty command payloads")
            payload_hash = "6e46dd10defc9b56c29a6ec56b508c21f54c08192194e4df25bf36f0c9c3c279"
        envelope = IpcCommandEnvelope(
            protocol_version=PROTOCOL_VERSION,
            schema_version=COMMAND_SCHEMA_VERSION,
            request_id=request_id or str(uuid4()),
            client_instance_id=self.client_instance_id,
            idempotency_key=idempotency_key or str(uuid4()),
            command_name=command_name,
            payload=command_payload,
            payload_hash=payload_hash,
        )
        return self._roundtrip(envelope.to_dict())

    def _roundtrip(self, request: dict[str, Any]) -> IpcResponse:
        wire_request = dict(request)
        wire_request.setdefault("request_id", str(uuid4()))
        request_id = request_id_from_frame(wire_request)
        cancellation = self._background_cancellation
        if cancellation is not None and cancellation.is_set():
            raise InterruptedError("named-pipe request cancelled before open")
        handle = self._open(cancellation=cancellation)
        try:
            _write_message(
                handle,
                wire_request,
                timeout_ms=self.timeout_ms,
                cancellation=cancellation,
            )
            payload = _read_message(
                handle,
                limit=MAX_QUERY_RESPONSE_BYTES,
                timeout_ms=self.timeout_ms,
                cancellation=cancellation,
            )
            response = IpcResponse.from_dict(
                payload,
                expected_request_id=request_id,
                allow_uncorrelated_version_rejection=(
                    wire_request.get("message_type") == "HANDSHAKE"
                ),
            )
            _write_all(
                handle,
                RESPONSE_ACK,
                timeout_ms=self.timeout_ms,
                operation_name="pipe response acknowledgment write",
                cancellation=cancellation,
            )
        finally:
            _close_handle(handle)
        return response

    def _open(self, *, cancellation: Event | None = None) -> HANDLE:
        deadline = time.monotonic() + self.timeout_ms / 1000
        path = _pipe_path(self.pipe_name)
        while True:
            if cancellation is not None and cancellation.is_set():
                raise InterruptedError("named-pipe request cancelled while opening")
            handle = kernel32.CreateFileW(
                path,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED,
                None,
            )
            value = _handle_value(handle)
            if value not in (0, INVALID_HANDLE_VALUE):
                opened = HANDLE(value)
                if cancellation is not None and cancellation.is_set():
                    _close_handle(opened)
                    raise InterruptedError(
                        "named-pipe request cancelled while opening"
                    )
                return opened
            code = ctypes.get_last_error()
            if code in {ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND}:
                kernel32.WaitNamedPipeW(path, 250)
            if cancellation is not None and cancellation.is_set():
                raise InterruptedError("named-pipe request cancelled while opening")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out opening pipe {self.pipe_name}; last_error={code}")
            time.sleep(0.02)


def _optional_query_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_query_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("query integer must not be a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("query integer must be an integer or string")


def _optional_query_object(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("query object must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("query object keys must be strings")
    return dict(value)


def _optional_query_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError("query string tuple must be a JSON array")
    if not isinstance(value, (list, tuple)):
        raise ValueError("query string tuple must be a JSON array")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("query string tuple values must be strings")
    return tuple(value)


def _optional_query_bool(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ValueError("query boolean must be a boolean")
