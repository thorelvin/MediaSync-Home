from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID

from mediasync_home.domain.process_roles import ProcessRole


PROTOCOL_VERSION = 1
SCHEMA_VERSION = 2
COMMAND_SCHEMA_VERSION = 1
MAX_FRAME_BYTES = 1_048_576
MAX_QUERY_RESPONSE_BYTES = 4_194_304
MAX_PROGRESS_EVENT_BYTES = 65_536
COMMAND_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
HEX_256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class IpcStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class IpcReason(str, Enum):
    CLIENT_IDENTITY_MISMATCH = "CLIENT_IDENTITY_MISMATCH"
    COMMAND_DISPATCHER_NOT_CONFIGURED = "COMMAND_DISPATCHER_NOT_CONFIGURED"
    COMMAND_IDEMPOTENCY_CONFLICT = "COMMAND_IDEMPOTENCY_CONFLICT"
    COMMAND_PRECONDITION_FAILED = "COMMAND_PRECONDITION_FAILED"
    ENGINE_HOST_UNAVAILABLE = "ENGINE_HOST_UNAVAILABLE"
    HANDSHAKE_REQUIRED = "HANDSHAKE_REQUIRED"
    INVALID_FRAME = "INVALID_FRAME"
    IPC_RATE_LIMITED = "IPC_RATE_LIMITED"
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
class IpcCommandEnvelope:
    protocol_version: int
    schema_version: int
    request_id: str
    client_instance_id: str
    idempotency_key: str
    command_name: str
    payload: dict[str, Any]
    payload_hash: str
    expected_entity_revision: int | None = None
    payload_hash_scope: str = "PAYLOAD_ONLY"
    payload_canonicalization_algorithm: str = "JCS-RFC8785"
    payload_hash_algorithm: str = "BLAKE3-256"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IpcCommandEnvelope":
        required = {
            "protocol_version",
            "schema_version",
            "message_type",
            "request_id",
            "client_instance_id",
            "idempotency_key",
            "command_name",
            "payload",
            "payload_hash_scope",
            "payload_canonicalization_algorithm",
            "payload_hash_algorithm",
            "payload_hash",
        }
        missing = required - set(payload)
        if missing:
            raise IpcProtocolError(f"command missing fields: {sorted(missing)}")
        if payload["message_type"] != "COMMAND":
            raise IpcProtocolError("command message_type must be COMMAND")

        request_id = _uuid_string(payload["request_id"], "request_id")
        client_instance_id = _uuid_string(payload["client_instance_id"], "client_instance_id")
        idempotency_key = _uuid_string(payload["idempotency_key"], "idempotency_key")
        command_name = str(payload["command_name"])
        if COMMAND_NAME_PATTERN.fullmatch(command_name) is None:
            raise IpcProtocolError("command_name is invalid")
        command_payload = payload["payload"]
        if not isinstance(command_payload, dict):
            raise IpcProtocolError("command payload must be an object")

        expected_entity_revision = payload.get("expected_entity_revision")
        if expected_entity_revision is not None:
            expected_entity_revision = int(expected_entity_revision)
            if expected_entity_revision < 0:
                raise IpcProtocolError("expected_entity_revision must be non-negative")

        payload_hash_scope = str(payload["payload_hash_scope"])
        payload_canonicalization_algorithm = str(payload["payload_canonicalization_algorithm"])
        payload_hash_algorithm = str(payload["payload_hash_algorithm"])
        payload_hash = str(payload["payload_hash"])
        if payload_hash_scope != "PAYLOAD_ONLY":
            raise IpcProtocolError("payload_hash_scope must be PAYLOAD_ONLY")
        if payload_canonicalization_algorithm != "JCS-RFC8785":
            raise IpcProtocolError("payload_canonicalization_algorithm must be JCS-RFC8785")
        if payload_hash_algorithm != "BLAKE3-256":
            raise IpcProtocolError("payload_hash_algorithm must be BLAKE3-256")
        if HEX_256_PATTERN.fullmatch(payload_hash) is None:
            raise IpcProtocolError("payload_hash must be 64 lowercase hex characters")

        return cls(
            protocol_version=int(payload["protocol_version"]),
            schema_version=int(payload["schema_version"]),
            request_id=request_id,
            client_instance_id=client_instance_id,
            idempotency_key=idempotency_key,
            command_name=command_name,
            payload=command_payload,
            expected_entity_revision=expected_entity_revision,
            payload_hash_scope=payload_hash_scope,
            payload_canonicalization_algorithm=payload_canonicalization_algorithm,
            payload_hash_algorithm=payload_hash_algorithm,
            payload_hash=payload_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "schema_version": self.schema_version,
            "message_type": "COMMAND",
            "request_id": self.request_id,
            "client_instance_id": self.client_instance_id,
            "idempotency_key": self.idempotency_key,
            "command_name": self.command_name,
            "payload": self.payload,
            "payload_hash_scope": self.payload_hash_scope,
            "payload_canonicalization_algorithm": self.payload_canonicalization_algorithm,
            "payload_hash_algorithm": self.payload_hash_algorithm,
            "payload_hash": self.payload_hash,
        }
        if self.expected_entity_revision is not None:
            result["expected_entity_revision"] = self.expected_entity_revision
        return result


