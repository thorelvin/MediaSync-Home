from __future__ import annotations

import csv
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mediasync_home.application.duplicate_scanning import (
    DUPLICATE_REPORT_MAX_PAGE_SIZE,
    DUPLICATE_REPORT_MAX_ROWS,
)
from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.duplicate_scanning import (
    duplicate_report_page_from_response,
)


class DuplicateReportExportError(RuntimeError):
    pass


class DuplicateReportProvider(Protocol):
    def get_duplicate_report(
        self,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse: ...


@dataclass(frozen=True, slots=True)
class DuplicateReportExportResult:
    destination: Path
    row_count: int
    group_count: int


_HEADERS = (
    "relationship_class",
    "evidence_kind",
    "group_id",
    "full_hash",
    "size_bytes",
    "member_count",
    "physical_object_count",
    "expected_replica_count",
    "potential_savings_bytes",
    "review_state",
    "endpoint_role",
    "endpoint_id",
    "member_role",
    "absolute_path",
    "relative_path",
)


def export_duplicate_report_csv(
    *,
    provider: DuplicateReportProvider,
    analysis_id: str,
    destination: Path,
    cancelled: Callable[[], bool] | None = None,
) -> DuplicateReportExportResult:
    if not analysis_id.strip():
        raise DuplicateReportExportError("DUPLICATE_REPORT_ANALYSIS_ID_INVALID")
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise DuplicateReportExportError("DUPLICATE_REPORT_DESTINATION_INVALID")
    temporary = destination.with_name(
        f".{destination.name}.mediasync-{uuid4().hex}.tmp"
    )
    cursor: dict[str, object] | None = None
    row_count = 0
    group_count = 0
    previous_group_id: str | None = None
    try:
        with temporary.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(_HEADERS)
            while True:
                if cancelled is not None and cancelled():
                    raise DuplicateReportExportError("DUPLICATE_REPORT_EXPORT_CANCELLED")
                page = duplicate_report_page_from_response(
                    provider.get_duplicate_report(
                        analysis_id=analysis_id,
                        limit=DUPLICATE_REPORT_MAX_PAGE_SIZE,
                        after=cursor,
                    )
                )
                if (
                    not page.read_model_available
                    or page.analysis_id != analysis_id
                    or row_count + len(page.rows) > DUPLICATE_REPORT_MAX_ROWS
                ):
                    raise DuplicateReportExportError("DUPLICATE_REPORT_PAGE_INVALID")
                for item in page.rows:
                    group = item.group
                    member = item.member
                    if group.group_id != previous_group_id:
                        group_count += 1
                        previous_group_id = group.group_id
                    writer.writerow(
                        (
                            group.relationship_class,
                            member.evidence_kind,
                            group.group_id,
                            group.full_hash,
                            group.size_bytes,
                            group.member_count,
                            group.physical_object_count,
                            group.expected_replica_count,
                            group.potential_savings_bytes,
                            group.review_state,
                            member.endpoint_role,
                            member.endpoint_id,
                            member.member_role,
                            member.absolute_path,
                            member.relative_path,
                        )
                    )
                    row_count += 1
                if not page.has_more:
                    break
                if page.next_cursor is None or page.next_cursor == cursor:
                    raise DuplicateReportExportError("DUPLICATE_REPORT_CURSOR_INVALID")
                cursor = dict(page.next_cursor)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return DuplicateReportExportResult(
        destination=destination,
        row_count=row_count,
        group_count=group_count,
    )
