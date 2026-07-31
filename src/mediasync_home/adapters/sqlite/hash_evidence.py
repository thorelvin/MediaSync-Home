from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mediasync_home.adapters.current_read_hash import (
    CurrentReadHashRequest,
    LocalCurrentReadHasher,
)
from mediasync_home.adapters.sqlite.endpoint_roots import local_path_from_file_uri
from mediasync_home.application.hash_evidence import (
    CurrentReadHashEvidence,
    CurrentReadHashEvidenceError,
    CurrentReadHashRefreshReport,
    CurrentReadHashRefreshState,
    HashEvidenceKind,
)


class SqliteCurrentReadHashEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _HashEntry:
    snapshot_id: str
    entry_id: str
    endpoint_id: str
    root: Path
    relative_path: str
    object_type: str
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class _HashEndpoint:
    endpoint_id: str
    role: str
    ordinal: int
    snapshot_id: str
    root_case_mode: str
    entries: tuple[_HashEntry, ...]


@dataclass(frozen=True, slots=True)
class _HashPair:
    source: _HashEntry
    target: _HashEntry


class SqliteCurrentReadHashEvidenceRefresher:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        hasher: LocalCurrentReadHasher | None = None,
    ) -> None:
        self._connection = connection
        self._hasher = hasher or LocalCurrentReadHasher()

    def refresh_current_read_hash_evidence(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> CurrentReadHashRefreshReport:
        if self._connection.in_transaction:
            raise SqliteCurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_REFRESH_REQUIRES_IDLE_CONNECTION"
            )
        try:
            endpoints = self._load_endpoints(analysis_id)
            pairs = _candidate_pairs(endpoints)
        except CurrentReadHashEvidenceError as exc:
            return _blocked_report(
                analysis_id,
                reason_code=exc.validation_code,
            )
        existing = self._load_existing_evidence(analysis_id)
        required_entries = {
            (entry.snapshot_id, entry.entry_id): entry
            for pair in pairs
            for entry in (pair.source, pair.target)
        }
        evidence = dict(existing)
        new_evidence: list[CurrentReadHashEvidence] = []
        try:
            for key, entry in sorted(required_entries.items()):
                if key in evidence:
                    continue
                hashed = self._hasher.hash_file(
                    CurrentReadHashRequest(
                        snapshot_id=entry.snapshot_id,
                        entry_id=entry.entry_id,
                        endpoint_id=entry.endpoint_id,
                        root=entry.root,
                        relative_path=entry.relative_path,
                        expected_size_bytes=_required_size(entry),
                        computed_utc=observed_utc,
                    )
                )
                evidence[key] = hashed
                new_evidence.append(hashed)
        except CurrentReadHashEvidenceError as exc:
            return _blocked_report(
                analysis_id,
                reason_code=exc.validation_code,
                candidate_pair_count=len(pairs),
                reused_entry_count=sum(
                    key in existing for key in required_entries
                ),
            )

        try:
            self._persist_evidence(
                analysis_id=analysis_id,
                evidence=tuple(new_evidence),
            )
        except sqlite3.Error as exc:
            raise SqliteCurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_PERSISTENCE_FAILED"
            ) from exc

        identical_pairs = sum(
            evidence[(pair.source.snapshot_id, pair.source.entry_id)].content_hash
            == evidence[(pair.target.snapshot_id, pair.target.entry_id)].content_hash
            for pair in pairs
        )
        return CurrentReadHashRefreshReport(
            analysis_id=analysis_id,
            state=CurrentReadHashRefreshState.READY,
            reason_code="CURRENT_READ_HASH_EVIDENCE_READY",
            candidate_pair_count=len(pairs),
            hashed_entry_count=len(new_evidence),
            reused_entry_count=len(required_entries) - len(new_evidence),
            identical_pair_count=identical_pairs,
            changed_pair_count=len(pairs) - identical_pairs,
        )

    def _load_endpoints(self, analysis_id: str) -> tuple[_HashEndpoint, ...]:
        rows = self._connection.execute(
            """
            SELECT
                bindings.endpoint_id,
                bindings.role,
                bindings.ordinal,
                snapshots.id,
                revisions.root_uri,
                root_coverage.case_mode,
                entries.id,
                entries.relative_path,
                entries.object_type,
                entries.size_bytes
            FROM snapshots
            INNER JOIN analyses
                ON analyses.id = snapshots.analysis_id
            INNER JOIN standard_backup_job_endpoint_bindings AS bindings
                ON bindings.job_id = analyses.job_id
                AND bindings.job_revision_id = analyses.job_revision_id
                AND bindings.endpoint_id = snapshots.endpoint_id
                AND bindings.endpoint_revision_id = snapshots.endpoint_revision_id
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = snapshots.endpoint_id
                AND revisions.id = snapshots.endpoint_revision_id
            INNER JOIN directory_coverage AS root_coverage
                ON root_coverage.snapshot_id = snapshots.id
                AND root_coverage.relative_path = '.'
            LEFT JOIN file_entries AS entries
                ON entries.snapshot_id = snapshots.id
            WHERE snapshots.analysis_id = ?
                AND snapshots.complete = 1
                AND snapshots.immutable = 1
            ORDER BY
                CASE bindings.role WHEN 'SOURCE' THEN 0 ELSE 1 END,
                bindings.ordinal,
                bindings.endpoint_id,
                entries.comparison_key,
                entries.relative_path,
                entries.id
            """,
            (analysis_id,),
        ).fetchall()
        grouped: list[_HashEndpoint] = []
        current: tuple[str, str, int, str, str, str] | None = None
        entries: list[_HashEntry] = []
        for row in rows:
            key = (
                str(row[0]),
                str(row[1]),
                _required_int(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
            )
            if current is not None and key != current:
                grouped.append(_endpoint_from_group(current, entries))
                entries = []
            current = key
            if row[6] is not None:
                entries.append(
                    _HashEntry(
                        snapshot_id=str(row[3]),
                        entry_id=str(row[6]),
                        endpoint_id=str(row[0]),
                        root=local_path_from_file_uri(str(row[4])),
                        relative_path=str(row[7]),
                        object_type=str(row[8]),
                        size_bytes=None if row[9] is None else _required_int(row[9]),
                    )
                )
        if current is not None:
            grouped.append(_endpoint_from_group(current, entries))
        if not grouped:
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_ANALYSIS_NOT_READY",
                "Refresh sealed endpoint snapshots before comparing file contents.",
            )
        return tuple(grouped)

    def _load_existing_evidence(
        self,
        analysis_id: str,
    ) -> dict[tuple[str, str], CurrentReadHashEvidence]:
        rows = self._connection.execute(
            """
            SELECT
                evidence.snapshot_id,
                evidence.entry_id,
                evidence.endpoint_id,
                evidence.content_hash,
                evidence.size_bytes,
                evidence.algorithm,
                evidence.hash_schema_version,
                evidence.evidence_kind,
                evidence.read_started_fingerprint_hash,
                evidence.read_completed_fingerprint_hash,
                evidence.computed_utc
            FROM current_read_hash_evidence AS evidence
            INNER JOIN snapshots
                ON snapshots.id = evidence.snapshot_id
            WHERE snapshots.analysis_id = ?
            """,
            (analysis_id,),
        ).fetchall()
        result: dict[tuple[str, str], CurrentReadHashEvidence] = {}
        for row in rows:
            item = CurrentReadHashEvidence(
                snapshot_id=str(row[0]),
                entry_id=str(row[1]),
                endpoint_id=str(row[2]),
                content_hash=str(row[3]),
                size_bytes=_required_int(row[4]),
                algorithm=str(row[5]),
                hash_schema_version=_required_int(row[6]),
                evidence_kind=HashEvidenceKind(str(row[7])),
                read_started_fingerprint_hash=str(row[8]),
                read_completed_fingerprint_hash=str(row[9]),
                computed_utc=str(row[10]),
            )
            result[(item.snapshot_id, item.entry_id)] = item
        return result

    def _persist_evidence(
        self,
        *,
        analysis_id: str,
        evidence: tuple[CurrentReadHashEvidence, ...],
    ) -> None:
        if not evidence:
            return
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                """
                SELECT count(*)
                FROM snapshots
                WHERE analysis_id = ?
                    AND complete = 1
                    AND immutable = 1
                """,
                (analysis_id,),
            ).fetchone()
            if row is None or _required_int(row[0]) < 1:
                raise SqliteCurrentReadHashEvidenceError(
                    "CURRENT_READ_HASH_ANALYSIS_CHANGED"
                )
            self._connection.executemany(
                """
                INSERT INTO current_read_hash_evidence (
                    snapshot_id,
                    entry_id,
                    endpoint_id,
                    content_hash,
                    size_bytes,
                    algorithm,
                    hash_schema_version,
                    evidence_kind,
                    read_started_fingerprint_hash,
                    read_completed_fingerprint_hash,
                    computed_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (snapshot_id, entry_id)
                DO NOTHING
                """,
                tuple(
                    (
                        item.snapshot_id,
                        item.entry_id,
                        item.endpoint_id,
                        item.content_hash,
                        item.size_bytes,
                        item.algorithm,
                        item.hash_schema_version,
                        item.evidence_kind.value,
                        item.read_started_fingerprint_hash,
                        item.read_completed_fingerprint_hash,
                        item.computed_utc,
                    )
                    for item in evidence
                ),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise


def _candidate_pairs(
    endpoints: tuple[_HashEndpoint, ...],
) -> tuple[_HashPair, ...]:
    sources = tuple(endpoint for endpoint in endpoints if endpoint.role == "SOURCE")
    targets = tuple(endpoint for endpoint in endpoints if endpoint.role == "TARGET")
    if len(sources) != 1 or not targets:
        raise CurrentReadHashEvidenceError(
            "CURRENT_READ_HASH_ENDPOINT_SET_INVALID",
            "Refresh the backup endpoint bindings before comparing file contents.",
        )
    source = sources[0]
    pairs: list[_HashPair] = []
    for target in targets:
        if target.root_case_mode not in {"CASE_SENSITIVE", "CASE_INSENSITIVE"}:
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_CASE_CONTEXT_UNKNOWN",
                "Refresh target case evidence before comparing file contents.",
            )
        source_entries = _entries_by_target_key(
            source.entries,
            target_case_mode=target.root_case_mode,
        )
        target_entries = _entries_by_target_key(
            target.entries,
            target_case_mode=target.root_case_mode,
        )
        for key, source_entry in source_entries.items():
            target_entry = target_entries.get(key)
            if (
                target_entry is None
                or source_entry.object_type != "file"
                or target_entry.object_type != "file"
                or source_entry.size_bytes is None
                or source_entry.size_bytes != target_entry.size_bytes
            ):
                continue
            pairs.append(_HashPair(source=source_entry, target=target_entry))
    return tuple(pairs)


def _entries_by_target_key(
    entries: tuple[_HashEntry, ...],
    *,
    target_case_mode: str,
) -> dict[str, _HashEntry]:
    result: dict[str, _HashEntry] = {}
    for entry in entries:
        key = _target_comparison_key(entry.relative_path, target_case_mode)
        if key in result:
            raise CurrentReadHashEvidenceError(
                "CURRENT_READ_HASH_CASE_COLLISION",
                "Resolve case-colliding paths before comparing file contents.",
            )
        result[key] = entry
    return result


def _target_comparison_key(relative_path: str, target_case_mode: str) -> str:
    parts = relative_path.replace("\\", "/").split("/")
    if target_case_mode == "CASE_INSENSITIVE":
        parts = [part.casefold() for part in parts]
    return "/".join(parts)


def _endpoint_from_group(
    key: tuple[str, str, int, str, str, str],
    entries: list[_HashEntry],
) -> _HashEndpoint:
    return _HashEndpoint(
        endpoint_id=key[0],
        role=key[1],
        ordinal=key[2],
        snapshot_id=key[3],
        root_case_mode=key[5],
        entries=tuple(entries),
    )


def _required_size(entry: _HashEntry) -> int:
    if entry.size_bytes is None or entry.size_bytes < 0:
        raise CurrentReadHashEvidenceError(
            "CURRENT_READ_HASH_SIZE_INVALID",
            "Refresh the endpoint snapshot before comparing file contents.",
        )
    return entry.size_bytes


def _required_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("current-read hash integer column is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("current-read hash integer column is invalid")


def _blocked_report(
    analysis_id: str,
    *,
    reason_code: str,
    candidate_pair_count: int = 0,
    reused_entry_count: int = 0,
) -> CurrentReadHashRefreshReport:
    return CurrentReadHashRefreshReport(
        analysis_id=analysis_id,
        state=CurrentReadHashRefreshState.BLOCKED,
        reason_code=reason_code,
        candidate_pair_count=candidate_pair_count,
        hashed_entry_count=0,
        reused_entry_count=reused_entry_count,
        identical_pair_count=0,
        changed_pair_count=0,
    )
