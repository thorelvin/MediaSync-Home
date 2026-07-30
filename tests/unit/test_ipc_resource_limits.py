from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from mediasync_home.application.command_receipts import CommandReceipt
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService, IpcResourceLimits


EXPECTED_USER = "same-user-sid-hash"
EXPECTED_SESSION = 42


@dataclass
class _FakeMonotonicClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ReceiptStoreSpy:
    def __init__(self) -> None:
        self.recorded: list[CommandReceipt] = []

    def record_received(self, receipt: CommandReceipt) -> CommandReceipt:
        self.recorded.append(receipt)
        return receipt

    def load_command_receipt(self, idempotency_key: str) -> CommandReceipt | None:
        del idempotency_key
        return None

    def update_command_receipt(self, receipt: CommandReceipt) -> None:
        raise AssertionError(f"unexpected receipt update: {receipt.idempotency_key}")


def _service(
    *,
    clock: _FakeMonotonicClock,
    limits: IpcResourceLimits,
) -> EngineHostIpcService:
    return EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash=EXPECTED_USER,
            expected_session_id=EXPECTED_SESSION,
        ),
        resource_limits=limits,
        monotonic_clock=clock,
    )


def _client(service: EngineHostIpcService) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=service,
        identity=VerifiedClientIdentity(
            user_sid_hash=EXPECTED_USER,
            session_id=EXPECTED_SESSION,
            is_remote=False,
            transport="in-process-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id=str(uuid4()),
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_accepted_clients", 0),
        ("accepted_client_idle_seconds", 0.0),
        ("max_global_frames_per_window", 0),
        ("max_client_frames_per_window", 0),
        ("frame_window_seconds", 0.0),
        ("max_outstanding_requests", 2),
        ("max_subscriptions", 1),
    ],
)
def test_resource_limits_require_positive_values(field_name: str, value: float) -> None:
    values: dict[str, float | int] = {
        "max_accepted_clients": 2,
        "accepted_client_idle_seconds": 5.0,
        "max_global_frames_per_window": 10,
        "max_client_frames_per_window": 5,
        "frame_window_seconds": 1.0,
        "max_outstanding_requests": 1,
        "max_subscriptions": 0,
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        IpcResourceLimits(**values)  # type: ignore[arg-type]


def test_accepted_client_limit_reuses_existing_slot_and_evicts_idle_client() -> None:
    clock = _FakeMonotonicClock()
    service = _service(
        clock=clock,
        limits=IpcResourceLimits(
            max_accepted_clients=1,
            accepted_client_idle_seconds=5.0,
            max_global_frames_per_window=20,
            max_client_frames_per_window=10,
        ),
    )
    first = _client(service)
    second = _client(service)

    assert first.connect().status is IpcStatus.ACCEPTED
    capacity_rejection = second.connect()
    assert first.connect().status is IpcStatus.ACCEPTED

    assert capacity_rejection.status is IpcStatus.REJECTED
    assert capacity_rejection.reason is IpcReason.IPC_RATE_LIMITED
    assert capacity_rejection.payload == {
        "limit_scope": "ACCEPTED_CLIENTS",
        "limit": 1,
        "window_ms": 5000,
        "retry_after_ms": 5000,
    }

    clock.advance(5.0)

    assert second.connect().status is IpcStatus.ACCEPTED
    expired_client_response = first.query_status()
    assert expired_client_response.status is IpcStatus.REJECTED
    assert expired_client_response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_handshake_publishes_fixed_resource_limits() -> None:
    clock = _FakeMonotonicClock()
    limits = IpcResourceLimits(
        max_accepted_clients=4,
        accepted_client_idle_seconds=15.0,
        max_global_frames_per_window=20,
        max_client_frames_per_window=5,
        frame_window_seconds=2.0,
    )
    client = _client(_service(clock=clock, limits=limits))

    response = client.connect()

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["resource_limits"] == {
        "max_accepted_clients": 4,
        "accepted_client_idle_ms": 15000,
        "max_global_frames_per_window": 20,
        "max_client_frames_per_window": 5,
        "frame_window_ms": 2000,
        "max_outstanding_requests": 1,
        "max_subscriptions": 0,
    }


def test_per_client_frame_window_is_bounded_and_recovers_at_window_boundary() -> None:
    clock = _FakeMonotonicClock()
    service = _service(
        clock=clock,
        limits=IpcResourceLimits(
            max_global_frames_per_window=20,
            max_client_frames_per_window=3,
            frame_window_seconds=0.5,
        ),
    )
    client = _client(service)

    assert client.connect().status is IpcStatus.ACCEPTED
    assert client.query_status().status is IpcStatus.ACCEPTED
    assert client.query_status().status is IpcStatus.ACCEPTED

    rejection = client.query_status()

    assert rejection.status is IpcStatus.REJECTED
    assert rejection.reason is IpcReason.IPC_RATE_LIMITED
    assert rejection.payload == {
        "limit_scope": "CLIENT_FRAMES",
        "limit": 3,
        "window_ms": 500,
        "retry_after_ms": 500,
    }

    clock.advance(0.5)

    assert client.query_status().status is IpcStatus.ACCEPTED


def test_global_frame_window_limits_distinct_clients_and_recovers() -> None:
    clock = _FakeMonotonicClock()
    service = _service(
        clock=clock,
        limits=IpcResourceLimits(
            max_global_frames_per_window=3,
            max_client_frames_per_window=3,
            frame_window_seconds=1.0,
        ),
    )
    first = _client(service)
    second = _client(service)

    assert first.connect().status is IpcStatus.ACCEPTED
    assert second.connect().status is IpcStatus.ACCEPTED
    assert first.query_status().status is IpcStatus.ACCEPTED

    rejection = second.query_status()

    assert rejection.status is IpcStatus.REJECTED
    assert rejection.reason is IpcReason.IPC_RATE_LIMITED
    assert rejection.payload == {
        "limit_scope": "GLOBAL_FRAMES",
        "limit": 3,
        "window_ms": 1000,
        "retry_after_ms": 1000,
    }

    clock.advance(1.0)

    assert second.query_status().status is IpcStatus.ACCEPTED


def test_rate_limited_command_does_not_create_a_durable_receipt() -> None:
    clock = _FakeMonotonicClock()
    service = _service(
        clock=clock,
        limits=IpcResourceLimits(
            max_global_frames_per_window=10,
            max_client_frames_per_window=1,
        ),
    )
    receipt_store = _ReceiptStoreSpy()
    service.command_receipt_store = receipt_store
    client = _client(service)

    assert client.connect().status is IpcStatus.ACCEPTED

    response = client.submit_command("UNKNOWN_MUTATION")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.IPC_RATE_LIMITED
    assert response.payload["limit_scope"] == "CLIENT_FRAMES"
    assert receipt_store.recorded == []
