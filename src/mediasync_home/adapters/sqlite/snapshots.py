from __future__ import annotations

import hashlib
import sqlite3

from mediasync_home.application.snapshots import (
    SealedSnapshot,
    SnapshotBatchCommitReceipt,
    SnapshotBatchSummary,
    SnapshotEntryBatch,
    SnapshotEntryMaterializationStore,
    SnapshotFileEntry,
    SnapshotSealRequest,
    SnapshotSealStore,
    snapshot_seal,
    validate_snapshot_entry_batch,
    validate_snapshot_seal_request,
)


class SqliteSnapshotEntryStoreError(ValueError):
    pass


class SqliteSnapshotEntryStore(SnapshotEntryMaterializationStore, SnapshotSealStore):
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
                    approximate_bytes=existing.approximate_bytes,
                    idempotent_replay=True,
                )

            endpoint_id = self._load_mutable_snapshot_endpoint(batch.snapshot_id)
            self._connection.execute(
                """
                INSERT INTO snapshot_batches (
                    snapshot_id,
                    sequence_no,
                    payload_hash,
                    entry_count,
                    approximate_bytes,
                    state
                )
                VALUES (?, ?, ?, ?, ?, 'COMMITTED')
                """,
                (
                    batch.snapshot_id,
                    batch.sequence_no,
                    batch.payload_hash,
                    len(batch.entries),
                    batch.approximate_bytes,
                ),
            )
            for entry in batch.entries:
                self._connection.execute(
                    """
                    INSERT INTO file_entries (
                        snapshot_id,
                        endpoint_id,
                        id,
                        relative_path,
                        comparison_key,
                        object_type,
                        size_bytes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch.snapshot_id,
                        endpoint_id,
                        entry.entry_id,
                        entry.relative_path,
                        entry.comparison_key,
                        entry.object_type,
                        entry.size_bytes,
                    ),
                )
            self._materialize_case_collisions(batch.snapshot_id)
            self._connection.execute(
                """
                UPDATE snapshots
                SET
                    entry_count = entry_count + ?,
                    total_bytes = total_bytes + ?
                WHERE id = ?
                    AND immutable = 0
                """,
                (
                    len(batch.entries),
                    sum(entry.size_bytes or 0 for entry in batch.entries),
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
                approximate_bytes=batch.approximate_bytes,
                idempotent_replay=False,
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
            SELECT id, relative_path, comparison_key, object_type, size_bytes
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
            )
            for row in rows
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
            entries = self.load_snapshot_entries(request.snapshot_id)
            actual_case_groups = self._expected_case_collision_group_count(request.snapshot_id)
            self._validate_snapshot_ready_for_seal(
                request=request,
                snapshot_entry_count=snapshot_counts[0],
                snapshot_total_bytes=snapshot_counts[1],
                batches=batches,
                entries=entries,
                actual_case_groups=actual_case_groups,
            )
            sealed = snapshot_seal(
                snapshot_id=request.snapshot_id,
                entries=entries,
                batches=batches,
                case_collision_group_count=actual_case_groups,
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
                    snapshot_checksum = ?
                WHERE id = ?
                    AND immutable = 0
                """,
                (
                    sealed.snapshot_schema_version,
                    sealed.checksum_algorithm,
                    sealed.serializer_version,
                    sealed.snapshot_checksum,
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
            case_collision_group_count=self._expected_case_collision_group_count(snapshot_id),
            complete=bool(row[6]),
            immutable=bool(row[7]),
        )

    def _load_batch_receipt(
        self,
        snapshot_id: str,
        sequence_no: int,
    ) -> SnapshotBatchCommitReceipt | None:
        row = self._connection.execute(
            """
            SELECT payload_hash, entry_count, approximate_bytes
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
            approximate_bytes=int(row[2]),
            idempotent_replay=False,
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

    def _materialize_case_collisions(self, snapshot_id: str) -> None:
        rows = self._connection.execute(
            """
            SELECT comparison_key
            FROM file_entries
            WHERE snapshot_id = ?
            GROUP BY comparison_key
            HAVING count(*) > 1
            ORDER BY comparison_key
            """,
            (snapshot_id,),
        ).fetchall()
        for row in rows:
            comparison_key = str(row[0])
            group_id = self._case_group_id(snapshot_id=snapshot_id, comparison_key=comparison_key)
            self._connection.execute(
                """
                INSERT INTO case_collision_groups (snapshot_id, id, comparison_key)
                VALUES (?, ?, ?)
                ON CONFLICT(snapshot_id, comparison_key) DO NOTHING
                """,
                (snapshot_id, group_id, comparison_key),
            )
            persisted_group_id = self._case_group_id(
                snapshot_id=snapshot_id,
                comparison_key=comparison_key,
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO case_collision_members (
                    snapshot_id,
                    group_id,
                    file_entry_id
                )
                SELECT snapshot_id, ?, id
                FROM file_entries
                WHERE snapshot_id = ?
                    AND comparison_key = ?
                """,
                (persisted_group_id, snapshot_id, comparison_key),
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

    def _load_snapshot_counts_for_seal(self, snapshot_id: str) -> tuple[int, int]:
        row = self._connection.execute(
            """
            SELECT entry_count, total_bytes, immutable
            FROM snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_SNAPSHOT_NOT_FOUND")
        if int(row[2]) != 0:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_INCOMPLETE")
        return (int(row[0]), int(row[1]))

    def _load_batch_summaries(self, snapshot_id: str) -> tuple[SnapshotBatchSummary, ...]:
        rows = self._connection.execute(
            """
            SELECT sequence_no, payload_hash, entry_count, approximate_bytes
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
                approximate_bytes=int(row[3]),
            )
            for row in rows
        )

    def _validate_snapshot_ready_for_seal(
        self,
        *,
        request: SnapshotSealRequest,
        snapshot_entry_count: int,
        snapshot_total_bytes: int,
        batches: tuple[SnapshotBatchSummary, ...],
        entries: tuple[SnapshotFileEntry, ...],
        actual_case_groups: int,
    ) -> None:
        if snapshot_entry_count != request.expected_entry_count or len(entries) != request.expected_entry_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_ENTRY_COUNT_MISMATCH")
        if snapshot_total_bytes != request.expected_total_bytes:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BYTES_MISMATCH")
        if sum(entry.size_bytes or 0 for entry in entries) != request.expected_total_bytes:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_ENTRY_BYTES_MISMATCH")
        if len(batches) != request.expected_batch_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_COUNT_MISMATCH")
        if sum(batch.entry_count for batch in batches) != request.expected_entry_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_BATCH_ENTRY_COUNT_MISMATCH")
        if actual_case_groups != request.expected_case_collision_group_count:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_CASE_GROUP_COUNT_MISMATCH")
        if self._missing_case_collision_member_count(request.snapshot_id) != 0:
            raise SqliteSnapshotEntryStoreError("SNAPSHOT_SEAL_CASE_COLLISIONS_INCOMPLETE")

    def _validate_existing_seal_matches_request(
        self,
        existing: SealedSnapshot,
        request: SnapshotSealRequest,
    ) -> None:
        if (
            existing.entry_count != request.expected_entry_count
            or existing.total_bytes != request.expected_total_bytes
            or existing.batch_count != request.expected_batch_count
            or existing.case_collision_group_count != request.expected_case_collision_group_count
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
