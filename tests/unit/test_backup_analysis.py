from __future__ import annotations

from dataclasses import replace

from tests.support.source_preconditions import source_precondition_json

from mediasync_home.application.backup_analysis import (
    BackupAnalysisRequest,
    BackupAnalysisRequestState,
    execute_next_backup_analysis,
    parse_check_backup_command,
)
from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanMaterializationResult,
    InitialBackupPlanRefreshReport,
)
from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.runs import RunIds, StartedRun
from mediasync_home.application.hash_evidence import (
    CurrentReadHashRefreshReport,
    CurrentReadHashRefreshState,
)
from mediasync_home.application.snapshot_scanning import (
    JobSnapshotMaterializationResult,
    SnapshotMaterializationRefreshReport,
)


def test_parse_check_backup_command_requires_only_job_id() -> None:
    command = parse_check_backup_command(
        request_id="request-a",
        idempotency_key="key-a",
        payload={"job_id": "job-a"},
    )

    assert command.job_id == "job-a"


def test_execute_next_backup_analysis_forces_fresh_job_scans_and_plan() -> None:
    requests = _RequestStore()
    snapshots = _SnapshotRefresher()
    plans = _PlanRefresher()
    hashes = _HashRefresher()
    clock = iter(
        (
            "2026-07-31T10:00:00Z",
            "2026-07-31T10:00:01Z",
            "2026-07-31T10:00:02Z",
            "2026-07-31T10:00:03Z",
            "2026-07-31T10:00:04Z",
        )
    )

    completed = execute_next_backup_analysis(
        requests=requests,
        runs=_Runs(),
        refresh_endpoint_classifications=lambda: None,
        snapshots=snapshots,
        hash_evidence=hashes,
        plans=plans,
        utc_now=lambda: next(clock),
    )

    assert completed is not None
    assert completed.state is BackupAnalysisRequestState.SUCCEEDED
    assert completed.analysis_id == "analysis-new"
    assert completed.plan_id == "plan-new"
    assert snapshots.calls == [("job-a", True)]
    assert hashes.calls == ["analysis-new"]
    assert plans.calls == [("job-a", True)]


def test_execute_next_backup_analysis_blocks_while_job_has_active_run() -> None:
    requests = _RequestStore()

    completed = execute_next_backup_analysis(
        requests=requests,
        runs=_Runs(active=True),
        refresh_endpoint_classifications=lambda: None,
        snapshots=_SnapshotRefresher(),
        plans=_PlanRefresher(),
        utc_now=lambda: "2026-07-31T10:00:00Z",
    )

    assert completed is not None
    assert completed.state is BackupAnalysisRequestState.BLOCKED
    assert completed.reason_code == "BACKUP_ANALYSIS_ACTIVE_RUN"


def test_execute_next_backup_analysis_starts_fresh_low_risk_plan() -> None:
    requests = _RequestStore(start_when_safe=True)
    runs = _Runs()
    plan = _safe_plan()
    clock_values = iter(
        f"2026-07-31T10:00:0{second}Z" for second in range(8)
    )

    completed = execute_next_backup_analysis(
        requests=requests,
        runs=runs,
        refresh_endpoint_classifications=lambda: None,
        snapshots=_SnapshotRefresher(),
        hash_evidence=_HashRefresher(),
        plans=_PlanRefresher(
            plan_checksum=plan.plan_checksum,
            operation_count=plan.operation_count,
        ),
        plan_store=_PlanStore(plan),
        run_id_factory=_RunIds(),
        utc_now=lambda: next(clock_values),
    )

    assert completed is not None
    assert completed.state is BackupAnalysisRequestState.SUCCEEDED
    assert completed.reason_code == "BACKUP_ANALYSIS_SAFE_RUN_QUEUED"
    assert completed.started_run_id == "run-a"
    assert runs.started is not None
    assert runs.started.plan_id == "plan-new"


