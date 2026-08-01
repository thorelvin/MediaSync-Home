from __future__ import annotations

import pytest

from mediasync_home.application.retained_version_history import (
    RetainedVersionCursor,
    RetainedVersionHistoryError,
    RetainedVersionSummary,
    parse_protect_retained_version_for_restore_command,
    query_retained_versions,
)


class _Store:
    def __init__(self, rows: tuple[RetainedVersionSummary, ...]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, int, RetainedVersionCursor | None]] = []

    def list_retained_versions_for_run(
        self,
        *,
        run_id: str,
        limit: int,
        after: RetainedVersionCursor | None,
    ) -> tuple[RetainedVersionSummary, ...]:
        self.calls.append((run_id, limit, after))
        return self._rows[:limit]


def test_retained_version_query_is_bounded_and_returns_keyset_cursor() -> None:
    store = _Store((_summary("version-b"), _summary("version-a")))

    page = query_retained_versions(
        version_store=store,
        run_id=" run-a ",
        limit=1,
    )

    assert store.calls == [("run-a", 2, None)]
    assert [version.version_object_id for version in page.versions] == ["version-b"]
    assert page.has_more is True
    assert page.next_cursor is not None
    assert page.next_cursor.to_dict() == {
        "cursor_version": 1,
        "created_utc": "2026-08-01T00:00:00.000Z",
        "version_object_id": "version-b",
    }


def test_retained_version_query_rejects_invalid_cursor_and_limit() -> None:
    with pytest.raises(
        RetainedVersionHistoryError,
        match="RETAINED_VERSION_CURSOR_FIELDS_INVALID",
    ):
        query_retained_versions(
            version_store=_Store(()),
            run_id="run-a",
            after={"cursor_version": 1},
        )
    with pytest.raises(
        RetainedVersionHistoryError,
        match="RETAINED_VERSION_LIMIT_OUT_OF_RANGE",
    ):
        query_retained_versions(
            version_store=_Store(()),
            run_id="run-a",
            limit=26,
        )


def test_restore_protection_command_requires_confirmation_and_exact_fields() -> None:
    command = parse_protect_retained_version_for_restore_command(
        request_id="request-a",
        idempotency_key="key-a",
        payload={
            "version_object_id": "version-a",
            "expected_row_version": 2,
            "explicit_confirmation": True,
        },
    )

    assert command.version_object_id == "version-a"
    assert command.expected_row_version == 2
    with pytest.raises(
        RetainedVersionHistoryError,
        match="VERSION_RESTORE_PROTECTION_CONFIRMATION_REQUIRED",
    ):
        parse_protect_retained_version_for_restore_command(
            request_id="request-a",
            idempotency_key="key-a",
            payload={
                "version_object_id": "version-a",
                "expected_row_version": 2,
                "explicit_confirmation": False,
            },
        )


def _summary(version_object_id: str) -> RetainedVersionSummary:
    return RetainedVersionSummary(
        version_object_id=version_object_id,
        run_id="run-a",
        operation_id=f"operation-{version_object_id}",
        job_id="job-a",
        target_endpoint_id="target-a",
        final_relative_path="Photos/image.jpg",
        created_utc="2026-08-01T00:00:00.000Z",
        retention_until_utc="2026-08-31T00:00:00.000Z",
        state="RETAINED",
        row_version=1,
    )
