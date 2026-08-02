from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypeVar

import pytest

from tests.support.source_preconditions import source_precondition_json

from mediasync_home.application.activity_read_models import (
    RunActivityReadModelStore,
    RunActivitySummary,
    RunTargetActivitySummary,
)
from mediasync_home.application.catalog_read_models import (
    CatalogedFileReadModel,
    CatalogedFileReadModelStore,
)
from mediasync_home.application.backup_analysis import (
    BackupAnalysisCommandName,
    BackupAnalysisRequest,
)
from mediasync_home.application.command_receipts import (
    CommandEffectStorageFailure,
    CommandReceipt,
    CommandReceiptState,
    CommandReceiptStore,
    ensure_idempotency_compatible,
)
from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.duplicates import DuplicateAnalysisSummary
from mediasync_home.application.duplicate_scanning import (
    DuplicateGroupCursor,
    DuplicateGroupPage,
    DuplicateGroupReadModel,
    DuplicateMemberCursor,
    DuplicateMemberPage,
    DuplicateMemberReadModel,
    DuplicateScanCommandName,
    DuplicateScanStage,
    DuplicateScanState,
    DuplicateScanStatus,
)
from mediasync_home.application.endpoint_registration import (
    EndpointClassificationRefreshReport,
)
from mediasync_home.application.endpoint_takeover import EndpointTakeoverCommandName
from mediasync_home.application.external_resources import (
    ExternalResourceRecord,
    ExternalResourceState,
    ExternalResourceType,
)
from mediasync_home.application.job_creation import (
    JobCreationCommandName,
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
    StandardBackupJobCatalog,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
)
from mediasync_home.application.job_draft_saving import JobDraftCommandName
from mediasync_home.application.job_drafts import (
    JobDraftStore,
    StandardBackupDefaults,
    StandardBackupJobDraft,
)
from mediasync_home.application.job_lifecycle import (
    ChangeJobLifecycleCommand,
    JobLifecycleRecord,
    JobLifecycleState,
    JobLifecycleTransitionOutcome,
)
from mediasync_home.application.job_read_models import (
    InitialBackupPlanSummary,
    StandardBackupJobDetail,
    StandardBackupJobSummary,
    StandardBackupTargetSummary,
)
from mediasync_home.application.job_scheduling import JobSchedulingCommandName
from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanRefreshReport,
)
from mediasync_home.application.operation_audit_read_models import (
    OperationAttemptSummary,
    OperationAuditIdentity,
    OperationOutcomeSummary,
)
from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointCursor,
    PlanEndpointPage,
    PlanEndpointPageQuery,
    PlanEndpointReadModel,
    PlanEndpointReadModelStore,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationCursor,
    PlanOperationPage,
    PlanOperationPageQuery,
    PlanOperationReadModel,
    PlanOperationReadModelStore,
    PlanOperationType,
    PlanRiskLevel,
    PlanStore,
    SealedPlan,
    TargetPreconditionKind,
    validate_plan_endpoint_page_query,
    seal_plan,
    validate_plan_operation_page_query,
)
from mediasync_home.application.runs import (
    RunCommandName,
    RunIdFactory,
    RunIds,
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)
from mediasync_home.application.schedules import ScheduleDefinition, ScheduleStore
from mediasync_home.application.selected_directory_identity import (
    SelectedDirectoryProbeEvidence,
    StorageIdentityTrust,
)
from mediasync_home.application.snapshot_scanning import (
    SnapshotMaterializationRefreshReport,
)
from mediasync_home.application.snapshots import (
    SnapshotCoverageCursor,
    SnapshotCoveragePage,
    SnapshotCoveragePageQuery,
    SnapshotCoverageReadModel,
    SnapshotEntryCursor,
    SnapshotEntryPage,
    SnapshotEntryPageQuery,
    SnapshotEntryReadModel,
    SnapshotEntryReadModelStore,
    SnapshotFilterDecisionCursor,
    SnapshotFilterDecisionPage,
    SnapshotFilterDecisionPageQuery,
    SnapshotFilterDecisionReadModel,
    SnapshotIssueCursor,
    SnapshotIssuePage,
    SnapshotIssuePageQuery,
    SnapshotIssueReadModel,
    validate_snapshot_coverage_page_query,
    validate_snapshot_entry_page_query,
    validate_snapshot_filter_decision_page_query,
    validate_snapshot_issue_page_query,
)
from mediasync_home.application.state_maintenance import StateMaintenanceCommandName
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCommandName,
    WritableEndpointRegistrationError,
    WritableEndpointRegistrationReport,
    WritableEndpointRegistrationState,
)
from mediasync_home.application.trigger_occurrences import (
    TriggerCommandName,
    TriggerKind,
    TriggerOccurrence,
    TriggerOccurrenceRegistration,
    TriggerOccurrenceState,
    TriggerOccurrenceStore,
    ensure_trigger_occurrence_compatible,
    payload_hash,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    COMMAND_SCHEMA_VERSION,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
    IpcStatus,
    decode_frame,
    encode_frame,
)
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient


EXPECTED_USER = "same-user-sid-hash"
EXPECTED_SESSION = 42
REQUEST_ID_A = "44444444-4444-4444-8444-444444444444"
REQUEST_ID_B = "77777777-7777-4777-8777-777777777777"
IDEMPOTENCY_KEY_A = "66666666-6666-4666-8666-666666666666"
PAYLOAD_HASH_A = "98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02"
_T = TypeVar("_T")
PAYLOAD_HASH_B = "cbed2c1daab2fe4217ac17819f2f3aa86a7c8b0657d613e255a214724254de3b"
TRIGGER_DELIVERY_ID = "11111111-1111-4111-8111-111111111111"


def _trigger_payload(
    *,
    delivery_id: str = TRIGGER_DELIVERY_ID,
    observed_start_utc: str = "2026-07-20T12:00:00.000Z",
    scheduled_slot_utc: str | None = None,
) -> dict[str, object]:
    delivery: dict[str, object] = {
        "delivery_id": delivery_id,
        "observed_start_utc": observed_start_utc,
        "task_definition_hash": "b" * 64,
        "trigger_kind": "SCHEDULED_TIME",
    }
    if scheduled_slot_utc is not None:
        delivery["scheduled_slot_utc"] = scheduled_slot_utc
    return {
        "delivery": delivery,
        "schedule_id": "schedule-a",
        "schedule_revision_hash": "a" * 64,
    }


def _inline_creation_payload(
    *,
    independent_device_id: str | None,
) -> dict[str, object]:
    return {
        "draft_id": "draft-identity",
        "draft": {
            "draft_id": "draft-identity",
            "schema_version": 1,
            "source_name": "Pictures",
            "source_path_label": "C:/Users/Ada/Pictures",
            "targets": [
                {
                    "name": "Backup",
                    "path_label": "E:/Backup",
                    "independent_device_id": independent_device_id,
                }
            ],
            "defaults": {
                "behavior": "UPDATE_BACKUP",
                "file_selection": "ALL_USER_FILES",
                "verification": "STANDARD",
                "retention": "THIRTY_DAYS",
                "extra_files": "KEEP_ON_TARGET",
                "performance": "AUTO",
            },
        },
    }


def _identity(
    *,
    user_sid_hash: str = EXPECTED_USER,
    session_id: int = EXPECTED_SESSION,
    is_remote: bool = False,
) -> VerifiedClientIdentity:
    return VerifiedClientIdentity(
        user_sid_hash=user_sid_hash,
        session_id=session_id,
        is_remote=is_remote,
        transport="in-process-test",
    )


def _service(*, mutations_enabled: bool = False) -> EngineHostIpcService:
    service = EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash=EXPECTED_USER,
            expected_session_id=EXPECTED_SESSION,
        )
    )
    if mutations_enabled:
        service.status = replace(
            service.status,
            mutations_enabled=True,
            scope="0B_LOCAL_MUTATION_PREVIEW",
        )
    return service


class _InMemoryJobDraftStore(JobDraftStore):
    def __init__(self) -> None:
        self._drafts: dict[str, StandardBackupJobDraft] = {}

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        self._drafts[draft.draft_id] = draft

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None:
        return self._drafts.get(draft_id)


class _SelectedDirectoryIdentityProbe:
    def inspect_directory(self, path_label: str) -> SelectedDirectoryProbeEvidence:
        suffix = path_label.rsplit("/", 1)[-1]
        return SelectedDirectoryProbeEvidence(
            object_identity_key=f"object:{suffix}",
            final_path=rf"\\?\Volume{{shared}}\{suffix}",
            storage_identity_key="disk-a",
            storage_identity_trust=StorageIdentityTrust.CONFIRMED,
        )


class _AliasingSelectedDirectoryIdentityProbe:
    def __init__(self) -> None:
        self.calls = 0

    def inspect_directory(self, path_label: str) -> SelectedDirectoryProbeEvidence:
        del path_label
        self.calls += 1
        return SelectedDirectoryProbeEvidence(
            object_identity_key="same-object",
            final_path=r"\\?\Volume{shared}\Pictures",
            storage_identity_key="disk-a",
            storage_identity_trust=StorageIdentityTrust.CONFIRMED,
        )


class _FailingCommandEffectTransaction:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, work: Callable[[], _T]) -> _T:
        del work
        self.calls += 1
        raise CommandEffectStorageFailure("SQLITE_FULL", retryable=False)


class _InMemoryCommandReceiptStore(CommandReceiptStore):
    def __init__(self) -> None:
        self.receipts: dict[str, CommandReceipt] = {}

    def record_received(self, receipt: CommandReceipt) -> CommandReceipt:
        existing = self.receipts.get(receipt.idempotency_key)
        if existing is not None:
            return ensure_idempotency_compatible(existing, receipt)
        self.receipts[receipt.idempotency_key] = receipt
        return receipt

    def load_command_receipt(self, idempotency_key: str) -> CommandReceipt | None:
        return self.receipts.get(idempotency_key)

    def update_command_receipt(self, receipt: CommandReceipt) -> None:
        self.receipts[receipt.idempotency_key] = receipt


class _RecordingWritableEndpointRegistration:
    def __init__(self, *, error: WritableEndpointRegistrationError | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, str]] = []

    def register_job_targets(self, **kwargs: str) -> WritableEndpointRegistrationReport:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return WritableEndpointRegistrationReport(
            job_id=kwargs["job_id"],
            source_job_revision_id=kwargs["job_revision_id"],
            active_job_revision_id="job-revision-registered",
            intent_id="registration-intent-a",
            state=WritableEndpointRegistrationState.COMMITTED,
            target_count=1,
            registered_target_count=1,
            idempotent_replay=len(self.calls) > 1,
        )


class _StaticArchivedJobLifecycleStore:
    def load_job_lifecycle(self, job_id: str) -> JobLifecycleRecord | None:
        if job_id != "job-a":
            return None
        return JobLifecycleRecord(
            job_id=job_id,
            job_revision_id="job-revision-a",
            state=JobLifecycleState.ARCHIVED,
            row_version=2,
            archived_utc="2026-08-01T06:00:00Z",
        )

    def archive_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome:
        del command, occurred_utc
        raise AssertionError("lifecycle transition was not expected")

    def reactivate_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome:
        del command, occurred_utc
        raise AssertionError("lifecycle transition was not expected")


class _RecordingEndpointTakeover:
    def __init__(self) -> None:
        self.calls = 0

    def start_controlled_takeover(self, **kwargs: object) -> object:
        del kwargs
        self.calls += 1
        raise AssertionError("archived job reached endpoint takeover coordinator")


class _InMemoryStandardBackupJobCatalog(StandardBackupJobCatalog):
    def __init__(self) -> None:
        self.jobs: dict[str, SealedStandardBackupJob] = {}
        self.idempotency_keys: dict[str, str] = {}

    def save_standard_backup_job(self, job: SealedStandardBackupJob) -> None:
        self.jobs[job.job_id] = job
        self.idempotency_keys[job.idempotency_key] = job.job_id

    def load_standard_backup_job(self, job_id: str) -> SealedStandardBackupJob | None:
        return self.jobs.get(job_id)

    def list_active_standard_backup_jobs(self) -> tuple[SealedStandardBackupJob, ...]:
        return tuple(self.jobs[job_id] for job_id in sorted(self.jobs))

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None:
        job_id = self.idempotency_keys.get(idempotency_key)
        if job_id is None:
            return None
        return self.jobs[job_id]

    def list_active_standard_backup_job_summaries(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[StandardBackupJobSummary, ...]:
        jobs = self.list_active_standard_backup_jobs()[offset : offset + limit]
        return tuple(
            StandardBackupJobSummary(
                job_id=job.job_id,
                job_revision_id=job.job_revision_id,
                filter_set_id=job.filter_set_id,
                source_name=job.source_name,
                source_path_label=job.source_path_label,
                targets=tuple(
                    StandardBackupTargetSummary(
                        name=target.name,
                        path_label=target.path_label,
                        independent_device_id=target.independent_device_id,
                    )
                    for target in job.targets
                ),
            )
            for job in jobs
        )

    def load_standard_backup_job_detail(self, job_id: str) -> StandardBackupJobDetail | None:
        job = self.load_standard_backup_job(job_id)
        if job is None:
            return None
        return StandardBackupJobDetail(
            job_id=job.job_id,
            job_revision_id=job.job_revision_id,
            filter_set_id=job.filter_set_id,
            source_name=job.source_name,
            source_path_label=job.source_path_label,
            defaults=job.defaults,
            targets=tuple(
                StandardBackupTargetSummary(
                    name=target.name,
                    path_label=target.path_label,
                    independent_device_id=target.independent_device_id,
                )
                for target in job.targets
            ),
        )


class _InMemoryBackupAnalysisRequestStore:
    def __init__(self) -> None:
        self.requests: dict[str, BackupAnalysisRequest] = {}

    def enqueue_backup_analysis(
        self,
        request: BackupAnalysisRequest,
    ) -> BackupAnalysisRequest:
        self.requests.setdefault(request.request_id, request)
        return self.requests[request.request_id]

    def load_backup_analysis_request(
        self,
        request_id: str,
    ) -> BackupAnalysisRequest | None:
        return self.requests.get(request_id)


class _BackupJobDetailStore:
    def load_standard_backup_job_detail(
        self,
        job_id: str,
    ) -> StandardBackupJobDetail | None:
        if job_id != "job-a":
            return None
        return StandardBackupJobDetail(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
            source_name="Pictures",
            source_path_label="C:/Pictures",
            targets=(),
            defaults=StandardBackupDefaults(),
        )


class _AutomationJobDetailStore:
    def load_standard_backup_job_detail(
        self,
        job_id: str,
    ) -> StandardBackupJobDetail | None:
        if job_id != "job-a":
            return None
        return StandardBackupJobDetail(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
            source_name="Pictures",
            source_path_label="C:/Pictures",
            targets=(
                StandardBackupTargetSummary(
                    name="NAS",
                    path_label="//server/backup",
                    independent_device_id="nas-a",
                ),
            ),
            defaults=StandardBackupDefaults(),
            initial_plan=InitialBackupPlanSummary(
                state="SEALED",
                reason_code="PLAN_READY",
                operation_count=3,
                planned_bytes=1024,
                plan_runnable=True,
                next_action="Start backup.",
                analysis_id="analysis-a",
                plan_id="plan-a",
                plan_checksum="a" * 64,
            ),
        )


class _DuplicateAnalysisReadStore:
    def load_duplicate_analysis_summary(
        self,
        analysis_id: str,
    ) -> DuplicateAnalysisSummary | None:
        if analysis_id != "analysis-a":
            return None
        return DuplicateAnalysisSummary(
            analysis_id=analysis_id,
            read_model_available=True,
            duplicate_group_count=1,
            expected_replica_group_count=1,
            expected_replica_count=1,
            same_file_alias_group_count=1,
            same_file_alias_path_count=2,
            potential_savings_bytes=0,
        )


class _DuplicateScanStore:
    def __init__(self) -> None:
        self.status: DuplicateScanStatus | None = None
        self.start_calls = 0

    def prepare_scan(self, *, analysis_id: str, observed_utc: str) -> None:
        del analysis_id, observed_utc

    def start_scan(
        self,
        *,
        analysis_id: str,
        requested_utc: str,
    ) -> DuplicateScanStatus:
        self.start_calls += 1
        if self.status is None:
            self.status = _duplicate_scan_status(
                analysis_id=analysis_id,
                requested_utc=requested_utc,
            )
        return self.status

    def pause_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None:
        del analysis_id
        if self.status is not None:
            self.status = replace(
                self.status,
                state=DuplicateScanState.PAUSED,
                reason_code="USER_REQUESTED",
                updated_utc=observed_utc,
            )
        return self.status

    def resume_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None:
        del analysis_id
        if self.status is not None:
            self.status = replace(
                self.status,
                state=DuplicateScanState.RUNNING,
                reason_code=None,
                started_utc=observed_utc,
                updated_utc=observed_utc,
            )
        return self.status

    def load_duplicate_scan(self, analysis_id: str) -> DuplicateScanStatus | None:
        if self.status is None or self.status.analysis_id != analysis_id:
            return None
        return self.status

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
                    size_bytes=128,
                    member_count=2,
                    physical_object_count=2,
                    expected_replica_count=0,
                    potential_savings_bytes=128,
                    review_state="UNREVIEWED",
                    created_utc="2026-08-02T10:00:00Z",
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
                    endpoint_id="endpoint-a",
                    file_entry_id="entry-a",
                    relative_path="A.bin",
                    member_role="DUPLICATE",
                    physical_object_key="object-a",
                ),
            ),
            next_cursor=None,
            has_more=False,
        )


