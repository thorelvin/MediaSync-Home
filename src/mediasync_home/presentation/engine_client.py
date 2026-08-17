from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Protocol

from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.backup_analysis import BackupAnalysisCommandName
from mediasync_home.application.duplicate_scanning import DuplicateScanCommandName
from mediasync_home.application.endpoint_takeover import EndpointTakeoverCommandName
from mediasync_home.application.job_creation import JobCreationCommandName
from mediasync_home.application.job_draft_saving import JobDraftCommandName
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.job_editing import JobEditingCommandName
from mediasync_home.application.job_lifecycle import JobLifecycleCommandName
from mediasync_home.application.job_scheduling import JobSchedulingCommandName
from mediasync_home.application.retained_version_history import VersionRestoreCommandName
from mediasync_home.application.runs import RunCommandName
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCommandName,
)
from mediasync_home.ipc.protocol import IpcReason, IpcResponse


class StatusIpcClient(Protocol):
    def connect(self) -> IpcResponse:
        pass

    def query_status(self) -> IpcResponse:
        pass

    def request_engine_host_shutdown(self) -> IpcResponse:
        pass

    def query_selected_directory_identities(
        self,
        *,
        path_labels: tuple[str, ...],
    ) -> IpcResponse:
        pass

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        pass

    def query_duplicate_scan(self, *, analysis_id: str) -> IpcResponse:
        pass

    def query_duplicate_groups(
        self,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        relationship_classes: tuple[str, ...] = (),
    ) -> IpcResponse:
        pass

    def query_duplicate_members(
        self,
        *,
        group_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_duplicate_report(
        self,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
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
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def query_retained_versions(
        self,
        *,
        run_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        pass

    def query_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        pass

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
        duplicate_group_id: str | None = None,
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

    def query_snapshot_filter_decisions(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        decision_states: tuple[str, ...] = (),
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

    def bind_background_cancellation(self, cancellation: Event | None) -> None:
        binder = getattr(self._ipc_client, "bind_background_cancellation", None)
        if callable(binder):
            binder(cancellation)

    def connect(self) -> IpcResponse:
        return self._ipc_client.connect()

    def get_status(self) -> IpcResponse:
        return self._request_with_handshake_retry(self._ipc_client.query_status)

    def shutdown_engine_host(self) -> IpcResponse:
        return self._request_with_handshake_retry(
            self._ipc_client.request_engine_host_shutdown
        )

    def get_selected_directory_identities(
        self,
        *,
        path_labels: tuple[str, ...],
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_selected_directory_identities(
                path_labels=path_labels,
            )
        )

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_backup_overview(
                draft_id=draft_id,
                lifecycle_state=lifecycle_state,
                limit=limit,
                offset=offset,
            )
        )

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_backup_job_detail(job_id=job_id)
        )

    def get_duplicate_scan(self, *, analysis_id: str) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_duplicate_scan(analysis_id=analysis_id)
        )

    def get_duplicate_groups(
        self,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        relationship_classes: tuple[str, ...] = (),
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_duplicate_groups(
                analysis_id=analysis_id,
                limit=limit,
                after=after,
                relationship_classes=relationship_classes,
            )
        )

    def get_duplicate_members(
        self,
        *,
        group_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_duplicate_members(
                group_id=group_id,
                limit=limit,
                after=after,
            )
        )

    def get_duplicate_report(
        self,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_duplicate_report(
                analysis_id=analysis_id,
                limit=limit,
                after=after,
            )
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
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_history_timeline(
                activity_filter=activity_filter,
                job_id=job_id,
                limit=limit,
                after=after,
                offset=offset,
            )
        )

    def get_retained_versions(
        self,
        *,
        run_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_retained_versions(
                run_id=run_id,
                limit=limit,
                after=after,
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

    def get_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_operation_audit(
                run_id=run_id,
                operation_id=operation_id,
                limit=limit,
            )
        )

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
        duplicate_group_id: str | None = None,
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_plan_operations(
                plan_id=plan_id,
                limit=limit,
                after=after,
                target_endpoint_id=target_endpoint_id,
                risk_levels=risk_levels,
                duplicate_group_id=duplicate_group_id,
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

    def get_snapshot_filter_decisions(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        decision_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.query_snapshot_filter_decisions(
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                decision_states=decision_states,
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
        autosave_draft_id: str | None = None,
    ) -> IpcResponse:
        payload = _create_standard_backup_job_payload(draft)
        if autosave_draft_id is not None:
            payload["autosave_draft_id"] = autosave_draft_id
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

    def save_standard_backup_draft(
        self,
        *,
        draft: StandardBackupJobDraft,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "draft_id": draft.draft_id,
            "draft": _standard_backup_draft_payload(draft),
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                JobDraftCommandName.SAVE_STANDARD_BACKUP_DRAFT.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def update_standard_backup_job(
        self,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        draft: StandardBackupJobDraft,
        check_after_save: bool,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "job_id": job_id,
            "expected_job_revision_id": expected_job_revision_id,
            "expected_lifecycle_row_version": expected_lifecycle_row_version,
            "draft": _standard_backup_draft_payload(draft),
            "explicit_save": True,
            "check_after_save": check_after_save,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                JobEditingCommandName.UPDATE_STANDARD_BACKUP_JOB.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def register_writable_targets(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "job_id": job_id,
            "job_revision_id": job_revision_id,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def start_controlled_endpoint_takeover(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        target_ordinal: int,
        endpoint_id: str,
        expected_foreign_owner_installation_id: str,
        expected_ownership_epoch: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "job_id": job_id,
            "job_revision_id": job_revision_id,
            "target_ordinal": target_ordinal,
            "endpoint_id": endpoint_id,
            "expected_foreign_owner_installation_id": (
                expected_foreign_owner_installation_id
            ),
            "expected_ownership_epoch": expected_ownership_epoch,
            "explicit_confirmation": True,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def archive_standard_backup_job(
        self,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._change_standard_backup_job_lifecycle(
            JobLifecycleCommandName.ARCHIVE_STANDARD_BACKUP_JOB,
            job_id=job_id,
            expected_job_revision_id=expected_job_revision_id,
            expected_lifecycle_row_version=expected_lifecycle_row_version,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def reactivate_standard_backup_job(
        self,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._change_standard_backup_job_lifecycle(
            JobLifecycleCommandName.REACTIVATE_STANDARD_BACKUP_JOB,
            job_id=job_id,
            expected_job_revision_id=expected_job_revision_id,
            expected_lifecycle_row_version=expected_lifecycle_row_version,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def delete_standard_backup_job(
        self,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._change_standard_backup_job_lifecycle(
            JobLifecycleCommandName.DELETE_STANDARD_BACKUP_JOB,
            job_id=job_id,
            expected_job_revision_id=expected_job_revision_id,
            expected_lifecycle_row_version=expected_lifecycle_row_version,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def _change_standard_backup_job_lifecycle(
        self,
        command_name: JobLifecycleCommandName,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "job_id": job_id,
            "expected_job_revision_id": expected_job_revision_id,
            "expected_lifecycle_row_version": expected_lifecycle_row_version,
            "explicit_confirmation": True,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                command_name.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def configure_daily_backup_schedule(
        self,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        expected_schedule_row_version: int,
        enabled: bool,
        local_time: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "job_id": job_id,
            "expected_job_revision_id": expected_job_revision_id,
            "expected_lifecycle_row_version": expected_lifecycle_row_version,
            "expected_schedule_row_version": expected_schedule_row_version,
            "enabled": enabled,
            "local_time": local_time,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                JobSchedulingCommandName.CONFIGURE_DAILY_BACKUP_SCHEDULE.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
        target_endpoint_ids: tuple[str, ...] = (),
        resumed_from_run_id: str | None = None,
        source_operation_ids: tuple[str, ...] = (),
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "plan_id": plan_id,
            "plan_checksum": plan_checksum,
        }
        if target_endpoint_ids:
            payload["target_endpoint_ids"] = list(target_endpoint_ids)
        if resumed_from_run_id is not None:
            payload["resumed_from_run_id"] = resumed_from_run_id
        if source_operation_ids:
            payload["source_operation_ids"] = list(source_operation_ids)
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                RunCommandName.START_RUN.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def check_backup(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
        start_when_safe: bool = True,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "job_id": job_id,
            "start_when_safe": start_when_safe,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                BackupAnalysisCommandName.CHECK_BACKUP.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def start_duplicate_scan(
        self,
        *,
        analysis_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._submit_duplicate_scan_command(
            DuplicateScanCommandName.START_DUPLICATE_SCAN,
            analysis_id=analysis_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def pause_duplicate_scan(
        self,
        *,
        analysis_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._submit_duplicate_scan_command(
            DuplicateScanCommandName.PAUSE_DUPLICATE_SCAN,
            analysis_id=analysis_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def resume_duplicate_scan(
        self,
        *,
        analysis_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._submit_duplicate_scan_command(
            DuplicateScanCommandName.RESUME_DUPLICATE_SCAN,
            analysis_id=analysis_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def mark_duplicate_group_reviewed(
        self,
        *,
        group_id: str,
        expected_review_state: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "group_id": group_id,
            "expected_review_state": expected_review_state,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                DuplicateScanCommandName.MARK_DUPLICATE_GROUP_REVIEWED.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def _submit_duplicate_scan_command(
        self,
        command_name: DuplicateScanCommandName,
        *,
        analysis_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {"analysis_id": analysis_id}
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                command_name.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def protect_retained_version_for_restore(
        self,
        *,
        version_object_id: str,
        expected_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "version_object_id": version_object_id,
            "expected_row_version": expected_row_version,
            "explicit_confirmation": True,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                VersionRestoreCommandName.PROTECT_RETAINED_VERSION_FOR_RESTORE.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def restore_retained_version(
        self,
        *,
        version_object_id: str,
        expected_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "version_object_id": version_object_id,
            "expected_row_version": expected_row_version,
            "explicit_confirmation": True,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                VersionRestoreCommandName.RESTORE_RETAINED_VERSION.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def undo_retained_version_restore(
        self,
        *,
        restore_id: str,
        version_object_id: str,
        expected_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {
            "restore_id": restore_id,
            "version_object_id": version_object_id,
            "expected_row_version": expected_row_version,
            "explicit_confirmation": True,
        }
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                VersionRestoreCommandName.UNDO_RETAINED_VERSION_RESTORE.value,
                request_id=request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                payload_hash=canonical_command_payload_hash(payload),
            )
        )

    def pause_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._submit_run_control(
            RunCommandName.PAUSE_RUN,
            run_id=run_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def resume_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._submit_run_control(
            RunCommandName.RESUME_RUN,
            run_id=run_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def stop_backup_after_active_file(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        return self._submit_run_control(
            RunCommandName.STOP_RUN_AFTER_ACTIVE_FILE,
            run_id=run_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def _submit_run_control(
        self,
        command_name: RunCommandName,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        payload: dict[str, object] = {"run_id": run_id}
        return self._request_with_handshake_retry(
            lambda: self._ipc_client.submit_command(
                command_name.value,
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
        "draft": _standard_backup_draft_payload(draft),
    }


def _standard_backup_draft_payload(
    draft: StandardBackupJobDraft,
) -> dict[str, object]:
    return {
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
            "automation_policy": draft.defaults.automation_policy.value,
        },
    }
