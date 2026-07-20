from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mediasync_home.application.snapshots import (
    SnapshotCoverageCursor,
    SnapshotCoveragePageQuery,
    SnapshotCoverageReadModel,
    SnapshotCoverageReadModelStore,
    SnapshotEntryCursor,
    SnapshotEntryPageQuery,
    SnapshotEntryReadModel,
    SnapshotEntryReadModelStore,
    SnapshotIssueCursor,
    SnapshotIssuePageQuery,
    SnapshotIssueReadModel,
    SnapshotIssueReadModelStore,
    SnapshotMaterializationError,
    validate_snapshot_coverage_page_query,
    validate_snapshot_entry_page_query,
    validate_snapshot_issue_page_query,
)


DEFAULT_SNAPSHOT_ENTRY_PAGE_LIMIT = 100
DEFAULT_SNAPSHOT_COVERAGE_PAGE_LIMIT = 100
DEFAULT_SNAPSHOT_ISSUE_PAGE_LIMIT = 100


class SnapshotEntriesQueryError(ValueError):
    pass


class SnapshotCoverageQueryError(ValueError):
    pass


class SnapshotIssuesQueryError(ValueError):
    pass


@dataclass(frozen=True)
class SnapshotEntriesReadPage:
    snapshot_id: str
    limit: int
    has_more: bool
    read_model_available: bool
    entries: tuple[SnapshotEntryReadModel, ...] = ()
    next_cursor: SnapshotEntryCursor | None = None

    @classmethod
    def unavailable(cls, *, query: SnapshotEntryPageQuery) -> "SnapshotEntriesReadPage":
        return cls(
            snapshot_id=query.snapshot_id,
            limit=query.limit,
            has_more=False,
            read_model_available=False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "limit": self.limit,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "next_cursor": _entry_cursor_to_dict(self.next_cursor),
            "entries": [_entry_to_dict(entry) for entry in self.entries],
        }


@dataclass(frozen=True)
class SnapshotCoverageReadPage:
    snapshot_id: str
    limit: int
    has_more: bool
    read_model_available: bool
    coverage_states: tuple[str, ...] = ()
    coverage: tuple[SnapshotCoverageReadModel, ...] = ()
    next_cursor: SnapshotCoverageCursor | None = None

    @classmethod
    def unavailable(cls, *, query: SnapshotCoveragePageQuery) -> "SnapshotCoverageReadPage":
        return cls(
            snapshot_id=query.snapshot_id,
            limit=query.limit,
            has_more=False,
            read_model_available=False,
            coverage_states=query.coverage_states,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "limit": self.limit,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "coverage_states": list(self.coverage_states),
            "next_cursor": _coverage_cursor_to_dict(self.next_cursor),
            "coverage": [_coverage_to_dict(coverage) for coverage in self.coverage],
        }


@dataclass(frozen=True)
class SnapshotIssuesReadPage:
    snapshot_id: str
    limit: int
    has_more: bool
    read_model_available: bool
    blocking_only: bool = False
    issues: tuple[SnapshotIssueReadModel, ...] = ()
    next_cursor: SnapshotIssueCursor | None = None

    @classmethod
    def unavailable(cls, *, query: SnapshotIssuePageQuery) -> "SnapshotIssuesReadPage":
        return cls(
            snapshot_id=query.snapshot_id,
            limit=query.limit,
            has_more=False,
            read_model_available=False,
            blocking_only=query.blocking_only,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "limit": self.limit,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "blocking_only": self.blocking_only,
            "next_cursor": _issue_cursor_to_dict(self.next_cursor),
            "issues": [_issue_to_dict(issue) for issue in self.issues],
        }


def query_snapshot_entries(
    *,
    snapshot_read_store: SnapshotEntryReadModelStore | None,
    snapshot_id: str,
    limit: int | None = None,
    after: SnapshotEntryCursor | Mapping[str, object] | None = None,
) -> SnapshotEntriesReadPage:
    query = normalize_snapshot_entry_page_query(
        snapshot_id=snapshot_id,
        limit=limit,
        after=after,
    )
    if snapshot_read_store is None:
        return SnapshotEntriesReadPage.unavailable(query=query)

    try:
        page = snapshot_read_store.page_snapshot_entries(query)
    except SnapshotMaterializationError as exc:
        raise SnapshotEntriesQueryError(str(exc)) from exc
    return SnapshotEntriesReadPage(
        snapshot_id=page.snapshot_id,
        limit=query.limit,
        has_more=page.has_more,
        read_model_available=True,
        entries=page.entries,
        next_cursor=page.next_cursor,
    )


def query_snapshot_coverage(
    *,
    snapshot_coverage_store: SnapshotCoverageReadModelStore | None,
    snapshot_id: str,
    limit: int | None = None,
    after: SnapshotCoverageCursor | Mapping[str, object] | None = None,
    coverage_states: tuple[str, ...] = (),
) -> SnapshotCoverageReadPage:
    query = normalize_snapshot_coverage_page_query(
        snapshot_id=snapshot_id,
        limit=limit,
        after=after,
        coverage_states=coverage_states,
    )
    if snapshot_coverage_store is None:
        return SnapshotCoverageReadPage.unavailable(query=query)

    try:
        page = snapshot_coverage_store.page_snapshot_directory_coverage(query)
    except SnapshotMaterializationError as exc:
        raise SnapshotCoverageQueryError(str(exc)) from exc
    return SnapshotCoverageReadPage(
        snapshot_id=page.snapshot_id,
        limit=query.limit,
        has_more=page.has_more,
        read_model_available=True,
        coverage_states=query.coverage_states,
        coverage=page.coverage,
        next_cursor=page.next_cursor,
    )


def query_snapshot_issues(
    *,
    snapshot_issue_store: SnapshotIssueReadModelStore | None,
    snapshot_id: str,
    limit: int | None = None,
    after: SnapshotIssueCursor | Mapping[str, object] | None = None,
    blocking_only: bool = False,
) -> SnapshotIssuesReadPage:
    query = normalize_snapshot_issue_page_query(
        snapshot_id=snapshot_id,
        limit=limit,
        after=after,
        blocking_only=blocking_only,
    )
    if snapshot_issue_store is None:
        return SnapshotIssuesReadPage.unavailable(query=query)

    try:
        page = snapshot_issue_store.page_snapshot_issues(query)
    except SnapshotMaterializationError as exc:
        raise SnapshotIssuesQueryError(str(exc)) from exc
    return SnapshotIssuesReadPage(
        snapshot_id=page.snapshot_id,
        limit=query.limit,
        has_more=page.has_more,
        read_model_available=True,
        blocking_only=query.blocking_only,
        issues=page.issues,
        next_cursor=page.next_cursor,
    )


def normalize_snapshot_entry_page_query(
    *,
    snapshot_id: str,
    limit: int | None,
    after: SnapshotEntryCursor | Mapping[str, object] | None,
) -> SnapshotEntryPageQuery:
    try:
        query = SnapshotEntryPageQuery(
            snapshot_id=str(snapshot_id).strip(),
            limit=DEFAULT_SNAPSHOT_ENTRY_PAGE_LIMIT if limit is None else int(limit),
            after=_normalize_entry_cursor(after),
        )
        validate_snapshot_entry_page_query(query)
    except (KeyError, TypeError, ValueError, SnapshotMaterializationError) as exc:
        raise SnapshotEntriesQueryError("SNAPSHOT_ENTRIES_QUERY_INVALID") from exc
    return query


def normalize_snapshot_coverage_page_query(
    *,
    snapshot_id: str,
    limit: int | None,
    after: SnapshotCoverageCursor | Mapping[str, object] | None,
    coverage_states: tuple[str, ...] = (),
) -> SnapshotCoveragePageQuery:
    try:
        query = SnapshotCoveragePageQuery(
            snapshot_id=str(snapshot_id).strip(),
            limit=DEFAULT_SNAPSHOT_COVERAGE_PAGE_LIMIT if limit is None else int(limit),
            after=_normalize_coverage_cursor(after),
            coverage_states=tuple(str(state).strip() for state in coverage_states),
        )
        validate_snapshot_coverage_page_query(query)
    except (KeyError, TypeError, ValueError, SnapshotMaterializationError) as exc:
        raise SnapshotCoverageQueryError("SNAPSHOT_COVERAGE_QUERY_INVALID") from exc
    return query


