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
            "--host-mutex-name",
            "Local\\MediaSyncHome-0B-1234567890abcdef12345678",
            "--publish-host-locator",
            "--inactive-outbox-owner-instance-id",
            "host-old",
        ]
    )

    assert args.state_root == tmp_path
    assert args.host_mutex_name == "Local\\MediaSyncHome-0B-1234567890abcdef12345678"
    assert args.publish_host_locator is True
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
        assert runtime.service.standard_backup_job_detail_store is not None
        assert runtime.service.snapshot_entry_read_store is not None
        assert runtime.service.snapshot_coverage_read_store is not None
        assert runtime.service.snapshot_issue_read_store is not None
        assert runtime.service.plan_store is not None
        assert runtime.service.plan_operation_read_store is not None
        assert runtime.service.plan_endpoint_read_store is not None
        assert runtime.service.run_activity_read_store is not None
        assert runtime.service.schedule_store is not None
        assert runtime.service.trigger_occurrence_store is not None
        assert runtime.run_executor_lease_authority is not None
        assert runtime.run_executor_catalog_handoff_store is not None
        assert current_schema_version(runtime.catalog_connection, SqliteStore.CATALOG) == 20
        assert current_schema_version(runtime.recovery_connection, SqliteStore.RECOVERY) == 5
        assert runtime.startup_reconciliation is not None
        assert runtime.startup_reconciliation.reconciler_instance_id == "host-new"
        assert runtime.startup_reconciliation.recovery_operations is not None
        assert runtime.startup_reconciliation.recovery_operations.scanned == 0
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
        backup_job_detail = ipc_client.query_backup_job_detail(job_id="job-a")
        activity = ipc_client.query_activity_overview(limit=5)
        plan_operations = ipc_client.query_plan_operations(plan_id="plan-a", limit=5)
        plan_endpoints = ipc_client.query_plan_endpoints(plan_id="plan-a", limit=5)
        snapshot_entries = ipc_client.query_snapshot_entries(snapshot_id="snapshot-a", limit=5)
        snapshot_coverage = ipc_client.query_snapshot_coverage(
            snapshot_id="snapshot-a",
            limit=5,
        )
        snapshot_issues = ipc_client.query_snapshot_issues(snapshot_id="snapshot-a", limit=5)

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
        assert backup_job_detail.status is IpcStatus.ACCEPTED
        assert backup_job_detail.payload["backup_job_detail"]["read_model_available"] is True
        assert backup_job_detail.payload["backup_job_detail"]["found"] is False
        assert backup_job_detail.payload["backup_job_detail"]["job"] is None
        assert activity.status is IpcStatus.ACCEPTED
        assert activity.payload["activity_overview"]["read_model_available"] is True
        assert activity.payload["activity_overview"]["limit"] == 5
        assert activity.payload["activity_overview"]["runs"] == []
        assert plan_operations.status is IpcStatus.ACCEPTED
        assert plan_operations.payload["plan_operations"]["read_model_available"] is True
        assert plan_operations.payload["plan_operations"]["limit"] == 5
        assert plan_operations.payload["plan_operations"]["operations"] == []
        assert plan_endpoints.status is IpcStatus.ACCEPTED
        assert plan_endpoints.payload["plan_endpoints"]["read_model_available"] is True
        assert plan_endpoints.payload["plan_endpoints"]["limit"] == 5
        assert plan_endpoints.payload["plan_endpoints"]["endpoints"] == []
        assert snapshot_entries.status is IpcStatus.ACCEPTED
        assert snapshot_entries.payload["snapshot_entries"]["read_model_available"] is True
        assert snapshot_entries.payload["snapshot_entries"]["limit"] == 5
        assert snapshot_entries.payload["snapshot_entries"]["entries"] == []
        assert snapshot_coverage.status is IpcStatus.ACCEPTED
        assert snapshot_coverage.payload["snapshot_coverage"]["read_model_available"] is True
        assert snapshot_coverage.payload["snapshot_coverage"]["limit"] == 5
        assert snapshot_coverage.payload["snapshot_coverage"]["coverage"] == []
        assert snapshot_issues.status is IpcStatus.ACCEPTED
        assert snapshot_issues.payload["snapshot_issues"]["read_model_available"] is True
        assert snapshot_issues.payload["snapshot_issues"]["limit"] == 5
        assert snapshot_issues.payload["snapshot_issues"]["issues"] == []
        assert response.status is IpcStatus.REJECTED
        assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
        assert row == (
            "REJECTED",
            IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
    finally:
        runtime.close()


def test_engine_host_runtime_releases_executor_leases_on_close(tmp_path: Path) -> None:
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=startup_status(ProcessRole.ENGINE_HOST),
        state_root=tmp_path / "state",
        reconciler_instance_id="host-new",
    )
    closed = False
    try:
        assert runtime.run_executor_lease_registry is not None
        lease = _FakeLiveLease()
        runtime.run_executor_lease_registry.retain_run_target_lease(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            lease=lease,
        )

        runtime.close()
        closed = True

        assert lease.released is True
        assert runtime.run_executor_lease_registry.retained_count == 0
    finally:
        if not closed:
            runtime.close()


class _FakePipeServer:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self._fail_on_call = fail_on_call

    def serve_once(self) -> None:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise RuntimeError("internal detail must not leak")


class _FakeLiveLease:
    lease_id = "lease-a"
    owner_installation_id = "owner-a"
    ownership_epoch = 1
    fencing_token = 42

    def __init__(self) -> None:
        self.released = False

    def issue_mutation_permit(self) -> object:
        raise AssertionError("permit issuance is not used by this test")

    def release(self) -> None:
        self.released = True


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
