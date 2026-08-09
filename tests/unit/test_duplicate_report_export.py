from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mediasync_home.ipc.protocol import IpcReason, IpcResponse
from mediasync_home.presentation.duplicate_report_export import (
    DuplicateReportExportError,
    export_duplicate_report_csv,
)


class _ReportProvider:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[dict[str, object] | None] = []

    def get_duplicate_report(
        self,
        *,
        analysis_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        assert analysis_id == "analysis-a"
        assert limit == 500
        self.calls.append(after)
        rows = [self._row("source-a", "SOURCE", "Original.bin")]
        cursor = {
            "relationship_class": "UNRELATED_CROSS_ENDPOINT_DUPLICATE",
            "full_hash": "a" * 64,
            "group_id": "group-a",
            "relative_path": "Original.bin",
            "snapshot_id": "snapshot-source",
            "file_entry_id": "file-source",
        }
        if after is not None:
            rows = [self._row("target-a", "TARGET", "Elsewhere.bin")]
            cursor = None
        return IpcResponse.accepted(
            {
                "duplicate_report": {
                    "analysis_id": analysis_id,
                    "rows": rows,
                    "next_cursor": cursor,
                    "has_more": after is None,
                }
            }
        )

    def _row(self, endpoint_id: str, endpoint_role: str, name: str) -> dict[str, object]:
        source = endpoint_role == "SOURCE"
        return {
            "group_id": "group-a",
            "relationship_class": "UNRELATED_CROSS_ENDPOINT_DUPLICATE",
            "full_hash": "a" * 64,
            "size_bytes": 4096,
            "member_count": 2,
            "physical_object_count": 2,
            "expected_replica_count": 0,
            "potential_savings_bytes": 4096,
            "review_state": "UNREVIEWED",
            "created_utc": "2026-08-02T12:00:00Z",
            "snapshot_id": "snapshot-source" if source else "snapshot-target",
            "endpoint_id": endpoint_id,
            "file_entry_id": "file-source" if source else "file-target",
            "relative_path": name,
            "member_role": "DUPLICATE",
            "physical_object_key": f"physical-{endpoint_id}",
            "endpoint_role": endpoint_role,
            "absolute_path": str(self.root / endpoint_id / name),
            "evidence_kind": "CURRENT_READ_HASH",
        }


def test_duplicate_report_export_pages_to_atomic_classified_csv(tmp_path: Path) -> None:
    provider = _ReportProvider(tmp_path)
    destination = tmp_path / "identical-files.csv"

    result = export_duplicate_report_csv(
        provider=provider,
        analysis_id="analysis-a",
        destination=destination,
    )

    with destination.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert result.row_count == 2
    assert result.group_count == 1
    assert len(provider.calls) == 2
    assert {row["endpoint_role"] for row in rows} == {"SOURCE", "TARGET"}
    assert {row["evidence_kind"] for row in rows} == {"CURRENT_READ_HASH"}
    assert {row["relationship_class"] for row in rows} == {
        "UNRELATED_CROSS_ENDPOINT_DUPLICATE"
    }
    assert not tuple(tmp_path.glob("*.tmp"))


def test_duplicate_report_export_preserves_existing_file_on_invalid_page(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "existing.csv"
    destination.write_text("existing", encoding="utf-8")

    class _RejectedProvider:
        def get_duplicate_report(
            self,
            *,
            analysis_id: str,
            limit: int | None = None,
            after: dict[str, object] | None = None,
        ) -> IpcResponse:
            del analysis_id, limit, after
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

    with pytest.raises(DuplicateReportExportError, match="PAGE_INVALID"):
        export_duplicate_report_csv(
            provider=_RejectedProvider(),
            analysis_id="analysis-a",
            destination=destination,
        )

    assert destination.read_text(encoding="utf-8") == "existing"
    assert not tuple(tmp_path.glob("*.tmp"))
