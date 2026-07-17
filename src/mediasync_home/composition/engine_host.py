from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from mediasync_home.application.runtime_status import startup_status
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home Engine Host")
    parser.add_argument("--pipe-name", help="serve non-mutating IPC over this local named pipe")
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--serve-requests", type=int, default=0)
    return parser


def run_engine_host(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pipe_name:
        return run_role(ProcessRole.ENGINE_HOST, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe Engine Host mode is Windows-only")

    from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeServer

    output = emit or print
    service_status = startup_status(ProcessRole.ENGINE_HOST).to_dict()
    output(
        json.dumps(
            {
                "event": "ENGINE_HOST_PIPE_STARTING",
                "pipe_name": args.pipe_name,
                "serve_requests": args.serve_requests,
                "host_status": service_status,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    server = Win32NamedPipeServer(pipe_name=args.pipe_name)
    requests = max(args.serve_requests, 1)
    for _ in range(requests):
        server.serve_once()
    output(
        json.dumps(
            {
                "event": "ENGINE_HOST_PIPE_STOPPED",
                "pipe_name": args.pipe_name,
                "served_requests": requests,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0
