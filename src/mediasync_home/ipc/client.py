from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcResponse,
)
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

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self.service.query_backup_overview(
            self.client_instance_id,
            draft_id=draft_id,
            limit=limit,
            offset=offset,
        )

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        return self.service.query_backup_job_detail(
            self.client_instance_id,
            job_id=job_id,
        )

    def query_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self.service.query_activity_overview(
            self.client_instance_id,
            job_id=job_id,
            limit=limit,
            offset=offset,
        )

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self.service.query_plan_operations(
            self.client_instance_id,
            plan_id=plan_id,
            limit=limit,
            after=after,
        )

    def query_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self.service.query_snapshot_entries(
            self.client_instance_id,
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
        )

    def query_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        return self.service.query_snapshot_coverage(
            self.client_instance_id,
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
            coverage_states=coverage_states,
        )

    def query_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        return self.service.query_snapshot_issues(
            self.client_instance_id,
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
            blocking_only=blocking_only,
        )

    def submit_command(
        self,
        command_name: str,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
        payload_hash: str | None = None,
    ) -> IpcResponse:
        command_payload = payload or {}
        if payload_hash is None:
            if command_payload:
                raise IpcProtocolError("payload_hash is required for non-empty command payloads")
            payload_hash = "6e46dd10defc9b56c29a6ec56b508c21f54c08192194e4df25bf36f0c9c3c279"
        envelope = IpcCommandEnvelope(
            protocol_version=PROTOCOL_VERSION,
            schema_version=SCHEMA_VERSION,
            request_id=request_id or str(uuid4()),
            client_instance_id=self.client_instance_id,
            idempotency_key=idempotency_key or str(uuid4()),
            command_name=command_name,
            payload=command_payload,
            payload_hash=payload_hash,
        )
        return self.service.submit_command_envelope(envelope.to_dict())
