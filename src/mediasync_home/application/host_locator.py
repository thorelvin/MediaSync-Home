from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
USER_SCOPE_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCAL_PREVIEW_SCOPE = "0B_SAME_USER_LOCAL_PREVIEW"
LOCAL_PREVIEW_PIPE_PREFIX = "MediaSyncHome-0B"
LOCAL_PREVIEW_PIPE_PATTERN = re.compile(r"^MediaSyncHome-0B-([0-9a-f]{24})$")
LOCAL_PREVIEW_MUTEX_PATTERN = re.compile(r"^Local\\MediaSyncHome-0B-([0-9a-f]{24})$")
LOCAL_ENGINE_HOST_PUBLICATION_FILENAME = "engine-host.locator.json"
HOST_LOCATOR_HEARTBEAT_FUTURE_TOLERANCE_SECONDS = 5.0


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


@dataclass(frozen=True, slots=True)
class LocalEngineHostPublication:
    installation_id: str
    locator_key: str
    pipe_name: str
    mutex_name: str
    state_root: Path
    process_id: int
    heartbeat_utc: str | None = None
    scope: str = LOCAL_PREVIEW_SCOPE
    status: str = "STARTING"
    schema_version: int = 1

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "installation_id": self.installation_id,
            "locator_key": self.locator_key,
            "mutex_name": self.mutex_name,
            "pipe_name": self.pipe_name,
            "process_id": self.process_id,
            "schema_version": self.schema_version,
            "scope": self.scope,
            "state_root": str(self.state_root),
            "status": self.status,
        }
        if self.heartbeat_utc is not None:
            payload["heartbeat_utc"] = self.heartbeat_utc
        return payload


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


def build_local_engine_host_publication(
    *,
    installation_id: str,
    pipe_name: str,
    mutex_name: str,
    state_root: Path,
    process_id: int,
    heartbeat_utc: str | None = None,
) -> LocalEngineHostPublication:
    normalized_installation_id = _normalize_installation_id(installation_id)
    _validate_state_root(state_root)
    locator_key = _locator_key_from_pipe_name(pipe_name)
    mutex_locator_key = _locator_key_from_mutex_name(mutex_name)
    if locator_key != mutex_locator_key:
        raise HostLocatorViolation("HOST_LOCATOR_PIPE_MUTEX_MISMATCH")
    normalized_process_id = _normalize_process_id(process_id)
    return LocalEngineHostPublication(
        installation_id=normalized_installation_id,
        locator_key=locator_key,
        pipe_name=pipe_name,
        mutex_name=mutex_name,
        state_root=state_root,
        process_id=normalized_process_id,
        heartbeat_utc=_normalize_optional_utc_timestamp(heartbeat_utc),
    )


def local_engine_host_publication_from_payload(
    payload: Mapping[str, object],
) -> LocalEngineHostPublication:
    if payload.get("schema_version") != 1:
        raise HostLocatorViolation("HOST_LOCATOR_PUBLICATION_SCHEMA_UNSUPPORTED")
    if payload.get("scope") != LOCAL_PREVIEW_SCOPE:
        raise HostLocatorViolation("HOST_LOCATOR_PUBLICATION_SCOPE_UNSUPPORTED")
    if payload.get("status") != "STARTING":
        raise HostLocatorViolation("HOST_LOCATOR_PUBLICATION_STATUS_UNSUPPORTED")

    publication = build_local_engine_host_publication(
        installation_id=_require_string(payload.get("installation_id"), "INSTALLATION_ID"),
        pipe_name=_require_string(payload.get("pipe_name"), "PIPE_NAME"),
        mutex_name=_require_string(payload.get("mutex_name"), "MUTEX_NAME"),
        state_root=Path(_require_string(payload.get("state_root"), "STATE_ROOT")),
        process_id=_normalize_process_id(payload.get("process_id")),
        heartbeat_utc=_normalize_optional_utc_timestamp(payload.get("heartbeat_utc")),
    )
    if payload.get("locator_key") != publication.locator_key:
        raise HostLocatorViolation("HOST_LOCATOR_PUBLICATION_LOCATOR_KEY_MISMATCH")
    return publication


def local_engine_host_publication_matches_descriptor(
    publication: LocalEngineHostPublication,
    descriptor: LocalEngineHostDescriptor,
) -> bool:
    return (
        descriptor.state_root is not None
        and publication.installation_id == descriptor.installation_id
        and publication.locator_key == descriptor.locator_key
        and publication.pipe_name == descriptor.pipe_name
        and publication.mutex_name == descriptor.mutex_name
        and publication.state_root == descriptor.state_root
        and publication.scope == descriptor.scope
    )


