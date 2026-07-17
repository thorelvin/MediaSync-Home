from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptConflict,
    CommandReceiptState,
    CommandReceiptStore,
    transition_command_receipt,
)
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
    command_receipt_store: CommandReceiptStore | None = None
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
        identity = self._accepted_clients.get(command.client_instance_id)
        if identity is None:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        if command.command_name == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value:
            return self._reject_create_standard_backup_job(command, identity)
        receipt_response = self._record_terminal_rejected_receipt(
            command,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {"command_name": command.command_name, "recognized": False}
        self._add_receipt_payload(response_payload, command.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED,
            response_payload,
        )

    def _reject_create_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_create_standard_backup_job_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobCreationPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "draft_id": command.draft_id,
            "recognized": True,
            "mutations_enabled": self.status.mutations_enabled,
        }
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        if self.job_draft_store is not None:
            response_payload["readiness"] = evaluate_standard_backup_job_creation(
                command=command,
                drafts=self.job_draft_store,
            ).to_dict()
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, response_payload)

    def _record_terminal_rejected_receipt(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        *,
        rejection_reason: str,
    ) -> IpcResponse | None:
        if self.command_receipt_store is None:
            return None
        incoming = _receipt_from_envelope(envelope, identity)
        try:
            receipt = self.command_receipt_store.record_received(incoming)
        except CommandReceiptConflict as exc:
            return IpcResponse.rejected(
                IpcReason.COMMAND_IDEMPOTENCY_CONFLICT,
                {
                    "conflict": str(exc),
                    "idempotency_key": envelope.idempotency_key,
                },
            )
        if receipt.state is CommandReceiptState.RECEIVED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=rejection_reason,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        return None

    def _add_receipt_payload(self, payload: dict[str, Any], idempotency_key: str) -> None:
        if self.command_receipt_store is None:
            return
        receipt = self.command_receipt_store.load_command_receipt(idempotency_key)
        if receipt is not None:
            payload["receipt"] = _receipt_payload(receipt)


def _receipt_from_envelope(
    envelope: IpcCommandEnvelope,
    identity: VerifiedClientIdentity,
) -> CommandReceipt:
    return CommandReceipt(
        request_id=envelope.request_id,
        client_instance_id=envelope.client_instance_id,
        principal_fingerprint=identity.user_sid_hash,
        idempotency_key=envelope.idempotency_key,
        command_name=envelope.command_name,
        payload_hash=envelope.payload_hash,
        protocol_version=envelope.protocol_version,
        schema_version=envelope.schema_version,
        expected_entity_revision=envelope.expected_entity_revision,
        payload_hash_scope=envelope.payload_hash_scope,
        payload_canonicalization_algorithm=envelope.payload_canonicalization_algorithm,
        payload_hash_algorithm=envelope.payload_hash_algorithm,
    )


def _receipt_payload(receipt: CommandReceipt) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": receipt.request_id,
        "idempotency_key": receipt.idempotency_key,
        "command_name": receipt.command_name,
        "state": receipt.state.value,
    }
    if receipt.result_entity_type is not None:
        payload["result_entity_type"] = receipt.result_entity_type
    if receipt.result_entity_id is not None:
        payload["result_entity_id"] = receipt.result_entity_id
    if receipt.rejection_reason is not None:
        payload["rejection_reason"] = receipt.rejection_reason
    return payload
