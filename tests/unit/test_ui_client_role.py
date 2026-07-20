from __future__ import annotations

import pytest

from mediasync_home.composition.ui import (
    build_parser,
    _parse_after_json,
    _parse_payload_json,
    _pipe_action_requested,
    _run_pipe_action,
)
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


def test_ui_pipe_action_queries_backup_job_detail_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-backup-job-detail",
            "--job-id",
            "job-a",
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"backup_job_detail": "ok"}
    assert client.calls == ("connect", "query_backup_job_detail")
    assert client.backup_job_detail_query == {"job_id": "job-a"}


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


def test_ui_pipe_action_queries_plan_operations_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-plan-operations",
            "--plan-id",
            "plan-a",
            "--limit",
            "5",
            "--after-json",
            (
                '{"execution_phase":10,'
                '"stable_order_key":"010:Pictures/A.jpg",'
                '"operation_id":"op-a"}'
            ),
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"plan_operations": "ok"}
    assert client.calls == ("connect", "query_plan_operations")
    assert client.plan_operations_query == {
        "plan_id": "plan-a",
        "limit": 5,
        "after": {
            "execution_phase": 10,
            "stable_order_key": "010:Pictures/A.jpg",
            "operation_id": "op-a",
        },
    }


def test_ui_pipe_action_queries_plan_endpoints_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-plan-endpoints",
            "--plan-id",
            "plan-a",
            "--limit",
            "5",
            "--after-json",
            '{"role":"SOURCE","target_ordinal":null,"endpoint_id":"source-a"}',
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"plan_endpoints": "ok"}
    assert client.calls == ("connect", "query_plan_endpoints")
    assert client.plan_endpoints_query == {
        "plan_id": "plan-a",
        "limit": 5,
        "after": {
            "role": "SOURCE",
            "target_ordinal": None,
            "endpoint_id": "source-a",
        },
    }


def test_ui_pipe_action_queries_snapshot_entries_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-snapshot-entries",
            "--snapshot-id",
            "snapshot-a",
            "--limit",
            "5",
            "--after-json",
            (
                '{"comparison_key":"010:Pictures/A.jpg",'
                '"relative_path":"Pictures/A.jpg",'
                '"entry_id":"file-a"}'
            ),
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"snapshot_entries": "ok"}
    assert client.calls == ("connect", "query_snapshot_entries")
    assert client.snapshot_entries_query == {
        "snapshot_id": "snapshot-a",
        "limit": 5,
        "after": {
            "comparison_key": "010:Pictures/A.jpg",
            "relative_path": "Pictures/A.jpg",
            "entry_id": "file-a",
        },
    }


def test_ui_pipe_action_queries_snapshot_coverage_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-snapshot-coverage",
            "--snapshot-id",
            "snapshot-a",
            "--limit",
            "5",
            "--after-json",
            '{"comparison_key":"010:Photos","relative_path":"Photos"}',
            "--coverage-state",
            "COMPLETE",
            "--coverage-state",
            "VOLATILE",
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"snapshot_coverage": "ok"}
    assert client.calls == ("connect", "query_snapshot_coverage")
    assert client.snapshot_coverage_query == {
        "snapshot_id": "snapshot-a",
        "limit": 5,
        "after": {
            "comparison_key": "010:Photos",
            "relative_path": "Photos",
        },
        "coverage_states": ("COMPLETE", "VOLATILE"),
    }


