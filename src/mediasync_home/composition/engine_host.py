from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediasync_home.adapters.runtime_policy import current_process_runtime_policy
from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    StateStoreLayout,
    apply_sqlite_connection_policy,
    build_state_store_layout,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore
from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationReport,
    EngineHostStartupReconciliationRequest,
    reconcile_engine_host_after_startup,
)
from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy
from mediasync_home.ipc.server import EngineHostIpcService


ROOT = Path(__file__).resolve().parents[3]


class PipeServer(Protocol):
    def serve_once(self) -> None:
        pass


@dataclass(frozen=True)
class PipeLoopResult:
    served_requests: int
    completed: bool
    error_type: str | None = None


@dataclass
class EngineHostRuntime:
    service: EngineHostIpcService
    state_layout: StateStoreLayout | None = None
    startup_reconciliation: EngineHostStartupReconciliationReport | None = None
    catalog_connection: sqlite3.Connection | None = None
    recovery_connection: sqlite3.Connection | None = None

    def close(self) -> None:
        if self.catalog_connection is not None:
            self.catalog_connection.close()
        if self.recovery_connection is not None:
            self.recovery_connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home Engine Host")
    parser.add_argument("--pipe-name", help="serve non-mutating IPC over this local named pipe")
    parser.add_argument("--installation-id", default="local-dev")
    parser.add_argument("--serve-requests", type=_positive_int, default=1)
    parser.add_argument("--state-root", type=Path, help="optional local preview state root")
    parser.add_argument(
        "--inactive-outbox-owner-instance-id",
        action="append",
        help="owner instance proven inactive before startup outbox requeue",
    )
    return parser


def run_engine_host(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pipe_name:
        return run_role(ProcessRole.ENGINE_HOST, argv, emit=emit)
    if os.name != "nt":
        raise RuntimeError("named-pipe Engine Host mode is Windows-only")

    from mediasync_home.ipc.win32_named_pipe import Win32NamedPipeServer
    from mediasync_home.ipc.win32_named_pipe import current_user_policy

    output = emit or print
    service_status = startup_status(
        ProcessRole.ENGINE_HOST,
        runtime_policy=current_process_runtime_policy(ROOT),
    )
    runtime = build_engine_host_runtime(
        authorization=current_user_policy(),
        service_status=service_status,
        state_root=args.state_root,
        reconciler_instance_id=args.installation_id,
        inactive_outbox_owner_instance_ids=tuple(args.inactive_outbox_owner_instance_id or ()),
    )
    output(
        json.dumps(
            {
                "event": "ENGINE_HOST_PIPE_STARTING",
                "pipe_name": args.pipe_name,
                "serve_requests": args.serve_requests,
                "startup_reconciliation": _startup_reconciliation_payload(
                    runtime.startup_reconciliation
                ),
                "state_root": None
                if runtime.state_layout is None
                else str(runtime.state_layout.root),
                "host_status": service_status.to_dict(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    server = Win32NamedPipeServer(
        pipe_name=args.pipe_name,
        service=runtime.service,
    )
    try:
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
    finally:
        runtime.close()


def build_engine_host_runtime(
    *,
    authorization: ClientAuthorizationPolicy,
    service_status: RuntimeStatus,
    state_root: Path | None = None,
    reconciler_instance_id: str = "local-dev",
    inactive_outbox_owner_instance_ids: tuple[str, ...] = (),
) -> EngineHostRuntime:
    if state_root is None:
        return EngineHostRuntime(
            service=EngineHostIpcService(
                authorization,
                status=service_status,
            )
        )

    layout = build_state_store_layout(state_root)
    layout.root.mkdir(parents=True, exist_ok=True)
    catalog_connection = sqlite3.connect(layout.catalog)
    recovery_connection = sqlite3.connect(layout.recovery)
    try:
        apply_sqlite_connection_policy(
            catalog_connection,
            catalog_critical_writer_policy(layout.catalog),
        )
        apply_sqlite_connection_policy(
            recovery_connection,
            recovery_writer_policy(layout.recovery),
        )
        apply_sqlite_migrations(catalog_connection, catalog_migration_plan())
        apply_sqlite_migrations(recovery_connection, recovery_migration_plan())
        command_receipts = SqliteCommandReceiptStore(catalog_connection)
        outbox = SqliteOutboxStore(catalog_connection)
        startup_reconciliation = reconcile_engine_host_after_startup(
            EngineHostStartupReconciliationRequest(
                reconciler_instance_id=reconciler_instance_id,
                inactive_outbox_owner_instance_ids=inactive_outbox_owner_instance_ids,
            ),
            command_receipts=command_receipts,
            outbox=outbox,
        )
        service = EngineHostIpcService(
            authorization,
            status=service_status,
            command_receipt_store=command_receipts,
            outbox_store=outbox,
        )
    except Exception:
        catalog_connection.close()
        recovery_connection.close()
        raise
    return EngineHostRuntime(
        service=service,
        state_layout=layout,
        startup_reconciliation=startup_reconciliation,
        catalog_connection=catalog_connection,
        recovery_connection=recovery_connection,
    )


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


def _startup_reconciliation_payload(
    report: EngineHostStartupReconciliationReport | None,
) -> dict[str, object] | None:
    if report is None:
        return None
    command_receipts = None
    if report.command_receipts is not None:
        command_receipts = {
            "scanned": report.command_receipts.scanned,
            "rejected_idempotency_keys": report.command_receipts.rejected_idempotency_keys,
            "pending_effect_reconciliation_keys": (
                report.command_receipts.pending_effect_reconciliation_keys
            ),
        }
    outbox = None
    if report.outbox is not None:
        outbox = {
            "scanned": report.outbox.scanned,
            "requeued_message_ids": report.outbox.requeued_message_ids,
        }
    return {
        "reconciler_instance_id": report.reconciler_instance_id,
        "command_receipts": command_receipts,
        "outbox": outbox,
        "skipped_outbox_requeue_reason": report.skipped_outbox_requeue_reason,
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed
