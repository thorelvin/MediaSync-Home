from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Callable
from typing import Protocol

from mediasync_home.application.hash_evidence import (
    CurrentReadHashEvidenceRefresher,
)
from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanRefresher,
)
from mediasync_home.application.plans import (
    MUTATING_OPERATION_TYPES,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    PlanStore,
    TargetPreconditionKind,
)
from mediasync_home.application.runs import (
    RunIdFactory,
    RunStore,
    StartRunCommand,
    StartedRun,
    start_run_from_sealed_plan,
)
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
    start_when_safe: bool = False


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
    start_when_safe: bool = False
    started_run_id: str | None = None
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
            "start_when_safe": self.start_when_safe,
            "started_run_id": self.started_run_id,
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
        started_run_id: str | None,
    ) -> BackupAnalysisRequest: ...

    def requeue_interrupted_backup_analyses(self) -> int: ...


def parse_check_backup_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> CheckBackupCommand:
    if not set(payload).issubset({"job_id", "start_when_safe"}) or "job_id" not in payload:
        raise BackupAnalysisPayloadError("CHECK_BACKUP_PAYLOAD_INVALID")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
        raise BackupAnalysisPayloadError("CHECK_BACKUP_JOB_ID_INVALID")
    start_when_safe = payload.get("start_when_safe", False)
    if not isinstance(start_when_safe, bool):
        raise BackupAnalysisPayloadError("CHECK_BACKUP_START_POLICY_INVALID")
    return CheckBackupCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        start_when_safe=start_when_safe,
    )


def execute_next_backup_analysis(
    *,
    requests: BackupAnalysisRequestStore,
    runs: RunStore,
    refresh_endpoint_classifications: Callable[[], object],
    snapshots: JobSnapshotMaterializationRefresher,
    plans: InitialBackupPlanRefresher,
    utc_now: Callable[[], str],
    hash_evidence: CurrentReadHashEvidenceRefresher | None = None,
    plan_store: PlanStore | None = None,
    run_id_factory: RunIdFactory | None = None,
) -> BackupAnalysisRequest | None:
    request = requests.claim_next_backup_analysis(started_utc=utc_now())
    if request is None:
        return None
    active = runs.load_active_run_for_job(request.job_id)
    if (
        isinstance(active, StartedRun)
        and request.start_when_safe
        and active.command_request_id == request.request_id
    ):
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.SUCCEEDED,
            reason_code="BACKUP_ANALYSIS_SAFE_RUN_QUEUED",
            started_run_id=active.run_id,
            utc_now=utc_now,
        )
    if active is not None:
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
        if snapshot_result.job_revision_id != request.job_revision_id:
            return _complete_request(
                requests,
                request=request,
                state=BackupAnalysisRequestState.BLOCKED,
                reason_code="BACKUP_ANALYSIS_JOB_REVISION_CHANGED",
                analysis_id=snapshot_result.analysis_id,
                utc_now=utc_now,
            )
        if hash_evidence is not None:
            if snapshot_result.analysis_id is None:
                return _complete_request(
                    requests,
                    request=request,
                    state=BackupAnalysisRequestState.FAILED,
                    reason_code="BACKUP_ANALYSIS_HASH_ANALYSIS_MISSING",
                    utc_now=utc_now,
                )
            hash_report = hash_evidence.refresh_current_read_hash_evidence(
                analysis_id=snapshot_result.analysis_id,
                observed_utc=utc_now(),
            )
            if not hash_report.ready:
                return _complete_request(
                    requests,
                    request=request,
                    state=BackupAnalysisRequestState.BLOCKED,
                    reason_code=hash_report.reason_code,
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
        if (
            state is BackupAnalysisRequestState.SUCCEEDED
            and request.start_when_safe
        ):
            return _complete_after_safe_start(
                requests,
                request=request,
                plan_id=plan_result.plan_id,
                plan_checksum=plan_result.plan_checksum,
                analysis_id=plan_result.analysis_id,
                operation_count=plan_result.operation_count,
                planned_bytes=plan_result.planned_bytes,
                fallback_reason_code=plan_result.reason_code,
                plans=plan_store,
                runs=runs,
                run_id_factory=run_id_factory,
                utc_now=utc_now,
            )
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
    except Exception as exc:
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.FAILED,
            reason_code=_sanitized_failure_reason(exc),
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
    started_run_id: str | None = None,
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
        started_run_id=started_run_id,
    )


