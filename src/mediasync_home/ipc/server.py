from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from math import ceil
from secrets import compare_digest
from time import monotonic
from typing import Any, Mapping
from uuid import uuid4

from mediasync_home.application.activity_read_models import (
    ActivityOverviewQueryError,
    RunActivityReadModelStore,
    query_activity_overview,
)
from mediasync_home.application.catalog_read_models import (
    CatalogedFileReadModelStore,
    CatalogedFilesQueryError,
    query_cataloged_files,
)
from mediasync_home.application.backup_analysis import (
    BackupAnalysisCommandName,
    BackupAnalysisPayloadError,
    BackupAnalysisRequest,
    BackupAnalysisRequestState,
    BackupAnalysisRequestStore,
    CheckBackupCommand,
    parse_check_backup_command,
)
from mediasync_home.application.command_receipts import (
    CommandEffectStorageFailure,
    CommandReceipt,
    CommandReceiptConflict,
    CommandEffectTransaction,
    CommandReceiptState,
    CommandReceiptStore,
    transition_command_receipt,
)
from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.cross_store_handoffs import (
    CrossStoreHandoffError,
    RunStartCrossStoreCoordinator,
)
from mediasync_home.application.duplicates import (
    DuplicateAnalysisReadStore,
    DuplicateRelationError,
    query_duplicate_analysis_summary,
)
from mediasync_home.application.duplicate_scanning import (
    DUPLICATE_GROUP_DEFAULT_PAGE_SIZE,
    DUPLICATE_GROUP_MAX_PAGE_SIZE,
    DUPLICATE_MEMBER_DEFAULT_PAGE_SIZE,
    DUPLICATE_MEMBER_MAX_PAGE_SIZE,
    DUPLICATE_REPORT_DEFAULT_PAGE_SIZE,
    DUPLICATE_REPORT_MAX_PAGE_SIZE,
    DuplicateGroupCursor,
    DuplicateGroupReadModel,
    DuplicateGroupReviewCommand,
    DuplicateMemberCursor,
    DuplicateReportCursor,
    DuplicateScanCommand,
    DuplicateScanCommandName,
    DuplicateScanError,
    DuplicateScanStatus,
    DuplicateScanStore,
    parse_duplicate_group_review_command,
    parse_duplicate_scan_command,
)
from mediasync_home.application.external_resources import (
    ExternalResourceStateStore,
    ExternalResourceType,
)
from mediasync_home.application.history_read_models import (
    HistoryTimelineQueryError,
    HistoryTimelineReadModelStore,
    query_history_timeline,
)
from mediasync_home.application.endpoint_registration import (
    EndpointClassificationRefreshReport,
)
from mediasync_home.application.endpoint_takeover import (
    EndpointTakeoverCommandName,
    EndpointTakeoverCoordinator,
    EndpointTakeoverError,
    EndpointTakeoverPayloadError,
    EndpointTakeoverReport,
    StartControlledEndpointTakeoverCommand,
    parse_start_controlled_endpoint_takeover_command,
)
from mediasync_home.application.job_creation import (
    CreateStandardBackupJobCommand,
    JobCreationOutcome,
    JobCreationCommandName,
    JobCreationPayloadError,
    JobCreationReadiness,
    SealedStandardBackupJob,
    StandardBackupJobCatalog,
    StandardBackupJobIdFactory,
    create_standard_backup_job_from_draft,
    evaluate_standard_backup_job_creation,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_draft_saving import (
    JobDraftCommandName,
    JobDraftPayloadError,
    SaveStandardBackupDraftCommand,
    parse_save_standard_backup_draft_command,
    save_standard_backup_draft,
)
from mediasync_home.application.job_drafts import JobDraftStore, StandardBackupJobDraft
from mediasync_home.application.job_editing import (
    JobEditingCommandName,
    JobEditingOutcome,
    JobEditingPayloadError,
    JobScheduleInvalidationError,
    JobScheduleInvalidator,
    StandardBackupJobRevisionCatalog,
    StandardBackupJobRevisionIdFactory,
    UpdateStandardBackupJobCommand,
    parse_update_standard_backup_job_command,
    update_standard_backup_job_from_draft,
)
from mediasync_home.application.job_endpoints import (
    StandardBackupJobEndpointRegistrar,
    StandardBackupJobEndpointSet,
)
from mediasync_home.application.job_lifecycle import (
    ChangeJobLifecycleCommand,
    JobLifecycleCommandName,
    JobLifecyclePayloadError,
    JobLifecycleState,
    JobLifecycleStore,
    JobLifecycleTransitionOutcome,
    parse_change_job_lifecycle_command,
)
from mediasync_home.application.job_read_models import (
    BackupJobDetailQueryError,
    BackupOverviewQueryError,
    StandardBackupJobDetailReadModelStore,
    StandardBackupJobReadModelStore,
    query_backup_job_detail,
    query_backup_overview,
)
from mediasync_home.application.job_scheduling import (
    ConfigureDailyBackupScheduleCommand,
    JobSchedulingCommandName,
    JobSchedulingOutcome,
    JobSchedulingPayloadError,
    configure_daily_backup_schedule,
    daily_backup_schedule_id,
    parse_configure_daily_backup_schedule_command,
)
from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanRefreshReport,
)
from mediasync_home.application.outbox import OutboxStore, command_effect_outbox_message
from mediasync_home.application.operation_audit_read_models import (
    OperationAuditQueryError,
    OperationAuditReadModelStore,
    query_operation_audit,
)
from mediasync_home.application.plan_read_models import (
    PlanEndpointsQueryError,
    PlanOperationsQueryError,
    query_plan_endpoints,
    query_plan_operations,
)
from mediasync_home.application.progress_read_models import (
    ProgressSnapshotQueryError,
    RunProgressSnapshotStore,
    query_run_progress,
)
from mediasync_home.application.retained_version_history import (
    ProtectRetainedVersionForRestoreCommand,
    RetainedVersionHistoryError,
    RetainedVersionReadModelStore,
    RestoreRetainedVersionCommand,
    UndoRetainedVersionRestoreCommand,
    VersionRestoreCommandName,
    VersionRestoreProtectionOutcome,
    VersionRestoreProtectionStore,
    VersionRestoreRequestOutcome,
    VersionRestoreRequestStore,
    VersionRestoreUndoRequestOutcome,
    VersionRestoreUndoRequestStore,
    parse_protect_retained_version_for_restore_command,
    parse_restore_retained_version_command,
    parse_undo_retained_version_restore_command,
    query_retained_versions,
)
from mediasync_home.application.plans import (
    PlanEndpointReadModelStore,
    PlanOperationReadModelStore,
    PlanStore,
)
from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.application.selected_directory_identity import (
    SelectedDirectoryIdentityError,
    SelectedDirectoryIdentityProbe,
    bind_standard_backup_draft_directory_identities,
    query_selected_directory_identities,
)
from mediasync_home.application.schedules import ScheduleStore
from mediasync_home.application.runs import (
    RunCommandName,
    RunControlCommand,
    RunControlOutcome,
    RunControlStore,
    RunIdFactory,
    RunStartOutcome,
    RunStartViolation,
    RunStore,
    StartRunCommand,
    StartedRun,
    evaluate_start_run,
    parse_run_control_command,
    parse_start_run_command,
    request_run_stop_after_active_file,
    request_run_pause,
    resume_paused_run,
    start_run_from_sealed_plan,
)
from mediasync_home.application.snapshot_read_models import (
    SnapshotCoverageQueryError,
    SnapshotEntriesQueryError,
    SnapshotFilterDecisionsQueryError,
    SnapshotIssuesQueryError,
    query_snapshot_coverage,
    query_snapshot_entries,
    query_snapshot_filter_decisions,
    query_snapshot_issues,
)
from mediasync_home.application.snapshot_scanning import (
    SnapshotMaterializationRefreshReport,
)
from mediasync_home.application.snapshots import (
    SnapshotCoverageReadModelStore,
    SnapshotEntryReadModelStore,
    SnapshotFilterDecisionReadModelStore,
    SnapshotIssueReadModelStore,
)
from mediasync_home.application.state_maintenance import (
    RestoreStateFromBackupSetCommand,
    StateMaintenanceCommandName,
    StateMaintenancePayloadError,
    StateRestoreCommandExecutor,
    parse_restore_state_from_backup_set_command,
)
from mediasync_home.application.trigger_occurrences import (
    EnqueueTriggerOccurrenceCommand,
    TriggerCommandName,
    TriggerOccurrenceStore,
    TriggerOccurrencePayloadError,
    parse_enqueue_trigger_occurrence_command,
)
from mediasync_home.application.trigger_runs import (
    TriggerRunEnqueueOutcome,
    enqueue_trigger_occurrence_analysis,
)
from mediasync_home.application.writable_endpoint_registration import (
    RegisterWritableTargetsCommand,
    WritableEndpointRegistrationCommandName,
    WritableEndpointRegistrationCoordinator,
    WritableEndpointRegistrationError,
    WritableEndpointRegistrationPayloadError,
    WritableEndpointRegistrationReport,
    parse_register_writable_targets_command,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import (
    COMMAND_SCHEMA_VERSION,
    MAX_PROGRESS_EVENT_BYTES,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
    IpcStatus,
    encode_frame,
)


@dataclass(frozen=True)
class IpcResourceLimits:
    max_accepted_clients: int = 32
    accepted_client_idle_seconds: float = 300.0
    max_global_frames_per_window: int = 240
    max_client_frames_per_window: int = 120
    frame_window_seconds: float = 1.0
    max_outstanding_requests: int = 1
    max_subscriptions: int = 0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_accepted_clients,
            self.max_global_frames_per_window,
            self.max_client_frames_per_window,
        )
        if any(limit < 1 for limit in integer_limits):
            raise ValueError("IPC count limits must be positive")
        if self.accepted_client_idle_seconds <= 0:
            raise ValueError("accepted_client_idle_seconds must be positive")
        if self.frame_window_seconds <= 0:
            raise ValueError("frame_window_seconds must be positive")
        if self.max_client_frames_per_window > self.max_global_frames_per_window:
            raise ValueError(
                "per-client frame limit cannot exceed the global frame limit"
            )
        if self.max_outstanding_requests != 1:
            raise ValueError(
                "the synchronous named-pipe transport permits one outstanding request"
            )
        if self.max_subscriptions != 0:
            raise ValueError(
                "IPC subscriptions are unavailable in the current protocol"
            )

    def to_payload(self) -> dict[str, int]:
        return {
            "max_accepted_clients": self.max_accepted_clients,
            "accepted_client_idle_ms": int(self.accepted_client_idle_seconds * 1000),
            "max_global_frames_per_window": self.max_global_frames_per_window,
            "max_client_frames_per_window": self.max_client_frames_per_window,
            "frame_window_ms": int(self.frame_window_seconds * 1000),
            "max_outstanding_requests": self.max_outstanding_requests,
            "max_subscriptions": self.max_subscriptions,
        }


@dataclass
class _AcceptedClient:
    identity: VerifiedClientIdentity
    last_seen_monotonic: float
    frame_times: deque[float] = field(default_factory=deque)


