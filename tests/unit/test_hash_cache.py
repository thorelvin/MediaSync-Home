from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.hash_cache import (
    HASH_CACHE_ALGORITHM,
    HASH_CACHE_HASH_SCHEMA_VERSION,
    QUICK_SIGNATURE_SCHEMA_VERSION,
    HashCacheEvidenceError,
    HashCacheEvidenceKind,
    HashCacheIdentity,
    HashCacheRecord,
    compatible_strong_cache_evidence,
)


def test_only_strong_compatible_cache_evidence_can_skip_identical() -> None:
    left = _record(HashCacheEvidenceKind.CURRENT_READ_HASH)
    right = replace(
        left,
        identity=replace(left.identity, endpoint_id="target-a"),
    )

    assert compatible_strong_cache_evidence(
        left,
        right,
        expected_size_bytes=128,
    )
    for weak_kind in (
        HashCacheEvidenceKind.METADATA_REVALIDATED_CACHED_HASH,
        HashCacheEvidenceKind.STALE_HASH_HINT,
    ):
        assert not compatible_strong_cache_evidence(
            replace(left, evidence_kind=weak_kind),
            right,
            expected_size_bytes=128,
        )


def test_quick_signature_only_requires_versioned_hint_without_full_hash() -> None:
    record = HashCacheRecord(
        identity=_identity(),
        evidence_kind=HashCacheEvidenceKind.QUICK_SIGNATURE_ONLY,
        evidence_generation=1,
        computed_utc="2026-08-02T10:00:00Z",
        quick_hash="b" * 64,
        signature_schema_version=QUICK_SIGNATURE_SCHEMA_VERSION,
        read_started_fingerprint_hash="c" * 64,
        read_completed_fingerprint_hash="c" * 64,
    )

    assert record.can_drive_skip_identical is False
    with pytest.raises(
        HashCacheEvidenceError,
        match="HASH_CACHE_QUICK_EVIDENCE_INVALID",
    ):
        replace(record, full_hash="a" * 64)


def _record(kind: HashCacheEvidenceKind) -> HashCacheRecord:
    return HashCacheRecord(
        identity=_identity(),
        evidence_kind=kind,
        evidence_generation=1,
        computed_utc="2026-08-02T10:00:00Z",
        full_hash="a" * 64,
        algorithm=HASH_CACHE_ALGORITHM,
        hash_schema_version=HASH_CACHE_HASH_SCHEMA_VERSION,
        read_started_fingerprint_hash="c" * 64,
        read_completed_fingerprint_hash="c" * 64,
    )


def _identity() -> HashCacheIdentity:
    return HashCacheIdentity(
        endpoint_id="source-a",
        endpoint_generation=1,
        volume_identity="volume-a",
        relative_path="Photos/A.jpg",
        comparison_key="photos/a.jpg",
        comparison_key_version=1,
        parent_case_context_hash="d" * 64,
        entry_type="file",
        size_bytes=128,
        mtime_ns=100,
        birthtime_ns=50,
        attributes=0,
        reparse_tag=None,
        file_id="file-a",
        file_id_reliability="stable",
        link_count=1,
    )
