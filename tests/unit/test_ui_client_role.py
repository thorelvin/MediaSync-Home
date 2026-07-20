from __future__ import annotations

import pytest

from mediasync_home.composition.ui import build_parser, _parse_payload_json, _run_pipe_action
from mediasync_home.ipc.protocol import IpcProtocolError, IpcReason, IpcResponse


def test_ui_pipe_action_queries_status_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(["--pipe-name", "pipe-a", "--query-status"])

    response = _run_pipe_action(args, client)

    assert response.payload == {"status": "ready"}
    assert client.calls == ("connect", "query_status")


def test_ui_pipe_action_queries_backup_overview_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-backup-overview",
            "--draft-id",
            "draft-a",
            "--limit",
            "5",
            "--offset",
            "10",
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"overview": "ok"}
    assert client.calls == ("connect", "query_backup_overview")
    assert client.overview_query == {
        "draft_id": "draft-a",
        "limit": 5,
        "offset": 10,
    }


def test_ui_pipe_action_queries_activity_overview_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-activity-overview",
            "--job-id",
            "job-a",
            "--limit",
            "5",
            "--offset",
            "10",
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"activity": "ok"}
    assert client.calls == ("connect", "query_activity_overview")
    assert client.activity_query == {
        "job_id": "job-a",
        "limit": 5,
        "offset": 10,
    }


def test_ui_pipe_action_submits_command_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--submit-command",
            "UNKNOWN_MUTATION",
            "--request-id",
            "request-a",
            "--idempotency-key",
            "idempotency-a",
            "--payload-json",
            '{"draft_id":"draft-a"}',
            "--payload-hash",
            "a" * 64,
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"command": "UNKNOWN_MUTATION"}
    assert client.calls == ("connect", "submit_command")
    assert client.submitted == {
        "command_name": "UNKNOWN_MUTATION",
        "request_id": "request-a",
        "idempotency_key": "idempotency-a",
        "payload": {"draft_id": "draft-a"},
        "payload_hash": "a" * 64,
    }


def test_ui_pipe_action_returns_failed_handshake_without_command() -> None:
    client = _FakeGuiIpcClient(handshake=IpcResponse.rejected(IpcReason.CLIENT_IDENTITY_MISMATCH))
    args = build_parser().parse_args(["--pipe-name", "pipe-a", "--submit-command", "UNKNOWN"])

    response = _run_pipe_action(args, client)

    assert response.reason is IpcReason.CLIENT_IDENTITY_MISMATCH
    assert client.calls == ("connect",)


def test_ui_pipe_action_defaults_to_handshake_response() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(["--pipe-name", "pipe-a"])

    response = _run_pipe_action(args, client)

    assert response.payload == {"handshake": "ok"}
    assert client.calls == ("connect",)


def test_ui_client_parser_rejects_query_and_submit_command_together() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["--pipe-name", "pipe-a", "--query-status", "--submit-command", "UNKNOWN"]
        )


def test_parse_payload_json_requires_object() -> None:
    assert _parse_payload_json(None) is None
    assert _parse_payload_json('{"a":1}') == {"a": 1}
    with pytest.raises(IpcProtocolError, match="command payload JSON must be an object"):
        _parse_payload_json("[]")
    with pytest.raises(IpcProtocolError, match="command payload JSON must be valid"):
        _parse_payload_json("{")


class _FakeGuiIpcClient:
    def __init__(self, *, handshake: IpcResponse | None = None) -> None:
        self._handshake = handshake or IpcResponse.accepted({"handshake": "ok"})
        self.calls: tuple[str, ...] = ()
        self.submitted: dict[str, object] | None = None
        self.overview_query: dict[str, object] | None = None
        self.activity_query: dict[str, object] | None = None

    def connect(self) -> IpcResponse:
        self.calls = (*self.calls, "connect")
        return self._handshake

    def query_status(self) -> IpcResponse:
        self.calls = (*self.calls, "query_status")
        return IpcResponse.accepted({"status": "ready"})

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_backup_overview")
        self.overview_query = {
            "draft_id": draft_id,
            "limit": limit,
            "offset": offset,
        }
        return IpcResponse.accepted({"overview": "ok"})

    def query_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_activity_overview")
        self.activity_query = {
            "job_id": job_id,
            "limit": limit,
            "offset": offset,
        }
        return IpcResponse.accepted({"activity": "ok"})

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
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "payload_hash": payload_hash,
        }
        return IpcResponse.accepted({"command": command_name})
