from __future__ import annotations

import hashlib
import ntpath
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.job_drafts import (
    DraftTarget,
    StandardBackupJobDraft,
)


MAX_SELECTED_DIRECTORIES = 4
MAX_SELECTED_DIRECTORY_PATH_LENGTH = 32_767


class SelectedDirectoryIdentityError(ValueError):
    pass


class SelectedDirectoryProbeError(RuntimeError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


class StorageIdentityTrust(str, Enum):
    CONFIRMED = "CONFIRMED"
    LOGICAL_ONLY = "LOGICAL_ONLY"
    UNKNOWN = "UNKNOWN"


class SelectedDirectoryRelationKind(str, Enum):
    SAME_ROOT_ALIAS = "SAME_ROOT_ALIAS"
    ROOT_OVERLAP = "ROOT_OVERLAP"
    SAME_PHYSICAL_DEVICE = "SAME_PHYSICAL_DEVICE"
    SAME_LOGICAL_STORAGE = "SAME_LOGICAL_STORAGE"


@dataclass(frozen=True)
class SelectedDirectoryProbeEvidence:
    object_identity_key: str
    final_path: str
    storage_identity_key: str | None
    storage_identity_trust: StorageIdentityTrust


class SelectedDirectoryIdentityProbe(Protocol):
    def inspect_directory(
        self,
        path_label: str,
    ) -> SelectedDirectoryProbeEvidence: ...


@dataclass(frozen=True)
class SelectedDirectoryIdentityItem:
    ordinal: int
    status: str
    independent_device_id: str | None
    storage_identity_trust: StorageIdentityTrust
    validation_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "status": self.status,
            "independent_device_id": self.independent_device_id,
            "storage_identity_trust": self.storage_identity_trust.value,
            "validation_code": self.validation_code,
        }


@dataclass(frozen=True)
class SelectedDirectoryRelationship:
    left_ordinal: int
    right_ordinal: int
    kind: SelectedDirectoryRelationKind
    blocking: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "left_ordinal": self.left_ordinal,
            "right_ordinal": self.right_ordinal,
            "kind": self.kind.value,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class SelectedDirectoryIdentityResult:
    items: tuple[SelectedDirectoryIdentityItem, ...]
    relationships: tuple[SelectedDirectoryRelationship, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "relationships": [
                relationship.to_dict() for relationship in self.relationships
            ],
        }


def query_selected_directory_identities(
    *,
    path_labels: tuple[str, ...],
    probe: SelectedDirectoryIdentityProbe,
) -> SelectedDirectoryIdentityResult:
    normalized = _normalize_path_labels(path_labels)
    evidence_by_ordinal: dict[int, SelectedDirectoryProbeEvidence] = {}
    items: list[SelectedDirectoryIdentityItem] = []
    for ordinal, path_label in enumerate(normalized):
        try:
            evidence = probe.inspect_directory(path_label)
            _validate_probe_evidence(evidence)
        except SelectedDirectoryProbeError as exc:
            items.append(
                SelectedDirectoryIdentityItem(
                    ordinal=ordinal,
                    status="UNAVAILABLE",
                    independent_device_id=None,
                    storage_identity_trust=StorageIdentityTrust.UNKNOWN,
                    validation_code=exc.validation_code,
                )
            )
            continue
        evidence_by_ordinal[ordinal] = evidence
        items.append(
            SelectedDirectoryIdentityItem(
                ordinal=ordinal,
                status="READY",
                independent_device_id=(
                    _opaque_storage_identity(evidence.storage_identity_key)
                    if evidence.storage_identity_key is not None
                    and evidence.storage_identity_trust
                    is StorageIdentityTrust.CONFIRMED
                    else None
                ),
                storage_identity_trust=evidence.storage_identity_trust,
            )
        )

    relationships: list[SelectedDirectoryRelationship] = []
    for left_ordinal in range(len(normalized)):
        left = evidence_by_ordinal.get(left_ordinal)
        if left is None:
            continue
        for right_ordinal in range(left_ordinal + 1, len(normalized)):
            right = evidence_by_ordinal.get(right_ordinal)
            if right is None:
                continue
            relation = _strongest_relationship(
                left_ordinal=left_ordinal,
                left=left,
                right_ordinal=right_ordinal,
                right=right,
            )
            if relation is not None:
                relationships.append(relation)
    return SelectedDirectoryIdentityResult(
        items=tuple(items),
        relationships=tuple(relationships),
    )


