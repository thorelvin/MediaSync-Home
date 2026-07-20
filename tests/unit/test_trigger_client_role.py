from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

import mediasync_home.composition.trigger_client as trigger_module
from mediasync_home.application.trigger_occurrences import TriggerCommandName
from mediasync_home.composition.trigger_client import (
    _run_status_query,
    run_trigger_client,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason, IpcResponse


def test_trigger_client_without_query_keeps_generic_role_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_role(
        role: ProcessRole,
        argv: object,
        *,
        emit: object | None = None,
    ) -> int:
        captured["role"] = role
        captured["argv"] = argv
        captured["emit"] = emit
        return 7

    monkeypatch.setattr(trigger_module, "run_role", fake_run_role)

    assert run_trigger_client(["--installation-id", "preview-a"]) == 7

    assert captured == {
        "role": ProcessRole.TRIGGER_CLIENT,
        "argv": ["--installation-id", "preview-a"],
        "emit": None,
    }


def test_trigger_status_query_uses_explicit_pipe_with_trigger_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWin32NamedPipeClient(_AcceptedStatusClient):
        instances: list["FakeWin32NamedPipeClient"] = []

    monkeypatch.setattr(trigger_module.os, "name", "nt")
    _install_fake_win32_module(monkeypatch, FakeWin32NamedPipeClient)
    output: list[str] = []

    result = run_trigger_client(
        [
            "--query-status",
            "--pipe-name",
            "pipe-a",
            "--timeout-seconds",
            "1.25",
        ],
        emit=output.append,
    )

    response = json.loads(output[0])
    client = FakeWin32NamedPipeClient.instances[0]
    assert result == 0
    assert response["status"] == "ACCEPTED"
    assert response["payload"] == {"status": "trigger-ok"}
    assert client.pipe_name == "pipe-a"
    assert client.role is ProcessRole.TRIGGER_CLIENT
    assert client.timeout_ms == 1250
    assert client.calls == ("connect", "query_status")


def test_trigger_enqueue_occurrence_submits_command_with_stable_delivery_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWin32NamedPipeClient(_AcceptedTriggerCommandClient):
        instances: list["FakeWin32NamedPipeClient"] = []

    monkeypatch.setattr(trigger_module.os, "name", "nt")
    _install_fake_win32_module(monkeypatch, FakeWin32NamedPipeClient)
    output: list[str] = []
    delivery_id = "11111111-1111-4111-8111-111111111111"

    result = run_trigger_client(
        [
            "--enqueue-trigger-occurrence",
            "--pipe-name",
            "pipe-a",
            "--schedule-id",
            "schedule-a",
            "--schedule-revision-hash",
            "a" * 64,
            "--delivery-id",
            delivery_id,
            "--observed-start-utc",
            "2026-07-20T12:00:00.000Z",
            "--task-definition-hash",
            "b" * 64,
            "--task-instance-id",
            "task-instance-a",
        ],
        emit=output.append,
    )

    response = json.loads(output[0])
    client = FakeWin32NamedPipeClient.instances[0]
    assert result == 2
    assert response["reason"] == "MUTATING_COMMANDS_DISABLED"
    assert client.pipe_name == "pipe-a"
    assert client.role is ProcessRole.TRIGGER_CLIENT
    assert client.calls == ("connect", "submit_command")
    assert client.submitted is not None
    assert client.submitted["command_name"] == TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value
    assert client.submitted["request_id"] == delivery_id
    assert client.submitted["idempotency_key"] == delivery_id
    assert len(str(client.submitted["payload_hash"])) == 64
    assert client.submitted["payload"] == {
        "delivery": {
            "delivery_id": delivery_id,
            "observed_start_utc": "2026-07-20T12:00:00.000Z",
            "task_definition_hash": "b" * 64,
            "task_instance_id": "task-instance-a",
            "trigger_kind": "SCHEDULED_TIME",
        },
        "schedule_id": "schedule-a",
        "schedule_revision_hash": "a" * 64,
    }


def test_trigger_enqueue_occurrence_rejects_invalid_cli_payload_without_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWin32NamedPipeClient(_AcceptedTriggerCommandClient):
        instances: list["FakeWin32NamedPipeClient"] = []

    monkeypatch.setattr(trigger_module.os, "name", "nt")
    _install_fake_win32_module(monkeypatch, FakeWin32NamedPipeClient)
    output: list[str] = []

    result = run_trigger_client(
        [
            "--enqueue-trigger-occurrence",
            "--pipe-name",
            "pipe-a",
            "--schedule-id",
            "schedule-a",
            "--schedule-revision-hash",
            "not-a-hash",
        ],
        emit=output.append,
    )

    response = json.loads(output[0])
    client = FakeWin32NamedPipeClient.instances[0]
    assert result == 2
    assert response == {
        "payload": {"reason": "TRIGGER_OCCURRENCE_PAYLOAD_INVALID"},
        "reason": "INVALID_FRAME",
        "status": "REJECTED",
    }
    assert client.calls == ()
    assert client.submitted is None


def test_trigger_status_query_returns_typed_unavailable_when_publication_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWin32NamedPipeClient(_AcceptedStatusClient):
        instances: list["FakeWin32NamedPipeClient"] = []

    monkeypatch.setattr(trigger_module.os, "name", "nt")
    monkeypatch.setattr(trigger_module, "_load_matching_local_preview_publication", lambda args: None)
    _install_fake_win32_module(monkeypatch, FakeWin32NamedPipeClient)
    output: list[str] = []

    result = run_trigger_client(
        [
            "--query-status",
            "--installation-id",
            "preview-a",
        ],
        emit=output.append,
    )

    assert result == 2
    assert json.loads(output[0]) == {
        "payload": {
            "reason": "HOST_LOCATOR_PUBLICATION_UNAVAILABLE",
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        },
        "reason": "ENGINE_HOST_UNAVAILABLE",
        "status": "REJECTED",
    }
    assert FakeWin32NamedPipeClient.instances == []


def test_trigger_status_query_uses_matching_host_locator_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWin32NamedPipeClient(_AcceptedStatusClient):
        instances: list["FakeWin32NamedPipeClient"] = []

    publication = _Publication(pipe_name="pipe-from-host-locator")
    monkeypatch.setattr(trigger_module.os, "name", "nt")
    monkeypatch.setattr(
        trigger_module,
        "_load_matching_local_preview_publication",
        lambda args: publication,
    )
    _install_fake_win32_module(monkeypatch, FakeWin32NamedPipeClient)
    output: list[str] = []

    result = run_trigger_client(
        [
            "--query-status",
            "--installation-id",
            "preview-a",
            "--timeout-seconds",
            "2",
        ],
        emit=output.append,
    )

    client = FakeWin32NamedPipeClient.instances[0]
    assert result == 0
    assert json.loads(output[0])["status"] == "ACCEPTED"
    assert client.pipe_name == "pipe-from-host-locator"
    assert client.role is ProcessRole.TRIGGER_CLIENT
    assert client.timeout_ms == 2000


def test_trigger_status_query_clears_stale_publication_when_locator_pipe_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWin32NamedPipeClient(_DeadPipeClient):
        instances: list["FakeWin32NamedPipeClient"] = []

    publication = _Publication(pipe_name="dead-pipe")
    monkeypatch.setattr(trigger_module.os, "name", "nt")
    monkeypatch.setattr(
        trigger_module,
        "_load_matching_local_preview_publication",
        lambda args: publication,
    )
    monkeypatch.setattr(trigger_module, "_clear_stale_host_publication", lambda pub: True)
    _install_fake_win32_module(monkeypatch, FakeWin32NamedPipeClient)
    output: list[str] = []

    result = run_trigger_client(
        [
            "--query-status",
            "--installation-id",
            "preview-a",
            "--timeout-seconds",
            "1",
        ],
        emit=output.append,
    )

    assert result == 2
    assert json.loads(output[0]) == {
        "payload": {
            "host_locator_publication": publication.to_payload(),
            "reason": "HOST_LOCATOR_PUBLICATION_NOT_LIVE",
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
            "stale_host_locator_publication_cleared": True,
        },
        "reason": "ENGINE_HOST_UNAVAILABLE",
        "status": "REJECTED",
    }


def test_trigger_status_query_returns_failed_handshake_without_status_query() -> None:
    client = _RejectedHandshakeClient()

    response = _run_status_query(client)

    assert response.reason is IpcReason.CLIENT_IDENTITY_MISMATCH
    assert client.calls == ("connect",)


class _AcceptedStatusClient:
    instances: list["_AcceptedStatusClient"] = []

    def __init__(self, *, pipe_name: str, role: ProcessRole, timeout_ms: int) -> None:
        self.pipe_name = pipe_name
        self.role = role
        self.timeout_ms = timeout_ms
        self.calls: tuple[str, ...] = ()
        self.instances.append(self)

    def connect(self) -> IpcResponse:
        self.calls = (*self.calls, "connect")
        return IpcResponse.accepted({"handshake": "ok"})

    def query_status(self) -> IpcResponse:
        self.calls = (*self.calls, "query_status")
        return IpcResponse.accepted({"status": "trigger-ok"})


class _AcceptedTriggerCommandClient(_AcceptedStatusClient):
    def __init__(self, *, pipe_name: str, role: ProcessRole, timeout_ms: int) -> None:
        super().__init__(pipe_name=pipe_name, role=role, timeout_ms=timeout_ms)
        self.submitted: dict[str, object] | None = None

    def submit_command(
        self,
        command_name: str,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
        payload_hash: str | None = None,
    ) -> IpcResponse:
        self.calls = (*self.calls, "submit_command")
        self.submitted = {
            "command_name": command_name,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "payload_hash": payload_hash,
            "request_id": request_id,
        }
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED,
            {"command_name": command_name, "recognized": True},
        )


