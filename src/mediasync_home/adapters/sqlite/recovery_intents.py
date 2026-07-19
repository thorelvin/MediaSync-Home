from __future__ import annotations

import sqlite3
from typing import Any

from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentDurabilityState,
    RecoveryIntentSegmentState,
    RecoveryIntentSegmentStore,
    validate_recovery_intent_segment,
)


class SqliteRecoveryIntentSegmentStoreError(ValueError):
    pass


class SqliteRecoveryIntentSegmentStore(RecoveryIntentSegmentStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def publish_intent_segment(self, segment: RecoveryIntentSegment) -> RecoveryIntentSegment:
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
                raise SqliteRecoveryIntentSegmentStoreError("RECOVERY_INTENT_SEGMENT_LOAD_FAILED")
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
            raise SqliteRecoveryIntentSegmentStoreError("RECOVERY_INTENT_SEGMENT_LEASE_MISMATCH")
        if (
            str(row[0]) != segment.owner_installation_id
            or int(row[1]) != segment.ownership_epoch
            or int(row[2]) != segment.fencing_token
            or str(row[3]) != segment.target_endpoint_id
            or str(row[4]) != "ACQUIRED"
        ):
            raise SqliteRecoveryIntentSegmentStoreError("RECOVERY_INTENT_SEGMENT_LEASE_MISMATCH")

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
            raise SqliteRecoveryIntentSegmentStoreError("RECOVERY_INTENT_SEGMENT_CHAIN_MISMATCH")

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
