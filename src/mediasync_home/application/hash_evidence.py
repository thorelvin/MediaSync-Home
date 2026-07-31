from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


CONTENT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CURRENT_READ_HASH_ALGORITHM = "BLAKE3-256"
CURRENT_READ_HASH_SCHEMA_VERSION = 1


class HashEvidenceKind(str, Enum):
    CURRENT_READ_HASH = "CURRENT_READ_HASH"


class CurrentReadHashRefreshState(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class CurrentReadHashEvidenceError(ValueError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True, slots=True)
class CurrentReadHashEvidence:
    snapshot_id: str
    entry_id: str
    endpoint_id: str
    content_hash: str
    size_bytes: int
    algorithm: str
    hash_schema_version: int
    evidence_kind: HashEvidenceKind
    read_started_fingerprint_hash: str
    read_completed_fingerprint_hash: str
    computed_utc: str

    def __post_init__(self) -> None:
        if (
            not self.snapshot_id.strip()
            or not self.entry_id.strip()
            or not self.endpoint_id.strip()
        ):
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_IDENTITY_INVALID",
                "Refresh the endpoint snapshot before comparing file contents.",
            )
        if self.size_bytes < 0:
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_SIZE_INVALID",
                "Refresh the endpoint snapshot before comparing file contents.",
            )
        if (
            self.algorithm != CURRENT_READ_HASH_ALGORITHM
            or self.hash_schema_version != CURRENT_READ_HASH_SCHEMA_VERSION
            or self.evidence_kind is not HashEvidenceKind.CURRENT_READ_HASH
            or CONTENT_HASH_PATTERN.fullmatch(self.content_hash) is None
            or CONTENT_HASH_PATTERN.fullmatch(
                self.read_started_fingerprint_hash
            )
            is None
            or CONTENT_HASH_PATTERN.fullmatch(
                self.read_completed_fingerprint_hash
            )
            is None
            or self.read_started_fingerprint_hash
            != self.read_completed_fingerprint_hash
            or not self.computed_utc.strip()
        ):
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_EVIDENCE_INVALID",
                "Read the complete file again before treating it as identical.",
            )


@dataclass(frozen=True, slots=True)
class CurrentReadHashRefreshReport:
    analysis_id: str
    state: CurrentReadHashRefreshState
    reason_code: str
    candidate_pair_count: int
    hashed_entry_count: int
    reused_entry_count: int
    identical_pair_count: int
    changed_pair_count: int

    @property
    def ready(self) -> bool:
        return self.state is CurrentReadHashRefreshState.READY


class CurrentReadHashEvidenceRefresher(Protocol):
    def refresh_current_read_hash_evidence(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> CurrentReadHashRefreshReport: ...


def compatible_current_read_hashes(
    left: CurrentReadHashEvidence | None,
    right: CurrentReadHashEvidence | None,
    *,
    left_snapshot_id: str,
    left_entry_id: str,
    right_snapshot_id: str,
    right_entry_id: str,
    expected_size: int,
) -> bool:
    if left is None or right is None:
        return False
    return (
        left.snapshot_id == left_snapshot_id
        and left.entry_id == left_entry_id
        and right.snapshot_id == right_snapshot_id
        and right.entry_id == right_entry_id
        and left.size_bytes == expected_size
        and right.size_bytes == expected_size
        and left.algorithm == right.algorithm == CURRENT_READ_HASH_ALGORITHM
        and left.hash_schema_version
        == right.hash_schema_version
        == CURRENT_READ_HASH_SCHEMA_VERSION
        and left.evidence_kind
        is right.evidence_kind
        is HashEvidenceKind.CURRENT_READ_HASH
    )
