from __future__ import annotations

import pytest

from mediasync_home.application.catalog_read_models import (
    DEFAULT_CATALOGED_FILES_LIMIT,
    CatalogedFileReadModel,
    CatalogedFilesQueryError,
    query_cataloged_files,
)


def test_cataloged_files_query_returns_unavailable_page_without_store() -> None:
    page = query_cataloged_files(
        cataloged_file_read_store=None,
        run_id=" run-a ",
        target_endpoint_id=" target-a ",
    )

    assert page.to_dict() == {
        "limit": DEFAULT_CATALOGED_FILES_LIMIT,
        "offset": 0,
        "has_more": False,
        "read_model_available": False,
        "run_id": "run-a",
        "target_endpoint_id": "target-a",
        "files": [],
    }


def test_cataloged_files_query_returns_bounded_recent_page() -> None:
    store = _ReadStore(
        (
            _cataloged_file("final-file:run-b:op-b", run_id="run-b"),
            _cataloged_file("final-file:run-a:op-b", operation_id="op-b"),
            _cataloged_file("final-file:run-a:op-a", operation_id="op-a"),
        )
    )

    page = query_cataloged_files(
        cataloged_file_read_store=store,
        run_id="run-a",
        limit=1,
        offset=0,
    )

    payload = page.to_dict()
    assert store.calls == [
        {
            "limit": 2,
            "offset": 0,
            "run_id": "run-a",
            "target_endpoint_id": None,
        }
    ]
    assert payload["has_more"] is True
    assert payload["read_model_available"] is True
    assert [file["handoff_id"] for file in payload["files"]] == ["final-file:run-a:op-b"]


@pytest.mark.parametrize(
    ("limit", "offset"),
    [
        (0, 0),
        (101, 0),
        (1, -1),
        (True, 0),
    ],
)
def test_cataloged_files_query_rejects_invalid_bounds(
    limit: int | bool,
    offset: int,
) -> None:
    with pytest.raises(CatalogedFilesQueryError):
        query_cataloged_files(
            cataloged_file_read_store=None,
            limit=limit,  # type: ignore[arg-type]
            offset=offset,
        )


@pytest.mark.parametrize(
    ("run_id", "target_endpoint_id"),
    [
        (" ", None),
        (None, ""),
    ],
)
def test_cataloged_files_query_rejects_blank_filters(
    run_id: str | None,
    target_endpoint_id: str | None,
) -> None:
    with pytest.raises(CatalogedFilesQueryError):
        query_cataloged_files(
            cataloged_file_read_store=None,
            run_id=run_id,
            target_endpoint_id=target_endpoint_id,
        )


class _ReadStore:
    def __init__(self, files: tuple[CatalogedFileReadModel, ...]) -> None:
        self._files = files
        self.calls: list[dict[str, object]] = []

    def list_recent_cataloged_files(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
    ) -> tuple[CatalogedFileReadModel, ...]:
        self.calls.append(
            {
                "limit": limit,
                "offset": offset,
                "run_id": run_id,
                "target_endpoint_id": target_endpoint_id,
            }
        )
        files = tuple(
            file
            for file in self._files
            if (run_id is None or file.run_id == run_id)
            and (target_endpoint_id is None or file.target_endpoint_id == target_endpoint_id)
        )
        return files[offset : offset + limit]


def _cataloged_file(
    handoff_id: str,
    *,
    run_id: str = "run-a",
    operation_id: str = "op-a",
) -> CatalogedFileReadModel:
    return CatalogedFileReadModel(
        handoff_id=handoff_id,
        run_id=run_id,
        run_target_id=f"{run_id}-target-0000",
        operation_id=operation_id,
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        final_relative_path="Photos/image.jpg",
        content_hash="a" * 64,
        lease_id="lease-a",
        fencing_token=1,
        effect_kind="COPY_NEW_FINAL_FILE",
        recorded_utc="2026-07-20T12:00:00.000Z",
    )
