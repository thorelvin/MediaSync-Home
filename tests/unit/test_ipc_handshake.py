from __future__ import annotations

import pytest

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    IpcProtocolError,
    IpcReason,
    IpcStatus,
    decode_frame,
    encode_frame,
)
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient


EXPECTED_USER = "same-user-sid-hash"
EXPECTED_SESSION = 42


def _identity(
    *,
    user_sid_hash: str = EXPECTED_USER,
    session_id: int = EXPECTED_SESSION,
    is_remote: bool = False,
) -> VerifiedClientIdentity:
    return VerifiedClientIdentity(
        user_sid_hash=user_sid_hash,
        session_id=session_id,
        is_remote=is_remote,
        transport="in-process-test",
    )


def _service() -> EngineHostIpcService:
    return EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash=EXPECTED_USER,
            expected_session_id=EXPECTED_SESSION,
        )
    )


def _client(
    *,
    role: ProcessRole = ProcessRole.GUI,
    identity: VerifiedClientIdentity | None = None,
) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=_service(),
        identity=identity or _identity(),
        role=role,
    )


def test_gui_client_handshake_and_status_query_succeed() -> None:
    ipc_client = _client()
    gui_client = EngineClient(ipc_client)

    handshake = gui_client.connect()
    status = gui_client.get_status()

    assert handshake.status is IpcStatus.ACCEPTED
    assert handshake.reason is None
    assert handshake.payload["verified_user_sid_hash"] == EXPECTED_USER
    assert status.status is IpcStatus.ACCEPTED
    assert status.payload["host_status"]["role"] == ProcessRole.ENGINE_HOST.value
    assert status.payload["host_status"]["mutations_enabled"] is False


def test_handshake_uses_verified_identity_not_payload_claim() -> None:
    ipc_client = _client()

    response = ipc_client.connect(claimed_user_sid_hash="attacker-claim")

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["verified_user_sid_hash"] == EXPECTED_USER


@pytest.mark.parametrize(
    ("protocol_version", "schema_version", "reason"),
    [
        (PROTOCOL_VERSION + 1, SCHEMA_VERSION, IpcReason.PROTOCOL_MISMATCH),
        (PROTOCOL_VERSION, SCHEMA_VERSION + 1, IpcReason.SCHEMA_MISMATCH),
    ],
)
def test_version_mismatch_is_rejected_without_status_access(
    protocol_version: int,
    schema_version: int,
    reason: IpcReason,
) -> None:
    ipc_client = _client()

    handshake = ipc_client.connect(
        protocol_version=protocol_version,
        schema_version=schema_version,
    )
    status = ipc_client.query_status()

    assert handshake.status is IpcStatus.REJECTED
    assert handshake.reason is reason
    assert status.status is IpcStatus.REJECTED
    assert status.reason is IpcReason.HANDSHAKE_REQUIRED


@pytest.mark.parametrize(
    ("identity", "reason"),
    [
        (_identity(user_sid_hash="other-user"), IpcReason.CLIENT_IDENTITY_MISMATCH),
        (_identity(session_id=99), IpcReason.CLIENT_IDENTITY_MISMATCH),
        (_identity(is_remote=True), IpcReason.REMOTE_CLIENT_REJECTED),
    ],
)
def test_identity_policy_rejects_untrusted_clients(
    identity: VerifiedClientIdentity,
    reason: IpcReason,
) -> None:
    response = _client(identity=identity).connect()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is reason


def test_mutating_commands_are_disabled_in_0b_ipc_slice() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.submit_command("START_RUN")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED


def test_query_requires_prior_handshake() -> None:
    response = _client().query_status()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_frame_codec_enforces_json_object_and_size_limit() -> None:
    payload = {"message_type": "HANDSHAKE", "content": "ok"}

    assert decode_frame(encode_frame(payload)) == payload
    with pytest.raises(IpcProtocolError, match="IPC frame must be a JSON object"):
        decode_frame(b"[]")
    with pytest.raises(IpcProtocolError, match="frame exceeds limit"):
        encode_frame({"payload": "x" * MAX_FRAME_BYTES})
