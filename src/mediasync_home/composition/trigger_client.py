from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mediasync_home.application.host_locator import (
    LocalEngineHostDescriptor,
    LocalEngineHostPublication,
)
from mediasync_home.application.trigger_occurrences import (
    TriggerCommandName,
    TriggerDeliveryContext,
    TriggerKind,
    TriggerOccurrencePayloadError,
    build_enqueue_trigger_occurrence_payload,
    payload_hash,
)
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason, IpcResponse


class TriggerIpcClient(Protocol):
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
    parser.add_argument(
        "--enqueue-trigger-occurrence",
        action="store_true",
        help="submit a validated trigger occurrence command to the compatible Engine Host",
    )
    parser.add_argument("--schedule-id")
    parser.add_argument("--schedule-revision-hash")
    parser.add_argument("--delivery-id")
    parser.add_argument("--observed-start-utc")
    parser.add_argument(
        "--trigger-kind",
        choices=tuple(kind.value for kind in TriggerKind),
        default=TriggerKind.SCHEDULED_TIME.value,
    )
    parser.add_argument("--task-definition-hash")
    parser.add_argument("--task-instance-id")
    parser.add_argument("--scheduled-slot-utc")
    parser.add_argument("--event-identity")
    return parser


def run_trigger_client(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    action_count = int(args.query_status) + int(args.enqueue_trigger_occurrence)
    if action_count > 1:
        parser.error("choose only one trigger-client action")
    if action_count == 0:
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
        response = _run_named_pipe_action(args, client)
    except (OSError, TimeoutError):
        if publication is None:
            raise
        response = IpcResponse.rejected(
            IpcReason.ENGINE_HOST_UNAVAILABLE,
            {
                "host_locator_publication": publication.to_payload(),
                "reason": "HOST_LOCATOR_PUBLICATION_NOT_LIVE",
                "scope": "0B_SAME_USER_LOCAL_PREVIEW",
                "stale_host_locator_publication_cleared": False,
            },
        )

    output(json.dumps(response.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if response.reason is None else 2


def _run_named_pipe_action(args: argparse.Namespace, client: TriggerIpcClient) -> IpcResponse:
    if args.query_status:
        return _run_status_query(client)
    return _run_enqueue_trigger_occurrence(args, client)


def _run_status_query(client: TriggerIpcClient) -> IpcResponse:
    handshake = client.connect()
    if handshake.reason is not None:
        return handshake
    return client.query_status()


def _run_enqueue_trigger_occurrence(
    args: argparse.Namespace,
    client: TriggerIpcClient,
) -> IpcResponse:
    try:
        delivery_id = args.delivery_id or str(uuid4())
        schedule_revision_hash = _required_cli_value(
            args.schedule_revision_hash,
            "schedule-revision-hash",
        )
        task_definition_hash = args.task_definition_hash or schedule_revision_hash
        delivery = TriggerDeliveryContext(
            delivery_id=delivery_id,
            observed_start_utc=args.observed_start_utc or _utc_now(),
            trigger_kind=TriggerKind(args.trigger_kind),
            task_definition_hash=task_definition_hash,
            task_instance_id=args.task_instance_id,
            scheduled_slot_utc=args.scheduled_slot_utc,
            event_identity=args.event_identity,
        )
        payload = build_enqueue_trigger_occurrence_payload(
            schedule_id=_required_cli_value(args.schedule_id, "schedule-id"),
            schedule_revision_hash=schedule_revision_hash,
            delivery=delivery,
        )
    except (TriggerOccurrencePayloadError, ValueError):
        return IpcResponse.rejected(
            IpcReason.INVALID_FRAME,
            {"reason": "TRIGGER_OCCURRENCE_PAYLOAD_INVALID"},
        )
    handshake = client.connect()
    if handshake.reason is not None:
        return handshake
    return client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=delivery_id,
        idempotency_key=delivery_id,
        payload=payload,
        payload_hash=payload_hash(payload),
    )


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

    from mediasync_home.adapters.local_host_locator import (
        load_matching_live_local_engine_host_publication,
    )

    try:
        return load_matching_live_local_engine_host_publication(descriptor)
    except (OSError, ValueError):
        return None


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


def _required_cli_value(value: str | None, argument_name: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{argument_name} is required")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
