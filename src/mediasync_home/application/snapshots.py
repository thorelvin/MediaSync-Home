from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
SNAPSHOT_SCHEMA_VERSION = 2
SNAPSHOT_CHECKSUM_ALGORITHM = "SHA-256"
SNAPSHOT_SERIALIZER_VERSION = "0B-SNAPSHOT-CANONICAL-JSON-V2"
LEGACY_SNAPSHOT_SERIALIZER_VERSION = "0B-SNAPSHOT-CANONICAL-JSON-V1"
SNAPSHOT_COMPLETE_COVERAGE_STATE = "COMPLETE"
MAX_SNAPSHOT_ENTRY_PAGE_LIMIT = 1000
MAX_SNAPSHOT_COVERAGE_PAGE_LIMIT = 1000
MAX_SNAPSHOT_ISSUE_PAGE_LIMIT = 1000
SNAPSHOT_COVERAGE_STATES = frozenset(
    {
        "COMPLETE",
        "VOLATILE",
        "UNREADABLE",
        "DISAPPEARED",
        "REPARSE_BLOCKED",
        "CASE_CONTEXT_UNKNOWN",
        "CANCELLED",
    }
)
SNAPSHOT_CASE_MODES = frozenset({"CASE_SENSITIVE", "CASE_INSENSITIVE", "UNKNOWN"})


class SnapshotMaterializationError(ValueError):
    pass


@dataclass(frozen=True)
class SnapshotFileEntry:
    entry_id: str
    relative_path: str
    comparison_key: str
    object_type: str
    size_bytes: int | None = None
    identity_fingerprint_hash: str | None = None


@dataclass(frozen=True)
class SnapshotDirectoryCoverage:
    relative_path: str
    comparison_key: str
    coverage_state: str
    case_mode: str
    case_mode_evidence: str
    case_context_hash: str
    case_probe_error: str | None = None


@dataclass(frozen=True)
class SnapshotIssue:
    relative_path: str
    issue_type: str
    blocks_destructive_actions: bool
    error_code: str | None = None
    sanitized_message: str | None = None


@dataclass(frozen=True)
class SnapshotEntryCursor:
    comparison_key: str
    relative_path: str
    entry_id: str


@dataclass(frozen=True)
class SnapshotEntryPageQuery:
    snapshot_id: str
    limit: int
    after: SnapshotEntryCursor | None = None


@dataclass(frozen=True)
class SnapshotCoverageCursor:
    comparison_key: str
    relative_path: str


@dataclass(frozen=True)
class SnapshotCoveragePageQuery:
    snapshot_id: str
    limit: int
    after: SnapshotCoverageCursor | None = None
    coverage_states: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotIssueCursor:
    relative_path: str
    issue_type: str
    issue_id: int


@dataclass(frozen=True)
class SnapshotIssuePageQuery:
    snapshot_id: str
    limit: int
    after: SnapshotIssueCursor | None = None
    blocking_only: bool = False


@dataclass(frozen=True)
class SnapshotEntryReadModel:
    entry_id: str
    relative_path: str
    comparison_key: str
    object_type: str
    size_bytes: int | None
    case_collision_group_id: str | None = None


@dataclass(frozen=True)
class SnapshotEntryPage:
    snapshot_id: str
    entries: tuple[SnapshotEntryReadModel, ...]
    next_cursor: SnapshotEntryCursor | None
    has_more: bool


@dataclass(frozen=True)
class SnapshotCoverageReadModel:
    relative_path: str
    comparison_key: str
    coverage_state: str
    case_mode: str
    case_mode_evidence: str
    case_context_hash: str
    case_probe_error: str | None = None


@dataclass(frozen=True)
class SnapshotCoveragePage:
    snapshot_id: str
    coverage: tuple[SnapshotCoverageReadModel, ...]
    next_cursor: SnapshotCoverageCursor | None
    has_more: bool


@dataclass(frozen=True)
class SnapshotIssueReadModel:
    issue_id: int
    relative_path: str
    issue_type: str
    blocks_destructive_actions: bool
    error_code: str | None = None
    sanitized_message: str | None = None