def normalize_snapshot_issue_page_query(
    *,
    snapshot_id: str,
    limit: int | None,
    after: SnapshotIssueCursor | Mapping[str, object] | None,
    blocking_only: bool,
) -> SnapshotIssuePageQuery:
    try:
        query = SnapshotIssuePageQuery(
            snapshot_id=str(snapshot_id).strip(),
            limit=DEFAULT_SNAPSHOT_ISSUE_PAGE_LIMIT if limit is None else int(limit),
            after=_normalize_issue_cursor(after),
            blocking_only=blocking_only,
        )
        validate_snapshot_issue_page_query(query)
    except (KeyError, TypeError, ValueError, SnapshotMaterializationError) as exc:
        raise SnapshotIssuesQueryError("SNAPSHOT_ISSUES_QUERY_INVALID") from exc
    return query


def _normalize_entry_cursor(
    value: SnapshotEntryCursor | Mapping[str, object] | None,
) -> SnapshotEntryCursor | None:
    if value is None or isinstance(value, SnapshotEntryCursor):
        return value
    return SnapshotEntryCursor(
        comparison_key=str(value["comparison_key"]),
        relative_path=str(value["relative_path"]),
        entry_id=str(value["entry_id"]),
    )


def _normalize_coverage_cursor(
    value: SnapshotCoverageCursor | Mapping[str, object] | None,
) -> SnapshotCoverageCursor | None:
    if value is None or isinstance(value, SnapshotCoverageCursor):
        return value
    return SnapshotCoverageCursor(
        comparison_key=str(value["comparison_key"]),
        relative_path=str(value["relative_path"]),
    )


def _normalize_issue_cursor(
    value: SnapshotIssueCursor | Mapping[str, object] | None,
) -> SnapshotIssueCursor | None:
    if value is None or isinstance(value, SnapshotIssueCursor):
        return value
    return SnapshotIssueCursor(
        relative_path=str(value["relative_path"]),
        issue_type=str(value["issue_type"]),
        issue_id=_cursor_int(value["issue_id"]),
    )


def _cursor_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("cursor integer must not be a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("cursor integer must be an integer or string")


def _entry_to_dict(entry: SnapshotEntryReadModel) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "relative_path": entry.relative_path,
        "comparison_key": entry.comparison_key,
        "object_type": entry.object_type,
        "size_bytes": entry.size_bytes,
        "case_collision_group_id": entry.case_collision_group_id,
    }


def _coverage_to_dict(coverage: SnapshotCoverageReadModel) -> dict[str, object]:
    return {
        "relative_path": coverage.relative_path,
        "comparison_key": coverage.comparison_key,
        "coverage_state": coverage.coverage_state,
        "case_mode": coverage.case_mode,
        "case_mode_evidence": coverage.case_mode_evidence,
        "case_context_hash": coverage.case_context_hash,
        "case_probe_error": coverage.case_probe_error,
    }


def _issue_to_dict(issue: SnapshotIssueReadModel) -> dict[str, object]:
    return {
        "issue_id": issue.issue_id,
        "relative_path": issue.relative_path,
        "issue_type": issue.issue_type,
        "blocks_destructive_actions": issue.blocks_destructive_actions,
        "error_code": issue.error_code,
        "sanitized_message": issue.sanitized_message,
    }


def _entry_cursor_to_dict(cursor: SnapshotEntryCursor | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    return {
        "comparison_key": cursor.comparison_key,
        "relative_path": cursor.relative_path,
        "entry_id": cursor.entry_id,
    }


def _coverage_cursor_to_dict(cursor: SnapshotCoverageCursor | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    return {
        "comparison_key": cursor.comparison_key,
        "relative_path": cursor.relative_path,
    }


def _issue_cursor_to_dict(cursor: SnapshotIssueCursor | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    return {
        "relative_path": cursor.relative_path,
        "issue_type": cursor.issue_type,
        "issue_id": cursor.issue_id,
    }
