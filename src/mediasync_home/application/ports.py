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
    fingerprint_json: str | None = None


@dataclass(frozen=True)
class CommitReceipt:
    operation_id: str
    final_relative_path: RelativePath
    durability_state: str = "FINAL_DURABILITY_UNCONFIRMED"
    file_flush_succeeded: bool | None = None
    write_through_move_used: bool | None = None
    filesystem_apply_method: str = "UNCONFIRMED"

    def __post_init__(self) -> None:
        expected_flags = {
            "FINAL_DURABILITY_UNCONFIRMED": (None, None),
            "LOCAL_FILE_FLUSH_CONFIRMED": (True, False),
            "LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED": (True, True),
            "LOCAL_DIRECTORY_MARKER_FLUSH_CONFIRMED_ENTRY_UNCONFIRMED": (
                True,
                False,
            ),
            "LOCAL_DIRECTORY_MARKER_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED": (
                True,
                True,
            ),
        }
        expected = expected_flags.get(self.durability_state)
        if expected is None:
            raise ValueError("FINAL_COMMIT_RECEIPT_DURABILITY_STATE_UNSUPPORTED")
        actual = (self.file_flush_succeeded, self.write_through_move_used)
        if actual != expected:
            raise ValueError("FINAL_COMMIT_RECEIPT_DURABILITY_EVIDENCE_INCONSISTENT")
        if self.filesystem_apply_method not in {
            "UNCONFIRMED",
            "MOVEFILEEX_NO_OVERWRITE",
            "MOVEFILEEX_REPLACE_EXISTING",
            "REPLACEFILEW_WITH_BACKUP",
            "REVERIFIED_EXISTING",
        }:
            raise ValueError("FINAL_COMMIT_RECEIPT_APPLY_METHOD_UNSUPPORTED")


@dataclass(frozen=True)
class OldTargetPreservationReceipt:
    operation_id: str
    final_relative_path: RelativePath
    version_object_id: str | None = None
    quarantine_object_id: str | None = None
    fingerprint_json: str | None = None
    version_created_utc: str | None = None
    version_retention_until_utc: str | None = None
    version_manifest_hash: str | None = None


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