@dataclass(frozen=True)
class SnapshotIssuePage:
    snapshot_id: str
    issues: tuple[SnapshotIssueReadModel, ...]
    next_cursor: SnapshotIssueCursor | None
    has_more: bool


@dataclass(frozen=True)
class SnapshotEntryBatch:
    snapshot_id: str
    sequence_no: int
    payload_hash: str
    entries: tuple[SnapshotFileEntry, ...]
    coverage_updates: tuple[SnapshotDirectoryCoverage, ...]
    issues: tuple[SnapshotIssue, ...]
    approximate_bytes: int


@dataclass(frozen=True)
class SnapshotBatchCommitReceipt:
    snapshot_id: str
    sequence_no: int
    payload_hash: str
    entry_count: int
    coverage_update_count: int
    issue_count: int
    approximate_bytes: int
    idempotent_replay: bool


@dataclass(frozen=True)
class SnapshotBatchSummary:
    sequence_no: int
    payload_hash: str
    entry_count: int
    coverage_update_count: int
    issue_count: int
    approximate_bytes: int


@dataclass(frozen=True)
class SnapshotSealRequest:
    snapshot_id: str
    expected_entry_count: int
    expected_total_bytes: int
    expected_batch_count: int
    expected_directory_coverage_count: int
    expected_issue_count: int = 0
    expected_blocking_issue_count: int = 0
    expected_case_collision_group_count: int = 0


@dataclass(frozen=True)
class SealedSnapshot:
    snapshot_id: str
    snapshot_schema_version: int
    checksum_algorithm: str
    serializer_version: str
    snapshot_checksum: str
    entry_count: int
    total_bytes: int
    batch_count: int
    directory_coverage_count: int
    issue_count: int
    blocking_issue_count: int
    case_collision_group_count: int
    complete: bool = True
    immutable: bool = True


class SnapshotEntryMaterializationStore(Protocol):
    def commit_snapshot_entry_batch(self, batch: SnapshotEntryBatch) -> SnapshotBatchCommitReceipt: ...

    def load_snapshot_entries(self, snapshot_id: str) -> tuple[SnapshotFileEntry, ...]: ...

    def load_directory_coverage(self, snapshot_id: str) -> tuple[SnapshotDirectoryCoverage, ...]: ...

    def load_snapshot_issues(self, snapshot_id: str) -> tuple[SnapshotIssue, ...]: ...


class SnapshotSealStore(Protocol):
    def seal_snapshot(self, request: SnapshotSealRequest) -> SealedSnapshot: ...

    def load_sealed_snapshot(self, snapshot_id: str) -> SealedSnapshot | None: ...


class SnapshotEntryReadModelStore(Protocol):
    def page_snapshot_entries(self, query: SnapshotEntryPageQuery) -> SnapshotEntryPage: ...


class SnapshotCoverageReadModelStore(Protocol):
    def page_snapshot_directory_coverage(self, query: SnapshotCoveragePageQuery) -> SnapshotCoveragePage: ...


class SnapshotIssueReadModelStore(Protocol):
    def page_snapshot_issues(self, query: SnapshotIssuePageQuery) -> SnapshotIssuePage: ...


def snapshot_entry_batch(
    *,
    snapshot_id: str,
    sequence_no: int,
    entries: tuple[SnapshotFileEntry, ...],
    coverage_updates: tuple[SnapshotDirectoryCoverage, ...] = (),
    issues: tuple[SnapshotIssue, ...] = (),
    approximate_bytes: int | None = None,
    payload_hash: str | None = None,
) -> SnapshotEntryBatch:
    measured_bytes = sum(entry.size_bytes or 0 for entry in entries)
    batch = SnapshotEntryBatch(
        snapshot_id=snapshot_id,
        sequence_no=sequence_no,
        payload_hash=payload_hash
        or _payload_hash(
            snapshot_id=snapshot_id,
            sequence_no=sequence_no,
            entries=entries,
            coverage_updates=coverage_updates,
            issues=issues,
            approximate_bytes=measured_bytes if approximate_bytes is None else approximate_bytes,
        ),
        entries=entries,
        coverage_updates=coverage_updates,
        issues=issues,
        approximate_bytes=measured_bytes if approximate_bytes is None else approximate_bytes,
    )
    validate_snapshot_entry_batch(batch)
    return batch


