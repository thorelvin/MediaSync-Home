from __future__ import annotations

import os
import struct
import threading
from collections.abc import Callable
from time import monotonic
from typing import TypeVar
from uuid import uuid4

import pytest

from mediasync_home.application.selected_directory_identity import (
    SelectedDirectoryProbeEvidence,
    StorageIdentityTrust,
)
from mediasync_home.application.duplicate_scanning import (
    DuplicateGroupCursor,
    DuplicateGroupPage,
    DuplicateGroupReadModel,
    DuplicateMemberCursor,
    DuplicateMemberPage,
    DuplicateMemberReadModel,
    DuplicateReportCursor,
    DuplicateReportPage,
    DuplicateReportRow,
    DuplicateScanStage,
    DuplicateScanState,
    DuplicateScanStatus,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import (
    MAX_FRAME_BYTES,
    MAX_QUERY_RESPONSE_BYTES,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    IpcReason,
    IpcResponse,
    IpcStatus,
)
from mediasync_home.ipc.server import EngineHostIpcService, IpcResourceLimits


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Win32 named-pipe adapter is Windows-only"
)


if os.name == "nt":
    from mediasync_home.ipc import win32_named_pipe


_T = TypeVar("_T")


def _server_and_client(
    role: ProcessRole = ProcessRole.GUI,
) -> tuple[
    win32_named_pipe.Win32NamedPipeServer,
    win32_named_pipe.Win32NamedPipeClient,
]:
    service = EngineHostIpcService(win32_named_pipe.current_user_policy())
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="integration-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(pipe_name=pipe_name, service=service)
    client = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name, role=role)
    return server, client


def _roundtrip(
    server: win32_named_pipe.Win32NamedPipeServer,
    action: Callable[[], _T],
) -> _T:
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.serve_once()
        except BaseException as exc:  # pragma: no cover - re-raised in test thread
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    response = action()
    thread.join(timeout=5)
    assert not thread.is_alive(), "named-pipe server did not finish one request"
    if errors:
        raise errors[0]
    return response


