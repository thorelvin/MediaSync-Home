from __future__ import annotations

import sqlite3

from mediasync_home.application.hash_cache import (
    HASH_CACHE_MAX_ACTIVE_ROWS,
    HashCacheEvidenceKind,
    HashCacheIdentity,
    HashCacheRecord,
    HashCacheWriteReport,
    HashCacheWriteState,
)


HASH_CACHE_MAX_HISTORY_PER_IDENTITY = 2


class SqliteHashCacheError(RuntimeError):
    pass


class SqliteHashCacheStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        max_active_rows: int = HASH_CACHE_MAX_ACTIVE_ROWS,
        max_history_per_identity: int = HASH_CACHE_MAX_HISTORY_PER_IDENTITY,
    ) -> None:
        if not 1 <= max_active_rows <= HASH_CACHE_MAX_ACTIVE_ROWS:
            raise ValueError("hash-cache active row limit is invalid")
        if max_history_per_identity < 0:
            raise ValueError("hash-cache history limit is invalid")
        self._connection = connection
        self._max_active_rows = max_active_rows
        self._max_history_per_identity = max_history_per_identity

    def persist_evidence(self, record: HashCacheRecord) -> HashCacheWriteReport:
        if self._connection.in_transaction:
            raise SqliteHashCacheError("HASH_CACHE_WRITE_REQUIRES_IDLE_CONNECTION")
        identity_hash = record.identity.identity_hash
        evidence_hash = record.evidence_hash
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            replay = self._connection.execute(
                """
                SELECT id, evidence_generation
                FROM hash_cache
                WHERE cache_identity_hash = ?
                    AND evidence_hash = ?
                """,
                (identity_hash, evidence_hash),
            ).fetchone()
            active = self._load_active_row(identity_hash)
            if replay is not None:
                self._connection.execute("COMMIT")
                return HashCacheWriteReport(
                    state=HashCacheWriteState.REPLAYED,
                    cache_identity_hash=identity_hash,
                    evidence_hash=evidence_hash,
                    record_id=_required_int(replay[0]),
                    evidence_generation=_required_int(replay[1]),
                    active_evidence_kind=(
                        None
                        if active is None
                        else HashCacheEvidenceKind(str(active[1]))
                    ),
                )

            if active is None and self._active_count() >= self._max_active_rows:
                self._connection.execute("COMMIT")
                return HashCacheWriteReport(
                    state=HashCacheWriteState.CAPACITY_REJECTED,
                    cache_identity_hash=identity_hash,
                    evidence_hash=evidence_hash,
                    record_id=None,
                    evidence_generation=None,
                    active_evidence_kind=None,
                )

            next_generation = self._next_generation(identity_hash)
            if active is None:
                record_id = self._insert(
                    record,
                    evidence_generation=next_generation,
                    active=True,
                )
                state = HashCacheWriteState.INSERTED
                active_kind = record.evidence_kind
            else:
                active_kind = HashCacheEvidenceKind(str(active[1]))
                strength = _evidence_strength(record.evidence_kind)
                active_strength = _evidence_strength(active_kind)
                if strength == active_strength:
                    raise SqliteHashCacheError(
                        "HASH_CACHE_EQUAL_STRENGTH_CONFLICT"
                    )
                if strength > active_strength:
                    self._connection.execute(
                        "UPDATE hash_cache SET active = 0 WHERE id = ?",
                        (_required_int(active[0]),),
                    )
                    record_id = self._insert(
                        record,
                        evidence_generation=next_generation,
                        active=True,
                    )
                    state = HashCacheWriteState.PROMOTED
                    active_kind = record.evidence_kind
                else:
                    record_id = self._insert(
                        record,
                        evidence_generation=next_generation,
                        active=False,
                    )
                    state = HashCacheWriteState.RETAINED_STRONGER
            self._prune_history(identity_hash)
            self._connection.execute("COMMIT")
            return HashCacheWriteReport(
                state=state,
                cache_identity_hash=identity_hash,
                evidence_hash=evidence_hash,
                record_id=record_id,
                evidence_generation=next_generation,
                active_evidence_kind=active_kind,
            )
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def load_active(self, identity: HashCacheIdentity) -> HashCacheRecord | None:
        row = self._connection.execute(
            f"""
            SELECT {_HASH_CACHE_RECORD_COLUMNS}
            FROM hash_cache
            WHERE cache_identity_hash = ?
                AND active = 1
            """,
            (identity.identity_hash,),
        ).fetchone()
        if row is None:
            return None
        record = _record_from_row(row)
        if record.identity != identity:
            raise SqliteHashCacheError("HASH_CACHE_IDENTITY_HASH_COLLISION")
        return record

    def load_active_by_identity_hash(
        self,
        identity_hash: str,
    ) -> HashCacheRecord | None:
        row = self._connection.execute(
            f"""
            SELECT {_HASH_CACHE_RECORD_COLUMNS}
            FROM hash_cache
            WHERE cache_identity_hash = ?
                AND active = 1
            """,
            (identity_hash,),
        ).fetchone()
        if row is None:
            return None
        record = _record_from_row(row)
        if record.identity.identity_hash != identity_hash:
            raise SqliteHashCacheError("HASH_CACHE_IDENTITY_HASH_COLLISION")
        return record

    def load_reusable_quick_signature(
        self,
        *,
        endpoint_id: str,
        endpoint_generation: int,
        relative_path: str,
        comparison_key: str,
        comparison_key_version: int,
        parent_case_context_hash: str,
        size_bytes: int,
        birthtime_ns: int | None,
        identity_fingerprint_hash: str,
    ) -> HashCacheRecord | None:
        rows = self._connection.execute(
            f"""
            SELECT {_HASH_CACHE_RECORD_COLUMNS}
            FROM hash_cache
            WHERE endpoint_id = ?
                AND endpoint_generation = ?
                AND relative_path = ?
                AND comparison_key = ?
                AND comparison_key_version = ?
                AND parent_case_context_hash = ?
                AND entry_type = 'file'
                AND size_bytes = ?
                AND birthtime_ns IS ?
                AND read_started_fingerprint_hash = ?
                AND read_completed_fingerprint_hash = ?
                AND quick_hash IS NOT NULL
                AND signature_schema_version = 1
                AND active = 1
            ORDER BY evidence_generation DESC, id DESC
            LIMIT 2
            """,
            (
                endpoint_id,
                endpoint_generation,
                relative_path,
                comparison_key,
                comparison_key_version,
                parent_case_context_hash,
                size_bytes,
                birthtime_ns,
                identity_fingerprint_hash,
                identity_fingerprint_hash,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise SqliteHashCacheError("HASH_CACHE_REUSE_AMBIGUOUS")
        return None if not rows else _record_from_row(tuple(rows[0]))

    def _load_active_row(self, identity_hash: str) -> tuple[object, ...] | None:
        row = self._connection.execute(
            """
            SELECT id, evidence_kind, evidence_generation, evidence_hash
            FROM hash_cache
            WHERE cache_identity_hash = ?
                AND active = 1
            """,
            (identity_hash,),
        ).fetchone()
        return None if row is None else tuple(row)

    def _active_count(self) -> int:
        row = self._connection.execute(
            "SELECT count(*) FROM hash_cache WHERE active = 1"
        ).fetchone()
        return 0 if row is None else _required_int(row[0])

    def _next_generation(self, identity_hash: str) -> int:
        row = self._connection.execute(
            """
            SELECT COALESCE(max(evidence_generation), 0) + 1
            FROM hash_cache
            WHERE cache_identity_hash = ?
            """,
            (identity_hash,),
        ).fetchone()
        if row is None:
            raise SqliteHashCacheError("HASH_CACHE_GENERATION_QUERY_FAILED")
        return _required_int(row[0])

    def _insert(
        self,
        record: HashCacheRecord,
        *,
        evidence_generation: int,
        active: bool,
    ) -> int:
        identity = record.identity
        cursor = self._connection.execute(
            """
            INSERT INTO hash_cache (
                cache_identity_hash,
                evidence_hash,
                endpoint_id,
                endpoint_generation,
                volume_identity,
                relative_path,
                comparison_key,
                comparison_key_version,
                parent_case_context_hash,
                entry_type,
                size_bytes,
                mtime_ns,
                birthtime_ns,
                attributes,
                reparse_tag,
                file_id,
                file_id_reliability,
                link_count,
                quick_hash,
                full_hash,
                algorithm,
                evidence_kind,
                hash_schema_version,
                signature_schema_version,
                read_started_fingerprint_hash,
                read_completed_fingerprint_hash,
                usn_journal_id,
                usn_first_record,
                usn_last_record,
                evidence_generation,
                active,
                computed_utc
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                identity.identity_hash,
                record.evidence_hash,
                identity.endpoint_id,
                identity.endpoint_generation,
                identity.volume_identity,
                identity.relative_path,
                identity.comparison_key,
                identity.comparison_key_version,
                identity.parent_case_context_hash,
                identity.entry_type,
                identity.size_bytes,
                identity.mtime_ns,
                identity.birthtime_ns,
                identity.attributes,
                identity.reparse_tag,
                identity.file_id,
                identity.file_id_reliability,
                identity.link_count,
                record.quick_hash,
                record.full_hash,
                record.algorithm,
                record.evidence_kind.value,
                record.hash_schema_version,
                record.signature_schema_version,
                record.read_started_fingerprint_hash,
                record.read_completed_fingerprint_hash,
                record.usn_journal_id,
                record.usn_first_record,
                record.usn_last_record,
                evidence_generation,
                int(active),
                record.computed_utc,
            ),
        )
        if cursor.lastrowid is None:
            raise SqliteHashCacheError("HASH_CACHE_INSERT_ID_MISSING")
        return int(cursor.lastrowid)

    def _prune_history(self, identity_hash: str) -> None:
        rows = self._connection.execute(
            """
            SELECT id
            FROM hash_cache
            WHERE cache_identity_hash = ?
                AND active = 0
            ORDER BY evidence_generation DESC, id DESC
            LIMIT -1 OFFSET ?
            """,
            (identity_hash, self._max_history_per_identity),
        ).fetchall()
        self._connection.executemany(
            "DELETE FROM hash_cache WHERE id = ?",
            ((row[0],) for row in rows),
        )


_HASH_CACHE_RECORD_COLUMNS = """
    endpoint_id,
    endpoint_generation,
    volume_identity,
    relative_path,
    comparison_key,
    comparison_key_version,
    parent_case_context_hash,
    entry_type,
    size_bytes,
    mtime_ns,
    birthtime_ns,
    attributes,
    reparse_tag,
    file_id,
    file_id_reliability,
    link_count,
    evidence_kind,
    evidence_generation,
    computed_utc,
    quick_hash,
    full_hash,
    algorithm,
    hash_schema_version,
    signature_schema_version,
    read_started_fingerprint_hash,
    read_completed_fingerprint_hash,
    usn_journal_id,
    usn_first_record,
    usn_last_record
"""


def _record_from_row(row: tuple[object, ...]) -> HashCacheRecord:
    identity = HashCacheIdentity(
        endpoint_id=str(row[0]),
        endpoint_generation=_required_int(row[1]),
        volume_identity=_optional_text(row[2]),
        relative_path=str(row[3]),
        comparison_key=str(row[4]),
        comparison_key_version=_required_int(row[5]),
        parent_case_context_hash=str(row[6]),
        entry_type=str(row[7]),
        size_bytes=_required_int(row[8]),
        mtime_ns=_required_int(row[9]),
        birthtime_ns=_optional_int(row[10]),
        attributes=_optional_int(row[11]),
        reparse_tag=_optional_int(row[12]),
        file_id=_optional_text(row[13]),
        file_id_reliability=str(row[14]),
        link_count=_optional_int(row[15]),
    )
    return HashCacheRecord(
        identity=identity,
        evidence_kind=HashCacheEvidenceKind(str(row[16])),
        evidence_generation=_required_int(row[17]),
        computed_utc=str(row[18]),
        quick_hash=_optional_text(row[19]),
        full_hash=_optional_text(row[20]),
        algorithm=str(row[21]),
        hash_schema_version=_required_int(row[22]),
        signature_schema_version=_optional_int(row[23]),
        read_started_fingerprint_hash=_optional_text(row[24]),
        read_completed_fingerprint_hash=_optional_text(row[25]),
        usn_journal_id=_optional_text(row[26]),
        usn_first_record=_optional_text(row[27]),
        usn_last_record=_optional_text(row[28]),
    )


def _evidence_strength(kind: HashCacheEvidenceKind) -> int:
    return {
        HashCacheEvidenceKind.STALE_HASH_HINT: 0,
        HashCacheEvidenceKind.QUICK_SIGNATURE_ONLY: 1,
        HashCacheEvidenceKind.METADATA_REVALIDATED_CACHED_HASH: 2,
        HashCacheEvidenceKind.USN_CONTINUITY_VALIDATED_HASH: 3,
        HashCacheEvidenceKind.CURRENT_READ_HASH: 4,
    }[kind]


def _required_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("hash-cache integer column is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("hash-cache integer column is invalid")


def _optional_int(value: object) -> int | None:
    return None if value is None else _required_int(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)