def snapshot_seal(
    *,
    snapshot_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    coverage: tuple[SnapshotDirectoryCoverage, ...],
    issues: tuple[SnapshotIssue, ...],
    batches: tuple[SnapshotBatchSummary, ...],
    case_collision_group_count: int,
    snapshot_schema_version: int = SNAPSHOT_SCHEMA_VERSION,
) -> SealedSnapshot:
    ordered_entries = tuple(sorted(entries, key=lambda entry: (entry.relative_path, entry.entry_id)))
    ordered_coverage = tuple(sorted(coverage, key=lambda item: item.relative_path))
    ordered_issues = tuple(sorted(issues, key=_snapshot_issue_sort_key))
    ordered_batches = tuple(sorted(batches, key=lambda batch: batch.sequence_no))
    _validate_snapshot_seal_inputs(
        snapshot_id=snapshot_id,
        entries=ordered_entries,
        coverage=ordered_coverage,
        issues=ordered_issues,
        batches=ordered_batches,
        case_collision_group_count=case_collision_group_count,
        snapshot_schema_version=snapshot_schema_version,
    )
    entry_count = len(ordered_entries)
    total_bytes = sum(entry.size_bytes or 0 for entry in ordered_entries)
    checksum = _snapshot_checksum(
        snapshot_id=snapshot_id,
        entries=ordered_entries,
        coverage=ordered_coverage,
        issues=ordered_issues,
        batches=ordered_batches,
        entry_count=entry_count,
        total_bytes=total_bytes,
        case_collision_group_count=case_collision_group_count,
        snapshot_schema_version=snapshot_schema_version,
    )
    serializer_version = _serializer_version(snapshot_schema_version)
    sealed = SealedSnapshot(
        snapshot_id=snapshot_id,
        snapshot_schema_version=snapshot_schema_version,
        checksum_algorithm=SNAPSHOT_CHECKSUM_ALGORITHM,
        serializer_version=serializer_version,
        snapshot_checksum=checksum,
        entry_count=entry_count,
        total_bytes=total_bytes,
        batch_count=len(ordered_batches),
        directory_coverage_count=len(ordered_coverage),
        issue_count=len(ordered_issues),
        blocking_issue_count=sum(1 for issue in ordered_issues if issue.blocks_destructive_actions),
        case_collision_group_count=case_collision_group_count,
    )
    validate_sealed_snapshot(sealed)
    return sealed


def verify_snapshot_checksum(
    snapshot: SealedSnapshot,
    *,
    entries: tuple[SnapshotFileEntry, ...],
    coverage: tuple[SnapshotDirectoryCoverage, ...],
    issues: tuple[SnapshotIssue, ...],
    batches: tuple[SnapshotBatchSummary, ...],
) -> bool:
    if not snapshot.complete or not snapshot.immutable:
        return False
    try:
        expected = snapshot_seal(
            snapshot_id=snapshot.snapshot_id,
            entries=entries,
            coverage=coverage,
            issues=issues,
            batches=batches,
            case_collision_group_count=snapshot.case_collision_group_count,
            snapshot_schema_version=snapshot.snapshot_schema_version,
        )
    except SnapshotMaterializationError:
        return False
    return (
        snapshot.snapshot_schema_version == expected.snapshot_schema_version
        and snapshot.checksum_algorithm == expected.checksum_algorithm
        and snapshot.serializer_version == expected.serializer_version
        and snapshot.snapshot_checksum == expected.snapshot_checksum
        and snapshot.entry_count == expected.entry_count
        and snapshot.total_bytes == expected.total_bytes
        and snapshot.batch_count == expected.batch_count
        and snapshot.directory_coverage_count == expected.directory_coverage_count
        and snapshot.issue_count == expected.issue_count
        and snapshot.blocking_issue_count == expected.blocking_issue_count
    )