def _duplicate_scan_status(
    *,
    analysis_id: str = "analysis-a",
    requested_utc: str = "2026-08-02T10:00:00Z",
) -> DuplicateScanStatus:
    return DuplicateScanStatus(
        scan_id="duplicate-scan-a",
        analysis_id=analysis_id,
        state=DuplicateScanState.QUEUED,
        stage=DuplicateScanStage.QUICK_SIGNATURE,
        candidate_file_count=2,
        quick_completed_count=0,
        full_hash_candidate_count=0,
        full_hash_completed_count=0,
        issue_count=0,
        requested_utc=requested_utc,
        updated_utc=requested_utc,
    )


class _InMemoryExternalResourceStateStore:
    def __init__(self) -> None:
        self.records: dict[tuple[ExternalResourceType, str], ExternalResourceRecord] = {}
        self.upsert_calls = 0

    def upsert_desired_resource_state(
        self,
        *,
        resource_type: ExternalResourceType,
        resource_id: str,
        desired_generation: int,
        desired_hash: str,
    ) -> ExternalResourceRecord:
        self.upsert_calls += 1
        record = ExternalResourceRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            desired_generation=desired_generation,
            desired_hash=desired_hash,
            state=ExternalResourceState.PENDING,
        )
        self.records[(resource_type, resource_id)] = record
        return record


