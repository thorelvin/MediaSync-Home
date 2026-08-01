from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class JobLifecycleCommandName(str, Enum):
    ARCHIVE_STANDARD_BACKUP_JOB = "ARCHIVE_STANDARD_BACKUP_JOB"
    REACTIVATE_STANDARD_BACKUP_JOB = "REACTIVATE_STANDARD_BACKUP_JOB"


class JobLifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class JobLifecyclePayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChangeJobLifecycleCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    expected_job_revision_id: str
    expected_lifecycle_row_version: int
    explicit_confirmation: bool


@dataclass(frozen=True, slots=True)
class JobLifecycleRecord:
    job_id: str
    job_revision_id: str
    state: JobLifecycleState
    row_version: int
    archived_utc: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "lifecycle_state": self.state.value,
            "lifecycle_row_version": self.row_version,
            "archived_utc": self.archived_utc,
        }


@dataclass(frozen=True, slots=True)
class JobLifecycleTransitionOutcome:
    applied: bool
    validation_code: str
    next_action: str
    record: JobLifecycleRecord | None = None
    disabled_schedule_count: int = 0
    analysis_request_id: str | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "applied": self.applied,
            "validation_code": self.validation_code,
            "next_action": self.next_action,
            "disabled_schedule_count": self.disabled_schedule_count,
            "analysis_request_id": self.analysis_request_id,
            "idempotent_replay": self.idempotent_replay,
        }
        if self.record is not None:
            payload["job_lifecycle"] = self.record.to_dict()
        return payload


class JobLifecycleStore(Protocol):
    def load_job_lifecycle(self, job_id: str) -> JobLifecycleRecord | None: ...

    def archive_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome: ...

    def reactivate_standard_backup_job(
        self,
        *,
        command: ChangeJobLifecycleCommand,
        occurred_utc: str,
    ) -> JobLifecycleTransitionOutcome: ...


def parse_change_job_lifecycle_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> ChangeJobLifecycleCommand:
    required = {
        "job_id",
        "expected_job_revision_id",
        "expected_lifecycle_row_version",
        "explicit_confirmation",
    }
    if set(payload) != required:
        raise JobLifecyclePayloadError("JOB_LIFECYCLE_PAYLOAD_INVALID")
    job_id = _required_identifier(payload.get("job_id"), "JOB_ID")
    revision_id = _required_identifier(
        payload.get("expected_job_revision_id"),
        "JOB_REVISION_ID",
    )
    row_version = payload.get("expected_lifecycle_row_version")
    if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 1:
        raise JobLifecyclePayloadError("JOB_LIFECYCLE_ROW_VERSION_INVALID")
    confirmation = payload.get("explicit_confirmation")
    if confirmation is not True:
        raise JobLifecyclePayloadError("JOB_LIFECYCLE_CONFIRMATION_REQUIRED")
    return ChangeJobLifecycleCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        expected_job_revision_id=revision_id,
        expected_lifecycle_row_version=row_version,
        explicit_confirmation=True,
    )


def _required_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise JobLifecyclePayloadError(f"JOB_LIFECYCLE_{field_name}_INVALID")
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise JobLifecyclePayloadError(f"JOB_LIFECYCLE_{field_name}_INVALID")
    return normalized