@dataclass(frozen=True)
class IpcResponse:
    status: IpcStatus
    reason: IpcReason | None
    payload: dict[str, Any]
    request_id: str | None = None

    @classmethod
    def accepted(
        cls,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> "IpcResponse":
        return cls(
            status=IpcStatus.ACCEPTED,
            reason=None,
            payload=payload,
            request_id=_optional_uuid_string(request_id, "request_id"),
        )

    @classmethod
    def rejected(
        cls,
        reason: IpcReason,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> "IpcResponse":
        return cls(
            status=IpcStatus.REJECTED,
            reason=reason,
            payload=payload or {},
            request_id=_optional_uuid_string(request_id, "request_id"),
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        expected_request_id: str | None = None,
        allow_uncorrelated_version_rejection: bool = False,
    ) -> "IpcResponse":
        response_payload = payload.get("payload")
        if not isinstance(response_payload, dict):
            raise IpcProtocolError("IPC response payload must be an object")
        try:
            status = IpcStatus(payload["status"])
            raw_reason = payload["reason"]
            reason = None if raw_reason is None else IpcReason(raw_reason)
        except (KeyError, TypeError, ValueError) as exc:
            raise IpcProtocolError("IPC response status or reason is invalid") from exc
        if status is IpcStatus.ACCEPTED and reason is not None:
            raise IpcProtocolError("accepted IPC response must not include a reason")
        if status is IpcStatus.REJECTED and reason is None:
            raise IpcProtocolError("rejected IPC response must include a reason")

        request_id = _optional_uuid_string(payload.get("request_id"), "request_id")
        if expected_request_id is not None:
            expected = _uuid_string(expected_request_id, "expected_request_id")
            if request_id is None:
                if not (
                    allow_uncorrelated_version_rejection
                    and status is IpcStatus.REJECTED
                    and reason in {IpcReason.PROTOCOL_MISMATCH, IpcReason.SCHEMA_MISMATCH}
                ):
                    raise IpcProtocolError("IPC response request_id is missing")
            if request_id != expected:
                if request_id is not None:
                    raise IpcProtocolError("IPC response request_id mismatch")
        return cls(
            status=status,
            reason=reason,
            payload=response_payload,
            request_id=request_id,
        )

    def correlated(self, request_id: str) -> "IpcResponse":
        normalized = _uuid_string(request_id, "request_id")
        if self.request_id is not None and self.request_id != normalized:
            raise IpcProtocolError("IPC response request_id mismatch")
        return IpcResponse(
            status=self.status,
            reason=self.reason,
            payload=self.payload,
            request_id=normalized,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "status": self.status.value,
            "reason": None if self.reason is None else self.reason.value,
            "payload": self.payload,
        }
        if self.request_id is not None:
            result["request_id"] = self.request_id
        return result


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


def request_id_from_frame(payload: dict[str, Any]) -> str:
    if "request_id" not in payload:
        raise IpcProtocolError("IPC request_id is missing")
    return _uuid_string(payload["request_id"], "request_id")


def optional_request_id_from_frame(payload: dict[str, Any]) -> str | None:
    try:
        return request_id_from_frame(payload)
    except (IpcProtocolError, TypeError, ValueError):
        return None


def _uuid_string(value: object, field_name: str) -> str:
    try:
        parsed = UUID(str(value))
    except ValueError as exc:
        raise IpcProtocolError(f"{field_name} must be a UUID") from exc
    return str(parsed)


def _optional_uuid_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _uuid_string(value, field_name)
