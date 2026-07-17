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
    return parser


def run_ui(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
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
