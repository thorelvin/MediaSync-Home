from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.runs import RunCommandName
from mediasync_home.ipc.protocol import IpcReason, IpcResponse


class StatusIpcClient(Protocol):
    def connect(self) -> IpcResponse:
        pass

    def query_status(self) -> IpcResponse:
        pass

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        pass

    def query_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def query_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def query_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        pass

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        pass

    def query_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        pass

    def query_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def submit_command(
        self,
        command_name: str,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
        payload_hash: str | None = None,
    ) -> IpcResponse:
        pass


class EngineClient:
    def __init__(self, ipc_client: StatusIpcClient) -> None:
        self._ipc_client = ipc_client

    def connect(self) -> IpcResponse:
        return self._ipc_client.connect()

    def get_status(self) -> IpcResponse:
        return self._request_with_handshake_retry(self._ipc_client.query_status)

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_backup_overview(
                draft_id=draft_id,
                limit=limit,
                offset=offset,
            )
        )

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_backup_job_detail(job_id=job_id)
        )

    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_activity_overview(
                job_id=job_id,
                limit=limit,
                offset=offset,
            )
        )

    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_history_timeline(
                activity_filter=activity_filter,
                job_id=job_id,
                limit=limit,
                offset=offset,
            )
        )

    def get_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_run_progress(
                run_id=run_id,
                after_sequence_no=after_sequence_no,
            )
        )

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_plan_operations(
                plan_id=plan_id,
                limit=limit,
                after=after,
            )
        )

    def get_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_plan_endpoints(
                plan_id=plan_id,
                limit=limit,
                after=after,
            )
        )

    def get_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_snapshot_entries(
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
            )
        )

    def get_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_snapshot_coverage(
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                coverage_states=coverage_states,
            )
        )

    def get_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_snapshot_issues(
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                blocking_only=blocking_only,
            )
        )

    def get_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_cataloged_files(
                run_id=run_id,
                target_endpoint_id=target_endpoint_id,
                limit=limit,
                offset=offset,
            )
        )

    def create_standard_backup_job(
        self,
        *,
        draft: StandardBackupJobDraft,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload = _create_standard_backup_job_payload(draft)
        payload_hash = canonical_command_payload_hash(payload)
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=payload_hash,
            )
        )

    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "plan_id": plan_id,
            "plan_checksum": plan_checksum,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                RunCommandName.START_RUN.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def _request_with_handshake_retry(
        self,
        request: Callable[[], IpcResponse],
    ) -> IpcResponse:
        response = request()
        if response.reason is not IpcReason.HANDSHAKE_REQUIRED:
            return response
        handshake = self._ipc_client.connect()
        if handshake.reason is not None:
            return handshake
        return request()


def _create_standard_backup_job_payload(
    draft: StandardBackupJobDraft,
) -> dict[str, object]:
    return {
        "draft_id": draft.draft_id,
        "draft": {
            "draft_id": draft.draft_id,
            "schema_version": draft.schema_version,
            "source_name": draft.source_name,
            "source_path_label": draft.source_path_label,
            "targets": [
                {
                    "name": target.name,
                    "path_label": target.path_label,
                    "independent_device_id": target.independent_device_id,
                }
                for target in draft.targets
            ],
            "defaults": {
                "behavior": draft.defaults.behavior.value,
                "file_selection": draft.defaults.file_selection.value,
                "verification": draft.defaults.verification.value,
                "retention": draft.defaults.retention.value,
                "extra_files": draft.defaults.extra_files.value,
                "performance": draft.defaults.performance.value,
            },
        },
    }