def test_ui_pipe_action_queries_snapshot_issues_after_handshake() -> None:
    client = _FakeGuiIpcClient()
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--query-snapshot-issues",
            "--snapshot-id",
            "snapshot-a",
            "--limit",
            "5",
            "--after-json",
            (
                '{"relative_path":"Archive",'
                '"issue_type":"UNREADABLE_DIRECTORY",'
                '"issue_id":1}'
            ),
            "--blocking-only",
        ]
    )

    response = _run_pipe_action(args, client)

    assert response.payload == {"snapshot_issues": "ok"}
    assert client.calls == ("connect", "query_snapshot_issues")
    assert client.snapshot_issues_query == {
        "snapshot_id": "snapshot-a",
        "limit": 5,
        "after": {
            "relative_path": "Archive",
            "issue_type": "UNREADABLE_DIRECTORY",
            "issue_id": 1,
        },
        "blocking_only": True,
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


def test_ui_client_treats_no_pipe_query_as_host_locator_action() -> None:
    args = build_parser().parse_args(
        [
            "--query-status",
            "--installation-id",
            "preview-a",
            "--state-root",
            "C:/Users/Ada/AppData/Local/MediaSyncHome/0b-local-preview/preview-a",
            "--timeout-seconds",
            "1",
        ]
    )

    assert args.pipe_name is None
    assert _pipe_action_requested(args) is True


def test_ui_client_without_pipe_or_query_keeps_generic_role_path() -> None:
    args = build_parser().parse_args([])

    assert args.pipe_name is None
    assert _pipe_action_requested(args) is False


def test_parse_payload_json_requires_object() -> None:
    assert _parse_payload_json(None) is None
    assert _parse_payload_json('{"a":1}') == {"a": 1}
    with pytest.raises(IpcProtocolError, match="command payload JSON must be an object"):
        _parse_payload_json("[]")
    with pytest.raises(IpcProtocolError, match="command payload JSON must be valid"):
        _parse_payload_json("{")


def test_parse_after_json_requires_object() -> None:
    assert _parse_after_json(None) is None
    assert _parse_after_json('{"execution_phase":1}') == {"execution_phase": 1}
    with pytest.raises(IpcProtocolError, match="cursor JSON must be an object"):
        _parse_after_json("[]")
    with pytest.raises(IpcProtocolError, match="cursor JSON must be valid"):
        _parse_after_json("{")


class _FakeGuiIpcClient:
    def __init__(self, *, handshake: IpcResponse | None = None) -> None:
        self._handshake = handshake or IpcResponse.accepted({"handshake": "ok"})
        self.calls: tuple[str, ...] = ()
        self.submitted: dict[str, object] | None = None
        self.overview_query: dict[str, object] | None = None
        self.backup_job_detail_query: dict[str, object] | None = None
        self.activity_query: dict[str, object] | None = None
        self.plan_operations_query: dict[str, object] | None = None
        self.plan_endpoints_query: dict[str, object] | None = None
        self.snapshot_entries_query: dict[str, object] | None = None
        self.snapshot_coverage_query: dict[str, object] | None = None
        self.snapshot_issues_query: dict[str, object] | None = None

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

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        self.calls = (*self.calls, "query_backup_job_detail")
        self.backup_job_detail_query = {"job_id": job_id}
        return IpcResponse.accepted({"backup_job_detail": "ok"})

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

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_plan_operations")
        self.plan_operations_query = {
            "plan_id": plan_id,
            "limit": limit,
            "after": after,
        }
        return IpcResponse.accepted({"plan_operations": "ok"})

    def query_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_plan_endpoints")
        self.plan_endpoints_query = {
            "plan_id": plan_id,
            "limit": limit,
            "after": after,
        }
        return IpcResponse.accepted({"plan_endpoints": "ok"})

    def query_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_snapshot_entries")
        self.snapshot_entries_query = {
            "snapshot_id": snapshot_id,
            "limit": limit,
            "after": after,
        }
        return IpcResponse.accepted({"snapshot_entries": "ok"})

    def query_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_snapshot_coverage")
        self.snapshot_coverage_query = {
            "snapshot_id": snapshot_id,
            "limit": limit,
            "after": after,
            "coverage_states": coverage_states,
        }
        return IpcResponse.accepted({"snapshot_coverage": "ok"})

    def query_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        self.calls = (*self.calls, "query_snapshot_issues")
        self.snapshot_issues_query = {
            "snapshot_id": snapshot_id,
            "limit": limit,
            "after": after,
            "blocking_only": blocking_only,
        }
        return IpcResponse.accepted({"snapshot_issues": "ok"})

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
