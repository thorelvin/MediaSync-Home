from __future__ import annotations

import ctypes
import json
import os
from contextlib import suppress
from collections.abc import Mapping
from ctypes import wintypes
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mediasync_home.application.host_locator import (
    LOCAL_ENGINE_HOST_PUBLICATION_FILENAME,
    HostLocatorViolation,
    LocalEngineHostDescriptor,
    LocalEngineHostPublication,
    build_local_engine_host_descriptor,
    local_engine_host_publication_matches_descriptor,
    local_engine_host_publication_from_payload,
    validate_installation_id,
)
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseGuardError


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_INVALID_PARAMETER = 87


class ProcessLivenessProbe(Protocol):
    def is_process_running(self, process_id: int) -> bool | None: ...


class LocalProcessLivenessProbe:
    def is_process_running(self, process_id: int) -> bool | None:
        if os.name != "nt":
            return None
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                process_id,
            )
            if not handle:
                return False if ctypes.get_last_error() == ERROR_INVALID_PARAMETER else None
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return None
                return int(exit_code.value) == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return None


def build_local_engine_host_descriptor_for_user(
    *,
    installation_id: str,
    user_scope_hash: str,
    state_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LocalEngineHostDescriptor:
    return build_local_engine_host_descriptor(
        installation_id=installation_id,
        user_scope_hash=user_scope_hash,
        state_root=state_root
        if state_root is not None
        else default_local_preview_state_root(installation_id, environ=environ),
    )


def default_local_preview_state_root(
    installation_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    validate_installation_id(installation_id)
    env = environ or os.environ
    base = env.get("LOCALAPPDATA")
    if base is None or not base.strip():
        base_path = Path.home() / "AppData" / "Local"
    else:
        base_path = Path(base)
    return (
        base_path.expanduser().resolve()
        / "MediaSyncHome"
        / "0b-local-preview"
        / installation_id
    )


def local_engine_host_publication_path(state_root: Path) -> Path:
    return state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME


def publish_local_engine_host_publication(
    publication: LocalEngineHostPublication,
) -> Path:
    path = local_engine_host_publication_path(publication.state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_publication_path_safe(publication.state_root, allow_missing_publication=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temp_created = False
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            temp_created = True
            handle.write(
                json.dumps(publication.to_payload(), sort_keys=True, separators=(",", ":"))
            )
        temp_path.replace(path)
        temp_created = False
    finally:
        if temp_created:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    return path


def load_local_engine_host_publication(
    state_root: Path,
) -> LocalEngineHostPublication | None:
    path = local_engine_host_publication_path(state_root)
    _ensure_publication_path_safe(state_root, allow_missing_publication=True)
    if not path.is_file():
        return None
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise HostLocatorViolation("HOST_LOCATOR_PUBLICATION_NOT_OBJECT")
    return local_engine_host_publication_from_payload(
        {str(key): value for key, value in raw_payload.items()}
    )


def load_matching_live_local_engine_host_publication(
    descriptor: LocalEngineHostDescriptor,
    *,
    process_probe: ProcessLivenessProbe | None = None,
) -> LocalEngineHostPublication | None:
    if descriptor.state_root is None:
        return None
    publication = load_local_engine_host_publication(descriptor.state_root)
    if publication is None:
        return None
    if not local_engine_host_publication_matches_descriptor(publication, descriptor):
        return None
    probe = process_probe or LocalProcessLivenessProbe()
    if probe.is_process_running(publication.process_id) is False:
        with suppress(OSError):
            clear_stale_local_engine_host_publication(publication)
        return None
    return publication


def clear_stale_local_engine_host_publication(
    publication: LocalEngineHostPublication,
) -> bool:
    path = local_engine_host_publication_path(publication.state_root)
    try:
        current = load_local_engine_host_publication(publication.state_root)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if current != publication:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _ensure_publication_path_safe(
    state_root: Path,
    *,
    allow_missing_publication: bool,
) -> None:
    try:
        LocalReparseGuard().reject_reparse_chain(
            root=state_root,
            relative_parts=(LOCAL_ENGINE_HOST_PUBLICATION_FILENAME,),
            missing_code="HOST_LOCATOR_PUBLICATION_MISSING",
            missing_next_action="Start a local-preview Engine Host before adopting its locator.",
            reparse_code="HOST_LOCATOR_PUBLICATION_REPARSE_UNSUPPORTED",
            reparse_next_action="Remove the reparse-point HostLocator control path before retrying.",
            allow_missing_suffix=allow_missing_publication,
        )
    except ReparseGuardError as exc:
        if allow_missing_publication and exc.validation_code == "HOST_LOCATOR_PUBLICATION_MISSING":
            return
        raise HostLocatorViolation(exc.validation_code) from exc
