from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


DUPLICATE_SCAN_MAX_CANDIDATE_FILES = 1_000_000
DUPLICATE_SCAN_MAX_ACTIVE_JOBS = 4
DUPLICATE_SCAN_MAX_HISTORY_ROWS = 10_000
DUPLICATE_SCAN_WORK_BATCH_SIZE = 64
DUPLICATE_SCAN_MAX_ATTEMPTS_PER_FILE = 3
DUPLICATE_GROUP_DEFAULT_PAGE_SIZE = 50
DUPLICATE_GROUP_MAX_PAGE_SIZE = 200
DUPLICATE_MEMBER_DEFAULT_PAGE_SIZE = 100
DUPLICATE_MEMBER_MAX_PAGE_SIZE = 200

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DuplicateScanCommandName(str, Enum):
    START_DUPLICATE_SCAN = "START_DUPLICATE_SCAN"
    PAUSE_DUPLICATE_SCAN = "PAUSE_DUPLICATE_SCAN"
    RESUME_DUPLICATE_SCAN = "RESUME_DUPLICATE_SCAN"


class DuplicateScanState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DuplicateScanStage(str, Enum):
    QUICK_SIGNATURE = "QUICK_SIGNATURE"
    FULL_HASH = "FULL_HASH"
    MATERIALIZE = "MATERIALIZE"
    DONE = "DONE"


class HashRequestStage(str, Enum):
    QUICK_SIGNATURE = "QUICK_SIGNATURE"
    FULL_HASH = "FULL_HASH"
    DONE = "DONE"


class HashRequestState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class DuplicateScanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DuplicateScanCommand:
    request_id: str
    idempotency_key: str
    analysis_id: str


@dataclass(frozen=True, slots=True)
class DuplicateScanStatus:
    scan_id: str
    analysis_id: str
    state: DuplicateScanState
    stage: DuplicateScanStage
    candidate_file_count: int
    quick_completed_count: int
    full_hash_candidate_count: int
    full_hash_completed_count: int
    issue_count: int
    requested_utc: str
    updated_utc: str
    started_utc: str | None = None
    completed_utc: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not self.scan_id.strip() or not self.analysis_id.strip():
            raise DuplicateScanError("DUPLICATE_SCAN_IDENTITY_INVALID")
        counts = (
            self.candidate_file_count,
            self.quick_completed_count,
            self.full_hash_candidate_count,
            self.full_hash_completed_count,
            self.issue_count,
        )
        if any(value < 0 for value in counts):
            raise DuplicateScanError("DUPLICATE_SCAN_COUNT_INVALID")
        if self.candidate_file_count > DUPLICATE_SCAN_MAX_CANDIDATE_FILES:
            raise DuplicateScanError("DUPLICATE_SCAN_CANDIDATE_LIMIT_EXCEEDED")
        if self.quick_completed_count > self.candidate_file_count:
            raise DuplicateScanError("DUPLICATE_SCAN_QUICK_COUNT_INVALID")
        if self.full_hash_candidate_count > self.candidate_file_count:
            raise DuplicateScanError("DUPLICATE_SCAN_FULL_COUNT_INVALID")
        if self.full_hash_completed_count > self.full_hash_candidate_count:
            raise DuplicateScanError("DUPLICATE_SCAN_FULL_COUNT_INVALID")
        if not self.requested_utc.strip() or not self.updated_utc.strip():
            raise DuplicateScanError("DUPLICATE_SCAN_TIME_INVALID")
        if self.state in {DuplicateScanState.COMPLETED, DuplicateScanState.FAILED}:
            if self.completed_utc is None or not self.completed_utc.strip():
                raise DuplicateScanError("DUPLICATE_SCAN_TERMINAL_TIME_REQUIRED")
        elif self.completed_utc is not None:
            raise DuplicateScanError("DUPLICATE_SCAN_TERMINAL_TIME_INVALID")
        if self.stage is DuplicateScanStage.DONE and self.state not in {
            DuplicateScanState.COMPLETED,
            DuplicateScanState.FAILED,
        }:
            raise DuplicateScanError("DUPLICATE_SCAN_DONE_STATE_INVALID")

    @property
    def terminal(self) -> bool:
        return self.state in {DuplicateScanState.COMPLETED, DuplicateScanState.FAILED}

    @property
    def progress_numerator(self) -> int:
        return self.quick_completed_count + self.full_hash_completed_count

    @property
    def progress_denominator(self) -> int:
        return self.candidate_file_count + self.full_hash_candidate_count

    def to_dict(self) -> dict[str, object]:
        return {
            "scan_id": self.scan_id,
            "analysis_id": self.analysis_id,
            "state": self.state.value,
            "stage": self.stage.value,
            "candidate_file_count": self.candidate_file_count,
            "quick_completed_count": self.quick_completed_count,
            "full_hash_candidate_count": self.full_hash_candidate_count,
            "full_hash_completed_count": self.full_hash_completed_count,
            "issue_count": self.issue_count,
            "progress_numerator": self.progress_numerator,
            "progress_denominator": self.progress_denominator,
            "requested_utc": self.requested_utc,
            "started_utc": self.started_utc,
            "updated_utc": self.updated_utc,
            "completed_utc": self.completed_utc,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class DuplicateScanCycleReport:
    scan: DuplicateScanStatus | None
    files_attempted: int
    files_completed: int
    files_failed: int
    stopped_reason: str

    def __post_init__(self) -> None:
        if min(self.files_attempted, self.files_completed, self.files_failed) < 0:
            raise DuplicateScanError("DUPLICATE_SCAN_CYCLE_COUNT_INVALID")
        if self.files_completed + self.files_failed > self.files_attempted:
            raise DuplicateScanError("DUPLICATE_SCAN_CYCLE_COUNT_INVALID")
        if not self.stopped_reason.strip():
            raise DuplicateScanError("DUPLICATE_SCAN_CYCLE_REASON_INVALID")


@dataclass(frozen=True, slots=True)
class DuplicateGroupCursor:
    relationship_class: str
    full_hash: str
    group_id: str


@dataclass(frozen=True, slots=True)
class DuplicateGroupReadModel:
    group_id: str
    relationship_class: str
    full_hash: str
    size_bytes: int
    member_count: int
    physical_object_count: int
    expected_replica_count: int
    potential_savings_bytes: int
    review_state: str
    created_utc: str

    def __post_init__(self) -> None:
        if not self.group_id.strip() or _HASH_PATTERN.fullmatch(self.full_hash) is None:
            raise DuplicateScanError("DUPLICATE_GROUP_IDENTITY_INVALID")
        if min(
            self.size_bytes,
            self.member_count,
            self.physical_object_count,
            self.expected_replica_count,
            self.potential_savings_bytes,
        ) < 0:
            raise DuplicateScanError("DUPLICATE_GROUP_COUNT_INVALID")

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "relationship_class": self.relationship_class,
            "full_hash": self.full_hash,
            "size_bytes": self.size_bytes,
            "member_count": self.member_count,
            "physical_object_count": self.physical_object_count,
            "expected_replica_count": self.expected_replica_count,
            "potential_savings_bytes": self.potential_savings_bytes,
            "review_state": self.review_state,
            "created_utc": self.created_utc,
        }


