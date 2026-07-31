from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.runs import RunState, RunTargetState


PROGRESS_SNAPSHOT_SCHEMA_VERSION = 4
MAX_PROGRESS_SNAPSHOT_TARGETS = 32
MAX_PROGRESS_QUERY_ID_LENGTH = 256
MAX_PROGRESS_ACTIVE_PATH_LENGTH = 32767


class ProgressSnapshotQueryError(ValueError):
    pass


@dataclass(frozen=True)
class RunTargetProgressSnapshot:
    run_target_id: str
    endpoint_id: str
    endpoint_revision_id: str
    state: RunTargetState
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int
    endpoint_wait_attempts: int = 0
    endpoint_wait_total_backoff_ms: int = 0
    endpoint_retry_backoff_ms: int | None = None
    endpoint_retry_not_before_utc: str | None = None
    endpoint_wait_reason_code: str | None = None
    endpoint_wait_started_utc: str | None = None

    def __post_init__(self) -> None:
        _validate_snapshot_text(self.run_target_id, "RUN_PROGRESS_TARGET_ID_INVALID")
        _validate_snapshot_text(self.endpoint_id, "RUN_PROGRESS_ENDPOINT_ID_INVALID")
        _validate_snapshot_text(
            self.endpoint_revision_id,
            "RUN_PROGRESS_ENDPOINT_REVISION_ID_INVALID",
        )
        _validate_non_negative_counts(
            self.planned_operations,
            self.completed_operations,
            self.planned_bytes,
            self.completed_bytes,
            self.warning_count,
            self.error_count,
            self.endpoint_wait_attempts,
            self.endpoint_wait_total_backoff_ms,
        )
        if self.endpoint_retry_backoff_ms is not None and (
            self.endpoint_retry_backoff_ms < 1
            or self.endpoint_retry_backoff_ms > 300_000
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ENDPOINT_BACKOFF_INVALID")
        for value in (
            self.endpoint_retry_not_before_utc,
            self.endpoint_wait_started_utc,
        ):
            if value is not None and (
                not value.strip() or len(value) > 64 or not value.endswith("Z")
            ):
                raise ProgressSnapshotQueryError("RUN_PROGRESS_ENDPOINT_UTC_INVALID")
        if self.endpoint_wait_reason_code is not None:
            _validate_snapshot_text(
                self.endpoint_wait_reason_code,
                "RUN_PROGRESS_ENDPOINT_WAIT_REASON_INVALID",
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_target_id": self.run_target_id,
            "endpoint_id": self.endpoint_id,
            "endpoint_revision_id": self.endpoint_revision_id,
            "state": self.state.value,
            "planned_operations": self.planned_operations,
            "completed_operations": self.completed_operations,
            "planned_bytes": self.planned_bytes,
            "completed_bytes": self.completed_bytes,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "endpoint_wait_attempts": self.endpoint_wait_attempts,
            "endpoint_wait_total_backoff_ms": self.endpoint_wait_total_backoff_ms,
            "endpoint_retry_backoff_ms": self.endpoint_retry_backoff_ms,
            "endpoint_retry_not_before_utc": self.endpoint_retry_not_before_utc,
            "endpoint_wait_reason_code": self.endpoint_wait_reason_code,
            "endpoint_wait_started_utc": self.endpoint_wait_started_utc,
        }


@dataclass(frozen=True)
class RunProgressSnapshot:
    run_id: str
    job_id: str
    job_revision_id: str
    plan_id: str
    sequence_no: int
    state: RunState
    terminal: bool
    started_utc: str
    finished_utc: str | None
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int
    targets: tuple[RunTargetProgressSnapshot, ...]
    transferred_operations: int = 0
    transferred_bytes: int = 0
    active_relative_path: str | None = None
    active_phase: str | None = None
    active_planned_bytes: int | None = None
    active_staging_failure_count: int | None = None
    active_retry_backoff_ms: int | None = None
    active_retry_not_before_utc: str | None = None
    active_last_error_code: str | None = None
    bytes_per_second: float | None = None
    eta_seconds: int | None = None
    stop_requested: bool = False
    schema_version: int = PROGRESS_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, error_code in (
            (self.run_id, "RUN_PROGRESS_RUN_ID_INVALID"),
            (self.job_id, "RUN_PROGRESS_JOB_ID_INVALID"),
            (self.job_revision_id, "RUN_PROGRESS_JOB_REVISION_ID_INVALID"),
            (self.plan_id, "RUN_PROGRESS_PLAN_ID_INVALID"),
        ):
            _validate_snapshot_text(value, error_code)
        if self.schema_version != PROGRESS_SNAPSHOT_SCHEMA_VERSION:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_SCHEMA_VERSION_INVALID")
        if self.sequence_no < 0:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_SEQUENCE_INVALID")
        if not self.started_utc or len(self.started_utc) > 64:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_STARTED_UTC_INVALID")
        if self.finished_utc is not None and (
            not self.finished_utc or len(self.finished_utc) > 64
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_FINISHED_UTC_INVALID")
        if len(self.targets) > MAX_PROGRESS_SNAPSHOT_TARGETS:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_TARGET_LIMIT_EXCEEDED")
        _validate_non_negative_counts(
            self.planned_operations,
            self.completed_operations,
            self.planned_bytes,
            self.completed_bytes,
            self.warning_count,
            self.error_count,
            self.transferred_operations,
            self.transferred_bytes,
        )
        if self.active_relative_path is not None and (
            not self.active_relative_path
            or len(self.active_relative_path) > MAX_PROGRESS_ACTIVE_PATH_LENGTH
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ACTIVE_PATH_INVALID")
        if self.active_phase is not None:
            _validate_snapshot_text(self.active_phase, "RUN_PROGRESS_ACTIVE_PHASE_INVALID")
        if self.active_planned_bytes is not None and self.active_planned_bytes < 0:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ACTIVE_BYTES_INVALID")
        if self.active_staging_failure_count is not None and (
            self.active_staging_failure_count < 0
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ACTIVE_FAILURE_COUNT_INVALID")
        if (self.active_retry_backoff_ms is None) != (
            self.active_retry_not_before_utc is None
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ACTIVE_RETRY_PAIR_INVALID")
        if self.active_retry_backoff_ms is not None and not (
            1 <= self.active_retry_backoff_ms <= 30_000
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ACTIVE_RETRY_BACKOFF_INVALID")
        if self.active_retry_not_before_utc is not None and (
            not self.active_retry_not_before_utc.endswith("Z")
            or len(self.active_retry_not_before_utc) > 64
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ACTIVE_RETRY_UTC_INVALID")
        if self.active_last_error_code is not None:
            _validate_snapshot_text(
                self.active_last_error_code,
                "RUN_PROGRESS_ACTIVE_ERROR_CODE_INVALID",
            )
        if self.bytes_per_second is not None and (
            not math.isfinite(self.bytes_per_second) or self.bytes_per_second < 0
        ):
            raise ProgressSnapshotQueryError("RUN_PROGRESS_SPEED_INVALID")
        if self.eta_seconds is not None and self.eta_seconds < 0:
            raise ProgressSnapshotQueryError("RUN_PROGRESS_ETA_INVALID")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "plan_id": self.plan_id,
            "sequence_no": self.sequence_no,
            "state": self.state.value,
            "terminal": self.terminal,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "planned_operations": self.planned_operations,
            "completed_operations": self.completed_operations,
            "planned_bytes": self.planned_bytes,
            "completed_bytes": self.completed_bytes,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "targets": [target.to_dict() for target in self.targets],
            "transferred_operations": self.transferred_operations,
            "transferred_bytes": self.transferred_bytes,
            "active_relative_path": self.active_relative_path,
            "active_phase": self.active_phase,
            "active_planned_bytes": self.active_planned_bytes,
            "active_staging_failure_count": self.active_staging_failure_count,
            "active_retry_backoff_ms": self.active_retry_backoff_ms,
            "active_retry_not_before_utc": self.active_retry_not_before_utc,
            "active_last_error_code": self.active_last_error_code,
            "bytes_per_second": self.bytes_per_second,
            "eta_seconds": self.eta_seconds,
            "stop_requested": self.stop_requested,
        }


class RunProgressSnapshotStore(Protocol):
    def load_run_progress_snapshot(self, run_id: str) -> RunProgressSnapshot | None: ...


@dataclass(frozen=True)
class RunProgressSnapshotResult:
    run_id: str
    read_model_available: bool
    run_found: bool
    changed: bool
    sequence_reset: bool
    requested_after_sequence_no: int | None
    snapshot: RunProgressSnapshot | None = None

    @classmethod
    def unavailable(
        cls,
        *,
        run_id: str,
        after_sequence_no: int | None,
    ) -> "RunProgressSnapshotResult":
        return cls(
            run_id=run_id,
            read_model_available=False,
            run_found=False,
            changed=False,
            sequence_reset=False,
            requested_after_sequence_no=after_sequence_no,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "read_model_available": self.read_model_available,
            "run_found": self.run_found,
            "changed": self.changed,
            "sequence_reset": self.sequence_reset,
            "requested_after_sequence_no": self.requested_after_sequence_no,
            "snapshot": None if self.snapshot is None else self.snapshot.to_dict(),
        }


def query_run_progress(
    *,
    run_progress_store: RunProgressSnapshotStore | None,
    run_id: str,
    after_sequence_no: int | None = None,
) -> RunProgressSnapshotResult:
    normalized_run_id = _normalize_run_id(run_id)
    normalized_sequence = _normalize_after_sequence(after_sequence_no)
    if run_progress_store is None:
        return RunProgressSnapshotResult.unavailable(
            run_id=normalized_run_id,
            after_sequence_no=normalized_sequence,
        )

    snapshot = run_progress_store.load_run_progress_snapshot(normalized_run_id)
    if snapshot is None:
        return RunProgressSnapshotResult(
            run_id=normalized_run_id,
            read_model_available=True,
            run_found=False,
            changed=False,
            sequence_reset=False,
            requested_after_sequence_no=normalized_sequence,
        )

    unchanged = normalized_sequence == snapshot.sequence_no
    sequence_reset = (
        normalized_sequence is not None and normalized_sequence > snapshot.sequence_no
    )
    return RunProgressSnapshotResult(
        run_id=normalized_run_id,
        read_model_available=True,
        run_found=True,
        changed=not unchanged,
        sequence_reset=sequence_reset,
        requested_after_sequence_no=normalized_sequence,
        snapshot=None if unchanged else snapshot,
    )


def _normalize_run_id(run_id: str) -> str:
    normalized = run_id.strip()
    if not normalized:
        raise ProgressSnapshotQueryError("RUN_PROGRESS_REQUIRES_RUN_ID")
    if len(normalized) > MAX_PROGRESS_QUERY_ID_LENGTH:
        raise ProgressSnapshotQueryError("RUN_PROGRESS_RUN_ID_TOO_LONG")
    return normalized


def _normalize_after_sequence(after_sequence_no: int | None) -> int | None:
    if after_sequence_no is None:
        return None
    if isinstance(after_sequence_no, bool):
        raise ProgressSnapshotQueryError("RUN_PROGRESS_SEQUENCE_INVALID")
    normalized = int(after_sequence_no)
    if normalized < 0:
        raise ProgressSnapshotQueryError("RUN_PROGRESS_SEQUENCE_INVALID")
    return normalized


def _validate_snapshot_text(value: str, error_code: str) -> None:
    if not value or len(value) > MAX_PROGRESS_QUERY_ID_LENGTH:
        raise ProgressSnapshotQueryError(error_code)


def _validate_non_negative_counts(*values: int) -> None:
    if any(value < 0 for value in values):
        raise ProgressSnapshotQueryError("RUN_PROGRESS_COUNT_INVALID")
