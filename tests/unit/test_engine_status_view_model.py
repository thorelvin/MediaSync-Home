from __future__ import annotations

from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason, IpcResponse
from mediasync_home.presentation.view_models.engine_status import (
    EngineStatusViewState,
    engine_status_from_response,
    load_engine_status,
)


def test_engine_status_view_model_formats_accepted_host_status() -> None:
    response = IpcResponse.accepted(
        {"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()}
    )

    state = engine_status_from_response(response)

    assert state.connection_label == "Connected"
    assert state.state_label == "Ready"
    assert state.status_kind == "ready"
    assert state.protocol_label == "Protocol 1 / schema 2"
    assert state.mutation_label == "Read-only local preview"
    assert state.ready is True
    assert state.mutations_enabled is False


def test_engine_status_view_model_formats_rejected_response() -> None:
    response = IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)

    state = engine_status_from_response(response)

    assert state.connection_label == "Blocked"
    assert state.status_kind == "blocked"
    assert "handshake required" in state.detail


def test_load_engine_status_returns_disconnected_without_provider() -> None:
    state = load_engine_status(None)

    assert state == EngineStatusViewState.disconnected()


def test_load_engine_status_connects_before_status_query() -> None:
    provider = _FakeStatusProvider()

    state = load_engine_status(provider)

    assert provider.calls == ["connect", "get_status"]
    assert state.connection_label == "Connected"


class _FakeStatusProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def connect(self) -> IpcResponse:
        self.calls.append("connect")
        return IpcResponse.accepted(
            {"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()}
        )

    def get_status(self) -> IpcResponse:
        self.calls.append("get_status")
        return IpcResponse.accepted(
            {"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()}
        )
