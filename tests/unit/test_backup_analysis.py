from __future__ import annotations

from dataclasses import replace

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
    clock = iter(
        (
            "2026-07-31T10:00:00Z",
            "2026-07-31T10:00:01Z",
            "2026-07-31T10:00:02Z",
            "2026-07-31T10:00:03Z",
        )
    )

    completed = execute_next_backup_analysis(
        requests=requests,
        runs=_Runs(),
        refresh_endpoint_classifications=lambda: None,
        snapshots=snapshots,
        plans=plans,
        utc_now=lambda: next(clock),
    )

    assert completed is not None
    assert completed.state is BackupAnalysisRequestState.SUCCEEDED
    assert completed.analysis_id == "analysis-new"
    assert completed.plan_id == "plan-new"
    assert snapshots.calls == [("job-a", True)]
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


class _RequestStore:
    def __init__(self) -> None:
        self.request = BackupAnalysisRequest(
            request_id="request-a",
            command_idempotency_key="key-a",
            job_id="job-a",
            job_revision_id="revision-a",
            state=BackupAnalysisRequestState.QUEUED,
            requested_utc="2026-07-31T09:59:00Z",
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
        )
        return self.request

    def requeue_interrupted_backup_analyses(self) -> int:
        return 0


class _Runs:
    def __init__(self, *, active: bool = False) -> None:
        self.active = active

    def load_active_run_for_job(self, job_id: str) -> object | None:
        assert job_id == "job-a"
        return object() if self.active else None


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
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool]] = []

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
                    plan_checksum="a" * 64,
                    state="SEALED",
                    reason_code="INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
                    operation_count=2,
                    planned_bytes=100,
                    plan_runnable=True,
                    idempotent_replay=False,
                    next_action="Review.",
                ),
            ),
        )
