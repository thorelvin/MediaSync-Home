from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator

from mediasync_home.application.snapshots import (
    SNAPSHOT_COMPLETE_COVERAGE_STATE,
    SNAPSHOT_CHECKSUM_ALGORITHM,
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_SERIALIZER_VERSION,
    SealedSnapshot,
    SnapshotBatchCommitReceipt,
    SnapshotBatchSummary,
    SnapshotCoverageCursor,
    SnapshotCoveragePage,
    SnapshotCoveragePageQuery,
    SnapshotCoverageReadModel,
    SnapshotCoverageReadModelStore,
    SnapshotDirectoryCoverage,
    SnapshotEntryBatch,
    SnapshotEntryCursor,
    SnapshotEntryMaterializationStore,
    SnapshotEntryPage,
    SnapshotEntryPageQuery,
    SnapshotEntryReadModel,
    SnapshotEntryReadModelStore,
    SnapshotFileEntry,
    SnapshotFilterDecision,
    SnapshotFilterDecisionCursor,
    SnapshotFilterDecisionPage,
    SnapshotFilterDecisionPageQuery,
    SnapshotFilterDecisionReadModel,
    SnapshotFilterDecisionReadModelStore,
    SnapshotIssueCursor,
    SnapshotIssuePage,
    SnapshotIssuePageQuery,
    SnapshotIssueReadModel,
    SnapshotIssueReadModelStore,
    SnapshotIssue,
    SnapshotSealRequest,
    SnapshotSealStore,
    validate_snapshot_coverage_page_query,
    validate_snapshot_filter_decision_page_query,
    validate_snapshot_issue_page_query,
    validate_snapshot_entry_page_query,
    validate_snapshot_entry_batch,
    validate_snapshot_seal_request,
)


class SqliteSnapshotEntryStoreError(ValueError):
    pass


