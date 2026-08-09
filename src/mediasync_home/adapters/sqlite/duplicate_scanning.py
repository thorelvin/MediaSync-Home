from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from mediasync_home.adapters.current_read_hash import (
    CurrentReadHashRequest,
    LocalCurrentReadHasher,
)
from mediasync_home.adapters.quick_signature import (
    LocalQuickSignatureHasher,
    QuickSignatureRequest,
)
from mediasync_home.adapters.sqlite.duplicates import (
    SqliteDuplicateRelationError,
    SqliteDuplicateRelationStore,
)
from mediasync_home.adapters.sqlite.endpoint_roots import local_path_from_file_uri
from mediasync_home.adapters.sqlite.hash_cache import (
    SqliteHashCacheStore,
)
from mediasync_home.adapters.sqlite.hash_evidence import (
    SqliteCurrentReadHashEvidenceRefresher,
)
from mediasync_home.application.duplicate_scanning import (
    DUPLICATE_GROUP_MAX_PAGE_SIZE,
    DUPLICATE_MEMBER_MAX_PAGE_SIZE,
    DUPLICATE_REPORT_MAX_PAGE_SIZE,
    DUPLICATE_SCAN_MAX_ACTIVE_JOBS,
    DUPLICATE_SCAN_MAX_ATTEMPTS_PER_FILE,
    DUPLICATE_SCAN_MAX_CANDIDATE_FILES,
    DUPLICATE_SCAN_MAX_HISTORY_ROWS,
    DUPLICATE_SCAN_WORK_BATCH_SIZE,
    DuplicateGroupCursor,
    DuplicateGroupPage,
    DuplicateGroupReadModel,
    DuplicateMemberCursor,
    DuplicateMemberPage,
    DuplicateMemberReadModel,
    DuplicateReportCursor,
    DuplicateReportPage,
    DuplicateReportRow,
    DuplicateScanCycleReport,
    DuplicateScanError,
    DuplicateScanStage,
    DuplicateScanState,
    DuplicateScanStatus,
    deterministic_duplicate_scan_id,
)
from mediasync_home.application.endpoint_capabilities import (
    EndpointCapabilities,
    EndpointCapabilityEvidenceError,
    FileIdReliability,
)
from mediasync_home.application.hash_cache import (
    HASH_CACHE_ALGORITHM,
    HASH_CACHE_HASH_SCHEMA_VERSION,
    QUICK_SIGNATURE_SCHEMA_VERSION,
    HashCacheEvidenceKind,
    HashCacheIdentity,
    HashCacheRecord,
    HashCacheWriteState,
)
from mediasync_home.application.hash_evidence import (
    CurrentReadHashEvidence,
    HashEvidenceKind,
)
from mediasync_home.application.safe_paths import parse_endpoint_relative_path


HASH_REQUEST_MAX_PERSISTED_ROWS = 1_000_000

_DUPLICATE_RELATION_CLASSES = {
    "EXPECTED_REPLICA",
    "INTRA_ENDPOINT_DUPLICATE",
    "UNRELATED_CROSS_ENDPOINT_DUPLICATE",
    "POTENTIAL_DUPLICATE",
}


class SqliteDuplicateScannerError(DuplicateScanError):
    pass


@dataclass(frozen=True, slots=True)
class _HashWorkItem:
    request_id: str
    scan_id: str
    analysis_id: str
    request_stage: str
    attempt_count: int
    snapshot_id: str
    endpoint_id: str
    entry_id: str
    physical_object_key: str
    relative_path: str
    comparison_key: str
    size_bytes: int
    birthtime_ns: int
    identity_fingerprint_hash: str
    endpoint_generation: int
    root: Path
    parent_case_context_hash: str
    file_id_reliability: str
    quick_cache_identity_hash: str | None
    quick_hash: str | None


