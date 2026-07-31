from __future__ import annotations

from typing import Any

from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.application.job_drafts import DraftTarget, StandardBackupJobDraft
from mediasync_home.application.runs import RunCommandName
from mediasync_home.ipc.protocol import IpcReason, IpcResponse
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


def test_engine_client_reconnects_once_when_host_loses_handshake_state() -> None:
    ipc_client = _RestartedHostIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.get_status()

    assert response.reason is None
    assert response.payload == {"ready": True}
    assert ipc_client.status_calls == 2
    assert ipc_client.connect_calls == 1


def test_engine_client_submits_checksum_bound_start_run() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.start_backup(
        plan_id="plan-a",
        plan_checksum="a" * 64,
        request_id="request-a",
        idempotency_key="idempotency-a",
    )

    assert response.reason is None
    assert ipc_client.command_name == RunCommandName.START_RUN.value
    assert ipc_client.payload == {
        "plan_id": "plan-a",
        "plan_checksum": "a" * 64,
    }
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_queues_backup_check() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.check_backup(
        job_id="job-a",
        request_id="request-a",
        idempotency_key="idempotency-a",
    )

    assert response.reason is None
    assert ipc_client.command_name == "CHECK_BACKUP"
    assert ipc_client.request_id == "request-a"
    assert ipc_client.idempotency_key == "idempotency-a"
    assert ipc_client.payload == {"job_id": "job-a"}
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_submits_pause_and_resume_run_controls() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    pause = client.pause_backup(
        run_id="run-a",
        request_id="pause-request",
        idempotency_key="pause-key",
    )

    assert pause.reason is None
    assert ipc_client.command_name == RunCommandName.PAUSE_RUN.value
    assert ipc_client.payload == {"run_id": "run-a"}
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)

    resume = client.resume_backup(
        run_id="run-a",
        request_id="resume-request",
        idempotency_key="resume-key",
    )

    assert resume.reason is None
    assert ipc_client.command_name == RunCommandName.RESUME_RUN.value
    assert ipc_client.payload == {"run_id": "run-a"}
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)

    stop = client.stop_backup_after_active_file(
        run_id="run-a",
        request_id="stop-request",
        idempotency_key="stop-key",
    )

    assert stop.reason is None
    assert ipc_client.command_name == RunCommandName.STOP_RUN_AFTER_ACTIVE_FILE.value
    assert ipc_client.payload == {"run_id": "run-a"}
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_returns_failed_reconnect_without_replaying_request() -> None:
    ipc_client = _RestartedHostIpcClient(
        handshake=IpcResponse.rejected(IpcReason.CLIENT_IDENTITY_MISMATCH)
    )
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.get_status()

    assert response.reason is IpcReason.CLIENT_IDENTITY_MISMATCH
    assert ipc_client.status_calls == 1
    assert ipc_client.connect_calls == 1


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


class _RestartedHostIpcClient:
    def __init__(self, *, handshake: IpcResponse | None = None) -> None:
        self.handshake = handshake or IpcResponse.accepted({"connected": True})
        self.status_calls = 0
        self.connect_calls = 0

    def connect(self) -> IpcResponse:
        self.connect_calls += 1
        return self.handshake

    def query_status(self) -> IpcResponse:
        self.status_calls += 1
        if self.status_calls == 1:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        return IpcResponse.accepted({"ready": True})
