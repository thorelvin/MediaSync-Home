from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home GUI client role")
    parser.add_argument("--pipe-name", help="connect to an Engine Host local named pipe")
    parser.add_argument("--query-status", action="store_true")
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
    handshake = client.connect()
    response = client.query_status() if args.query_status else handshake
    output(json.dumps(response.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if response.reason is None else 2


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
