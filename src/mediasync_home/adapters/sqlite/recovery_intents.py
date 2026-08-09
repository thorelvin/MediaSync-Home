from __future__ import annotations

import sqlite3
from typing import Any

from mediasync_home.generated.contract_types import RECOVERY_OPERATION_TERMINAL_PHASES
from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentDurabilityState,
    RecoveryIntentSegmentState,
    RecoveryIntentSegmentStore,
    validate_recovery_intent_segment,
)
from mediasync_home.application.run_intent_cleanup import (
    RecoveryIntentSegmentLifecycleStore,
)
from mediasync_home.application.target_recovery_intents import (
    MissingTerminalIntentSegmentFinalizer,
    RecoveryIntentSegmentReconciliationStore,
)


class SqliteRecoveryIntentSegmentStoreError(ValueError):
    pass


class SqliteRecoveryIntentSegmentStore(
    RecoveryIntentSegmentStore,
    RecoveryIntentSegmentReconciliationStore,
    RecoveryIntentSegmentLifecycleStore,
    MissingTerminalIntentSegmentFinalizer,
):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def publish_intent_segment(
        self, segment: RecoveryIntentSegment
    ) -> RecoveryIntentSegment:
        validate_recovery_intent_segment(segment)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")

            existing = self.load_intent_segment(segment.segment_id)
            if existing is not None:
                if existing != segment:
                    raise SqliteRecoveryIntentSegmentStoreError(
                        "RECOVERY_INTENT_SEGMENT_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing

            self._require_active_matching_lease(segment)
            self._require_segment_chain(segment)
            self._insert_segment(segment)
            published = self.load_intent_segment(segment.segment_id)
            if published is None:
                raise SqliteRecoveryIntentSegmentStoreError(
                    "RECOVERY_INTENT_SEGMENT_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return published
        except SqliteRecoveryIntentSegmentStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_PERSISTENCE_FAILED"
            ) from exc

    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                segment_sequence,
                relative_path,
                schema_version,
                operation_count,
                byte_count,
                segment_hash,
                previous_segment_hash,
                durability_state,
                state
            FROM recovery_intent_segments
            WHERE id = ?
            """,
            (segment_id,),
        ).fetchone()
        if row is None:
            return None
        return _segment_from_row(row)

    def load_latest_intent_segment_for_run_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> RecoveryIntentSegment | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                segment_sequence,
                relative_path,
                schema_version,
                operation_count,
                byte_count,
                segment_hash,
                previous_segment_hash,
                durability_state,
                state
            FROM recovery_intent_segments
            WHERE run_id = ?
                AND run_target_id = ?
            ORDER BY segment_sequence DESC
            LIMIT 1
            """,
            (run_id, run_target_id),
        ).fetchone()
        if row is None:
            return None
        return _segment_from_row(row)

    def import_intent_segment(
        self,
        segment: RecoveryIntentSegment,
    ) -> RecoveryIntentSegment:
        validate_recovery_intent_segment(segment)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_intent_segment(segment.segment_id)
            if existing is not None:
                if existing != segment:
                    raise SqliteRecoveryIntentSegmentStoreError(
                        "RECOVERY_INTENT_SEGMENT_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing
            self._require_historical_matching_lease(segment)
            self._require_segment_chain(segment)
            self._insert_segment(segment)
            imported = self.load_intent_segment(segment.segment_id)
            if imported is None:
                raise SqliteRecoveryIntentSegmentStoreError(
                    "RECOVERY_INTENT_SEGMENT_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return imported
        except SqliteRecoveryIntentSegmentStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_IMPORT_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_IMPORT_FAILED"
            ) from exc

    def list_unresolved_intent_segments(
        self,
        *,
        limit: int,
    ) -> tuple[RecoveryIntentSegment, ...]:
        if not 1 <= limit <= 10_000:
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_LIST_LIMIT_INVALID"
            )
        rows = self._connection.execute(
            """
            SELECT
                id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                segment_sequence,
                relative_path,
                schema_version,
                operation_count,
                byte_count,
                segment_hash,
                previous_segment_hash,
                durability_state,
                state
            FROM recovery_intent_segments
            WHERE state IN ('BUILDING', 'DURABLE', 'RECONCILED')
            ORDER BY run_target_id, segment_sequence, id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(_segment_from_row(row) for row in rows)

    def finalize_missing_terminal_intent_segment(self, segment_id: str) -> bool:
        terminal_phases = tuple(
            sorted(phase.value for phase in RECOVERY_OPERATION_TERMINAL_PHASES)
        )
        placeholders = ", ".join("?" for _ in terminal_phases)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                f"""
                UPDATE recovery_intent_segments AS candidate
                SET
                    state = 'CLEANED',
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE candidate.id = ?
                    AND candidate.state IN ('DURABLE', 'RECONCILED')
                    AND EXISTS (
                        SELECT 1
                        FROM recovery_operations AS bound
                        WHERE bound.run_id = candidate.run_id
                            AND bound.run_target_id = candidate.run_target_id
                            AND bound.intent_segment_id = candidate.id
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM recovery_operations AS unfinished
                        WHERE unfinished.run_id = candidate.run_id
                            AND unfinished.run_target_id = candidate.run_target_id
                            AND unfinished.phase NOT IN ({placeholders})
                    )
                """,
                (segment_id, *terminal_phases),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return cursor.rowcount == 1
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_TERMINAL_RECONCILIATION_FAILED"
            ) from exc

    def load_next_intent_segment_lifecycle_candidate(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> RecoveryIntentSegment | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                segment_sequence,
                relative_path,
                schema_version,
                operation_count,
                byte_count,
                segment_hash,
                previous_segment_hash,
                durability_state,
                state
            FROM recovery_intent_segments
            WHERE run_id = ?
                AND run_target_id = ?
                AND state IN ('DURABLE', 'RECONCILED', 'CLEANUP_ELIGIBLE')
            ORDER BY segment_sequence, id
            LIMIT 1
            """,
            (run_id, run_target_id),
        ).fetchone()
        if row is None:
            return None
        return _segment_from_row(row)

    def intent_segment_reconciliation_ready(self, *, segment_id: str) -> bool:
        terminal_phases = tuple(
            sorted(phase.value for phase in RECOVERY_OPERATION_TERMINAL_PHASES)
        )
        placeholders = ", ".join("?" for _ in terminal_phases)
        row = self._connection.execute(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM recovery_intent_segments AS candidate
                WHERE candidate.id = ?
                    AND candidate.state = 'DURABLE'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM recovery_operations AS operation
                        WHERE operation.run_id = candidate.run_id
                            AND operation.run_target_id = candidate.run_target_id
                            AND operation.phase NOT IN ({placeholders})
                    )
                    AND (
                        EXISTS (
                            SELECT 1
                            FROM recovery_operations AS operation
                            WHERE operation.intent_segment_id = candidate.id
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM recovery_intent_segments AS later
                            WHERE later.run_id = candidate.run_id
                                AND later.run_target_id = candidate.run_target_id
                                AND later.segment_sequence > candidate.segment_sequence
                                AND later.previous_segment_hash = candidate.segment_hash
                        )
                    )
            )
            """,
            (segment_id, *terminal_phases),
        ).fetchone()
        return row is not None and int(row[0]) == 1

    def transition_intent_segment_state(
        self,
        *,
        segment_id: str,
        expected_state: RecoveryIntentSegmentState,
        next_state: RecoveryIntentSegmentState,
    ) -> RecoveryIntentSegment | None:
        allowed = {
            RecoveryIntentSegmentState.DURABLE: RecoveryIntentSegmentState.RECONCILED,
            RecoveryIntentSegmentState.RECONCILED: (
                RecoveryIntentSegmentState.CLEANUP_ELIGIBLE
            ),
            RecoveryIntentSegmentState.CLEANUP_ELIGIBLE: (
                RecoveryIntentSegmentState.CLEANED
            ),
        }
        if allowed.get(expected_state) is not next_state:
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_LIFECYCLE_TRANSITION_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE recovery_intent_segments
                SET
                    state = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                    AND state = ?
                """,
                (next_state.value, segment_id, expected_state.value),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            transitioned = self.load_intent_segment(segment_id)
            if transitioned is None:
                raise SqliteRecoveryIntentSegmentStoreError(
                    "RECOVERY_INTENT_SEGMENT_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return transitioned
        except SqliteRecoveryIntentSegmentStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_LIFECYCLE_UPDATE_FAILED"
            ) from exc

    def _require_active_matching_lease(self, segment: RecoveryIntentSegment) -> None:
        row = self._connection.execute(
            """
            SELECT
                owner_instance_id,
                ownership_epoch,
                fencing_token,
                endpoint_id,
                state
            FROM resource_leases
            WHERE lease_id = ?
            """,
            (segment.lease_id,),
        ).fetchone()
        if row is None:
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_LEASE_MISMATCH"
            )
        if (
            str(row[0]) != segment.owner_installation_id
            or int(row[1]) != segment.ownership_epoch
            or int(row[2]) != segment.fencing_token
            or str(row[3]) != segment.target_endpoint_id
            or str(row[4]) != "ACQUIRED"
        ):
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_LEASE_MISMATCH"
            )

    def _require_historical_matching_lease(
        self, segment: RecoveryIntentSegment
    ) -> None:
        row = self._connection.execute(
            """
            SELECT
                owner_instance_id,
                ownership_epoch,
                fencing_token,
                run_id,
                run_target_id,
                endpoint_id
            FROM resource_leases
            WHERE lease_id = ?
            """,
            (segment.lease_id,),
        ).fetchone()
        if row is None or (
            str(row[0]) != segment.owner_installation_id
            or int(row[1]) != segment.ownership_epoch
            or int(row[2]) != segment.fencing_token
            or str(row[3]) != segment.run_id
            or str(row[4]) != segment.run_target_id
            or str(row[5]) != segment.target_endpoint_id
        ):
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_HISTORICAL_LEASE_MISMATCH"
            )

    def _require_segment_chain(self, segment: RecoveryIntentSegment) -> None:
        if segment.segment_sequence == 0:
            if segment.previous_segment_hash is not None:
                raise SqliteRecoveryIntentSegmentStoreError(
                    "RECOVERY_INTENT_SEGMENT_CHAIN_MISMATCH"
                )
            return

        row = self._connection.execute(
            """
            SELECT segment_hash
            FROM recovery_intent_segments
            WHERE run_target_id = ?
                AND segment_sequence = ?
            """,
            (segment.run_target_id, segment.segment_sequence - 1),
        ).fetchone()
        if row is None or segment.previous_segment_hash != str(row[0]):
            raise SqliteRecoveryIntentSegmentStoreError(
                "RECOVERY_INTENT_SEGMENT_CHAIN_MISMATCH"
            )

    def _insert_segment(self, segment: RecoveryIntentSegment) -> None:
        self._connection.execute(
            """
            INSERT INTO recovery_intent_segments (
                id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                segment_sequence,
                relative_path,
                schema_version,
                operation_count,
                byte_count,
                segment_hash,
                previous_segment_hash,
                durability_state,
                state
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment.segment_id,
                segment.run_id,
                segment.run_target_id,
                segment.target_endpoint_id,
                segment.target_endpoint_revision_id,
                segment.endpoint_generation,
                segment.owner_installation_id,
                segment.ownership_epoch,
                segment.lease_id,
                segment.fencing_token,
                segment.segment_sequence,
                segment.relative_path,
                segment.schema_version,
                segment.operation_count,
                segment.byte_count,
                segment.segment_hash,
                segment.previous_segment_hash,
                segment.durability_state.value,
                segment.state.value,
            ),
        )


def _segment_from_row(row: sqlite3.Row | tuple[Any, ...]) -> RecoveryIntentSegment:
    return RecoveryIntentSegment(
        segment_id=str(row[0]),
        run_id=str(row[1]),
        run_target_id=str(row[2]),
        target_endpoint_id=str(row[3]),
        target_endpoint_revision_id=str(row[4]),
        endpoint_generation=int(row[5]),
        owner_installation_id=str(row[6]),
        ownership_epoch=int(row[7]),
        lease_id=str(row[8]),
        fencing_token=int(row[9]),
        segment_sequence=int(row[10]),
        relative_path=str(row[11]),
        schema_version=int(row[12]),
        operation_count=int(row[13]),
        byte_count=int(row[14]),
        segment_hash=str(row[15]),
        previous_segment_hash=None if row[16] is None else str(row[16]),
        durability_state=RecoveryIntentSegmentDurabilityState(str(row[17])),
        state=RecoveryIntentSegmentState(str(row[18])),
    )
