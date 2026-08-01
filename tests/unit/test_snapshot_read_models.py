from __future__ import annotations

import pytest

from mediasync_home.application.snapshot_read_models import (
    DEFAULT_SNAPSHOT_COVERAGE_PAGE_LIMIT,
    DEFAULT_SNAPSHOT_ENTRY_PAGE_LIMIT,
    DEFAULT_SNAPSHOT_ISSUE_PAGE_LIMIT,
    SnapshotCoverageQueryError,
    SnapshotEntriesQueryError,
    SnapshotIssuesQueryError,
    query_snapshot_coverage,
    query_snapshot_entries,
    query_snapshot_issues,
)
from mediasync_home.application.snapshots import (
    SnapshotCoverageCursor,
    SnapshotCoveragePage,
    SnapshotCoveragePageQuery,
    SnapshotCoverageReadModel,
    SnapshotEntryCursor,
    SnapshotEntryPage,
    SnapshotEntryPageQuery,
    SnapshotEntryReadModel,
    SnapshotIssueCursor,
    SnapshotIssuePage,
    SnapshotIssuePageQuery,
    SnapshotIssueReadModel,
)


def test_snapshot_entry_query_reports_unavailable_store_with_normalized_bounds() -> None:
    page = query_snapshot_entries(snapshot_read_store=None, snapshot_id=" snapshot-a ")

    assert page.to_dict() == {
        "snapshot_id": "snapshot-a",
        "limit": DEFAULT_SNAPSHOT_ENTRY_PAGE_LIMIT,
        "has_more": False,
        "read_model_available": False,
        "next_cursor": None,
        "entries": [],
    }


def test_snapshot_entry_query_returns_bounded_serializable_page() -> None:
    store = _FakeSnapshotEntryStore((_entry("file-a"), _entry("file-b")))

    page = query_snapshot_entries(
        snapshot_read_store=store,
        snapshot_id="snapshot-a",
        limit=1,
        after={
            "comparison_key": "000:start",
            "relative_path": "Start.txt",
            "entry_id": "file-start",
        },
    )

    assert store.queries == (
        SnapshotEntryPageQuery(
            snapshot_id="snapshot-a",
            limit=1,
            after=SnapshotEntryCursor(
                comparison_key="000:start",
                relative_path="Start.txt",
                entry_id="file-start",
            ),
        ),
    )
    assert page.to_dict() == {
        "snapshot_id": "snapshot-a",
        "limit": 1,
        "has_more": True,
        "read_model_available": True,
        "next_cursor": {
            "comparison_key": "010:Pictures/file-a.jpg",
            "relative_path": "Pictures/file-a.jpg",
            "entry_id": "file-a",
        },
        "entries": [
            {
                "entry_id": "file-a",
                "relative_path": "Pictures/file-a.jpg",
                "comparison_key": "010:Pictures/file-a.jpg",
                "object_type": "file",
                "size_bytes": 128,
                "birthtime_ns": 1_000,
                "case_collision_group_id": "case-group-a",
            }
        ],
    }


@pytest.mark.parametrize(
    ("snapshot_id", "limit", "after"),
    [
        (" ", None, None),
        ("snapshot-a", 0, None),
        ("snapshot-a", 1001, None),
        ("snapshot-a", 10, {"comparison_key": "", "relative_path": ".", "entry_id": "file-a"}),
        (
            "snapshot-a",
            10,
            {"comparison_key": "010:path", "relative_path": "C:/absolute", "entry_id": "file-a"},
        ),
        ("snapshot-a", 10, {"comparison_key": "010:path", "relative_path": ".", "entry_id": ""}),
    ],
)
def test_snapshot_entry_query_rejects_invalid_bounds_or_cursor(
    snapshot_id: str,
    limit: int | None,
    after: dict[str, object] | None,
) -> None:
    with pytest.raises(SnapshotEntriesQueryError):
        query_snapshot_entries(
            snapshot_read_store=None,
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
        )


def test_snapshot_coverage_query_reports_unavailable_store_with_normalized_filters() -> None:
    page = query_snapshot_coverage(
        snapshot_coverage_store=None,
        snapshot_id=" snapshot-a ",
        coverage_states=("VOLATILE",),
    )

    assert page.to_dict() == {
        "snapshot_id": "snapshot-a",
        "limit": DEFAULT_SNAPSHOT_COVERAGE_PAGE_LIMIT,
        "has_more": False,
        "read_model_available": False,
        "coverage_states": ["VOLATILE"],
        "next_cursor": None,
        "coverage": [],
    }