def test_execute_next_backup_analysis_stops_review_plan_before_run() -> None:
    requests = _RequestStore(start_when_safe=True)
    runs = _Runs()
    plan = _review_plan()
    clock_values = iter(
        f"2026-07-31T10:00:0{second}Z" for second in range(8)
    )

    completed = execute_next_backup_analysis(
        requests=requests,
        runs=runs,
        refresh_endpoint_classifications=lambda: None,
        snapshots=_SnapshotRefresher(),
        hash_evidence=_HashRefresher(),
        plans=_PlanRefresher(
            plan_checksum=plan.plan_checksum,
            operation_count=plan.operation_count,
        ),
        plan_store=_PlanStore(plan),
        run_id_factory=_RunIds(),
        utc_now=lambda: next(clock_values),
    )

    assert completed is not None
    assert completed.state is BackupAnalysisRequestState.SUCCEEDED
    assert completed.reason_code == "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW"
    assert completed.started_run_id is None
    assert runs.started is None


class _RequestStore:
    def __init__(self, *, start_when_safe: bool = False) -> None:
        self.request = BackupAnalysisRequest(
            request_id="request-a",
            command_idempotency_key="key-a",
            job_id="job-a",
            job_revision_id="revision-a",
            state=BackupAnalysisRequestState.QUEUED,
            requested_utc="2026-07-31T09:59:00Z",
            start_when_safe=start_when_safe,
        )

    def enqueue_backup_analysis(
        self,
        request: BackupAnalysisRequest,
    ) -> BackupAnalysisRequest:
        self.request = request
        return request

    def load_backup_analysis_request(
        self,
        request_id: str,
    ) -> BackupAnalysisRequest | None:
        return self.request if request_id == self.request.request_id else None

    def claim_next_backup_analysis(
        self,
        *,
        started_utc: str,
    ) -> BackupAnalysisRequest | None:
        if self.request.state is not BackupAnalysisRequestState.QUEUED:
            return None
        self.request = replace(
            self.request,
            state=BackupAnalysisRequestState.RUNNING,
            started_utc=started_utc,
        )
        return self.request

    def complete_backup_analysis(
        self,
        *,
        request_id: str,
        state: BackupAnalysisRequestState,
        completed_utc: str,
        analysis_id: str | None,
        plan_id: str | None,
        reason_code: str,
        operation_count: int,
        planned_bytes: int,
        started_run_id: str | None,
    ) -> BackupAnalysisRequest:
        assert request_id == self.request.request_id
        self.request = replace(
            self.request,
            state=state,
            completed_utc=completed_utc,
            analysis_id=analysis_id,
            plan_id=plan_id,
            reason_code=reason_code,
            operation_count=operation_count,
            planned_bytes=planned_bytes,
            started_run_id=started_run_id,
        )
        return self.request

    def requeue_interrupted_backup_analyses(self) -> int:
        return 0


class _Runs:
    def __init__(self, *, active: bool = False) -> None:
        self.active = active
        self.started: StartedRun | None = None

    def load_active_run_for_job(self, job_id: str) -> object | None:
        assert job_id == "job-a"
        return object() if self.active else self.started

    def load_started_run_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> StartedRun | None:
        if self.started is None or self.started.idempotency_key != idempotency_key:
            return None
        return self.started

    def save_started_run(self, run: StartedRun) -> None:
        self.started = run


class _SnapshotRefresher:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool]] = []

    def refresh_job_snapshots(
        self,
        *,
        observed_utc: str,
        job_id: str | None = None,
        force: bool = False,
    ) -> SnapshotMaterializationRefreshReport:
        assert observed_utc
        self.calls.append((job_id, force))
        return SnapshotMaterializationRefreshReport(
            scanned_job_count=1,
            reused_job_count=0,
            blocked_job_count=0,
            failed_job_count=0,
            sealed_snapshot_count=2,
            results=(
                JobSnapshotMaterializationResult(
                    job_id="job-a",
                    job_revision_id="revision-a",
                    analysis_id="analysis-new",
                    state="SEALED",
                    reason_code="JOB_SNAPSHOTS_SEALED",
                ),
            ),
        )


