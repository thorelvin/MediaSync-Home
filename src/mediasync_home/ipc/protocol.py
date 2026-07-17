from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID

from mediasync_home.domain.process_roles import ProcessRole


PROTOCOL_VERSION = 1
SCHEMA_VERSION = 1
MAX_FRAME_BYTES = 1_048_576
MAX_QUERY_RESPONSE_BYTES = 4_194_304
MAX_PROGRESS_EVENT_BYTES = 65_536


class IpcStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class IpcReason(str, Enum):
    CLIENT_IDENTITY_MISMATCH = "CLIENT_IDENTITY_MISMATCH"
    HANDSHAKE_REQUIRED = "HANDSHAKE_REQUIRED"
    INVALID_FRAME = "INVALID_FRAME"
    MUTATING_COMMANDS_DISABLED = "MUTATING_COMMANDS_DISABLED"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    REMOTE_CLIENT_REJECTED = "REMOTE_CLIENT_REJECTED"
    ROLE_NOT_ALLOWED = "ROLE_NOT_ALLOWED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"


class IpcProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class HandshakeRequest:
    protocol_version: int
    schema_version: int
    role: ProcessRole
    client_instance_id: str
    app_build: str
    launch_nonce: str
    claimed_user_sid_hash: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HandshakeRequest":
        required = {
            "protocol_version",
            "schema_version",
            "role",
            "client_instance_id",
            "app_build",
            "launch_nonce",
        }
        missing = required - set(payload)
        if missing:
            raise IpcProtocolError(f"handshake missing fields: {sorted(missing)}")
        try:
            UUID(str(payload["client_instance_id"]))
        except ValueError as exc:
            raise IpcProtocolError("client_instance_id must be a UUID") from exc
        launch_nonce = str(payload["launch_nonce"])
        if not launch_nonce:
            raise IpcProtocolError("launch_nonce must be non-empty")
        try:
            role = ProcessRole(str(payload["role"]))
        except ValueError as exc:
            raise IpcProtocolError(f"unsupported role: {payload['role']!r}") from exc
        return cls(
            protocol_version=int(payload["protocol_version"]),
            schema_version=int(payload["schema_version"]),
            role=role,
            client_instance_id=str(payload["client_instance_id"]),
            app_build=str(payload["app_build"]),
            launch_nonce=launch_nonce,
            claimed_user_sid_hash=(
                None
                if payload.get("claimed_user_sid_hash") is None
                else str(payload["claimed_user_sid_hash"])
            ),
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "schema_version": self.schema_version,
            "role": self.role.value,
            "client_instance_id": self.client_instance_id,
            "app_build": self.app_build,
            "launch_nonce": self.launch_nonce,
        }
        if self.claimed_user_sid_hash is not None:
            result["claimed_user_sid_hash"] = self.claimed_user_sid_hash
        return result


@dataclass(frozen=True)
class IpcResponse:
    status: IpcStatus
    reason: IpcReason | None
    payload: dict[str, Any]

    @classmethod
    def accepted(cls, payload: dict[str, Any]) -> "IpcResponse":
        return cls(status=IpcStatus.ACCEPTED, reason=None, payload=payload)

    @classmethod
    def rejected(cls, reason: IpcReason, payload: dict[str, Any] | None = None) -> "IpcResponse":
        return cls(status=IpcStatus.REJECTED, reason=reason, payload=payload or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": None if self.reason is None else self.reason.value,
            "payload": self.payload,
        }


def encode_frame(payload: dict[str, Any], *, limit: int = MAX_FRAME_BYTES) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > limit:
        raise IpcProtocolError(f"frame exceeds limit: {len(encoded)} > {limit}")
    return encoded


def decode_frame(frame: bytes, *, limit: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    if len(frame) > limit:
        raise IpcProtocolError(f"frame exceeds limit: {len(frame)} > {limit}")
    try:
        payload = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IpcProtocolError(f"invalid JSON frame: {exc}") from exc
    if not isinstance(payload, dict):
        raise IpcProtocolError("IPC frame must be a JSON object")
    return payload