class SqliteDuplicateScanner:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        relations: SqliteDuplicateRelationStore | None = None,
        cache: SqliteHashCacheStore | None = None,
        current_evidence: SqliteCurrentReadHashEvidenceRefresher | None = None,
        quick_hasher: LocalQuickSignatureHasher | None = None,
        full_hasher: LocalCurrentReadHasher | None = None,
        max_candidate_files: int = DUPLICATE_SCAN_MAX_CANDIDATE_FILES,
        max_active_jobs: int = DUPLICATE_SCAN_MAX_ACTIVE_JOBS,
        max_history_rows: int = DUPLICATE_SCAN_MAX_HISTORY_ROWS,
        max_persisted_requests: int = HASH_REQUEST_MAX_PERSISTED_ROWS,
        work_batch_size: int = DUPLICATE_SCAN_WORK_BATCH_SIZE,
    ) -> None:
        if not 1 <= max_candidate_files <= DUPLICATE_SCAN_MAX_CANDIDATE_FILES:
            raise ValueError("duplicate-scan candidate limit is invalid")
        if not 1 <= max_active_jobs <= DUPLICATE_SCAN_MAX_ACTIVE_JOBS:
            raise ValueError("duplicate-scan active-job limit is invalid")
        if not 1 <= max_history_rows <= DUPLICATE_SCAN_MAX_HISTORY_ROWS:
            raise ValueError("duplicate-scan history limit is invalid")
        if not 1 <= max_persisted_requests <= HASH_REQUEST_MAX_PERSISTED_ROWS:
            raise ValueError("hash-request row limit is invalid")
        if not 1 <= work_batch_size <= DUPLICATE_SCAN_WORK_BATCH_SIZE:
            raise ValueError("duplicate-scan work batch is invalid")
        self._connection = connection
        self._relations = relations or SqliteDuplicateRelationStore(connection)
        self._cache = cache or SqliteHashCacheStore(connection)
        self._current_evidence = current_evidence or (
            SqliteCurrentReadHashEvidenceRefresher(connection)
        )
        self._quick_hasher = quick_hasher or LocalQuickSignatureHasher()
        self._full_hasher = full_hasher or LocalCurrentReadHasher()
        self._max_candidate_files = max_candidate_files
        self._max_active_jobs = max_active_jobs
        self._max_history_rows = max_history_rows
        self._max_persisted_requests = max_persisted_requests
        self._work_batch_size = work_batch_size
        self._connection.create_function(
            "mediasync_parent_path",
            1,
            _parent_path,
            deterministic=True,
        )

    def recover_interrupted_requests(self, *, observed_utc: str) -> int:
        if not observed_utc.strip():
            raise ValueError("duplicate-scan recovery time is required")
        cursor = self._connection.execute(
            """
            UPDATE hash_requests
            SET
                state = 'PENDING',
                updated_utc = ?,
                completed_utc = NULL,
                last_error_code = 'HASH_REQUEST_INTERRUPTED'
            WHERE state = 'RUNNING'
            """,
            (observed_utc,),
        )
        self._connection.commit()
        return max(0, cursor.rowcount)

    def start_scan(
        self,
        *,
        analysis_id: str,
        requested_utc: str,
    ) -> DuplicateScanStatus:
        normalized_analysis_id = analysis_id.strip()
        if not normalized_analysis_id or not requested_utc.strip():
            raise DuplicateScanError("DUPLICATE_SCAN_REQUEST_INVALID")
        if self._connection.in_transaction:
            self._materialize_scan_relations(
                analysis_id=normalized_analysis_id,
                observed_utc=requested_utc,
            )
        else:
            self.prepare_scan(
                analysis_id=normalized_analysis_id,
                observed_utc=requested_utc,
            )
        scan_id = deterministic_duplicate_scan_id(normalized_analysis_id)
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_duplicate_scan(normalized_analysis_id)
            if existing is not None:
                if owns_transaction:
                    self._connection.execute("COMMIT")
                return existing
            if self._active_scan_count() >= self._max_active_jobs:
                raise SqliteDuplicateScannerError(
                    "DUPLICATE_SCAN_ACTIVE_LIMIT_REACHED"
                )
            candidate_count = self._count_candidate_files(normalized_analysis_id)
            if candidate_count > self._max_candidate_files:
                raise SqliteDuplicateScannerError(
                    "DUPLICATE_SCAN_CANDIDATE_LIMIT_EXCEEDED"
                )
            self._prune_scan_history()
            self._connection.execute(
                """
                INSERT INTO duplicate_scans (
                    id,
                    analysis_id,
                    state,
                    stage,
                    candidate_file_count,
                    requested_utc,
                    updated_utc
                )
                VALUES (?, ?, 'QUEUED', 'QUICK_SIGNATURE', ?, ?, ?)
                """,
                (
                    scan_id,
                    normalized_analysis_id,
                    candidate_count,
                    requested_utc,
                    requested_utc,
                ),
            )
            if owns_transaction:
                self._connection.execute("COMMIT")
        except Exception:
            if owns_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        status = self.load_duplicate_scan(normalized_analysis_id)
        if status is None:
            raise SqliteDuplicateScannerError("DUPLICATE_SCAN_CREATE_FAILED")
        return status

    def prepare_scan(self, *, analysis_id: str, observed_utc: str) -> None:
        if self._connection.in_transaction:
            raise SqliteDuplicateScannerError(
                "DUPLICATE_SCAN_PREPARATION_REQUIRES_IDLE_CONNECTION"
            )
        self._materialize_scan_relations(
            analysis_id=analysis_id,
            observed_utc=observed_utc,
        )

    def _materialize_scan_relations(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> None:
        try:
            self._relations.materialize_known_duplicate_relations(
                analysis_id=analysis_id,
                observed_utc=observed_utc,
            )
        except SqliteDuplicateRelationError as exc:
            raise SqliteDuplicateScannerError(
                "DUPLICATE_SCAN_ANALYSIS_NOT_READY"
            ) from exc

    def pause_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None:
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                UPDATE duplicate_scans
                SET
                    state = 'PAUSED',
                    reason_code = 'USER_REQUESTED',
                    updated_utc = ?
                WHERE analysis_id = ?
                    AND state IN ('QUEUED', 'RUNNING')
                """,
                (observed_utc, analysis_id),
            )
            status = self.load_duplicate_scan(analysis_id)
            if owns_transaction:
                self._connection.execute("COMMIT")
            return status
        except Exception:
            if owns_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def resume_scan(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateScanStatus | None:
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                UPDATE duplicate_scans
                SET
                    state = 'RUNNING',
                    reason_code = NULL,
                    started_utc = COALESCE(started_utc, ?),
                    updated_utc = ?
                WHERE analysis_id = ?
                    AND state = 'PAUSED'
                """,
                (observed_utc, observed_utc, analysis_id),
            )
            status = self.load_duplicate_scan(analysis_id)
            if owns_transaction:
                self._connection.execute("COMMIT")
            return status
        except Exception:
            if owns_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def run_cycle(
        self,
        *,
        observed_utc: str,
        active_backup: bool = False,
        max_files: int | None = None,
    ) -> DuplicateScanCycleReport:
        limit = self._work_batch_size if max_files is None else max_files
        if not 1 <= limit <= self._work_batch_size:
            raise ValueError("duplicate-scan cycle file limit is invalid")
        scan = self._load_next_cycle_scan(observed_utc=observed_utc)
        if scan is None:
            return DuplicateScanCycleReport(
                scan=None,
                files_attempted=0,
                files_completed=0,
                files_failed=0,
                stopped_reason="NO_RUNNABLE_SCAN",
            )
        if active_backup:
            paused = self._pause_for_active_backup(scan, observed_utc=observed_utc)
            return DuplicateScanCycleReport(
                scan=paused,
                files_attempted=0,
                files_completed=0,
                files_failed=0,
                stopped_reason="ACTIVE_BACKUP",
            )

        attempted = 0
        completed = 0
        failed = 0
        while attempted < limit:
            scan = self.load_duplicate_scan(scan.analysis_id)
            if scan is None:
                raise SqliteDuplicateScannerError("DUPLICATE_SCAN_DISAPPEARED")
            if scan.stage is DuplicateScanStage.QUICK_SIGNATURE:
                item = self._claim_next_request(
                    scan_id=scan.scan_id,
                    request_stage="QUICK_SIGNATURE",
                    observed_utc=observed_utc,
                )
                if item is None:
                    enqueued = self._enqueue_quick_requests(
                        scan,
                        observed_utc=observed_utc,
                    )
                    if enqueued == 0:
                        if self._quick_stage_complete(scan.scan_id):
                            self._advance_to_full_hash(
                                scan.scan_id,
                                observed_utc=observed_utc,
                            )
                            continue
                        break
                    continue
                attempted += 1
                if self._process_quick_request(item, observed_utc=observed_utc):
                    completed += 1
                else:
                    failed += 1
                self._refresh_progress_counts(scan.scan_id, observed_utc=observed_utc)
                continue
            if scan.stage is DuplicateScanStage.FULL_HASH:
                item = self._claim_next_request(
                    scan_id=scan.scan_id,
                    request_stage="FULL_HASH",
                    observed_utc=observed_utc,
                )
                if item is None:
                    self._advance_to_materialization(
                        scan.scan_id,
                        observed_utc=observed_utc,
                    )
                    continue
                attempted += 1
                if self._process_full_hash_request(item, observed_utc=observed_utc):
                    completed += 1
                else:
                    failed += 1
                self._refresh_progress_counts(scan.scan_id, observed_utc=observed_utc)
                continue
            if scan.stage is DuplicateScanStage.MATERIALIZE:
                self._materialize_and_complete(scan, observed_utc=observed_utc)
                break
            break

        latest = self.load_duplicate_scan(scan.analysis_id)
        return DuplicateScanCycleReport(
            scan=latest,
            files_attempted=attempted,
            files_completed=completed,
            files_failed=failed,
            stopped_reason=(
                "SCAN_COMPLETED"
                if latest is not None and latest.terminal
                else "WORK_LIMIT_REACHED"
                if attempted >= limit
                else "WAITING_FOR_WORK"
            ),
        )

    def load_duplicate_scan(self, analysis_id: str) -> DuplicateScanStatus | None:
        row = self._connection.execute(
            f"""
            SELECT {_DUPLICATE_SCAN_COLUMNS}
            FROM duplicate_scans
            WHERE analysis_id = ?
            """,
            (analysis_id,),
        ).fetchone()
        return None if row is None else _scan_from_row(tuple(row))

    def load_duplicate_group(self, group_id: str) -> DuplicateGroupReadModel | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                relationship_class,
                full_hash,
                size_bytes,
                member_count,
                physical_object_count,
                expected_replica_count,
                potential_savings_bytes,
                review_state,
                created_utc
            FROM duplicate_groups
            WHERE id = ?
            """,
            (group_id,),
        ).fetchone()
        return None if row is None else _group_from_row(tuple(row))

    def mark_duplicate_group_reviewed(
        self,
        *,
        group_id: str,
        expected_review_state: str,
    ) -> DuplicateGroupReadModel | None:
        if not group_id.strip() or expected_review_state != "UNREVIEWED":
            raise DuplicateScanError("DUPLICATE_GROUP_REVIEW_PRECONDITION_INVALID")
        cursor = self._connection.execute(
            """
            UPDATE duplicate_groups
            SET review_state = 'REVIEWED'
            WHERE id = ? AND review_state = ?
            """,
            (group_id, expected_review_state),
        )
        if cursor.rowcount != 1:
            return None
        return self.load_duplicate_group(group_id)

    def page_duplicate_groups(
        self,
        *,
        analysis_id: str,
        limit: int,
        after: DuplicateGroupCursor | None = None,
        relationship_classes: tuple[str, ...] = (),
    ) -> DuplicateGroupPage:
        if not 1 <= limit <= DUPLICATE_GROUP_MAX_PAGE_SIZE:
            raise DuplicateScanError("DUPLICATE_GROUP_PAGE_LIMIT_INVALID")
        if any(item not in _DUPLICATE_RELATION_CLASSES for item in relationship_classes):
            raise DuplicateScanError("DUPLICATE_GROUP_FILTER_INVALID")
        predicates = ["analysis_id = ?"]
        parameters: list[object] = [analysis_id]
        if relationship_classes:
            placeholders = ", ".join("?" for _ in relationship_classes)
            predicates.append(f"relationship_class IN ({placeholders})")
            parameters.extend(relationship_classes)
        if after is not None:
            predicates.append(
                """
                (
                    relationship_class > ?
                    OR (relationship_class = ? AND full_hash > ?)
                    OR (
                        relationship_class = ?
                        AND full_hash = ?
                        AND id > ?
                    )
                )
                """
            )
            parameters.extend(
                (
                    after.relationship_class,
                    after.relationship_class,
                    after.full_hash,
                    after.relationship_class,
                    after.full_hash,
                    after.group_id,
                )
            )
        parameters.append(limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT
                id,
                relationship_class,
                full_hash,
                size_bytes,
                member_count,
                physical_object_count,
                expected_replica_count,
                potential_savings_bytes,
                review_state,
                created_utc
            FROM duplicate_groups
            WHERE {' AND '.join(predicates)}
            ORDER BY relationship_class, full_hash, id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        has_more = len(rows) > limit
        items = tuple(_group_from_row(tuple(row)) for row in rows[:limit])
        last = items[-1] if items else None
        return DuplicateGroupPage(
            analysis_id=analysis_id,
            groups=items,
            next_cursor=(
                None
                if not has_more or last is None
                else DuplicateGroupCursor(
                    relationship_class=last.relationship_class,
                    full_hash=last.full_hash,
                    group_id=last.group_id,
                )
            ),
            has_more=has_more,
        )

    def page_duplicate_members(
        self,
        *,
        group_id: str,
        limit: int,
        after: DuplicateMemberCursor | None = None,
    ) -> DuplicateMemberPage:
        if not 1 <= limit <= DUPLICATE_MEMBER_MAX_PAGE_SIZE:
            raise DuplicateScanError("DUPLICATE_MEMBER_PAGE_LIMIT_INVALID")
        parameters: list[object] = [group_id]
        cursor_predicate = ""
        if after is not None:
            cursor_predicate = """
                AND (
                    members.relative_path > ?
                    OR (members.relative_path = ? AND members.snapshot_id > ?)
                    OR (
                        members.relative_path = ?
                        AND members.snapshot_id = ?
                        AND members.file_entry_id > ?
                    )
                )
            """
            parameters.extend(
                (
                    after.relative_path,
                    after.relative_path,
                    after.snapshot_id,
                    after.relative_path,
                    after.snapshot_id,
                    after.file_entry_id,
                )
            )
        parameters.append(limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT
                members.group_id,
                members.snapshot_id,
                members.endpoint_id,
                members.file_entry_id,
                members.relative_path,
                members.member_role,
                members.physical_object_key,
                bindings.role,
                revisions.root_uri,
                hashes.size_bytes,
                hashes.evidence_kind
            FROM duplicate_members AS members
            INNER JOIN duplicate_groups AS groups
                ON groups.id = members.group_id
            INNER JOIN analyses
                ON analyses.id = groups.analysis_id
            INNER JOIN standard_backup_job_endpoint_bindings AS bindings
                ON bindings.job_id = analyses.job_id
                AND bindings.job_revision_id = analyses.job_revision_id
                AND bindings.endpoint_id = members.endpoint_id
            INNER JOIN snapshots
                ON snapshots.id = members.snapshot_id
                AND snapshots.endpoint_id = members.endpoint_id
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = snapshots.endpoint_id
                AND revisions.id = snapshots.endpoint_revision_id
            INNER JOIN current_read_hash_evidence AS hashes
                ON hashes.snapshot_id = members.snapshot_id
                AND hashes.entry_id = members.file_entry_id
                AND hashes.endpoint_id = members.endpoint_id
                AND hashes.evidence_kind = 'CURRENT_READ_HASH'
            WHERE members.group_id = ?
                {cursor_predicate}
            ORDER BY members.relative_path, members.snapshot_id, members.file_entry_id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        has_more = len(rows) > limit
        items = tuple(_member_from_row(tuple(row)) for row in rows[:limit])
        last = items[-1] if items else None
        return DuplicateMemberPage(
            group_id=group_id,
            members=items,
            next_cursor=(
                None
                if not has_more or last is None
                else DuplicateMemberCursor(
                    relative_path=last.relative_path,
                    snapshot_id=last.snapshot_id,
                    file_entry_id=last.file_entry_id,
                )
            ),
            has_more=has_more,
        )

    def page_duplicate_report(
        self,
        *,
        analysis_id: str,
        limit: int,
        after: DuplicateReportCursor | None = None,
    ) -> DuplicateReportPage:
        if not analysis_id.strip():
            raise DuplicateScanError("DUPLICATE_REPORT_ANALYSIS_ID_INVALID")
        if not 1 <= limit <= DUPLICATE_REPORT_MAX_PAGE_SIZE:
            raise DuplicateScanError("DUPLICATE_REPORT_PAGE_LIMIT_INVALID")
        parameters: list[object] = [analysis_id]
        cursor_predicate = ""
        if after is not None:
            cursor_predicate = """
                AND (
                    groups.relationship_class > ?
                    OR (
                        groups.relationship_class = ?
                        AND groups.full_hash > ?
                    )
                    OR (
                        groups.relationship_class = ?
                        AND groups.full_hash = ?
                        AND groups.id > ?
                    )
                    OR (
                        groups.relationship_class = ?
                        AND groups.full_hash = ?
                        AND groups.id = ?
                        AND members.relative_path > ?
                    )
                    OR (
                        groups.relationship_class = ?
                        AND groups.full_hash = ?
                        AND groups.id = ?
                        AND members.relative_path = ?
                        AND members.snapshot_id > ?
                    )
                    OR (
                        groups.relationship_class = ?
                        AND groups.full_hash = ?
                        AND groups.id = ?
                        AND members.relative_path = ?
                        AND members.snapshot_id = ?
                        AND members.file_entry_id > ?
                    )
                )
            """
            parameters.extend(
                (
                    after.relationship_class,
                    after.relationship_class,
                    after.full_hash,
                    after.relationship_class,
                    after.full_hash,
                    after.group_id,
                    after.relationship_class,
                    after.full_hash,
                    after.group_id,
                    after.relative_path,
                    after.relationship_class,
                    after.full_hash,
                    after.group_id,
                    after.relative_path,
                    after.snapshot_id,
                    after.relationship_class,
                    after.full_hash,
                    after.group_id,
                    after.relative_path,
                    after.snapshot_id,
                    after.file_entry_id,
                )
            )
        parameters.append(limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT
                groups.id,
                groups.relationship_class,
                groups.full_hash,
                groups.size_bytes,
                groups.member_count,
                groups.physical_object_count,
                groups.expected_replica_count,
                groups.potential_savings_bytes,
                groups.review_state,
                groups.created_utc,
                members.group_id,
                members.snapshot_id,
                members.endpoint_id,
                members.file_entry_id,
                members.relative_path,
                members.member_role,
                members.physical_object_key,
                bindings.role,
                revisions.root_uri,
                hashes.size_bytes,
                hashes.evidence_kind
            FROM duplicate_groups AS groups
            INNER JOIN duplicate_members AS members
                ON members.group_id = groups.id
            INNER JOIN analyses
                ON analyses.id = groups.analysis_id
            INNER JOIN standard_backup_job_endpoint_bindings AS bindings
                ON bindings.job_id = analyses.job_id
                AND bindings.job_revision_id = analyses.job_revision_id
                AND bindings.endpoint_id = members.endpoint_id
            INNER JOIN snapshots
                ON snapshots.id = members.snapshot_id
                AND snapshots.endpoint_id = members.endpoint_id
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = snapshots.endpoint_id
                AND revisions.id = snapshots.endpoint_revision_id
            INNER JOIN current_read_hash_evidence AS hashes
                ON hashes.snapshot_id = members.snapshot_id
                AND hashes.entry_id = members.file_entry_id
                AND hashes.endpoint_id = members.endpoint_id
                AND hashes.evidence_kind = 'CURRENT_READ_HASH'
            WHERE groups.analysis_id = ?
                {cursor_predicate}
            ORDER BY
                groups.relationship_class,
                groups.full_hash,
                groups.id,
                members.relative_path,
                members.snapshot_id,
                members.file_entry_id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = tuple(
            DuplicateReportRow(
                group=_group_from_row(tuple(row[:10])),
                member=_member_from_row(tuple(row[10:])),
            )
            for row in page_rows
        )
        last = items[-1] if items else None
        return DuplicateReportPage(
            analysis_id=analysis_id,
            rows=items,
            next_cursor=(
                None
                if not has_more or last is None
                else DuplicateReportCursor(
                    relationship_class=last.group.relationship_class,
                    full_hash=last.group.full_hash,
                    group_id=last.group.group_id,
                    relative_path=last.member.relative_path,
                    snapshot_id=last.member.snapshot_id,
                    file_entry_id=last.member.file_entry_id,
                )
            ),
            has_more=has_more,
        )

    def _load_next_cycle_scan(self, *, observed_utc: str) -> DuplicateScanStatus | None:
        row = self._connection.execute(
            """
            SELECT analysis_id
            FROM duplicate_scans
            WHERE state = 'PAUSED'
                AND reason_code = 'ACTIVE_BACKUP'
            ORDER BY requested_utc, id
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            resumed = self.resume_scan(
                analysis_id=str(row[0]),
                observed_utc=observed_utc,
            )
            if resumed is not None:
                return resumed
        row = self._connection.execute(
            """
            SELECT analysis_id
            FROM duplicate_scans
            WHERE state IN ('QUEUED', 'RUNNING')
            ORDER BY requested_utc, id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        analysis_id = str(row[0])
        self._connection.execute(
            """
            UPDATE duplicate_scans
            SET
                state = 'RUNNING',
                started_utc = COALESCE(started_utc, ?),
                updated_utc = ?,
                reason_code = NULL
            WHERE analysis_id = ?
                AND state = 'QUEUED'
            """,
            (observed_utc, observed_utc, analysis_id),
        )
        self._connection.commit()
        return self.load_duplicate_scan(analysis_id)

    def _pause_for_active_backup(
        self,
        scan: DuplicateScanStatus,
        *,
        observed_utc: str,
    ) -> DuplicateScanStatus:
        self._connection.execute(
            """
            UPDATE duplicate_scans
            SET
                state = 'PAUSED',
                reason_code = 'ACTIVE_BACKUP',
                updated_utc = ?
            WHERE id = ?
                AND state IN ('QUEUED', 'RUNNING')
            """,
            (observed_utc, scan.scan_id),
        )
        self._connection.commit()
        paused = self.load_duplicate_scan(scan.analysis_id)
        if paused is None:
            raise SqliteDuplicateScannerError("DUPLICATE_SCAN_DISAPPEARED")
        return paused

    def _count_candidate_files(self, analysis_id: str) -> int:
        row = self._connection.execute(
            f"""
            {_DUPLICATE_SCAN_CANDIDATES_CTE}
            SELECT count(*)
            FROM duplicate_candidates
            """,
            (analysis_id,),
        ).fetchone()
        return 0 if row is None else _required_int(row[0])

    def _enqueue_quick_requests(
        self,
        scan: DuplicateScanStatus,
        *,
        observed_utc: str,
    ) -> int:
        row_count = self._request_row_count()
        available = self._max_persisted_requests - row_count
        if available <= 0:
            if self._scan_request_count(scan.scan_id) >= scan.candidate_file_count:
                self._mark_quick_enumeration_complete(
                    scan.scan_id,
                    observed_utc=observed_utc,
                )
            return 0
        limit = min(self._work_batch_size, available)
        cursor = self._connection.execute(
            """
            SELECT quick_cursor_snapshot_id, quick_cursor_entry_id
            FROM duplicate_scans
            WHERE id = ?
            """,
            (scan.scan_id,),
        ).fetchone()
        if cursor is None:
            raise SqliteDuplicateScannerError("DUPLICATE_SCAN_DISAPPEARED")
        after_snapshot = None if cursor[0] is None else str(cursor[0])
        after_entry = None if cursor[1] is None else str(cursor[1])
        rows = self._connection.execute(
            f"""
            {_DUPLICATE_SCAN_CANDIDATES_CTE}
            SELECT
                snapshot_id,
                endpoint_id,
                file_entry_id,
                physical_object_key,
                relative_path,
                size_bytes
            FROM duplicate_candidates
            WHERE ? IS NULL
                OR snapshot_id > ?
                OR (snapshot_id = ? AND file_entry_id > ?)
            ORDER BY snapshot_id, file_entry_id
            LIMIT ?
            """,
            (
                scan.analysis_id,
                after_snapshot,
                after_snapshot,
                after_snapshot,
                after_entry,
                limit,
            ),
        ).fetchall()
        if not rows:
            self._mark_quick_enumeration_complete(
                scan.scan_id,
                observed_utc=observed_utc,
            )
            return 0
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                snapshot_id = str(row[0])
                entry_id = str(row[2])
                self._connection.execute(
                    """
                    INSERT INTO hash_requests (
                        id,
                        scan_id,
                        request_scope,
                        snapshot_id,
                        endpoint_id,
                        file_entry_id,
                        physical_object_key,
                        relative_path,
                        size_bytes,
                        request_stage,
                        state,
                        requested_utc,
                        updated_utc
                    )
                    VALUES (
                        ?, ?, 'DUPLICATE_SCAN', ?, ?, ?, ?, ?, ?,
                        'QUICK_SIGNATURE', 'PENDING', ?, ?
                    )
                    ON CONFLICT (scan_id, snapshot_id, file_entry_id) DO NOTHING
                    """,
                    (
                        _hash_request_id(scan.scan_id, snapshot_id, entry_id),
                        scan.scan_id,
                        snapshot_id,
                        str(row[1]),
                        entry_id,
                        str(row[3]),
                        str(row[4]),
                        _required_int(row[5]),
                        observed_utc,
                        observed_utc,
                    ),
                )
            last = rows[-1]
            self._connection.execute(
                """
                UPDATE duplicate_scans
                SET
                    quick_cursor_snapshot_id = ?,
                    quick_cursor_entry_id = ?,
                    quick_enumeration_complete = CASE
                        WHEN (
                            SELECT count(*)
                            FROM hash_requests
                            WHERE scan_id = duplicate_scans.id
                        ) >= candidate_file_count
                        THEN 1
                        ELSE quick_enumeration_complete
                    END,
                    updated_utc = ?
                WHERE id = ?
                """,
                (str(last[0]), str(last[2]), observed_utc, scan.scan_id),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        return len(rows)

    def _claim_next_request(
        self,
        *,
        scan_id: str,
        request_stage: str,
        observed_utc: str,
    ) -> _HashWorkItem | None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT id
                FROM hash_requests
                WHERE scan_id = ?
                    AND request_stage = ?
                    AND state = 'PENDING'
                ORDER BY id
                LIMIT 1
                """,
                (scan_id, request_stage),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            request_id = str(row[0])
            self._connection.execute(
                """
                UPDATE hash_requests
                SET
                    state = 'RUNNING',
                    attempt_count = attempt_count + 1,
                    updated_utc = ?,
                    completed_utc = NULL
                WHERE id = ?
                    AND state = 'PENDING'
                """,
                (observed_utc, request_id),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        return self._load_work_item(request_id)

    def _load_work_item(self, request_id: str) -> _HashWorkItem:
        row = self._connection.execute(
            """
            SELECT
                requests.id,
                requests.scan_id,
                scans.analysis_id,
                requests.request_stage,
                requests.attempt_count,
                requests.snapshot_id,
                requests.endpoint_id,
                requests.file_entry_id,
                requests.physical_object_key,
                requests.relative_path,
                entries.comparison_key,
                requests.size_bytes,
                entries.birthtime_ns,
                entries.identity_fingerprint_hash,
                revisions.generation,
                revisions.root_uri,
                coverage.case_context_hash,
                observations.read_capabilities_json,
                requests.quick_cache_identity_hash,
                requests.quick_hash
            FROM hash_requests AS requests
            INNER JOIN duplicate_scans AS scans
                ON scans.id = requests.scan_id
            INNER JOIN snapshots
                ON snapshots.id = requests.snapshot_id
                AND snapshots.endpoint_id = requests.endpoint_id
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.id = snapshots.endpoint_revision_id
                AND revisions.endpoint_id = snapshots.endpoint_id
            INNER JOIN file_entries AS entries
                ON entries.snapshot_id = requests.snapshot_id
                AND entries.id = requests.file_entry_id
            INNER JOIN directory_coverage AS coverage
                ON coverage.snapshot_id = requests.snapshot_id
                AND coverage.relative_path = mediasync_parent_path(
                    requests.relative_path
                )
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = snapshots.endpoint_id
                AND observations.endpoint_revision_id = snapshots.endpoint_revision_id
            WHERE requests.id = ?
                AND snapshots.complete = 1
                AND snapshots.immutable = 1
            """,
            (request_id,),
        ).fetchone()
        if row is None or row[12] is None or row[13] is None:
            raise SqliteDuplicateScannerError("HASH_REQUEST_SNAPSHOT_CONTEXT_INVALID")
        return _HashWorkItem(
            request_id=str(row[0]),
            scan_id=str(row[1]),
            analysis_id=str(row[2]),
            request_stage=str(row[3]),
            attempt_count=_required_int(row[4]),
            snapshot_id=str(row[5]),
            endpoint_id=str(row[6]),
            entry_id=str(row[7]),
            physical_object_key=str(row[8]),
            relative_path=str(row[9]),
            comparison_key=str(row[10]),
            size_bytes=_required_int(row[11]),
            birthtime_ns=_required_int(row[12]),
            identity_fingerprint_hash=str(row[13]),
            endpoint_generation=_required_int(row[14]),
            root=local_path_from_file_uri(str(row[15])),
            parent_case_context_hash=str(row[16]),
            file_id_reliability=_file_id_reliability(
                None if row[17] is None else str(row[17])
            ),
            quick_cache_identity_hash=(
                None if row[18] is None else str(row[18])
            ),
            quick_hash=None if row[19] is None else str(row[19]),
        )

    def _process_quick_request(
        self,
        item: _HashWorkItem,
        *,
        observed_utc: str,
    ) -> bool:
        try:
            cached = self._cache.load_reusable_quick_signature(
                endpoint_id=item.endpoint_id,
                endpoint_generation=item.endpoint_generation,
                relative_path=item.relative_path,
                comparison_key=item.comparison_key,
                comparison_key_version=1,
                parent_case_context_hash=item.parent_case_context_hash,
                size_bytes=item.size_bytes,
                birthtime_ns=item.birthtime_ns,
                identity_fingerprint_hash=item.identity_fingerprint_hash,
            )
            if cached is not None and cached.quick_hash is not None:
                self._complete_quick_request(
                    item.request_id,
                    identity_hash=cached.identity.identity_hash,
                    quick_hash=cached.quick_hash,
                    observed_utc=observed_utc,
                )
                return True
            evidence = self._quick_hasher.hash_file(
                QuickSignatureRequest(
                    snapshot_id=item.snapshot_id,
                    entry_id=item.entry_id,
                    endpoint_id=item.endpoint_id,
                    root=item.root,
                    relative_path=item.relative_path,
                    expected_size_bytes=item.size_bytes,
                    computed_utc=observed_utc,
                )
            )
            if (
                evidence.read_started_fingerprint_hash
                != item.identity_fingerprint_hash
                or evidence.birthtime_ns != item.birthtime_ns
            ):
                raise SqliteDuplicateScannerError(
                    "QUICK_SIGNATURE_SNAPSHOT_IDENTITY_CHANGED"
                )
            identity = HashCacheIdentity(
                endpoint_id=item.endpoint_id,
                endpoint_generation=item.endpoint_generation,
                volume_identity=evidence.volume_identity,
                relative_path=item.relative_path,
                comparison_key=item.comparison_key,
                comparison_key_version=1,
                parent_case_context_hash=item.parent_case_context_hash,
                entry_type="file",
                size_bytes=item.size_bytes,
                mtime_ns=evidence.mtime_ns,
                birthtime_ns=evidence.birthtime_ns,
                attributes=evidence.attributes,
                reparse_tag=evidence.reparse_tag,
                file_id=evidence.file_id,
                file_id_reliability=item.file_id_reliability,
                link_count=evidence.link_count,
            )
            write = self._cache.persist_evidence(
                HashCacheRecord(
                    identity=identity,
                    evidence_kind=HashCacheEvidenceKind.QUICK_SIGNATURE_ONLY,
                    evidence_generation=1,
                    computed_utc=observed_utc,
                    quick_hash=evidence.signature_hash,
                    signature_schema_version=QUICK_SIGNATURE_SCHEMA_VERSION,
                    read_started_fingerprint_hash=(
                        evidence.read_started_fingerprint_hash
                    ),
                    read_completed_fingerprint_hash=(
                        evidence.read_completed_fingerprint_hash
                    ),
                )
            )
            if write.state is HashCacheWriteState.CAPACITY_REJECTED:
                raise SqliteDuplicateScannerError("HASH_CACHE_CAPACITY_REJECTED")
            self._complete_quick_request(
                item.request_id,
                identity_hash=identity.identity_hash,
                quick_hash=evidence.signature_hash,
                observed_utc=observed_utc,
            )
            return True
        except Exception as exc:
            self._retry_or_fail_request(
                item,
                error_code=_error_code(exc),
                observed_utc=observed_utc,
            )
            return False

    def _process_full_hash_request(
        self,
        item: _HashWorkItem,
        *,
        observed_utc: str,
    ) -> bool:
        try:
            if item.quick_cache_identity_hash is None or item.quick_hash is None:
                raise SqliteDuplicateScannerError(
                    "FULL_HASH_QUICK_EVIDENCE_MISSING"
                )
            cache_record = self._cache.load_active_by_identity_hash(
                item.quick_cache_identity_hash
            )
            if cache_record is None or cache_record.quick_hash != item.quick_hash:
                raise SqliteDuplicateScannerError("FULL_HASH_CACHE_EVIDENCE_CHANGED")
            evidence = self._load_current_read_evidence(item)
            if evidence is None:
                evidence = self._full_hasher.hash_file(
                    CurrentReadHashRequest(
                        snapshot_id=item.snapshot_id,
                        entry_id=item.entry_id,
                        endpoint_id=item.endpoint_id,
                        root=item.root,
                        relative_path=item.relative_path,
                        expected_size_bytes=item.size_bytes,
                        computed_utc=observed_utc,
                    )
                )
            if (
                evidence.read_started_fingerprint_hash
                != item.identity_fingerprint_hash
            ):
                raise SqliteDuplicateScannerError("FULL_HASH_SNAPSHOT_IDENTITY_CHANGED")
            propagated = self._propagate_alias_evidence(item, evidence)
            self._current_evidence.persist_current_read_hash_evidence(
                analysis_id=item.analysis_id,
                evidence=propagated,
            )
            write = self._cache.persist_evidence(
                HashCacheRecord(
                    identity=cache_record.identity,
                    evidence_kind=HashCacheEvidenceKind.CURRENT_READ_HASH,
                    evidence_generation=1,
                    computed_utc=observed_utc,
                    quick_hash=item.quick_hash,
                    full_hash=evidence.content_hash,
                    algorithm=HASH_CACHE_ALGORITHM,
                    hash_schema_version=HASH_CACHE_HASH_SCHEMA_VERSION,
                    signature_schema_version=QUICK_SIGNATURE_SCHEMA_VERSION,
                    read_started_fingerprint_hash=(
                        evidence.read_started_fingerprint_hash
                    ),
                    read_completed_fingerprint_hash=(
                        evidence.read_completed_fingerprint_hash
                    ),
                )
            )
            if write.state is HashCacheWriteState.CAPACITY_REJECTED:
                raise SqliteDuplicateScannerError("HASH_CACHE_CAPACITY_REJECTED")
            self._connection.execute(
                """
                UPDATE hash_requests
                SET
                    state = 'SUCCEEDED',
                    full_hash = ?,
                    last_error_code = NULL,
                    updated_utc = ?,
                    completed_utc = ?
                WHERE id = ?
                    AND request_stage = 'FULL_HASH'
                    AND state = 'RUNNING'
                """,
                (
                    evidence.content_hash,
                    observed_utc,
                    observed_utc,
                    item.request_id,
                ),
            )
            self._connection.commit()
            return True
        except Exception as exc:
            self._retry_or_fail_request(
                item,
                error_code=_error_code(exc),
                observed_utc=observed_utc,
            )
            return False

    def _load_current_read_evidence(
        self,
        item: _HashWorkItem,
    ) -> CurrentReadHashEvidence | None:
        row = self._connection.execute(
            """
            SELECT
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
            FROM current_read_hash_evidence
            WHERE snapshot_id = ?
                AND entry_id = ?
            """,
            (item.snapshot_id, item.entry_id),
        ).fetchone()
        if row is None:
            return None
        return CurrentReadHashEvidence(
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

    def _propagate_alias_evidence(
        self,
        item: _HashWorkItem,
        evidence: CurrentReadHashEvidence,
    ) -> tuple[CurrentReadHashEvidence, ...]:
        rows = self._connection.execute(
            """
            SELECT aliases.file_entry_id
            FROM file_object_alias_members AS member
            INNER JOIN file_object_alias_members AS aliases
                ON aliases.group_id = member.group_id
                AND aliases.snapshot_id = member.snapshot_id
            WHERE member.snapshot_id = ?
                AND member.file_entry_id = ?
            ORDER BY aliases.file_entry_id
            """,
            (item.snapshot_id, item.entry_id),
        ).fetchall()
        entry_ids = (
            (item.entry_id,)
            if not rows
            else tuple(str(row[0]) for row in rows)
        )
        return tuple(replace(evidence, entry_id=entry_id) for entry_id in entry_ids)

    def _complete_quick_request(
        self,
        request_id: str,
        *,
        identity_hash: str,
        quick_hash: str,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE hash_requests
            SET
                state = 'SUCCEEDED',
                quick_cache_identity_hash = ?,
                quick_hash = ?,
                last_error_code = NULL,
                updated_utc = ?,
                completed_utc = ?
            WHERE id = ?
                AND request_stage = 'QUICK_SIGNATURE'
                AND state = 'RUNNING'
            """,
            (identity_hash, quick_hash, observed_utc, observed_utc, request_id),
        )
        self._connection.commit()

    def _retry_or_fail_request(
        self,
        item: _HashWorkItem,
        *,
        error_code: str,
        observed_utc: str,
    ) -> None:
        terminal = item.attempt_count >= DUPLICATE_SCAN_MAX_ATTEMPTS_PER_FILE
        terminal_stage = (
            "DONE" if item.request_stage == "QUICK_SIGNATURE" else item.request_stage
        )
        self._connection.execute(
            """
            UPDATE hash_requests
            SET
                request_stage = CASE WHEN ? THEN ? ELSE request_stage END,
                state = CASE WHEN ? THEN 'FAILED' ELSE 'PENDING' END,
                last_error_code = ?,
                updated_utc = ?,
                completed_utc = CASE WHEN ? THEN ? ELSE NULL END
            WHERE id = ?
                AND state = 'RUNNING'
            """,
            (
                terminal,
                terminal_stage,
                terminal,
                error_code,
                observed_utc,
                terminal,
                observed_utc,
                item.request_id,
            ),
        )
        self._connection.commit()

    def _quick_stage_complete(self, scan_id: str) -> bool:
        row = self._connection.execute(
            """
            SELECT
                quick_enumeration_complete,
                EXISTS (
                    SELECT 1
                    FROM hash_requests
                    WHERE scan_id = duplicate_scans.id
                        AND request_stage = 'QUICK_SIGNATURE'
                        AND state IN ('PENDING', 'RUNNING')
                )
            FROM duplicate_scans
            WHERE id = ?
            """,
            (scan_id,),
        ).fetchone()
        return row is not None and _required_int(row[0]) == 1 and _required_int(row[1]) == 0

    def _advance_to_full_hash(self, scan_id: str, *, observed_utc: str) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                WITH matching_quick_groups AS (
                    SELECT size_bytes, quick_hash
                    FROM hash_requests
                    WHERE scan_id = ?
                        AND request_stage = 'QUICK_SIGNATURE'
                        AND state = 'SUCCEEDED'
                        AND quick_hash IS NOT NULL
                    GROUP BY size_bytes, quick_hash
                    HAVING count(*) >= 2
                )
                SELECT count(*)
                FROM hash_requests AS requests
                WHERE requests.scan_id = ?
                    AND requests.request_stage = 'QUICK_SIGNATURE'
                    AND requests.state = 'SUCCEEDED'
                    AND (requests.size_bytes, requests.quick_hash) IN (
                        SELECT size_bytes, quick_hash
                        FROM matching_quick_groups
                    )
                """,
                (scan_id, scan_id),
            ).fetchone()
            full_candidate_count = 0 if row is None else _required_int(row[0])
            self._connection.execute(
                """
                WITH matching_quick_groups AS (
                    SELECT size_bytes, quick_hash
                    FROM hash_requests
                    WHERE scan_id = ?
                        AND request_stage = 'QUICK_SIGNATURE'
                        AND state = 'SUCCEEDED'
                        AND quick_hash IS NOT NULL
                    GROUP BY size_bytes, quick_hash
                    HAVING count(*) >= 2
                )
                UPDATE hash_requests
                SET
                    request_stage = 'FULL_HASH',
                    state = 'PENDING',
                    completed_utc = NULL,
                    updated_utc = ?
                WHERE scan_id = ?
                    AND request_stage = 'QUICK_SIGNATURE'
                    AND state = 'SUCCEEDED'
                    AND (size_bytes, quick_hash) IN (
                        SELECT size_bytes, quick_hash
                        FROM matching_quick_groups
                    )
                """,
                (scan_id, observed_utc, scan_id),
            )
            self._connection.execute(
                """
                UPDATE hash_requests
                SET request_stage = 'DONE', updated_utc = ?
                WHERE scan_id = ?
                    AND request_stage = 'QUICK_SIGNATURE'
                    AND state = 'SUCCEEDED'
                """,
                (observed_utc, scan_id),
            )
            self._connection.execute(
                """
                UPDATE duplicate_scans
                SET
                    stage = 'FULL_HASH',
                    quick_completed_count = candidate_file_count,
                    full_hash_candidate_count = ?,
                    updated_utc = ?
                WHERE id = ?
                    AND stage = 'QUICK_SIGNATURE'
                """,
                (full_candidate_count, observed_utc, scan_id),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _advance_to_materialization(
        self,
        scan_id: str,
        *,
        observed_utc: str,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM hash_requests
                WHERE scan_id = ?
                    AND request_stage = 'FULL_HASH'
                    AND state IN ('PENDING', 'RUNNING')
            )
            """,
            (scan_id,),
        ).fetchone()
        if row is not None and _required_int(row[0]) == 1:
            return
        self._connection.execute(
            """
            UPDATE duplicate_scans
            SET stage = 'MATERIALIZE', updated_utc = ?
            WHERE id = ?
                AND stage = 'FULL_HASH'
            """,
            (observed_utc, scan_id),
        )
        self._connection.commit()

    def _materialize_and_complete(
        self,
        scan: DuplicateScanStatus,
        *,
        observed_utc: str,
    ) -> None:
        self._relations.materialize_known_duplicate_relations(
            analysis_id=scan.analysis_id,
            observed_utc=observed_utc,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            issue_row = self._connection.execute(
                """
                SELECT count(*)
                FROM hash_requests
                WHERE scan_id = ?
                    AND state = 'FAILED'
                """,
                (scan.scan_id,),
            ).fetchone()
            issue_count = 0 if issue_row is None else _required_int(issue_row[0])
            self._connection.execute(
                "DELETE FROM hash_requests WHERE scan_id = ?",
                (scan.scan_id,),
            )
            self._connection.execute(
                """
                UPDATE duplicate_scans
                SET
                    state = 'COMPLETED',
                    stage = 'DONE',
                    quick_completed_count = candidate_file_count,
                    full_hash_completed_count = full_hash_candidate_count,
                    issue_count = ?,
                    reason_code = CASE
                        WHEN ? > 0 THEN 'COMPLETED_WITH_ISSUES'
                        ELSE 'DUPLICATE_SCAN_COMPLETED'
                    END,
                    updated_utc = ?,
                    completed_utc = ?
                WHERE id = ?
                    AND stage = 'MATERIALIZE'
                """,
                (
                    issue_count,
                    issue_count,
                    observed_utc,
                    observed_utc,
                    scan.scan_id,
                ),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _refresh_progress_counts(self, scan_id: str, *, observed_utc: str) -> None:
        self._connection.execute(
            """
            UPDATE duplicate_scans
            SET
                quick_completed_count = MIN(
                    candidate_file_count,
                    (
                        SELECT count(*)
                        FROM hash_requests
                        WHERE scan_id = duplicate_scans.id
                            AND (
                                quick_hash IS NOT NULL
                                OR (request_stage = 'DONE' AND state = 'FAILED')
                            )
                    )
                ),
                full_hash_completed_count = MIN(
                    full_hash_candidate_count,
                    (
                        SELECT count(*)
                        FROM hash_requests
                        WHERE scan_id = duplicate_scans.id
                            AND request_stage = 'FULL_HASH'
                            AND state IN ('SUCCEEDED', 'FAILED')
                    )
                ),
                issue_count = (
                    SELECT count(*)
                    FROM hash_requests
                    WHERE scan_id = duplicate_scans.id
                        AND state = 'FAILED'
                ),
                updated_utc = ?
            WHERE id = ?
            """,
            (observed_utc, scan_id),
        )
        self._connection.commit()

    def _active_scan_count(self) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM duplicate_scans
            WHERE state IN ('QUEUED', 'RUNNING', 'PAUSED')
            """
        ).fetchone()
        return 0 if row is None else _required_int(row[0])

    def _request_row_count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM hash_requests").fetchone()
        return 0 if row is None else _required_int(row[0])

    def _scan_request_count(self, scan_id: str) -> int:
        row = self._connection.execute(
            "SELECT count(*) FROM hash_requests WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        return 0 if row is None else _required_int(row[0])

    def _mark_quick_enumeration_complete(
        self,
        scan_id: str,
        *,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE duplicate_scans
            SET quick_enumeration_complete = 1, updated_utc = ?
            WHERE id = ?
            """,
            (observed_utc, scan_id),
        )
        self._connection.commit()

    def _prune_scan_history(self) -> None:
        rows = self._connection.execute(
            """
            SELECT id
            FROM duplicate_scans
            WHERE state IN ('COMPLETED', 'FAILED')
            ORDER BY completed_utc DESC, id DESC
            LIMIT -1 OFFSET ?
            """,
            (self._max_history_rows - 1,),
        ).fetchall()
        for row in rows:
            scan_id = str(row[0])
            self._connection.execute(
                "DELETE FROM hash_requests WHERE scan_id = ?",
                (scan_id,),
            )
            self._connection.execute(
                "DELETE FROM duplicate_scans WHERE id = ?",
                (scan_id,),
            )


_DUPLICATE_SCAN_COLUMNS = """
    id,
    analysis_id,
    state,
    stage,
    candidate_file_count,
    quick_completed_count,
    full_hash_candidate_count,
    full_hash_completed_count,
    issue_count,
    requested_utc,
    updated_utc,
    started_utc,
    completed_utc,
    reason_code
"""


_DUPLICATE_SCAN_CANDIDATES_CTE = """
WITH physical_entries AS (
    SELECT
        snapshots.analysis_id,
        entries.snapshot_id,
        entries.endpoint_id,
        entries.id AS file_entry_id,
        entries.relative_path,
        entries.size_bytes,
        COALESCE(
            alias_members.group_id,
            'entry:' || entries.snapshot_id || ':' || entries.id
        ) AS physical_object_key,
        row_number() OVER (
            PARTITION BY
                entries.snapshot_id,
                COALESCE(
                    alias_members.group_id,
                    'entry:' || entries.snapshot_id || ':' || entries.id
                )
            ORDER BY entries.id
        ) AS physical_ordinal
    FROM snapshots
    INNER JOIN file_entries AS entries
        ON entries.snapshot_id = snapshots.id
        AND entries.endpoint_id = snapshots.endpoint_id
        AND entries.object_type = 'file'
        AND entries.size_bytes IS NOT NULL
    LEFT JOIN file_object_alias_members AS alias_members
        ON alias_members.snapshot_id = entries.snapshot_id
        AND alias_members.file_entry_id = entries.id
    WHERE snapshots.analysis_id = ?
        AND snapshots.complete = 1
        AND snapshots.immutable = 1
),
canonical_entries AS (
    SELECT *
    FROM physical_entries
    WHERE physical_ordinal = 1
),
candidate_sizes AS (
    SELECT size_bytes
    FROM canonical_entries
    GROUP BY size_bytes
    HAVING count(*) >= 2
),
duplicate_candidates AS (
    SELECT canonical_entries.*
    FROM canonical_entries
    INNER JOIN candidate_sizes
        ON candidate_sizes.size_bytes = canonical_entries.size_bytes
)
"""


def _scan_from_row(row: tuple[object, ...]) -> DuplicateScanStatus:
    return DuplicateScanStatus(
        scan_id=str(row[0]),
        analysis_id=str(row[1]),
        state=DuplicateScanState(str(row[2])),
        stage=DuplicateScanStage(str(row[3])),
        candidate_file_count=_required_int(row[4]),
        quick_completed_count=_required_int(row[5]),
        full_hash_candidate_count=_required_int(row[6]),
        full_hash_completed_count=_required_int(row[7]),
        issue_count=_required_int(row[8]),
        requested_utc=str(row[9]),
        updated_utc=str(row[10]),
        started_utc=None if row[11] is None else str(row[11]),
        completed_utc=None if row[12] is None else str(row[12]),
        reason_code=None if row[13] is None else str(row[13]),
    )


def _group_from_row(row: tuple[object, ...]) -> DuplicateGroupReadModel:
    return DuplicateGroupReadModel(
        group_id=str(row[0]),
        relationship_class=str(row[1]),
        full_hash=str(row[2]),
        size_bytes=_required_int(row[3]),
        member_count=_required_int(row[4]),
        physical_object_count=_required_int(row[5]),
        expected_replica_count=_required_int(row[6]),
        potential_savings_bytes=_required_int(row[7]),
        review_state=str(row[8]),
        created_utc=str(row[9]),
    )


def _member_from_row(row: tuple[object, ...]) -> DuplicateMemberReadModel:
    relative_path = str(row[4])
    safe_path = parse_endpoint_relative_path(relative_path)
    root = local_path_from_file_uri(str(row[8]))
    return DuplicateMemberReadModel(
        group_id=str(row[0]),
        snapshot_id=str(row[1]),
        endpoint_id=str(row[2]),
        file_entry_id=str(row[3]),
        relative_path=relative_path,
        member_role=str(row[5]),
        physical_object_key=str(row[6]),
        endpoint_role=str(row[7]),
        absolute_path=str(root.joinpath(*safe_path.parts)),
        size_bytes=_required_int(row[9]),
        evidence_kind=str(row[10]),
    )


def _hash_request_id(scan_id: str, snapshot_id: str, entry_id: str) -> str:
    digest = hashlib.sha256(
        f"hash-request\0{scan_id}\0{snapshot_id}\0{entry_id}".encode()
    ).hexdigest()
    return f"hash-request:{digest[:32]}"


def _parent_path(relative_path: object) -> str:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative path is invalid")
    parent = PurePosixPath(relative_path.replace("\\", "/")).parent.as_posix()
    return "." if parent in {"", "."} else parent


def _file_id_reliability(capabilities_json: str | None) -> str:
    if capabilities_json is None:
        return FileIdReliability.UNAVAILABLE.value
    try:
        profile = EndpointCapabilities.from_json(capabilities_json)
    except EndpointCapabilityEvidenceError as exc:
        raise SqliteDuplicateScannerError(
            "DUPLICATE_SCAN_CAPABILITY_EVIDENCE_INVALID"
        ) from exc
    if not profile.supports_file_ids:
        return FileIdReliability.UNAVAILABLE.value
    return profile.file_id_reliability.value


def _error_code(exc: Exception) -> str:
    validation_code = getattr(exc, "validation_code", None)
    if isinstance(validation_code, str) and validation_code.strip():
        return validation_code
    text = str(exc).strip()
    if text and " " not in text and len(text) <= 128:
        return text
    return type(exc).__name__.upper()


def _required_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("duplicate-scan integer column is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("duplicate-scan integer column is invalid")
