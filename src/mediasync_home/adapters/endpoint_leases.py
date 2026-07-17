from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, SupportsInt, cast
from uuid import uuid4

from mediasync_home.application.runs import (
    EndpointLeaseAttempt,
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
)


class EndpointLeaseAdapterError(RuntimeError):
    pass


class EndpointLeaseUnavailable(EndpointLeaseAdapterError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class FencingTokenAllocationError(EndpointLeaseAdapterError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class FencingTokenStore(Protocol):
    def allocate_next_fencing_token(self, *, resource_key: str, ownership_epoch: int) -> int: ...


class EndpointLockHandle(Protocol):
    path: Path

    def close(self) -> None: ...


class EndpointLockOpener(Protocol):
    def acquire_exclusive_lock(self, lock_path: Path) -> EndpointLockHandle: ...


@dataclass
class LocalEndpointLease:
    lease_id: str
    owner_installation_id: str
    ownership_epoch: int
    fencing_token: int
    resource_key: str
    lock_path: Path
    _lock_handle: EndpointLockHandle = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._lock_handle.close()
        self._released = True


class LocalEndpointLeaseAuthority(EndpointLeaseAuthority):
    def __init__(
        self,
        *,
        target_roots: Mapping[str, Path],
        token_store: FencingTokenStore,
        lock_opener: EndpointLockOpener | None = None,
    ) -> None:
        self._target_roots = {resource_key: Path(root) for resource_key, root in target_roots.items()}
        self._token_store = token_store
        self._lock_opener = lock_opener or Win32EndpointLockOpener()

    def acquire_endpoint_lease(self, request: EndpointLeaseRequest) -> EndpointLeaseAttempt:
        root = self._target_roots.get(request.resource_key)
        if root is None:
            return _failed(
                "ENDPOINT_LEASE_RESOURCE_UNKNOWN",
                "Register the target endpoint root before acquiring its mutation lock.",
            )

        control_dir = root / ".mediasync"
        lock_dir = control_dir / "locks"
        marker_path = control_dir / "endpoint.json"
        if not control_dir.is_dir():
            return _failed(
                "ENDPOINT_CONTROL_AREA_MISSING",
                "Classify or adopt the endpoint control area before acquiring a lease.",
            )
        if not lock_dir.is_dir():
            return _failed(
                "ENDPOINT_LOCK_DIRECTORY_MISSING",
                "Create the endpoint lock directory during controlled endpoint adoption.",
            )
        if not marker_path.is_file():
            return _failed(
                "ENDPOINT_MARKER_MISSING",
                "Write and verify the endpoint marker before acquiring a lease.",
            )

        lock_path = lock_dir / "mutation.lock"
        try:
            lock_handle = self._lock_opener.acquire_exclusive_lock(lock_path)
        except EndpointLeaseUnavailable as exc:
            return _failed(exc.validation_code, exc.next_action)

        try:
            marker = _read_endpoint_marker(marker_path)
            owner_installation_id, ownership_epoch = _validate_marker(marker, request)
            fencing_token = self._token_store.allocate_next_fencing_token(
                resource_key=request.resource_key,
                ownership_epoch=ownership_epoch,
            )
            if fencing_token < 1:
                raise FencingTokenAllocationError(
                    "ENDPOINT_FENCING_TOKEN_INVALID",
                    "Allocate a positive monotonic fencing token before issuing a lease.",
                )
        except EndpointLeaseUnavailable as exc:
            lock_handle.close()
            return _failed(exc.validation_code, exc.next_action)
        except FencingTokenAllocationError as exc:
            lock_handle.close()
            return _failed(exc.validation_code, exc.next_action)

        lease = LocalEndpointLease(
            lease_id=str(uuid4()),
            owner_installation_id=owner_installation_id,
            ownership_epoch=ownership_epoch,
            fencing_token=fencing_token,
            resource_key=request.resource_key,
            lock_path=lock_path,
            _lock_handle=lock_handle,
        )
        return EndpointLeaseAttempt(
            acquired=True,
            lease=lease,
            validation_codes=(),
            next_action="Endpoint mutation lock is held by a live OS handle.",
        )


@dataclass
class Win32EndpointLockHandle:
    path: Path
    handle_value: int
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        kernel32 = _kernel32()
        kernel32.CloseHandle(wintypes.HANDLE(self.handle_value))
        self._closed = True


class Win32EndpointLockOpener(EndpointLockOpener):
    def acquire_exclusive_lock(self, lock_path: Path) -> EndpointLockHandle:
        if os.name != "nt":
            raise EndpointLeaseUnavailable(
                "ENDPOINT_LEASE_REQUIRES_WINDOWS",
                "Use the Windows endpoint lock adapter only on Windows hosts.",
            )
        kernel32 = _kernel32()
        handle = kernel32.CreateFileW(
            str(lock_path),
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        value = _handle_value(handle)
        if value in {0, INVALID_HANDLE_VALUE}:
            error_code = ctypes.get_last_error()
            if error_code in {ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION}:
                raise EndpointLeaseUnavailable(
                    "ENDPOINT_LEASE_UNAVAILABLE",
                    "Wait for the current endpoint writer to release the mutation lock.",
                )
            raise EndpointLeaseUnavailable(
                "ENDPOINT_LEASE_OPEN_FAILED",
                f"Inspect endpoint control permissions before retrying; win32_error={error_code}.",
            )
        return Win32EndpointLockHandle(path=lock_path, handle_value=value)


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x00000080
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
INVALID_HANDLE_VALUE = int(wintypes.HANDLE(-1).value or -1)


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


def _read_endpoint_marker(marker_path: Path) -> dict[str, object]:
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_MARKER_INVALID",
            "Repair or re-adopt the endpoint marker before acquiring a lease.",
        ) from exc
    if not isinstance(data, dict):
        raise EndpointLeaseUnavailable(
            "ENDPOINT_MARKER_INVALID",
            "Repair or re-adopt the endpoint marker before acquiring a lease.",
        )
    return cast(dict[str, object], data)


def _validate_marker(
    marker: Mapping[str, object],
    request: EndpointLeaseRequest,
) -> tuple[str, int]:
    endpoint_id = marker.get("endpoint_id")
    owner_installation_id = marker.get("owner_installation_id")
    ownership_epoch = marker.get("ownership_epoch")
    if (
        not isinstance(endpoint_id, str)
        or not isinstance(owner_installation_id, str)
        or not isinstance(ownership_epoch, int)
    ):
        raise EndpointLeaseUnavailable(
            "ENDPOINT_MARKER_INVALID",
            "Repair or re-adopt the endpoint marker before acquiring a lease.",
        )
    if endpoint_id != request.endpoint_id:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_ID_MISMATCH",
            "Refresh endpoint analysis because the control marker no longer matches the run target.",
        )
    if (
        request.required_owner_installation_id is not None
        and owner_installation_id != request.required_owner_installation_id
    ):
        raise EndpointLeaseUnavailable(
            "ENDPOINT_OWNER_MISMATCH",
            "Stop mutation and refresh ownership before writing to this endpoint.",
        )
    if request.required_ownership_epoch is not None and ownership_epoch != request.required_ownership_epoch:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_OWNERSHIP_EPOCH_MISMATCH",
            "Refresh the sealed plan because endpoint ownership changed.",
        )
    return owner_installation_id, ownership_epoch


def _failed(validation_code: str, next_action: str) -> EndpointLeaseAttempt:
    return EndpointLeaseAttempt(
        acquired=False,
        lease=None,
        validation_codes=(validation_code,),
        next_action=next_action,
    )