def test_snapshot_coverage_query_returns_bounded_serializable_page() -> None:
    store = _FakeSnapshotCoverageStore((_coverage("Photos"), _coverage("Videos", "VOLATILE")))

    page = query_snapshot_coverage(
        snapshot_coverage_store=store,
        snapshot_id="snapshot-a",
        limit=1,
        after={
            "comparison_key": "archive",
            "relative_path": "Archive",
        },
        coverage_states=("COMPLETE",),
    )

    assert store.queries == (
        SnapshotCoveragePageQuery(
            snapshot_id="snapshot-a",
            limit=1,
            after=SnapshotCoverageCursor(
                comparison_key="archive",
                relative_path="Archive",
            ),
            coverage_states=("COMPLETE",),
        ),
    )
    assert page.to_dict() == {
        "snapshot_id": "snapshot-a",
        "limit": 1,
        "has_more": True,
        "read_model_available": True,
        "coverage_states": ["COMPLETE"],
        "next_cursor": {
            "comparison_key": "photos",
            "relative_path": "Photos",
        },
        "coverage": [
            {
                "relative_path": "Photos",
                "comparison_key": "photos",
                "coverage_state": "COMPLETE",
                "case_mode": "CASE_INSENSITIVE",
                "case_mode_evidence": "probe-ok",
                "case_context_hash": "a" * 64,
                "case_probe_error": None,
            }
        ],
    }


@pytest.mark.parametrize(
    ("snapshot_id", "limit", "after", "coverage_states"),
    [
        (" ", None, None, ()),
        ("snapshot-a", 0, None, ()),
        ("snapshot-a", 1001, None, ()),
        ("snapshot-a", 10, {"comparison_key": "", "relative_path": "."}, ()),
        ("snapshot-a", 10, {"comparison_key": "archive", "relative_path": "C:/absolute"}, ()),
        ("snapshot-a", 10, None, ("VOLATILE", "VOLATILE")),
        ("snapshot-a", 10, None, ("UNKNOWN_NEW_STATE",)),
    ],
)
def test_snapshot_coverage_query_rejects_invalid_bounds_filters_or_cursor(
    snapshot_id: str,
    limit: int | None,
    after: dict[str, object] | None,
    coverage_states: tuple[str, ...],
) -> None:
    with pytest.raises(SnapshotCoverageQueryError):
        query_snapshot_coverage(
            snapshot_coverage_store=None,
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
            coverage_states=coverage_states,
        )


def test_snapshot_issues_query_reports_unavailable_store_with_normalized_filter() -> None:
    page = query_snapshot_issues(
        snapshot_issue_store=None,
        snapshot_id=" snapshot-a ",
        blocking_only=True,
    )

    assert page.to_dict() == {
        "snapshot_id": "snapshot-a",
        "limit": DEFAULT_SNAPSHOT_ISSUE_PAGE_LIMIT,
        "has_more": False,
        "read_model_available": False,
        "blocking_only": True,
        "next_cursor": None,
        "issues": [],
    }


def test_snapshot_issues_query_returns_bounded_serializable_page() -> None:
    store = _FakeSnapshotIssueStore((_issue(1, "Archive"), _issue(2, "Videos")))

    page = query_snapshot_issues(
        snapshot_issue_store=store,
        snapshot_id="snapshot-a",
        limit=1,
        after={
            "relative_path": "Archive",
            "issue_type": "UNREADABLE_DIRECTORY",
            "issue_id": "1",
        },
        blocking_only=True,
    )

    assert store.queries == (
        SnapshotIssuePageQuery(
            snapshot_id="snapshot-a",
            limit=1,
            after=SnapshotIssueCursor(
                relative_path="Archive",
                issue_type="UNREADABLE_DIRECTORY",
                issue_id=1,
            ),
            blocking_only=True,
        ),
    )
    assert page.to_dict() == {
        "snapshot_id": "snapshot-a",
        "limit": 1,
        "has_more": True,
        "read_model_available": True,
        "blocking_only": True,
        "next_cursor": {
            "relative_path": "Archive",
            "issue_type": "UNREADABLE_DIRECTORY",
            "issue_id": 1,
        },
        "issues": [
            {
                "issue_id": 1,
                "relative_path": "Archive",
                "issue_type": "UNREADABLE_DIRECTORY",
                "blocks_destructive_actions": True,
                "error_code": "ERROR_ACCESS_DENIED",
                "sanitized_message": "access denied",
            }
        ],
    }


