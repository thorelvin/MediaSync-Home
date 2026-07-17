from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
    IpcStatus,
    decode_frame,
    encode_frame,
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
SECURITY_DESCRIPTOR_REVISION = 1
DEFAULT_BUFFER_SIZE = 65_536


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


def _read_exact(handle: HANDLE, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk_size = min(remaining, DEFAULT_BUFFER_SIZE)
        buffer = ctypes.create_string_buffer(chunk_size)
        read = DWORD(0)
        if not kernel32.ReadFile(handle, buffer, chunk_size, ctypes.byref(read), None):
            _raise_last_error("ReadFile")
        if read.value == 0:
            raise ConnectionError("pipe closed while reading frame")
        chunks.append(buffer.raw[: read.value])
        remaining -= read.value
    return b"".join(chunks)


def _write_all(handle: HANDLE, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + DEFAULT_BUFFER_SIZE]
        written = DWORD(0)
        buffer = ctypes.create_string_buffer(chunk)
        if not kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            _raise_last_error("WriteFile")
        offset += written.value


def _read_message(handle: HANDLE) -> dict[str, Any]:
    header = _read_exact(handle, 4)
    (length,) = struct.unpack("<I", header)
    return decode_frame(_read_exact(handle, length))


def _write_message(handle: HANDLE, message: dict[str, Any]) -> None:
    payload = encode_frame(message)
    _write_all(handle, struct.pack("<I", len(payload)) + payload)


@dataclass
class Win32NamedPipeServer:
    pipe_name: str
    service: EngineHostIpcService = field(
        default_factory=lambda: EngineHostIpcService(current_user_policy())
    )

    def serve_once(self) -> None:
        security = _SecurityDescriptor.for_current_user()
        pipe = HANDLE()
        try:
            pipe = _checked_handle(
                kernel32.CreateNamedPipeW(
                    _pipe_path(self.pipe_name),
                    PIPE_ACCESS_DUPLEX,
                    PIPE_MODE,
                    1,
                    DEFAULT_BUFFER_SIZE,
                    DEFAULT_BUFFER_SIZE,
                    3000,
                    ctypes.byref(security.attributes),
                ),
                "CreateNamedPipeW",
            )
            ok = kernel32.ConnectNamedPipe(pipe, None)
            if not ok:
                code = ctypes.get_last_error()
                if code != ERROR_PIPE_CONNECTED:
                    raise Win32PipeError(code, f"ConnectNamedPipe: {ctypes.FormatError(code)}")
            try:
                request = _read_message(pipe)
                identity = _client_identity_from_pipe(pipe)
                response = self._dispatch(request, identity)
            except (IpcProtocolError, KeyError, TypeError, ValueError):
                response = IpcResponse.rejected(IpcReason.INVALID_FRAME)
            _write_message(pipe, response.to_dict())
        finally:
            if pipe:
                kernel32.FlushFileBuffers(pipe)
                kernel32.DisconnectNamedPipe(pipe)
                _close_handle(pipe)
            security.close()

    def _dispatch(
        self,
        request: dict[str, Any],
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        message_type = request.get("message_type")
        if message_type == "HANDSHAKE":
            return self.service.handshake(request, identity)
        if message_type == "QUERY_STATUS":
            return self.service.query_status(str(request["client_instance_id"]))
        if message_type == "COMMAND":
            return self.service.submit_command_envelope(request)
        return IpcResponse.rejected(IpcReason.INVALID_FRAME)


@dataclass
class Win32NamedPipeClient:
    pipe_name: str
    role: ProcessRole = ProcessRole.GUI
    client_instance_id: str = field(default_factory=lambda: str(uuid4()))
    timeout_ms: int = 5000

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

    def submit_command(
        self,
        command_name: str,
        *,
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
            schema_version=SCHEMA_VERSION,
            request_id=str(uuid4()),
            client_instance_id=self.client_instance_id,
            idempotency_key=str(uuid4()),
            command_name=command_name,
            payload=command_payload,
            payload_hash=payload_hash,
        )
        return self._roundtrip(envelope.to_dict())

    def _roundtrip(self, request: dict[str, Any]) -> IpcResponse:
        handle = self._open()
        try:
            _write_message(handle, request)
            payload = _read_message(handle)
        finally:
            _close_handle(handle)
        status = payload.get("status")
        reason = payload.get("reason")
        response_payload = payload.get("payload")
        if not isinstance(response_payload, dict):
            raise IpcProtocolError("pipe response payload must be an object")
        return IpcResponse(
            status=IpcStatus(status),
            reason=None if reason is None else IpcReason(reason),
            payload=response_payload,
        )

    def _open(self) -> HANDLE:
        deadline = time.monotonic() + self.timeout_ms / 1000
        path = _pipe_path(self.pipe_name)
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
            if code in {ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND}:
                kernel32.WaitNamedPipeW(path, 250)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out opening pipe {self.pipe_name}; last_error={code}")
            time.sleep(0.02)
