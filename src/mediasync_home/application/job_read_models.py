from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.job_drafts import (
    JobDraftStore,
    StandardBackupDefaults,
    StandardBackupJobDraft,
)


DEFAULT_BACKUP_OVERVIEW_LIMIT = 10
MAX_BACKUP_OVERVIEW_LIMIT = 25


class BackupOverviewQueryError(ValueError):
    pass


class BackupJobDetailQueryError(ValueError):
    pass


@dataclass(frozen=True)
class StandardBackupTargetSummary:
    name: str
    path_label: str
    independent_device_id: str | None = None
    registration_state: str | None = None
    registration_reason_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "path_label": self.path_label,
            "independent_device_id": self.independent_device_id,
        }
        if self.registration_state is not None:
            payload["registration_state"] = self.registration_state
        if self.registration_reason_code is not None:
            payload["registration_reason_code"] = self.registration_reason_code
        return payload


@dataclass(frozen=True)
class StandardBackupJobSummary:
    job_id: str
    job_revision_id: str
    filter_set_id: str
    source_name: str
    source_path_label: str
    targets: tuple[StandardBackupTargetSummary, ...]
    filter_set_version: int = 1

    def to_dict(self) -> dict[str, object]:
        independent_device_ids = {
            target.independent_device_id for target in self.targets if target.independent_device_id
        }
        return {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "filter_set_id": self.filter_set_id,
            "filter_set_version": self.filter_set_version,
            "title": self.source_name,
            "source_name": self.source_name,
            "source_path_label": self.source_path_label,
            "configured_target_count": len(self.targets),
            "independent_device_count": len(independent_device_ids),
            "targets": [target.to_dict() for target in self.targets],
        }


@dataclass(frozen=True)
class InitialBackupPlanSummary:
    state: str
    reason_code: str
    operation_count: int
    planned_bytes: int
    plan_runnable: bool
    next_action: str
    analysis_id: str | None = None
    plan_id: str | None = None
    plan_checksum: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "analysis_id": self.analysis_id,
            "plan_id": self.plan_id,
            "plan_checksum": self.plan_checksum,
            "operation_count": self.operation_count,
            "planned_bytes": self.planned_bytes,
            "plan_runnable": self.plan_runnable,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class StandardBackupJobDetail:
    job_id: str
    job_revision_id: str
    filter_set_id: str
    source_name: str
    source_path_label: str
    targets: tuple[StandardBackupTargetSummary, ...]
    defaults: StandardBackupDefaults
    filter_set_version: int = 1
    initial_plan: InitialBackupPlanSummary | None = None

    def to_dict(self) -> dict[str, object]:
        independent_device_ids = {
            target.independent_device_id for target in self.targets if target.independent_device_id
        }
        payload: dict[str, object] = {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "filter_set_id": self.filter_set_id,
            "filter_set_version": self.filter_set_version,
            "title": self.source_name,
            "source_name": self.source_name,
            "source_path_label": self.source_path_label,
            "configured_target_count": len(self.targets),
            "independent_device_count": len(independent_device_ids),
            "defaults": _defaults_to_dict(self.defaults),
            "targets": [target.to_dict() for target in self.targets],
        }
        payload["initial_plan"] = (
            None if self.initial_plan is None else self.initial_plan.to_dict()
        )
        return payload


@dataclass(frozen=True)
class BackupOverviewPage:
    limit: int
    offset: int
    has_more: bool
    read_model_available: bool
    jobs: tuple[StandardBackupJobSummary, ...] = ()
    draft: StandardBackupJobDraft | None = None
    requested_draft_id: str | None = None

    @classmethod
    def unavailable(cls, *, limit: int, offset: int, draft_id: str | None) -> "BackupOverviewPage":
        return cls(
            limit=limit,
            offset=offset,
            has_more=False,
            read_model_available=False,
            requested_draft_id=draft_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "requested_draft_id": self.requested_draft_id,
            "draft": None if self.draft is None else _draft_to_dict(self.draft),
            "jobs": [job.to_dict() for job in self.jobs],
        }


@dataclass(frozen=True)
class BackupJobDetailResult:
    job_id: str
    read_model_available: bool
    found: bool
    job: StandardBackupJobDetail | None = None

    @classmethod
    def unavailable(cls, *, job_id: str) -> "BackupJobDetailResult":
        return cls(
            job_id=job_id,
            read_model_available=False,
            found=False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "read_model_available": self.read_model_available,
            "found": self.found,
            "job": None if self.job is None else self.job.to_dict(),
        }


