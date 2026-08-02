from __future__ import annotations

import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from blake3 import blake3

from mediasync_home.adapters.file_identity import (
    file_birthtime_ns,
    stable_file_identity_hash,
)
from mediasync_home.adapters.reparse_guard import (
    LocalReparseGuard,
    ReparseGuard,
    ReparseGuardError,
)
from mediasync_home.application.hash_cache import (
    QUICK_SIGNATURE_SCHEMA_VERSION,
    QUICK_SIGNATURE_SEGMENT_BYTES,
    HashCacheEvidenceError,
    QuickSignatureEvidence,
    quick_signature_segments,
)
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)


QUICK_SIGNATURE_READ_CHUNK_BYTES = 256 * 1024
_CANONICAL_PREFIX = b"MediaSyncHome.quick-signature\x00"


@dataclass(frozen=True, slots=True)
class QuickSignatureRequest:
    snapshot_id: str
    entry_id: str
    endpoint_id: str
    root: Path
    relative_path: str
    expected_size_bytes: int
    computed_utc: str


class LocalQuickSignatureHasher:
    def __init__(
        self,
        *,
        reparse_guard: ReparseGuard | None = None,
        chunk_bytes: int = QUICK_SIGNATURE_READ_CHUNK_BYTES,
    ) -> None:
        if not 1 <= chunk_bytes <= QUICK_SIGNATURE_SEGMENT_BYTES:
            raise ValueError("quick-signature chunk size is invalid")
        self._reparse_guard = reparse_guard or LocalReparseGuard()
        self._chunk_bytes = chunk_bytes

    def hash_file(self, request: QuickSignatureRequest) -> QuickSignatureEvidence:
        if request.expected_size_bytes < 0:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SIZE_INVALID")
        checked_path = self._checked_path(request)
        segments = quick_signature_segments(request.expected_size_bytes)
        signature = blake3()
        signature.update(_CANONICAL_PREFIX)
        signature.update(
            struct.pack(
                ">IQQ",
                QUICK_SIGNATURE_SCHEMA_VERSION,
                request.expected_size_bytes,
                len(segments),
            )
        )
        try:
            with checked_path.open("rb", buffering=0) as stream:
                started = os.fstat(stream.fileno())
                _validate_regular_file(
                    started,
                    expected_size_bytes=request.expected_size_bytes,
                )
                birthtime_ns = file_birthtime_ns(
                    checked_path,
                    stat_result=started,
                )
                reparse_tag = getattr(started, "st_reparse_tag", None)
                for segment in segments:
                    signature.update(struct.pack(">QQ", segment.offset, segment.length))
                    stream.seek(segment.offset)
                    remaining = segment.length
                    while remaining:
                        chunk = stream.read(min(self._chunk_bytes, remaining))
                        if not chunk:
                            raise HashCacheEvidenceError(
                                "QUICK_SIGNATURE_SOURCE_CHANGED"
                            )
                        signature.update(chunk)
                        remaining -= len(chunk)
                completed = os.fstat(stream.fileno())
        except HashCacheEvidenceError:
            raise
        except OSError as exc:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_FILE_UNREADABLE") from exc

        started_fingerprint = stable_file_identity_hash(started)
        completed_fingerprint = stable_file_identity_hash(completed)
        if started_fingerprint != completed_fingerprint:
            raise HashCacheEvidenceError("QUICK_SIGNATURE_SOURCE_CHANGED")
        return QuickSignatureEvidence(
            snapshot_id=request.snapshot_id,
            entry_id=request.entry_id,
            endpoint_id=request.endpoint_id,
            signature_hash=signature.hexdigest(),
            size_bytes=request.expected_size_bytes,
            signature_schema_version=QUICK_SIGNATURE_SCHEMA_VERSION,
            segments=segments,
            read_started_fingerprint_hash=started_fingerprint,
            read_completed_fingerprint_hash=completed_fingerprint,
            volume_identity=str(int(started.st_dev)),
            file_id=(
                None if int(started.st_ino) <= 0 else f"{int(started.st_ino):032x}"
            ),
            mtime_ns=int(started.st_mtime_ns),
            birthtime_ns=birthtime_ns,
            attributes=int(getattr(started, "st_file_attributes", 0)),
            reparse_tag=None if reparse_tag is None else int(reparse_tag),
            link_count=int(started.st_nlink),
            computed_utc=request.computed_utc,
        )

    def _checked_path(self, request: QuickSignatureRequest) -> Path:
        try:
            relative = parse_endpoint_relative_path(request.relative_path)
            root = self._reparse_guard.resolve_existing_root(
                request.root,
                missing_code="QUICK_SIGNATURE_ROOT_MISSING",
                missing_next_action="Reconnect the endpoint and retry the scan.",
                reparse_code="QUICK_SIGNATURE_ROOT_REPARSE_UNSUPPORTED",
                reparse_next_action="Use an ordinary non-reparse endpoint root.",
            )
            evidence = self._reparse_guard.reject_reparse_chain(
                root=root,
                relative_parts=relative.parts,
                missing_code="QUICK_SIGNATURE_FILE_MISSING",
                missing_next_action="Refresh the endpoint snapshot and retry the scan.",
                reparse_code="QUICK_SIGNATURE_REPARSE_UNSUPPORTED",
                reparse_next_action="Remove the reparse path from this backup job.",
            )
            self._reparse_guard.require_resolved_under_root(
                root=root,
                path=evidence.checked_path,
                strict=True,
                escape_code="QUICK_SIGNATURE_PATH_ESCAPED_ROOT",
                escape_next_action="Refresh the endpoint after correcting its path chain.",
            )
            return evidence.checked_path
        except SafePathViolation as exc:
            raise HashCacheEvidenceError(exc.validation_code) from exc
        except ReparseGuardError as exc:
            raise HashCacheEvidenceError(exc.validation_code) from exc


def _validate_regular_file(
    value: os.stat_result,
    *,
    expected_size_bytes: int,
) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise HashCacheEvidenceError("QUICK_SIGNATURE_NOT_REGULAR_FILE")
    if int(value.st_size) != expected_size_bytes:
        raise HashCacheEvidenceError("QUICK_SIGNATURE_SNAPSHOT_DRIFT")