def validate_snapshot_seal_request(request: SnapshotSealRequest) -> None:
    if not request.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SNAPSHOT_ID")
    if request.expected_entry_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ENTRY_COUNT_MUST_BE_NON_NEGATIVE")
    if request.expected_total_bytes < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BYTES_MUST_BE_NON_NEGATIVE")
    if request.expected_batch_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_COUNT_MUST_BE_NON_NEGATIVE")
    if request.expected_directory_coverage_count <= 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_COVERAGE")
    if request.expected_issue_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ISSUE_COUNT_MUST_BE_NON_NEGATIVE")
    if request.expected_blocking_issue_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BLOCKING_COUNT_MUST_BE_NON_NEGATIVE")
    if request.expected_case_collision_group_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MUST_BE_NON_NEGATIVE")


def validate_snapshot_entry_page_query(query: SnapshotEntryPageQuery) -> None:
    if not query.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_READ_REQUIRES_SNAPSHOT_ID")
    if query.limit <= 0:
        raise SnapshotMaterializationError("SNAPSHOT_READ_LIMIT_MUST_BE_POSITIVE")
    if query.limit > MAX_SNAPSHOT_ENTRY_PAGE_LIMIT:
        raise SnapshotMaterializationError("SNAPSHOT_READ_LIMIT_TOO_LARGE")
    if query.after is None:
        return
    if not query.after.comparison_key.strip():
        raise SnapshotMaterializationError("SNAPSHOT_READ_CURSOR_REQUIRES_COMPARISON_KEY")
    if not _valid_relative_path(query.after.relative_path):
        raise SnapshotMaterializationError("SNAPSHOT_READ_CURSOR_REQUIRES_RELATIVE_PATH")
    if not query.after.entry_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_READ_CURSOR_REQUIRES_ENTRY_ID")


def validate_snapshot_coverage_page_query(query: SnapshotCoveragePageQuery) -> None:
    if not query.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_REQUIRES_SNAPSHOT_ID")
    if query.limit <= 0:
        raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_LIMIT_MUST_BE_POSITIVE")
    if query.limit > MAX_SNAPSHOT_COVERAGE_PAGE_LIMIT:
        raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_LIMIT_TOO_LARGE")
    seen_states: set[str] = set()
    for state in query.coverage_states:
        if state not in SNAPSHOT_COVERAGE_STATES:
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_STATE_UNKNOWN")
        if state in seen_states:
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_STATES_MUST_BE_UNIQUE")
        seen_states.add(state)
    if query.after is None:
        return
    if not query.after.comparison_key.strip():
        raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_CURSOR_REQUIRES_COMPARISON_KEY")
    if not _valid_coverage_path(query.after.relative_path):
        raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_READ_CURSOR_REQUIRES_RELATIVE_PATH")


def validate_snapshot_issue_page_query(query: SnapshotIssuePageQuery) -> None:
    if not query.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_ISSUE_READ_REQUIRES_SNAPSHOT_ID")
    if query.limit <= 0:
        raise SnapshotMaterializationError("SNAPSHOT_ISSUE_READ_LIMIT_MUST_BE_POSITIVE")
    if query.limit > MAX_SNAPSHOT_ISSUE_PAGE_LIMIT:
        raise SnapshotMaterializationError("SNAPSHOT_ISSUE_READ_LIMIT_TOO_LARGE")
    if query.after is None:
        return
    if not _valid_coverage_path(query.after.relative_path):
        raise SnapshotMaterializationError("SNAPSHOT_ISSUE_READ_CURSOR_REQUIRES_RELATIVE_PATH")
    if not query.after.issue_type.strip():
        raise SnapshotMaterializationError("SNAPSHOT_ISSUE_READ_CURSOR_REQUIRES_TYPE")
    if query.after.issue_id <= 0:
        raise SnapshotMaterializationError("SNAPSHOT_ISSUE_READ_CURSOR_REQUIRES_POSITIVE_ID")


