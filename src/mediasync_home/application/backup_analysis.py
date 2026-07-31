from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Callable
from typing import Protocol

from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanRefresher,
)
from mediasync_home.application.runs import RunStore
from mediasync_home.application.snapshot_scanning import (
    JobSnapshotMaterializationRefresher,
)


class BackupAnalysisCommandName(str, Enum):
    CHECK_BACKUP = "CHECK_BACKUP"


class BackupAnalysisPayloadError(ValueError):
    pass


class BackupAnalysisRequestState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    NO_CHANGES = "NO_CHANGES"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


TERMINAL_BACKUP_ANALYSIS_STATES = frozenset(
    {
        BackupAnalysisRequestState.SUCCEEDED,
        BackupAnalysisRequestState.NO_CHANGES,
        BackupAnalysisRequestState.BLOCKED,
        BackupAnalysisRequestState.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class CheckBackupCommand:
    request_id: str
    idempotency_key: str
    job_id: str


@dataclass(frozen=True, slots=True)
class BackupAnalysisRequest:
    request_id: str
    command_idempotency_key: str
    job_id: str
    job_revision_id: str
    state: BackupAnalysisRequestState
    requested_utc: str
    started_utc: str | None = None
    completed_utc: str | None = None
    analysis_id: str | None = None
    plan_id: str | None = None
    reason_code: str | None = None
    operation_count: int = 0
    planned_bytes: int = 0
    row_version: int = 1

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_BACKUP_ANALYSIS_STATES

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "state": self.state.value,
            "requested_utc": self.requested_utc,
            "started_utc": self.started_utc,
            "completed_utc": self.completed_utc,
            "analysis_id": self.analysis_id,
            "plan_id": self.plan_id,
            "reason_code": self.reason_code,
            "operation_count": self.operation_count,
            "planned_bytes": self.planned_bytes,
            "row_version": self.row_version,
        }


class BackupAnalysisRequestStore(Protocol):
    def enqueue_backup_analysis(
        self,
        request: BackupAnalysisRequest,
    ) -> BackupAnalysisRequest: ...

    def load_backup_analysis_request(
        self,
        request_id: str,
    ) -> BackupAnalysisRequest | None: ...

    def claim_next_backup_analysis(
        self,
        *,
        started_utc: str,
    ) -> BackupAnalysisRequest | None: ...

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
    ) -> BackupAnalysisRequest: ...

    def requeue_interrupted_backup_analyses(self) -> int: ...


def parse_check_backup_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> CheckBackupCommand:
    if set(payload) != {"job_id"}:
        raise BackupAnalysisPayloadError("CHECK_BACKUP_PAYLOAD_INVALID")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
        raise BackupAnalysisPayloadError("CHECK_BACKUP_JOB_ID_INVALID")
    return CheckBackupCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
    )


def execute_next_backup_analysis(
    *,
    requests: BackupAnalysisRequestStore,
    runs: RunStore,
    refresh_endpoint_classifications: Callable[[], object],
    snapshots: JobSnapshotMaterializationRefresher,
    plans: InitialBackupPlanRefresher,
    utc_now: Callable[[], str],
) -> BackupAnalysisRequest | None:
    request = requests.claim_next_backup_analysis(started_utc=utc_now())
    if request is None:
        return None
    if runs.load_active_run_for_job(request.job_id) is not None:
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.BLOCKED,
            reason_code="BACKUP_ANALYSIS_ACTIVE_RUN",
            utc_now=utc_now,
        )
    try:
        refresh_endpoint_classifications()
        snapshot_report = snapshots.refresh_job_snapshots(
            observed_utc=utc_now(),
            job_id=request.job_id,
            force=True,
        )
        snapshot_result = next(
            (
                result
                for result in snapshot_report.results
                if result.job_id == request.job_id
            ),
            None,
        )
        if snapshot_result is None:
            return _complete_request(
                requests,
                request=request,
                state=BackupAnalysisRequestState.FAILED,
                reason_code="BACKUP_ANALYSIS_JOB_NOT_FOUND",
                utc_now=utc_now,
            )
        if snapshot_result.state != "SEALED":
            return _complete_request(
                requests,
                request=request,
                state=(
                    BackupAnalysisRequestState.BLOCKED
                    if snapshot_result.state == "BLOCKED"
                    else BackupAnalysisRequestState.FAILED
                ),
                reason_code=snapshot_result.reason_code,
                analysis_id=snapshot_result.analysis_id,
                utc_now=utc_now,
            )
        plan_report = plans.refresh_initial_backup_plans(
            observed_utc=utc_now(),
            job_id=request.job_id,
            force=True,
        )
        plan_result = next(
            (
                result
                for result in plan_report.results
                if result.job_id == request.job_id
            ),
            None,
        )
        if plan_result is None:
            return _complete_request(
                requests,
                request=request,
                state=BackupAnalysisRequestState.FAILED,
                reason_code="BACKUP_ANALYSIS_PLAN_RESULT_MISSING",
                analysis_id=snapshot_result.analysis_id,
                utc_now=utc_now,
            )
        state = {
            "SEALED": BackupAnalysisRequestState.SUCCEEDED,
            "NO_CHANGES": BackupAnalysisRequestState.NO_CHANGES,
            "BLOCKED": BackupAnalysisRequestState.BLOCKED,
            "FAILED": BackupAnalysisRequestState.FAILED,
        }.get(plan_result.state, BackupAnalysisRequestState.FAILED)
        return _complete_request(
            requests,
            request=request,
            state=state,
            reason_code=plan_result.reason_code,
            analysis_id=plan_result.analysis_id,
            plan_id=plan_result.plan_id,
            operation_count=plan_result.operation_count,
            planned_bytes=plan_result.planned_bytes,
            utc_now=utc_now,
        )
    except Exception:
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.FAILED,
            reason_code="BACKUP_ANALYSIS_EXECUTION_FAILED",
            utc_now=utc_now,
        )


def _complete_request(
    requests: BackupAnalysisRequestStore,
    *,
    request: BackupAnalysisRequest,
    state: BackupAnalysisRequestState,
    reason_code: str,
    utc_now: Callable[[], str],
    analysis_id: str | None = None,
    plan_id: str | None = None,
    operation_count: int = 0,
    planned_bytes: int = 0,
) -> BackupAnalysisRequest:
    return requests.complete_backup_analysis(
        request_id=request.request_id,
        state=state,
        completed_utc=utc_now(),
        analysis_id=analysis_id,
        plan_id=plan_id,
        reason_code=reason_code,
        operation_count=operation_count,
        planned_bytes=planned_bytes,
    )