def bind_standard_backup_draft_directory_identities(
    *,
    draft: StandardBackupJobDraft,
    probe: SelectedDirectoryIdentityProbe,
) -> StandardBackupJobDraft:
    if draft.source_path_label is None:
        raise SelectedDirectoryIdentityError(
            "SELECTED_DIRECTORY_IDENTITY_SOURCE_REQUIRED"
        )
    result = query_selected_directory_identities(
        path_labels=(
            draft.source_path_label,
            *(target.path_label for target in draft.targets),
        ),
        probe=probe,
    )
    if any(relationship.blocking for relationship in result.relationships):
        raise SelectedDirectoryIdentityError(
            "STANDARD_BACKUP_JOB_PHYSICAL_ROOT_OVERLAP"
        )
    identities = {item.ordinal: item.independent_device_id for item in result.items}
    return StandardBackupJobDraft(
        draft_id=draft.draft_id,
        source_name=draft.source_name,
        source_path_label=draft.source_path_label,
        targets=tuple(
            DraftTarget(
                name=target.name,
                path_label=target.path_label,
                independent_device_id=identities.get(index + 1),
            )
            for index, target in enumerate(draft.targets)
        ),
        defaults=draft.defaults,
        schema_version=draft.schema_version,
    )


def _normalize_path_labels(path_labels: tuple[str, ...]) -> tuple[str, ...]:
    if not 1 <= len(path_labels) <= MAX_SELECTED_DIRECTORIES:
        raise SelectedDirectoryIdentityError(
            "SELECTED_DIRECTORY_IDENTITY_PATH_COUNT_INVALID"
        )
    normalized: list[str] = []
    for path_label in path_labels:
        if (
            not isinstance(path_label, str)
            or not path_label.strip()
            or "\0" in path_label
            or len(path_label) > MAX_SELECTED_DIRECTORY_PATH_LENGTH
        ):
            raise SelectedDirectoryIdentityError(
                "SELECTED_DIRECTORY_IDENTITY_PATH_INVALID"
            )
        normalized.append(path_label.strip())
    return tuple(normalized)


def _validate_probe_evidence(evidence: SelectedDirectoryProbeEvidence) -> None:
    if not evidence.object_identity_key or not evidence.final_path:
        raise SelectedDirectoryProbeError(
            "SELECTED_DIRECTORY_IDENTITY_EVIDENCE_INCOMPLETE"
        )
    if (
        evidence.storage_identity_trust is StorageIdentityTrust.UNKNOWN
        and evidence.storage_identity_key is not None
    ):
        raise SelectedDirectoryProbeError(
            "SELECTED_DIRECTORY_STORAGE_IDENTITY_TRUST_INVALID"
        )
    if (
        evidence.storage_identity_trust is not StorageIdentityTrust.UNKNOWN
        and not evidence.storage_identity_key
    ):
        raise SelectedDirectoryProbeError(
            "SELECTED_DIRECTORY_STORAGE_IDENTITY_EVIDENCE_INCOMPLETE"
        )


def _strongest_relationship(
    *,
    left_ordinal: int,
    left: SelectedDirectoryProbeEvidence,
    right_ordinal: int,
    right: SelectedDirectoryProbeEvidence,
) -> SelectedDirectoryRelationship | None:
    if left.object_identity_key == right.object_identity_key:
        return SelectedDirectoryRelationship(
            left_ordinal=left_ordinal,
            right_ordinal=right_ordinal,
            kind=SelectedDirectoryRelationKind.SAME_ROOT_ALIAS,
            blocking=True,
        )
    if _final_paths_overlap(left.final_path, right.final_path):
        return SelectedDirectoryRelationship(
            left_ordinal=left_ordinal,
            right_ordinal=right_ordinal,
            kind=SelectedDirectoryRelationKind.ROOT_OVERLAP,
            blocking=True,
        )
    if (
        left.storage_identity_key is None
        or right.storage_identity_key is None
        or left.storage_identity_key != right.storage_identity_key
    ):
        return None
    confirmed = (
        left.storage_identity_trust is StorageIdentityTrust.CONFIRMED
        and right.storage_identity_trust is StorageIdentityTrust.CONFIRMED
    )
    return SelectedDirectoryRelationship(
        left_ordinal=left_ordinal,
        right_ordinal=right_ordinal,
        kind=(
            SelectedDirectoryRelationKind.SAME_PHYSICAL_DEVICE
            if confirmed
            else SelectedDirectoryRelationKind.SAME_LOGICAL_STORAGE
        ),
        blocking=False,
    )


def _opaque_storage_identity(storage_identity_key: str) -> str:
    return hashlib.sha256(
        f"mediasync-selected-storage-v1:{storage_identity_key}".encode("utf-8")
    ).hexdigest()


def _final_paths_overlap(first: str, second: str) -> bool:
    normalized_first = _normalized_final_path(first)
    normalized_second = _normalized_final_path(second)
    try:
        common = ntpath.commonpath((normalized_first, normalized_second))
    except ValueError:
        return False
    return common in {normalized_first, normalized_second}


def _normalized_final_path(value: str) -> str:
    normalized = value.replace("/", "\\")
    if normalized.casefold().startswith("\\\\?\\unc\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.casefold().startswith("\\\\?\\"):
        normalized = normalized[4:]
    return ntpath.normcase(ntpath.normpath(normalized))