def validate_sealed_snapshot(snapshot: SealedSnapshot) -> None:
    if not snapshot.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SNAPSHOT_ID")
    if snapshot.snapshot_schema_version < 1:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SCHEMA_VERSION")
    if snapshot.checksum_algorithm != SNAPSHOT_CHECKSUM_ALGORITHM:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_CHECKSUM_ALGORITHM")
    if snapshot.serializer_version != _serializer_version(snapshot.snapshot_schema_version):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SERIALIZER_VERSION")
    if HASH_PATTERN.fullmatch(snapshot.snapshot_checksum) is None:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_CHECKSUM")
    if snapshot.entry_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ENTRY_COUNT_MUST_BE_NON_NEGATIVE")
    if snapshot.total_bytes < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BYTES_MUST_BE_NON_NEGATIVE")
    if snapshot.batch_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_COUNT_MUST_BE_NON_NEGATIVE")
    if snapshot.directory_coverage_count <= 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_COVERAGE")
    if snapshot.issue_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ISSUE_COUNT_MUST_BE_NON_NEGATIVE")
    if snapshot.blocking_issue_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BLOCKING_COUNT_MUST_BE_NON_NEGATIVE")
    if snapshot.blocking_issue_count > snapshot.issue_count:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BLOCKING_COUNT_MISMATCH")
    if snapshot.case_collision_group_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MUST_BE_NON_NEGATIVE")
    if not snapshot.complete or not snapshot.immutable:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_MUST_BE_COMPLETE_AND_IMMUTABLE")


def validate_snapshot_entry_batch(batch: SnapshotEntryBatch) -> None:
    if not batch.snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_REQUIRES_SNAPSHOT_ID")
    if batch.sequence_no < 0:
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_SEQUENCE_MUST_BE_NON_NEGATIVE")
    if HASH_PATTERN.fullmatch(batch.payload_hash) is None:
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_REQUIRES_PAYLOAD_HASH")
    if batch.approximate_bytes < 0:
        raise SnapshotMaterializationError("SNAPSHOT_BATCH_BYTES_MUST_BE_NON_NEGATIVE")

    entry_ids: set[str] = set()
    relative_paths: set[str] = set()
    for entry in batch.entries:
        if not entry.entry_id.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_ID")
        if entry.entry_id in entry_ids:
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_IDS_MUST_BE_UNIQUE_IN_BATCH")
        entry_ids.add(entry.entry_id)
        if not _valid_relative_path(entry.relative_path):
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_RELATIVE_PATH")
        if entry.relative_path in relative_paths:
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_PATHS_MUST_BE_UNIQUE_IN_BATCH")
        relative_paths.add(entry.relative_path)
        if not entry.comparison_key.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_COMPARISON_KEY")
        if not entry.object_type.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_REQUIRES_OBJECT_TYPE")
        if entry.size_bytes is not None and entry.size_bytes < 0:
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_SIZE_MUST_BE_NON_NEGATIVE")
        if (
            entry.identity_fingerprint_hash is not None
            and HASH_PATTERN.fullmatch(entry.identity_fingerprint_hash) is None
        ):
            raise SnapshotMaterializationError("SNAPSHOT_ENTRY_IDENTITY_FINGERPRINT_INVALID")
    _validate_directory_coverage(batch.coverage_updates)
    _validate_snapshot_issues(batch.issues)