class StandardBackupJobReadModelStore(Protocol):
    def list_active_standard_backup_job_summaries(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[StandardBackupJobSummary, ...]: ...


class StandardBackupJobDetailReadModelStore(Protocol):
    def load_standard_backup_job_detail(self, job_id: str) -> StandardBackupJobDetail | None: ...


def query_backup_overview(
    *,
    job_read_store: StandardBackupJobReadModelStore | None,
    draft_store: JobDraftStore | None = None,
    draft_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> BackupOverviewPage:
    page_limit, page_offset = normalize_backup_overview_bounds(limit=limit, offset=offset)
    normalized_draft_id = _normalized_draft_id(draft_id)
    draft = (
        draft_store.load_standard_backup_draft(normalized_draft_id)
        if draft_store is not None and normalized_draft_id is not None
        else None
    )
    if job_read_store is None:
        return BackupOverviewPage.unavailable(
            limit=page_limit,
            offset=page_offset,
            draft_id=normalized_draft_id,
        )

    rows = job_read_store.list_active_standard_backup_job_summaries(
        limit=page_limit + 1,
        offset=page_offset,
    )
    return BackupOverviewPage(
        limit=page_limit,
        offset=page_offset,
        has_more=len(rows) > page_limit,
        read_model_available=True,
        jobs=rows[:page_limit],
        draft=draft,
        requested_draft_id=normalized_draft_id,
    )


def query_backup_job_detail(
    *,
    job_detail_store: StandardBackupJobDetailReadModelStore | None,
    job_id: str,
) -> BackupJobDetailResult:
    normalized_job_id = normalize_backup_job_detail_id(job_id)
    if job_detail_store is None:
        return BackupJobDetailResult.unavailable(job_id=normalized_job_id)

    detail = job_detail_store.load_standard_backup_job_detail(normalized_job_id)
    return BackupJobDetailResult(
        job_id=normalized_job_id,
        read_model_available=True,
        found=detail is not None,
        job=detail,
    )


def normalize_backup_job_detail_id(job_id: str) -> str:
    normalized = str(job_id).strip()
    if not normalized:
        raise BackupJobDetailQueryError("BACKUP_JOB_DETAIL_REQUIRES_JOB_ID")
    return normalized


def normalize_backup_overview_bounds(
    *,
    limit: int | None,
    offset: int | None,
) -> tuple[int, int]:
    page_limit = DEFAULT_BACKUP_OVERVIEW_LIMIT if limit is None else int(limit)
    page_offset = 0 if offset is None else int(offset)
    if page_limit < 1 or page_limit > MAX_BACKUP_OVERVIEW_LIMIT:
        raise BackupOverviewQueryError("BACKUP_OVERVIEW_LIMIT_OUT_OF_RANGE")
    if page_offset < 0:
        raise BackupOverviewQueryError("BACKUP_OVERVIEW_OFFSET_OUT_OF_RANGE")
    return page_limit, page_offset


def _normalized_draft_id(draft_id: str | None) -> str | None:
    if draft_id is None:
        return None
    normalized = draft_id.strip()
    if not normalized:
        raise BackupOverviewQueryError("BACKUP_OVERVIEW_DRAFT_ID_EMPTY")
    return normalized


def _draft_to_dict(draft: StandardBackupJobDraft) -> dict[str, object]:
    return {
        "draft_id": draft.draft_id,
        "schema_version": draft.schema_version,
        "source_name": draft.source_name,
        "source_path_label": draft.source_path_label,
        "can_create": draft.can_create(),
        "validation_codes": [issue.code.value for issue in draft.validation_issues()],
        "defaults": _defaults_to_dict(draft.defaults),
        "targets": [
            {
                "name": target.name,
                "path_label": target.path_label,
                "independent_device_id": target.independent_device_id,
            }
            for target in draft.targets
        ],
    }


def _defaults_to_dict(defaults: StandardBackupDefaults) -> dict[str, object]:
    return {
        "behavior": defaults.behavior.value,
        "file_selection": defaults.file_selection.value,
        "verification": defaults.verification.value,
        "retention": defaults.retention.value,
        "extra_files": defaults.extra_files.value,
        "performance": defaults.performance.value,
    }
