from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class DuplicateRelationshipClass(str, Enum):
    EXPECTED_REPLICA = "EXPECTED_REPLICA"
    INTRA_ENDPOINT_DUPLICATE = "INTRA_ENDPOINT_DUPLICATE"
    UNRELATED_CROSS_ENDPOINT_DUPLICATE = "UNRELATED_CROSS_ENDPOINT_DUPLICATE"
    SAME_FILE_MULTIPLE_PATHS = "SAME_FILE_MULTIPLE_PATHS"
    POTENTIAL_DUPLICATE = "POTENTIAL_DUPLICATE"


class DuplicateRelationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DuplicateRelationMaterializationReport:
    analysis_id: str
    alias_group_count: int
    alias_path_count: int
    expected_replica_group_count: int
    expected_replica_count: int
    idempotent_replay: bool
    internal_duplicate_group_count: int = 0
    internal_duplicate_file_count: int = 0
    cross_endpoint_duplicate_group_count: int = 0
    cross_endpoint_duplicate_file_count: int = 0


class DuplicateRelationMaterializer(Protocol):
    def materialize_known_duplicate_relations(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateRelationMaterializationReport: ...


@dataclass(frozen=True, slots=True)
class DuplicateAnalysisSummary:
    analysis_id: str | None
    read_model_available: bool
    duplicate_group_count: int = 0
    expected_replica_group_count: int = 0
    expected_replica_count: int = 0
    same_file_alias_group_count: int = 0
    same_file_alias_path_count: int = 0
    internal_duplicate_group_count: int = 0
    internal_duplicate_file_count: int = 0
    cross_endpoint_duplicate_group_count: int = 0
    cross_endpoint_duplicate_file_count: int = 0
    potential_savings_bytes: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_id": self.analysis_id,
            "read_model_available": self.read_model_available,
            "duplicate_group_count": self.duplicate_group_count,
            "expected_replica_group_count": self.expected_replica_group_count,
            "expected_replica_count": self.expected_replica_count,
            "same_file_alias_group_count": self.same_file_alias_group_count,
            "same_file_alias_path_count": self.same_file_alias_path_count,
            "internal_duplicate_group_count": self.internal_duplicate_group_count,
            "internal_duplicate_file_count": self.internal_duplicate_file_count,
            "cross_endpoint_duplicate_group_count": (
                self.cross_endpoint_duplicate_group_count
            ),
            "cross_endpoint_duplicate_file_count": (
                self.cross_endpoint_duplicate_file_count
            ),
            "potential_savings_bytes": self.potential_savings_bytes,
        }


class DuplicateAnalysisReadStore(Protocol):
    def load_duplicate_analysis_summary(
        self,
        analysis_id: str,
    ) -> DuplicateAnalysisSummary | None: ...


def query_duplicate_analysis_summary(
    *,
    read_store: DuplicateAnalysisReadStore | None,
    analysis_id: str,
) -> DuplicateAnalysisSummary:
    normalized_analysis_id = analysis_id.strip()
    if not normalized_analysis_id or len(normalized_analysis_id) > 256:
        raise DuplicateRelationError("DUPLICATE_SUMMARY_ANALYSIS_ID_INVALID")
    if read_store is None:
        return DuplicateAnalysisSummary(
            analysis_id=normalized_analysis_id,
            read_model_available=False,
        )
    summary = read_store.load_duplicate_analysis_summary(normalized_analysis_id)
    if summary is None:
        return DuplicateAnalysisSummary(
            analysis_id=normalized_analysis_id,
            read_model_available=True,
        )
    return summary
