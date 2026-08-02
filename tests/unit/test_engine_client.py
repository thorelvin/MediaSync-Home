from __future__ import annotations

from threading import Event
from typing import Any

from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.endpoint_takeover import EndpointTakeoverCommandName
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.application.job_draft_saving import JobDraftCommandName
from mediasync_home.application.job_drafts import DraftTarget, StandardBackupJobDraft
from mediasync_home.application.job_editing import JobEditingCommandName
from mediasync_home.application.runs import RunCommandName
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCommandName,
)
from mediasync_home.ipc.protocol import IpcReason, IpcResponse
from mediasync_home.presentation.engine_client import EngineClient


def test_engine_client_forwards_background_cancellation_binding() -> None:
    ipc_client = _CancellationBindingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]
    cancellation = Event()

    client.bind_background_cancellation(cancellation)
    client.bind_background_cancellation(None)

    assert ipc_client.bindings == [cancellation, None]


def test_engine_client_queries_selected_directory_identities() -> None:
    ipc_client = _DirectoryIdentityIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.get_selected_directory_identities(
        path_labels=("C:/Pictures", "E:/Backup")
    )

    assert response.status.value == "ACCEPTED"
    assert ipc_client.path_labels == ("C:/Pictures", "E:/Backup")


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
        autosave_draft_id="local-setup-autosave-v1",
    )

    assert response.reason is None
    assert (
        ipc_client.command_name
        == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value
    )
    assert ipc_client.request_id == "44444444-4444-4444-8444-444444444444"
    assert ipc_client.idempotency_key == "66666666-6666-4666-8666-666666666666"
    assert ipc_client.payload is not None
    assert ipc_client.payload["draft_id"] == draft.draft_id
    assert ipc_client.payload["autosave_draft_id"] == "local-setup-autosave-v1"
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_submits_incomplete_standard_backup_draft() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]
    draft = StandardBackupJobDraft(
        draft_id="setup-autosave",
        source_name="Pictures",
        source_path_label="C:/Users/Ada/Pictures",
    )

    response = client.save_standard_backup_draft(
        draft=draft,
        request_id="44444444-4444-4444-8444-444444444444",
        idempotency_key="66666666-6666-4666-8666-666666666666",
    )

    assert response.reason is None
    assert ipc_client.command_name == JobDraftCommandName.SAVE_STANDARD_BACKUP_DRAFT.value
    assert ipc_client.payload == {
        "draft_id": "setup-autosave",
        "draft": {
            "draft_id": "setup-autosave",
            "schema_version": 1,
            "source_name": "Pictures",
            "source_path_label": "C:/Users/Ada/Pictures",
            "targets": [],
            "defaults": {
                "behavior": "UPDATE_BACKUP",
                "file_selection": "ALL_USER_FILES",
                "verification": "STANDARD",
                "retention": "THIRTY_DAYS",
                "extra_files": "KEEP_ON_TARGET",
                "performance": "AUTO",
            },
        },
    }
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_submits_revision_bound_standard_backup_edit() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]
    draft = StandardBackupJobDraft(
        draft_id="77777777-7777-4777-8777-777777777777",
        source_name="Pictures renamed",
        source_path_label="C:/Users/Ada/Pictures",
        targets=(DraftTarget(name="USB 1", path_label="E:/Backup"),),
    )

    response = client.update_standard_backup_job(
        job_id="job-a",
        expected_job_revision_id="job-revision-a",
        expected_lifecycle_row_version=3,
        draft=draft,
        check_after_save=False,
        request_id="44444444-4444-4444-8444-444444444444",
        idempotency_key="66666666-6666-4666-8666-666666666666",
    )

    assert response.reason is None
    assert (
        ipc_client.command_name
        == JobEditingCommandName.UPDATE_STANDARD_BACKUP_JOB.value
    )
    assert ipc_client.payload is not None
    assert ipc_client.payload["job_id"] == "job-a"
    assert ipc_client.payload["expected_job_revision_id"] == "job-revision-a"
    assert ipc_client.payload["expected_lifecycle_row_version"] == 3
    assert ipc_client.payload["check_after_save"] is False
    assert ipc_client.payload["explicit_save"] is True
    assert ipc_client.payload["draft"] == {
        "draft_id": draft.draft_id,
        "schema_version": 1,
        "source_name": "Pictures renamed",
        "source_path_label": "C:/Users/Ada/Pictures",
        "targets": [
            {
                "name": "USB 1",
                "path_label": "E:/Backup",
                "independent_device_id": None,
            }
        ],
        "defaults": {
            "behavior": "UPDATE_BACKUP",
            "file_selection": "ALL_USER_FILES",
            "verification": "STANDARD",
            "retention": "THIRTY_DAYS",
            "extra_files": "KEEP_ON_TARGET",
            "performance": "AUTO",
        },
    }
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


