from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from blake3 import blake3

from mediasync_home.adapters.file_identity import stable_file_identity_hash
from mediasync_home.adapters.reparse_guard import (
    LocalReparseGuard,
    ReparseGuard,
    ReparseGuardError,
)
from mediasync_home.application.hash_evidence import (
    CURRENT_READ_HASH_ALGORITHM,
    CURRENT_READ_HASH_SCHEMA_VERSION,
    CurrentReadHashEvidence,
    CurrentReadHashEvidenceError,
    HashEvidenceKind,
)
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)


CURRENT_READ_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CurrentReadHashRequest:
    snapshot_id: str
    entry_id: str
    endpoint_id: str
    root: Path
    relative_path: str
    expected_size_bytes: int
    computed_utc: str


class LocalCurrentReadHasher:
    def __init__(
        self,
        *,
        reparse_guard: ReparseGuard | None = None,
        chunk_bytes: int = CURRENT_READ_HASH_CHUNK_BYTES,
    ) -> None:
        if chunk_bytes < 1:
            raise ValueError("current-read hash chunk size must be positive")
        self._reparse_guard = reparse_guard or LocalReparseGuard()
        self._chunk_bytes = chunk_bytes

    def hash_file(self, request: CurrentReadHashRequest) -> CurrentReadHashEvidence:
        if request.expected_size_bytes < 0:
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_SIZE_INVALID",
                "Refresh the endpoint snapshot before comparing file contents.",
            )
        try:
            relative = parse_endpoint_relative_path(request.relative_path)
            root = self._reparse_guard.resolve_existing_root(
                request.root,
                missing_code="CURRENT_READ_HASH_ROOT_MISSING",
                missing_next_action="Reconnect the endpoint and retry the backup check.",
                reparse_code="CURRENT_READ_HASH_ROOT_REPARSE_UNSUPPORTED",
                reparse_next_action="Use an ordinary non-reparse endpoint root.",
            )
            evidence = self._reparse_guard.reject_reparse_chain(
                root=root,
                relative_parts=relative.parts,
                missing_code="CURRENT_READ_HASH_FILE_MISSING",
                missing_next_action="Refresh the endpoint snapshot and retry the backup check.",
                reparse_code="CURRENT_READ_HASH_REPARSE_UNSUPPORTED",
                reparse_next_action="Remove the reparse path from this backup job.",
            )
            self._reparse_guard.require_resolved_under_root(
                root=root,
                path=evidence.checked_path,
                strict=True,
                escape_code="CURRENT_READ_HASH_PATH_ESCAPED_ROOT",
                escape_next_action="Refresh the endpoint after correcting its path chain.",
            )
        except SafePathViolation as exc:
            raise CurrentReadHashEvidenceError(
                exc.validation_code,
                "Remove the unsafe relative path from the backup source.",
            ) from exc
        except ReparseGuardError as exc:
            raise CurrentReadHashEvidenceError(
                exc.validation_code,
                exc.next_action,
            ) from exc

        try:
            with evidence.checked_path.open("rb", buffering=0) as stream:
                started = os.fstat(stream.fileno())
                _validate_regular_file(
                    started,
                    expected_size_bytes=request.expected_size_bytes,
                )
                content_hasher = blake3()
                while True:
                    chunk = stream.read(self._chunk_bytes)
                    if not chunk:
                        break
                    content_hasher.update(chunk)
                completed = os.fstat(stream.fileno())
        except CurrentReadHashEvidenceError:
            raise
        except OSError as exc:
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_FILE_UNREADABLE",
                "Close applications using the file and retry the backup check.",
            ) from exc

        started_fingerprint = stable_file_identity_hash(started)
        completed_fingerprint = stable_file_identity_hash(completed)
        if started_fingerprint != completed_fingerprint:
            raise CurrentReadHashEvidenceError(
                "SOURCE_CHANGED_DURING_HASH",
                "Wait for the file to stop changing and retry the backup check.",
            )
        return CurrentReadHashEvidence(
            snapshot_id=request.snapshot_id,
            entry_id=request.entry_id,
            endpoint_id=request.endpoint_id,
            content_hash=content_hasher.hexdigest(),
            size_bytes=request.expected_size_bytes,
            algorithm=CURRENT_READ_HASH_ALGORITHM,
            hash_schema_version=CURRENT_READ_HASH_SCHEMA_VERSION,
            evidence_kind=HashEvidenceKind.CURRENT_READ_HASH,
            read_started_fingerprint_hash=started_fingerprint,
            read_completed_fingerprint_hash=completed_fingerprint,
            computed_utc=request.computed_utc,
        )


def _validate_regular_file(
    value: os.stat_result,
    *,
    expected_size_bytes: int,
) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise CurrentReadHashEvidenceError(
            "CURRENT_READ_HASH_NOT_REGULAR_FILE",
            "Refresh the endpoint snapshot after resolving the file type change.",
        )
    if int(value.st_size) != expected_size_bytes:
        raise CurrentReadHashEvidenceError(
            "CURRENT_READ_HASH_SNAPSHOT_DRIFT",
            "Refresh the endpoint snapshot because the file size changed.",
        )
