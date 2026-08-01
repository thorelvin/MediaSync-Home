from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediasync_home.application.file_filters import FileFilterPolicy
from mediasync_home.application.snapshots import (
    SNAPSHOT_COMPLETE_COVERAGE_STATE,
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotFilterDecision,
    SnapshotIssue,
)


@dataclass(frozen=True, slots=True)
class DirectoryCaseContext:
    case_mode: str
    evidence: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class FilesystemSnapshotScan:
    snapshot_id: str
    root: Path
    entries: tuple[SnapshotFileEntry, ...]
    coverage: tuple[SnapshotDirectoryCoverage, ...]
    issues: tuple[SnapshotIssue, ...]
    control_area_excluded: bool
    filter_decisions: tuple[SnapshotFilterDecision, ...] = ()
    rescan_attempt_count: int = 0

    @property
    def complete(self) -> bool:
        return bool(self.coverage) and all(
            item.coverage_state == SNAPSHOT_COMPLETE_COVERAGE_STATE
            for item in self.coverage
        ) and not any(issue.blocks_destructive_actions for issue in self.issues)


@dataclass(frozen=True, slots=True)
class SnapshotMaterializationIds:
    analysis_id: str
    snapshot_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JobSnapshotMaterializationResult:
    job_id: str
    job_revision_id: str
    analysis_id: str | None
    state: str
    reason_code: str
    snapshot_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "analysis_id": self.analysis_id,
            "state": self.state,
            "reason_code": self.reason_code,
            "snapshot_ids": list(self.snapshot_ids),
        }


@dataclass(frozen=True, slots=True)
class SnapshotMaterializationRefreshReport:
    scanned_job_count: int
    reused_job_count: int
    blocked_job_count: int
    failed_job_count: int
    sealed_snapshot_count: int
    results: tuple[JobSnapshotMaterializationResult, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_job_count": self.scanned_job_count,
            "reused_job_count": self.reused_job_count,
            "blocked_job_count": self.blocked_job_count,
            "failed_job_count": self.failed_job_count,
            "sealed_snapshot_count": self.sealed_snapshot_count,
            "results": [result.to_dict() for result in self.results],
        }


class DirectoryCaseModeProbe(Protocol):
    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext: ...


class FilesystemSnapshotScanner(Protocol):
    def scan(
        self,
        root: Path,
        *,
        snapshot_id: str,
        exclude_control_area: bool,
        filter_policy: FileFilterPolicy | None = None,
    ) -> FilesystemSnapshotScan: ...


class SnapshotMaterializationIdFactory(Protocol):
    def new_snapshot_materialization_ids(
        self,
        *,
        snapshot_count: int,
    ) -> SnapshotMaterializationIds: ...


class JobSnapshotMaterializationRefresher(Protocol):
    def refresh_job_snapshots(
        self,
        *,
        observed_utc: str,
        job_id: str | None = None,
        force: bool = False,
    ) -> SnapshotMaterializationRefreshReport: ...
