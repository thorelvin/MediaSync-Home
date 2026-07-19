from __future__ import annotations

import hashlib
import sqlite3

from mediasync_home.application.snapshots import (
    SnapshotBatchCommitReceipt,
    SnapshotEntryBatch,
    SnapshotEntryMaterializationStore,
    SnapshotFileEntry,
    validate_snapshot_entry_batch,
)


class SqliteSnapshotEntryStoreError(ValueError):
    pass


class SqliteSnapshotEntryStore(SnapshotEntryMaterializationStore):
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