def test_named_pipe_handshake_uses_impersonated_client_identity_not_payload_claim() -> (
    None
):
    server, client = _server_and_client()
    expected_identity = win32_named_pipe.current_process_identity()

    response = _roundtrip(
        server,
        lambda: client.connect(
            claimed_user_sid_hash="payload-identity-must-not-authorize"
        ),
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.reason is None
    assert response.request_id is not None
    assert response.payload["verified_user_sid_hash"] == expected_identity.user_sid_hash
    assert response.payload["host_status"]["mutations_enabled"] is False


def test_named_pipe_status_query_requires_successful_handshake() -> None:
    server, client = _server_and_client()

    response = _roundtrip(server, client.query_status)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_named_pipe_status_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    status = _roundtrip(server, client.query_status)

    assert handshake.status is IpcStatus.ACCEPTED
    assert status.status is IpcStatus.ACCEPTED
    assert handshake.request_id is not None
    assert status.request_id is not None
    assert handshake.request_id != status.request_id
    assert status.payload["host_status"]["role"] == ProcessRole.ENGINE_HOST.value


def test_named_pipe_selected_directory_identity_query_roundtrips() -> None:
    service = EngineHostIpcService(win32_named_pipe.current_user_policy())
    service.selected_directory_identity_probe = _SelectedDirectoryIdentityProbe()
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="directory-identity-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(
        pipe_name=pipe_name,
        service=service,
    )
    client = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name)

    handshake = _roundtrip(server, client.connect)
    response = _roundtrip(
        server,
        lambda: client.query_selected_directory_identities(
            path_labels=("C:/Pictures", "C:/Backup")
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert response.status is IpcStatus.ACCEPTED
    identities = response.payload["selected_directory_identities"]
    assert identities["relationships"][0]["kind"] == "SAME_PHYSICAL_DEVICE"


class _SelectedDirectoryIdentityProbe:
    def inspect_directory(self, path_label: str) -> SelectedDirectoryProbeEvidence:
        suffix = path_label.rsplit("/", 1)[-1]
        return SelectedDirectoryProbeEvidence(
            object_identity_key=f"object:{suffix}",
            final_path=rf"\\?\Volume{{shared}}\{suffix}",
            storage_identity_key="disk-a",
            storage_identity_trust=StorageIdentityTrust.CONFIRMED,
        )


def test_named_pipe_backup_overview_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    overview = _roundtrip(server, client.query_backup_overview)

    assert handshake.status is IpcStatus.ACCEPTED
    assert overview.status is IpcStatus.ACCEPTED
    assert overview.payload["backup_overview"]["read_model_available"] is False


def test_named_pipe_backup_job_detail_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    detail = _roundtrip(server, lambda: client.query_backup_job_detail(job_id="job-a"))

    assert handshake.status is IpcStatus.ACCEPTED
    assert detail.status is IpcStatus.ACCEPTED
    assert detail.payload["backup_job_detail"]["job_id"] == "job-a"
    assert detail.payload["backup_job_detail"]["read_model_available"] is False
    assert detail.payload["backup_job_detail"]["found"] is False


def test_named_pipe_duplicate_scan_queries_roundtrip_after_handshake() -> None:
    service = EngineHostIpcService(win32_named_pipe.current_user_policy())
    service.duplicate_scan_store = _DuplicateScanReadStore()
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="duplicate-scan-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(
        pipe_name=pipe_name,
        service=service,
    )
    client = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name)

    handshake = _roundtrip(server, client.connect)
    status = _roundtrip(
        server,
        lambda: client.query_duplicate_scan(analysis_id="analysis-a"),
    )
    groups = _roundtrip(
        server,
        lambda: client.query_duplicate_groups(
            analysis_id="analysis-a",
            limit=1,
        ),
    )
    members = _roundtrip(
        server,
        lambda: client.query_duplicate_members(group_id="group-a", limit=1),
    )
    report = _roundtrip(
        server,
        lambda: client.query_duplicate_report(analysis_id="analysis-a", limit=1),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert status.status is IpcStatus.ACCEPTED
    assert status.payload["duplicate_scan"]["scan"]["state"] == "COMPLETED"
    assert groups.status is IpcStatus.ACCEPTED
    assert groups.payload["duplicate_groups"]["groups"][0]["group_id"] == ("group-a")
    assert members.status is IpcStatus.ACCEPTED
    assert (
        members.payload["duplicate_members"]["members"][0]["relative_path"]
        == "Photos/A.jpg"
    )
    assert report.status is IpcStatus.ACCEPTED
    assert report.payload["duplicate_report"]["rows"][0]["group_id"] == "group-a"
    assert (
        report.payload["duplicate_report"]["rows"][0]["evidence_kind"]
        == "CURRENT_READ_HASH"
    )


class _DuplicateScanReadStore:
    def prepare_scan(self, *, analysis_id: str, observed_utc: str) -> None:
        del analysis_id, observed_utc

    def start_scan(
        self,
        *,
        analysis_id: str,
        requested_utc: str,
    ) -> DuplicateScanStatus:
        del requested_utc
        status = self.load_duplicate_scan(analysis_id)
        assert status is not None
        return status

    def pause_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None:
        del observed_utc
        return self.load_duplicate_scan(analysis_id)

    def resume_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None:
        del observed_utc
        return self.load_duplicate_scan(analysis_id)

    def load_duplicate_scan(self, analysis_id: str) -> DuplicateScanStatus | None:
        return DuplicateScanStatus(
            scan_id="scan-a",
            analysis_id=analysis_id,
            state=DuplicateScanState.COMPLETED,
            stage=DuplicateScanStage.DONE,
            candidate_file_count=2,
            quick_completed_count=2,
            full_hash_candidate_count=2,
            full_hash_completed_count=2,
            issue_count=0,
            requested_utc="2026-08-02T10:00:00Z",
            updated_utc="2026-08-02T10:01:00Z",
            started_utc="2026-08-02T10:00:00Z",
            completed_utc="2026-08-02T10:01:00Z",
        )

    def page_duplicate_groups(
        self,
        *,
        analysis_id: str,
        limit: int,
        after: DuplicateGroupCursor | None = None,
        relationship_classes: tuple[str, ...] = (),
    ) -> DuplicateGroupPage:
        del limit, after, relationship_classes
        return DuplicateGroupPage(
            analysis_id=analysis_id,
            groups=(
                DuplicateGroupReadModel(
                    group_id="group-a",
                    relationship_class="INTRA_ENDPOINT_DUPLICATE",
                    full_hash="a" * 64,
                    size_bytes=4096,
                    member_count=2,
                    physical_object_count=2,
                    expected_replica_count=0,
                    potential_savings_bytes=4096,
                    review_state="UNREVIEWED",
                    created_utc="2026-08-02T10:01:00Z",
                ),
            ),
            next_cursor=None,
            has_more=False,
        )

    def page_duplicate_members(
        self,
        *,
        group_id: str,
        limit: int,
        after: DuplicateMemberCursor | None = None,
    ) -> DuplicateMemberPage:
        del limit, after
        return DuplicateMemberPage(
            group_id=group_id,
            members=(
                DuplicateMemberReadModel(
                    group_id=group_id,
                    snapshot_id="snapshot-a",
                    endpoint_id="source-a",
                    file_entry_id="file-a",
                    relative_path="Photos/A.jpg",
                    member_role="SOURCE_ORIGIN",
                    physical_object_key="physical-a",
                    endpoint_role="SOURCE",
                    absolute_path="C:\\Source\\Photos\\A.jpg",
                    size_bytes=1024,
                    evidence_kind="CURRENT_READ_HASH",
                ),
            ),
            next_cursor=None,
            has_more=False,
        )

    def page_duplicate_report(
        self,
        *,
        analysis_id: str,
        limit: int,
        after: DuplicateReportCursor | None = None,
    ) -> DuplicateReportPage:
        del limit, after
        group = self.page_duplicate_groups(
            analysis_id=analysis_id,
            limit=1,
        ).groups[0]
        member = self.page_duplicate_members(
            group_id=group.group_id,
            limit=1,
        ).members[0]
        return DuplicateReportPage(
            analysis_id=analysis_id,
            rows=(DuplicateReportRow(group=group, member=member),),
            next_cursor=None,
            has_more=False,
        )


def test_named_pipe_activity_overview_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    overview = _roundtrip(server, client.query_activity_overview)

    assert handshake.status is IpcStatus.ACCEPTED
    assert overview.status is IpcStatus.ACCEPTED
    assert overview.payload["activity_overview"]["read_model_available"] is False


def test_named_pipe_history_timeline_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    timeline = _roundtrip(
        server,
        lambda: client.query_history_timeline(
            activity_filter="CONTROLS",
            job_id="job-a",
            limit=5,
            after={
                "cursor_version": 1,
                "started_utc": "2026-07-20T12:00:00.000Z",
                "activity_kind": "CONTROL",
                "activity_id": "analysis-z",
            },
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert timeline.status is IpcStatus.ACCEPTED
    assert timeline.payload["history_timeline"]["read_model_available"] is False
    assert timeline.payload["history_timeline"]["activity_filter"] == "CONTROLS"
    assert timeline.payload["history_timeline"]["job_id"] == "job-a"
    assert timeline.payload["history_timeline"]["next_cursor"] is None


def test_named_pipe_retained_version_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(
        server,
        lambda: client.query_retained_versions(
            run_id="run-a",
            limit=5,
            after={
                "cursor_version": 1,
                "created_utc": "2026-07-20T12:00:00.000Z",
                "version_object_id": "version-z",
            },
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["retained_versions"]["read_model_available"] is False
    assert page.payload["retained_versions"]["run_id"] == "run-a"
    assert page.payload["retained_versions"]["next_cursor"] is None


def test_named_pipe_run_progress_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    progress = _roundtrip(
        server,
        lambda: client.query_run_progress(run_id="run-a", after_sequence_no=4),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert progress.status is IpcStatus.ACCEPTED
    assert progress.payload["run_progress"]["read_model_available"] is False
    assert progress.payload["run_progress"]["requested_after_sequence_no"] == 4


def test_named_pipe_operation_audit_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    detail = _roundtrip(
        server,
        lambda: client.query_operation_audit(
            run_id="run-a",
            operation_id="op-a",
            limit=5,
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert detail.status is IpcStatus.ACCEPTED
    assert detail.payload["operation_audit"]["read_model_available"] is False
    assert detail.payload["operation_audit"]["found"] is False


def test_named_pipe_plan_operations_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(
        server,
        lambda: client.query_plan_operations(
            plan_id="plan-a",
            target_endpoint_id="target-a",
            risk_levels=("MEDIUM", "BLOCKED"),
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["plan_operations"]["read_model_available"] is False


def test_named_pipe_plan_endpoints_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(server, lambda: client.query_plan_endpoints(plan_id="plan-a"))

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["plan_endpoints"]["read_model_available"] is False


def test_named_pipe_snapshot_entries_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(
        server, lambda: client.query_snapshot_entries(snapshot_id="snapshot-a")
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["snapshot_entries"]["read_model_available"] is False


def test_named_pipe_snapshot_coverage_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(
        server,
        lambda: client.query_snapshot_coverage(
            snapshot_id="snapshot-a",
            coverage_states=("COMPLETE",),
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["snapshot_coverage"]["read_model_available"] is False
    assert page.payload["snapshot_coverage"]["coverage_states"] == ["COMPLETE"]


def test_named_pipe_snapshot_issues_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(
        server,
        lambda: client.query_snapshot_issues(
            snapshot_id="snapshot-a", blocking_only=True
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["snapshot_issues"]["read_model_available"] is False
    assert page.payload["snapshot_issues"]["blocking_only"] is True


def test_named_pipe_cataloged_files_query_succeeds_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    page = _roundtrip(
        server,
        lambda: client.query_cataloged_files(
            run_id="run-a", target_endpoint_id="target-a"
        ),
    )

    assert handshake.status is IpcStatus.ACCEPTED
    assert page.status is IpcStatus.ACCEPTED
    assert page.payload["cataloged_files"]["read_model_available"] is False
    assert page.payload["cataloged_files"]["run_id"] == "run-a"
    assert page.payload["cataloged_files"]["target_endpoint_id"] == "target-a"


@pytest.mark.parametrize(
    ("protocol_version", "schema_version", "reason"),
    [
        (PROTOCOL_VERSION + 1, SCHEMA_VERSION, IpcReason.PROTOCOL_MISMATCH),
        (PROTOCOL_VERSION, SCHEMA_VERSION + 1, IpcReason.SCHEMA_MISMATCH),
    ],
)
def test_named_pipe_version_mismatch_is_rejected(
    protocol_version: int,
    schema_version: int,
    reason: IpcReason,
) -> None:
    server, client = _server_and_client()

    response = _roundtrip(
        server,
        lambda: client.connect(
            protocol_version=protocol_version,
            schema_version=schema_version,
        ),
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is reason


def test_named_pipe_engine_host_role_is_not_allowed_as_client() -> None:
    server, client = _server_and_client(role=ProcessRole.ENGINE_HOST)

    response = _roundtrip(server, client.connect)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.ROLE_NOT_ALLOWED


def test_named_pipe_mutating_commands_are_disabled_after_handshake() -> None:
    server, client = _server_and_client()

    handshake = _roundtrip(server, client.connect)
    command = _roundtrip(server, lambda: client.submit_command("UNKNOWN_MUTATION"))

    assert handshake.status is IpcStatus.ACCEPTED
    assert command.status is IpcStatus.REJECTED
    assert command.reason is IpcReason.MUTATING_COMMANDS_DISABLED


def test_named_pipe_serializes_structured_client_capacity_rejection() -> None:
    service = EngineHostIpcService(
        win32_named_pipe.current_user_policy(),
        resource_limits=IpcResourceLimits(
            max_accepted_clients=1,
            max_global_frames_per_window=20,
            max_client_frames_per_window=10,
        ),
    )
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="integration-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(pipe_name=pipe_name, service=service)
    first = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name)
    second = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name)

    assert _roundtrip(server, first.connect).status is IpcStatus.ACCEPTED

    rejection = _roundtrip(server, second.connect)

    assert rejection.status is IpcStatus.REJECTED
    assert rejection.reason is IpcReason.IPC_RATE_LIMITED
    assert rejection.payload["limit_scope"] == "ACCEPTED_CLIENTS"
    assert rejection.payload["limit"] == 1
    assert rejection.payload["retry_after_ms"] > 0


def test_named_pipe_rejects_oversized_declared_frame_before_reading_body() -> None:
    server, client = _server_and_client()
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.serve_once()
        except BaseException as exc:  # pragma: no cover - re-raised in test thread
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    handle = client._open()
    try:
        win32_named_pipe._write_all(handle, struct.pack("<I", MAX_FRAME_BYTES + 1))
        payload = win32_named_pipe._read_message(
            handle,
            limit=MAX_QUERY_RESPONSE_BYTES,
        )
        win32_named_pipe._write_all(handle, win32_named_pipe.RESPONSE_ACK)
    finally:
        win32_named_pipe._close_handle(handle)
    thread.join(timeout=5)

    assert not thread.is_alive(), "named-pipe server waited for an oversized frame body"
    if errors:
        raise errors[0]
    assert payload["status"] == IpcStatus.REJECTED.value
    assert payload["reason"] == IpcReason.INVALID_FRAME.value


def test_named_pipe_background_cancellation_interrupts_pending_response_read() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingStatusService(EngineHostIpcService):
        def query_status(self, client_instance_id: str) -> IpcResponse:
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("status query release timed out")
            return super().query_status(client_instance_id)

    service = BlockingStatusService(win32_named_pipe.current_user_policy())
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="integration-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(
        pipe_name=pipe_name,
        service=service,
    )
    client = win32_named_pipe.Win32NamedPipeClient(
        pipe_name=pipe_name,
        timeout_ms=5_000,
    )
    server_errors: list[BaseException] = []

    def serve_two_requests() -> None:
        try:
            server.serve_once()
            server.serve_once()
        except BaseException as exc:  # pragma: no cover - re-raised below
            server_errors.append(exc)

    server_thread = threading.Thread(target=serve_two_requests, daemon=True)
    server_thread.start()
    handshake = client.connect()
    assert handshake.status is IpcStatus.ACCEPTED

    cancellation = threading.Event()
    client.bind_background_cancellation(cancellation)
    client_errors: list[BaseException] = []

    def query_status() -> None:
        try:
            client.query_status()
        except BaseException as exc:  # pragma: no cover - asserted below
            client_errors.append(exc)

    client_thread = threading.Thread(target=query_status, daemon=True)
    client_thread.start()
    assert started.wait(timeout=2)
    cancelled_at = monotonic()
    cancellation.set()
    client_thread.join(timeout=1)
    cancellation_latency = monotonic() - cancelled_at

    assert not client_thread.is_alive(), "cancelled client read remained blocked"
    assert cancellation_latency < 1
    assert len(client_errors) == 1
    assert isinstance(client_errors[0], InterruptedError)

    client.bind_background_cancellation(None)
    release.set()
    server_thread.join(timeout=5)
    assert not server_thread.is_alive()
    if server_errors:
        raise server_errors[0]


def test_named_pipe_disconnects_client_that_stalls_before_frame_header() -> None:
    service = EngineHostIpcService(win32_named_pipe.current_user_policy())
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="integration-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(
        pipe_name=pipe_name,
        service=service,
        request_timeout_ms=50,
        response_timeout_ms=50,
        ack_timeout_ms=50,
    )
    client = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name)
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.serve_once()
        except BaseException as exc:  # pragma: no cover - re-raised in test thread
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    handle = client._open()
    try:
        thread.join(timeout=2)
    finally:
        win32_named_pipe._close_handle(handle)

    assert not thread.is_alive(), "stalled client pinned the named-pipe server"
    if errors:
        raise errors[0]


def test_named_pipe_does_not_wait_indefinitely_for_response_acknowledgment() -> None:
    service = EngineHostIpcService(win32_named_pipe.current_user_policy())
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="integration-test",
        suffix=uuid4().hex,
    )
    server = win32_named_pipe.Win32NamedPipeServer(
        pipe_name=pipe_name,
        service=service,
        request_timeout_ms=100,
        response_timeout_ms=100,
        ack_timeout_ms=50,
    )
    client = win32_named_pipe.Win32NamedPipeClient(pipe_name=pipe_name)
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.serve_once()
        except BaseException as exc:  # pragma: no cover - re-raised in test thread
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    handle = client._open()
    request_id = str(uuid4())
    try:
        win32_named_pipe._write_message(
            handle,
            {
                "message_type": "QUERY_STATUS",
                "request_id": request_id,
                "client_instance_id": client.client_instance_id,
            },
        )
        response = win32_named_pipe._read_message(
            handle,
            limit=MAX_QUERY_RESPONSE_BYTES,
        )
        thread.join(timeout=2)
    finally:
        win32_named_pipe._close_handle(handle)

    assert response["reason"] == IpcReason.HANDSHAKE_REQUIRED.value
    assert response["request_id"] == request_id
    assert not thread.is_alive(), (
        "missing response acknowledgment pinned the pipe server"
    )
    if errors:
        raise errors[0]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("request_timeout_ms", 0),
        ("response_timeout_ms", 0),
        ("ack_timeout_ms", 0),
    ],
)
def test_named_pipe_server_requires_positive_io_deadlines(
    field_name: str,
    value: int,
) -> None:
    values = {
        "request_timeout_ms": 100,
        "response_timeout_ms": 100,
        "ack_timeout_ms": 100,
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        win32_named_pipe.Win32NamedPipeServer(
            pipe_name="unused",
            service=EngineHostIpcService(win32_named_pipe.current_user_policy()),
            **values,
        )


def test_named_pipe_creation_uses_remote_client_rejection_flag() -> None:
    assert win32_named_pipe.PIPE_MODE & win32_named_pipe.PIPE_REJECT_REMOTE_CLIENTS
