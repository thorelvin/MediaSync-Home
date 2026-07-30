from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class EndpointControlAreaState(str, Enum):
    ABSENT = "ABSENT"
    VALID_OWNED = "VALID_OWNED"
    VALID_FOREIGN = "VALID_FOREIGN"
    VALID_READ_ONLY_NEWER_SCHEMA = "VALID_READ_ONLY_NEWER_SCHEMA"
    PARTIAL_CONTROL_AREA = "PARTIAL_CONTROL_AREA"
    UNKNOWN_EMPTY_DIRECTORY = "UNKNOWN_EMPTY_DIRECTORY"
    UNKNOWN_NONEMPTY_DIRECTORY = "UNKNOWN_NONEMPTY_DIRECTORY"
    CASE_ALIAS_COLLISION = "CASE_ALIAS_COLLISION"
    CORRUPT_MARKER = "CORRUPT_MARKER"


EXCLUDABLE_CONTROL_AREA_STATES = frozenset(
    {
        EndpointControlAreaState.VALID_OWNED,
        EndpointControlAreaState.VALID_FOREIGN,
        EndpointControlAreaState.VALID_READ_ONLY_NEWER_SCHEMA,
    }
)


@dataclass(frozen=True, slots=True)
class EndpointMarkerEvidence:
    control_schema_version: int
    endpoint_id: str
    control_area_id: str
    owner_installation_id: str
    ownership_epoch: int
    root_identity_hash_algorithm: str
    root_identity_hash: str
    marker_checksum_algorithm: str
    marker_checksum: str
    latest_ownership_record: str

    def to_dict(self) -> dict[str, object]:
        return {
            "control_schema_version": self.control_schema_version,
            "endpoint_id": self.endpoint_id,
            "control_area_id": self.control_area_id,
            "owner_installation_id": self.owner_installation_id,
            "ownership_epoch": self.ownership_epoch,
            "root_identity_hash_algorithm": self.root_identity_hash_algorithm,
            "root_identity_hash": self.root_identity_hash,
            "marker_checksum_algorithm": self.marker_checksum_algorithm,
            "marker_checksum": self.marker_checksum,
            "latest_ownership_record": self.latest_ownership_record,
        }


@dataclass(frozen=True, slots=True)
class EndpointControlAreaClassification:
    root: Path
    state: EndpointControlAreaState
    reason_codes: tuple[str, ...]
    marker: EndpointMarkerEvidence | None = None

    @property
    def exclude_from_snapshot(self) -> bool:
        return self.state in EXCLUDABLE_CONTROL_AREA_STATES

    @property
    def mutating_allowed(self) -> bool:
        return self.state is EndpointControlAreaState.VALID_OWNED

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "state": self.state.value,
            "reason_codes": list(self.reason_codes),
            "exclude_from_snapshot": self.exclude_from_snapshot,
            "mutating_allowed": self.mutating_allowed,
            "marker": None if self.marker is None else self.marker.to_dict(),
        }


class EndpointControlAreaClassifier(Protocol):
    def classify_control_area(
        self,
        root: Path,
        *,
        local_installation_id: str,
    ) -> EndpointControlAreaClassification: ...