@pytest.mark.parametrize(
    ("snapshot_id", "limit", "after"),
    [
        (" ", None, None),
        ("snapshot-a", 0, None),
        ("snapshot-a", 1001, None),
        (
            "snapshot-a",
            10,
            {"relative_path": "C:/absolute", "issue_type": "UNREADABLE_DIRECTORY", "issue_id": 1},
        ),
        ("snapshot-a", 10, {"relative_path": "Archive", "issue_type": "", "issue_id": 1}),
        ("snapshot-a", 10, {"relative_path": "Archive", "issue_type": "TYPE", "issue_id": 0}),
        ("snapshot-a", 10, {"relative_path": "Archive", "issue_type": "TYPE", "issue_id": True}),
    ],
)
def test_snapshot_issues_query_rejects_invalid_bounds_or_cursor(
    snapshot_id: str,
    limit: int | None,
    after: dict[str, object] | None,
) -> None:
    with pytest.raises(SnapshotIssuesQueryError):
        query_snapshot_issues(
            snapshot_issue_store=None,
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
        )


class _FakeSnapshotEntryStore:
    def __init__(self, entries: tuple[SnapshotEntryReadModel, ...]) -> None:
        self._entries = entries
        self.queries: tuple[SnapshotEntryPageQuery, ...] = ()

    def page_snapshot_entries(self, query: SnapshotEntryPageQuery) -> SnapshotEntryPage:
        self.queries = (*self.queries, query)
        entries = self._entries[: query.limit]
        return SnapshotEntryPage(
            snapshot_id=query.snapshot_id,
            entries=entries,
            has_more=len(self._entries) > query.limit,
            next_cursor=_cursor(entries[-1]) if len(self._entries) > query.limit else None,
        )


class _FakeSnapshotCoverageStore:
    def __init__(self, coverage: tuple[SnapshotCoverageReadModel, ...]) -> None:
        self._coverage = coverage
        self.queries: tuple[SnapshotCoveragePageQuery, ...] = ()

    def page_snapshot_directory_coverage(
        self,
        query: SnapshotCoveragePageQuery,
    ) -> SnapshotCoveragePage:
        self.queries = (*self.queries, query)
        coverage = self._coverage[: query.limit]
        return SnapshotCoveragePage(
            snapshot_id=query.snapshot_id,
            coverage=coverage,
            has_more=len(self._coverage) > query.limit,
            next_cursor=_coverage_cursor(coverage[-1])
            if len(self._coverage) > query.limit
            else None,
        )


class _FakeSnapshotIssueStore:
    def __init__(self, issues: tuple[SnapshotIssueReadModel, ...]) -> None:
        self._issues = issues
        self.queries: tuple[SnapshotIssuePageQuery, ...] = ()

    def page_snapshot_issues(self, query: SnapshotIssuePageQuery) -> SnapshotIssuePage:
        self.queries = (*self.queries, query)
        issues = self._issues[: query.limit]
        return SnapshotIssuePage(
            snapshot_id=query.snapshot_id,
            issues=issues,
            has_more=len(self._issues) > query.limit,
            next_cursor=_issue_cursor(issues[-1]) if len(self._issues) > query.limit else None,
        )


def _entry(entry_id: str) -> SnapshotEntryReadModel:
    return SnapshotEntryReadModel(
        entry_id=entry_id,
        relative_path=f"Pictures/{entry_id}.jpg",
        comparison_key=f"010:Pictures/{entry_id}.jpg",
        object_type="file",
        size_bytes=128,
        birthtime_ns=1_000,
        case_collision_group_id="case-group-a",
    )


def _cursor(entry: SnapshotEntryReadModel) -> SnapshotEntryCursor:
    return SnapshotEntryCursor(
        comparison_key=entry.comparison_key,
        relative_path=entry.relative_path,
        entry_id=entry.entry_id,
    )


def _coverage(
    relative_path: str,
    coverage_state: str = "COMPLETE",
) -> SnapshotCoverageReadModel:
    return SnapshotCoverageReadModel(
        relative_path=relative_path,
        comparison_key=relative_path.lower(),
        coverage_state=coverage_state,
        case_mode="CASE_INSENSITIVE",
        case_mode_evidence="probe-ok",
        case_context_hash="a" * 64,
    )


def _coverage_cursor(coverage: SnapshotCoverageReadModel) -> SnapshotCoverageCursor:
    return SnapshotCoverageCursor(
        comparison_key=coverage.comparison_key,
        relative_path=coverage.relative_path,
    )


def _issue(issue_id: int, relative_path: str) -> SnapshotIssueReadModel:
    return SnapshotIssueReadModel(
        issue_id=issue_id,
        relative_path=relative_path,
        issue_type="UNREADABLE_DIRECTORY",
        blocks_destructive_actions=True,
        error_code="ERROR_ACCESS_DENIED",
        sanitized_message="access denied",
    )


def _issue_cursor(issue: SnapshotIssueReadModel) -> SnapshotIssueCursor:
    return SnapshotIssueCursor(
        relative_path=issue.relative_path,
        issue_type=issue.issue_type,
        issue_id=issue.issue_id,
    )
