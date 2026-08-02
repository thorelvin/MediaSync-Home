from __future__ import annotations

import pytest

from mediasync_home.application.duplicates import (
    DuplicateAnalysisSummary,
    DuplicateRelationError,
    query_duplicate_analysis_summary,
)


def test_duplicate_summary_is_explicitly_unavailable_without_read_store() -> None:
    summary = query_duplicate_analysis_summary(
        read_store=None,
        analysis_id="analysis-a",
    )

    assert summary == DuplicateAnalysisSummary(
        analysis_id="analysis-a",
        read_model_available=False,
    )


def test_duplicate_summary_returns_truthful_relation_counts() -> None:
    expected = DuplicateAnalysisSummary(
        analysis_id="analysis-a",
        read_model_available=True,
        duplicate_group_count=1,
        expected_replica_group_count=1,
        expected_replica_count=2,
        same_file_alias_group_count=1,
        same_file_alias_path_count=2,
        potential_savings_bytes=0,
    )

    assert query_duplicate_analysis_summary(
        read_store=_ReadStore(expected),
        analysis_id=" analysis-a ",
    ) == expected
    assert expected.to_dict()["potential_savings_bytes"] == 0


@pytest.mark.parametrize("analysis_id", ["", " ", "a" * 257])
def test_duplicate_summary_rejects_invalid_analysis_id(analysis_id: str) -> None:
    with pytest.raises(
        DuplicateRelationError,
        match="DUPLICATE_SUMMARY_ANALYSIS_ID_INVALID",
    ):
        query_duplicate_analysis_summary(
            read_store=None,
            analysis_id=analysis_id,
        )


class _ReadStore:
    def __init__(self, summary: DuplicateAnalysisSummary) -> None:
        self.summary = summary

    def load_duplicate_analysis_summary(
        self,
        analysis_id: str,
    ) -> DuplicateAnalysisSummary | None:
        return self.summary if analysis_id == self.summary.analysis_id else None