@dataclass
class EngineHostIpcService:
    authorization: ClientAuthorizationPolicy
    status: RuntimeStatus = field(
        default_factory=lambda: startup_status(ProcessRole.ENGINE_HOST)
    )
    installation_id: str = "local-dev"
    resource_limits: IpcResourceLimits = field(default_factory=IpcResourceLimits)
    monotonic_clock: Callable[[], float] = field(
        default=monotonic,
        repr=False,
        compare=False,
    )
    selected_directory_identity_probe: SelectedDirectoryIdentityProbe | None = None
    job_draft_store: JobDraftStore | None = None
    standard_backup_job_catalog: StandardBackupJobCatalog | None = None
    standard_backup_job_revision_catalog: (
        StandardBackupJobRevisionCatalog | None
    ) = None
    standard_backup_job_read_store: StandardBackupJobReadModelStore | None = None
    standard_backup_job_detail_store: StandardBackupJobDetailReadModelStore | None = (
        None
    )
    standard_backup_job_endpoint_registrar: (
        StandardBackupJobEndpointRegistrar | None
    ) = None
    endpoint_classification_refresh: (
        Callable[[], EndpointClassificationRefreshReport] | None
    ) = None
    writable_endpoint_registration: WritableEndpointRegistrationCoordinator | None = (
        None
    )
    writable_endpoint_registration_utc_now: Callable[[], str] | None = None
    endpoint_takeover: EndpointTakeoverCoordinator | None = None
    endpoint_takeover_utc_now: Callable[[], str] | None = None
    job_snapshot_refresh: Callable[[], SnapshotMaterializationRefreshReport] | None = (
        None
    )
    initial_backup_plan_refresh: Callable[[], InitialBackupPlanRefreshReport] | None = (
        None
    )
    history_timeline_read_store: HistoryTimelineReadModelStore | None = None
    retained_version_read_store: RetainedVersionReadModelStore | None = None
    version_restore_protection_store: VersionRestoreProtectionStore | None = None
    version_restore_request_store: VersionRestoreRequestStore | None = None
    version_restore_undo_request_store: VersionRestoreUndoRequestStore | None = None
    retained_version_utc_now: Callable[[], str] | None = None
    operation_audit_read_store: OperationAuditReadModelStore | None = None
    run_activity_read_store: RunActivityReadModelStore | None = None
    run_progress_snapshot_store: RunProgressSnapshotStore | None = None
    plan_operation_read_store: PlanOperationReadModelStore | None = None
    plan_endpoint_read_store: PlanEndpointReadModelStore | None = None
    snapshot_entry_read_store: SnapshotEntryReadModelStore | None = None
    snapshot_coverage_read_store: SnapshotCoverageReadModelStore | None = None
    snapshot_issue_read_store: SnapshotIssueReadModelStore | None = None
    snapshot_filter_decision_read_store: SnapshotFilterDecisionReadModelStore | None = None
    cataloged_file_read_store: CatalogedFileReadModelStore | None = None
    duplicate_analysis_read_store: DuplicateAnalysisReadStore | None = None
    duplicate_scan_store: DuplicateScanStore | None = None
    duplicate_scan_utc_now: Callable[[], str] | None = None
    backup_analysis_request_store: BackupAnalysisRequestStore | None = None
    job_lifecycle_store: JobLifecycleStore | None = None
    job_lifecycle_utc_now: Callable[[], str] | None = None
    standard_backup_job_id_factory: StandardBackupJobIdFactory | None = None
    standard_backup_job_revision_id_factory: (
        StandardBackupJobRevisionIdFactory | None
    ) = None
    job_schedule_invalidator: JobScheduleInvalidator | None = None
    job_editing_utc_now: Callable[[], str] | None = None
    plan_store: PlanStore | None = None
    run_store: RunStore | None = None
    run_control_store: RunControlStore | None = None
    run_id_factory: RunIdFactory | None = None
    run_start_cross_store_coordinator: RunStartCrossStoreCoordinator | None = None
    schedule_store: ScheduleStore | None = None
    task_scheduler_executable_path: str | None = None
    task_scheduler_time_zone_id: str | None = None
    trigger_occurrence_store: TriggerOccurrenceStore | None = None
    external_resource_state_store: ExternalResourceStateStore | None = None
    command_receipt_store: CommandReceiptStore | None = None
    command_effect_transaction: CommandEffectTransaction | None = None
    state_restore_executor: StateRestoreCommandExecutor | None = None
    outbox_store: OutboxStore | None = None
    state_capacity_provider: Callable[[], dict[str, object]] | None = None
    _accepted_clients: dict[str, _AcceptedClient] = field(default_factory=dict)
    _global_frame_times: deque[float] = field(default_factory=deque)

    def handshake(
        self,
        payload: dict[str, Any],
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        now = self.monotonic_clock()
        rate_limit_response = self._admit_global_frame(now)
        if rate_limit_response is not None:
            return rate_limit_response
        try:
            request = HandshakeRequest.from_dict(payload)
        except (IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if request.protocol_version != PROTOCOL_VERSION:
            return IpcResponse.rejected(IpcReason.PROTOCOL_MISMATCH)
        if request.schema_version != SCHEMA_VERSION:
            return IpcResponse.rejected(IpcReason.SCHEMA_MISMATCH)
        reject_reason = self.authorization.reject_reason(request.role, identity)
        if reject_reason is not None:
            return IpcResponse.rejected(reject_reason)

        self._expire_idle_clients(now)
        accepted_client = self._accepted_clients.get(request.client_instance_id)
        if accepted_client is None:
            if len(self._accepted_clients) >= self.resource_limits.max_accepted_clients:
                return self._client_capacity_response(now)
            accepted_client = _AcceptedClient(
                identity=identity,
                last_seen_monotonic=now,
            )
            self._accepted_clients[request.client_instance_id] = accepted_client
        else:
            rate_limit_response = self._admit_client_frame(accepted_client, now)
            if rate_limit_response is not None:
                return rate_limit_response
            accepted_client.identity = identity
            accepted_client.last_seen_monotonic = now
        if not accepted_client.frame_times:
            accepted_client.frame_times.append(now)
        response_payload: dict[str, object] = {
            "server_nonce": str(uuid4()),
            "verified_user_sid_hash": identity.user_sid_hash,
            "host_status": self.status.to_dict(),
            "resource_limits": self.resource_limits.to_payload(),
        }
        self._add_state_capacity_payload(response_payload)
        return IpcResponse.accepted(response_payload)

    def query_status(self, client_instance_id: str) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        response_payload: dict[str, object] = {"host_status": self.status.to_dict()}
        self._add_state_capacity_payload(response_payload)
        return IpcResponse.accepted(response_payload)

    def query_selected_directory_identities(
        self,
        client_instance_id: str,
        *,
        path_labels: tuple[str, ...],
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        if self.selected_directory_identity_probe is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        try:
            result = query_selected_directory_identities(
                path_labels=path_labels,
                probe=self.selected_directory_identity_probe,
            )
        except SelectedDirectoryIdentityError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted(
            {"selected_directory_identities": result.to_dict()}
        )

    def query_backup_overview(
        self,
        client_instance_id: str,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            overview = query_backup_overview(
                job_read_store=self.standard_backup_job_read_store,
                draft_store=self.job_draft_store,
                draft_id=draft_id,
                lifecycle_state=lifecycle_state,
                limit=limit,
                offset=offset,
            )
        except BackupOverviewQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"backup_overview": overview.to_dict()})

    def query_backup_job_detail(
        self,
        client_instance_id: str,
        *,
        job_id: str,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            detail = query_backup_job_detail(
                job_detail_store=self.standard_backup_job_detail_store,
                job_id=job_id,
            )
        except BackupJobDetailQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        payload = detail.to_dict()
        if (
            detail.job is not None
            and detail.job.initial_plan is not None
            and detail.job.initial_plan.analysis_id is not None
        ):
            try:
                duplicate_summary = query_duplicate_analysis_summary(
                    read_store=self.duplicate_analysis_read_store,
                    analysis_id=detail.job.initial_plan.analysis_id,
                )
            except DuplicateRelationError:
                return IpcResponse.rejected(IpcReason.INVALID_FRAME)
            job_payload = payload.get("job")
            if isinstance(job_payload, dict):
                job_payload["duplicate_summary"] = duplicate_summary.to_dict()
        return IpcResponse.accepted({"backup_job_detail": payload})

    def query_duplicate_scan(
        self,
        client_instance_id: str,
        *,
        analysis_id: str,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        if not analysis_id.strip():
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        status = (
            None
            if self.duplicate_scan_store is None
            else self.duplicate_scan_store.load_duplicate_scan(analysis_id.strip())
        )
        return IpcResponse.accepted(
            {
                "duplicate_scan": {
                    "analysis_id": analysis_id.strip(),
                    "available": self.duplicate_scan_store is not None,
                    "scan": None if status is None else status.to_dict(),
                }
            }
        )

    def query_duplicate_groups(
        self,
        client_instance_id: str,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        relationship_classes: tuple[str, ...] = (),
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        if self.duplicate_scan_store is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        try:
            page_limit = _bounded_query_limit(
                limit,
                default=DUPLICATE_GROUP_DEFAULT_PAGE_SIZE,
                maximum=DUPLICATE_GROUP_MAX_PAGE_SIZE,
            )
            cursor = _duplicate_group_cursor(after)
            page = self.duplicate_scan_store.page_duplicate_groups(
                analysis_id=analysis_id.strip(),
                limit=page_limit,
                after=cursor,
                relationship_classes=relationship_classes,
            )
        except (DuplicateScanError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"duplicate_groups": page.to_dict()})

    def query_duplicate_members(
        self,
        client_instance_id: str,
        *,
        group_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        if self.duplicate_scan_store is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        try:
            page_limit = _bounded_query_limit(
                limit,
                default=DUPLICATE_MEMBER_DEFAULT_PAGE_SIZE,
                maximum=DUPLICATE_MEMBER_MAX_PAGE_SIZE,
            )
            cursor = _duplicate_member_cursor(after)
            page = self.duplicate_scan_store.page_duplicate_members(
                group_id=group_id.strip(),
                limit=page_limit,
                after=cursor,
            )
        except (DuplicateScanError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"duplicate_members": page.to_dict()})

    def query_duplicate_report(
        self,
        client_instance_id: str,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        if self.duplicate_scan_store is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        try:
            page_limit = _bounded_query_limit(
                limit,
                default=DUPLICATE_REPORT_DEFAULT_PAGE_SIZE,
                maximum=DUPLICATE_REPORT_MAX_PAGE_SIZE,
            )
            page = self.duplicate_scan_store.page_duplicate_report(
                analysis_id=analysis_id.strip(),
                limit=page_limit,
                after=_duplicate_report_cursor(after),
            )
        except (DuplicateScanError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"duplicate_report": page.to_dict()})

    def query_activity_overview(
        self,
        client_instance_id: str,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            overview = query_activity_overview(
                run_read_store=self.run_activity_read_store,
                job_id=job_id,
                limit=limit,
                offset=offset,
            )
        except ActivityOverviewQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"activity_overview": overview.to_dict()})

    def query_history_timeline(
        self,
        client_instance_id: str,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            timeline = query_history_timeline(
                history_store=self.history_timeline_read_store,
                activity_filter=activity_filter,
                job_id=job_id,
                limit=limit,
                after=after,
                offset=offset,
            )
        except HistoryTimelineQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"history_timeline": timeline.to_dict()})

    def query_retained_versions(
        self,
        client_instance_id: str,
        *,
        run_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_retained_versions(
                version_store=self.retained_version_read_store,
                run_id=run_id,
                limit=limit,
                after=after,
            )
        except (RetainedVersionHistoryError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"retained_versions": page.to_dict()})

    def query_run_progress(
        self,
        client_instance_id: str,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            result = query_run_progress(
                run_progress_store=self.run_progress_snapshot_store,
                run_id=run_id,
                after_sequence_no=after_sequence_no,
            )
            response = IpcResponse.accepted({"run_progress": result.to_dict()})
            encode_frame(
                response.correlated("00000000-0000-4000-8000-000000000000").to_dict(),
                limit=MAX_PROGRESS_EVENT_BYTES,
            )
        except (ProgressSnapshotQueryError, IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return response

    def query_operation_audit(
        self,
        client_instance_id: str,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            detail = query_operation_audit(
                operation_audit_store=self.operation_audit_read_store,
                run_id=run_id,
                operation_id=operation_id,
                limit=limit,
            )
        except (OperationAuditQueryError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"operation_audit": detail.to_dict()})

    def query_plan_operations(
        self,
        client_instance_id: str,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
        duplicate_group_id: str | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_plan_operations(
                plan_read_store=self.plan_operation_read_store,
                plan_id=plan_id,
                limit=limit,
                after=after,
                target_endpoint_id=target_endpoint_id,
                risk_levels=risk_levels,
                duplicate_group_id=duplicate_group_id,
            )
        except PlanOperationsQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"plan_operations": page.to_dict()})

    def query_plan_endpoints(
        self,
        client_instance_id: str,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_plan_endpoints(
                plan_read_store=self.plan_endpoint_read_store,
                plan_id=plan_id,
                limit=limit,
                after=after,
            )
        except PlanEndpointsQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"plan_endpoints": page.to_dict()})

    def query_snapshot_entries(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_snapshot_entries(
                snapshot_read_store=self.snapshot_entry_read_store,
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
            )
        except SnapshotEntriesQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"snapshot_entries": page.to_dict()})

    def query_snapshot_coverage(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_snapshot_coverage(
                snapshot_coverage_store=self.snapshot_coverage_read_store,
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                coverage_states=coverage_states,
            )
        except SnapshotCoverageQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"snapshot_coverage": page.to_dict()})

    def query_snapshot_issues(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_snapshot_issues(
                snapshot_issue_store=self.snapshot_issue_read_store,
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                blocking_only=blocking_only,
            )
        except SnapshotIssuesQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"snapshot_issues": page.to_dict()})

    def query_snapshot_filter_decisions(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        decision_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_snapshot_filter_decisions(
                snapshot_filter_decision_store=(
                    self.snapshot_filter_decision_read_store
                ),
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                decision_states=decision_states,
            )
        except SnapshotFilterDecisionsQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted(
            {"snapshot_filter_decisions": page.to_dict()}
        )

    def query_cataloged_files(
        self,
        client_instance_id: str,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        try:
            page = query_cataloged_files(
                cataloged_file_read_store=self.cataloged_file_read_store,
                run_id=run_id,
                target_endpoint_id=target_endpoint_id,
                limit=limit,
                offset=offset,
            )
        except CatalogedFilesQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"cataloged_files": page.to_dict()})

    def submit_command(self, client_instance_id: str, command_name: str) -> IpcResponse:
        del command_name
        rejection = self._authorize_client_request(client_instance_id)
        if rejection is not None:
            return rejection
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED)

    def submit_command_envelope(self, payload: dict[str, Any]) -> IpcResponse:
        now = self.monotonic_clock()
        rate_limit_response = self._admit_global_frame(now)
        if rate_limit_response is not None:
            return rate_limit_response
        try:
            command = IpcCommandEnvelope.from_dict(payload)
        except (IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if command.protocol_version != PROTOCOL_VERSION:
            return IpcResponse.rejected(IpcReason.PROTOCOL_MISMATCH)
        if command.schema_version != COMMAND_SCHEMA_VERSION:
            return IpcResponse.rejected(IpcReason.SCHEMA_MISMATCH)
        self._expire_idle_clients(now)
        accepted_client = self._accepted_clients.get(command.client_instance_id)
        if accepted_client is None:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        rate_limit_response = self._admit_client_frame(accepted_client, now)
        if rate_limit_response is not None:
            return rate_limit_response
        identity = accepted_client.identity
        try:
            actual_payload_hash = canonical_command_payload_hash(command.payload)
        except (TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not compare_digest(command.payload_hash, actual_payload_hash):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if (
            command.command_name
            == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value
        ):
            return self._handle_create_standard_backup_job(command, identity)
        if (
            command.command_name
            == JobDraftCommandName.SAVE_STANDARD_BACKUP_DRAFT.value
        ):
            return self._handle_save_standard_backup_draft(command, identity)
        if (
            command.command_name
            == JobEditingCommandName.UPDATE_STANDARD_BACKUP_JOB.value
        ):
            return self._handle_update_standard_backup_job(command, identity)
        if (
            command.command_name
            == WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value
        ):
            return self._handle_register_writable_targets(command, identity)
        if (
            command.command_name
            == EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value
        ):
            return self._handle_controlled_endpoint_takeover(command, identity)
        if command.command_name == BackupAnalysisCommandName.CHECK_BACKUP.value:
            return self._handle_check_backup(command, identity)
        if command.command_name in {
            DuplicateScanCommandName.START_DUPLICATE_SCAN.value,
            DuplicateScanCommandName.PAUSE_DUPLICATE_SCAN.value,
            DuplicateScanCommandName.RESUME_DUPLICATE_SCAN.value,
        }:
            return self._handle_duplicate_scan_command(command, identity)
        if (
            command.command_name
            == DuplicateScanCommandName.MARK_DUPLICATE_GROUP_REVIEWED.value
        ):
            return self._handle_duplicate_group_review(command, identity)
        if (
            command.command_name
            == JobSchedulingCommandName.CONFIGURE_DAILY_BACKUP_SCHEDULE.value
        ):
            return self._handle_configure_daily_backup_schedule(command, identity)
        if command.command_name in {
            JobLifecycleCommandName.ARCHIVE_STANDARD_BACKUP_JOB.value,
            JobLifecycleCommandName.REACTIVATE_STANDARD_BACKUP_JOB.value,
        }:
            return self._handle_job_lifecycle(command, identity)
        if (
            command.command_name
            == VersionRestoreCommandName.PROTECT_RETAINED_VERSION_FOR_RESTORE.value
        ):
            return self._handle_version_restore_protection(command, identity)
        if (
            command.command_name
            == VersionRestoreCommandName.RESTORE_RETAINED_VERSION.value
        ):
            return self._handle_version_restore_request(command, identity)
        if (
            command.command_name
            == VersionRestoreCommandName.UNDO_RETAINED_VERSION_RESTORE.value
        ):
            return self._handle_version_restore_undo_request(command, identity)
        if command.command_name == RunCommandName.START_RUN.value:
            return self._handle_start_run(command, identity)
        if command.command_name in {
            RunCommandName.PAUSE_RUN.value,
            RunCommandName.RESUME_RUN.value,
            RunCommandName.STOP_RUN_AFTER_ACTIVE_FILE.value,
        }:
            return self._handle_run_control(command, identity)
        if command.command_name == TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value:
            return self._handle_enqueue_trigger_occurrence(command, identity)
        if (
            command.command_name
            == StateMaintenanceCommandName.RESTORE_STATE_FROM_BACKUP_SET.value
        ):
            return self._handle_restore_state_from_backup_set(command)
        receipt_response = self._record_terminal_rejected_receipt(
            command,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {
            "command_name": command.command_name,
            "recognized": False,
        }
        self._add_receipt_payload(response_payload, command.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED,
            response_payload,
        )

    def _authorize_client_request(self, client_instance_id: str) -> IpcResponse | None:
        now = self.monotonic_clock()
        rate_limit_response = self._admit_global_frame(now)
        if rate_limit_response is not None:
            return rate_limit_response
        self._expire_idle_clients(now)
        accepted_client = self._accepted_clients.get(client_instance_id)
        if accepted_client is None:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        return self._admit_client_frame(accepted_client, now)

    def _admit_global_frame(self, now: float) -> IpcResponse | None:
        self._discard_expired_frame_times(self._global_frame_times, now)
        if (
            len(self._global_frame_times)
            >= self.resource_limits.max_global_frames_per_window
        ):
            return self._rate_limit_response(
                scope="GLOBAL_FRAMES",
                limit=self.resource_limits.max_global_frames_per_window,
                retry_after_seconds=self._retry_after_seconds(
                    self._global_frame_times,
                    now,
                ),
            )
        self._global_frame_times.append(now)
        return None

    def _admit_client_frame(
        self,
        accepted_client: _AcceptedClient,
        now: float,
    ) -> IpcResponse | None:
        self._discard_expired_frame_times(accepted_client.frame_times, now)
        if (
            len(accepted_client.frame_times)
            >= self.resource_limits.max_client_frames_per_window
        ):
            return self._rate_limit_response(
                scope="CLIENT_FRAMES",
                limit=self.resource_limits.max_client_frames_per_window,
                retry_after_seconds=self._retry_after_seconds(
                    accepted_client.frame_times,
                    now,
                ),
            )
        accepted_client.frame_times.append(now)
        accepted_client.last_seen_monotonic = now
        return None

    def _discard_expired_frame_times(
        self,
        frame_times: deque[float],
        now: float,
    ) -> None:
        cutoff = now - self.resource_limits.frame_window_seconds
        while frame_times and frame_times[0] <= cutoff:
            frame_times.popleft()

    def _retry_after_seconds(
        self,
        frame_times: deque[float],
        now: float,
    ) -> float:
        if not frame_times:
            return 0.0
        return max(
            0.0,
            frame_times[0] + self.resource_limits.frame_window_seconds - now,
        )

    def _expire_idle_clients(self, now: float) -> None:
        cutoff = now - self.resource_limits.accepted_client_idle_seconds
        expired_ids = [
            client_instance_id
            for client_instance_id, accepted_client in self._accepted_clients.items()
            if accepted_client.last_seen_monotonic <= cutoff
        ]
        for client_instance_id in expired_ids:
            del self._accepted_clients[client_instance_id]

    def _client_capacity_response(self, now: float) -> IpcResponse:
        oldest_last_seen = min(
            accepted_client.last_seen_monotonic
            for accepted_client in self._accepted_clients.values()
        )
        retry_after_seconds = max(
            0.0,
            oldest_last_seen + self.resource_limits.accepted_client_idle_seconds - now,
        )
        return self._rate_limit_response(
            scope="ACCEPTED_CLIENTS",
            limit=self.resource_limits.max_accepted_clients,
            retry_after_seconds=retry_after_seconds,
        )

    def _rate_limit_response(
        self,
        *,
        scope: str,
        limit: int,
        retry_after_seconds: float,
    ) -> IpcResponse:
        return IpcResponse.rejected(
            IpcReason.IPC_RATE_LIMITED,
            {
                "limit_scope": scope,
                "limit": limit,
                "window_ms": (
                    int(self.resource_limits.frame_window_seconds * 1000)
                    if scope != "ACCEPTED_CLIENTS"
                    else int(self.resource_limits.accepted_client_idle_seconds * 1000)
                ),
                "retry_after_ms": max(1, ceil(retry_after_seconds * 1000)),
            },
        )

    def _handle_restore_state_from_backup_set(
        self,
        envelope: IpcCommandEnvelope,
    ) -> IpcResponse:
        try:
            command = parse_restore_state_from_backup_set_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except StateMaintenancePayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            response_payload = _state_restore_response_payload(
                envelope=envelope,
                command=command,
                recognized=True,
                restored=False,
                restore_receipt=None,
                mutations_enabled=True,
                executor_configured=self.state_restore_executor is not None,
            )
            response_payload["error_code"] = "RESTORE_STATE_REQUIRES_READ_ONLY_IPC_MODE"
            return IpcResponse.rejected(
                IpcReason.COMMAND_PRECONDITION_FAILED,
                response_payload,
            )

        if self.state_restore_executor is None:
            response_payload = _state_restore_response_payload(
                envelope=envelope,
                command=command,
                recognized=True,
                restored=False,
                restore_receipt=None,
                mutations_enabled=self.status.mutations_enabled,
                executor_configured=False,
            )
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
                response_payload,
            )

        try:
            restore_receipt = self.state_restore_executor(command)
        except Exception as exc:
            response_payload = _state_restore_response_payload(
                envelope=envelope,
                command=command,
                recognized=True,
                restored=False,
                restore_receipt=None,
                mutations_enabled=self.status.mutations_enabled,
                executor_configured=True,
            )
            response_payload["error_code"] = str(exc) or type(exc).__name__
            return IpcResponse.rejected(
                IpcReason.COMMAND_PRECONDITION_FAILED,
                response_payload,
            )

        response_payload = _state_restore_response_payload(
            envelope=envelope,
            command=command,
            recognized=True,
            restored=True,
            restore_receipt=restore_receipt,
            mutations_enabled=self.status.mutations_enabled,
            executor_configured=True,
        )
        return IpcResponse.accepted(response_payload)

    def _handle_enqueue_trigger_occurrence(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_enqueue_trigger_occurrence_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except TriggerOccurrencePayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._dispatch_enqueue_trigger_occurrence(
                envelope, identity, command
            )

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload = command.response_payload(mutations_enabled=False)
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED, response_payload
        )

    def _dispatch_enqueue_trigger_occurrence(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: EnqueueTriggerOccurrenceCommand,
    ) -> IpcResponse:
        return self._run_command_effect_transaction(
            lambda: self._dispatch_enqueue_trigger_occurrence_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_enqueue_trigger_occurrence_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: EnqueueTriggerOccurrenceCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.schedule_store is None
            or self.trigger_occurrence_store is None
            or self.standard_backup_job_detail_store is None
            or self.backup_analysis_request_store is None
        ):
            return self._reject_config_missing_trigger_occurrence(
                envelope, identity, command
            )

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)

        replay = self._trigger_occurrence_replay_response(envelope, command, receipt)
        if replay is not None:
            return replay

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = enqueue_trigger_occurrence_analysis(
            command=command,
            installation_id=self.installation_id,
            schedules=self.schedule_store,
            occurrences=self.trigger_occurrence_store,
            jobs=self.standard_backup_job_detail_store,
            analysis_requests=self.backup_analysis_request_store,
        )
        if not outcome.enqueued or outcome.analysis_request is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _trigger_occurrence_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="backup_analysis_request",
            result_entity_id=outcome.analysis_request.request_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        if not outcome.deduplicated:
            self._enqueue_command_effect_outbox(receipt)

        payload = _trigger_occurrence_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_trigger_occurrence(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: EnqueueTriggerOccurrenceCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _trigger_occurrence_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
        )

    def _trigger_occurrence_replay_response(
        self,
        envelope: IpcCommandEnvelope,
        command: EnqueueTriggerOccurrenceCommand,
        receipt: CommandReceipt,
    ) -> IpcResponse | None:
        if receipt.state is CommandReceiptState.RECEIVED:
            return None
        analysis_request = None
        run = None
        if (
            receipt.result_entity_id is not None
            and self.backup_analysis_request_store is not None
        ):
            analysis_request = (
                self.backup_analysis_request_store.load_backup_analysis_request(
                    receipt.result_entity_id
                )
            )
            if (
                analysis_request is not None
                and analysis_request.started_run_id is not None
                and self.run_store is not None
            ):
                run = self.run_store.load_started_run(
                    analysis_request.started_run_id
                )
        payload = _trigger_occurrence_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            analysis_request=analysis_request,
            run=run,
        )
        payload["enqueued"] = receipt.state is CommandReceiptState.SUCCEEDED
        payload["created"] = False
        payload["idempotent_replay"] = True
        self._add_receipt_payload(payload, envelope.idempotency_key)
        if receipt.state is CommandReceiptState.SUCCEEDED:
            return IpcResponse.accepted(payload)
        if receipt.state is CommandReceiptState.REJECTED:
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
        return IpcResponse.accepted(payload)

    def _handle_save_standard_backup_draft(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_save_standard_backup_draft_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobDraftPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if self.status.mutations_enabled:
            try:
                return self._run_command_effect_transaction(
                    lambda: self._dispatch_save_standard_backup_draft_in_transaction(
                        envelope,
                        identity,
                        command,
                    )
                )
            except ValueError:
                return IpcResponse.rejected(
                    IpcReason.COMMAND_PRECONDITION_FAILED,
                    {
                        "command_name": envelope.command_name,
                        "draft_id": command.draft.draft_id,
                        "saved": False,
                        "retryable": True,
                    },
                )

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = {
            "command_name": envelope.command_name,
            "draft_id": command.draft.draft_id,
            "recognized": True,
            "mutations_enabled": False,
            "saved": False,
        }
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)

    def _dispatch_save_standard_backup_draft_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: SaveStandardBackupDraftCommand,
    ) -> IpcResponse:
        if self.command_receipt_store is None or self.job_draft_store is None:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
                {
                    "command_name": envelope.command_name,
                    "draft_id": command.draft.draft_id,
                    "recognized": True,
                    "mutations_enabled": True,
                    "saved": False,
                },
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "draft_id": command.draft.draft_id,
            "recognized": True,
            "mutations_enabled": True,
            "saved": receipt.state is CommandReceiptState.SUCCEEDED,
            "idempotent_replay": receipt.state is not CommandReceiptState.RECEIVED,
        }
        if receipt.state is not CommandReceiptState.RECEIVED:
            self._add_receipt_payload(payload, envelope.idempotency_key)
            if receipt.state is CommandReceiptState.REJECTED:
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        save_standard_backup_draft(command=command, drafts=self.job_draft_store)
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="standard_backup_job_draft",
            result_entity_id=command.draft.draft_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload["saved"] = True
        payload["idempotent_replay"] = False
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _handle_create_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_create_standard_backup_job_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobCreationPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._dispatch_create_standard_backup_job(
                envelope, identity, command
            )

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "draft_id": command.draft_id,
            "recognized": True,
            "mutations_enabled": self.status.mutations_enabled,
        }
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        if self.job_draft_store is not None:
            response_payload["readiness"] = evaluate_standard_backup_job_creation(
                command=command,
                drafts=self.job_draft_store,
            ).to_dict()
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED, response_payload
        )

    def _handle_update_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_update_standard_backup_job_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobEditingPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if self.status.mutations_enabled:
            return self._dispatch_update_standard_backup_job(
                envelope,
                identity,
                command,
            )
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _job_editing_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=False,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)

    def _handle_register_writable_targets(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_register_writable_targets_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except WritableEndpointRegistrationPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            response = self._dispatch_register_writable_targets(
                envelope,
                identity,
                command,
            )
            response = self._refresh_endpoint_classification_after_job_command(response)
            response = self._refresh_job_snapshots_after_job_command(response)
            return self._refresh_initial_backup_plan_after_job_command(response)

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _writable_endpoint_registration_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=False,
            recognized=True,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)

    def _dispatch_register_writable_targets(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: RegisterWritableTargetsCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.writable_endpoint_registration is None
        ):
            return self._reject_config_missing_writable_target_registration(
                envelope,
                identity,
                command,
            )

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is CommandReceiptState.REJECTED:
            payload = _writable_endpoint_registration_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                recognized=True,
                idempotent_replay=True,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)

        lifecycle = (
            None
            if self.job_lifecycle_store is None
            else self.job_lifecycle_store.load_job_lifecycle(command.job_id)
        )
        if lifecycle is not None and lifecycle.state is JobLifecycleState.ARCHIVED:
            return self._run_command_effect_transaction(
                lambda: self._reject_writable_target_registration(
                    envelope,
                    command,
                    validation_code="JOB_ARCHIVED",
                    next_action="Reactivate the backup job before registering its targets.",
                )
            )

        try:
            report = self.writable_endpoint_registration.register_job_targets(
                job_id=command.job_id,
                job_revision_id=command.job_revision_id,
                command_request_id=envelope.request_id,
                command_idempotency_key=envelope.idempotency_key,
                observed_utc=(
                    self.writable_endpoint_registration_utc_now()
                    if self.writable_endpoint_registration_utc_now is not None
                    else _system_utc_now()
                ),
            )
        except WritableEndpointRegistrationError as exc:
            validation_code = exc.validation_code
            next_action = exc.next_action
            return self._run_command_effect_transaction(
                lambda: self._reject_writable_target_registration(
                    envelope,
                    command,
                    validation_code=validation_code,
                    next_action=next_action,
                )
            )

        return self._run_command_effect_transaction(
            lambda: self._accept_writable_target_registration(
                envelope,
                command,
                report,
            )
        )

    def _accept_writable_target_registration(
        self,
        envelope: IpcCommandEnvelope,
        command: RegisterWritableTargetsCommand,
        report: WritableEndpointRegistrationReport,
    ) -> IpcResponse:
        assert self.command_receipt_store is not None
        receipt = self.command_receipt_store.load_command_receipt(
            envelope.idempotency_key
        )
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is CommandReceiptState.RECEIVED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.VALIDATED,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.VALIDATED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.EFFECT_PREPARED,
                result_entity_type="writable_endpoint_registration",
                result_entity_id=report.intent_id or command.job_id,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.EFFECT_PREPARED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.ACCEPTED,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.ACCEPTED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.SUCCEEDED,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            self._enqueue_command_effect_outbox(receipt)
        payload = _writable_endpoint_registration_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            report=report,
            idempotent_replay=(
                report.idempotent_replay
                or receipt.state is not CommandReceiptState.SUCCEEDED
            ),
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_writable_target_registration(
        self,
        envelope: IpcCommandEnvelope,
        command: RegisterWritableTargetsCommand,
        *,
        validation_code: str,
        next_action: str,
    ) -> IpcResponse:
        assert self.command_receipt_store is not None
        receipt = self.command_receipt_store.load_command_receipt(
            envelope.idempotency_key
        )
        if receipt is not None and receipt.state in {
            CommandReceiptState.RECEIVED,
            CommandReceiptState.VALIDATED,
        }:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        payload = _writable_endpoint_registration_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            validation_code=validation_code,
            next_action=next_action,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

    def _reject_config_missing_writable_target_registration(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: RegisterWritableTargetsCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _writable_endpoint_registration_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
        )

    def _handle_controlled_endpoint_takeover(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_start_controlled_endpoint_takeover_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except EndpointTakeoverPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _endpoint_takeover_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=False,
                recognized=True,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        response = self._dispatch_controlled_endpoint_takeover(
            envelope,
            identity,
            command,
        )
        return self._refresh_endpoint_classification_after_job_command(response)

    def _dispatch_controlled_endpoint_takeover(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartControlledEndpointTakeoverCommand,
    ) -> IpcResponse:
        if self.command_receipt_store is None or self.endpoint_takeover is None:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _endpoint_takeover_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                recognized=True,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
            )

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is CommandReceiptState.REJECTED:
            payload = _endpoint_takeover_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                recognized=True,
                idempotent_replay=True,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)

        lifecycle = (
            None
            if self.job_lifecycle_store is None
            else self.job_lifecycle_store.load_job_lifecycle(command.job_id)
        )
        if lifecycle is not None and lifecycle.state is JobLifecycleState.ARCHIVED:
            return self._run_command_effect_transaction(
                lambda: self._reject_controlled_endpoint_takeover(
                    envelope,
                    command,
                    validation_code="JOB_ARCHIVED",
                    next_action="Reactivate the backup job before taking over its endpoint.",
                )
            )
        try:
            report = self.endpoint_takeover.start_controlled_takeover(
                command=command,
                observed_utc=(
                    self.endpoint_takeover_utc_now()
                    if self.endpoint_takeover_utc_now is not None
                    else _system_utc_now()
                ),
            )
        except EndpointTakeoverError as exc:
            validation_code = exc.validation_code
            next_action = exc.next_action
            return self._run_command_effect_transaction(
                lambda: self._reject_controlled_endpoint_takeover(
                    envelope,
                    command,
                    validation_code=validation_code,
                    next_action=next_action,
                )
            )
        return self._run_command_effect_transaction(
            lambda: self._accept_controlled_endpoint_takeover(
                envelope,
                command,
                report,
            )
        )

    def _accept_controlled_endpoint_takeover(
        self,
        envelope: IpcCommandEnvelope,
        command: StartControlledEndpointTakeoverCommand,
        report: EndpointTakeoverReport,
    ) -> IpcResponse:
        assert self.command_receipt_store is not None
        receipt = self.command_receipt_store.load_command_receipt(
            envelope.idempotency_key
        )
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is CommandReceiptState.RECEIVED:
            receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
            self.command_receipt_store.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.VALIDATED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.EFFECT_PREPARED,
                result_entity_type="controlled_endpoint_takeover",
                result_entity_id=report.intent_id,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.EFFECT_PREPARED:
            receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
            self.command_receipt_store.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.ACCEPTED:
            receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
            self.command_receipt_store.update_command_receipt(receipt)
            self._enqueue_command_effect_outbox(receipt)
        payload = _endpoint_takeover_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            report=report,
            idempotent_replay=report.idempotent_replay,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_controlled_endpoint_takeover(
        self,
        envelope: IpcCommandEnvelope,
        command: StartControlledEndpointTakeoverCommand,
        *,
        validation_code: str,
        next_action: str,
    ) -> IpcResponse:
        assert self.command_receipt_store is not None
        receipt = self.command_receipt_store.load_command_receipt(
            envelope.idempotency_key
        )
        if receipt is not None and receipt.state in {
            CommandReceiptState.RECEIVED,
            CommandReceiptState.VALIDATED,
        }:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        payload = _endpoint_takeover_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            recognized=True,
            validation_code=validation_code,
            next_action=next_action,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

    def _handle_start_run(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_start_run_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except RunStartViolation:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._dispatch_start_run(envelope, identity, command)

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "plan_id": command.plan_id,
            "recognized": True,
            "mutations_enabled": self.status.mutations_enabled,
        }
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        if self.plan_store is not None:
            response_payload["readiness"] = evaluate_start_run(
                command=command,
                plans=self.plan_store,
            ).to_dict()
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED, response_payload
        )

    def _handle_check_backup(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_check_backup_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except BackupAnalysisPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._run_command_effect_transaction(
                lambda: self._dispatch_check_backup_in_transaction(
                    envelope,
                    identity,
                    command,
                )
            )

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _backup_analysis_response_payload(
            envelope=envelope,
            job_id=command.job_id,
            mutations_enabled=False,
            recognized=True,
            request=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)

    def _dispatch_check_backup_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CheckBackupCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.backup_analysis_request_store is None
            or self.standard_backup_job_detail_store is None
            or self.run_store is None
        ):
            return self._reject_config_missing_check_backup(
                envelope,
                identity,
                command,
            )

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is not CommandReceiptState.RECEIVED:
            request = (
                None
                if receipt.result_entity_id is None
                else self.backup_analysis_request_store.load_backup_analysis_request(
                    receipt.result_entity_id
                )
            )
            payload = _backup_analysis_response_payload(
                envelope=envelope,
                job_id=command.job_id,
                mutations_enabled=True,
                recognized=True,
                request=request,
            )
            payload["idempotent_replay"] = True
            self._add_receipt_payload(payload, envelope.idempotency_key)
            if receipt.state is CommandReceiptState.REJECTED:
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            return IpcResponse.accepted(payload)

        job = self.standard_backup_job_detail_store.load_standard_backup_job_detail(
            command.job_id
        )
        lifecycle = (
            None
            if self.job_lifecycle_store is None
            else self.job_lifecycle_store.load_job_lifecycle(command.job_id)
        )
        if (
            job is None
            or (
                self.job_lifecycle_store is not None
                and (
                    lifecycle is None
                    or lifecycle.state is not JobLifecycleState.ACTIVE
                )
            )
            or self.run_store.load_active_run_for_job(command.job_id) is not None
        ):
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _backup_analysis_response_payload(
                envelope=envelope,
                job_id=command.job_id,
                mutations_enabled=True,
                recognized=True,
                request=None,
            )
            payload["reason_code"] = (
                "BACKUP_ANALYSIS_JOB_NOT_FOUND"
                if job is None
                or (self.job_lifecycle_store is not None and lifecycle is None)
                else "BACKUP_ANALYSIS_JOB_ARCHIVED"
                if lifecycle is not None
                and lifecycle.state is JobLifecycleState.ARCHIVED
                else "BACKUP_ANALYSIS_ACTIVE_RUN"
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        request = self.backup_analysis_request_store.enqueue_backup_analysis(
            BackupAnalysisRequest(
                request_id=envelope.request_id,
                command_idempotency_key=envelope.idempotency_key,
                job_id=job.job_id,
                job_revision_id=job.job_revision_id,
                state=BackupAnalysisRequestState.QUEUED,
                requested_utc=_system_utc_now(),
                start_when_safe=command.start_when_safe,
            )
        )
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="backup_analysis_request",
            result_entity_id=request.request_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)

        payload = _backup_analysis_response_payload(
            envelope=envelope,
            job_id=command.job_id,
            mutations_enabled=True,
            recognized=True,
            request=request,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_check_backup(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CheckBackupCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _backup_analysis_response_payload(
            envelope=envelope,
            job_id=command.job_id,
            mutations_enabled=True,
            recognized=True,
            request=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
        )

    def _handle_configure_daily_backup_schedule(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_configure_daily_backup_schedule_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobSchedulingPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _job_scheduling_response_payload(
                envelope=envelope,
                mutations_enabled=False,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_configure_daily_backup_schedule_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_configure_daily_backup_schedule_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: ConfigureDailyBackupScheduleCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.standard_backup_job_detail_store is None
            or self.schedule_store is None
            or self.external_resource_state_store is None
            or self.task_scheduler_executable_path is None
            or self.task_scheduler_time_zone_id is None
        ):
            return self._reject_config_missing_job_scheduling(envelope, identity)
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is not CommandReceiptState.RECEIVED:
            outcome = self._replay_job_scheduling(command, receipt)
            payload = _job_scheduling_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            if receipt.state is CommandReceiptState.REJECTED:
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = configure_daily_backup_schedule(
            command=command,
            jobs=self.standard_backup_job_detail_store,
            schedules=self.schedule_store,
            installation_id=self.installation_id,
            executable_path=self.task_scheduler_executable_path,
            time_zone_id=self.task_scheduler_time_zone_id,
        )
        if not outcome.configured or outcome.schedule is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                result_entity_type="backup_automation_validation",
                result_entity_id=outcome.validation_code,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _job_scheduling_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        resource = self.external_resource_state_store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id=outcome.schedule.schedule_id,
            desired_generation=outcome.schedule.definition_generation,
            desired_hash=outcome.schedule.desired_definition_hash,
        )
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="backup_automation_schedule",
            result_entity_id=outcome.schedule.schedule_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _job_scheduling_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=outcome,
            reconciliation_state=resource.state.value,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _replay_job_scheduling(
        self,
        command: ConfigureDailyBackupScheduleCommand,
        receipt: CommandReceipt,
    ) -> JobSchedulingOutcome:
        if receipt.state is CommandReceiptState.REJECTED:
            validation_code = (
                receipt.result_entity_id
                if receipt.result_entity_type == "backup_automation_validation"
                and receipt.result_entity_id is not None
                else "BACKUP_AUTOMATION_COMMAND_REJECTED"
            )
            return JobSchedulingOutcome(
                configured=False,
                validation_code=validation_code,
                next_action="Refresh the job before changing automation.",
                idempotent_replay=True,
            )
        assert self.schedule_store is not None
        schedule = self.schedule_store.load_schedule(
            receipt.result_entity_id or daily_backup_schedule_id(command.job_id)
        )
        return JobSchedulingOutcome(
            configured=True,
            validation_code="BACKUP_AUTOMATION_SCHEDULE_UPDATED",
            next_action="Windows Task Scheduler reconciliation is pending.",
            schedule=schedule,
            idempotent_replay=True,
        )

    def _reject_config_missing_job_scheduling(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _job_scheduling_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
            payload,
        )

    def _handle_duplicate_scan_command(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_duplicate_scan_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except DuplicateScanError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _duplicate_scan_response_payload(
                envelope=envelope,
                analysis_id=command.analysis_id,
                mutations_enabled=False,
                status=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_duplicate_scan_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_duplicate_scan_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: DuplicateScanCommand,
    ) -> IpcResponse:
        if self.command_receipt_store is None or self.duplicate_scan_store is None:
            return self._reject_config_missing_duplicate_scan(
                envelope,
                identity,
                command,
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is not CommandReceiptState.RECEIVED:
            status = self.duplicate_scan_store.load_duplicate_scan(command.analysis_id)
            payload = _duplicate_scan_response_payload(
                envelope=envelope,
                analysis_id=command.analysis_id,
                mutations_enabled=True,
                status=status,
            )
            payload["idempotent_replay"] = True
            self._add_receipt_payload(payload, envelope.idempotency_key)
            if receipt.state is CommandReceiptState.REJECTED:
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        occurred_utc = (
            self.duplicate_scan_utc_now()
            if self.duplicate_scan_utc_now is not None
            else _system_utc_now()
        )
        try:
            if (
                envelope.command_name
                == DuplicateScanCommandName.START_DUPLICATE_SCAN.value
            ):
                status = self.duplicate_scan_store.start_scan(
                    analysis_id=command.analysis_id,
                    requested_utc=occurred_utc,
                )
            elif (
                envelope.command_name
                == DuplicateScanCommandName.PAUSE_DUPLICATE_SCAN.value
            ):
                status = self.duplicate_scan_store.pause_scan(
                    analysis_id=command.analysis_id,
                    observed_utc=occurred_utc,
                )
            else:
                status = self.duplicate_scan_store.resume_scan(
                    analysis_id=command.analysis_id,
                    observed_utc=occurred_utc,
                )
        except DuplicateScanError:
            status = None
        if status is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _duplicate_scan_response_payload(
                envelope=envelope,
                analysis_id=command.analysis_id,
                mutations_enabled=True,
                status=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="duplicate_scan",
            result_entity_id=status.scan_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _duplicate_scan_response_payload(
            envelope=envelope,
            analysis_id=command.analysis_id,
            mutations_enabled=True,
            status=status,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_duplicate_scan(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: DuplicateScanCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _duplicate_scan_response_payload(
            envelope=envelope,
            analysis_id=command.analysis_id,
            mutations_enabled=True,
            status=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
            payload,
        )

    def _handle_duplicate_group_review(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_duplicate_group_review_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except DuplicateScanError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _duplicate_group_review_response_payload(
                envelope=envelope,
                group_id=command.group_id,
                mutations_enabled=False,
                group=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_duplicate_group_review_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_duplicate_group_review_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: DuplicateGroupReviewCommand,
    ) -> IpcResponse:
        if self.command_receipt_store is None or self.duplicate_scan_store is None:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is not CommandReceiptState.RECEIVED:
            group = self.duplicate_scan_store.load_duplicate_group(command.group_id)
            payload = _duplicate_group_review_response_payload(
                envelope=envelope,
                group_id=command.group_id,
                mutations_enabled=True,
                group=group,
            )
            payload["idempotent_replay"] = True
            self._add_receipt_payload(payload, envelope.idempotency_key)
            if receipt.state is CommandReceiptState.REJECTED:
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        group = self.duplicate_scan_store.mark_duplicate_group_reviewed(
            group_id=command.group_id,
            expected_review_state=command.expected_review_state,
        )
        if group is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _duplicate_group_review_response_payload(
                envelope=envelope,
                group_id=command.group_id,
                mutations_enabled=True,
                group=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="duplicate_group",
            result_entity_id=group.group_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _duplicate_group_review_response_payload(
            envelope=envelope,
            group_id=command.group_id,
            mutations_enabled=True,
            group=group,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _handle_job_lifecycle(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_change_job_lifecycle_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobLifecyclePayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _job_lifecycle_response_payload(
                envelope=envelope,
                mutations_enabled=False,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_job_lifecycle_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_job_lifecycle_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: ChangeJobLifecycleCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.job_lifecycle_store is None
            or (
                envelope.command_name
                == JobLifecycleCommandName.REACTIVATE_STANDARD_BACKUP_JOB.value
                and self.backup_analysis_request_store is None
            )
        ):
            return self._reject_config_missing_job_lifecycle(
                envelope,
                identity,
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is not CommandReceiptState.RECEIVED:
            outcome = self._replay_job_lifecycle(envelope, command)
            payload = _job_lifecycle_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            if receipt.state is CommandReceiptState.REJECTED:
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        occurred_utc = (
            self.job_lifecycle_utc_now()
            if self.job_lifecycle_utc_now is not None
            else _system_utc_now()
        )
        if (
            envelope.command_name
            == JobLifecycleCommandName.ARCHIVE_STANDARD_BACKUP_JOB.value
        ):
            outcome = self.job_lifecycle_store.archive_standard_backup_job(
                command=command,
                occurred_utc=occurred_utc,
            )
        else:
            outcome = self.job_lifecycle_store.reactivate_standard_backup_job(
                command=command,
                occurred_utc=occurred_utc,
            )
        if not outcome.applied or outcome.record is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _job_lifecycle_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        if (
            envelope.command_name
            == JobLifecycleCommandName.REACTIVATE_STANDARD_BACKUP_JOB.value
        ):
            assert self.backup_analysis_request_store is not None
            self.backup_analysis_request_store.enqueue_backup_analysis(
                BackupAnalysisRequest(
                    request_id=envelope.request_id,
                    command_idempotency_key=envelope.idempotency_key,
                    job_id=outcome.record.job_id,
                    job_revision_id=outcome.record.job_revision_id,
                    state=BackupAnalysisRequestState.QUEUED,
                    requested_utc=occurred_utc,
                    start_when_safe=False,
                )
            )
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="job_lifecycle_event",
            result_entity_id=envelope.request_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _job_lifecycle_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _replay_job_lifecycle(
        self,
        envelope: IpcCommandEnvelope,
        command: ChangeJobLifecycleCommand,
    ) -> JobLifecycleTransitionOutcome:
        assert self.job_lifecycle_store is not None
        occurred_utc = (
            self.job_lifecycle_utc_now()
            if self.job_lifecycle_utc_now is not None
            else _system_utc_now()
        )
        if (
            envelope.command_name
            == JobLifecycleCommandName.ARCHIVE_STANDARD_BACKUP_JOB.value
        ):
            return self.job_lifecycle_store.archive_standard_backup_job(
                command=command,
                occurred_utc=occurred_utc,
            )
        return self.job_lifecycle_store.reactivate_standard_backup_job(
            command=command,
            occurred_utc=occurred_utc,
        )

    def _reject_config_missing_job_lifecycle(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _job_lifecycle_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
            payload,
        )

    def _handle_version_restore_protection(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_protect_retained_version_for_restore_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except RetainedVersionHistoryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _version_restore_protection_response_payload(
                envelope=envelope,
                mutations_enabled=False,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_version_restore_protection_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_version_restore_protection_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: ProtectRetainedVersionForRestoreCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.version_restore_protection_store is None
        ):
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _version_restore_protection_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
                payload,
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        created_utc = (
            self.retained_version_utc_now()
            if self.retained_version_utc_now is not None
            else _system_utc_now()
        )
        if receipt.state is not CommandReceiptState.RECEIVED:
            if receipt.state is CommandReceiptState.REJECTED:
                outcome = VersionRestoreProtectionOutcome(
                    protected=False,
                    validation_code="VERSION_RESTORE_PROTECTION_REJECTED",
                    next_action="Refresh version history before trying again.",
                )
                payload = _version_restore_protection_response_payload(
                    envelope=envelope,
                    mutations_enabled=True,
                    outcome=outcome,
                )
                self._add_receipt_payload(payload, envelope.idempotency_key)
                return IpcResponse.rejected(
                    _receipt_rejection_reason(receipt),
                    payload,
                )
            outcome = self.version_restore_protection_store.protect_retained_version_for_restore(
                command=command,
                created_utc=created_utc,
            )
            payload = _version_restore_protection_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = self.version_restore_protection_store.protect_retained_version_for_restore(
            command=command,
            created_utc=created_utc,
        )
        if not outcome.protected or outcome.version is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _version_restore_protection_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="version_retention_hold",
            result_entity_id=outcome.version.hold_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _version_restore_protection_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _handle_version_restore_request(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_restore_retained_version_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except RetainedVersionHistoryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _version_restore_request_response_payload(
                envelope=envelope,
                mutations_enabled=False,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_version_restore_request_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_version_restore_request_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: RestoreRetainedVersionCommand,
    ) -> IpcResponse:
        if self.command_receipt_store is None or self.version_restore_request_store is None:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _version_restore_request_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
                payload,
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        created_utc = (
            self.retained_version_utc_now()
            if self.retained_version_utc_now is not None
            else _system_utc_now()
        )
        if receipt.state is not CommandReceiptState.RECEIVED:
            if receipt.state is CommandReceiptState.REJECTED:
                outcome = VersionRestoreRequestOutcome(
                    scheduled=False,
                    validation_code="VERSION_RESTORE_REQUEST_REJECTED",
                    next_action="Refresh version history before trying again.",
                )
                payload = _version_restore_request_response_payload(
                    envelope=envelope,
                    mutations_enabled=True,
                    outcome=outcome,
                )
                self._add_receipt_payload(payload, envelope.idempotency_key)
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            outcome = self.version_restore_request_store.request_retained_version_restore(
                command=command,
                created_utc=created_utc,
            )
            payload = _version_restore_request_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = self.version_restore_request_store.request_retained_version_restore(
            command=command,
            created_utc=created_utc,
        )
        if not outcome.scheduled or outcome.restore_id is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _version_restore_request_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="retained_version_restore",
            result_entity_id=outcome.restore_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _version_restore_request_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _handle_version_restore_undo_request(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_undo_retained_version_restore_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except RetainedVersionHistoryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if not self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _version_restore_undo_request_response_payload(
                envelope=envelope,
                mutations_enabled=False,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)
        return self._run_command_effect_transaction(
            lambda: self._dispatch_version_restore_undo_request_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_version_restore_undo_request_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: UndoRetainedVersionRestoreCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.version_restore_undo_request_store is None
        ):
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            payload = _version_restore_undo_request_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=None,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
                payload,
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        created_utc = (
            self.retained_version_utc_now()
            if self.retained_version_utc_now is not None
            else _system_utc_now()
        )
        if receipt.state is not CommandReceiptState.RECEIVED:
            if receipt.state is CommandReceiptState.REJECTED:
                outcome = VersionRestoreUndoRequestOutcome(
                    scheduled=False,
                    validation_code="VERSION_RESTORE_UNDO_REQUEST_REJECTED",
                    next_action="Refresh version history before trying again.",
                )
                payload = _version_restore_undo_request_response_payload(
                    envelope=envelope,
                    mutations_enabled=True,
                    outcome=outcome,
                )
                self._add_receipt_payload(payload, envelope.idempotency_key)
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            outcome = (
                self.version_restore_undo_request_store.request_retained_version_restore_undo(
                    command=command,
                    created_utc=created_utc,
                )
            )
            payload = _version_restore_undo_request_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = (
            self.version_restore_undo_request_store.request_retained_version_restore_undo(
                command=command,
                created_utc=created_utc,
            )
        )
        if not outcome.scheduled or outcome.restore_id is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _version_restore_undo_request_response_payload(
                envelope=envelope,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="retained_version_restore_undo",
            result_entity_id=outcome.restore_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _version_restore_undo_request_response_payload(
            envelope=envelope,
            mutations_enabled=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _handle_run_control(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_run_control_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except RunStartViolation:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._dispatch_run_control(envelope, identity, command)

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _run_control_response_payload(
            envelope=envelope,
            run_id=command.run_id,
            mutations_enabled=False,
            recognized=True,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, payload)

    def _dispatch_run_control(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: RunControlCommand,
    ) -> IpcResponse:
        return self._run_command_effect_transaction(
            lambda: self._dispatch_run_control_in_transaction(
                envelope,
                identity,
                command,
            )
        )

    def _dispatch_run_control_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: RunControlCommand,
    ) -> IpcResponse:
        if self.command_receipt_store is None or self.run_control_store is None:
            return self._reject_config_missing_run_control(envelope, identity, command)

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)

        replay = self._run_control_replay_response(envelope, command, receipt)
        if replay is not None:
            return replay

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        if envelope.command_name == RunCommandName.PAUSE_RUN.value:
            outcome = request_run_pause(command=command, runs=self.run_control_store)
        elif envelope.command_name == RunCommandName.RESUME_RUN.value:
            outcome = resume_paused_run(command=command, runs=self.run_control_store)
        else:
            outcome = request_run_stop_after_active_file(
                command=command,
                runs=self.run_control_store,
            )
        if not outcome.applied or outcome.run is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _run_control_response_payload(
                envelope=envelope,
                run_id=command.run_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="run",
            result_entity_id=outcome.run.run_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)

        payload = _run_control_response_payload(
            envelope=envelope,
            run_id=command.run_id,
            mutations_enabled=True,
            recognized=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_run_control(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: RunControlCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _run_control_response_payload(
            envelope=envelope,
            run_id=command.run_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
        )

    def _run_control_replay_response(
        self,
        envelope: IpcCommandEnvelope,
        command: RunControlCommand,
        receipt: CommandReceipt,
    ) -> IpcResponse | None:
        if receipt.state is CommandReceiptState.RECEIVED:
            return None
        run = None
        if receipt.result_entity_id is not None and self.run_control_store is not None:
            run = self.run_control_store.load_started_run(receipt.result_entity_id)
        payload = _run_control_response_payload(
            envelope=envelope,
            run_id=command.run_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            run=run,
        )
        payload["applied"] = receipt.state is CommandReceiptState.SUCCEEDED
        payload["idempotent_replay"] = True
        self._add_receipt_payload(payload, envelope.idempotency_key)
        if receipt.state is CommandReceiptState.REJECTED:
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
        return IpcResponse.accepted(payload)

    def _dispatch_start_run(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartRunCommand,
    ) -> IpcResponse:
        prepared = self._run_command_effect_transaction(
            lambda: self._dispatch_start_run_in_transaction(envelope, identity, command)
        )
        coordinator = self.run_start_cross_store_coordinator
        if coordinator is None or prepared.status is IpcStatus.REJECTED:
            return prepared
        if self.run_store is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        run = self.run_store.load_started_run_by_idempotency_key(
            envelope.idempotency_key
        )
        if run is None:
            return prepared
        try:
            coordinator.advance_run_start(run.run_id)
        except CrossStoreHandoffError as exc:
            payload = dict(prepared.payload)
            payload["error_code"] = exc.validation_code
            payload["next_action"] = exc.next_action
            payload["retryable"] = True
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)
        released = self.run_store.load_started_run(run.run_id)
        payload = _start_run_response_payload(
            envelope=envelope,
            plan_id=command.plan_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            run=released,
        )
        payload["created"] = bool(prepared.payload.get("created", False))
        payload["idempotent_replay"] = bool(
            prepared.payload.get("idempotent_replay", False)
        )
        if "readiness" in prepared.payload:
            payload["readiness"] = prepared.payload["readiness"]
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _dispatch_start_run_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartRunCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.plan_store is None
            or self.run_store is None
            or self.run_id_factory is None
        ):
            return self._reject_config_missing_start_run(envelope, identity, command)

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)

        replay = self._start_run_replay_response(envelope, command, receipt)
        if replay is not None:
            return replay

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = start_run_from_sealed_plan(
            command=command,
            plans=self.plan_store,
            runs=self.run_store,
            id_factory=self.run_id_factory,
            operation_audit_store=self.operation_audit_read_store,
            job_lifecycle=self.job_lifecycle_store,
            defer_until_recovery_bound=(
                self.run_start_cross_store_coordinator is not None
            ),
        )
        if outcome.run is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _start_run_response_payload(
                envelope=envelope,
                plan_id=command.plan_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="run",
            result_entity_id=outcome.run.run_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        if self.run_start_cross_store_coordinator is not None:
            self.run_start_cross_store_coordinator.prepare_run_start(outcome.run)
            payload = _start_run_response_payload(
                envelope=envelope,
                plan_id=command.plan_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.accepted(payload)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)

        payload = _start_run_response_payload(
            envelope=envelope,
            plan_id=command.plan_id,
            mutations_enabled=True,
            recognized=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_start_run(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartRunCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = {
            "command_name": envelope.command_name,
            "plan_id": command.plan_id,
            "recognized": True,
            "mutations_enabled": True,
        }
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
        )

    def _start_run_replay_response(
        self,
        envelope: IpcCommandEnvelope,
        command: StartRunCommand,
        receipt: CommandReceipt,
    ) -> IpcResponse | None:
        if receipt.state is CommandReceiptState.RECEIVED:
            return None
        if self.run_store is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        run = self.run_store.load_started_run_by_idempotency_key(
            envelope.idempotency_key
        )
        payload = _start_run_response_payload(
            envelope=envelope,
            plan_id=command.plan_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            run=run,
        )
        payload["created"] = False
        payload["idempotent_replay"] = True
        self._add_receipt_payload(payload, envelope.idempotency_key)
        if receipt.state is CommandReceiptState.SUCCEEDED:
            return IpcResponse.accepted(payload)
        if receipt.state is CommandReceiptState.REJECTED:
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
        return IpcResponse.accepted(payload)

    def _dispatch_update_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: UpdateStandardBackupJobCommand,
    ) -> IpcResponse:
        prepared_command = command
        identity_validation_code: str | None = None
        existing_receipt = (
            self.command_receipt_store.load_command_receipt(
                envelope.idempotency_key
            )
            if self.command_receipt_store is not None
            else None
        )
        if (
            self.selected_directory_identity_probe is not None
            and self.standard_backup_job_revision_catalog is not None
            and (
                existing_receipt is None
                or existing_receipt.state is CommandReceiptState.RECEIVED
            )
        ):
            try:
                prepared_command = self._bind_job_edit_directory_identities(command)
            except SelectedDirectoryIdentityError as exc:
                identity_validation_code = str(exc)
        try:
            response = self._run_command_effect_transaction(
                lambda: self._dispatch_update_standard_backup_job_in_transaction(
                    envelope,
                    identity,
                    prepared_command,
                    identity_validation_code=identity_validation_code,
                )
            )
        except (JobScheduleInvalidationError, ValueError) as exc:
            outcome = JobEditingOutcome(
                saved=False,
                validation_code=str(exc),
                next_action="Refresh the job and retry saving the edit.",
            )
            return IpcResponse.rejected(
                IpcReason.COMMAND_PRECONDITION_FAILED,
                _job_editing_response_payload(
                    envelope=envelope,
                    command=command,
                    mutations_enabled=True,
                    outcome=outcome,
                ),
            )
        response = self._refresh_endpoint_classification_after_job_command(response)
        response = self._register_writable_targets_after_job_command(
            response,
            envelope=envelope,
        )
        response = self._refresh_endpoint_classification_after_job_command(response)
        return self._enqueue_analysis_after_job_edit(
            response,
            envelope=envelope,
            command=prepared_command,
        )

    def _bind_job_edit_directory_identities(
        self,
        command: UpdateStandardBackupJobCommand,
    ) -> UpdateStandardBackupJobCommand:
        assert self.standard_backup_job_revision_catalog is not None
        assert self.selected_directory_identity_probe is not None
        current = self.standard_backup_job_revision_catalog.load_standard_backup_job(
            command.job_id
        )
        draft = command.draft
        if (
            current is not None
            and current.job_revision_id == command.expected_job_revision_id
            and current.source_path_label == draft.source_path_label
            and tuple(target.path_label for target in current.targets)
            == tuple(target.path_label for target in draft.targets)
        ):
            draft = replace(
                draft,
                targets=tuple(
                    replace(
                        target,
                        independent_device_id=current.targets[index].independent_device_id,
                    )
                    for index, target in enumerate(draft.targets)
                ),
            )
        else:
            draft = bind_standard_backup_draft_directory_identities(
                draft=draft,
                probe=self.selected_directory_identity_probe,
            )
        return replace(command, draft=draft)

    def _dispatch_update_standard_backup_job_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: UpdateStandardBackupJobCommand,
        *,
        identity_validation_code: str | None = None,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.job_draft_store is None
            or self.standard_backup_job_revision_catalog is None
            or self.standard_backup_job_revision_id_factory is None
            or self.job_schedule_invalidator is None
            or self.job_lifecycle_store is None
            or self.run_store is None
        ):
            return self._reject_config_missing_job_editing(
                envelope,
                identity,
                command,
            )
        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        if receipt.state is not CommandReceiptState.RECEIVED:
            if receipt.state is CommandReceiptState.REJECTED:
                outcome = JobEditingOutcome(
                    saved=False,
                    validation_code=(
                        receipt.rejection_reason
                        or IpcReason.COMMAND_PRECONDITION_FAILED.value
                    ),
                    next_action="Refresh the job and retry saving the edit.",
                    idempotent_replay=True,
                )
                payload = _job_editing_response_payload(
                    envelope=envelope,
                    command=command,
                    mutations_enabled=True,
                    outcome=outcome,
                )
                self._add_receipt_payload(payload, envelope.idempotency_key)
                return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
            outcome = update_standard_backup_job_from_draft(
                command=command,
                catalog=self.standard_backup_job_revision_catalog,
                runs=self.run_store,
                id_factory=self.standard_backup_job_revision_id_factory,
                schedules=self.job_schedule_invalidator,
                lifecycle=self.job_lifecycle_store,
            )
            payload = _job_editing_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                outcome=outcome,
                endpoint_set=self._load_job_edit_endpoint_set(outcome),
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.accepted(payload)

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        if identity_validation_code is not None:
            outcome = JobEditingOutcome(
                saved=False,
                validation_code=identity_validation_code,
                next_action=(
                    "Choose source and target folders that are not aliases or nested."
                ),
            )
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=identity_validation_code,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _job_editing_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)
        self.job_draft_store.save_standard_backup_draft(command.draft)
        outcome = update_standard_backup_job_from_draft(
            command=command,
            catalog=self.standard_backup_job_revision_catalog,
            runs=self.run_store,
            id_factory=self.standard_backup_job_revision_id_factory,
            schedules=self.job_schedule_invalidator,
            lifecycle=self.job_lifecycle_store,
        )
        if not outcome.saved or outcome.job is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=outcome.validation_code,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _job_editing_response_payload(
                envelope=envelope,
                command=command,
                mutations_enabled=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        endpoint_set = None
        if self.standard_backup_job_endpoint_registrar is not None:
            endpoint_set = self.standard_backup_job_endpoint_registrar.register_standard_backup_job_endpoints(
                outcome.job
            )
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="standard_backup_job_revision",
            result_entity_id=outcome.job.job_revision_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)
        payload = _job_editing_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            outcome=outcome,
            endpoint_set=endpoint_set,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _load_job_edit_endpoint_set(
        self,
        outcome: JobEditingOutcome,
    ) -> StandardBackupJobEndpointSet | None:
        if (
            outcome.job is None
            or self.standard_backup_job_endpoint_registrar is None
        ):
            return None
        return self.standard_backup_job_endpoint_registrar.load_standard_backup_job_endpoint_set(
            job_id=outcome.job.job_id,
            job_revision_id=outcome.job.job_revision_id,
        )

    def _reject_config_missing_job_editing(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: UpdateStandardBackupJobCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = _job_editing_response_payload(
            envelope=envelope,
            command=command,
            mutations_enabled=True,
            outcome=None,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
            payload,
        )

    def _enqueue_analysis_after_job_edit(
        self,
        response: IpcResponse,
        *,
        envelope: IpcCommandEnvelope,
        command: UpdateStandardBackupJobCommand,
    ) -> IpcResponse:
        if response.status is not IpcStatus.ACCEPTED:
            return response
        payload = dict(response.payload)
        edit = payload.get("job_edit")
        if not isinstance(edit, dict) or edit.get("requires_full_check") is not True:
            return response
        if not command.check_after_save:
            edit["check_queued"] = False
            edit["validation_code"] = "STANDARD_BACKUP_JOB_UPDATED_NEEDS_CHECK"
            payload["job_edit"] = edit
            return IpcResponse.accepted(payload)
        registration = payload.get("writable_endpoint_registration")
        if (
            isinstance(registration, dict)
            and registration.get("completed") is not True
        ):
            edit["check_queued"] = False
            edit["validation_code"] = (
                "STANDARD_BACKUP_JOB_UPDATED_REGISTRATION_INCOMPLETE"
            )
            payload["job_edit"] = edit
            return IpcResponse.accepted(payload)
        job = payload.get("job")
        if not isinstance(job, dict) or self.backup_analysis_request_store is None:
            edit["check_queued"] = False
            edit["validation_code"] = "STANDARD_BACKUP_JOB_CHECK_QUEUE_UNAVAILABLE"
            payload["job_edit"] = edit
            return IpcResponse.accepted(payload)
        job_id = job.get("job_id")
        job_revision_id = job.get("job_revision_id")
        if not isinstance(job_id, str) or not isinstance(job_revision_id, str):
            return response
        requested_utc = (
            self.job_editing_utc_now()
            if self.job_editing_utc_now is not None
            else _system_utc_now()
        )
        request = BackupAnalysisRequest(
            request_id=envelope.request_id,
            command_idempotency_key=envelope.idempotency_key,
            job_id=job_id,
            job_revision_id=job_revision_id,
            state=BackupAnalysisRequestState.QUEUED,
            requested_utc=requested_utc,
            start_when_safe=False,
        )

        def enqueue() -> BackupAnalysisRequest:
            assert self.backup_analysis_request_store is not None
            return self.backup_analysis_request_store.enqueue_backup_analysis(request)

        try:
            recorded = (
                enqueue()
                if self.command_effect_transaction is None
                else self.command_effect_transaction.run(enqueue)
            )
        except (CommandEffectStorageFailure, RuntimeError, ValueError):
            edit["check_queued"] = False
            edit["validation_code"] = "STANDARD_BACKUP_JOB_CHECK_QUEUE_FAILED"
            payload["job_edit"] = edit
            return IpcResponse.accepted(payload)
        edit["check_queued"] = True
        payload["job_edit"] = edit
        payload["analysis_request"] = recorded.to_dict()
        return IpcResponse.accepted(payload)

    def _dispatch_create_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CreateStandardBackupJobCommand,
    ) -> IpcResponse:
        prepared_command = command
        identity_validation_code: str | None = None
        existing_receipt = (
            self.command_receipt_store.load_command_receipt(
                envelope.idempotency_key
            )
            if self.command_receipt_store is not None
            else None
        )
        if (
            command.inline_draft is not None
            and self.selected_directory_identity_probe is not None
            and (
                existing_receipt is None
                or existing_receipt.state is CommandReceiptState.RECEIVED
            )
        ):
            try:
                prepared_command = replace(
                    command,
                    inline_draft=bind_standard_backup_draft_directory_identities(
                        draft=command.inline_draft,
                        probe=self.selected_directory_identity_probe,
                    ),
                )
            except SelectedDirectoryIdentityError as exc:
                identity_validation_code = str(exc)
        response = self._run_command_effect_transaction(
            lambda: self._dispatch_create_standard_backup_job_in_transaction(
                envelope,
                identity,
                prepared_command,
                identity_validation_code=identity_validation_code,
            )
        )
        response = self._refresh_endpoint_classification_after_job_command(response)
        response = self._register_writable_targets_after_job_command(
            response,
            envelope=envelope,
        )
        response = self._refresh_endpoint_classification_after_job_command(response)
        response = self._refresh_job_snapshots_after_job_command(response)
        return self._refresh_initial_backup_plan_after_job_command(response)

    def _register_writable_targets_after_job_command(
        self,
        response: IpcResponse,
        *,
        envelope: IpcCommandEnvelope,
    ) -> IpcResponse:
        if (
            response.status is not IpcStatus.ACCEPTED
            or self.writable_endpoint_registration is None
        ):
            return response
        payload = dict(response.payload)
        job = payload.get("job")
        if not isinstance(job, dict):
            return response
        job_id = job.get("job_id")
        job_revision_id = job.get("job_revision_id")
        if not isinstance(job_id, str) or not isinstance(job_revision_id, str):
            return response
        try:
            report = self.writable_endpoint_registration.register_job_targets(
                job_id=job_id,
                job_revision_id=job_revision_id,
                command_request_id=envelope.request_id,
                command_idempotency_key=envelope.idempotency_key,
                observed_utc=(
                    self.writable_endpoint_registration_utc_now()
                    if self.writable_endpoint_registration_utc_now is not None
                    else _system_utc_now()
                ),
            )
            payload["writable_endpoint_registration"] = report.to_dict()
            if report.completed:
                payload["job"] = {
                    **job,
                    "job_revision_id": report.active_job_revision_id,
                }
        except WritableEndpointRegistrationError as exc:
            payload["writable_endpoint_registration"] = {
                "job_id": job_id,
                "source_job_revision_id": job_revision_id,
                "active_job_revision_id": job_revision_id,
                "intent_id": None,
                "state": None,
                "target_count": 0,
                "registered_target_count": 0,
                "idempotent_replay": False,
                "completed": False,
                "validation_codes": [exc.validation_code],
                "next_action": exc.next_action,
            }
        return IpcResponse.accepted(payload)

    def _refresh_endpoint_classification_after_job_command(
        self,
        response: IpcResponse,
    ) -> IpcResponse:
        if (
            response.status is not IpcStatus.ACCEPTED
            or self.endpoint_classification_refresh is None
        ):
            return response
        payload = dict(response.payload)
        try:
            report = self.endpoint_classification_refresh()
            payload["endpoint_classification_refresh"] = {
                "completed": True,
                "report": report.to_dict(),
            }
            endpoint_set = self._reload_response_endpoint_set(payload)
            if endpoint_set is not None:
                payload["endpoint_bindings"] = endpoint_set.to_dict()
        except Exception:
            payload["endpoint_classification_refresh"] = {
                "completed": False,
                "reason_code": "ENDPOINT_CLASSIFICATION_REFRESH_FAILED",
            }
        return IpcResponse.accepted(payload)

    def _refresh_job_snapshots_after_job_command(
        self,
        response: IpcResponse,
    ) -> IpcResponse:
        if (
            response.status is not IpcStatus.ACCEPTED
            or self.job_snapshot_refresh is None
        ):
            return response
        payload = dict(response.payload)
        classification_refresh = payload.get("endpoint_classification_refresh")
        if (
            isinstance(classification_refresh, dict)
            and classification_refresh.get("completed") is False
        ):
            payload["job_snapshot_refresh"] = {
                "completed": False,
                "reason_code": "JOB_SNAPSHOT_CLASSIFICATION_REFRESH_REQUIRED",
            }
            return IpcResponse.accepted(payload)
        try:
            report = self.job_snapshot_refresh()
            payload["job_snapshot_refresh"] = {
                "completed": True,
                "report": report.to_dict(),
            }
        except Exception:
            payload["job_snapshot_refresh"] = {
                "completed": False,
                "reason_code": "JOB_SNAPSHOT_REFRESH_FAILED",
            }
        return IpcResponse.accepted(payload)

    def _refresh_initial_backup_plan_after_job_command(
        self,
        response: IpcResponse,
    ) -> IpcResponse:
        if (
            response.status is not IpcStatus.ACCEPTED
            or self.initial_backup_plan_refresh is None
        ):
            return response
        payload = dict(response.payload)
        snapshot_refresh = payload.get("job_snapshot_refresh")
        if (
            isinstance(snapshot_refresh, dict)
            and snapshot_refresh.get("completed") is False
        ):
            payload["initial_backup_plan_refresh"] = {
                "completed": False,
                "reason_code": "INITIAL_BACKUP_PLAN_SNAPSHOT_REFRESH_REQUIRED",
            }
            return IpcResponse.accepted(payload)
        try:
            report = self.initial_backup_plan_refresh()
            payload["initial_backup_plan_refresh"] = {
                "completed": True,
                "report": report.to_dict(),
            }
        except Exception:
            payload["initial_backup_plan_refresh"] = {
                "completed": False,
                "reason_code": "INITIAL_BACKUP_PLAN_REFRESH_FAILED",
            }
        return IpcResponse.accepted(payload)

    def _reload_response_endpoint_set(
        self,
        payload: dict[str, Any],
    ) -> StandardBackupJobEndpointSet | None:
        if self.standard_backup_job_endpoint_registrar is None:
            return None
        job = payload.get("job")
        if not isinstance(job, dict):
            return None
        job_id = job.get("job_id")
        job_revision_id = job.get("job_revision_id")
        registration = payload.get("writable_endpoint_registration")
        if isinstance(registration, dict):
            active_job_revision_id = registration.get("active_job_revision_id")
            if isinstance(active_job_revision_id, str):
                job_revision_id = active_job_revision_id
        if not isinstance(job_id, str) or not isinstance(job_revision_id, str):
            return None
        return self.standard_backup_job_endpoint_registrar.load_standard_backup_job_endpoint_set(
            job_id=job_id,
            job_revision_id=job_revision_id,
        )

    def _dispatch_create_standard_backup_job_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CreateStandardBackupJobCommand,
        *,
        identity_validation_code: str | None = None,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.job_draft_store is None
            or self.standard_backup_job_catalog is None
            or self.standard_backup_job_id_factory is None
        ):
            return self._reject_config_missing_standard_backup_job(
                envelope, identity, command
            )

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)

        replay = self._standard_backup_job_replay_response(envelope, command, receipt)
        if replay is not None:
            return replay

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        if identity_validation_code is not None:
            outcome = JobCreationOutcome(
                created=False,
                idempotent_replay=False,
                readiness=JobCreationReadiness(
                    draft_id=command.draft_id,
                    draft_found=True,
                    draft_valid=False,
                    validation_codes=(identity_validation_code,),
                    next_action=(
                        "Choose source and target folders that are not aliases or nested."
                    ),
                ),
            )
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _create_standard_backup_job_response_payload(
                envelope=envelope,
                draft_id=command.draft_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)
        if command.inline_draft is not None:
            self.job_draft_store.save_standard_backup_draft(command.inline_draft)
        outcome = create_standard_backup_job_from_draft(
            command=command,
            drafts=self.job_draft_store,
            catalog=self.standard_backup_job_catalog,
            id_factory=self.standard_backup_job_id_factory,
        )
        if outcome.job is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _create_standard_backup_job_response_payload(
                envelope=envelope,
                draft_id=command.draft_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        if command.autosave_draft_id is not None:
            self.job_draft_store.save_standard_backup_draft(
                StandardBackupJobDraft.new(command.autosave_draft_id)
            )

        endpoint_set = None
        if self.standard_backup_job_endpoint_registrar is not None:
            endpoint_set = self.standard_backup_job_endpoint_registrar.register_standard_backup_job_endpoints(
                outcome.job
            )
        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="standard_backup_job",
            result_entity_id=outcome.job.job_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)

        payload = _create_standard_backup_job_response_payload(
            envelope=envelope,
            draft_id=command.draft_id,
            mutations_enabled=True,
            recognized=True,
            outcome=outcome,
            endpoint_set=endpoint_set,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CreateStandardBackupJobCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = {
            "command_name": envelope.command_name,
            "draft_id": command.draft_id,
            "recognized": True,
            "mutations_enabled": True,
        }
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload
        )

    def _standard_backup_job_replay_response(
        self,
        envelope: IpcCommandEnvelope,
        command: CreateStandardBackupJobCommand,
        receipt: CommandReceipt,
    ) -> IpcResponse | None:
        if receipt.state is CommandReceiptState.RECEIVED:
            return None
        if self.standard_backup_job_catalog is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        job = self.standard_backup_job_catalog.load_standard_backup_job_by_idempotency_key(
            envelope.idempotency_key
        )
        endpoint_set = None
        if job is not None and self.standard_backup_job_endpoint_registrar is not None:
            endpoint_set = self.standard_backup_job_endpoint_registrar.load_standard_backup_job_endpoint_set(
                job_id=job.job_id,
                job_revision_id=job.job_revision_id,
            )
        payload = _create_standard_backup_job_response_payload(
            envelope=envelope,
            draft_id=command.draft_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            job=job,
            endpoint_set=endpoint_set,
        )
        payload["created"] = False
        payload["idempotent_replay"] = True
        self._add_receipt_payload(payload, envelope.idempotency_key)
        if receipt.state is CommandReceiptState.SUCCEEDED:
            return IpcResponse.accepted(payload)
        if receipt.state is CommandReceiptState.REJECTED:
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
        return IpcResponse.accepted(payload)

    def _record_received_receipt(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> tuple[CommandReceipt | None, IpcResponse | None]:
        if self.command_receipt_store is None:
            return None, None
        incoming = _receipt_from_envelope(envelope, identity)
        try:
            return self.command_receipt_store.record_received(incoming), None
        except CommandReceiptConflict as exc:
            return None, IpcResponse.rejected(
                IpcReason.COMMAND_IDEMPOTENCY_CONFLICT,
                {
                    "conflict": str(exc),
                    "idempotency_key": envelope.idempotency_key,
                },
            )

    def _record_terminal_rejected_receipt(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        *,
        rejection_reason: str,
    ) -> IpcResponse | None:
        if self.command_receipt_store is None:
            return None
        incoming = _receipt_from_envelope(envelope, identity)
        try:
            receipt = self.command_receipt_store.record_received(incoming)
        except CommandReceiptConflict as exc:
            return IpcResponse.rejected(
                IpcReason.COMMAND_IDEMPOTENCY_CONFLICT,
                {
                    "conflict": str(exc),
                    "idempotency_key": envelope.idempotency_key,
                },
            )
        if receipt.state is CommandReceiptState.RECEIVED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=rejection_reason,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        return None

    def _add_receipt_payload(
        self, payload: dict[str, Any], idempotency_key: str
    ) -> None:
        if self.command_receipt_store is None:
            return
        receipt = self.command_receipt_store.load_command_receipt(idempotency_key)
        if receipt is not None:
            payload["receipt"] = _receipt_payload(receipt)

    def _run_command_effect_transaction(
        self, work: Callable[[], IpcResponse]
    ) -> IpcResponse:
        if self.command_effect_transaction is None:
            return work()
        try:
            return self.command_effect_transaction.run(work)
        except CommandEffectStorageFailure as exc:
            return IpcResponse.rejected(
                IpcReason.COMMAND_PRECONDITION_FAILED,
                {
                    "error_code": exc.error_code,
                    "retryable": exc.retryable,
                },
            )

    def _add_state_capacity_payload(self, payload: dict[str, object]) -> None:
        if self.state_capacity_provider is not None:
            payload["state_capacity"] = self.state_capacity_provider()

    def _enqueue_command_effect_outbox(self, receipt: CommandReceipt) -> None:
        if self.outbox_store is None:
            return
        self.outbox_store.enqueue_outbox_message(command_effect_outbox_message(receipt))


def _receipt_from_envelope(
    envelope: IpcCommandEnvelope,
    identity: VerifiedClientIdentity,
) -> CommandReceipt:
    return CommandReceipt(
        request_id=envelope.request_id,
        client_instance_id=envelope.client_instance_id,
        principal_fingerprint=identity.user_sid_hash,
        idempotency_key=envelope.idempotency_key,
        command_name=envelope.command_name,
        payload_hash=envelope.payload_hash,
        protocol_version=envelope.protocol_version,
        schema_version=envelope.schema_version,
        expected_entity_revision=envelope.expected_entity_revision,
        payload_hash_scope=envelope.payload_hash_scope,
        payload_canonicalization_algorithm=envelope.payload_canonicalization_algorithm,
        payload_hash_algorithm=envelope.payload_hash_algorithm,
    )


def _receipt_payload(receipt: CommandReceipt) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": receipt.request_id,
        "idempotency_key": receipt.idempotency_key,
        "command_name": receipt.command_name,
        "state": receipt.state.value,
    }
    if receipt.result_entity_type is not None:
        payload["result_entity_type"] = receipt.result_entity_type
    if receipt.result_entity_id is not None:
        payload["result_entity_id"] = receipt.result_entity_id
    if receipt.rejection_reason is not None:
        payload["rejection_reason"] = receipt.rejection_reason
    return payload


def _create_standard_backup_job_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    draft_id: str,
    mutations_enabled: bool,
    recognized: bool,
    outcome: JobCreationOutcome | None,
    job: SealedStandardBackupJob | None = None,
    endpoint_set: StandardBackupJobEndpointSet | None = None,
) -> dict[str, Any]:
    result = {
        "command_name": envelope.command_name,
        "draft_id": draft_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        result["created"] = outcome.created
        result["idempotent_replay"] = outcome.idempotent_replay
        result["readiness"] = outcome.readiness.to_dict()
        job = outcome.job
    if job is not None:
        result["job"] = {
            "job_id": job.job_id,
            "job_revision_id": job.job_revision_id,
            "filter_set_id": job.filter_set_id,
        }
    if endpoint_set is not None:
        result["endpoint_bindings"] = endpoint_set.to_dict()
    return result


def _job_editing_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    command: UpdateStandardBackupJobCommand,
    mutations_enabled: bool,
    outcome: JobEditingOutcome | None,
    endpoint_set: StandardBackupJobEndpointSet | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "command_name": envelope.command_name,
        "job_id": command.job_id,
        "requested_job_revision_id": command.expected_job_revision_id,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
        "check_after_save": command.check_after_save,
    }
    job = None
    if outcome is not None:
        result["job_edit"] = outcome.to_dict()
        job = outcome.job
    if job is not None:
        result["job"] = {
            "job_id": job.job_id,
            "job_revision_id": job.job_revision_id,
            "filter_set_id": job.filter_set_id,
        }
    if endpoint_set is not None:
        result["endpoint_bindings"] = endpoint_set.to_dict()
    return result


def _writable_endpoint_registration_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    command: RegisterWritableTargetsCommand,
    mutations_enabled: bool,
    recognized: bool,
    report: WritableEndpointRegistrationReport | None = None,
    idempotent_replay: bool = False,
    validation_code: str | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    active_job_revision_id = (
        command.job_revision_id if report is None else report.active_job_revision_id
    )
    result: dict[str, Any] = {
        "command_name": envelope.command_name,
        "job_id": command.job_id,
        "requested_job_revision_id": command.job_revision_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
        "idempotent_replay": idempotent_replay,
        "job": {
            "job_id": command.job_id,
            "job_revision_id": active_job_revision_id,
        },
    }
    if report is not None:
        result["writable_endpoint_registration"] = report.to_dict()
    elif validation_code is not None:
        result["writable_endpoint_registration"] = {
            "job_id": command.job_id,
            "source_job_revision_id": command.job_revision_id,
            "active_job_revision_id": command.job_revision_id,
            "intent_id": None,
            "state": None,
            "target_count": 0,
            "registered_target_count": 0,
            "idempotent_replay": idempotent_replay,
            "completed": False,
            "validation_codes": [validation_code],
            "next_action": next_action,
        }
    return result


def _endpoint_takeover_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    command: StartControlledEndpointTakeoverCommand,
    mutations_enabled: bool,
    recognized: bool,
    report: EndpointTakeoverReport | None = None,
    idempotent_replay: bool = False,
    validation_code: str | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    active_job_revision_id = (
        command.job_revision_id if report is None else report.active_job_revision_id
    )
    result: dict[str, Any] = {
        "command_name": envelope.command_name,
        "job_id": command.job_id,
        "requested_job_revision_id": command.job_revision_id,
        "target_ordinal": command.target_ordinal,
        "endpoint_id": command.endpoint_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
        "idempotent_replay": idempotent_replay,
        "job": {
            "job_id": command.job_id,
            "job_revision_id": active_job_revision_id,
        },
    }
    if report is not None:
        result["endpoint_takeover"] = report.to_dict()
    elif validation_code is not None:
        result["endpoint_takeover"] = {
            "job_id": command.job_id,
            "source_job_revision_id": command.job_revision_id,
            "active_job_revision_id": command.job_revision_id,
            "endpoint_id": command.endpoint_id,
            "target_ordinal": command.target_ordinal,
            "intent_id": None,
            "analysis_request_id": None,
            "state": None,
            "idempotent_replay": idempotent_replay,
            "completed": False,
            "full_analysis_queued": False,
            "start_when_safe": False,
            "validation_codes": [validation_code],
            "next_action": next_action,
        }
    return result


def _backup_analysis_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    job_id: str,
    mutations_enabled: bool,
    recognized: bool,
    request: BackupAnalysisRequest | None,
) -> dict[str, Any]:
    return {
        "command_name": envelope.command_name,
        "job_id": job_id,
        "mutations_enabled": mutations_enabled,
        "recognized": recognized,
        "queued": request is not None,
        "analysis_request": None if request is None else request.to_dict(),
    }


def _duplicate_scan_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    analysis_id: str,
    mutations_enabled: bool,
    status: DuplicateScanStatus | None,
) -> dict[str, Any]:
    return {
        "command_name": envelope.command_name,
        "analysis_id": analysis_id,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
        "duplicate_scan": None if status is None else status.to_dict(),
    }


def _duplicate_group_review_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    group_id: str,
    mutations_enabled: bool,
    group: DuplicateGroupReadModel | None,
) -> dict[str, Any]:
    return {
        "command_name": envelope.command_name,
        "group_id": group_id,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
        "duplicate_group": None if group is None else group.to_dict(),
    }


def _job_lifecycle_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    mutations_enabled: bool,
    outcome: JobLifecycleTransitionOutcome | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "command_name": envelope.command_name,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        payload.update(outcome.to_dict())
    return payload


def _job_scheduling_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    mutations_enabled: bool,
    outcome: JobSchedulingOutcome | None,
    reconciliation_state: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "command_name": envelope.command_name,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        payload.update(outcome.to_dict())
    if reconciliation_state is not None:
        payload["reconciliation_state"] = reconciliation_state
    return payload


def _version_restore_protection_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    mutations_enabled: bool,
    outcome: VersionRestoreProtectionOutcome | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "command_name": envelope.command_name,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        payload["version_restore_protection"] = outcome.to_dict()
    return payload


def _version_restore_request_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    mutations_enabled: bool,
    outcome: VersionRestoreRequestOutcome | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "command_name": envelope.command_name,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        payload["version_restore_request"] = outcome.to_dict()
    return payload


def _version_restore_undo_request_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    mutations_enabled: bool,
    outcome: VersionRestoreUndoRequestOutcome | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "command_name": envelope.command_name,
        "recognized": True,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        payload["version_restore_undo_request"] = outcome.to_dict()
    return payload


def _start_run_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    plan_id: str,
    mutations_enabled: bool,
    recognized: bool,
    outcome: RunStartOutcome | None,
    run: StartedRun | None = None,
) -> dict[str, Any]:
    result = {
        "command_name": envelope.command_name,
        "plan_id": plan_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        result["created"] = outcome.created
        result["idempotent_replay"] = outcome.idempotent_replay
        result["readiness"] = outcome.readiness.to_dict()
        run = outcome.run
    if run is not None:
        result["run"] = {
            "run_id": run.run_id,
            "logical_run_group_id": run.logical_run_group_id,
            "resumed_from_run_id": run.resumed_from_run_id,
            "job_id": run.job_id,
            "job_revision_id": run.job_revision_id,
            "plan_id": run.plan_id,
            "state": run.state.value,
            "plan_checksum": run.plan_checksum,
            "planned_operations": run.planned_operations,
            "planned_bytes": run.planned_bytes,
            "target_endpoint_ids": [target.endpoint_id for target in run.targets],
            "operation_ids": _run_summary_ids(run.summary, "operation_ids"),
            "source_operation_ids": _run_summary_ids(
                run.summary,
                "source_operation_ids",
            ),
        }
    return result


def _run_summary_ids(summary: Mapping[str, object], key: str) -> list[str]:
    value = summary.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _run_control_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    run_id: str,
    mutations_enabled: bool,
    recognized: bool,
    outcome: RunControlOutcome | None,
    run: StartedRun | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "command_name": envelope.command_name,
        "run_id": run_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        result["applied"] = outcome.applied
        result["idempotent_replay"] = outcome.idempotent_replay
        result["validation_codes"] = list(outcome.validation_codes)
        result["next_action"] = outcome.next_action
        run = outcome.run
    if run is not None:
        result["run"] = {
            "run_id": run.run_id,
            "job_id": run.job_id,
            "job_revision_id": run.job_revision_id,
            "plan_id": run.plan_id,
            "state": run.state.value,
            "planned_operations": run.planned_operations,
            "planned_bytes": run.planned_bytes,
        }
    return result


def _trigger_occurrence_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    command: EnqueueTriggerOccurrenceCommand | None,
    mutations_enabled: bool,
    recognized: bool,
    outcome: TriggerRunEnqueueOutcome | None,
    analysis_request: BackupAnalysisRequest | None = None,
    run: StartedRun | None = None,
) -> dict[str, Any]:
    result = {
        "command_name": envelope.command_name,
        "delivery_id": envelope.idempotency_key,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
    }
    if command is not None:
        result["delivery_id"] = command.delivery.delivery_id
        result["schedule_id"] = command.schedule_id
        result["schedule_revision_hash"] = command.schedule_revision_hash
    if outcome is not None:
        result["enqueued"] = outcome.enqueued
        result["deduplicated"] = outcome.deduplicated
        result["compacted"] = outcome.compacted
        result["schedule_resolution"] = outcome.schedule_resolution_kind.value
        result["validation_codes"] = list(outcome.validation_codes)
        result["next_action"] = outcome.next_action
        if outcome.occurrence is not None:
            result["occurrence"] = {
                "occurrence_id": outcome.occurrence.occurrence_id,
                "state": outcome.occurrence.state.value,
                "run_id": outcome.occurrence.run_id,
            }
        if outcome.run_start is not None:
            result["created"] = outcome.run_start.created
            result["idempotent_replay"] = outcome.run_start.idempotent_replay
            result["readiness"] = outcome.run_start.readiness.to_dict()
            run = outcome.run_start.run
        if outcome.analysis_request is not None:
            analysis_request = outcome.analysis_request
            result["created"] = not outcome.deduplicated
            result["idempotent_replay"] = outcome.deduplicated
    if analysis_request is not None:
        result["analysis_request"] = analysis_request.to_dict()
    if run is not None:
        result["run"] = {
            "run_id": run.run_id,
            "job_id": run.job_id,
            "job_revision_id": run.job_revision_id,
            "plan_id": run.plan_id,
            "state": run.state.value,
            "plan_checksum": run.plan_checksum,
            "planned_operations": run.planned_operations,
            "planned_bytes": run.planned_bytes,
            "trigger_occurrence_id": run.trigger_occurrence_id,
        }
    return result


def _state_restore_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    command: RestoreStateFromBackupSetCommand,
    recognized: bool,
    restored: bool,
    restore_receipt: dict[str, object] | None,
    mutations_enabled: bool,
    executor_configured: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "backup_dir": str(command.backup_dir),
        "command_name": envelope.command_name,
        "executor_configured": executor_configured,
        "host_restart_required": restored,
        "mutations_enabled": mutations_enabled,
        "read_only_ipc_mode": not mutations_enabled,
        "recognized": recognized,
        "restore_epoch_id": command.restore_epoch_id,
        "restored": restored,
        "started_utc": command.started_utc,
    }
    if restore_receipt is not None:
        result["restore_receipt"] = restore_receipt
    return result


def _bounded_query_limit(
    value: int | None,
    *,
    default: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError("query limit is invalid")
    return value


def _duplicate_group_cursor(
    payload: dict[str, object] | None,
) -> DuplicateGroupCursor | None:
    if payload is None:
        return None
    if set(payload) != {"relationship_class", "full_hash", "group_id"}:
        raise ValueError("duplicate group cursor is invalid")
    values = tuple(
        payload.get(key) for key in ("relationship_class", "full_hash", "group_id")
    )
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("duplicate group cursor is invalid")
    return DuplicateGroupCursor(
        relationship_class=str(values[0]),
        full_hash=str(values[1]),
        group_id=str(values[2]),
    )


def _duplicate_member_cursor(
    payload: dict[str, object] | None,
) -> DuplicateMemberCursor | None:
    if payload is None:
        return None
    if set(payload) != {"relative_path", "snapshot_id", "file_entry_id"}:
        raise ValueError("duplicate member cursor is invalid")
    values = tuple(
        payload.get(key) for key in ("relative_path", "snapshot_id", "file_entry_id")
    )
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("duplicate member cursor is invalid")
    return DuplicateMemberCursor(
        relative_path=str(values[0]),
        snapshot_id=str(values[1]),
        file_entry_id=str(values[2]),
    )


def _duplicate_report_cursor(
    payload: dict[str, object] | None,
) -> DuplicateReportCursor | None:
    if payload is None:
        return None
    keys = (
        "relationship_class",
        "full_hash",
        "group_id",
        "relative_path",
        "snapshot_id",
        "file_entry_id",
    )
    if set(payload) != set(keys):
        raise ValueError("duplicate report cursor is invalid")
    values = tuple(payload.get(key) for key in keys)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("duplicate report cursor is invalid")
    return DuplicateReportCursor(
        relationship_class=str(values[0]),
        full_hash=str(values[1]),
        group_id=str(values[2]),
        relative_path=str(values[3]),
        snapshot_id=str(values[4]),
        file_entry_id=str(values[5]),
    )


def _system_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_rejection_reason(receipt: CommandReceipt) -> IpcReason:
    if receipt.rejection_reason is None:
        return IpcReason.COMMAND_PRECONDITION_FAILED
    try:
        return IpcReason(receipt.rejection_reason)
    except ValueError:
        return IpcReason.COMMAND_PRECONDITION_FAILED
