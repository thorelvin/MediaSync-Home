from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import VerifiedClientIdentity
from mediasync_home.ipc.protocol import PROTOCOL_VERSION, SCHEMA_VERSION, HandshakeRequest, IpcResponse
from mediasync_home.ipc.server import EngineHostIpcService


@dataclass
class InProcessIpcClient:
    service: EngineHostIpcService
    identity: VerifiedClientIdentity
    role: ProcessRole
    client_instance_id: str = field(default_factory=lambda: str(uuid4()))

    def connect(
        self,
        *,
        protocol_version: int = PROTOCOL_VERSION,
        schema_version: int = SCHEMA_VERSION,
        claimed_user_sid_hash: str | None = None,
    ) -> IpcResponse:
        request = HandshakeRequest(
            protocol_version=protocol_version,
            schema_version=schema_version,
            role=self.role,
            client_instance_id=self.client_instance_id,
            app_build="0B-dev",
            launch_nonce=str(uuid4()),
            claimed_user_sid_hash=claimed_user_sid_hash,
        )
        return self.service.handshake(request.to_dict(), self.identity)

    def query_status(self) -> IpcResponse:
        return self.service.query_status(self.client_instance_id)

    def submit_command(self, command_name: str) -> IpcResponse:
        return self.service.submit_command(self.client_instance_id, command_name)
