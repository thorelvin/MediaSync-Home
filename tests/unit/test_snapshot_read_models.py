from __future__ import annotations

import pytest

from mediasync_home.application.snapshot_read_models import (
    DEFAULT_SNAPSHOT_ENTRY_PAGE_LIMIT,
    SnapshotEntriesQueryError,
    query_snapshot_entries,
)
from mediasync_home.application.snapshots import (
    SnapshotEntryCursor,
    SnapshotEntryPage,
    SnapshotEntryPageQuery,
    SnapshotEntryReadModel,
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


def _entry(entry_id: str) -> SnapshotEntryReadModel:
    return SnapshotEntryReadModel(
        entry_id=entry_id,
        relative_path=f"Pictures/{entry_id}.jpg",
        comparison_key=f"010:Pictures/{entry_id}.jpg",
        object_type="file",
        size_bytes=128,
        case_collision_group_id="case-group-a",
    )


def _cursor(entry: SnapshotEntryReadModel) -> SnapshotEntryCursor:
    return SnapshotEntryCursor(
        comparison_key=entry.comparison_key,
        relative_path=entry.relative_path,
        entry_id=entry.entry_id,
    )
