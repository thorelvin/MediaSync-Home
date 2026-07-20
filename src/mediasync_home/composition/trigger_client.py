from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.application.host_locator import (
    LocalEngineHostDescriptor,
    LocalEngineHostPublication,
    local_engine_host_publication_matches_descriptor,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason, IpcResponse


class TriggerIpcClient(Protocol):
    def connect(self) -> IpcResponse: ...

    def query_status(self) -> IpcResponse: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home trigger-client role")
    parser.add_argument("--pipe-name", help="connect to an Engine Host local named pipe")
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=5.0)
    parser.add_argument(
        "--query-status",
        action="store_true",
        help="probe the compatible Engine Host through a non-mutating status query",
    )
    return parser


def run_trigger_client(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.query_status:
        return run_role(ProcessRole.TRIGGER_CLIENT, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe trigger-client mode is Windows-only")

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
        role=ProcessRole.TRIGGER_CLIENT,
        timeout_ms=int(args.timeout_seconds * 1000),
    )
    try:
        response = _run_status_query(client)
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


def _run_status_query(client: TriggerIpcClient) -> IpcResponse:
    handshake = client.connect()
    if handshake.reason is not None:
        return handshake
    return client.query_status()


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


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed
