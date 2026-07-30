from __future__ import annotations

from typing import Any

from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.application.job_drafts import DraftTarget, StandardBackupJobDraft
from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.engine_client import EngineClient


def test_engine_client_submits_reviewed_standard_backup_draft() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]
    draft = StandardBackupJobDraft(
        draft_id="77777777-7777-4777-8777-777777777777",
        source_name="Pictures",
        source_path_label="C:/Users/Ada/Pictures",
        targets=(DraftTarget(name="USB 1", path_label="E:/Backup"),),
    )

    response = client.create_standard_backup_job(
        draft=draft,
        request_id="44444444-4444-4444-8444-444444444444",
        idempotency_key="66666666-6666-4666-8666-666666666666",
    )

    assert response.reason is None
    assert ipc_client.command_name == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value
    assert ipc_client.request_id == "44444444-4444-4444-8444-444444444444"
    assert ipc_client.idempotency_key == "66666666-6666-4666-8666-666666666666"
    assert ipc_client.payload is not None
    assert ipc_client.payload["draft_id"] == draft.draft_id
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


class _RecordingIpcClient:
    def __init__(self) -> None:
        self.command_name: str | None = None
        self.request_id: str | None = None
        self.idempotency_key: str | None = None
        self.payload: dict[str, object] | None = None
        self.payload_hash: str | None = None

    def submit_command(
        self,
        command_name: str,
        **kwargs: Any,
    ) -> IpcResponse:
        self.command_name = command_name
        self.request_id = kwargs.get("request_id")
        self.idempotency_key = kwargs.get("idempotency_key")
        self.payload = kwargs.get("payload")
        self.payload_hash = kwargs.get("payload_hash")
        return IpcResponse.accepted({"created": True})
