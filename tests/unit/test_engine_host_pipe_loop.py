from __future__ import annotations

from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import SqliteStore
from mediasync_home.adapters.sqlite.migrations import current_schema_version
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.composition.engine_host import (
    build_engine_host_runtime,
    build_parser,
    serve_bounded_pipe_requests,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import IpcReason, IpcStatus


EXPECTED_USER = "same-user"
EXPECTED_SESSION = 42


def test_bounded_pipe_loop_serves_exact_request_limit() -> None:
    server = _FakePipeServer()

    result = serve_bounded_pipe_requests(server, request_limit=3)

    assert result.completed is True
    assert result.error_type is None
    assert result.served_requests == 3
    assert server.calls == 3


def test_bounded_pipe_loop_reports_sanitized_failure() -> None:
    server = _FakePipeServer(fail_on_call=2)

    result = serve_bounded_pipe_requests(server, request_limit=4)

    assert result.completed is False
    assert result.error_type == "RuntimeError"
    assert result.served_requests == 1
    assert server.calls == 2


def test_engine_host_parser_requires_positive_serve_request_limit() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--serve-requests", "0"])


def test_engine_host_parser_accepts_optional_state_root_and_inactive_outbox_owner(
    tmp_path: Path,
) -> None:
    args = build_parser().parse_args(
        [
            "--pipe-name",
            "pipe-a",
            "--state-root",
            str(tmp_path),
            "--inactive-outbox-owner-instance-id",
            "host-old",
        ]
    )

    assert args.state_root == tmp_path
    assert args.inactive_outbox_owner_instance_id == ["host-old"]


def test_engine_host_runtime_without_state_root_preserves_non_persistent_service() -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
    )

    try:
        assert runtime.state_layout is None
        assert runtime.startup_reconciliation is None
        assert runtime.catalog_connection is None
        assert runtime.recovery_connection is None
        assert runtime.service.command_receipt_store is None
        assert runtime.service.outbox_store is None
    finally:
        runtime.close()


def test_engine_host_runtime_state_root_initializes_sqlite_and_persists_receipts(
    tmp_path: Path,
) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        reconciler_instance_id="host-new",
    )

    try:
        assert runtime.state_layout is not None
        assert runtime.state_layout.catalog.is_file()
        assert runtime.state_layout.recovery.is_file()
        assert runtime.catalog_connection is not None
        assert runtime.recovery_connection is not None
        assert runtime.service.job_draft_store is not None
        assert runtime.service.standard_backup_job_read_store is not None
        assert current_schema_version(runtime.catalog_connection, SqliteStore.CATALOG) == 17
        assert current_schema_version(runtime.recovery_connection, SqliteStore.RECOVERY) == 5
        assert runtime.startup_reconciliation is not None
        assert runtime.startup_reconciliation.reconciler_instance_id == "host-new"
        assert runtime.startup_reconciliation.skipped_outbox_requeue_reason == (
            "OUTBOX_RECONCILIATION_SKIPPED_NO_INACTIVE_OWNER_PROOF"
        )

        ipc_client = InProcessIpcClient(
            service=runtime.service,
            identity=_identity(),
            role=ProcessRole.GUI,
            client_instance_id="55555555-5555-4555-8555-555555555555",
        )
        assert ipc_client.connect().status is IpcStatus.ACCEPTED
        runtime.service.job_draft_store.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-a")
            .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
            .with_added_target(name="USB 1", path_label="E:/Backup")
        )

        overview = ipc_client.query_backup_overview(draft_id="draft-a")

        response = ipc_client.submit_command(
            "UNKNOWN_MUTATION",
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="66666666-6666-4666-8666-666666666666",
        )

        row = runtime.catalog_connection.execute(
            """
            SELECT state, rejection_reason
            FROM command_receipts
            WHERE idempotency_key = ?
            """,
            ("66666666-6666-4666-8666-666666666666",),
        ).fetchone()
        assert overview.status is IpcStatus.ACCEPTED
        assert overview.payload["backup_overview"]["read_model_available"] is True
        assert overview.payload["backup_overview"]["draft"]["can_create"] is True
        assert response.status is IpcStatus.REJECTED
        assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
        assert row == (
            "REJECTED",
            IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
    finally:
        runtime.close()


class _FakePipeServer:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self._fail_on_call = fail_on_call

    def serve_once(self) -> None:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise RuntimeError("internal detail must not leak")


def _authorization() -> ClientAuthorizationPolicy:
    return ClientAuthorizationPolicy(
        expected_user_sid_hash=EXPECTED_USER,
        expected_session_id=EXPECTED_SESSION,
    )


def _identity() -> VerifiedClientIdentity:
    return VerifiedClientIdentity(
        user_sid_hash=EXPECTED_USER,
        session_id=EXPECTED_SESSION,
        is_remote=False,
        transport="in-process-engine-host-runtime-test",
    )