class _DeadPipeClient:
    instances: list["_DeadPipeClient"] = []

    def __init__(self, *, pipe_name: str, role: ProcessRole, timeout_ms: int) -> None:
        self.pipe_name = pipe_name
        self.role = role
        self.timeout_ms = timeout_ms
        self.instances.append(self)

    def connect(self) -> IpcResponse:
        raise TimeoutError("pipe is gone")

    def query_status(self) -> IpcResponse:
        raise AssertionError("status must not be queried after a dead pipe")


class _RejectedHandshakeClient:
    def __init__(self) -> None:
        self.calls: tuple[str, ...] = ()

    def connect(self) -> IpcResponse:
        self.calls = (*self.calls, "connect")
        return IpcResponse.rejected(IpcReason.CLIENT_IDENTITY_MISMATCH)

    def query_status(self) -> IpcResponse:
        self.calls = (*self.calls, "query_status")
        raise AssertionError("status must not be queried after a rejected handshake")


class _Publication:
    def __init__(self, *, pipe_name: str) -> None:
        self.pipe_name = pipe_name

    def to_payload(self) -> dict[str, object]:
        return {
            "installation_id": "preview-a",
            "locator_key": "a" * 24,
            "mutex_name": "Local\\MediaSyncHome-0B-" + "a" * 24,
            "pipe_name": self.pipe_name,
            "process_id": 4321,
            "schema_version": 1,
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
            "state_root": "C:\\State",
            "status": "STARTING",
        }


def _install_fake_win32_module(
    monkeypatch: pytest.MonkeyPatch,
    client_class: type[object],
) -> None:
    fake_module = ModuleType("mediasync_home.ipc.win32_named_pipe")
    fake_module.Win32NamedPipeClient = client_class  # type: ignore[attr-defined]
    fake_module.current_process_identity = lambda: SimpleNamespace(user_sid_hash="b" * 64)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mediasync_home.ipc.win32_named_pipe", fake_module)