def _payload_hash(
    *,
    snapshot_id: str,
    sequence_no: int,
    entries: tuple[SnapshotFileEntry, ...],
    coverage_updates: tuple[SnapshotDirectoryCoverage, ...],
    issues: tuple[SnapshotIssue, ...],
    approximate_bytes: int,
) -> str:
    payload = {
        "approximate_bytes": approximate_bytes,
        "coverage_updates": [
            _directory_coverage_payload(item)
            for item in sorted(coverage_updates, key=lambda coverage: coverage.relative_path)
        ],
        "entries": [
            {
                "comparison_key": entry.comparison_key,
                "entry_id": entry.entry_id,
                "identity_fingerprint_hash": entry.identity_fingerprint_hash,
                "object_type": entry.object_type,
                "relative_path": entry.relative_path,
                "size_bytes": entry.size_bytes,
            }
            for entry in sorted(entries, key=lambda item: (item.entry_id, item.relative_path))
        ],
        "issues": [_snapshot_issue_payload(issue) for issue in sorted(issues, key=_snapshot_issue_sort_key)],
        "sequence_no": sequence_no,
        "snapshot_id": snapshot_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _snapshot_checksum(
    *,
    snapshot_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    coverage: tuple[SnapshotDirectoryCoverage, ...],
    issues: tuple[SnapshotIssue, ...],
    batches: tuple[SnapshotBatchSummary, ...],
    entry_count: int,
    total_bytes: int,
    case_collision_group_count: int,
    snapshot_schema_version: int,
) -> str:
    serializer_version = _serializer_version(snapshot_schema_version)
    payload = {
        "batch_count": len(batches),
        "batches": [
            {
                "approximate_bytes": batch.approximate_bytes,
                "coverage_update_count": batch.coverage_update_count,
                "entry_count": batch.entry_count,
                "issue_count": batch.issue_count,
                "payload_hash": batch.payload_hash,
                "sequence_no": batch.sequence_no,
            }
            for batch in batches
        ],
        "case_collision_group_count": case_collision_group_count,
        "checksum_algorithm": SNAPSHOT_CHECKSUM_ALGORITHM,
        "complete": True,
        "coverage": [_directory_coverage_payload(item) for item in coverage],
        "directory_coverage_count": len(coverage),
        "entries": [
            _snapshot_entry_payload(
                entry,
                include_identity=snapshot_schema_version >= 2,
            )
            for entry in entries
        ],
        "entry_count": entry_count,
        "immutable": True,
        "issue_count": len(issues),
        "issues": [_snapshot_issue_payload(issue) for issue in issues],
        "blocking_issue_count": sum(1 for issue in issues if issue.blocks_destructive_actions),
        "serializer_version": serializer_version,
        "snapshot_id": snapshot_id,
        "snapshot_schema_version": snapshot_schema_version,
        "total_bytes": total_bytes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_snapshot_seal_inputs(
    *,
    snapshot_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    coverage: tuple[SnapshotDirectoryCoverage, ...],
    issues: tuple[SnapshotIssue, ...],
    batches: tuple[SnapshotBatchSummary, ...],
    case_collision_group_count: int,
    snapshot_schema_version: int,
) -> None:
    if not snapshot_id.strip():
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_SNAPSHOT_ID")
    if case_collision_group_count < 0:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MUST_BE_NON_NEGATIVE")
    _serializer_version(snapshot_schema_version)
    if snapshot_schema_version >= 2 and any(
        entry.object_type == "file"
        and HASH_PATTERN.fullmatch(entry.identity_fingerprint_hash or "") is None
        for entry in entries
    ):
        raise SnapshotMaterializationError(
            "SNAPSHOT_SEAL_REQUIRES_FILE_IDENTITY_FINGERPRINT"
        )
    validate_snapshot_entry_batch(
        SnapshotEntryBatch(
            snapshot_id=snapshot_id,
            sequence_no=0,
            payload_hash="0" * 64,
            entries=entries,
            coverage_updates=coverage,
            issues=issues,
            approximate_bytes=sum(entry.size_bytes or 0 for entry in entries),
        )
    )
    _validate_snapshot_batch_summaries(batches)
    if sum(batch.entry_count for batch in batches) != len(entries):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ENTRY_COUNT_MISMATCH")
    if sum(batch.coverage_update_count for batch in batches) != len(coverage):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_COVERAGE_COUNT_MISMATCH")
    if sum(batch.issue_count for batch in batches) != len(issues):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_ISSUE_COUNT_MISMATCH")
    if not coverage:
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_COVERAGE")
    if any(item.coverage_state != SNAPSHOT_COMPLETE_COVERAGE_STATE for item in coverage):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_COVERAGE_INCOMPLETE")
    if any(issue.blocks_destructive_actions for issue in issues):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_BLOCKING_ISSUES")


def _validate_snapshot_batch_summaries(batches: tuple[SnapshotBatchSummary, ...]) -> None:
    ordered_sequences = [batch.sequence_no for batch in batches]
    if ordered_sequences != list(range(len(batches))):
        raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_CONTIGUOUS_BATCHES")
    for batch in batches:
        if HASH_PATTERN.fullmatch(batch.payload_hash) is None:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_REQUIRES_BATCH_PAYLOAD_HASH")
        if batch.entry_count < 0:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_ENTRY_COUNT_MUST_BE_NON_NEGATIVE")
        if batch.coverage_update_count < 0:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_COVERAGE_COUNT_MUST_BE_NON_NEGATIVE")
        if batch.issue_count < 0:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_ISSUE_COUNT_MUST_BE_NON_NEGATIVE")
        if batch.approximate_bytes < 0:
            raise SnapshotMaterializationError("SNAPSHOT_SEAL_BATCH_BYTES_MUST_BE_NON_NEGATIVE")


def _serializer_version(snapshot_schema_version: int) -> str:
    if snapshot_schema_version == 1:
        return LEGACY_SNAPSHOT_SERIALIZER_VERSION
    if snapshot_schema_version == SNAPSHOT_SCHEMA_VERSION:
        return SNAPSHOT_SERIALIZER_VERSION
    raise SnapshotMaterializationError("SNAPSHOT_SEAL_SCHEMA_VERSION_UNSUPPORTED")


def _snapshot_entry_payload(
    entry: SnapshotFileEntry,
    *,
    include_identity: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "comparison_key": entry.comparison_key,
        "entry_id": entry.entry_id,
        "object_type": entry.object_type,
        "relative_path": entry.relative_path,
        "size_bytes": entry.size_bytes,
    }
    if include_identity:
        payload["identity_fingerprint_hash"] = entry.identity_fingerprint_hash
    return payload


def _validate_directory_coverage(coverage_updates: tuple[SnapshotDirectoryCoverage, ...]) -> None:
    relative_paths: set[str] = set()
    for coverage in coverage_updates:
        if not _valid_coverage_path(coverage.relative_path):
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_REQUIRES_RELATIVE_PATH")
        if coverage.relative_path in relative_paths:
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_PATHS_MUST_BE_UNIQUE_IN_BATCH")
        relative_paths.add(coverage.relative_path)
        if not coverage.comparison_key.strip():
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_REQUIRES_COMPARISON_KEY")
        if coverage.coverage_state not in SNAPSHOT_COVERAGE_STATES:
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_STATE_UNKNOWN")
        if coverage.case_mode not in SNAPSHOT_CASE_MODES:
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_CASE_MODE_UNKNOWN")
        if not coverage.case_mode_evidence.strip():
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_REQUIRES_CASE_MODE_EVIDENCE")
        if HASH_PATTERN.fullmatch(coverage.case_context_hash) is None:
            raise SnapshotMaterializationError("SNAPSHOT_COVERAGE_REQUIRES_CASE_CONTEXT_HASH")


def _validate_snapshot_issues(issues: tuple[SnapshotIssue, ...]) -> None:
    for issue in issues:
        if not _valid_coverage_path(issue.relative_path):
            raise SnapshotMaterializationError("SNAPSHOT_ISSUE_REQUIRES_RELATIVE_PATH")
        if not issue.issue_type.strip():
            raise SnapshotMaterializationError("SNAPSHOT_ISSUE_REQUIRES_TYPE")


def _directory_coverage_payload(coverage: SnapshotDirectoryCoverage) -> dict[str, str | None]:
    return {
        "case_context_hash": coverage.case_context_hash,
        "case_mode": coverage.case_mode,
        "case_mode_evidence": coverage.case_mode_evidence,
        "case_probe_error": coverage.case_probe_error,
        "comparison_key": coverage.comparison_key,
        "coverage_state": coverage.coverage_state,
        "relative_path": coverage.relative_path,
    }


def _snapshot_issue_payload(issue: SnapshotIssue) -> dict[str, bool | str | None]:
    return {
        "blocks_destructive_actions": issue.blocks_destructive_actions,
        "error_code": issue.error_code,
        "issue_type": issue.issue_type,
        "relative_path": issue.relative_path,
        "sanitized_message": issue.sanitized_message,
    }


def _snapshot_issue_sort_key(issue: SnapshotIssue) -> tuple[str, str, str, str, bool]:
    return (
        issue.relative_path,
        issue.issue_type,
        issue.error_code or "",
        issue.sanitized_message or "",
        issue.blocks_destructive_actions,
    )


def _valid_coverage_path(value: str) -> bool:
    return value == "." or _valid_relative_path(value)


def _valid_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or normalized.startswith("//")
        or WINDOWS_DRIVE_PATTERN.match(normalized)
    ):
        return False
    parts = tuple(normalized.split("/"))
    return all(part not in {"", ".", ".."} and ":" not in part for part in parts)