def test_engine_client_submits_revision_bound_writable_target_registration() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.register_writable_targets(
        job_id="job-a",
        job_revision_id="job-revision-a",
        request_id="request-a",
        idempotency_key="idempotency-a",
    )

    assert response.reason is None
    assert ipc_client.command_name == (
        WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value
    )
    assert ipc_client.request_id == "request-a"
    assert ipc_client.idempotency_key == "idempotency-a"
    assert ipc_client.payload == {
        "job_id": "job-a",
        "job_revision_id": "job-revision-a",
    }
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_submits_explicit_revision_bound_controlled_takeover() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.start_controlled_endpoint_takeover(
        job_id="job-a",
        job_revision_id="job-revision-a",
        target_ordinal=2,
        endpoint_id="11111111-1111-4111-8111-111111111111",
        expected_foreign_owner_installation_id=("22222222-2222-4222-8222-222222222222"),
        expected_ownership_epoch=7,
        request_id="request-a",
        idempotency_key="idempotency-a",
    )

    assert response.reason is None
    assert ipc_client.command_name == (
        EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value
    )
    assert ipc_client.payload == {
        "job_id": "job-a",
        "job_revision_id": "job-revision-a",
        "target_ordinal": 2,
        "endpoint_id": "11111111-1111-4111-8111-111111111111",
        "expected_foreign_owner_installation_id": (
            "22222222-2222-4222-8222-222222222222"
        ),
        "expected_ownership_epoch": 7,
        "explicit_confirmation": True,
    }
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)


def test_engine_client_submits_target_scoped_retry_lineage() -> None:
    ipc_client = _RecordingIpcClient()
    client = EngineClient(ipc_client)  # type: ignore[arg-type]

    response = client.start_backup(
        plan_id="plan-refreshed",
        plan_checksum="b" * 64,
        request_id="request-retry",
        idempotency_key="idempotency-retry",
        target_endpoint_ids=("target-b",),
        resumed_from_run_id="run-source",
        source_operation_ids=("op-source-b",),
    )

    assert response.reason is None
    assert ipc_client.command_name == RunCommandName.START_RUN.value
    assert ipc_client.payload == {
        "plan_id": "plan-refreshed",
        "plan_checksum": "b" * 64,
        "target_endpoint_ids": ["target-b"],
        "resumed_from_run_id": "run-source",
        "source_operation_ids": ["op-source-b"],
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
    assert ipc_client.payload == {
        "job_id": "job-a",
        "start_when_safe": True,
    }
    assert ipc_client.payload_hash == canonical_command_payload_hash(ipc_client.payload)

    client.check_backup(
        job_id="job-a",
        request_id="request-b",
        idempotency_key="idempotency-b",
        start_when_safe=False,
    )

    assert ipc_client.payload == {
        "job_id": "job-a",
        "start_when_safe": False,
    }


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


class _CancellationBindingIpcClient:
    def __init__(self) -> None:
        self.bindings: list[Event | None] = []

    def bind_background_cancellation(self, cancellation: Event | None) -> None:
        self.bindings.append(cancellation)


class _DirectoryIdentityIpcClient:
    def __init__(self) -> None:
        self.path_labels: tuple[str, ...] = ()

    def query_selected_directory_identities(
        self,
        *,
        path_labels: tuple[str, ...],
    ) -> IpcResponse:
        self.path_labels = path_labels
        return IpcResponse.accepted({"selected_directory_identities": {}})


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
