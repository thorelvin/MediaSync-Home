from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediasync_home.adapters.runtime_policy import current_process_runtime_policy
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole


ROOT = Path(__file__).resolve().parents[3]


class PipeServer(Protocol):
    def serve_once(self) -> None:
        pass


@dataclass(frozen=True)
class PipeLoopResult:
    served_requests: int
    completed: bool
    error_type: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home Engine Host")
    parser.add_argument("--pipe-name", help="serve non-mutating IPC over this local named pipe")
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--serve-requests", type=_positive_int, default=1)
    return parser


def run_engine_host(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pipe_name:
        return run_role(ProcessRole.ENGINE_HOST, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe Engine Host mode is Windows-only")

    from mediasync_home.ipc.server import EngineHostIpcService
    from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeServer
    from mediasync_home.ipc.win32_named_pipe import current_user_policy

    output = emit or print
    service_status = startup_status(
        ProcessRole.ENGINE_HOST,
        runtime_policy=current_process_runtime_policy(ROOT),
    )
    output(
        json.dumps(
            {
                "event": "ENGINE_HOST_PIPE_STARTING",
                "pipe_name": args.pipe_name,
                "serve_requests": args.serve_requests,
                "host_status": service_status.to_dict(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    server = Win32NamedPipeServer(
        pipe_name=args.pipe_name,
        service=EngineHostIpcService(current_user_policy(), status=service_status),
    )
    result = serve_bounded_pipe_requests(server, request_limit=args.serve_requests)
    if result.completed:
        output(
            json.dumps(
                {
                    "event": "ENGINE_HOST_PIPE_STOPPED",
                    "pipe_name": args.pipe_name,
                    "served_requests": result.served_requests,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    output(
        json.dumps(
            {
                "error_type": result.error_type,
                "event": "ENGINE_HOST_PIPE_FAILED",
                "pipe_name": args.pipe_name,
                "served_requests": result.served_requests,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 2


def serve_bounded_pipe_requests(server: PipeServer, *, request_limit: int) -> PipeLoopResult:
    served_requests = 0
    try:
        for _ in range(request_limit):
            server.serve_once()
            served_requests += 1
    except Exception as exc:
        return PipeLoopResult(
            served_requests=served_requests,
            completed=False,
            error_type=type(exc).__name__,
        )
    return PipeLoopResult(served_requests=served_requests, completed=True)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed
