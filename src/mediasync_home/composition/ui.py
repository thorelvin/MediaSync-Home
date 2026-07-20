from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.application.host_locator import (
    LocalEngineHostDescriptor,
    LocalEngineHostPublication,
    local_engine_host_publication_matches_descriptor,
)
from mediasync_home.ipc.protocol import IpcProtocolError, IpcReason, IpcResponse


class GuiIpcClient(Protocol):
    def connect(self) -> IpcResponse: ...

    def query_status(self) -> IpcResponse: ...

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse: ...

    def query_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse: ...

    def query_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse: ...

    def query_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse: ...

    def query_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse: ...

    def query_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse: ...

    def query_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...

    def submit_command(
        self,
        command_name: str,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
        payload_hash: str | None = None,
    ) -> IpcResponse: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home GUI client role")
    parser.add_argument("--pipe-name", help="connect to an Engine Host local named pipe")
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=5.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--query-status", action="store_true")
    mode.add_argument("--query-backup-overview", action="store_true")
    mode.add_argument("--query-backup-job-detail", action="store_true")
    mode.add_argument("--query-activity-overview", action="store_true")
    mode.add_argument("--query-plan-operations", action="store_true")
    mode.add_argument("--query-plan-endpoints", action="store_true")
    mode.add_argument("--query-snapshot-entries", action="store_true")
    mode.add_argument("--query-snapshot-coverage", action="store_true")
    mode.add_argument("--query-snapshot-issues", action="store_true")
    mode.add_argument("--query-cataloged-files", action="store_true")
    mode.add_argument("--submit-command", metavar="NAME")
    parser.add_argument("--draft-id")
    parser.add_argument("--job-id")
    parser.add_argument("--plan-id")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--run-id")
    parser.add_argument("--target-endpoint-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int)
    parser.add_argument("--after-json")
    parser.add_argument("--coverage-state", action="append")
    parser.add_argument("--blocking-only", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--payload-json")
    parser.add_argument("--payload-hash")
    parser.add_argument("--qt-shell", action="store_true", help="start the native PySide GUI shell")
    parser.add_argument("--theme", choices=("light", "dark", "system"), default="system")
    return parser


def run_ui(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.qt_shell:
        return _run_qt_shell(args)
    if not args.pipe_name and not _pipe_action_requested(args):
        return run_role(ProcessRole.GUI, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe GUI client mode is Windows-only")

    from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeClient

    output = emit or print
    publication = None
    pipe_name = args.pipe_name
    if pipe_name is None:
        publication = _load_matching_local_preview_publication(args)
        if publication is None:
            response = IpcResponse.rejected(
                IpcReason.ENGINE_HOST_UNAVAILABLE,
                {
                    "reason": "HOST_LOCATOR_PUBLICATION_UNAVAILABLE",
                    "scope": "0B_SAME_USER_LOCAL_PREVIEW",
                },
            )
            output(json.dumps(response.to_dict(), sort_keys=True, separators=(",", ":")))
            return 2
        pipe_name = publication.pipe_name

    assert pipe_name is not None
    client = Win32NamedPipeClient(
        pipe_name=pipe_name,
        role=ProcessRole.GUI,
        timeout_ms=int(args.timeout_seconds * 1000),
    )
    try:
        response = _run_pipe_action(args, client)
    except (OSError, TimeoutError):
        if publication is None:
            raise
        response = IpcResponse.rejected(
            IpcReason.ENGINE_HOST_UNAVAILABLE,
            {
                "host_locator_publication": publication.to_payload(),
                "reason": "HOST_LOCATOR_PUBLICATION_NOT_LIVE",
                "scope": "0B_SAME_USER_LOCAL_PREVIEW",
                "stale_host_locator_publication_cleared": _clear_stale_host_publication(
                    publication
                ),
            },
        )
    output(json.dumps(response.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if response.reason is None else 2


def _pipe_action_requested(args: argparse.Namespace) -> bool:
    return (
        args.query_status
        or args.query_backup_overview
        or args.query_backup_job_detail
        or args.query_activity_overview
        or args.query_plan_operations
        or args.query_plan_endpoints
        or args.query_snapshot_entries
        or args.query_snapshot_coverage
        or args.query_snapshot_issues
        or args.query_cataloged_files
        or args.submit_command is not None
    )


def _run_pipe_action(args: argparse.Namespace, client: GuiIpcClient) -> IpcResponse:
    handshake = client.connect()
    if handshake.reason is not None:
        return handshake
    if args.submit_command is not None:
        return client.submit_command(
            args.submit_command,
            request_id=args.request_id,
            idempotency_key=args.idempotency_key,
            payload=_parse_payload_json(args.payload_json),
            payload_hash=args.payload_hash,
        )
    if args.query_backup_overview:
        return client.query_backup_overview(
            draft_id=args.draft_id,
            limit=args.limit,
            offset=args.offset,
        )
    if args.query_backup_job_detail:
        return client.query_backup_job_detail(job_id=args.job_id or "")
    if args.query_activity_overview:
        return client.query_activity_overview(
            job_id=args.job_id,
            limit=args.limit,
            offset=args.offset,
        )
    if args.query_plan_operations:
        return client.query_plan_operations(
            plan_id=args.plan_id or "",
            limit=args.limit,
            after=_parse_after_json(args.after_json),
        )
    if args.query_plan_endpoints:
        return client.query_plan_endpoints(
            plan_id=args.plan_id or "",
            limit=args.limit,
            after=_parse_after_json(args.after_json),
        )
    if args.query_snapshot_entries:
        return client.query_snapshot_entries(
            snapshot_id=args.snapshot_id or "",
            limit=args.limit,
            after=_parse_after_json(args.after_json),
        )
    if args.query_snapshot_coverage:
        return client.query_snapshot_coverage(
            snapshot_id=args.snapshot_id or "",
            limit=args.limit,
            after=_parse_after_json(args.after_json),
            coverage_states=tuple(args.coverage_state or ()),
        )
    if args.query_snapshot_issues:
        return client.query_snapshot_issues(
            snapshot_id=args.snapshot_id or "",
            limit=args.limit,
            after=_parse_after_json(args.after_json),
            blocking_only=args.blocking_only,
        )
    if args.query_cataloged_files:
        return client.query_cataloged_files(
            run_id=args.run_id,
            target_endpoint_id=args.target_endpoint_id,
            limit=args.limit,
            offset=args.offset,
        )
    if args.query_status:
        return client.query_status()
    return handshake


def _load_matching_local_preview_publication(
    args: argparse.Namespace,
) -> LocalEngineHostPublication | None:
    from mediasync_home.adapters.local_host_locator import (
        build_local_engine_host_descriptor_for_user,
    )
    from mediasync_home.ipc.win32_named_pipe import current_process_identity

    identity = current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=args.installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=args.state_root,
        environ=os.environ,
    )
    return _load_matching_publication_for_descriptor(descriptor)


def _load_matching_publication_for_descriptor(
    descriptor: LocalEngineHostDescriptor,
) -> LocalEngineHostPublication | None:
    if descriptor.state_root is None:
        return None

    from mediasync_home.adapters.local_host_locator import load_local_engine_host_publication

    try:
        publication = load_local_engine_host_publication(descriptor.state_root)
    except (OSError, ValueError):
        return None
    if publication is None:
        return None
    if not local_engine_host_publication_matches_descriptor(publication, descriptor):
        return None
    return publication


def _clear_stale_host_publication(
    publication: LocalEngineHostPublication,
) -> bool:
    from mediasync_home.adapters.local_host_locator import (
        clear_stale_local_engine_host_publication,
    )

    try:
        return clear_stale_local_engine_host_publication(publication)
    except OSError:
        return False


def _parse_payload_json(payload_json: str | None) -> dict[str, object] | None:
    if payload_json is None:
        return None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise IpcProtocolError("command payload JSON must be valid") from exc
    if not isinstance(payload, dict):
        raise IpcProtocolError("command payload JSON must be an object")
    if any(not isinstance(key, str) for key in payload):
        raise IpcProtocolError("command payload JSON object keys must be strings")
    return dict(payload)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _parse_after_json(after_json: str | None) -> dict[str, object] | None:
    if after_json is None:
        return None
    try:
        payload = json.loads(after_json)
    except json.JSONDecodeError as exc:
        raise IpcProtocolError("cursor JSON must be valid") from exc
    if not isinstance(payload, dict):
        raise IpcProtocolError("cursor JSON must be an object")
    if any(not isinstance(key, str) for key in payload):
        raise IpcProtocolError("cursor JSON object keys must be strings")
    return dict(payload)


def _run_qt_shell(args: argparse.Namespace) -> int:
    engine_client = None
    pipe_name = _resolve_qt_shell_pipe_name(args)
    if pipe_name is not None:
        if os.name != "nt":
            raise RuntimeError("named-pipe GUI client mode is Windows-only")

        from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeClient
        from mediasync_home.presentation.engine_client import EngineClient

        engine_client = EngineClient(
            Win32NamedPipeClient(
                pipe_name=pipe_name,
                role=ProcessRole.GUI,
                timeout_ms=int(args.timeout_seconds * 1000),
            )
        )

    from mediasync_home.presentation.app import run_gui
    from mediasync_home.presentation.theme.theme_manager import ThemeMode

    return run_gui([], engine_client=engine_client, theme_mode=ThemeMode(args.theme))


def _resolve_qt_shell_pipe_name(args: argparse.Namespace) -> str | None:
    explicit_pipe_name = args.pipe_name
    if isinstance(explicit_pipe_name, str) and explicit_pipe_name:
        return explicit_pipe_name
    if os.name != "nt":
        return None
    publication = _load_matching_local_preview_publication(args)
    if publication is None:
        return None
    return publication.pipe_name
