from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


DEFAULT_CATALOGED_FILES_LIMIT = 25
MAX_CATALOGED_FILES_LIMIT = 100


class CatalogedFilesQueryError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogedFileReadModel:
    handoff_id: str
    run_id: str
    run_target_id: str
    operation_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    final_relative_path: str
    content_hash: str
    lease_id: str
    fencing_token: int
    effect_kind: str
    recorded_utc: str


@dataclass(frozen=True)
class CatalogedFilesReadPage:
    limit: int
    offset: int
    has_more: bool
    read_model_available: bool
    run_id: str | None = None
    target_endpoint_id: str | None = None
    files: tuple[CatalogedFileReadModel, ...] = ()

    @classmethod
    def unavailable(
        cls,
        *,
        limit: int,
        offset: int,
        run_id: str | None,
        target_endpoint_id: str | None,
    ) -> "CatalogedFilesReadPage":
        return cls(
            limit=limit,
            offset=offset,
            has_more=False,
            read_model_available=False,
            run_id=run_id,
            target_endpoint_id=target_endpoint_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "run_id": self.run_id,
            "target_endpoint_id": self.target_endpoint_id,
            "files": [_cataloged_file_to_dict(file) for file in self.files],
        }


class CatalogedFileReadModelStore(Protocol):
    def list_recent_cataloged_files(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
    ) -> tuple[CatalogedFileReadModel, ...]: ...


def query_cataloged_files(
    *,
    cataloged_file_read_store: CatalogedFileReadModelStore | None,
    run_id: str | None = None,
    target_endpoint_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> CatalogedFilesReadPage:
    page_limit, page_offset = normalize_cataloged_files_bounds(limit=limit, offset=offset)
    normalized_run_id = _optional_identifier(run_id, "run_id")
    normalized_target_endpoint_id = _optional_identifier(target_endpoint_id, "target_endpoint_id")
    if cataloged_file_read_store is None:
        return CatalogedFilesReadPage.unavailable(
            limit=page_limit,
            offset=page_offset,
            run_id=normalized_run_id,
            target_endpoint_id=normalized_target_endpoint_id,
        )

    try:
        rows = cataloged_file_read_store.list_recent_cataloged_files(
            limit=page_limit + 1,
            offset=page_offset,
            run_id=normalized_run_id,
            target_endpoint_id=normalized_target_endpoint_id,
        )
    except ValueError as exc:
        raise CatalogedFilesQueryError("CATALOGED_FILES_QUERY_INVALID") from exc
    return CatalogedFilesReadPage(
        limit=page_limit,
        offset=page_offset,
        has_more=len(rows) > page_limit,
        read_model_available=True,
        run_id=normalized_run_id,
        target_endpoint_id=normalized_target_endpoint_id,
        files=rows[:page_limit],
    )


def normalize_cataloged_files_bounds(
    *,
    limit: int | None,
    offset: int | None,
) -> tuple[int, int]:
    page_limit = DEFAULT_CATALOGED_FILES_LIMIT if limit is None else _int_value(limit)
    page_offset = 0 if offset is None else _int_value(offset)
    if page_limit < 1 or page_limit > MAX_CATALOGED_FILES_LIMIT:
        raise CatalogedFilesQueryError("CATALOGED_FILES_LIMIT_OUT_OF_RANGE")
    if page_offset < 0:
        raise CatalogedFilesQueryError("CATALOGED_FILES_OFFSET_OUT_OF_RANGE")
    return page_limit, page_offset


def _cataloged_file_to_dict(file: CatalogedFileReadModel) -> dict[str, object]:
    return {
        "handoff_id": file.handoff_id,
        "run_id": file.run_id,
        "run_target_id": file.run_target_id,
        "operation_id": file.operation_id,
        "target_endpoint_id": file.target_endpoint_id,
        "target_endpoint_revision_id": file.target_endpoint_revision_id,
        "final_relative_path": file.final_relative_path,
        "content_hash": file.content_hash,
        "lease_id": file.lease_id,
        "fencing_token": file.fencing_token,
        "effect_kind": file.effect_kind,
        "recorded_utc": file.recorded_utc,
    }


def _optional_identifier(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        raise CatalogedFilesQueryError(f"CATALOGED_FILES_REQUIRES_{field_name.upper()}")
    return normalized


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        raise CatalogedFilesQueryError("CATALOGED_FILES_INTEGER_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise CatalogedFilesQueryError("CATALOGED_FILES_INTEGER_INVALID") from exc
    raise CatalogedFilesQueryError("CATALOGED_FILES_INTEGER_INVALID")
