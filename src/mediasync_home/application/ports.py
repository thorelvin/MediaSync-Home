from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mediasync_home.domain.capabilities import MutationPermit
from mediasync_home.application.recovery_operations import RecoveryOperationKind

if TYPE_CHECKING:
    from mediasync_home.application.recovery_operations import RecoveryOperation


@dataclass(frozen=True)
class RelativePath:
    value: str


@dataclass(frozen=True)
class VerifiedStagingArtifact:
    object_id: str
    relative_path: RelativePath
    content_hash: str
    operation_kind: RecoveryOperationKind = RecoveryOperationKind.COPY_NEW


@dataclass(frozen=True)
class CommitReceipt:
    operation_id: str
    final_relative_path: RelativePath


@dataclass(frozen=True)
class OldTargetPreservationReceipt:
    operation_id: str
    final_relative_path: RelativePath
    version_object_id: str | None = None
    quarantine_object_id: str | None = None
    fingerprint_json: str | None = None


@dataclass(frozen=True)
class OldTargetRestoreReceipt:
    operation_id: str
    final_relative_path: RelativePath
    fingerprint_json: str


@dataclass(frozen=True)
class RecoveryObjectCleanupReceipt:
    operation_id: str
    final_relative_path: RelativePath
    cleaned_object_ids: tuple[str, ...]


@dataclass(frozen=True)
class FinalArtifactVerificationEvidence:
    fingerprint_json: str


class ReadOnlySourcePort(Protocol):
    def exists(self, relative_path: RelativePath) -> bool: ...


class StagingPort(Protocol):
    def stage_verified_artifact(self, artifact: VerifiedStagingArtifact) -> None: ...


class FinalCommitPort(Protocol):
    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt: ...


class OldTargetPreservationPort(Protocol):
    def preserve_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetPreservationReceipt: ...


class OldTargetRestorePort(Protocol):
    def restore_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetRestoreReceipt: ...


class RecoveryObjectCleanupPort(Protocol):
    def cleanup_recovery_objects(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> RecoveryObjectCleanupReceipt: ...
