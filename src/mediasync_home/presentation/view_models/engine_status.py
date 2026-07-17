from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.ipc.protocol import IpcReason, IpcResponse, IpcStatus


@dataclass(frozen=True)
class EngineStatusViewState:
    connection_label: str
    state_label: str
    detail: str
    scope_label: str
    protocol_label: str
    mutation_label: str
    status_kind: str
    ready: bool
    mutations_enabled: bool

    @classmethod
    def disconnected(cls, detail: str = "Engine Host is not connected yet.") -> "EngineStatusViewState":
        return cls(
            connection_label="Disconnected",
            state_label="Waiting",
            detail=detail,
            scope_label="Local preview",
            protocol_label="Protocol pending",
            mutation_label="Read-only local preview",
            status_kind="waiting",
            ready=False,
            mutations_enabled=False,
        )


class EngineStatusProvider(Protocol):
    def connect(self) -> IpcResponse:
        pass

    def get_status(self) -> IpcResponse:
        pass


def engine_status_from_response(response: IpcResponse | None) -> EngineStatusViewState:
    if response is None:
        return EngineStatusViewState.disconnected()

    if response.status is IpcStatus.REJECTED:
        reason = _format_reason(response.reason)
        return EngineStatusViewState(
            connection_label="Blocked",
            state_label="Rejected",
            detail=f"Engine Host rejected the status request: {reason}.",
            scope_label="Local preview",
            protocol_label="Protocol unavailable",
            mutation_label="Read-only local preview",
            status_kind="blocked",
            ready=False,
            mutations_enabled=False,
        )

    host_status = response.payload.get("host_status")
    if not isinstance(host_status, dict):
        return EngineStatusViewState(
            connection_label="Connected",
            state_label="Unknown",
            detail="Engine Host responded without a status payload.",
            scope_label="Local preview",
            protocol_label="Protocol unavailable",
            mutation_label="Read-only local preview",
            status_kind="warning",
            ready=False,
            mutations_enabled=False,
        )

    ready = bool(host_status.get("ready", False))
    mutations_enabled = bool(host_status.get("mutations_enabled", False))
    protocol_version = host_status.get("protocol_version", "?")
    schema_version = host_status.get("schema_version", "?")
    role = str(host_status.get("role", "engine-host"))

    return EngineStatusViewState(
        connection_label="Connected",
        state_label="Ready" if ready else "Starting",
        detail=f"{_format_role(role)} is reachable and reporting health.",
        scope_label=_format_scope(str(host_status.get("scope", "local preview"))),
        protocol_label=f"Protocol {protocol_version} / schema {schema_version}",
        mutation_label="Mutations enabled" if mutations_enabled else "Read-only local preview",
        status_kind="ready" if ready else "warning",
        ready=ready,
        mutations_enabled=mutations_enabled,
    )


def load_engine_status(provider: EngineStatusProvider | None) -> EngineStatusViewState:
    if provider is None:
        return EngineStatusViewState.disconnected()
    try:
        handshake = provider.connect()
        if handshake.status is IpcStatus.REJECTED:
            return engine_status_from_response(handshake)
        return engine_status_from_response(provider.get_status())
    except Exception:
        return EngineStatusViewState.disconnected("Engine status is unavailable.")


def _format_reason(reason: IpcReason | None) -> str:
    if reason is None:
        return "unknown reason"
    return reason.value.lower().replace("_", " ")


def _format_role(role: str) -> str:
    return role.replace("-", " ").title()


def _format_scope(scope: str) -> str:
    label = scope.replace("_", " ").lower()
    if label.startswith("0b "):
        label = "0B " + label[3:]
    return label