def format_host_locator_heartbeat_utc(value: datetime) -> str:
    return _format_utc_timestamp(_normalize_aware_datetime(value, "HEARTBEAT_UTC"))


def parse_host_locator_heartbeat_utc(value: str) -> datetime:
    return _parse_utc_timestamp(value, "HEARTBEAT_UTC")


def local_engine_host_publication_heartbeat_is_stale(
    publication: LocalEngineHostPublication,
    *,
    now_utc: datetime,
    max_age_seconds: float,
    future_tolerance_seconds: float = HOST_LOCATOR_HEARTBEAT_FUTURE_TOLERANCE_SECONDS,
) -> bool:
    if publication.heartbeat_utc is None:
        return False
    if max_age_seconds <= 0:
        raise HostLocatorViolation("HOST_LOCATOR_HEARTBEAT_MAX_AGE_INVALID")
    if future_tolerance_seconds < 0:
        raise HostLocatorViolation("HOST_LOCATOR_HEARTBEAT_FUTURE_TOLERANCE_INVALID")

    now = _normalize_aware_datetime(now_utc, "HEARTBEAT_NOW_UTC")
    heartbeat = parse_host_locator_heartbeat_utc(publication.heartbeat_utc)
    age = now - heartbeat
    if age < -timedelta(seconds=future_tolerance_seconds):
        return True
    return age > timedelta(seconds=max_age_seconds)


def validate_installation_id(installation_id: str) -> None:
    _normalize_installation_id(installation_id)


def validate_local_preview_pipe_name(pipe_name: str) -> None:
    _locator_key_from_pipe_name(pipe_name)


def validate_local_preview_mutex_name(mutex_name: str) -> None:
    _locator_key_from_mutex_name(mutex_name)


def _normalize_installation_id(installation_id: str) -> str:
    if INSTALLATION_ID_PATTERN.fullmatch(installation_id) is None:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_INSTALLATION_ID")
    return installation_id


def _normalize_user_scope_hash(user_scope_hash: str) -> str:
    normalized = user_scope_hash.lower()
    if USER_SCOPE_HASH_PATTERN.fullmatch(normalized) is None:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_USER_SCOPE_HASH")
    return normalized


def _normalize_process_id(process_id: object) -> int:
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id < 1:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_PROCESS_ID")
    return process_id


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise HostLocatorViolation(f"HOST_LOCATOR_PUBLICATION_INVALID_{field_name}")
    return value


def _normalize_optional_utc_timestamp(value: object) -> str | None:
    if value is None:
        return None
    return _format_utc_timestamp(_parse_utc_timestamp(value, "HEARTBEAT_UTC"))


def _parse_utc_timestamp(value: object, field_name: str) -> datetime:
    raw = _require_string(value, field_name)
    parseable = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise HostLocatorViolation(f"HOST_LOCATOR_PUBLICATION_INVALID_{field_name}") from exc
    return _normalize_aware_datetime(parsed, field_name)


def _normalize_aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HostLocatorViolation(f"HOST_LOCATOR_PUBLICATION_INVALID_{field_name}")
    return value.astimezone(timezone.utc)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _locator_key_from_pipe_name(pipe_name: str) -> str:
    match = LOCAL_PREVIEW_PIPE_PATTERN.fullmatch(pipe_name)
    if match is None:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_PIPE_NAME")
    return match.group(1)


def _locator_key_from_mutex_name(mutex_name: str) -> str:
    match = LOCAL_PREVIEW_MUTEX_PATTERN.fullmatch(mutex_name)
    if match is None:
        raise HostLocatorViolation("HOST_LOCATOR_INVALID_MUTEX_NAME")
    return match.group(1)


def _validate_state_root(state_root: Path) -> None:
    if not state_root.is_absolute():
        raise HostLocatorViolation("HOST_LOCATOR_STATE_ROOT_MUST_BE_ABSOLUTE")
    if _is_unc_path(state_root):
        raise HostLocatorViolation("HOST_LOCATOR_STATE_ROOT_MUST_BE_LOCAL")


def _is_unc_path(path: Path) -> bool:
    anchor = path.anchor
    return anchor.startswith("\\\\") or anchor.startswith("//")
