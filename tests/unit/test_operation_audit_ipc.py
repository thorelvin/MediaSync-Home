from __future__ import annotations

from mediasync_home.application.operation_audit_read_models import (
    OperationAttemptSummary,
    OperationAuditIdentity,
    OperationOutcomeSummary,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient


class _OperationAuditStore:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def load_operation_audit_identity(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationAuditIdentity | None:
        if run_id != "run-a" or operation_id != "op-a":
            return None
        return OperationAuditIdentity(
            run_id=run_id,
            run_target_id="target-a",
            operation_id=operation_id,
            target_relative_path="Pictures/A.jpg",
        )

    def list_operation_attempt_summaries(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[OperationAttemptSummary, ...]:
        del run_id, operation_id
        self.limits.append(limit)
        return (
            OperationAttemptSummary(
                attempt_number=1,
                state="SUCCEEDED",
                process_instance_id="host-a",
                finished_utc="2026-07-31T10:00:00.000Z",
                bytes_transferred=128,
                batch_id="staging-a",
                lease_id="lease-a",
                ownership_epoch=1,
                fencing_token=1,
                source_guard_kind="FILE_ID",
                source_guard_evidence_hash="a" * 64,
                transfer_state="TRANSFERRED_TO_STAGING",
                assurance_level="FULL_HASH",
                durability_level="DURABLE",
                verification_json='{"verified":true}',
                error_code=None,
            ),
        )

    def load_operation_outcome_summary(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationOutcomeSummary | None:
        del run_id, operation_id
        return OperationOutcomeSummary(
            final_state="SUCCEEDED",
            completed_utc="2026-07-31T10:00:01.000Z",
            bytes_transferred=128,
            transfer_state="TRANSFERRED_TO_STAGING",
            assurance_level="FULL_HASH",
            hash_evidence_kind="CURRENT_READ_HASH",
            durability_level="DURABLE",
            verification_json='{"verified":true}',
            error_code=None,
        )


def test_operation_audit_ipc_requires_handshake_and_rejects_invalid_bounds() -> None:
    client = _client(_service(_OperationAuditStore()))

    before_handshake = client.query_operation_audit(
        run_id="run-a",
        operation_id="op-a",
    )
    client.connect()
    invalid = client.query_operation_audit(
        run_id="run-a",
        operation_id="op-a",
        limit=101,
    )

    assert before_handshake.reason is IpcReason.HANDSHAKE_REQUIRED
    assert invalid.reason is IpcReason.INVALID_FRAME


def test_engine_client_queries_bounded_operation_audit_after_handshake_retry() -> None:
    store = _OperationAuditStore()
    engine_client = EngineClient(_client(_service(store)))

    response = engine_client.get_operation_audit(
        run_id="run-a",
        operation_id="op-a",
        limit=7,
    )

    assert response.status is IpcStatus.ACCEPTED
    assert store.limits == [7]
    detail = response.payload["operation_audit"]
    assert detail["found"] is True
    assert detail["target_relative_path"] == "Pictures/A.jpg"
    assert detail["attempts"][0]["state"] == "SUCCEEDED"
    assert detail["outcome"]["final_state"] == "SUCCEEDED"


def _service(store: _OperationAuditStore) -> EngineHostIpcService:
    return EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash="same-user",
            expected_session_id=42,
        ),
        operation_audit_read_store=store,
    )


def _client(service: EngineHostIpcService) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=service,
        identity=VerifiedClientIdentity(
            user_sid_hash="same-user",
            session_id=42,
            is_remote=False,
            transport="operation-audit-ipc-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id="77777777-7777-4777-8777-777777777777",
    )