def _complete_after_safe_start(
    requests: BackupAnalysisRequestStore,
    *,
    request: BackupAnalysisRequest,
    plan_id: str | None,
    plan_checksum: str | None,
    analysis_id: str | None,
    operation_count: int,
    planned_bytes: int,
    fallback_reason_code: str,
    plans: PlanStore | None,
    runs: RunStore,
    run_id_factory: RunIdFactory | None,
    utc_now: Callable[[], str],
) -> BackupAnalysisRequest:
    if (
        plan_id is None
        or plan_checksum is None
        or plans is None
        or run_id_factory is None
    ):
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.FAILED,
            reason_code="BACKUP_ANALYSIS_SAFE_RUN_NOT_CONFIGURED",
            analysis_id=analysis_id,
            plan_id=plan_id,
            operation_count=operation_count,
            planned_bytes=planned_bytes,
            utc_now=utc_now,
        )
    plan = plans.load_sealed_plan(plan_id)
    if plan is None or plan.plan_checksum != plan_checksum:
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.FAILED,
            reason_code="BACKUP_ANALYSIS_SAFE_PLAN_UNAVAILABLE",
            analysis_id=analysis_id,
            plan_id=plan_id,
            operation_count=operation_count,
            planned_bytes=planned_bytes,
            utc_now=utc_now,
        )
    if not _safe_for_automatic_start(plan):
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.SUCCEEDED,
            reason_code=fallback_reason_code,
            analysis_id=analysis_id,
            plan_id=plan_id,
            operation_count=operation_count,
            planned_bytes=planned_bytes,
            utc_now=utc_now,
        )
    outcome = start_run_from_sealed_plan(
        command=StartRunCommand(
            request_id=request.request_id,
            idempotency_key=request.command_idempotency_key,
            run_idempotency_key=f"backup-analysis-run:{request.request_id}",
            plan_id=plan_id,
            plan_checksum=plan_checksum,
        ),
        plans=plans,
        runs=runs,
        id_factory=run_id_factory,
    )
    if (
        outcome.run is None
        or not outcome.readiness.plan_runnable
        or outcome.run.command_request_id != request.request_id
    ):
        return _complete_request(
            requests,
            request=request,
            state=BackupAnalysisRequestState.BLOCKED,
            reason_code="BACKUP_ANALYSIS_SAFE_RUN_START_FAILED",
            analysis_id=analysis_id,
            plan_id=plan_id,
            operation_count=operation_count,
            planned_bytes=planned_bytes,
            utc_now=utc_now,
        )
    return _complete_request(
        requests,
        request=request,
        state=BackupAnalysisRequestState.SUCCEEDED,
        reason_code="BACKUP_ANALYSIS_SAFE_RUN_QUEUED",
        analysis_id=analysis_id,
        plan_id=plan_id,
        operation_count=operation_count,
        planned_bytes=planned_bytes,
        started_run_id=outcome.run.run_id,
        utc_now=utc_now,
    )


def _safe_for_automatic_start(plan: SealedPlan) -> bool:
    mutating = tuple(
        operation
        for operation in plan.operations
        if operation.operation_type in MUTATING_OPERATION_TYPES
    )
    return (
        bool(mutating)
        and plan.risk_summary.get("highest") == PlanRiskLevel.LOW.value
        and all(
            operation.risk_level is PlanRiskLevel.LOW
            and (
                operation.operation_type is PlanOperationType.CREATE_DIRECTORY
                or (
                    operation.operation_type is PlanOperationType.COPY_NEW
                    and operation.target_precondition_kind
                    is TargetPreconditionKind.ABSENT
                    and operation.reason_code == "COPY_NEW"
                )
            )
            for operation in mutating
        )
        and len(mutating) == len(plan.operations)
    )


def _sanitized_failure_reason(error: Exception) -> str:
    candidate = getattr(error, "validation_code", None)
    if not isinstance(candidate, str):
        candidate = str(error)
    if (
        candidate
        and len(candidate) <= 128
        and all(character.isupper() or character.isdigit() or character == "_" for character in candidate)
    ):
        return candidate
    return "BACKUP_ANALYSIS_EXECUTION_FAILED"