class SqliteSnapshotEntryStore(
    SnapshotEntryMaterializationStore,
    SnapshotSealStore,
    SnapshotEntryReadModelStore,
    SnapshotCoverageReadModelStore,
    SnapshotIssueReadModelStore,
    SnapshotFilterDecisionReadModelStore,
):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def commit_snapshot_entry_batch(self, batch: SnapshotEntryBatch) -> SnapshotBatchCommitReceipt:
        validate_snapshot_entry_batch(batch)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")

            existing = self._load_batch_receipt(batch.snapshot_id, batch.sequence_no)
            if existing is not None:
                if (
                    existing.payload_hash != batch.payload_hash
                    or existing.entry_count != len(batch.entries)
                    or existing.coverage_update_count != len(batch.coverage_updates)
                    or existing.issue_count != len(batch.issues)
                    or existing.filter_decision_count != len(batch.filter_decisions)
                    or existing.approximate_bytes != batch.approximate_bytes
                ):
                    raise SqliteSnapshotEntryStoreError("SNAPSHOT_BATCH_CONFLICT")
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return SnapshotBatchCommitReceipt(
                    snapshot_id=existing.snapshot_id,
                    sequence_no=existing.sequence_no,
                    payload_hash=existing.payload_hash,
                    entry_count=existing.entry_count,
                    coverage_update_count=existing.coverage_update_count,
                    issue_count=existing.issue_count,
                    approximate_bytes=existing.approximate_bytes,
                    idempotent_replay=True,
                    filter_decision_count=existing.filter_decision_count,
                )

            endpoint_id = self._load_mutable_snapshot_endpoint(batch.snapshot_id)
            self._connection.execute(
                """
                INSERT INTO snapshot_batches (
                    snapshot_id,
                    sequence_no,
                    payload_hash,
                    entry_count,
                    coverage_update_count,
                    issue_count,
                    filter_decision_count,
                    approximate_bytes,
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED')
                """,
                (
                    batch.snapshot_id,
                    batch.sequence_no,
                    batch.payload_hash,
                    len(batch.entries),
                    len(batch.coverage_updates),
                    len(batch.issues),
                    len(batch.filter_decisions),
                    batch.approximate_bytes,
                ),
            )
            self._connection.executemany(
                """
                    INSERT INTO file_entries (
                        snapshot_id,
                        endpoint_id,
                        id,
                        relative_path,
                        comparison_key,
                        object_type,
                        size_bytes,
                        birthtime_ns,
                        identity_fingerprint_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        batch.snapshot_id,
                        endpoint_id,
                        entry.entry_id,
                        entry.relative_path,
                        entry.comparison_key,
                        entry.object_type,
                        entry.size_bytes,
                        entry.birthtime_ns,
                        entry.identity_fingerprint_hash,
                    )
                    for entry in batch.entries
                ),
            )
            self._connection.executemany(
                """
                    INSERT INTO directory_coverage (
                        snapshot_id,
                        relative_path,
                        comparison_key,
                        coverage_state,
                        case_mode,
                        case_mode_evidence,
                        case_context_hash,
                        case_probe_error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        batch.snapshot_id,
                        coverage.relative_path,
                        coverage.comparison_key,
                        coverage.coverage_state,
                        coverage.case_mode,
                        coverage.case_mode_evidence,
                        coverage.case_context_hash,
                        coverage.case_probe_error,
                    )
                    for coverage in batch.coverage_updates
                ),
            )
            self._connection.executemany(
                """
                    INSERT INTO snapshot_issues (
                        snapshot_id,
                        relative_path,
                        issue_type,
                        error_code,
                        sanitized_message,
                        blocks_destructive_actions
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        batch.snapshot_id,
                        issue.relative_path,
                        issue.issue_type,
                        issue.error_code,
                        issue.sanitized_message,
                        1 if issue.blocks_destructive_actions else 0,
                    )
                    for issue in batch.issues
                ),
            )
            self._connection.executemany(
                """
                    INSERT INTO snapshot_filter_decisions (
                        snapshot_id,
                        relative_path,
                        object_type,
                        decision_state,
                        reason_code,
                        matched_rule_id,
                        evaluation_stage
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        batch.snapshot_id,
                        decision.relative_path,
                        decision.object_type,
                        decision.decision_state,
                        decision.reason_code,
                        decision.matched_rule_id,
                        decision.evaluation_stage,
                    )
                    for decision in batch.filter_decisions
                ),
            )
            self._materialize_case_collisions(
                batch.snapshot_id,
                comparison_keys=tuple(
                    {entry.comparison_key for entry in batch.entries}
                ),
            )
            self._connection.execute(
                """
                UPDATE snapshots
                SET
                    entry_count = entry_count + ?,
                    total_bytes = total_bytes + ?,
                    filter_decision_count = filter_decision_count + ?
                WHERE id = ?
                    AND immutable = 0
                """,
                (
                    len(batch.entries),
                    sum(entry.size_bytes or 0 for entry in batch.entries),
                    len(batch.filter_decisions),
                    batch.snapshot_id,
                ),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return SnapshotBatchCommitReceipt(
                snapshot_id=batch.snapshot_id,
                sequence_no=batch.sequence_no,
                payload_hash=batch.payload_hash,
                entry_count=len(batch.entries),
                coverage_update_count=len(batch.coverage_updates),
                issue_count=len(batch.issues),
                approximate_bytes=batch.approximate_bytes,
                idempotent_replay=False,
                filter_decision_count=len(batch.filter_decisions),
            )
        except SqliteSnapshotEntryStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_BATCH_PERSISTENCE_CONFLICT") from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_BATCH_PERSISTENCE_FAILED") from exc

    def load_snapshot_entries(self, snapshot_id: str) -> tuple[SnapshotFileEntry, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                relative_path,
                comparison_key,
                object_type,
                size_bytes,
                birthtime_ns,
                identity_fingerprint_hash
            FROM file_entries
            WHERE snapshot_id = ?
            ORDER BY relative_path, id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SnapshotFileEntry(
                entry_id=str(row[0]),
                relative_path=str(row[1]),
                comparison_key=str(row[2]),
                object_type=str(row[3]),
                size_bytes=None if row[4] is None else int(row[4]),
                birthtime_ns=None if row[5] is None else int(row[5]),
                identity_fingerprint_hash=None if row[6] is None else str(row[6]),
            )
            for row in rows
        )

    def load_directory_coverage(self, snapshot_id: str) -> tuple[SnapshotDirectoryCoverage, ...]:
        rows = self._connection.execute(
            """
            SELECT
                relative_path,
                comparison_key,
                coverage_state,
                case_mode,
                case_mode_evidence,
                case_context_hash,
                case_probe_error
            FROM directory_coverage
            WHERE snapshot_id = ?
            ORDER BY relative_path
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SnapshotDirectoryCoverage(
                relative_path=str(row[0]),
                comparison_key=str(row[1]),
                coverage_state=str(row[2]),
                case_mode=str(row[3]),
                case_mode_evidence=str(row[4]),
                case_context_hash=str(row[5]),
                case_probe_error=None if row[6] is None else str(row[6]),
            )
            for row in rows
        )

    def load_snapshot_issues(self, snapshot_id: str) -> tuple[SnapshotIssue, ...]:
        rows = self._connection.execute(
            """
            SELECT
                relative_path,
                issue_type,
                blocks_destructive_actions,
                error_code,
                sanitized_message
            FROM snapshot_issues
            WHERE snapshot_id = ?
            ORDER BY relative_path, issue_type, id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SnapshotIssue(
                relative_path=str(row[0]),
                issue_type=str(row[1]),
                blocks_destructive_actions=bool(row[2]),
                error_code=None if row[3] is None else str(row[3]),
                sanitized_message=None if row[4] is None else str(row[4]),
            )
            for row in rows
        )

    def load_snapshot_filter_decisions(
        self,
        snapshot_id: str,
    ) -> tuple[SnapshotFilterDecision, ...]:
        rows = self._connection.execute(
            """
            SELECT
                relative_path,
                object_type,
                decision_state,
                reason_code,
                matched_rule_id,
                evaluation_stage
            FROM snapshot_filter_decisions
            WHERE snapshot_id = ?
            ORDER BY relative_path, id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SnapshotFilterDecision(
                relative_path=str(row[0]),
                object_type=str(row[1]),
                decision_state=str(row[2]),
                reason_code=str(row[3]),
                matched_rule_id=None if row[4] is None else str(row[4]),
                evaluation_stage=str(row[5]),
            )
            for row in rows
        )

    def page_snapshot_entries(self, query: SnapshotEntryPageQuery) -> SnapshotEntryPage:
        validate_snapshot_entry_page_query(query)
        rows = self._connection.execute(
            _snapshot_entry_page_sql(query.after),
            (*_snapshot_entry_page_parameters(query), query.limit + 1),
        ).fetchall()
        page_rows = rows[: query.limit]
        entries = tuple(
            SnapshotEntryReadModel(
                entry_id=str(row[0]),
                relative_path=str(row[1]),
                comparison_key=str(row[2]),
                object_type=str(row[3]),
                size_bytes=None if row[4] is None else int(row[4]),
                birthtime_ns=None if row[5] is None else int(row[5]),
                case_collision_group_id=None if row[6] is None else str(row[6]),
            )
            for row in page_rows
        )
        has_more = len(rows) > query.limit
        next_cursor = _snapshot_entry_cursor(entries[-1]) if has_more and entries else None
        return SnapshotEntryPage(
            snapshot_id=query.snapshot_id,
            entries=entries,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def page_snapshot_directory_coverage(
        self,
        query: SnapshotCoveragePageQuery,
    ) -> SnapshotCoveragePage:
        validate_snapshot_coverage_page_query(query)
        rows = self._connection.execute(
            _snapshot_coverage_page_sql(query),
            (*_snapshot_coverage_page_parameters(query), query.limit + 1),
        ).fetchall()
        page_rows = rows[: query.limit]
        coverage = tuple(
            SnapshotCoverageReadModel(
                relative_path=str(row[0]),
                comparison_key=str(row[1]),
                coverage_state=str(row[2]),
                case_mode=str(row[3]),
                case_mode_evidence=str(row[4]),
                case_context_hash=str(row[5]),
                case_probe_error=None if row[6] is None else str(row[6]),
            )
            for row in page_rows
        )
        has_more = len(rows) > query.limit
        return SnapshotCoveragePage(
            snapshot_id=query.snapshot_id,
            coverage=coverage,
            next_cursor=_snapshot_coverage_cursor(coverage[-1]) if has_more and coverage else None,
            has_more=has_more,
        )

    def page_snapshot_issues(self, query: SnapshotIssuePageQuery) -> SnapshotIssuePage:
        validate_snapshot_issue_page_query(query)
        rows = self._connection.execute(
            _snapshot_issue_page_sql(query),
            (*_snapshot_issue_page_parameters(query), query.limit + 1),
        ).fetchall()
        page_rows = rows[: query.limit]
        issues = tuple(
            SnapshotIssueReadModel(
                issue_id=int(row[0]),
                relative_path=str(row[1]),
                issue_type=str(row[2]),
                blocks_destructive_actions=bool(row[3]),
                error_code=None if row[4] is None else str(row[4]),
                sanitized_message=None if row[5] is None else str(row[5]),
            )
            for row in page_rows
        )
        has_more = len(rows) > query.limit
        return SnapshotIssuePage(
            snapshot_id=query.snapshot_id,
            issues=issues,
            next_cursor=_snapshot_issue_cursor(issues[-1]) if has_more and issues else None,
            has_more=has_more,
        )

    def page_snapshot_filter_decisions(
        self,
        query: SnapshotFilterDecisionPageQuery,
    ) -> SnapshotFilterDecisionPage:
        validate_snapshot_filter_decision_page_query(query)
        rows = self._connection.execute(
            _snapshot_filter_decision_page_sql(query),
            (*_snapshot_filter_decision_page_parameters(query), query.limit + 1),
        ).fetchall()
        page_rows = rows[: query.limit]
        decisions = tuple(
            SnapshotFilterDecisionReadModel(
                decision_id=int(row[0]),
                relative_path=str(row[1]),
                object_type=str(row[2]),
                decision_state=str(row[3]),
                reason_code=str(row[4]),
                matched_rule_id=None if row[5] is None else str(row[5]),
                evaluation_stage=str(row[6]),
            )
            for row in page_rows
        )
        has_more = len(rows) > query.limit
        return SnapshotFilterDecisionPage(
            snapshot_id=query.snapshot_id,
            decisions=decisions,
            next_cursor=(
                _snapshot_filter_decision_cursor(decisions[-1])
                if has_more and decisions
                else None
            ),
            has_more=has_more,
        )

    def seal_snapshot(self, request: SnapshotSealRequest) -> SealedSnapshot:
        validate_snapshot_seal_request(request)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")

            existing = self.load_sealed_snapshot(request.snapshot_id)
            if existing is not None:
                self._validate_existing_seal_matches_request(existing, request)
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing

            snapshot_counts = self._load_snapshot_counts_for_seal(request.snapshot_id)
            batches = self._load_batch_summaries(request.snapshot_id)
            self._materialize_case_collisions(request.snapshot_id)
            actual_case_groups = self._expected_case_collision_group_count(request.snapshot_id)
            self._validate_snapshot_ready_for_streaming_seal(
                request=request,
                snapshot_entry_count=snapshot_counts[0],
                snapshot_total_bytes=snapshot_counts[1],
                snapshot_filter_decision_count=snapshot_counts[2],
                batches=batches,
                actual_case_groups=actual_case_groups,
            )
            sealed = SealedSnapshot(
                snapshot_id=request.snapshot_id,
                snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
                checksum_algorithm=SNAPSHOT_CHECKSUM_ALGORITHM,
                serializer_version=SNAPSHOT_SERIALIZER_VERSION,
                snapshot_checksum=self._stream_snapshot_checksum(
                    request=request,
                    batches=batches,
                    case_collision_group_count=actual_case_groups,
                ),
                entry_count=request.expected_entry_count,
                total_bytes=request.expected_total_bytes,
                batch_count=len(batches),
                directory_coverage_count=request.expected_directory_coverage_count,
                issue_count=request.expected_issue_count,
                blocking_issue_count=request.expected_blocking_issue_count,
                case_collision_group_count=actual_case_groups,
                filter_decision_count=request.expected_filter_decision_count,
            )
            cursor = self._connection.execute(
                """
                UPDATE snapshots
                SET
                    complete = 1,
                    immutable = 1,
                    sealed_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    snapshot_schema_version = ?,
                    checksum_algorithm = ?,
                    serializer_version = ?,
                    snapshot_checksum = ?,
                    scan_error_count = ?,
                    volatile_directory_count = ?
                WHERE id = ?
                    AND immutable = 0
                """,
                (
                    sealed.snapshot_schema_version,
                    sealed.checksum_algorithm,
                    sealed.serializer_version,
                    sealed.snapshot_checksum,
                    sealed.issue_count,
                    self._volatile_directory_count(request.snapshot_id),
                    sealed.snapshot_id,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_CONFLICT")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return sealed
        except SqliteSnapshotEntryStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_CONFLICT") from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_FAILED") from exc

    def load_sealed_snapshot(self, snapshot_id: str) -> SealedSnapshot | None:
        row = self._connection.execute(
            """
            SELECT
                snapshot_schema_version,
                checksum_algorithm,
                serializer_version,
                snapshot_checksum,
                entry_count,
                total_bytes,
                scan_error_count,
                volatile_directory_count,
                filter_decision_count,
                complete,
                immutable
            FROM snapshots
            WHERE id = ?
                AND immutable = 1
                AND snapshot_checksum IS NOT NULL
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return SealedSnapshot(
            snapshot_id=snapshot_id,
            snapshot_schema_version=int(row[0]),
            checksum_algorithm=str(row[1]),
            serializer_version=str(row[2]),
            snapshot_checksum=str(row[3]),
            entry_count=int(row[4]),
            total_bytes=int(row[5]),
            batch_count=len(self._load_batch_summaries(snapshot_id)),
            directory_coverage_count=len(self.load_directory_coverage(snapshot_id)),
            issue_count=int(row[6]),
            blocking_issue_count=self._blocking_issue_count(snapshot_id),
            case_collision_group_count=self._expected_case_collision_group_count(snapshot_id),
            filter_decision_count=int(row[8]),
            complete=bool(row[9]),
            immutable=bool(row[10]),
        )

    def _load_batch_receipt(
        self,
        snapshot_id: str,
        sequence_no: int,
    ) -> SnapshotBatchCommitReceipt | None:
        row = self._connection.execute(
            """
            SELECT
                payload_hash,
                entry_count,
                coverage_update_count,
                issue_count,
                approximate_bytes,
                filter_decision_count
            FROM snapshot_batches
            WHERE snapshot_id = ?
                AND sequence_no = ?
            """,
            (snapshot_id, sequence_no),
        ).fetchone()
        if row is None:
            return None
        return SnapshotBatchCommitReceipt(
            snapshot_id=snapshot_id,
            sequence_no=sequence_no,
            payload_hash=str(row[0]),
            entry_count=int(row[1]),
            coverage_update_count=int(row[2]),
            issue_count=int(row[3]),
            approximate_bytes=int(row[4]),
            idempotent_replay=False,
            filter_decision_count=int(row[5]),
        )

    def _load_mutable_snapshot_endpoint(self, snapshot_id: str) -> str:
        row = self._connection.execute(
            """
            SELECT endpoint_id, immutable
            FROM snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_BATCH_SNAPSHOT_NOT_FOUND")
        if int(row[1]) != 0:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_IMMUTABLE")
        return str(row[0])

    def _materialize_case_collisions(
        self,
        snapshot_id: str,
        *,
        comparison_keys: tuple[str, ...] | None = None,
    ) -> None:
        if comparison_keys == ():
            return
        key_filter = ""
        parameters: tuple[object, ...] = (snapshot_id,)
        if comparison_keys is not None:
            placeholders = ",".join("?" for _ in comparison_keys)
            key_filter = f"AND comparison_key IN ({placeholders})"
            parameters = (snapshot_id, *comparison_keys)
        rows = self._connection.execute(
            f"""
            SELECT comparison_key
            FROM file_entries
            WHERE snapshot_id = ?
                {key_filter}
            GROUP BY comparison_key
            HAVING count(*) > 1
            ORDER BY comparison_key
            """,
            parameters,
        ).fetchall()
        groups = tuple(
            (
                snapshot_id,
                self._case_group_id(
                    snapshot_id=snapshot_id,
                    comparison_key=str(row[0]),
                ),
                str(row[0]),
            )
            for row in rows
        )
        self._connection.executemany(
            """
                INSERT INTO case_collision_groups (snapshot_id, id, comparison_key)
                VALUES (?, ?, ?)
                ON CONFLICT(snapshot_id, comparison_key) DO NOTHING
            """,
            groups,
        )
        if not groups:
            return
        collision_keys = tuple(str(row[0]) for row in rows)
        collision_placeholders = ",".join("?" for _ in collision_keys)
        self._connection.execute(
            f"""
            INSERT OR IGNORE INTO case_collision_members (
                snapshot_id,
                group_id,
                file_entry_id
            )
            SELECT entry.snapshot_id, group_row.id, entry.id
            FROM file_entries AS entry
            INNER JOIN case_collision_groups AS group_row
                ON group_row.snapshot_id = entry.snapshot_id
                AND group_row.comparison_key = entry.comparison_key
            WHERE entry.snapshot_id = ?
                AND entry.comparison_key IN ({collision_placeholders})
            """,
            (snapshot_id, *collision_keys),
        )

    def _case_group_id(self, *, snapshot_id: str, comparison_key: str) -> str:
        row = self._connection.execute(
            """
            SELECT id
            FROM case_collision_groups
            WHERE snapshot_id = ?
                AND comparison_key = ?
            """,
            (snapshot_id, comparison_key),
        ).fetchone()
        if row is not None:
            return str(row[0])
        digest = hashlib.sha256(f"{snapshot_id}\0{comparison_key}".encode("utf-8")).hexdigest()
        return f"case:{digest[:32]}"

    def _load_snapshot_counts_for_seal(self, snapshot_id: str) -> tuple[int, int, int]:
        row = self._connection.execute(
            """
            SELECT entry_count, total_bytes, filter_decision_count, immutable
            FROM snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_SNAPSHOT_NOT_FOUND")
        if int(row[3]) != 0:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_INCOMPLETE")
        return (int(row[0]), int(row[1]), int(row[2]))

    def _load_batch_summaries(self, snapshot_id: str) -> tuple[SnapshotBatchSummary, ...]:
        rows = self._connection.execute(
            """
            SELECT
                sequence_no,
                payload_hash,
                entry_count,
                coverage_update_count,
                issue_count,
                approximate_bytes,
                filter_decision_count
            FROM snapshot_batches
            WHERE snapshot_id = ?
            ORDER BY sequence_no
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SnapshotBatchSummary(
                sequence_no=int(row[0]),
                payload_hash=str(row[1]),
                entry_count=int(row[2]),
                coverage_update_count=int(row[3]),
                issue_count=int(row[4]),
                approximate_bytes=int(row[5]),
                filter_decision_count=int(row[6]),
            )
            for row in rows
        )

    def _validate_snapshot_ready_for_streaming_seal(
        self,
        *,
        request: SnapshotSealRequest,
        snapshot_entry_count: int,
        snapshot_total_bytes: int,
        snapshot_filter_decision_count: int,
        batches: tuple[SnapshotBatchSummary, ...],
        actual_case_groups: int,
    ) -> None:
        entry_row = self._connection.execute(
            """
            SELECT count(*), coalesce(sum(size_bytes), 0)
            FROM file_entries
            WHERE snapshot_id = ?
            """,
            (request.snapshot_id,),
        ).fetchone()
        coverage_row = self._connection.execute(
            """
            SELECT
                count(*),
                coalesce(sum(coverage_state <> ?), 0)
            FROM directory_coverage
            WHERE snapshot_id = ?
            """,
            (SNAPSHOT_COMPLETE_COVERAGE_STATE, request.snapshot_id),
        ).fetchone()
        issue_row = self._connection.execute(
            """
            SELECT count(*), coalesce(sum(blocks_destructive_actions), 0)
            FROM snapshot_issues
            WHERE snapshot_id = ?
            """,
            (request.snapshot_id,),
        ).fetchone()
        filter_row = self._connection.execute(
            """
            SELECT count(*)
            FROM snapshot_filter_decisions
            WHERE snapshot_id = ?
            """,
            (request.snapshot_id,),
        ).fetchone()
        if None in {entry_row, coverage_row, issue_row, filter_row}:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_FAILED")
        assert entry_row is not None
        assert coverage_row is not None
        assert issue_row is not None
        assert filter_row is not None
        entry_count, entry_bytes = int(entry_row[0]), int(entry_row[1])
        coverage_count, incomplete_coverage = (
            int(coverage_row[0]),
            int(coverage_row[1]),
        )
        issue_count, blocking_issue_count = int(issue_row[0]), int(issue_row[1])
        filter_count = int(filter_row[0])
        if snapshot_entry_count != request.expected_entry_count or entry_count != request.expected_entry_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_ENTRY_COUNT_MISMATCH")
        if snapshot_total_bytes != request.expected_total_bytes:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BYTES_MISMATCH")
        if entry_bytes != request.expected_total_bytes:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_ENTRY_BYTES_MISMATCH")
        if len(batches) != request.expected_batch_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_COUNT_MISMATCH")
        if sum(batch.entry_count for batch in batches) != request.expected_entry_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_ENTRY_COUNT_MISMATCH")
        if coverage_count != request.expected_directory_coverage_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_COVERAGE_COUNT_MISMATCH")
        if sum(batch.coverage_update_count for batch in batches) != coverage_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_COVERAGE_COUNT_MISMATCH")
        if issue_count != request.expected_issue_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_ISSUE_COUNT_MISMATCH")
        if sum(batch.issue_count for batch in batches) != issue_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_ISSUE_COUNT_MISMATCH")
        if snapshot_filter_decision_count != request.expected_filter_decision_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_FILTER_COUNT_MISMATCH")
        if filter_count != request.expected_filter_decision_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_FILTER_COUNT_MISMATCH")
        if sum(batch.filter_decision_count for batch in batches) != filter_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_FILTER_COUNT_MISMATCH")
        if blocking_issue_count != request.expected_blocking_issue_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BLOCKING_COUNT_MISMATCH")
        if incomplete_coverage:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_COVERAGE_INCOMPLETE")
        if blocking_issue_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BLOCKING_ISSUES")
        if actual_case_groups != request.expected_case_collision_group_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MISMATCH")
        if self._missing_case_collision_member_count(request.snapshot_id) != 0:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_CASE_COLLISIONS_INCOMPLETE")

    def _stream_snapshot_checksum(
        self,
        *,
        request: SnapshotSealRequest,
        batches: tuple[SnapshotBatchSummary, ...],
        case_collision_group_count: int,
    ) -> str:
        digest = hashlib.sha256()
        first_field = True

        def write(value: str) -> None:
            digest.update(value.encode("utf-8"))

        def encoded(value: object) -> str:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))

        def field(name: str, value: object) -> None:
            nonlocal first_field
            if not first_field:
                write(",")
            first_field = False
            write(encoded(name))
            write(":")
            write(encoded(value))

        def list_field(name: str, values: Iterable[object]) -> None:
            nonlocal first_field
            if not first_field:
                write(",")
            first_field = False
            write(encoded(name))
            write(":[")
            first_item = True
            for value in values:
                if not first_item:
                    write(",")
                first_item = False
                write(encoded(value))
            write("]")

        snapshot_id = request.snapshot_id
        write("{")
        field("batch_count", len(batches))
        list_field(
            "batches",
            (
                {
                    "approximate_bytes": batch.approximate_bytes,
                    "coverage_update_count": batch.coverage_update_count,
                    "entry_count": batch.entry_count,
                    "filter_decision_count": batch.filter_decision_count,
                    "issue_count": batch.issue_count,
                    "payload_hash": batch.payload_hash,
                    "sequence_no": batch.sequence_no,
                }
                for batch in batches
            ),
        )
        field("blocking_issue_count", request.expected_blocking_issue_count)
        field("case_collision_group_count", case_collision_group_count)
        field("checksum_algorithm", SNAPSHOT_CHECKSUM_ALGORITHM)
        field("complete", True)
        list_field("coverage", self._stream_coverage_payloads(snapshot_id))
        field("directory_coverage_count", request.expected_directory_coverage_count)
        list_field("entries", self._stream_entry_payloads(snapshot_id))
        field("entry_count", request.expected_entry_count)
        field("filter_decision_count", request.expected_filter_decision_count)
        list_field(
            "filter_decisions",
            self._stream_filter_decision_payloads(snapshot_id),
        )
        field("immutable", True)
        field("issue_count", request.expected_issue_count)
        list_field("issues", self._stream_issue_payloads(snapshot_id))
        field("serializer_version", SNAPSHOT_SERIALIZER_VERSION)
        field("snapshot_id", snapshot_id)
        field("snapshot_schema_version", SNAPSHOT_SCHEMA_VERSION)
        field("total_bytes", request.expected_total_bytes)
        write("}")
        return digest.hexdigest()

    def _stream_entry_payloads(
        self,
        snapshot_id: str,
    ) -> Iterator[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT id, relative_path, comparison_key, object_type,
                   size_bytes, birthtime_ns, identity_fingerprint_hash
            FROM file_entries
            WHERE snapshot_id = ?
            ORDER BY relative_path, id
            """,
            (snapshot_id,),
        )
        return (
            {
                "birthtime_ns": None if row[5] is None else int(row[5]),
                "comparison_key": str(row[2]),
                "entry_id": str(row[0]),
                "identity_fingerprint_hash": None if row[6] is None else str(row[6]),
                "object_type": str(row[3]),
                "relative_path": str(row[1]),
                "size_bytes": None if row[4] is None else int(row[4]),
            }
            for row in rows
        )

    def _stream_coverage_payloads(
        self,
        snapshot_id: str,
    ) -> Iterator[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT relative_path, comparison_key, coverage_state, case_mode,
                   case_mode_evidence, case_context_hash, case_probe_error
            FROM directory_coverage
            WHERE snapshot_id = ?
            ORDER BY relative_path
            """,
            (snapshot_id,),
        )
        return (
            {
                "case_context_hash": str(row[5]),
                "case_mode": str(row[3]),
                "case_mode_evidence": str(row[4]),
                "case_probe_error": None if row[6] is None else str(row[6]),
                "comparison_key": str(row[1]),
                "coverage_state": str(row[2]),
                "relative_path": str(row[0]),
            }
            for row in rows
        )

    def _stream_issue_payloads(
        self,
        snapshot_id: str,
    ) -> Iterator[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT relative_path, issue_type, blocks_destructive_actions,
                   error_code, sanitized_message
            FROM snapshot_issues
            WHERE snapshot_id = ?
            ORDER BY relative_path, issue_type, coalesce(error_code, ''),
                     coalesce(sanitized_message, ''), blocks_destructive_actions
            """,
            (snapshot_id,),
        )
        return (
            {
                "blocks_destructive_actions": bool(row[2]),
                "error_code": None if row[3] is None else str(row[3]),
                "issue_type": str(row[1]),
                "relative_path": str(row[0]),
                "sanitized_message": None if row[4] is None else str(row[4]),
            }
            for row in rows
        )

    def _stream_filter_decision_payloads(
        self,
        snapshot_id: str,
    ) -> Iterator[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT relative_path, object_type, decision_state, reason_code,
                   matched_rule_id, evaluation_stage
            FROM snapshot_filter_decisions
            WHERE snapshot_id = ?
            ORDER BY relative_path, decision_state, reason_code,
                     coalesce(matched_rule_id, ''), evaluation_stage
            """,
            (snapshot_id,),
        )
        return (
            {
                "decision_state": str(row[2]),
                "evaluation_stage": str(row[5]),
                "matched_rule_id": None if row[4] is None else str(row[4]),
                "object_type": str(row[1]),
                "reason_code": str(row[3]),
                "relative_path": str(row[0]),
            }
            for row in rows
        )

    def _validate_existing_seal_matches_request(
        self,
        existing: SealedSnapshot,
        request: SnapshotSealRequest,
    ) -> None:
        if (
            existing.entry_count != request.expected_entry_count
            or existing.total_bytes != request.expected_total_bytes
            or existing.batch_count != request.expected_batch_count
            or existing.directory_coverage_count != request.expected_directory_coverage_count
            or existing.issue_count != request.expected_issue_count
            or existing.blocking_issue_count != request.expected_blocking_issue_count
            or existing.case_collision_group_count != request.expected_case_collision_group_count
            or existing.filter_decision_count != request.expected_filter_decision_count
        ):
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_CONFLICT")

    def _expected_case_collision_group_count(self, snapshot_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT comparison_key
                FROM file_entries
                WHERE snapshot_id = ?
                GROUP BY comparison_key
                HAVING count(*) > 1
            )
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_FAILED")
        return int(row[0])

    def _blocking_issue_count(self, snapshot_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM snapshot_issues
            WHERE snapshot_id = ?
                AND blocks_destructive_actions = 1
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_FAILED")
        return int(row[0])

    def _volatile_directory_count(self, snapshot_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM directory_coverage
            WHERE snapshot_id = ?
                AND coverage_state = 'VOLATILE'
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_FAILED")
        return int(row[0])

    def _missing_case_collision_member_count(self, snapshot_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM file_entries AS entry
            WHERE entry.snapshot_id = ?
                AND 1 < (
                    SELECT count(*)
                    FROM file_entries AS peer
                    WHERE peer.snapshot_id = entry.snapshot_id
                        AND peer.comparison_key = entry.comparison_key
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM case_collision_groups AS group_row
                    INNER JOIN case_collision_members AS member
                        ON member.snapshot_id = group_row.snapshot_id
                        AND member.group_id = group_row.id
                        AND member.file_entry_id = entry.id
                    WHERE group_row.snapshot_id = entry.snapshot_id
                        AND group_row.comparison_key = entry.comparison_key
                )
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_PERSISTENCE_FAILED")
        return int(row[0])


def _snapshot_entry_page_sql(after: SnapshotEntryCursor | None) -> str:
    cursor_clause = ""
    if after is not None:
        cursor_clause = """
                AND (
                    entry.comparison_key > ?
                    OR (entry.comparison_key = ? AND entry.relative_path > ?)
                    OR (entry.comparison_key = ? AND entry.relative_path = ? AND entry.id > ?)
                )
        """
    return f"""
            SELECT
                entry.id,
                entry.relative_path,
                entry.comparison_key,
                entry.object_type,
                entry.size_bytes,
                entry.birthtime_ns,
                member.group_id
            FROM file_entries AS entry
            LEFT JOIN case_collision_members AS member
                ON member.snapshot_id = entry.snapshot_id
                AND member.file_entry_id = entry.id
            WHERE entry.snapshot_id = ?
            {cursor_clause}
            ORDER BY entry.comparison_key, entry.relative_path, entry.id
            LIMIT ?
            """


def _snapshot_entry_page_parameters(query: SnapshotEntryPageQuery) -> tuple[object, ...]:
    if query.after is None:
        return (query.snapshot_id,)
    return (
        query.snapshot_id,
        query.after.comparison_key,
        query.after.comparison_key,
        query.after.relative_path,
        query.after.comparison_key,
        query.after.relative_path,
        query.after.entry_id,
    )


def _snapshot_entry_cursor(entry: SnapshotEntryReadModel) -> SnapshotEntryCursor:
    return SnapshotEntryCursor(
        comparison_key=entry.comparison_key,
        relative_path=entry.relative_path,
        entry_id=entry.entry_id,
    )


def _snapshot_coverage_page_sql(query: SnapshotCoveragePageQuery) -> str:
    state_clause = ""
    if query.coverage_states:
        placeholders = ", ".join("?" for _ in query.coverage_states)
        state_clause = f"AND coverage.coverage_state IN ({placeholders})"
    cursor_clause = ""
    if query.after is not None:
        cursor_clause = """
                AND (
                    coverage.comparison_key > ?
                    OR (
                        coverage.comparison_key = ?
                        AND coverage.relative_path > ?
                    )
                )
        """
    return f"""
            SELECT
                coverage.relative_path,
                coverage.comparison_key,
                coverage.coverage_state,
                coverage.case_mode,
                coverage.case_mode_evidence,
                coverage.case_context_hash,
                coverage.case_probe_error
            FROM directory_coverage AS coverage
            WHERE coverage.snapshot_id = ?
                {state_clause}
                {cursor_clause}
            ORDER BY coverage.comparison_key, coverage.relative_path
            LIMIT ?
            """


def _snapshot_coverage_page_parameters(query: SnapshotCoveragePageQuery) -> tuple[object, ...]:
    parameters: list[object] = [query.snapshot_id]
    parameters.extend(query.coverage_states)
    if query.after is not None:
        parameters.extend(
            (
                query.after.comparison_key,
                query.after.comparison_key,
                query.after.relative_path,
            )
        )
    return tuple(parameters)


def _snapshot_coverage_cursor(coverage: SnapshotCoverageReadModel) -> SnapshotCoverageCursor:
    return SnapshotCoverageCursor(
        comparison_key=coverage.comparison_key,
        relative_path=coverage.relative_path,
    )


def _snapshot_issue_page_sql(query: SnapshotIssuePageQuery) -> str:
    blocking_clause = ""
    if query.blocking_only:
        blocking_clause = "AND issue.blocks_destructive_actions = 1"
    cursor_clause = ""
    if query.after is not None:
        cursor_clause = """
                AND (
                    issue.relative_path > ?
                    OR (
                        issue.relative_path = ?
                        AND issue.issue_type > ?
                    )
                    OR (
                        issue.relative_path = ?
                        AND issue.issue_type = ?
                        AND issue.id > ?
                    )
                )
        """
    return f"""
            SELECT
                issue.id,
                issue.relative_path,
                issue.issue_type,
                issue.blocks_destructive_actions,
                issue.error_code,
                issue.sanitized_message
            FROM snapshot_issues AS issue
            WHERE issue.snapshot_id = ?
                {blocking_clause}
                {cursor_clause}
            ORDER BY issue.relative_path, issue.issue_type, issue.id
            LIMIT ?
            """


def _snapshot_issue_page_parameters(query: SnapshotIssuePageQuery) -> tuple[object, ...]:
    parameters: list[object] = [query.snapshot_id]
    if query.after is not None:
        parameters.extend(
            (
                query.after.relative_path,
                query.after.relative_path,
                query.after.issue_type,
                query.after.relative_path,
                query.after.issue_type,
                query.after.issue_id,
            )
        )
    return tuple(parameters)


def _snapshot_issue_cursor(issue: SnapshotIssueReadModel) -> SnapshotIssueCursor:
    return SnapshotIssueCursor(
        relative_path=issue.relative_path,
        issue_type=issue.issue_type,
        issue_id=issue.issue_id,
    )


def _snapshot_filter_decision_page_sql(
    query: SnapshotFilterDecisionPageQuery,
) -> str:
    state_clause = ""
    if query.decision_states:
        placeholders = ", ".join("?" for _state in query.decision_states)
        state_clause = f"AND decision.decision_state IN ({placeholders})"
    cursor_clause = ""
    if query.after is not None:
        cursor_clause = """
                AND (
                    decision.relative_path > ?
                    OR (
                        decision.relative_path = ?
                        AND decision.id > ?
                    )
                )
        """
    return f"""
            SELECT
                decision.id,
                decision.relative_path,
                decision.object_type,
                decision.decision_state,
                decision.reason_code,
                decision.matched_rule_id,
                decision.evaluation_stage
            FROM snapshot_filter_decisions AS decision
            WHERE decision.snapshot_id = ?
                {state_clause}
                {cursor_clause}
            ORDER BY decision.relative_path, decision.id
            LIMIT ?
            """


def _snapshot_filter_decision_page_parameters(
    query: SnapshotFilterDecisionPageQuery,
) -> tuple[object, ...]:
    parameters: list[object] = [query.snapshot_id]
    parameters.extend(query.decision_states)
    if query.after is not None:
        parameters.extend(
            (
                query.after.relative_path,
                query.after.relative_path,
                query.after.decision_id,
            )
        )
    return tuple(parameters)


def _snapshot_filter_decision_cursor(
    decision: SnapshotFilterDecisionReadModel,
) -> SnapshotFilterDecisionCursor:
    return SnapshotFilterDecisionCursor(
        relative_path=decision.relative_path,
        decision_id=decision.decision_id,
    )
