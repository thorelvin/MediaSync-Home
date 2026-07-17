from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from mediasync_home.application.job_creation import (
    JobCreationCommandName,
    JobCreationPayloadError,
    evaluate_standard_backup_job_creation,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_drafts import JobDraftStore
from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
)


@dataclass
class EngineHostIpcService:
    authorization: ClientAuthorizationPolicy
    status: RuntimeStatus = field(default_factory=lambda: startup_status(ProcessRole.ENGINE_HOST))
    job_draft_store: JobDraftStore | None = None
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

    def submit_command_envelope(self, payload: dict[str, Any]) -> IpcResponse:
        try:
            command = IpcCommandEnvelope.from_dict(payload)
        except (IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if command.protocol_version != PROTOCOL_VERSION:
            return IpcResponse.rejected(IpcReason.PROTOCOL_MISMATCH)
        if command.schema_version != SCHEMA_VERSION:
            return IpcResponse.rejected(IpcReason.SCHEMA_MISMATCH)
        if command.client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        if command.command_name == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value:
            return self._reject_create_standard_backup_job(command)
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED,
            {"command_name": command.command_name, "recognized": False},
        )

    def _reject_create_standard_backup_job(self, envelope: IpcCommandEnvelope) -> IpcResponse:
        try:
            command = parse_create_standard_backup_job_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobCreationPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "draft_id": command.draft_id,
            "recognized": True,
            "mutations_enabled": self.status.mutations_enabled,
        }
        if self.job_draft_store is not None:
            payload["readiness"] = evaluate_standard_backup_job_creation(
                command=command,
                drafts=self.job_draft_store,
            ).to_dict()
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
