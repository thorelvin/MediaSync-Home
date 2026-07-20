from __future__ import annotations

import sys
from enum import Enum
from types import ModuleType

import pytest

import mediasync_home.composition.ui as ui_module
from mediasync_home.composition.ui import (
    build_parser,
    _parse_after_json,
    _parse_payload_json,
    _pipe_action_requested,
    _resolve_qt_shell_pipe_name,
    _run_pipe_action,
    _run_qt_shell,
)
from mediasync_home.domain.process_roles import ProcessRole
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


def test_qt_shell_uses_explicit_pipe_name_without_host_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fail_if_called(args: object) -> None:
        calls.append(args)
        raise AssertionError("HostLocator should not be consulted for an explicit pipe")

    monkeypatch.setattr(ui_module, "_load_matching_local_preview_publication", fail_if_called)
    args = build_parser().parse_args(["--qt-shell", "--pipe-name", "pipe-a"])

    assert _resolve_qt_shell_pipe_name(args) == "pipe-a"
    assert calls == []


def test_qt_shell_uses_matching_host_locator_when_pipe_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Publication:
        pipe_name = "pipe-from-host-locator"

    monkeypatch.setattr(ui_module.os, "name", "nt")
    monkeypatch.setattr(
        ui_module,
        "_load_matching_local_preview_publication",
        lambda args: Publication(),
    )
    args = build_parser().parse_args(["--qt-shell", "--installation-id", "preview-a"])

    assert _resolve_qt_shell_pipe_name(args) == "pipe-from-host-locator"


def test_qt_shell_without_pipe_or_publication_starts_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_module.os, "name", "nt")
    monkeypatch.setattr(ui_module, "_load_matching_local_preview_publication", lambda args: None)
    args = build_parser().parse_args(["--qt-shell", "--installation-id", "preview-a"])

    assert _resolve_qt_shell_pipe_name(args) is None


def test_qt_shell_wires_host_locator_publication_to_engine_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Publication:
        pipe_name = "pipe-from-host-locator"

    class FakeThemeMode(str, Enum):
        LIGHT = "light"
        DARK = "dark"
        SYSTEM = "system"

    class FakeWin32NamedPipeClient:
        instances: list["FakeWin32NamedPipeClient"] = []

        def __init__(self, *, pipe_name: str, role: ProcessRole, timeout_ms: int) -> None:
            self.pipe_name = pipe_name
            self.role = role
            self.timeout_ms = timeout_ms
            self.instances.append(self)

        def connect(self) -> IpcResponse:
            return IpcResponse.accepted({"handshake": "ok"})

        def query_status(self) -> IpcResponse:
            return IpcResponse.accepted({"status": "ok"})

    captured: dict[str, object] = {}
    app_module = ModuleType("mediasync_home.presentation.app")

    def fake_run_gui(
        argv: list[str],
        *,
        engine_client: object | None = None,
        theme_mode: FakeThemeMode = FakeThemeMode.SYSTEM,
    ) -> int:
        captured["argv"] = argv
        captured["engine_client"] = engine_client
        captured["theme_mode"] = theme_mode
        return 0

    app_module.run_gui = fake_run_gui  # type: ignore[attr-defined]
    theme_module = ModuleType("mediasync_home.presentation.theme.theme_manager")
    theme_module.ThemeMode = FakeThemeMode  # type: ignore[attr-defined]

    from mediasync_home.ipc import win32_named_pipe

    monkeypatch.setattr(ui_module.os, "name", "nt")
    monkeypatch.setattr(
        ui_module,
        "_load_matching_local_preview_publication",
        lambda args: Publication(),
    )
    monkeypatch.setattr(win32_named_pipe, "Win32NamedPipeClient", FakeWin32NamedPipeClient)
    monkeypatch.setitem(sys.modules, "mediasync_home.presentation.app", app_module)
    monkeypatch.setitem(
        sys.modules,
        "mediasync_home.presentation.theme.theme_manager",
        theme_module,
    )
    args = build_parser().parse_args(
        [
            "--qt-shell",
            "--installation-id",
            "preview-a",
            "--timeout-seconds",
            "1.5",
            "--theme",
            "light",
        ]
    )

    assert _run_qt_shell(args) == 0

    assert captured["argv"] == []
    assert captured["theme_mode"] is FakeThemeMode.LIGHT
    assert captured["engine_client"] is not None
    assert len(FakeWin32NamedPipeClient.instances) == 1
    client = FakeWin32NamedPipeClient.instances[0]
    assert client.pipe_name == "pipe-from-host-locator"
    assert client.role is ProcessRole.GUI
    assert client.timeout_ms == 1500


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
