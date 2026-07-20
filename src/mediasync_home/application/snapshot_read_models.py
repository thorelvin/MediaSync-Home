from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mediasync_home.application.snapshots import (
    SnapshotEntryCursor,
    SnapshotEntryPageQuery,
    SnapshotEntryReadModel,
    SnapshotEntryReadModelStore,
    SnapshotMaterializationError,
    validate_snapshot_entry_page_query,
)


DEFAULT_SNAPSHOT_ENTRY_PAGE_LIMIT = 100


class SnapshotEntriesQueryError(ValueError):
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
            "next_cursor": _cursor_to_dict(self.next_cursor),
            "entries": [_entry_to_dict(entry) for entry in self.entries],
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
            after=_normalize_cursor(after),
        )
        validate_snapshot_entry_page_query(query)
    except (KeyError, TypeError, ValueError, SnapshotMaterializationError) as exc:
        raise SnapshotEntriesQueryError("SNAPSHOT_ENTRIES_QUERY_INVALID") from exc
    return query


def _normalize_cursor(
    value: SnapshotEntryCursor | Mapping[str, object] | None,
) -> SnapshotEntryCursor | None:
    if value is None or isinstance(value, SnapshotEntryCursor):
        return value
    return SnapshotEntryCursor(
        comparison_key=str(value["comparison_key"]),
        relative_path=str(value["relative_path"]),
        entry_id=str(value["entry_id"]),
    )


def _entry_to_dict(entry: SnapshotEntryReadModel) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "relative_path": entry.relative_path,
        "comparison_key": entry.comparison_key,
        "object_type": entry.object_type,
        "size_bytes": entry.size_bytes,
        "case_collision_group_id": entry.case_collision_group_id,
    }


def _cursor_to_dict(cursor: SnapshotEntryCursor | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    return {
        "comparison_key": cursor.comparison_key,
        "relative_path": cursor.relative_path,
        "entry_id": cursor.entry_id,
    }
