from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
USER_SCOPE_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCAL_PREVIEW_SCOPE = "0B_SAME_USER_LOCAL_PREVIEW"
LOCAL_PREVIEW_PIPE_PREFIX = "MediaSyncHome-0B"


class HostLocatorViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LocalEngineHostDescriptor:
    installation_id: str
    user_scope_hash: str
    locator_key: str
    pipe_name: str
    mutex_name: str
    state_root: Path | None
    scope: str = LOCAL_PREVIEW_SCOPE

    def to_payload(self) -> dict[str, object]:
        return {
            "installation_id": self.installation_id,
            "locator_key": self.locator_key,
            "mutex_name": self.mutex_name,
            "pipe_name": self.pipe_name,
            "scope": self.scope,
            "state_root": None if self.state_root is None else str(self.state_root),
        }


def build_local_engine_host_descriptor(
    *,
    installation_id: str,
    user_scope_hash: str,
    state_root: Path | None = None,
) -> LocalEngineHostDescriptor:
    normalized_installation_id = _normalize_installation_id(installation_id)
    normalized_user_scope_hash = _normalize_user_scope_hash(user_scope_hash)
    if state_root is not None:
        _validate_state_root(state_root)

    locator_key = hashlib.sha256(
        f"{normalized_user_scope_hash}:{normalized_installation_id}".encode("utf-8")
    ).hexdigest()[:24]
    return LocalEngineHostDescriptor(
        installation_id=normalized_installation_id,
        user_scope_hash=normalized_user_scope_hash,
        locator_key=locator_key,
        pipe_name=f"{LOCAL_PREVIEW_PIPE_PREFIX}-{locator_key}",
        mutex_name=rf"Local\{LOCAL_PREVIEW_PIPE_PREFIX}-{locator_key}",
        state_root=state_root,
    )


def validate_installation_id(installation_id: str) -> None:
    _normalize_installation_id(installation_id)


def _normalize_installation_id(installation_id: str) -> str:
    if INSTALLATION_ID_PATTERN.fullmatch(installation_id) is None:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_INSTALLATION_ID")
    return installation_id


def _normalize_user_scope_hash(user_scope_hash: str) -> str:
    normalized = user_scope_hash.lower()
    if USER_SCOPE_HASH_PATTERN.fullmatch(normalized) is None:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_USER_SCOPE_HASH")
    return normalized


def _validate_state_root(state_root: Path) -> None:
    if not state_root.is_absolute():
        raise HostLocatorViolation("HOST_LOCATOR_STATE_ROOT_MUST_BE_ABSOLUTE")
    if _is_unc_path(state_root):
        raise HostLocatorViolation("HOST_LOCATOR_STATE_ROOT_MUST_BE_LOCAL")


def _is_unc_path(path: Path) -> bool:
    anchor = path.anchor
    return anchor.startswith("\\\\") or anchor.startswith("//")
