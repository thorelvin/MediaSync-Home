from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediasync_home.domain.capabilities import MutationPermit


@dataclass(frozen=True)
class RelativePath:
    value: str


@dataclass(frozen=True)
class VerifiedStagingArtifact:
    object_id: str
    relative_path: RelativePath
    content_hash: str


@dataclass(frozen=True)
class CommitReceipt:
    operation_id: str
    final_relative_path: RelativePath


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