class _FixedStandardBackupJobIdFactory(StandardBackupJobIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_standard_backup_job_ids(self) -> StandardBackupJobIds:
        self.calls += 1
        return StandardBackupJobIds(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
        )


class _InMemoryPlanStore(PlanStore, PlanOperationReadModelStore, PlanEndpointReadModelStore):
    def __init__(
        self,
        plan: SealedPlan | None = None,
        *additional_plans: SealedPlan,
    ) -> None:
        self.plans = {
            item.plan_id: item
            for item in ((plan,) + additional_plans)
            if item is not None
        }

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self.plans[plan.plan_id] = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        return self.plans.get(plan_id)

    def page_plan_operations(self, query: PlanOperationPageQuery) -> PlanOperationPage:
        validate_plan_operation_page_query(query)
        plan = self.load_sealed_plan(query.plan_id)
        operations: tuple[PlanOperationReadModel, ...] = ()
        if plan is not None:
            operations = tuple(
                sorted(
                    (_plan_operation_read_model(operation) for operation in plan.operations),
                    key=lambda operation: (
                        operation.execution_phase,
                        operation.stable_order_key,
                        operation.operation_id,
                    ),
                )
            )
        if query.target_endpoint_id is not None:
            operations = tuple(
                operation
                for operation in operations
                if operation.target_endpoint_id == query.target_endpoint_id
            )
        if query.risk_levels:
            operations = tuple(
                operation
                for operation in operations
                if operation.risk_level in query.risk_levels
            )
        if query.after is not None:
            operations = tuple(
                operation
                for operation in operations
                if _operation_after_cursor(operation, query.after)
            )
        page_operations = operations[: query.limit]
        has_more = len(operations) > query.limit
        return PlanOperationPage(
            plan_id=query.plan_id,
            operations=page_operations,
            next_cursor=_plan_operation_cursor(page_operations[-1])
            if has_more and page_operations
            else None,
            has_more=has_more,
            risk_counts=(
                dict(plan.risk_summary.get("counts", {}))
                if plan is not None
                else {}
            ),
            highest_risk=(
                PlanRiskLevel(str(plan.risk_summary["highest"]))
                if plan is not None
                else None
            ),
            target_endpoint_ids=(
                tuple(
                    endpoint.endpoint_id
                    for endpoint in plan.endpoints
                    if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
                )
                if plan is not None
                else ()
            ),
        )

    def page_plan_endpoints(self, query: PlanEndpointPageQuery) -> PlanEndpointPage:
        validate_plan_endpoint_page_query(query)
        plan = self.load_sealed_plan(query.plan_id)
        endpoints: tuple[PlanEndpointReadModel, ...] = ()
        if plan is not None:
            endpoints = tuple(
                sorted(
                    (_plan_endpoint_read_model(endpoint) for endpoint in plan.endpoints),
                    key=lambda endpoint: (
                        endpoint.role.value,
                        -1 if endpoint.target_ordinal is None else endpoint.target_ordinal,
                        endpoint.endpoint_id,
                    ),
                )
            )
        if query.after is not None:
            endpoints = tuple(
                endpoint for endpoint in endpoints if _endpoint_after_cursor(endpoint, query.after)
            )
        page_endpoints = endpoints[: query.limit]
        has_more = len(endpoints) > query.limit
        return PlanEndpointPage(
            plan_id=query.plan_id,
            endpoints=page_endpoints,
            next_cursor=_plan_endpoint_cursor(page_endpoints[-1])
            if has_more and page_endpoints
            else None,
            has_more=has_more,
        )


class _FixedOperationAuditReadStore:
    def __init__(
        self,
        *,
        identity: OperationAuditIdentity,
        outcome: OperationOutcomeSummary,
    ) -> None:
        self.identity = identity
        self.outcome = outcome

    def load_operation_audit_identity(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationAuditIdentity | None:
        if (run_id, operation_id) == (
            self.identity.run_id,
            self.identity.operation_id,
        ):
            return self.identity
        return None

    def list_operation_attempt_summaries(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int,
    ) -> tuple[OperationAttemptSummary, ...]:
        del run_id, operation_id, limit
        return ()

    def load_operation_outcome_summary(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> OperationOutcomeSummary | None:
        if (run_id, operation_id) == (
            self.identity.run_id,
            self.identity.operation_id,
        ):
            return self.outcome
        return None


class _InMemoryScheduleStore(ScheduleStore):
    def __init__(self, *schedules: ScheduleDefinition) -> None:
        self.schedules = {schedule.schedule_id: schedule for schedule in schedules}

    def save_schedule(self, schedule: ScheduleDefinition) -> None:
        self.schedules[schedule.schedule_id] = schedule

    def load_schedule(self, schedule_id: str) -> ScheduleDefinition | None:
        return self.schedules.get(schedule_id)


class _InMemorySnapshotEntryStore(SnapshotEntryReadModelStore):
    def __init__(
        self,
        entries: tuple[SnapshotEntryReadModel, ...] = (),
        coverage: tuple[SnapshotCoverageReadModel, ...] = (),
        issues: tuple[SnapshotIssueReadModel, ...] = (),
        filter_decisions: tuple[SnapshotFilterDecisionReadModel, ...] = (),
    ) -> None:
        self.entries = entries
        self.coverage = coverage
        self.issues = issues
        self.filter_decisions = filter_decisions

    def page_snapshot_entries(self, query: SnapshotEntryPageQuery) -> SnapshotEntryPage:
        validate_snapshot_entry_page_query(query)
        entries = tuple(
            sorted(
                self.entries,
                key=lambda entry: (entry.comparison_key, entry.relative_path, entry.entry_id),
            )
        )
        if query.after is not None:
            entries = tuple(
                entry for entry in entries if _snapshot_entry_after_cursor(entry, query.after)
            )
        page_entries = entries[: query.limit]
        has_more = len(entries) > query.limit
        return SnapshotEntryPage(
            snapshot_id=query.snapshot_id,
            entries=page_entries,
            next_cursor=_snapshot_entry_cursor(page_entries[-1])
            if has_more and page_entries
            else None,
            has_more=has_more,
        )

    def page_snapshot_directory_coverage(
        self,
        query: SnapshotCoveragePageQuery,
    ) -> SnapshotCoveragePage:
        validate_snapshot_coverage_page_query(query)
        coverage = tuple(
            sorted(
                (
                    item
                    for item in self.coverage
                    if not query.coverage_states or item.coverage_state in query.coverage_states
                ),
                key=lambda item: (item.comparison_key, item.relative_path),
            )
        )
        if query.after is not None:
            coverage = tuple(
                item for item in coverage if _snapshot_coverage_after_cursor(item, query.after)
            )
        page_coverage = coverage[: query.limit]
        has_more = len(coverage) > query.limit
        return SnapshotCoveragePage(
            snapshot_id=query.snapshot_id,
            coverage=page_coverage,
            next_cursor=_snapshot_coverage_cursor(page_coverage[-1])
            if has_more and page_coverage
            else None,
            has_more=has_more,
        )

    def page_snapshot_issues(self, query: SnapshotIssuePageQuery) -> SnapshotIssuePage:
        validate_snapshot_issue_page_query(query)
        issues = tuple(
            sorted(
                (
                    issue
                    for issue in self.issues
                    if not query.blocking_only or issue.blocks_destructive_actions
                ),
                key=lambda issue: (issue.relative_path, issue.issue_type, issue.issue_id),
            )
        )
        if query.after is not None:
            issues = tuple(
                issue for issue in issues if _snapshot_issue_after_cursor(issue, query.after)
            )
        page_issues = issues[: query.limit]
        has_more = len(issues) > query.limit
        return SnapshotIssuePage(
            snapshot_id=query.snapshot_id,
            issues=page_issues,
            next_cursor=_snapshot_issue_cursor(page_issues[-1]) if has_more and page_issues else None,
            has_more=has_more,
        )

    def page_snapshot_filter_decisions(
        self,
        query: SnapshotFilterDecisionPageQuery,
    ) -> SnapshotFilterDecisionPage:
        validate_snapshot_filter_decision_page_query(query)
        decisions = tuple(
            sorted(
                (
                    decision
                    for decision in self.filter_decisions
                    if not query.decision_states
                    or decision.decision_state in query.decision_states
                ),
                key=lambda decision: (
                    decision.relative_path,
                    decision.decision_id,
                ),
            )
        )
        if query.after is not None:
            decisions = tuple(
                decision
                for decision in decisions
                if (decision.relative_path, decision.decision_id)
                > (query.after.relative_path, query.after.decision_id)
            )
        page_decisions = decisions[: query.limit]
        has_more = len(decisions) > query.limit
        return SnapshotFilterDecisionPage(
            snapshot_id=query.snapshot_id,
            decisions=page_decisions,
            next_cursor=(
                SnapshotFilterDecisionCursor(
                    relative_path=page_decisions[-1].relative_path,
                    decision_id=page_decisions[-1].decision_id,
                )
                if has_more and page_decisions
                else None
            ),
            has_more=has_more,
        )


class _InMemoryRunStore(RunStore, RunActivityReadModelStore):
    def __init__(self) -> None:
        self.runs: dict[str, StartedRun] = {}
        self.idempotency_keys: dict[str, str] = {}
        self.stop_requests: set[str] = set()

    def save_started_run(self, run: StartedRun) -> None:
        self.runs[run.run_id] = run
        self.idempotency_keys[run.idempotency_key] = run.run_id

    def load_started_run(self, run_id: str) -> StartedRun | None:
        return self.runs.get(run_id)

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
        run_id = self.idempotency_keys.get(idempotency_key)
        if run_id is None:
            return None
        return self.runs[run_id]

    def list_recent_run_activity_summaries(
        self,
        *,
        limit: int,
        offset: int,
        job_id: str | None = None,
    ) -> tuple[RunActivitySummary, ...]:
        runs = tuple(
            run
            for run in sorted(self.runs.values(), key=lambda item: item.run_id, reverse=True)
            if job_id is None or run.job_id == job_id
        )
        return tuple(_run_activity_summary(run) for run in runs[offset : offset + limit])

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None:
            return None
        return next((target for target in run.targets if target.state is RunTargetState.PENDING), None)

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {RunState.QUEUED, RunState.PREFLIGHT}:
            return None
        updated_targets: list[StartedRunTarget] = []
        claimed: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.PENDING:
                claimed = replace(target, state=RunTargetState.ACQUIRING_LEASE)
                updated_targets.append(claimed)
            else:
                updated_targets.append(target)
        if claimed is None:
            return None
        self.runs[run_id] = replace(run, state=RunState.PREFLIGHT, targets=tuple(updated_targets))
        return claimed

    def record_run_target_lease_acquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.PREFLIGHT:
            return None
        updated_targets: list[StartedRunTarget] = []
        recorded: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.ACQUIRING_LEASE:
                recorded = replace(
                    target,
                    state=RunTargetState.REVALIDATING,
                    last_lease_id=lease_id,
                    last_ownership_epoch=ownership_epoch,
                    last_fencing_token=fencing_token,
                )
                updated_targets.append(recorded)
            else:
                updated_targets.append(target)
        if recorded is None:
            return None
        self.runs[run_id] = replace(run, targets=tuple(updated_targets))
        return recorded

    def request_run_pause(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {
            RunState.CREATED,
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
        }:
            return None
        updated = replace(run, state=RunState.PAUSING)
        self.runs[run_id] = updated
        return updated

    def resume_paused_run(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.PAUSED:
            return None
        updated = replace(
            run,
            state=RunState.QUEUED,
            targets=tuple(
                replace(
                    target,
                    state=RunTargetState.PENDING,
                    last_lease_id=None,
                    last_ownership_epoch=None,
                    last_fencing_token=None,
                )
                if target.state is RunTargetState.PAUSED
                else target
                for target in run.targets
            ),
        )
        self.runs[run_id] = updated
        return updated

    def request_run_stop_after_active_file(self, run_id: str) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state not in {
            RunState.CREATED,
            RunState.QUEUED,
            RunState.PREFLIGHT,
            RunState.EXECUTING,
            RunState.PAUSING,
            RunState.PAUSED,
        }:
            return None
        self.stop_requests.add(run_id)
        return run


class _InMemoryTriggerOccurrenceStore(TriggerOccurrenceStore):
    def __init__(self) -> None:
        self.occurrences: dict[str, TriggerOccurrence] = {}
        self.deduplication_keys: dict[str, str] = {}

    def record_received(self, occurrence: TriggerOccurrence) -> TriggerOccurrenceRegistration:
        existing_id = self.deduplication_keys.get(occurrence.deduplication_key)
        if existing_id is not None:
            existing = ensure_trigger_occurrence_compatible(
                self.occurrences[existing_id],
                occurrence,
            )
            return TriggerOccurrenceRegistration(occurrence=existing, deduplicated=True)
        self.occurrences[occurrence.occurrence_id] = occurrence
        self.deduplication_keys[occurrence.deduplication_key] = occurrence.occurrence_id
        return TriggerOccurrenceRegistration(occurrence=occurrence, deduplicated=False)

    def load_trigger_occurrence(self, occurrence_id: str) -> TriggerOccurrence | None:
        return self.occurrences.get(occurrence_id)

    def load_trigger_occurrence_by_deduplication_key(
        self,
        deduplication_key: str,
    ) -> TriggerOccurrence | None:
        occurrence_id = self.deduplication_keys.get(deduplication_key)
        if occurrence_id is None:
            return None
        return self.occurrences[occurrence_id]

    def mark_run_enqueued(
        self,
        *,
        deduplication_key: str,
        run_id: str,
    ) -> TriggerOccurrence:
        occurrence = self.load_trigger_occurrence_by_deduplication_key(deduplication_key)
        if occurrence is None:
            raise AssertionError("occurrence must exist before run enqueue")
        if occurrence.state is TriggerOccurrenceState.RUN_ENQUEUED and occurrence.run_id == run_id:
            return occurrence
        if occurrence.state is not TriggerOccurrenceState.RECEIVED or occurrence.run_id is not None:
            raise AssertionError("occurrence cannot be rebound to a different run")
        updated = replace(occurrence, state=TriggerOccurrenceState.RUN_ENQUEUED, run_id=run_id)
        self.occurrences[updated.occurrence_id] = updated
        return updated

    def mark_terminal(
        self,
        *,
        deduplication_key: str,
        state: TriggerOccurrenceState,
        terminal_effect_hash: str,
        run_id: str | None = None,
    ) -> TriggerOccurrence:
        occurrence = self.load_trigger_occurrence_by_deduplication_key(
            deduplication_key
        )
        if occurrence is None:
            raise AssertionError("occurrence must exist before completion")
        updated = replace(
            occurrence,
            state=state,
            run_id=run_id or occurrence.run_id,
            terminal_effect_hash=terminal_effect_hash,
        )
        self.occurrences[updated.occurrence_id] = updated
        return updated


class _InMemoryCatalogedFileStore(CatalogedFileReadModelStore):
    def __init__(self, files: tuple[CatalogedFileReadModel, ...]) -> None:
        self._files = files

    def list_recent_cataloged_files(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
    ) -> tuple[CatalogedFileReadModel, ...]:
        files = tuple(
            file
            for file in self._files
            if (run_id is None or file.run_id == run_id)
            and (target_endpoint_id is None or file.target_endpoint_id == target_endpoint_id)
        )
        return files[offset : offset + limit]


class _FixedRunIdFactory(RunIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_run_ids(self) -> RunIds:
        self.calls += 1
        return RunIds(run_id="run-a", logical_run_group_id="run-group-a")


def _client(
    *,
    role: ProcessRole = ProcessRole.GUI,
    identity: VerifiedClientIdentity | None = None,
    service: EngineHostIpcService | None = None,
) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=service or _service(),
        identity=identity or _identity(),
        role=role,
    )


def _run_activity_summary(run: StartedRun) -> RunActivitySummary:
    return RunActivitySummary(
        run_id=run.run_id,
        job_id=run.job_id,
        job_revision_id=run.job_revision_id,
        plan_id=run.plan_id,
        state=run.state,
        trigger_type=run.trigger_type,
        started_utc="2026-07-20T12:00:00.000Z",
        finished_utc=None,
        planned_operations=run.planned_operations,
        planned_bytes=run.planned_bytes,
        warning_count=run.warning_count,
        error_count=run.error_count,
        targets=tuple(
            RunTargetActivitySummary(
                run_target_id=target.run_target_id,
                endpoint_id=target.endpoint_id,
                endpoint_revision_id=target.endpoint_revision_id,
                state=target.state,
                planned_operations=target.planned_operations,
                completed_operations=0,
                planned_bytes=target.planned_bytes,
                completed_bytes=0,
            )
            for target in run.targets
        ),
    )


def _cataloged_file(
    handoff_id: str,
    *,
    run_id: str = "run-a",
    operation_id: str = "operation-a",
) -> CatalogedFileReadModel:
    return CatalogedFileReadModel(
        handoff_id=handoff_id,
        run_id=run_id,
        run_target_id=f"{run_id}-target-0000",
        operation_id=operation_id,
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        final_relative_path=f"Photos/{operation_id}.jpg",
        content_hash="a" * 64,
        lease_id="lease-a",
        fencing_token=1,
        effect_kind="COPY_NEW_FINAL_FILE",
        recorded_utc="2026-07-20T12:00:00.000Z",
    )


def _started_run(run_id: str = "run-a", *, job_id: str = "job-a") -> StartedRun:
    return StartedRun(
        run_id=run_id,
        job_id=job_id,
        job_revision_id=f"{job_id}-rev",
        plan_id=f"{job_id}-plan",
        command_request_id=f"{run_id}-request",
        idempotency_key=f"{run_id}-idempotency",
        command_receipt_id=f"{run_id}-idempotency",
        logical_run_group_id=f"{run_id}-group",
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=RunState.QUEUED,
        app_version="0B-dev",
        plan_checksum="a" * 64,
        planned_operations=1,
        planned_bytes=128,
        targets=(
            StartedRunTarget(
                run_target_id=f"{run_id}-target-0000",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                state=RunTargetState.PENDING,
                planned_operations=1,
                planned_bytes=128,
            ),
        ),
    )


def test_gui_client_handshake_and_status_query_succeed() -> None:
    ipc_client = _client()
    gui_client = EngineClient(ipc_client)

    handshake = gui_client.connect()
    status = gui_client.get_status()
    overview = gui_client.get_backup_overview()
    backup_job_detail = gui_client.get_backup_job_detail(job_id="job-a")
    activity = gui_client.get_activity_overview()
    plan_operations = gui_client.get_plan_operations(plan_id="plan-a")
    plan_endpoints = gui_client.get_plan_endpoints(plan_id="plan-a")
    snapshot_entries = gui_client.get_snapshot_entries(snapshot_id="snapshot-a")
    snapshot_coverage = gui_client.get_snapshot_coverage(snapshot_id="snapshot-a")
    snapshot_issues = gui_client.get_snapshot_issues(snapshot_id="snapshot-a")
    cataloged_files = gui_client.get_cataloged_files()

    assert handshake.status is IpcStatus.ACCEPTED
    assert handshake.reason is None
    assert handshake.payload["verified_user_sid_hash"] == EXPECTED_USER
    assert status.status is IpcStatus.ACCEPTED
    assert status.payload["host_status"]["role"] == ProcessRole.ENGINE_HOST.value
    assert status.payload["host_status"]["mutations_enabled"] is False
    assert overview.status is IpcStatus.ACCEPTED
    assert overview.payload["backup_overview"]["read_model_available"] is False
    assert backup_job_detail.status is IpcStatus.ACCEPTED
    assert backup_job_detail.payload["backup_job_detail"]["read_model_available"] is False
    assert backup_job_detail.payload["backup_job_detail"]["found"] is False
    assert activity.status is IpcStatus.ACCEPTED
    assert activity.payload["activity_overview"]["read_model_available"] is False
    assert plan_operations.status is IpcStatus.ACCEPTED
    assert plan_operations.payload["plan_operations"]["read_model_available"] is False
    assert plan_endpoints.status is IpcStatus.ACCEPTED
    assert plan_endpoints.payload["plan_endpoints"]["read_model_available"] is False
    assert snapshot_entries.status is IpcStatus.ACCEPTED
    assert snapshot_entries.payload["snapshot_entries"]["read_model_available"] is False
    assert snapshot_coverage.status is IpcStatus.ACCEPTED
    assert snapshot_coverage.payload["snapshot_coverage"]["read_model_available"] is False
    assert snapshot_issues.status is IpcStatus.ACCEPTED
    assert snapshot_issues.payload["snapshot_issues"]["read_model_available"] is False
    assert cataloged_files.status is IpcStatus.ACCEPTED
    assert cataloged_files.payload["cataloged_files"]["read_model_available"] is False


def test_handshake_and_status_publish_current_state_capacity() -> None:
    service = _service()
    capacity: dict[str, object] = {
        "scope": "LOCAL_APPDATA_STATE",
        "status": "HARD_STOP",
        "reason_code": "STATE_CAPACITY_LOCAL_FREE_SPACE_LOW",
    }
    service.state_capacity_provider = lambda: capacity
    ipc_client = _client(service=service)

    handshake = ipc_client.connect()
    status = ipc_client.query_status()

    assert handshake.payload["state_capacity"] == capacity
    assert status.payload["state_capacity"] == capacity


def test_selected_directory_identity_query_is_authorized_bounded_and_opaque() -> None:
    service = _service()
    service.selected_directory_identity_probe = _SelectedDirectoryIdentityProbe()
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_selected_directory_identities(
        path_labels=("C:/Pictures", "C:/Backup")
    )

    assert response.status is IpcStatus.ACCEPTED
    identities = response.payload["selected_directory_identities"]
    assert [item["ordinal"] for item in identities["items"]] == [0, 1]
    assert identities["relationships"] == [
        {
            "left_ordinal": 0,
            "right_ordinal": 1,
            "kind": "SAME_PHYSICAL_DEVICE",
            "blocking": False,
        }
    ]
    assert "disk-a" not in repr(identities)

    invalid = ipc_client.query_selected_directory_identities(
        path_labels=("a", "b", "c", "d", "e")
    )
    assert invalid.status is IpcStatus.REJECTED
    assert invalid.reason is IpcReason.INVALID_FRAME


def test_selected_directory_identity_query_requires_prior_handshake() -> None:
    service = _service()
    service.selected_directory_identity_probe = _SelectedDirectoryIdentityProbe()

    response = _client(service=service).query_selected_directory_identities(
        path_labels=("C:/Pictures",)
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_backup_overview_query_requires_prior_handshake() -> None:
    response = _client().query_backup_overview()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_backup_overview_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_backup_overview(limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_backup_overview_query_returns_bounded_draft_and_job_read_model() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )
    drafts.save_standard_backup_draft(draft)
    service = _service()
    service.job_draft_store = drafts
    service.standard_backup_job_read_store = catalog
    catalog.save_standard_backup_job(
        SealedStandardBackupJob(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
            draft_id="draft-a",
            command_request_id=REQUEST_ID_A,
            idempotency_key=IDEMPOTENCY_KEY_A,
            source_name="Pictures",
            source_path_label="C:/Users/Ada/Pictures",
            targets=(
                SealedStandardBackupTarget(
                    name="USB 1",
                    path_label="E:/Backup",
                    independent_device_id="disk-a",
                ),
            ),
            defaults=draft.defaults,
        )
    )
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_backup_overview(draft_id="draft-a", limit=1, offset=0)

    overview = response.payload["backup_overview"]
    assert response.status is IpcStatus.ACCEPTED
    assert overview["read_model_available"] is True
    assert overview["draft"]["can_create"] is True
    assert overview["jobs"][0]["job_id"] == "job-a"
    assert overview["jobs"][0]["configured_target_count"] == 1


def test_backup_job_detail_query_requires_prior_handshake() -> None:
    response = _client().query_backup_job_detail(job_id="job-a")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_backup_job_detail_query_rejects_invalid_job_id() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_backup_job_detail(job_id=" ")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_backup_job_detail_query_returns_exact_active_job_revision() -> None:
    catalog = _InMemoryStandardBackupJobCatalog()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )
    catalog.save_standard_backup_job(
        SealedStandardBackupJob(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
            draft_id="draft-a",
            command_request_id=REQUEST_ID_A,
            idempotency_key=IDEMPOTENCY_KEY_A,
            source_name="Pictures",
            source_path_label="C:/Users/Ada/Pictures",
            targets=(
                SealedStandardBackupTarget(
                    name="USB 1",
                    path_label="E:/Backup",
                    independent_device_id="disk-a",
                ),
            ),
            defaults=draft.defaults,
        )
    )
    service = _service()
    service.standard_backup_job_detail_store = catalog
    ipc_client = _client(service=service)
    ipc_client.connect()

    found = ipc_client.query_backup_job_detail(job_id="job-a")
    missing = ipc_client.query_backup_job_detail(job_id="job-missing")

    detail = found.payload["backup_job_detail"]
    assert found.status is IpcStatus.ACCEPTED
    assert detail["read_model_available"] is True
    assert detail["found"] is True
    assert detail["job"]["job_id"] == "job-a"
    assert detail["job"]["job_revision_id"] == "job-rev-a"
    assert detail["job"]["source_path_label"] == "C:/Users/Ada/Pictures"
    assert detail["job"]["defaults"]["retention"] == "THIRTY_DAYS"
    assert detail["job"]["targets"][0]["independent_device_id"] == "disk-a"
    assert missing.status is IpcStatus.ACCEPTED
    assert missing.payload["backup_job_detail"]["read_model_available"] is True
    assert missing.payload["backup_job_detail"]["found"] is False
    assert missing.payload["backup_job_detail"]["job"] is None


def test_backup_job_detail_query_includes_truthful_duplicate_summary() -> None:
    service = _service()
    service.standard_backup_job_detail_store = _AutomationJobDetailStore()
    service.duplicate_analysis_read_store = _DuplicateAnalysisReadStore()
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_backup_job_detail(job_id="job-a")

    summary = response.payload["backup_job_detail"]["job"]["duplicate_summary"]
    assert response.status is IpcStatus.ACCEPTED
    assert summary["expected_replica_count"] == 1
    assert summary["same_file_alias_path_count"] == 2
    assert summary["potential_savings_bytes"] == 0


def test_duplicate_scan_queries_return_bounded_status_groups_and_members() -> None:
    store = _DuplicateScanStore()
    store.status = _duplicate_scan_status()
    service = _service()
    service.duplicate_scan_store = store
    ipc_client = _client(service=service)
    ipc_client.connect()

    status = ipc_client.query_duplicate_scan(analysis_id="analysis-a")
    groups = ipc_client.query_duplicate_groups(
        analysis_id="analysis-a",
        limit=1,
        relationship_classes=("INTRA_ENDPOINT_DUPLICATE",),
    )
    members = ipc_client.query_duplicate_members(group_id="group-a", limit=1)

    assert status.status is IpcStatus.ACCEPTED
    assert status.payload["duplicate_scan"]["scan"]["candidate_file_count"] == 2
    assert groups.status is IpcStatus.ACCEPTED
    assert groups.payload["duplicate_groups"]["groups"][0][
        "relationship_class"
    ] == "INTRA_ENDPOINT_DUPLICATE"
    assert members.status is IpcStatus.ACCEPTED
    assert members.payload["duplicate_members"]["members"][0][
        "relative_path"
    ] == "A.bin"


def test_duplicate_scan_command_is_receipted_and_replayed_once() -> None:
    store = _DuplicateScanStore()
    receipts = _InMemoryCommandReceiptStore()
    service = _service(mutations_enabled=True)
    service.duplicate_scan_store = store
    service.duplicate_scan_utc_now = lambda: "2026-08-02T10:00:00Z"
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload: dict[str, object] = {"analysis_id": "analysis-a"}
    payload_hash = canonical_command_payload_hash(payload)

    first = ipc_client.submit_command(
        DuplicateScanCommandName.START_DUPLICATE_SCAN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_hash,
    )
    replay = ipc_client.submit_command(
        DuplicateScanCommandName.START_DUPLICATE_SCAN.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_hash,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert first.status is IpcStatus.ACCEPTED
    assert first.payload["duplicate_scan"]["state"] == "QUEUED"
    assert replay.status is IpcStatus.ACCEPTED
    assert replay.payload["idempotent_replay"] is True
    assert store.start_calls == 1
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert receipt.result_entity_type == "duplicate_scan"


def test_activity_overview_query_requires_prior_handshake() -> None:
    response = _client().query_activity_overview()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_activity_overview_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_activity_overview(limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_activity_overview_query_returns_bounded_run_read_model() -> None:
    runs = _InMemoryRunStore()
    runs.save_started_run(_started_run("run-a", job_id="job-a"))
    runs.save_started_run(_started_run("run-b", job_id="job-a"))
    service = _service()
    service.run_activity_read_store = runs
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_activity_overview(job_id="job-a", limit=1, offset=0)

    overview = response.payload["activity_overview"]
    assert response.status is IpcStatus.ACCEPTED
    assert overview["read_model_available"] is True
    assert overview["has_more"] is True
    assert overview["runs"][0]["run_id"] == "run-b"
    assert overview["runs"][0]["targets"][0]["state"] == RunTargetState.PENDING.value


def test_cataloged_files_query_requires_prior_handshake() -> None:
    response = _client().query_cataloged_files()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_cataloged_files_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_cataloged_files(limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_cataloged_files_query_returns_bounded_catalog_read_model() -> None:
    store = _InMemoryCatalogedFileStore(
        (
            _cataloged_file("final-file:run-b:operation-c", run_id="run-b"),
            _cataloged_file("final-file:run-a:operation-b", operation_id="operation-b"),
            _cataloged_file("final-file:run-a:operation-a", operation_id="operation-a"),
        )
    )
    service = _service()
    service.cataloged_file_read_store = store
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_cataloged_files(run_id="run-a", limit=1, offset=0)

    page = response.payload["cataloged_files"]
    assert response.status is IpcStatus.ACCEPTED
    assert page["read_model_available"] is True
    assert page["has_more"] is True
    assert page["run_id"] == "run-a"
    assert page["target_endpoint_id"] is None
    assert page["files"][0]["handoff_id"] == "final-file:run-a:operation-b"
    assert page["files"][0]["final_relative_path"] == "Photos/operation-b.jpg"


def test_plan_operations_query_requires_prior_handshake() -> None:
    response = _client().query_plan_operations(plan_id="plan-a")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_plan_operations_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_plan_operations(plan_id="plan-a", limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_plan_operations_query_returns_bounded_sealed_operation_page() -> None:
    service = _service()
    service.plan_operation_read_store = _InMemoryPlanStore(_sealed_plan_for_operation_pages())
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.query_plan_operations(plan_id="plan-page", limit=1)
    first_page = first.payload["plan_operations"]
    second = ipc_client.query_plan_operations(
        plan_id="plan-page",
        limit=1,
        after=first_page["next_cursor"],
    )
    second_page = second.payload["plan_operations"]

    assert first.status is IpcStatus.ACCEPTED
    assert first_page["read_model_available"] is True
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == {
        "execution_phase": 10,
        "stable_order_key": "010:Pictures/A.jpg",
        "operation_id": "op-a",
    }
    assert [operation["operation_id"] for operation in first_page["operations"]] == ["op-a"]
    assert first_page["operations"][0]["operation_type"] == PlanOperationType.COPY_NEW.value
    assert first_page["operations"][0]["target_precondition_kind"] == (
        TargetPreconditionKind.ABSENT.value
    )
    assert first_page["operations"][0]["target_endpoint_id"] == "target-a"
    assert first_page["risk_counts"] == {
        "LOW": 2,
        "MEDIUM": 0,
        "HIGH": 0,
        "BLOCKED": 0,
    }
    assert first_page["highest_risk"] == "LOW"
    assert first_page["target_endpoint_ids"] == ["target-a"]
    assert second.status is IpcStatus.ACCEPTED
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert [operation["operation_id"] for operation in second_page["operations"]] == ["op-b"]


def test_plan_operations_query_applies_target_and_risk_filters() -> None:
    service = _service()
    service.plan_operation_read_store = _InMemoryPlanStore(
        _sealed_plan_for_operation_pages()
    )
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_plan_operations(
        plan_id="plan-page",
        target_endpoint_id="target-a",
        risk_levels=("LOW",),
    )

    assert response.status is IpcStatus.ACCEPTED
    page = response.payload["plan_operations"]
    assert [operation["operation_id"] for operation in page["operations"]] == [
        "op-a",
        "op-b",
    ]


def test_plan_endpoints_query_requires_prior_handshake() -> None:
    response = _client().query_plan_endpoints(plan_id="plan-a")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_plan_endpoints_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_plan_endpoints(plan_id="plan-a", limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_plan_endpoints_query_returns_bounded_sealed_endpoint_page() -> None:
    service = _service()
    service.plan_endpoint_read_store = _InMemoryPlanStore(_sealed_plan_for_endpoint_pages())
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.query_plan_endpoints(plan_id="plan-endpoints", limit=1)
    first_page = first.payload["plan_endpoints"]
    second = ipc_client.query_plan_endpoints(
        plan_id="plan-endpoints",
        limit=2,
        after=first_page["next_cursor"],
    )
    second_page = second.payload["plan_endpoints"]

    assert first.status is IpcStatus.ACCEPTED
    assert first_page["read_model_available"] is True
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == {
        "role": PlanEndpointRole.SOURCE.value,
        "target_ordinal": None,
        "endpoint_id": "source-a",
    }
    assert [endpoint["endpoint_id"] for endpoint in first_page["endpoints"]] == ["source-a"]
    assert first_page["endpoints"][0]["snapshot_id"] == "source-snapshot-a"
    assert second.status is IpcStatus.ACCEPTED
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert [endpoint["endpoint_id"] for endpoint in second_page["endpoints"]] == ["target-a"]
    assert second_page["endpoints"][0]["role"] == PlanEndpointRole.TARGET_WRITABLE.value


def test_snapshot_entries_query_requires_prior_handshake() -> None:
    response = _client().query_snapshot_entries(snapshot_id="snapshot-a")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_snapshot_entries_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_snapshot_entries(snapshot_id="snapshot-a", limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_snapshot_entries_query_returns_bounded_entry_page() -> None:
    service = _service()
    service.snapshot_entry_read_store = _InMemorySnapshotEntryStore(
        (
            _snapshot_entry("file-b", "Pictures/B.jpg", "020:pictures/b.jpg", None),
            _snapshot_entry("file-a", "Pictures/A.jpg", "010:pictures/a.jpg", "case-group-a"),
        )
    )
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.query_snapshot_entries(snapshot_id="snapshot-a", limit=1)
    first_page = first.payload["snapshot_entries"]
    second = ipc_client.query_snapshot_entries(
        snapshot_id="snapshot-a",
        limit=1,
        after=first_page["next_cursor"],
    )
    second_page = second.payload["snapshot_entries"]

    assert first.status is IpcStatus.ACCEPTED
    assert first_page["read_model_available"] is True
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == {
        "comparison_key": "010:pictures/a.jpg",
        "relative_path": "Pictures/A.jpg",
        "entry_id": "file-a",
    }
    assert first_page["entries"] == [
        {
            "entry_id": "file-a",
            "relative_path": "Pictures/A.jpg",
            "comparison_key": "010:pictures/a.jpg",
            "object_type": "file",
            "size_bytes": 128,
            "birthtime_ns": 1_000,
            "case_collision_group_id": "case-group-a",
        }
    ]
    assert second.status is IpcStatus.ACCEPTED
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert [entry["entry_id"] for entry in second_page["entries"]] == ["file-b"]


def test_snapshot_coverage_query_requires_prior_handshake() -> None:
    response = _client().query_snapshot_coverage(snapshot_id="snapshot-a")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_snapshot_coverage_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_snapshot_coverage(snapshot_id="snapshot-a", limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_snapshot_coverage_query_returns_bounded_filtered_coverage_page() -> None:
    store = _InMemorySnapshotEntryStore(
        coverage=(
            _snapshot_coverage("Videos", "030:videos", "VOLATILE"),
            _snapshot_coverage("Archive", "020:archive", "COMPLETE"),
            _snapshot_coverage("Photos", "010:photos", "COMPLETE"),
        )
    )
    service = _service()
    service.snapshot_coverage_read_store = store
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.query_snapshot_coverage(
        snapshot_id="snapshot-a",
        limit=1,
        coverage_states=("COMPLETE",),
    )
    first_page = first.payload["snapshot_coverage"]
    second = ipc_client.query_snapshot_coverage(
        snapshot_id="snapshot-a",
        limit=1,
        after=first_page["next_cursor"],
        coverage_states=("COMPLETE",),
    )
    second_page = second.payload["snapshot_coverage"]

    assert first.status is IpcStatus.ACCEPTED
    assert first_page["read_model_available"] is True
    assert first_page["coverage_states"] == ["COMPLETE"]
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == {
        "comparison_key": "010:photos",
        "relative_path": "Photos",
    }
    assert first_page["coverage"] == [
        {
            "relative_path": "Photos",
            "comparison_key": "010:photos",
            "coverage_state": "COMPLETE",
            "case_mode": "CASE_INSENSITIVE",
            "case_mode_evidence": "probe-ok",
            "case_context_hash": "a" * 64,
            "case_probe_error": None,
        }
    ]
    assert second.status is IpcStatus.ACCEPTED
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert [coverage["relative_path"] for coverage in second_page["coverage"]] == ["Archive"]


def test_snapshot_issues_query_requires_prior_handshake() -> None:
    response = _client().query_snapshot_issues(snapshot_id="snapshot-a")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_snapshot_issues_query_rejects_invalid_bounds() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.query_snapshot_issues(snapshot_id="snapshot-a", limit=0)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_snapshot_issues_query_returns_bounded_blocking_issue_page() -> None:
    store = _InMemorySnapshotEntryStore(
        issues=(
            _snapshot_issue(3, "Videos", blocking=True),
            _snapshot_issue(2, "Photos", blocking=False),
            _snapshot_issue(1, "Archive", blocking=True),
        )
    )
    service = _service()
    service.snapshot_issue_read_store = store
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.query_snapshot_issues(
        snapshot_id="snapshot-a",
        limit=1,
        blocking_only=True,
    )
    first_page = first.payload["snapshot_issues"]
    second = ipc_client.query_snapshot_issues(
        snapshot_id="snapshot-a",
        limit=1,
        after=first_page["next_cursor"],
        blocking_only=True,
    )
    second_page = second.payload["snapshot_issues"]

    assert first.status is IpcStatus.ACCEPTED
    assert first_page["read_model_available"] is True
    assert first_page["blocking_only"] is True
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == {
        "relative_path": "Archive",
        "issue_type": "UNREADABLE_DIRECTORY",
        "issue_id": 1,
    }
    assert first_page["issues"] == [
        {
            "issue_id": 1,
            "relative_path": "Archive",
            "issue_type": "UNREADABLE_DIRECTORY",
            "blocks_destructive_actions": True,
            "error_code": "ERROR_ACCESS_DENIED",
            "sanitized_message": "access denied",
        }
    ]
    assert second.status is IpcStatus.ACCEPTED
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert [issue["relative_path"] for issue in second_page["issues"]] == ["Videos"]


def test_snapshot_filter_decisions_query_returns_exact_bounded_page() -> None:
    store = _InMemorySnapshotEntryStore(
        filter_decisions=(
            _snapshot_filter_decision(2, "Thumbs.db"),
            _snapshot_filter_decision(1, "$RECYCLE.BIN"),
        )
    )
    service = _service()
    service.snapshot_filter_decision_read_store = store
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.query_snapshot_filter_decisions(
        snapshot_id="snapshot-a",
        limit=1,
        decision_states=("EXCLUDED",),
    )
    page = response.payload["snapshot_filter_decisions"]

    assert response.status is IpcStatus.ACCEPTED
    assert page["read_model_available"] is True
    assert page["has_more"] is True
    assert page["decision_states"] == ["EXCLUDED"]
    assert page["decisions"][0]["relative_path"] == "$RECYCLE.BIN"
    assert page["decisions"][0]["matched_rule_id"] == "default-safe-rule"


def test_handshake_uses_verified_identity_not_payload_claim() -> None:
    ipc_client = _client()

    response = ipc_client.connect(claimed_user_sid_hash="attacker-claim")

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["verified_user_sid_hash"] == EXPECTED_USER


@pytest.mark.parametrize(
    ("protocol_version", "schema_version", "reason"),
    [
        (PROTOCOL_VERSION + 1, SCHEMA_VERSION, IpcReason.PROTOCOL_MISMATCH),
        (PROTOCOL_VERSION, SCHEMA_VERSION + 1, IpcReason.SCHEMA_MISMATCH),
    ],
)
def test_version_mismatch_is_rejected_without_status_access(
    protocol_version: int,
    schema_version: int,
    reason: IpcReason,
) -> None:
    ipc_client = _client()

    handshake = ipc_client.connect(
        protocol_version=protocol_version,
        schema_version=schema_version,
    )
    status = ipc_client.query_status()

    assert handshake.status is IpcStatus.REJECTED
    assert handshake.reason is reason
    assert status.status is IpcStatus.REJECTED
    assert status.reason is IpcReason.HANDSHAKE_REQUIRED


@pytest.mark.parametrize(
    ("identity", "reason"),
    [
        (_identity(user_sid_hash="other-user"), IpcReason.CLIENT_IDENTITY_MISMATCH),
        (_identity(session_id=99), IpcReason.CLIENT_IDENTITY_MISMATCH),
        (_identity(is_remote=True), IpcReason.REMOTE_CLIENT_REJECTED),
    ],
)
def test_identity_policy_rejects_untrusted_clients(
    identity: VerifiedClientIdentity,
    reason: IpcReason,
) -> None:
    response = _client(identity=identity).connect()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is reason


def test_mutating_commands_are_disabled_in_0b_ipc_slice() -> None:
    ipc_client = _client()
    ipc_client.connect()

    response = ipc_client.submit_command("UNKNOWN_MUTATION")

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
    assert response.payload["recognized"] is False


def test_create_standard_backup_job_command_is_recognized_but_rejected_in_0b() -> None:
    store = _InMemoryJobDraftStore()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    store.save_standard_backup_draft(draft)
    service = _service()
    service.job_draft_store = store
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        payload={"draft_id": "draft-a"},
        payload_hash="98cdbb1f712331be51355f90ab8c193c5c6f681d33d5c052cd38fe94820f3d02",
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
    assert response.payload["recognized"] is True
    assert response.payload["mutations_enabled"] is False
    assert response.payload["readiness"] == {
        "draft_id": "draft-a",
        "draft_found": True,
        "draft_valid": True,
        "validation_codes": [],
        "next_action": "Backup job creation is recognized but disabled in the 0B local preview.",
    }


def test_create_standard_backup_job_command_records_rejected_receipt() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert receipt is not None
    assert receipt.state.value == "REJECTED"
    assert receipt.principal_fingerprint == EXPECTED_USER
    assert receipt.rejection_reason == IpcReason.MUTATING_COMMANDS_DISABLED.value
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
    assert response.payload["receipt"] == {
        "request_id": REQUEST_ID_A,
        "idempotency_key": IDEMPOTENCY_KEY_A,
        "command_name": JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        "state": "REJECTED",
        "rejection_reason": IpcReason.MUTATING_COMMANDS_DISABLED.value,
    }


def test_register_writable_targets_command_is_recognized_and_receipted_when_disabled() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {"job_id": "job-a", "job_revision_id": "job-revision-a"}

    response = ipc_client.submit_command(
        WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=canonical_command_payload_hash(payload),
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
    assert response.payload["recognized"] is True
    assert response.payload["requested_job_revision_id"] == "job-revision-a"
    assert receipt is not None
    assert receipt.state is CommandReceiptState.REJECTED
    assert receipt.command_name == "REGISTER_WRITABLE_TARGETS"


def test_enabled_register_writable_targets_commits_receipt_and_replays_intent() -> None:
    receipts = _InMemoryCommandReceiptStore()
    registration = _RecordingWritableEndpointRegistration()
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.writable_endpoint_registration = registration  # type: ignore[assignment]
    service.writable_endpoint_registration_utc_now = lambda: "2026-08-01T07:00:00Z"
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {"job_id": "job-a", "job_revision_id": "job-revision-a"}
    payload_digest = canonical_command_payload_hash(payload)

    response = ipc_client.submit_command(
        WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_digest,
    )
    replay = ipc_client.submit_command(
        WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_digest,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["job"] == {
        "job_id": "job-a",
        "job_revision_id": "job-revision-registered",
    }
    assert response.payload["writable_endpoint_registration"]["completed"] is True
    assert response.payload["idempotent_replay"] is False
    assert response.payload["receipt"]["state"] == "SUCCEEDED"
    assert response.payload["receipt"]["result_entity_type"] == (
        "writable_endpoint_registration"
    )
    assert response.payload["receipt"]["result_entity_id"] == "registration-intent-a"
    assert replay.status is IpcStatus.ACCEPTED
    assert replay.payload["idempotent_replay"] is True
    assert len(registration.calls) == 2
    assert registration.calls[0] == {
        "job_id": "job-a",
        "job_revision_id": "job-revision-a",
        "command_request_id": REQUEST_ID_A,
        "command_idempotency_key": IDEMPOTENCY_KEY_A,
        "observed_utc": "2026-08-01T07:00:00Z",
    }
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED


def test_register_writable_targets_rejects_stale_reviewed_revision() -> None:
    receipts = _InMemoryCommandReceiptStore()
    registration = _RecordingWritableEndpointRegistration(
        error=WritableEndpointRegistrationError(
            "WRITABLE_ENDPOINT_JOB_REVISION_STALE",
            "Refresh the active backup job before registering its targets.",
            retryable=False,
        )
    )
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.writable_endpoint_registration = registration  # type: ignore[assignment]
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {"job_id": "job-a", "job_revision_id": "job-revision-stale"}

    response = ipc_client.submit_command(
        WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=canonical_command_payload_hash(payload),
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    registration_payload = response.payload["writable_endpoint_registration"]
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert registration_payload["validation_codes"] == [
        "WRITABLE_ENDPOINT_JOB_REVISION_STALE"
    ]
    assert receipt is not None
    assert receipt.state is CommandReceiptState.REJECTED
    assert receipt.rejection_reason == IpcReason.COMMAND_PRECONDITION_FAILED.value


def test_register_writable_targets_rejects_archived_job_before_coordinator() -> None:
    receipts = _InMemoryCommandReceiptStore()
    registration = _RecordingWritableEndpointRegistration()
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.writable_endpoint_registration = registration  # type: ignore[assignment]
    service.job_lifecycle_store = _StaticArchivedJobLifecycleStore()
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {"job_id": "job-a", "job_revision_id": "job-revision-a"}

    response = ipc_client.submit_command(
        WritableEndpointRegistrationCommandName.REGISTER_WRITABLE_TARGETS.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=canonical_command_payload_hash(payload),
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload["writable_endpoint_registration"]["validation_codes"] == [
        "JOB_ARCHIVED"
    ]
    assert registration.calls == []
    assert receipt is not None
    assert receipt.state is CommandReceiptState.REJECTED


def test_controlled_endpoint_takeover_rejects_archived_job_before_coordinator() -> None:
    receipts = _InMemoryCommandReceiptStore()
    takeover = _RecordingEndpointTakeover()
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.endpoint_takeover = takeover  # type: ignore[assignment]
    service.job_lifecycle_store = _StaticArchivedJobLifecycleStore()
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload: dict[str, object] = {
        "job_id": "job-a",
        "job_revision_id": "job-revision-a",
        "target_ordinal": 1,
        "endpoint_id": "11111111-1111-4111-8111-111111111111",
        "expected_foreign_owner_installation_id": (
            "22222222-2222-4222-8222-222222222222"
        ),
        "expected_ownership_epoch": 7,
        "explicit_confirmation": True,
    }

    response = ipc_client.submit_command(
        EndpointTakeoverCommandName.START_CONTROLLED_ENDPOINT_TAKEOVER.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=canonical_command_payload_hash(payload),
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload["endpoint_takeover"]["validation_codes"] == [
        "JOB_ARCHIVED"
    ]
    assert takeover.calls == 0
    assert receipt is not None
    assert receipt.state is CommandReceiptState.REJECTED


def test_enqueue_trigger_occurrence_command_records_rejected_receipt() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service, role=ProcessRole.TRIGGER_CLIENT)
    ipc_client.connect()
    trigger_payload = _trigger_payload()

    response = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=TRIGGER_DELIVERY_ID,
        idempotency_key=TRIGGER_DELIVERY_ID,
        payload=trigger_payload,
        payload_hash=payload_hash(trigger_payload),
    )

    receipt = receipts.load_command_receipt(TRIGGER_DELIVERY_ID)
    assert receipt is not None
    assert receipt.state is CommandReceiptState.REJECTED
    assert receipt.rejection_reason == IpcReason.MUTATING_COMMANDS_DISABLED.value
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
    assert response.payload["recognized"] is True
    assert response.payload["mutations_enabled"] is False
    assert response.payload["schedule_id"] == "schedule-a"
    assert response.payload["delivery_id"] == TRIGGER_DELIVERY_ID
    assert response.payload["receipt"] == {
        "request_id": TRIGGER_DELIVERY_ID,
        "idempotency_key": TRIGGER_DELIVERY_ID,
        "command_name": TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        "state": "REJECTED",
        "rejection_reason": IpcReason.MUTATING_COMMANDS_DISABLED.value,
    }


def test_enqueue_trigger_occurrence_command_rejects_invalid_payload_shape() -> None:
    service = _service()
    ipc_client = _client(service=service, role=ProcessRole.TRIGGER_CLIENT)
    ipc_client.connect()

    response = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=TRIGGER_DELIVERY_ID,
        idempotency_key=TRIGGER_DELIVERY_ID,
        payload={"schedule_id": "schedule-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_enabled_enqueue_trigger_occurrence_requires_dispatcher_dependencies() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    ipc_client = _client(service=service, role=ProcessRole.TRIGGER_CLIENT)
    ipc_client.connect()
    trigger_payload = _trigger_payload()

    response = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=TRIGGER_DELIVERY_ID,
        idempotency_key=TRIGGER_DELIVERY_ID,
        payload=trigger_payload,
        payload_hash=payload_hash(trigger_payload),
    )

    receipt = receipts.load_command_receipt(TRIGGER_DELIVERY_ID)
    assert receipt is not None
    assert receipt.state is CommandReceiptState.REJECTED
    assert receipt.rejection_reason == IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED
    assert response.payload["recognized"] is True
    assert response.payload["mutations_enabled"] is True
    assert response.payload["receipt"]["rejection_reason"] == (
        IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value
    )


def test_enabled_enqueue_trigger_occurrence_queues_fresh_safe_analysis() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    occurrences = _InMemoryTriggerOccurrenceStore()
    requests = _InMemoryBackupAnalysisRequestStore()
    service = _service(mutations_enabled=True)
    service.installation_id = "preview-a"
    service.schedule_store = _InMemoryScheduleStore(_schedule(plan))
    service.trigger_occurrence_store = occurrences
    service.standard_backup_job_detail_store = _BackupJobDetailStore()
    service.backup_analysis_request_store = requests  # type: ignore[assignment]
    service.command_receipt_store = receipts
    ipc_client = _client(service=service, role=ProcessRole.TRIGGER_CLIENT)
    ipc_client.connect()
    trigger_payload = _trigger_payload(scheduled_slot_utc="2026-07-20T12:00:00.000Z")

    response = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=TRIGGER_DELIVERY_ID,
        idempotency_key=TRIGGER_DELIVERY_ID,
        payload=trigger_payload,
        payload_hash=payload_hash(trigger_payload),
    )

    receipt = receipts.load_command_receipt(TRIGGER_DELIVERY_ID)
    occurrence = next(iter(occurrences.occurrences.values()))
    analysis_request = requests.load_backup_analysis_request(
        occurrence.occurrence_id
    )
    assert response.status is IpcStatus.ACCEPTED
    assert response.reason is None
    assert response.payload["enqueued"] is True
    assert response.payload["created"] is True
    assert response.payload["idempotent_replay"] is False
    assert response.payload["schedule_resolution"] == "READY"
    assert response.payload["occurrence"] == {
        "occurrence_id": occurrence.occurrence_id,
        "state": TriggerOccurrenceState.RECEIVED.value,
        "run_id": None,
    }
    assert response.payload["analysis_request"]["request_id"] == (
        occurrence.occurrence_id
    )
    assert response.payload["analysis_request"]["state"] == "QUEUED"
    assert response.payload["analysis_request"]["start_when_safe"] is True
    assert response.payload["receipt"]["state"] == CommandReceiptState.SUCCEEDED.value
    assert response.payload["receipt"]["result_entity_type"] == (
        "backup_analysis_request"
    )
    assert response.payload["receipt"]["result_entity_id"] == occurrence.occurrence_id
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert analysis_request is not None
    assert analysis_request.job_id == "job-a"
    assert analysis_request.job_revision_id == "job-rev-a"
    assert analysis_request.start_when_safe is True
    assert occurrence.state is TriggerOccurrenceState.RECEIVED
    assert occurrence.run_id is None


def test_enabled_enqueue_trigger_occurrence_deduplicates_retry_to_existing_analysis() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    occurrences = _InMemoryTriggerOccurrenceStore()
    requests = _InMemoryBackupAnalysisRequestStore()
    service = _service(mutations_enabled=True)
    service.installation_id = "preview-a"
    service.schedule_store = _InMemoryScheduleStore(_schedule(plan))
    service.trigger_occurrence_store = occurrences
    service.standard_backup_job_detail_store = _BackupJobDetailStore()
    service.backup_analysis_request_store = requests  # type: ignore[assignment]
    service.command_receipt_store = receipts
    ipc_client = _client(service=service, role=ProcessRole.TRIGGER_CLIENT)
    ipc_client.connect()
    first_payload = _trigger_payload(
        delivery_id=TRIGGER_DELIVERY_ID,
        observed_start_utc="2026-07-20T12:00:02.000Z",
        scheduled_slot_utc="2026-07-20T12:00:00.000Z",
    )
    retry_delivery_id = "22222222-2222-4222-8222-222222222222"
    retry_payload = _trigger_payload(
        delivery_id=retry_delivery_id,
        observed_start_utc="2026-07-20T12:00:08.000Z",
        scheduled_slot_utc="2026-07-20T12:00:00.000Z",
    )

    first = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=TRIGGER_DELIVERY_ID,
        idempotency_key=TRIGGER_DELIVERY_ID,
        payload=first_payload,
        payload_hash=payload_hash(first_payload),
    )
    retry = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=retry_delivery_id,
        idempotency_key=retry_delivery_id,
        payload=retry_payload,
        payload_hash=payload_hash(retry_payload),
    )

    assert first.status is IpcStatus.ACCEPTED
    assert retry.status is IpcStatus.ACCEPTED
    assert first.payload["analysis_request"]["request_id"] == retry.payload[
        "analysis_request"
    ]["request_id"]
    assert retry.payload["deduplicated"] is True
    assert retry.payload["created"] is False
    assert retry.payload["idempotent_replay"] is True
    assert retry.payload["occurrence"]["run_id"] is None
    assert receipts.load_command_receipt(retry_delivery_id) is not None
    assert len(receipts.receipts) == 2
    assert len(requests.requests) == 1


def test_enabled_enqueue_trigger_occurrence_rejects_schedule_revision_mismatch() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    occurrences = _InMemoryTriggerOccurrenceStore()
    requests = _InMemoryBackupAnalysisRequestStore()
    service = _service(mutations_enabled=True)
    service.installation_id = "preview-a"
    service.schedule_store = _InMemoryScheduleStore(_schedule(plan, desired_definition_hash="c" * 64))
    service.trigger_occurrence_store = occurrences
    service.standard_backup_job_detail_store = _BackupJobDetailStore()
    service.backup_analysis_request_store = requests  # type: ignore[assignment]
    service.command_receipt_store = receipts
    ipc_client = _client(service=service, role=ProcessRole.TRIGGER_CLIENT)
    ipc_client.connect()
    trigger_payload = _trigger_payload()

    response = ipc_client.submit_command(
        TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
        request_id=TRIGGER_DELIVERY_ID,
        idempotency_key=TRIGGER_DELIVERY_ID,
        payload=trigger_payload,
        payload_hash=payload_hash(trigger_payload),
    )

    receipt = receipts.load_command_receipt(TRIGGER_DELIVERY_ID)
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload["enqueued"] is False
    assert response.payload["schedule_resolution"] == "REVISION_MISMATCH"
    assert response.payload["validation_codes"] == ["TRIGGER_SCHEDULE_REVISION_MISMATCH"]
    assert response.payload["receipt"]["state"] == CommandReceiptState.REJECTED.value
    assert receipt is not None
    assert receipt.rejection_reason == IpcReason.COMMAND_PRECONDITION_FAILED.value
    assert occurrences.occurrences == {}
    assert requests.requests == {}


def test_restore_state_command_runs_in_read_only_ipc_mode(
    tmp_path: Path,
) -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    calls = []

    def restore_executor(command):
        calls.append(command)
        return {
            "backup_set_id": "set-a",
            "committed_path": str(tmp_path / "state" / "state-restore.committed.json"),
            "intent_path": str(tmp_path / "state" / "state-restore.intent.json"),
            "restore_epoch_id": command.restore_epoch_id,
            "restored_files": [],
            "state_set_hash": "a" * 64,
        }

    service.state_restore_executor = restore_executor
    ipc_client = _client(service=service)
    ipc_client.connect()
    command_payload = {
        "backup_dir": str(tmp_path / "state-backups" / "set-a"),
        "restore_epoch_id": "restore-ipc-a",
        "started_utc": "2026-07-30T12:05:00Z",
    }

    response = ipc_client.submit_command(
        StateMaintenanceCommandName.RESTORE_STATE_FROM_BACKUP_SET.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.reason is None
    assert response.payload["recognized"] is True
    assert response.payload["read_only_ipc_mode"] is True
    assert response.payload["mutations_enabled"] is False
    assert response.payload["restored"] is True
    assert response.payload["host_restart_required"] is True
    assert response.payload["restore_epoch_id"] == "restore-ipc-a"
    assert response.payload["restore_receipt"]["restore_epoch_id"] == "restore-ipc-a"
    assert calls[0].backup_dir == tmp_path / "state-backups" / "set-a"
    assert receipts.receipts == {}


def test_restore_state_command_requires_maintenance_executor(
    tmp_path: Path,
) -> None:
    service = _service()
    ipc_client = _client(service=service)
    ipc_client.connect()
    command_payload = {
        "backup_dir": str(tmp_path / "state-backups" / "set-a"),
        "restore_epoch_id": "restore-ipc-a",
        "started_utc": "2026-07-30T12:05:00Z",
    }

    response = ipc_client.submit_command(
        StateMaintenanceCommandName.RESTORE_STATE_FROM_BACKUP_SET.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED
    assert response.payload["recognized"] is True
    assert response.payload["executor_configured"] is False
    assert response.payload["restored"] is False
    assert response.payload["read_only_ipc_mode"] is True


def test_restore_state_command_requires_read_only_ipc_mode(
    tmp_path: Path,
) -> None:
    service = _service(mutations_enabled=True)
    calls = []

    def restore_executor(command):
        calls.append(command)
        return {"restore_epoch_id": command.restore_epoch_id}

    service.state_restore_executor = restore_executor
    ipc_client = _client(service=service)
    ipc_client.connect()
    command_payload = {
        "backup_dir": str(tmp_path / "state-backups" / "set-a"),
        "restore_epoch_id": "restore-ipc-a",
        "started_utc": "2026-07-30T12:05:00Z",
    }

    response = ipc_client.submit_command(
        StateMaintenanceCommandName.RESTORE_STATE_FROM_BACKUP_SET.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload["recognized"] is True
    assert response.payload["executor_configured"] is True
    assert response.payload["restored"] is False
    assert response.payload["read_only_ipc_mode"] is False
    assert response.payload["error_code"] == "RESTORE_STATE_REQUIRES_READ_ONLY_IPC_MODE"
    assert calls == []


def test_enabled_save_standard_backup_draft_is_idempotent() -> None:
    drafts = _InMemoryJobDraftStore()
    receipts = _InMemoryCommandReceiptStore()
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {
        "draft_id": "setup-autosave",
        "draft": {
            "draft_id": "setup-autosave",
            "schema_version": 1,
            "source_name": "Pictures",
            "source_path_label": "C:/Users/Ada/Pictures",
            "targets": [],
            "defaults": {
                "behavior": "UPDATE_BACKUP",
                "file_selection": "ALL_USER_FILES",
                "verification": "STANDARD",
                "retention": "THIRTY_DAYS",
                "extra_files": "KEEP_ON_TARGET",
                "performance": "AUTO",
            },
        },
    }
    payload_digest = canonical_command_payload_hash(payload)

    first = ipc_client.submit_command(
        JobDraftCommandName.SAVE_STANDARD_BACKUP_DRAFT.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_digest,
    )
    second = ipc_client.submit_command(
        JobDraftCommandName.SAVE_STANDARD_BACKUP_DRAFT.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_digest,
    )

    assert first.status is IpcStatus.ACCEPTED
    assert first.payload["saved"] is True
    assert first.payload["idempotent_replay"] is False
    assert second.status is IpcStatus.ACCEPTED
    assert second.payload["saved"] is True
    assert second.payload["idempotent_replay"] is True
    assert drafts.load_standard_backup_draft("setup-autosave") == (
        StandardBackupJobDraft.new("setup-autosave").with_source(
            name="Pictures",
            path_label="C:/Users/Ada/Pictures",
        )
    )
    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert receipt.result_entity_type == "standard_backup_job_draft"
    assert receipt.result_entity_id == "setup-autosave"


def test_enabled_create_standard_backup_job_persists_job_and_succeeds_receipt() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    id_factory = _FixedStandardBackupJobIdFactory()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    drafts.save_standard_backup_draft(draft)
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = id_factory
    service.command_receipt_store = receipts
    service.job_snapshot_refresh = lambda: SnapshotMaterializationRefreshReport(
        0, 0, 0, 0, 0
    )
    service.initial_backup_plan_refresh = lambda: InitialBackupPlanRefreshReport(
        0, 0, 0, 0, 0
    )
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.ACCEPTED
    assert response.reason is None
    assert response.payload["created"] is True
    assert response.payload["idempotent_replay"] is False
    assert response.payload["job"] == {
        "job_id": "job-a",
        "job_revision_id": "job-rev-a",
        "filter_set_id": "filter-a",
    }
    assert response.payload["receipt"]["state"] == CommandReceiptState.SUCCEEDED.value
    assert response.payload["receipt"]["result_entity_type"] == "standard_backup_job"
    assert response.payload["receipt"]["result_entity_id"] == "job-a"
    assert response.payload["initial_backup_plan_refresh"] == {
        "completed": True,
        "report": {
            "sealed_plan_count": 0,
            "reused_plan_count": 0,
            "no_changes_count": 0,
            "blocked_job_count": 0,
            "failed_job_count": 0,
            "results": [],
        },
    }
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert catalog.load_standard_backup_job("job-a") is not None
    assert drafts.load_standard_backup_draft("draft-a") == draft
    assert id_factory.calls == 1


def test_inline_job_creation_binds_engine_host_device_identity() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    service = _service(mutations_enabled=True)
    service.selected_directory_identity_probe = _SelectedDirectoryIdentityProbe()
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = _FixedStandardBackupJobIdFactory()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = _inline_creation_payload(independent_device_id="untrusted-client")

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=canonical_command_payload_hash(payload),
    )

    saved = drafts.load_standard_backup_draft("draft-identity")
    job = catalog.load_standard_backup_job("job-a")
    assert response.status is IpcStatus.ACCEPTED
    assert saved is not None and job is not None
    assert saved.targets[0].independent_device_id != "untrusted-client"
    assert saved.targets[0].independent_device_id is not None
    assert len(saved.targets[0].independent_device_id or "") == 64
    assert job.targets[0].independent_device_id == (
        saved.targets[0].independent_device_id
    )


def test_inline_job_creation_durably_rejects_physical_alias_before_saving() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    probe = _AliasingSelectedDirectoryIdentityProbe()
    service = _service(mutations_enabled=True)
    service.selected_directory_identity_probe = probe
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = _FixedStandardBackupJobIdFactory()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = _inline_creation_payload(independent_device_id="untrusted-client")
    payload_digest = canonical_command_payload_hash(payload)

    first = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_digest,
    )
    calls_after_first = probe.calls
    second = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_digest,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert first.status is IpcStatus.REJECTED
    assert first.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert first.payload["readiness"]["validation_codes"] == [
        "STANDARD_BACKUP_JOB_PHYSICAL_ROOT_OVERLAP"
    ]
    assert second.status is IpcStatus.REJECTED
    assert second.payload["idempotent_replay"] is True
    assert probe.calls == calls_after_first
    assert receipt is not None and receipt.state is CommandReceiptState.REJECTED
    assert drafts.load_standard_backup_draft("draft-identity") is None
    assert catalog.load_standard_backup_job("job-a") is None


def test_command_storage_failure_is_sanitized_and_not_retried() -> None:
    drafts = _InMemoryJobDraftStore()
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    transaction = _FailingCommandEffectTransaction()
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = _InMemoryStandardBackupJobCatalog()
    service.standard_backup_job_id_factory = _FixedStandardBackupJobIdFactory()
    service.command_receipt_store = _InMemoryCommandReceiptStore()
    service.command_effect_transaction = transaction
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload == {
        "error_code": "SQLITE_FULL",
        "retryable": False,
    }
    assert transaction.calls == 1


def test_created_job_stays_accepted_when_post_commit_classification_fails() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = _FixedStandardBackupJobIdFactory()
    service.command_receipt_store = receipts

    def fail_refresh() -> EndpointClassificationRefreshReport:
        raise RuntimeError("private filesystem detail")

    service.endpoint_classification_refresh = fail_refresh
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["created"] is True
    assert response.payload["endpoint_classification_refresh"] == {
        "completed": False,
        "reason_code": "ENDPOINT_CLASSIFICATION_REFRESH_FAILED",
    }
    assert catalog.load_standard_backup_job("job-a") is not None
    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED


def test_created_job_stays_accepted_when_post_commit_snapshot_refresh_fails() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = _FixedStandardBackupJobIdFactory()
    service.command_receipt_store = receipts

    def fail_refresh() -> SnapshotMaterializationRefreshReport:
        raise RuntimeError("private filesystem detail")

    initial_plan_refresh_calls = 0

    def refresh_initial_plan() -> InitialBackupPlanRefreshReport:
        nonlocal initial_plan_refresh_calls
        initial_plan_refresh_calls += 1
        return InitialBackupPlanRefreshReport(0, 0, 0, 0, 0)

    service.job_snapshot_refresh = fail_refresh
    service.initial_backup_plan_refresh = refresh_initial_plan
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["created"] is True
    assert response.payload["job_snapshot_refresh"] == {
        "completed": False,
        "reason_code": "JOB_SNAPSHOT_REFRESH_FAILED",
    }
    assert response.payload["initial_backup_plan_refresh"] == {
        "completed": False,
        "reason_code": "INITIAL_BACKUP_PLAN_SNAPSHOT_REFRESH_REQUIRED",
    }
    assert initial_plan_refresh_calls == 0
    assert catalog.load_standard_backup_job("job-a") is not None
    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED


def test_snapshot_refresh_waits_when_post_commit_classification_fails() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = _FixedStandardBackupJobIdFactory()
    service.command_receipt_store = receipts
    snapshot_refresh_calls = 0

    def fail_classification() -> EndpointClassificationRefreshReport:
        raise RuntimeError("private filesystem detail")

    def refresh_snapshots() -> SnapshotMaterializationRefreshReport:
        nonlocal snapshot_refresh_calls
        snapshot_refresh_calls += 1
        return SnapshotMaterializationRefreshReport(0, 0, 0, 0, 0)

    service.endpoint_classification_refresh = fail_classification
    service.job_snapshot_refresh = refresh_snapshots
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["job_snapshot_refresh"] == {
        "completed": False,
        "reason_code": "JOB_SNAPSHOT_CLASSIFICATION_REFRESH_REQUIRED",
    }
    assert snapshot_refresh_calls == 0


def test_enabled_create_standard_backup_job_replay_returns_existing_success_receipt() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    id_factory = _FixedStandardBackupJobIdFactory()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    drafts.save_standard_backup_draft(draft)
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = id_factory
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )
    second = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert first.status is IpcStatus.ACCEPTED
    assert second.status is IpcStatus.ACCEPTED
    assert first.request_id == REQUEST_ID_A
    assert second.request_id == REQUEST_ID_B
    assert len(receipts.receipts) == 1
    assert receipts.receipts[IDEMPOTENCY_KEY_A].schema_version == COMMAND_SCHEMA_VERSION
    assert id_factory.calls == 1
    assert second.payload["created"] is False
    assert second.payload["idempotent_replay"] is True
    assert second.payload["receipt"]["request_id"] == REQUEST_ID_A
    assert second.payload["job"]["job_id"] == "job-a"


def test_enabled_create_standard_backup_job_rejects_invalid_draft_before_effect() -> None:
    drafts = _InMemoryJobDraftStore()
    catalog = _InMemoryStandardBackupJobCatalog()
    receipts = _InMemoryCommandReceiptStore()
    id_factory = _FixedStandardBackupJobIdFactory()
    service = _service(mutations_enabled=True)
    service.job_draft_store = drafts
    service.standard_backup_job_catalog = catalog
    service.standard_backup_job_id_factory = id_factory
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload["readiness"]["validation_codes"] == ["DRAFT_NOT_FOUND"]
    assert response.payload["receipt"]["state"] == CommandReceiptState.REJECTED.value
    assert receipt is not None
    assert receipt.rejection_reason == IpcReason.COMMAND_PRECONDITION_FAILED.value
    assert catalog.jobs == {}
    assert id_factory.calls == 0


def test_enabled_create_standard_backup_job_requires_dispatcher_dependencies() -> None:
    service = _service(mutations_enabled=True)
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED
    assert response.payload["recognized"] is True
    assert response.payload["mutations_enabled"] is True


def test_enabled_check_backup_queues_durable_idempotent_request() -> None:
    receipts = _InMemoryCommandReceiptStore()
    requests = _InMemoryBackupAnalysisRequestStore()
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.backup_analysis_request_store = requests  # type: ignore[assignment]
    service.standard_backup_job_detail_store = _BackupJobDetailStore()
    service.run_store = _InMemoryRunStore()
    ipc_client = _client(service=service)
    ipc_client.connect()
    command_payload = {"job_id": "job-a", "start_when_safe": True}

    first = ipc_client.submit_command(
        BackupAnalysisCommandName.CHECK_BACKUP.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )
    replay = ipc_client.submit_command(
        BackupAnalysisCommandName.CHECK_BACKUP.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )

    assert first.status is IpcStatus.ACCEPTED
    assert first.payload["queued"] is True
    assert first.payload["analysis_request"]["state"] == "QUEUED"
    assert first.payload["analysis_request"]["start_when_safe"] is True
    assert replay.status is IpcStatus.ACCEPTED
    assert replay.payload["idempotent_replay"] is True
    assert len(requests.requests) == 1
    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert receipt is not None
    assert receipt.result_entity_type == "backup_analysis_request"
    assert receipt.state is CommandReceiptState.SUCCEEDED


def test_enabled_daily_schedule_is_persisted_staged_and_idempotent() -> None:
    receipts = _InMemoryCommandReceiptStore()
    schedules = _InMemoryScheduleStore()
    resources = _InMemoryExternalResourceStateStore()
    service = _service(mutations_enabled=True)
    service.installation_id = "install-a"
    service.command_receipt_store = receipts
    service.standard_backup_job_detail_store = _AutomationJobDetailStore()
    service.schedule_store = schedules
    service.external_resource_state_store = resources  # type: ignore[assignment]
    service.task_scheduler_executable_path = "C:/Program Files/MediaSync/MediaSync.exe"
    service.task_scheduler_time_zone_id = "W. Europe Standard Time"
    ipc_client = _client(service=service)
    ipc_client.connect()
    command_payload = {
        "job_id": "job-a",
        "expected_job_revision_id": "job-rev-a",
        "expected_lifecycle_row_version": 1,
        "expected_schedule_row_version": 0,
        "enabled": True,
        "local_time": "21:30",
    }

    first = ipc_client.submit_command(
        JobSchedulingCommandName.CONFIGURE_DAILY_BACKUP_SCHEDULE.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )
    replay = ipc_client.submit_command(
        JobSchedulingCommandName.CONFIGURE_DAILY_BACKUP_SCHEDULE.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )

    assert first.status is IpcStatus.ACCEPTED
    assert first.payload["configured"] is True
    assert first.payload["reconciliation_state"] == "PENDING"
    assert first.payload["schedule"]["configuration"] == {
        "days_interval": 1,
        "hour": 21,
        "kind": "daily",
        "minute": 30,
    }
    assert first.payload["schedule"]["requires_network"] is True
    assert first.payload["schedule"]["task_logon_type"] == "INTERACTIVE_TOKEN"
    assert first.payload["schedule"]["run_only_when_logged_on"] is True
    assert replay.status is IpcStatus.ACCEPTED
    assert replay.payload["idempotent_replay"] is True
    assert resources.upsert_calls == 1
    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert receipt.result_entity_type == "backup_automation_schedule"


def test_daily_schedule_precondition_rejection_replays_original_validation() -> None:
    receipts = _InMemoryCommandReceiptStore()
    schedules = _InMemoryScheduleStore()
    resources = _InMemoryExternalResourceStateStore()
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.standard_backup_job_detail_store = _AutomationJobDetailStore()
    service.schedule_store = schedules
    service.external_resource_state_store = resources  # type: ignore[assignment]
    service.task_scheduler_executable_path = "C:/MediaSync.exe"
    service.task_scheduler_time_zone_id = "W. Europe Standard Time"
    ipc_client = _client(service=service)
    ipc_client.connect()
    command_payload = {
        "job_id": "job-a",
        "expected_job_revision_id": "stale-revision",
        "expected_lifecycle_row_version": 1,
        "expected_schedule_row_version": 0,
        "enabled": True,
        "local_time": "21:30",
    }

    first = ipc_client.submit_command(
        JobSchedulingCommandName.CONFIGURE_DAILY_BACKUP_SCHEDULE.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )
    replay = ipc_client.submit_command(
        JobSchedulingCommandName.CONFIGURE_DAILY_BACKUP_SCHEDULE.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=command_payload,
        payload_hash=payload_hash(command_payload),
    )

    assert first.status is IpcStatus.REJECTED
    assert first.payload["validation_code"] == "BACKUP_AUTOMATION_JOB_REVISION_STALE"
    assert replay.status is IpcStatus.REJECTED
    assert replay.payload["validation_code"] == "BACKUP_AUTOMATION_JOB_REVISION_STALE"
    assert replay.payload["idempotent_replay"] is True
    assert schedules.schedules == {}
    assert resources.upsert_calls == 0


def test_start_run_command_is_recognized_but_rejected_when_mutations_disabled() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.plan_store = _InMemoryPlanStore(plan)
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        payload_hash=payload_hash(
            {"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum}
        ),
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.MUTATING_COMMANDS_DISABLED
    assert response.payload["recognized"] is True
    assert response.payload["mutations_enabled"] is False
    assert response.payload["readiness"]["plan_runnable"] is True
    assert response.payload["receipt"]["state"] == CommandReceiptState.REJECTED.value


def test_enabled_start_run_persists_queued_run_and_succeeds_receipt() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    runs = _InMemoryRunStore()
    id_factory = _FixedRunIdFactory()
    service = _service(mutations_enabled=True)
    service.plan_store = _InMemoryPlanStore(plan)
    service.run_store = runs
    service.run_id_factory = id_factory
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        payload_hash=payload_hash(
            {"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum}
        ),
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    run = runs.load_started_run("run-a")
    assert response.status is IpcStatus.ACCEPTED
    assert response.reason is None
    assert response.payload["created"] is True
    assert response.payload["idempotent_replay"] is False
    assert response.payload["run"] == {
        "run_id": "run-a",
        "logical_run_group_id": "run-group-a",
        "resumed_from_run_id": None,
        "job_id": "job-a",
        "job_revision_id": "job-rev-a",
        "plan_id": "plan-a",
        "state": RunState.QUEUED.value,
        "plan_checksum": plan.plan_checksum,
        "planned_operations": 1,
        "planned_bytes": 128,
        "target_endpoint_ids": ["target-a"],
        "operation_ids": [],
        "source_operation_ids": [],
    }
    assert response.payload["receipt"]["state"] == CommandReceiptState.SUCCEEDED.value
    assert response.payload["receipt"]["result_entity_type"] == "run"
    assert response.payload["receipt"]["result_entity_id"] == "run-a"
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert run is not None
    assert run.state is RunState.QUEUED
    assert id_factory.calls == 1


def test_enabled_start_run_carries_failed_target_retry_scope_and_lineage() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    runs = _InMemoryRunStore()
    source = replace(
        _started_run("run-source"),
        job_revision_id=plan.job_revision_id,
        plan_id="plan-source",
        plan_checksum=plan.plan_checksum,
        logical_run_group_id="run-group-original",
        state=RunState.PARTIAL_FAILURE,
        targets=(
            replace(
                _started_run("run-source").targets[0],
                state=RunTargetState.FAILED,
            ),
        ),
    )
    runs.save_started_run(source)
    service = _service(mutations_enabled=True)
    service.plan_store = _InMemoryPlanStore(plan)
    service.run_store = runs
    service.run_id_factory = _FixedRunIdFactory()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {
        "plan_id": plan.plan_id,
        "plan_checksum": plan.plan_checksum,
        "target_endpoint_ids": ["target-a"],
        "resumed_from_run_id": "run-source",
    }

    response = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_hash(payload),
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["run"]["target_endpoint_ids"] == ["target-a"]
    assert response.payload["run"]["resumed_from_run_id"] == "run-source"
    assert response.payload["run"]["logical_run_group_id"] == "run-group-original"
    retry = runs.load_started_run("run-a")
    assert retry is not None
    assert retry.resumed_from_run_id == "run-source"
    assert tuple(target.endpoint_id for target in retry.targets) == ("target-a",)


def test_enabled_start_run_maps_failed_operation_retry_to_fresh_plan() -> None:
    source_plan = _sealed_plan(plan_id="plan-source", operation_id="op-source")
    plan = _sealed_plan(plan_id="plan-fresh", operation_id="op-fresh")
    receipts = _InMemoryCommandReceiptStore()
    runs = _InMemoryRunStore()
    source = replace(
        _started_run("run-source"),
        job_revision_id=plan.job_revision_id,
        plan_id=source_plan.plan_id,
        plan_checksum=source_plan.plan_checksum,
        logical_run_group_id="run-group-original",
        state=RunState.COMPLETED_WITH_WARNINGS,
        targets=(
            replace(
                _started_run("run-source").targets[0],
                state=RunTargetState.SUCCEEDED_WITH_WARNINGS,
            ),
        ),
    )
    runs.save_started_run(source)
    service = _service(mutations_enabled=True)
    service.plan_store = _InMemoryPlanStore(source_plan, plan)
    service.run_store = runs
    service.run_id_factory = _FixedRunIdFactory()
    service.command_receipt_store = receipts
    service.operation_audit_read_store = _FixedOperationAuditReadStore(
        identity=OperationAuditIdentity(
            run_id=source.run_id,
            run_target_id=source.targets[0].run_target_id,
            operation_id="op-source",
            target_relative_path="Pictures/A.jpg",
        ),
        outcome=OperationOutcomeSummary(
            final_state="SKIPPED",
            completed_utc="2026-07-31T12:00:00.000Z",
            bytes_transferred=0,
            transfer_state="NOT_TRANSFERRED",
            assurance_level="NOT_RECORDED",
            hash_evidence_kind=None,
            durability_level="NOT_RECORDED",
            verification_json=None,
            error_code="LOCAL_IO_TRANSIENT",
        ),
    )
    ipc_client = _client(service=service)
    ipc_client.connect()
    payload = {
        "plan_id": plan.plan_id,
        "plan_checksum": plan.plan_checksum,
        "target_endpoint_ids": ["target-a"],
        "resumed_from_run_id": source.run_id,
        "source_operation_ids": ["op-source"],
    }

    response = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=payload,
        payload_hash=payload_hash(payload),
    )

    assert response.status is IpcStatus.ACCEPTED
    assert response.payload["run"]["operation_ids"] == ["op-fresh"]
    assert response.payload["run"]["source_operation_ids"] == ["op-source"]
    retry = runs.load_started_run("run-a")
    assert retry is not None
    assert retry.summary["scope"] == "OPERATION_RETRY"
    assert retry.planned_operations == 1


def test_enabled_pause_and_resume_commands_are_durable_and_idempotent() -> None:
    receipts = _InMemoryCommandReceiptStore()
    runs = _InMemoryRunStore()
    started = replace(
        _started_run(),
        state=RunState.EXECUTING,
        targets=(
            replace(
                _started_run().targets[0],
                state=RunTargetState.EXECUTING,
                last_lease_id="lease-a",
                last_ownership_epoch=1,
                last_fencing_token=7,
            ),
        ),
    )
    runs.save_started_run(started)
    service = _service(mutations_enabled=True)
    service.command_receipt_store = receipts
    service.run_control_store = runs
    ipc_client = _client(service=service)
    ipc_client.connect()
    pause_payload = {"run_id": started.run_id}

    pause = ipc_client.submit_command(
        RunCommandName.PAUSE_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=pause_payload,
        payload_hash=payload_hash(pause_payload),
    )
    replay = ipc_client.submit_command(
        RunCommandName.PAUSE_RUN.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload=pause_payload,
        payload_hash=payload_hash(pause_payload),
    )

    assert pause.status is IpcStatus.ACCEPTED
    assert pause.payload["run"]["state"] == RunState.PAUSING.value
    assert replay.status is IpcStatus.ACCEPTED
    assert replay.payload["idempotent_replay"] is True
    assert len(receipts.receipts) == 1

    pausing = runs.load_started_run(started.run_id)
    assert pausing is not None
    paused = replace(
        pausing,
        state=RunState.PAUSED,
        targets=(
            replace(
                pausing.targets[0],
                state=RunTargetState.PAUSED,
                last_lease_id=None,
                last_ownership_epoch=None,
                last_fencing_token=None,
            ),
        ),
    )
    runs.runs[paused.run_id] = paused
    resume_payload = {"run_id": paused.run_id}
    resume = ipc_client.submit_command(
        RunCommandName.RESUME_RUN.value,
        request_id="11111111-2222-4333-8444-555555555555",
        idempotency_key="99999999-8888-4777-8666-555555555555",
        payload=resume_payload,
        payload_hash=payload_hash(resume_payload),
    )

    resumed = runs.load_started_run(paused.run_id)
    assert resume.status is IpcStatus.ACCEPTED
    assert resume.payload["run"]["state"] == RunState.QUEUED.value
    assert resumed is not None
    assert resumed.targets[0].state is RunTargetState.PENDING
    assert resumed.targets[0].last_lease_id is None

    stop_payload = {"run_id": resumed.run_id}
    stop = ipc_client.submit_command(
        RunCommandName.STOP_RUN_AFTER_ACTIVE_FILE.value,
        request_id="22222222-3333-4444-8555-666666666666",
        idempotency_key="88888888-7777-4666-8555-444444444444",
        payload=stop_payload,
        payload_hash=payload_hash(stop_payload),
    )

    assert stop.status is IpcStatus.ACCEPTED
    assert stop.payload["run"]["state"] == RunState.QUEUED.value
    assert runs.stop_requests == {resumed.run_id}


def test_enabled_start_run_replay_returns_existing_success_receipt() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    runs = _InMemoryRunStore()
    id_factory = _FixedRunIdFactory()
    service = _service(mutations_enabled=True)
    service.plan_store = _InMemoryPlanStore(plan)
    service.run_store = runs
    service.run_id_factory = id_factory
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        payload_hash=payload_hash(
            {"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum}
        ),
    )
    second = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        payload_hash=payload_hash(
            {"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum}
        ),
    )

    assert first.status is IpcStatus.ACCEPTED
    assert second.status is IpcStatus.ACCEPTED
    assert len(receipts.receipts) == 1
    assert len(runs.runs) == 1
    assert id_factory.calls == 1
    assert second.payload["created"] is False
    assert second.payload["idempotent_replay"] is True
    assert second.payload["receipt"]["request_id"] == REQUEST_ID_A
    assert second.payload["run"]["run_id"] == "run-a"


def test_enabled_start_run_rejects_checksum_mismatch_before_effect() -> None:
    plan = _sealed_plan()
    receipts = _InMemoryCommandReceiptStore()
    runs = _InMemoryRunStore()
    id_factory = _FixedRunIdFactory()
    service = _service(mutations_enabled=True)
    service.plan_store = _InMemoryPlanStore(plan)
    service.run_store = runs
    service.run_id_factory = id_factory
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": "a" * 64},
        payload_hash=payload_hash({"plan_id": plan.plan_id, "plan_checksum": "a" * 64}),
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert response.payload["readiness"]["validation_codes"] == ["PLAN_CHECKSUM_MISMATCH"]
    assert response.payload["receipt"]["state"] == CommandReceiptState.REJECTED.value
    assert receipt is not None
    assert receipt.rejection_reason == IpcReason.COMMAND_PRECONDITION_FAILED.value
    assert runs.runs == {}
    assert id_factory.calls == 0


def test_enabled_start_run_requires_dispatcher_dependencies() -> None:
    plan = _sealed_plan()
    service = _service(mutations_enabled=True)
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        payload_hash=payload_hash(
            {"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum}
        ),
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED
    assert response.payload["recognized"] is True
    assert response.payload["mutations_enabled"] is True


def test_command_receipt_replay_returns_existing_terminal_receipt() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    first = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )
    second = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    assert len(receipts.receipts) == 1
    assert first.payload["receipt"] == second.payload["receipt"]
    assert second.payload["receipt"]["request_id"] == REQUEST_ID_A


def test_command_receipt_conflict_rejects_same_key_with_different_payload_hash() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()
    ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_A,
    )

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-b"},
        payload_hash=PAYLOAD_HASH_B,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_IDEMPOTENCY_CONFLICT
    assert response.payload["idempotency_key"] == IDEMPOTENCY_KEY_A
    assert response.payload["conflict"] == "COMMAND_IDEMPOTENCY_CONFLICT:payload_hash"
    assert len(receipts.receipts) == 1


def test_command_rejects_declared_payload_hash_mismatch_before_receipt() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_B,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME
    assert receipts.receipts == {}


def test_invalid_create_standard_backup_payload_does_not_record_receipt() -> None:
    receipts = _InMemoryCommandReceiptStore()
    service = _service()
    service.command_receipt_store = receipts
    ipc_client = _client(service=service)
    ipc_client.connect()

    response = ipc_client.submit_command(
        JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
        request_id=REQUEST_ID_A,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={},
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME
    assert receipts.receipts == {}


def test_client_requires_payload_hash_for_non_empty_command_payload() -> None:
    ipc_client = _client()
    ipc_client.connect()

    with pytest.raises(IpcProtocolError, match="payload_hash is required"):
        ipc_client.submit_command(
            JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            payload={"draft_id": "draft-a"},
        )


def test_query_requires_prior_handshake() -> None:
    response = _client().query_status()

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED
    assert response.request_id is not None


def test_in_process_client_correlates_each_request_with_a_distinct_uuid() -> None:
    client = _client()

    handshake = client.connect()
    status = client.query_status()

    assert handshake.request_id is not None
    assert status.request_id is not None
    assert handshake.request_id != status.request_id


@pytest.mark.parametrize(
    "response_payload",
    [
        {
            "status": "ACCEPTED",
            "reason": None,
            "payload": {},
        },
        {
            "status": "ACCEPTED",
            "reason": None,
            "payload": {},
            "request_id": REQUEST_ID_B,
        },
    ],
)
def test_response_parser_rejects_missing_or_mismatched_request_correlation(
    response_payload: dict[str, object],
) -> None:
    with pytest.raises(IpcProtocolError, match="request_id"):
        IpcResponse.from_dict(
            response_payload,
            expected_request_id=REQUEST_ID_A,
        )


def test_response_parser_allows_only_uncorrelated_handshake_version_rejection() -> None:
    response = IpcResponse.from_dict(
        {
            "status": "REJECTED",
            "reason": "SCHEMA_MISMATCH",
            "payload": {},
        },
        expected_request_id=REQUEST_ID_A,
        allow_uncorrelated_version_rejection=True,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.SCHEMA_MISMATCH
    assert response.request_id is None


def test_command_envelope_requires_versioned_hash_metadata() -> None:
    command = IpcCommandEnvelope.from_dict(
        {
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": COMMAND_SCHEMA_VERSION,
            "message_type": "COMMAND",
            "request_id": "44444444-4444-4444-8444-444444444444",
            "client_instance_id": "55555555-5555-4555-8555-555555555555",
            "idempotency_key": IDEMPOTENCY_KEY_A,
            "command_name": JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value,
            "payload": {"draft_id": "draft-a"},
            "payload_hash_scope": "PAYLOAD_ONLY",
            "payload_canonicalization_algorithm": "JCS-RFC8785",
            "payload_hash_algorithm": "BLAKE3-256",
            "payload_hash": PAYLOAD_HASH_A,
        }
    )

    assert command.command_name == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value
    assert command.payload == {"draft_id": "draft-a"}


def test_frame_codec_enforces_json_object_and_size_limit() -> None:
    payload = {"message_type": "HANDSHAKE", "content": "ok"}

    assert decode_frame(encode_frame(payload)) == payload
    with pytest.raises(IpcProtocolError, match="IPC frame must be a JSON object"):
        decode_frame(b"[]")
    with pytest.raises(IpcProtocolError, match="frame exceeds limit"):
        encode_frame({"payload": "x" * MAX_FRAME_BYTES})


def _sealed_plan(
    *,
    plan_id: str = "plan-a",
    operation_id: str = "op-copy",
) -> SealedPlan:
    return seal_plan(
        plan_id=plan_id,
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id=operation_id,
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_json(),
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _schedule(
    plan: SealedPlan,
    *,
    desired_definition_hash: str = "a" * 64,
) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id=plan.job_id,
        plan_id=plan.plan_id,
        plan_checksum=plan.plan_checksum,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash=desired_definition_hash,
    )


def _sealed_plan_for_operation_pages() -> SealedPlan:
    return seal_plan(
        plan_id="plan-page",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-b",
                operation_type=PlanOperationType.SKIP_IDENTICAL,
                sequence_no=20,
                execution_phase=20,
                stable_order_key="020:Pictures/B.jpg",
                target_precondition_kind=TargetPreconditionKind.NONE,
                target_relative_path="Pictures/B.jpg",
                planned_bytes=0,
                reason_code="SKIP_IDENTICAL",
                risk_level=PlanRiskLevel.LOW,
            ),
            PlanOperation(
                operation_id="op-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_json(),
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _sealed_plan_for_endpoint_pages() -> SealedPlan:
    return seal_plan(
        plan_id="plan-endpoints",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(), _source_endpoint()),
        operations=(
            PlanOperation(
                operation_id="op-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=10,
                stable_order_key="010:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                source_relative_path="Pictures/A.jpg",
                source_precondition_json=source_precondition_json(),
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _plan_endpoint_read_model(endpoint: PlanEndpoint) -> PlanEndpointReadModel:
    return PlanEndpointReadModel(
        endpoint_id=endpoint.endpoint_id,
        endpoint_revision_id=endpoint.endpoint_revision_id,
        snapshot_id=endpoint.snapshot_id,
        role=endpoint.role,
        target_ordinal=endpoint.target_ordinal,
        capabilities_hash=endpoint.capabilities_hash,
        root_case_context_hash=endpoint.root_case_context_hash,
        endpoint_generation=endpoint.endpoint_generation,
        required_owner_installation_id=endpoint.required_owner_installation_id,
        required_ownership_epoch=endpoint.required_ownership_epoch,
        control_schema_version=endpoint.control_schema_version,
        planned_operations=endpoint.planned_operations,
        planned_bytes=endpoint.planned_bytes,
    )


def _plan_operation_read_model(operation: PlanOperation) -> PlanOperationReadModel:
    return PlanOperationReadModel(
        operation_id=operation.operation_id,
        operation_type=operation.operation_type,
        sequence_no=operation.sequence_no,
        execution_phase=operation.execution_phase,
        stable_order_key=operation.stable_order_key,
        target_precondition_kind=operation.target_precondition_kind,
        reason_code=operation.reason_code,
        risk_level=operation.risk_level,
        target_endpoint_id=operation.target_endpoint_id,
        target_relative_path=operation.target_relative_path,
        planned_bytes=operation.planned_bytes,
    )


def _endpoint_after_cursor(
    endpoint: PlanEndpointReadModel,
    cursor: PlanEndpointCursor,
) -> bool:
    return (
        endpoint.role.value,
        -1 if endpoint.target_ordinal is None else endpoint.target_ordinal,
        endpoint.endpoint_id,
    ) > (
        cursor.role.value,
        -1 if cursor.target_ordinal is None else cursor.target_ordinal,
        cursor.endpoint_id,
    )


def _plan_endpoint_cursor(endpoint: PlanEndpointReadModel) -> PlanEndpointCursor:
    return PlanEndpointCursor(
        role=endpoint.role,
        target_ordinal=endpoint.target_ordinal,
        endpoint_id=endpoint.endpoint_id,
    )


def _operation_after_cursor(
    operation: PlanOperationReadModel,
    cursor: PlanOperationCursor,
) -> bool:
    return (
        operation.execution_phase,
        operation.stable_order_key,
        operation.operation_id,
    ) > (
        cursor.execution_phase,
        cursor.stable_order_key,
        cursor.operation_id,
    )


def _plan_operation_cursor(operation: PlanOperationReadModel) -> PlanOperationCursor:
    return PlanOperationCursor(
        execution_phase=operation.execution_phase,
        stable_order_key=operation.stable_order_key,
        operation_id=operation.operation_id,
    )


def _snapshot_entry(
    entry_id: str,
    relative_path: str,
    comparison_key: str,
    case_collision_group_id: str | None,
) -> SnapshotEntryReadModel:
    return SnapshotEntryReadModel(
        entry_id=entry_id,
        relative_path=relative_path,
        comparison_key=comparison_key,
        object_type="file",
        size_bytes=128,
        birthtime_ns=1_000,
        case_collision_group_id=case_collision_group_id,
    )


def _snapshot_entry_after_cursor(
    entry: SnapshotEntryReadModel,
    cursor: SnapshotEntryCursor,
) -> bool:
    return (
        entry.comparison_key,
        entry.relative_path,
        entry.entry_id,
    ) > (
        cursor.comparison_key,
        cursor.relative_path,
        cursor.entry_id,
    )


def _snapshot_entry_cursor(entry: SnapshotEntryReadModel) -> SnapshotEntryCursor:
    return SnapshotEntryCursor(
        comparison_key=entry.comparison_key,
        relative_path=entry.relative_path,
        entry_id=entry.entry_id,
    )


def _snapshot_coverage(
    relative_path: str,
    comparison_key: str,
    coverage_state: str,
) -> SnapshotCoverageReadModel:
    return SnapshotCoverageReadModel(
        relative_path=relative_path,
        comparison_key=comparison_key,
        coverage_state=coverage_state,
        case_mode="CASE_INSENSITIVE",
        case_mode_evidence="probe-ok",
        case_context_hash="a" * 64,
    )


def _snapshot_coverage_after_cursor(
    coverage: SnapshotCoverageReadModel,
    cursor: SnapshotCoverageCursor,
) -> bool:
    return (
        coverage.comparison_key,
        coverage.relative_path,
    ) > (
        cursor.comparison_key,
        cursor.relative_path,
    )


def _snapshot_coverage_cursor(coverage: SnapshotCoverageReadModel) -> SnapshotCoverageCursor:
    return SnapshotCoverageCursor(
        comparison_key=coverage.comparison_key,
        relative_path=coverage.relative_path,
    )


def _snapshot_issue(
    issue_id: int,
    relative_path: str,
    *,
    blocking: bool,
) -> SnapshotIssueReadModel:
    return SnapshotIssueReadModel(
        issue_id=issue_id,
        relative_path=relative_path,
        issue_type="UNREADABLE_DIRECTORY",
        blocks_destructive_actions=blocking,
        error_code="ERROR_ACCESS_DENIED",
        sanitized_message="access denied",
    )


def _snapshot_issue_after_cursor(
    issue: SnapshotIssueReadModel,
    cursor: SnapshotIssueCursor,
) -> bool:
    return (
        issue.relative_path,
        issue.issue_type,
        issue.issue_id,
    ) > (
        cursor.relative_path,
        cursor.issue_type,
        cursor.issue_id,
    )


def _snapshot_issue_cursor(issue: SnapshotIssueReadModel) -> SnapshotIssueCursor:
    return SnapshotIssueCursor(
        relative_path=issue.relative_path,
        issue_type=issue.issue_type,
        issue_id=issue.issue_id,
    )


def _snapshot_filter_decision(
    decision_id: int,
    relative_path: str,
) -> SnapshotFilterDecisionReadModel:
    return SnapshotFilterDecisionReadModel(
        decision_id=decision_id,
        relative_path=relative_path,
        object_type="file",
        decision_state="EXCLUDED",
        reason_code="FILTER_RULE_EXCLUDED",
        matched_rule_id="default-safe-rule",
        evaluation_stage="PRE_METADATA",
    )


def _target_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        snapshot_id="target-snapshot-a",
        role=PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        endpoint_generation=1,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )


def _source_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="source-a",
        endpoint_revision_id="source-rev-a",
        snapshot_id="source-snapshot-a",
        role=PlanEndpointRole.SOURCE,
        capabilities_hash="capabilities-source",
        root_case_context_hash="case-source",
        endpoint_generation=1,
    )
