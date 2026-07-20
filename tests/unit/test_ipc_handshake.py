from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.activity_read_models import (
    RunActivityReadModelStore,
    RunActivitySummary,
    RunTargetActivitySummary,
)
from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptState,
    CommandReceiptStore,
    ensure_idempotency_compatible,
)
from mediasync_home.application.job_creation import (
    JobCreationCommandName,
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
    StandardBackupJobCatalog,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
)
from mediasync_home.application.job_drafts import JobDraftStore, StandardBackupJobDraft
from mediasync_home.application.job_read_models import (
    StandardBackupJobSummary,
    StandardBackupTargetSummary,
)
from mediasync_home.application.plans import (
    PlanEndpoint,
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
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
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
PAYLOAD_HASH_B = "a" * 64


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


class _InMemoryPlanStore(PlanStore, PlanOperationReadModelStore):
    def __init__(self, plan: SealedPlan | None = None) -> None:
        self.plan = plan

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self.plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        if self.plan is not None and self.plan.plan_id == plan_id:
            return self.plan
        return None

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
        )


class _InMemoryRunStore(RunStore, RunActivityReadModelStore):
    def __init__(self) -> None:
        self.runs: dict[str, StartedRun] = {}
        self.idempotency_keys: dict[str, str] = {}

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
    activity = gui_client.get_activity_overview()
    plan_operations = gui_client.get_plan_operations(plan_id="plan-a")

    assert handshake.status is IpcStatus.ACCEPTED
    assert handshake.reason is None
    assert handshake.payload["verified_user_sid_hash"] == EXPECTED_USER
    assert status.status is IpcStatus.ACCEPTED
    assert status.payload["host_status"]["role"] == ProcessRole.ENGINE_HOST.value
    assert status.payload["host_status"]["mutations_enabled"] is False
    assert overview.status is IpcStatus.ACCEPTED
    assert overview.payload["backup_overview"]["read_model_available"] is False
    assert activity.status is IpcStatus.ACCEPTED
    assert activity.payload["activity_overview"]["read_model_available"] is False
    assert plan_operations.status is IpcStatus.ACCEPTED
    assert plan_operations.payload["plan_operations"]["read_model_available"] is False


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
    assert second.status is IpcStatus.ACCEPTED
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert [operation["operation_id"] for operation in second_page["operations"]] == ["op-b"]


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
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert catalog.load_standard_backup_job("job-a") is not None
    assert id_factory.calls == 1


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
    assert len(receipts.receipts) == 1
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
        payload_hash=PAYLOAD_HASH_A,
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
        payload_hash=PAYLOAD_HASH_A,
    )

    receipt = receipts.load_command_receipt(IDEMPOTENCY_KEY_A)
    run = runs.load_started_run("run-a")
    assert response.status is IpcStatus.ACCEPTED
    assert response.reason is None
    assert response.payload["created"] is True
    assert response.payload["idempotent_replay"] is False
    assert response.payload["run"] == {
        "run_id": "run-a",
        "job_id": "job-a",
        "job_revision_id": "job-rev-a",
        "plan_id": "plan-a",
        "state": RunState.QUEUED.value,
        "plan_checksum": plan.plan_checksum,
        "planned_operations": 1,
        "planned_bytes": 128,
    }
    assert response.payload["receipt"]["state"] == CommandReceiptState.SUCCEEDED.value
    assert response.payload["receipt"]["result_entity_type"] == "run"
    assert response.payload["receipt"]["result_entity_id"] == "run-a"
    assert receipt is not None
    assert receipt.state is CommandReceiptState.SUCCEEDED
    assert run is not None
    assert run.state is RunState.QUEUED
    assert id_factory.calls == 1


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
        payload_hash=PAYLOAD_HASH_A,
    )
    second = ipc_client.submit_command(
        RunCommandName.START_RUN.value,
        request_id=REQUEST_ID_B,
        idempotency_key=IDEMPOTENCY_KEY_A,
        payload={"plan_id": plan.plan_id, "plan_checksum": plan.plan_checksum},
        payload_hash=PAYLOAD_HASH_A,
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
        payload_hash=PAYLOAD_HASH_A,
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
        payload_hash=PAYLOAD_HASH_A,
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
        payload={"draft_id": "draft-a"},
        payload_hash=PAYLOAD_HASH_B,
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.COMMAND_IDEMPOTENCY_CONFLICT
    assert response.payload["idempotency_key"] == IDEMPOTENCY_KEY_A
    assert response.payload["conflict"] == "COMMAND_IDEMPOTENCY_CONFLICT:payload_hash"
    assert len(receipts.receipts) == 1


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


def test_command_envelope_requires_versioned_hash_metadata() -> None:
    command = IpcCommandEnvelope.from_dict(
        {
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": SCHEMA_VERSION,
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


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-copy",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
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
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
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
        target_relative_path=operation.target_relative_path,
        planned_bytes=operation.planned_bytes,
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


def _target_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        snapshot_id="target-snapshot-a",
        role=PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )
