from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
)


@dataclass
class EngineHostIpcService:
    authorization: ClientAuthorizationPolicy
    status: RuntimeStatus = field(default_factory=lambda: startup_status(ProcessRole.ENGINE_HOST))
    _accepted_clients: dict[str, VerifiedClientIdentity] = field(default_factory=dict)

    def handshake(
        self,
        payload: dict[str, Any],
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            request = HandshakeRequest.from_dict(payload)
        except (IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if request.protocol_version != PROTOCOL_VERSION:
            return IpcResponse.rejected(IpcReason.PROTOCOL_MISMATCH)
        if request.schema_version != SCHEMA_VERSION:
            return IpcResponse.rejected(IpcReason.SCHEMA_MISMATCH)
        reject_reason = self.authorization.reject_reason(request.role, identity)
        if reject_reason is not None:
            return IpcResponse.rejected(reject_reason)

        self._accepted_clients[request.client_instance_id] = identity
        return IpcResponse.accepted(
            {
                "server_nonce": str(uuid4()),
                "verified_user_sid_hash": identity.user_sid_hash,
                "host_status": self.status.to_dict(),
            }
        )

    def query_status(self, client_instance_id: str) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        return IpcResponse.accepted({"host_status": self.status.to_dict()})

    def submit_command(self, client_instance_id: str, command_name: str) -> IpcResponse:
        del command_name
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED)
