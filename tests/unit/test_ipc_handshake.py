from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptState,
    CommandReceiptStore,
    ensure_idempotency_compatible,
)
from mediasync_home.application.job_creation import (
    JobCreationCommandName,
    SealedStandardBackupJob,
    StandardBackupJobCatalog,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
)
from mediasync_home.application.job_drafts import JobDraftStore, StandardBackupJobDraft
from mediasync_home.application.plans import (
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    PlanStore,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.runs import (
    RunCommandName,
    RunIdFactory,
    RunIds,
    RunState,
    RunStore,
    StartedRun,
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

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None:
        job_id = self.idempotency_keys.get(idempotency_key)
        if job_id is None:
            return None
        return self.jobs[job_id]


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


class _InMemoryPlanStore(PlanStore):
    def __init__(self, plan: SealedPlan | None = None) -> None:
        self.plan = plan

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        self.plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        if self.plan is not None and self.plan.plan_id == plan_id:
            return self.plan
        return None


class _InMemoryRunStore(RunStore):
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


def test_gui_client_handshake_and_status_query_succeed() -> None:
    ipc_client = _client()
    gui_client = EngineClient(ipc_client)

    handshake = gui_client.connect()
    status = gui_client.get_status()

    assert handshake.status is IpcStatus.ACCEPTED
    assert handshake.reason is None
    assert handshake.payload["verified_user_sid_hash"] == EXPECTED_USER
    assert status.status is IpcStatus.ACCEPTED
    assert status.payload["host_status"]["role"] == ProcessRole.ENGINE_HOST.value
    assert status.payload["host_status"]["mutations_enabled"] is False


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