@dataclass(frozen=True, slots=True)
class DuplicateGroupPage:
    analysis_id: str
    groups: tuple[DuplicateGroupReadModel, ...]
    next_cursor: DuplicateGroupCursor | None
    has_more: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_id": self.analysis_id,
            "groups": [item.to_dict() for item in self.groups],
            "next_cursor": (
                None
                if self.next_cursor is None
                else {
                    "relationship_class": self.next_cursor.relationship_class,
                    "full_hash": self.next_cursor.full_hash,
                    "group_id": self.next_cursor.group_id,
                }
            ),
            "has_more": self.has_more,
        }


@dataclass(frozen=True, slots=True)
class DuplicateMemberCursor:
    relative_path: str
    snapshot_id: str
    file_entry_id: str


@dataclass(frozen=True, slots=True)
class DuplicateMemberReadModel:
    group_id: str
    snapshot_id: str
    endpoint_id: str
    file_entry_id: str
    relative_path: str
    member_role: str
    physical_object_key: str

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "snapshot_id": self.snapshot_id,
            "endpoint_id": self.endpoint_id,
            "file_entry_id": self.file_entry_id,
            "relative_path": self.relative_path,
            "member_role": self.member_role,
            "physical_object_key": self.physical_object_key,
        }


@dataclass(frozen=True, slots=True)
class DuplicateMemberPage:
    group_id: str
    members: tuple[DuplicateMemberReadModel, ...]
    next_cursor: DuplicateMemberCursor | None
    has_more: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "members": [item.to_dict() for item in self.members],
            "next_cursor": (
                None
                if self.next_cursor is None
                else {
                    "relative_path": self.next_cursor.relative_path,
                    "snapshot_id": self.next_cursor.snapshot_id,
                    "file_entry_id": self.next_cursor.file_entry_id,
                }
            ),
            "has_more": self.has_more,
        }


class DuplicateScanReadStore(Protocol):
    def load_duplicate_scan(self, analysis_id: str) -> DuplicateScanStatus | None: ...

    def page_duplicate_groups(
        self,
        *,
        analysis_id: str,
        limit: int,
        after: DuplicateGroupCursor | None = None,
        relationship_classes: tuple[str, ...] = (),
    ) -> DuplicateGroupPage: ...

    def page_duplicate_members(
        self,
        *,
        group_id: str,
        limit: int,
        after: DuplicateMemberCursor | None = None,
    ) -> DuplicateMemberPage: ...


class DuplicateScanStore(DuplicateScanReadStore, Protocol):
    def prepare_scan(self, *, analysis_id: str, observed_utc: str) -> None: ...

    def start_scan(
        self,
        *,
        analysis_id: str,
        requested_utc: str,
    ) -> DuplicateScanStatus: ...

    def pause_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None: ...

    def resume_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None: ...


def parse_duplicate_scan_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> DuplicateScanCommand:
    if set(payload) != {"analysis_id"}:
        raise DuplicateScanError("DUPLICATE_SCAN_PAYLOAD_INVALID")
    analysis_id = payload.get("analysis_id")
    if not isinstance(analysis_id, str) or not analysis_id.strip():
        raise DuplicateScanError("DUPLICATE_SCAN_ANALYSIS_ID_REQUIRED")
    return DuplicateScanCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        analysis_id=analysis_id.strip(),
    )


def deterministic_duplicate_scan_id(analysis_id: str) -> str:
    normalized = analysis_id.strip()
    if not normalized:
        raise DuplicateScanError("DUPLICATE_SCAN_ANALYSIS_ID_REQUIRED")
    digest = hashlib.sha256(f"duplicate-scan\0{normalized}".encode()).hexdigest()
    return f"duplicate-scan:{digest[:32]}"