class _PlanRefresher:
    def __init__(
        self,
        *,
        plan_checksum: str = "a" * 64,
        operation_count: int = 2,
    ) -> None:
        self.calls: list[tuple[str | None, bool]] = []
        self.plan_checksum = plan_checksum
        self.operation_count = operation_count

    def refresh_initial_backup_plans(
        self,
        *,
        observed_utc: str,
        job_id: str | None = None,
        force: bool = False,
    ) -> InitialBackupPlanRefreshReport:
        assert observed_utc
        self.calls.append((job_id, force))
        return InitialBackupPlanRefreshReport(
            sealed_plan_count=1,
            reused_plan_count=0,
            no_changes_count=0,
            blocked_job_count=0,
            failed_job_count=0,
            results=(
                InitialBackupPlanMaterializationResult(
                    job_id="job-a",
                    job_revision_id="revision-a",
                    analysis_id="analysis-new",
                    plan_id="plan-new",
                    plan_checksum=self.plan_checksum,
                    state="SEALED",
                    reason_code="INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
                    operation_count=self.operation_count,
                    planned_bytes=100,
                    plan_runnable=True,
                    idempotent_replay=False,
                    next_action="Review.",
                ),
            ),
        )


class _HashRefresher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def refresh_current_read_hash_evidence(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> CurrentReadHashRefreshReport:
        assert observed_utc
        self.calls.append(analysis_id)
        return CurrentReadHashRefreshReport(
            analysis_id=analysis_id,
            state=CurrentReadHashRefreshState.READY,
            reason_code="CURRENT_READ_HASH_EVIDENCE_READY",
            candidate_pair_count=1,
            hashed_entry_count=2,
            reused_entry_count=0,
            identical_pair_count=1,
            changed_pair_count=0,
        )


class _PlanStore:
    def __init__(self, plan: SealedPlan) -> None:
        self.plan = plan

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        return self.plan if plan_id == "plan-new" else None


class _RunIds:
    def new_run_ids(self) -> RunIds:
        return RunIds(run_id="run-a", logical_run_group_id="group-a")


def _safe_plan() -> SealedPlan:
    return _plan(
        risk_level=PlanRiskLevel.LOW,
        target_precondition_kind=TargetPreconditionKind.ABSENT,
        reason_code="COPY_NEW",
    )


def _review_plan() -> SealedPlan:
    return _plan(
        risk_level=PlanRiskLevel.MEDIUM,
        target_precondition_kind=TargetPreconditionKind.MATCH_FINGERPRINT,
        reason_code="REPLACE_WITH_VERSION",
    )


def _plan(
    *,
    risk_level: PlanRiskLevel,
    target_precondition_kind: TargetPreconditionKind,
    reason_code: str,
) -> SealedPlan:
    return seal_plan(
        plan_id="plan-new",
        analysis_id="analysis-new",
        job_id="job-a",
        job_revision_id="revision-a",
        endpoints=(
            PlanEndpoint(
                endpoint_id="source-a",
                endpoint_revision_id="source-revision",
                endpoint_generation=1,
                snapshot_id="snapshot-source",
                role=PlanEndpointRole.SOURCE,
                capabilities_hash="1" * 64,
                root_case_context_hash="2" * 64,
            ),
            PlanEndpoint(
                endpoint_id="target-a",
                endpoint_revision_id="target-revision",
                endpoint_generation=1,
                snapshot_id="snapshot-target",
                role=PlanEndpointRole.TARGET_WRITABLE,
                capabilities_hash="3" * 64,
                root_case_context_hash="4" * 64,
                target_ordinal=1,
                required_owner_installation_id="owner-a",
                required_ownership_epoch=1,
                control_schema_version=1,
                planned_operations=1,
                planned_bytes=100,
            ),
        ),
        operations=(
            PlanOperation(
                operation_id="operation-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=1,
                execution_phase=20,
                stable_order_key="020:target-a:a.txt",
                target_precondition_kind=target_precondition_kind,
                reason_code=reason_code,
                risk_level=risk_level,
                target_endpoint_id="target-a",
                target_relative_path="A.txt",
                source_relative_path="A.txt",
                source_precondition_json=source_precondition_json(
                    relative_path="A.txt",
                    size_bytes=100,
                ),
                planned_bytes=100,
            ),
        ),
    )
