from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace

from mediasync_home.application.trigger_occurrences import (
    TERMINAL_TRIGGER_OCCURRENCE_STATES,
    TriggerKind,
    TriggerOccurrenceConflict,
    TriggerOccurrence,
    TriggerOccurrenceRegistration,
    TriggerOccurrenceState,
    TriggerOccurrenceStore,
    ensure_trigger_occurrence_compatible,
)


class SqliteTriggerOccurrenceStoreError(ValueError):
    pass


class SqliteTriggerOccurrenceStore(TriggerOccurrenceStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_received(
        self, occurrence: TriggerOccurrence
    ) -> TriggerOccurrenceRegistration:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_trigger_occurrence_by_deduplication_key(
                occurrence.deduplication_key
            )
            tombstone = self._load_trigger_tombstone(occurrence.deduplication_key)
            if existing is not None:
                ensure_trigger_occurrence_compatible(existing, occurrence)
                if (
                    tombstone is not None
                    and tombstone.payload_hash != existing.payload_hash
                ):
                    raise SqliteTriggerOccurrenceStoreError(
                        "TRIGGER_OCCURRENCE_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return TriggerOccurrenceRegistration(
                    occurrence=existing, deduplicated=True
                )
            if tombstone is not None:
                if tombstone.payload_hash != occurrence.payload_hash:
                    raise SqliteTriggerOccurrenceStoreError(
                        "TRIGGER_OCCURRENCE_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return TriggerOccurrenceRegistration(
                    occurrence=replace(
                        occurrence,
                        state=tombstone.terminal_state,
                        terminal_effect_hash=tombstone.terminal_effect_hash,
                    ),
                    deduplicated=True,
                    compacted=True,
                )

            self._connection.execute(
                """
                INSERT INTO trigger_occurrences (
                    id,
                    schedule_id,
                    schedule_revision_hash,
                    job_id,
                    occurrence_key,
                    deduplication_key,
                    first_delivery_id,
                    occurrence_slot_utc,
                    source_instance_key,
                    trigger_type,
                    payload_hash,
                    state,
                    run_id,
                    terminal_effect_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _occurrence_parameters(occurrence),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except (
            sqlite3.Error,
            TriggerOccurrenceConflict,
            SqliteTriggerOccurrenceStoreError,
        ) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, TriggerOccurrenceConflict):
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_CONFLICT"
                ) from exc
            if isinstance(exc, SqliteTriggerOccurrenceStoreError):
                raise
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_RECORD_FAILED"
            ) from exc
        return TriggerOccurrenceRegistration(occurrence=occurrence, deduplicated=False)

    def load_trigger_occurrence(self, occurrence_id: str) -> TriggerOccurrence | None:
        return self._load_one("WHERE id = ?", (occurrence_id,))

    def load_trigger_occurrence_by_deduplication_key(
        self,
        deduplication_key: str,
    ) -> TriggerOccurrence | None:
        return self._load_one("WHERE deduplication_key = ?", (deduplication_key,))

    def mark_run_enqueued(
        self,
        *,
        deduplication_key: str,
        run_id: str,
    ) -> TriggerOccurrence:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE trigger_occurrences
                SET
                    state = 'RUN_ENQUEUED',
                    run_id = ?
                WHERE deduplication_key = ?
                    AND state IN ('RECEIVED', 'RUN_ENQUEUED')
                    AND (run_id IS NULL OR run_id = ?)
                """,
                (run_id, deduplication_key, run_id),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_RUN_ENQUEUE_CONFLICT"
                )
            updated = self.load_trigger_occurrence_by_deduplication_key(
                deduplication_key
            )
            if updated is None:
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return updated
        except (sqlite3.Error, SqliteTriggerOccurrenceStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteTriggerOccurrenceStoreError):
                raise
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_RUN_ENQUEUE_FAILED"
            ) from exc

    def mark_terminal(
        self,
        *,
        deduplication_key: str,
        state: TriggerOccurrenceState,
        terminal_effect_hash: str,
        run_id: str | None = None,
    ) -> TriggerOccurrence:
        if state not in TERMINAL_TRIGGER_OCCURRENCE_STATES:
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_TERMINAL_STATE_REQUIRED"
            )
        if len(terminal_effect_hash) != 64:
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_TERMINAL_HASH_REQUIRED"
            )
        outer_transaction = self._connection.in_transaction
        try:
            cursor = self._connection.execute(
                """
                UPDATE trigger_occurrences
                SET
                    state = ?,
                    run_id = COALESCE(?, run_id),
                    terminal_effect_hash = ?,
                    completed_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE deduplication_key = ?
                """,
                (state.value, run_id, terminal_effect_hash, deduplication_key),
            )
            if cursor.rowcount != 1:
                raise SqliteTriggerOccurrenceStoreError("TRIGGER_OCCURRENCE_NOT_FOUND")
            updated = self.load_trigger_occurrence_by_deduplication_key(
                deduplication_key
            )
            if updated is None:
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.commit()
            return updated
        except (sqlite3.Error, SqliteTriggerOccurrenceStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteTriggerOccurrenceStoreError):
                raise
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_UPDATE_FAILED"
            ) from exc

    def list_run_enqueued_trigger_occurrences(
        self,
        *,
        limit: int,
    ) -> tuple[TriggerOccurrence, ...]:
        if limit < 1 or limit > 100:
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_RECONCILIATION_LIMIT_INVALID"
            )
        rows = self._connection.execute(
            f"""
            SELECT {_OCCURRENCE_COLUMNS}
            FROM trigger_occurrences
            WHERE state = 'RUN_ENQUEUED'
                AND run_id IS NOT NULL
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(_occurrence_from_row(row) for row in rows)

    def compact_terminal_trigger_occurrence(
        self, deduplication_key: str
    ) -> TriggerOccurrence:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            occurrence = self.load_trigger_occurrence_by_deduplication_key(
                deduplication_key
            )
            if occurrence is None:
                tombstone = self._load_trigger_tombstone(deduplication_key)
                if tombstone is None:
                    raise SqliteTriggerOccurrenceStoreError(
                        "TRIGGER_OCCURRENCE_NOT_FOUND"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_ALREADY_COMPACTED"
                )
            if occurrence.state not in TERMINAL_TRIGGER_OCCURRENCE_STATES:
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_COMPACTION_REQUIRES_TERMINAL"
                )
            if occurrence.terminal_effect_hash is None:
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_COMPACTION_REQUIRES_EFFECT_HASH"
                )
            self._connection.execute(
                """
                INSERT INTO effect_dedup_tombstones (
                    deduplication_key,
                    effect_kind,
                    payload_hash,
                    terminal_state,
                    effect_entity_type,
                    effect_entity_id,
                    terminal_effect_hash
                )
                VALUES (?, 'trigger', ?, ?, 'trigger_occurrence', ?, ?)
                """,
                (
                    occurrence.deduplication_key,
                    occurrence.payload_hash,
                    occurrence.state.value,
                    occurrence.occurrence_id,
                    occurrence.terminal_effect_hash,
                ),
            )
            cursor = self._connection.execute(
                "DELETE FROM trigger_occurrences WHERE deduplication_key = ?",
                (deduplication_key,),
            )
            if cursor.rowcount != 1:
                raise SqliteTriggerOccurrenceStoreError(
                    "TRIGGER_OCCURRENCE_COMPACTION_DELETE_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return occurrence
        except (sqlite3.Error, SqliteTriggerOccurrenceStoreError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteTriggerOccurrenceStoreError):
                raise
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_OCCURRENCE_COMPACTION_FAILED"
            ) from exc

    def _load_trigger_tombstone(
        self, deduplication_key: str
    ) -> _TriggerTombstone | None:
        row = self._connection.execute(
            """
            SELECT payload_hash, terminal_state, terminal_effect_hash
            FROM effect_dedup_tombstones
            WHERE deduplication_key = ?
                AND effect_kind = 'trigger'
            """,
            (deduplication_key,),
        ).fetchone()
        if row is None:
            return None
        if row[2] is None:
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_TOMBSTONE_REQUIRES_EFFECT_HASH"
            )
        try:
            terminal_state = TriggerOccurrenceState(str(row[1]))
        except ValueError as exc:
            raise SqliteTriggerOccurrenceStoreError(
                "TRIGGER_TOMBSTONE_TERMINAL_STATE_UNSUPPORTED"
            ) from exc
        return _TriggerTombstone(
            payload_hash=str(row[0]),
            terminal_state=terminal_state,
            terminal_effect_hash=str(row[2]),
        )

    def _load_one(
        self,
        where_clause: str,
        parameters: tuple[object, ...],
    ) -> TriggerOccurrence | None:
        row = self._connection.execute(
            f"""
            SELECT {_OCCURRENCE_COLUMNS}
            FROM trigger_occurrences
            {where_clause}
            """,
            parameters,
        ).fetchone()
        if row is None:
            return None
        return _occurrence_from_row(row)


_OCCURRENCE_COLUMNS = """
    id,
    schedule_id,
    schedule_revision_hash,
    job_id,
    occurrence_key,
    deduplication_key,
    first_delivery_id,
    occurrence_slot_utc,
    source_instance_key,
    trigger_type,
    payload_hash,
    state,
    run_id,
    terminal_effect_hash
"""


@dataclass(frozen=True)
class _TriggerTombstone:
    payload_hash: str
    terminal_state: TriggerOccurrenceState
    terminal_effect_hash: str


def _occurrence_from_row(row: Sequence[object]) -> TriggerOccurrence:
    return TriggerOccurrence(
        occurrence_id=str(row[0]),
        schedule_id=str(row[1]),
        schedule_revision_hash=str(row[2]),
        job_id=str(row[3]),
        occurrence_key=str(row[4]),
        deduplication_key=str(row[5]),
        first_delivery_id=str(row[6]),
        occurrence_slot_utc=None if row[7] is None else str(row[7]),
        source_instance_key=None if row[8] is None else str(row[8]),
        trigger_type=TriggerKind(str(row[9])),
        payload_hash=str(row[10]),
        state=TriggerOccurrenceState(str(row[11])),
        run_id=None if row[12] is None else str(row[12]),
        terminal_effect_hash=None if row[13] is None else str(row[13]),
    )


def _occurrence_parameters(occurrence: TriggerOccurrence) -> tuple[object, ...]:
    return (
        occurrence.occurrence_id,
        occurrence.schedule_id,
        occurrence.schedule_revision_hash,
        occurrence.job_id,
        occurrence.occurrence_key,
        occurrence.deduplication_key,
        occurrence.first_delivery_id,
        occurrence.occurrence_slot_utc,
        occurrence.source_instance_key,
        occurrence.trigger_type.value,
        occurrence.payload_hash,
        occurrence.state.value,
        occurrence.run_id,
        occurrence.terminal_effect_hash,
    )
