from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.hash_cache import (
    SqliteHashCacheError,
    SqliteHashCacheStore,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.application.hash_cache import (
    QUICK_SIGNATURE_SCHEMA_VERSION,
    HashCacheEvidenceKind,
    HashCacheIdentity,
    HashCacheRecord,
    HashCacheWriteState,
)


def test_hash_cache_promotes_strong_evidence_and_bounds_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_endpoint(connection)
        store = SqliteHashCacheStore(connection, max_history_per_identity=1)
        quick = _quick_record(_identity())

        inserted = store.persist_evidence(quick)
        replayed = store.persist_evidence(
            replace(quick, computed_utc="2026-08-02T10:00:01Z")
        )
        current = _current_record(quick.identity)
        promoted = store.persist_evidence(current)
        retained = store.persist_evidence(
            replace(
                current,
                evidence_kind=(
                    HashCacheEvidenceKind.METADATA_REVALIDATED_CACHED_HASH
                ),
                computed_utc="2026-08-02T10:00:02Z",
            )
        )

        assert inserted.state is HashCacheWriteState.INSERTED
        assert replayed.state is HashCacheWriteState.REPLAYED
        assert replayed.record_id == inserted.record_id
        assert promoted.state is HashCacheWriteState.PROMOTED
        assert promoted.evidence_generation == 2
        assert retained.state is HashCacheWriteState.RETAINED_STRONGER
        assert retained.active_evidence_kind is HashCacheEvidenceKind.CURRENT_READ_HASH
        active = store.load_active(quick.identity)
        assert active is not None
        assert active.evidence_kind is HashCacheEvidenceKind.CURRENT_READ_HASH
        assert active.evidence_generation == 2
        assert connection.execute(
            """
            SELECT evidence_kind, evidence_generation, active
            FROM hash_cache
            ORDER BY evidence_generation
            """
        ).fetchall() == [
            ("CURRENT_READ_HASH", 2, 1),
            ("METADATA_REVALIDATED_CACHED_HASH", 3, 0),
        ]
        with pytest.raises(
            sqlite3.IntegrityError,
            match="HASH_CACHE_EVIDENCE_IMMUTABLE",
        ):
            connection.execute(
                "UPDATE hash_cache SET full_hash = ? WHERE active = 1",
                ("f" * 64,),
            )


def test_hash_cache_rejects_equal_strength_conflict_and_active_overflow(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_endpoint(connection)
        store = SqliteHashCacheStore(connection, max_active_rows=1)
        identity = _identity()
        current = _current_record(identity)
        store.persist_evidence(current)

        with pytest.raises(
            SqliteHashCacheError,
            match="HASH_CACHE_EQUAL_STRENGTH_CONFLICT",
        ):
            store.persist_evidence(replace(current, full_hash="e" * 64))
        overflow = store.persist_evidence(
            _quick_record(replace(identity, relative_path="B.bin"))
        )

        assert overflow.state is HashCacheWriteState.CAPACITY_REJECTED
        assert connection.execute(
            "SELECT count(*) FROM hash_cache WHERE active = 1"
        ).fetchone() == (1,)
        active = store.load_active(identity)
        assert active is not None
        assert active.full_hash == "a" * 64


def _prepare_endpoint(connection: sqlite3.Connection) -> None:
    apply_sqlite_migrations(connection, catalog_migration_plan())
    connection.execute("INSERT INTO endpoints (id) VALUES ('source-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (
            endpoint_id,
            id,
            display_name,
            root_uri,
            generation
        )
        VALUES ('source-a', 'source-rev-a', 'Source', 'file:///source', 1)
        """
    )
    connection.commit()


def _identity() -> HashCacheIdentity:
    return HashCacheIdentity(
        endpoint_id="source-a",
        endpoint_generation=1,
        volume_identity="volume-a",
        relative_path="A.bin",
        comparison_key="a.bin",
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


def _quick_record(identity: HashCacheIdentity) -> HashCacheRecord:
    return HashCacheRecord(
        identity=identity,
        evidence_kind=HashCacheEvidenceKind.QUICK_SIGNATURE_ONLY,
        evidence_generation=1,
        computed_utc="2026-08-02T10:00:00Z",
        quick_hash="b" * 64,
        signature_schema_version=QUICK_SIGNATURE_SCHEMA_VERSION,
        read_started_fingerprint_hash="c" * 64,
        read_completed_fingerprint_hash="c" * 64,
    )


def _current_record(identity: HashCacheIdentity) -> HashCacheRecord:
    return HashCacheRecord(
        identity=identity,
        evidence_kind=HashCacheEvidenceKind.CURRENT_READ_HASH,
        evidence_generation=1,
        computed_utc="2026-08-02T10:00:01Z",
        quick_hash="b" * 64,
        full_hash="a" * 64,
        signature_schema_version=QUICK_SIGNATURE_SCHEMA_VERSION,
        read_started_fingerprint_hash="c" * 64,
        read_completed_fingerprint_hash="c" * 64,
    )
