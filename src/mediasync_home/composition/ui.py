from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from typing import Protocol

from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcProtocolError, IpcResponse


class GuiIpcClient(Protocol):
    def connect(self) -> IpcResponse: ...

    def query_status(self) -> IpcResponse: ...

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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--query-status", action="store_true")
    mode.add_argument("--submit-command", metavar="NAME")
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
    if not args.pipe_name:
        return run_role(ProcessRole.GUI, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe GUI client mode is Windows-only")

    from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeClient

    output = emit or print
    client = Win32NamedPipeClient(pipe_name=args.pipe_name, role=ProcessRole.GUI)
    response = _run_pipe_action(args, client)
    output(json.dumps(response.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if response.reason is None else 2


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
    if args.query_status:
        return client.query_status()
    return handshake


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


def _run_qt_shell(args: argparse.Namespace) -> int:
    engine_client = None
    if args.pipe_name:
        if os.name != "nt":
            raise RuntimeError("named-pipe GUI client mode is Windows-only")

        from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeClient
        from mediasync_home.presentation.engine_client import EngineClient

        engine_client = EngineClient(
            Win32NamedPipeClient(pipe_name=args.pipe_name, role=ProcessRole.GUI)
        )

    from mediasync_home.presentation.app import run_gui
    from mediasync_home.presentation.theme.theme_manager import ThemeMode

    return run_gui([], engine_client=engine_client, theme_mode=ThemeMode(args.theme))
