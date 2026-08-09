from __future__ import annotations

import sqlite3
from enum import Enum

from mediasync_home.application.cross_store_handoffs import (
    CrossStoreHandoff,
    CrossStoreHandoffStore,
    RecoveryRunBinding,
    RecoveryRunStartPeerStore,
    decode_handoff_payload,
    handoff_evidence_mismatch,
)
from mediasync_home.generated.contract_types import CrossStoreHandoffState


class SqliteCrossStoreHandoffError(ValueError):
    pass


class SqliteCrossStoreHandoffTable(str, Enum):
    CATALOG = "store_handoffs"
    RECOVERY = "recovery_handoffs"


_TERMINAL_STATES = {
    CrossStoreHandoffState.COMPLETED,
    CrossStoreHandoffState.ABORTED,
    CrossStoreHandoffState.AMBIGUOUS,
}


class SqliteCrossStoreHandoffStore(CrossStoreHandoffStore):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        table: SqliteCrossStoreHandoffTable,
    ) -> None:
        self._connection = connection
        self._table = table.value

    def record_handoff(self, handoff: CrossStoreHandoff) -> CrossStoreHandoff:
        decode_handoff_payload(handoff)
        if handoff.state not in {
            CrossStoreHandoffState.PREPARED,
            CrossStoreHandoffState.PEER_COMMITTED,
        }:
            raise SqliteCrossStoreHandoffError("HANDOFF_INSERT_STATE_INVALID")
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_handoff(handoff.handoff_id)
            if existing is not None:
                if handoff_evidence_mismatch(existing, handoff) is not None:
                    raise SqliteCrossStoreHandoffError("HANDOFF_IDEMPOTENCY_CONFLICT")
                if existing.state is not handoff.state:
                    if existing.state in _TERMINAL_STATES:
                        if not outer_transaction:
                            self._connection.execute("COMMIT")
                        return existing
                    raise SqliteCrossStoreHandoffError("HANDOFF_STATE_CONFLICT")
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing
            self._connection.execute(
                f"""
                INSERT INTO {self._table} (
                    id,
                    handoff_type,
                    direction,
                    payload_schema_version,
                    entity_type,
                    entity_id,
                    payload_json,
                    payload_hash,
                    state,
                    expected_peer_state,
                    attempt_count,
                    last_error_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _handoff_parameters(handoff),
            )
            recorded = self.load_handoff(handoff.handoff_id)
            if recorded is None:
                raise SqliteCrossStoreHandoffError("HANDOFF_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return recorded
        except (sqlite3.Error, SqliteCrossStoreHandoffError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteCrossStoreHandoffError):
                raise
            raise SqliteCrossStoreHandoffError("HANDOFF_PERSISTENCE_FAILED") from exc

    def load_handoff(self, handoff_id: str) -> CrossStoreHandoff | None:
        row = self._connection.execute(
            f"""
            SELECT
                id,
                handoff_type,
                direction,
                payload_schema_version,
                entity_type,
                entity_id,
                payload_json,
                payload_hash,
                state,
                expected_peer_state,
                attempt_count,
                last_error_code
            FROM {self._table}
            WHERE id = ?
            """,
            (handoff_id,),
        ).fetchone()
        return None if row is None else _handoff_from_row(row)

    def transition_handoff(
        self,
        *,
        handoff_id: str,
        expected_state: CrossStoreHandoffState,
        next_state: CrossStoreHandoffState,
        expected_payload_hash: str,
        last_error_code: str | None = None,
    ) -> CrossStoreHandoff | None:
        _validate_transition(expected_state, next_state)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            current = self.load_handoff(handoff_id)
            if current is None:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            if current.payload_hash != expected_payload_hash:
                raise SqliteCrossStoreHandoffError("HANDOFF_PAYLOAD_HASH_CONFLICT")
            if current.state is next_state:
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return current
            if current.state is not expected_state:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            cursor = self._connection.execute(
                f"""
                UPDATE {self._table}
                SET
                    state = ?,
                    expected_peer_state = ?,
                    attempt_count = attempt_count + 1,
                    last_error_code = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    completed_utc = CASE
                        WHEN ? = 'COMPLETED'
                            THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        ELSE NULL
                    END
                WHERE id = ? AND state = ? AND payload_hash = ?
                """,
                (
                    next_state.value,
                    _expected_peer_after(next_state).value,
                    last_error_code,
                    next_state.value,
                    handoff_id,
                    expected_state.value,
                    expected_payload_hash,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            updated = self.load_handoff(handoff_id)
            if updated is None:
                raise SqliteCrossStoreHandoffError("HANDOFF_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return updated
        except (sqlite3.Error, SqliteCrossStoreHandoffError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteCrossStoreHandoffError):
                raise
            raise SqliteCrossStoreHandoffError("HANDOFF_TRANSITION_FAILED") from exc

    def list_handoffs_for_reconciliation(
        self,
        *,
        handoff_type: str,
        direction: str,
        limit: int,
    ) -> tuple[CrossStoreHandoff, ...]:
        if limit < 1 or limit > 1000:
            raise SqliteCrossStoreHandoffError("HANDOFF_LIST_LIMIT_INVALID")
        rows = self._connection.execute(
            f"""
            SELECT
                id,
                handoff_type,
                direction,
                payload_schema_version,
                entity_type,
                entity_id,
                payload_json,
                payload_hash,
                state,
                expected_peer_state,
                attempt_count,
                last_error_code
            FROM {self._table}
            WHERE handoff_type = ?
                AND direction = ?
                AND state != 'ABORTED'
            ORDER BY
                CASE WHEN state = 'COMPLETED' THEN 1 ELSE 0 END,
                updated_utc,
                id
            LIMIT ?
            """,
            (handoff_type, direction, limit),
        ).fetchall()
        return tuple(_handoff_from_row(row) for row in rows)


class SqliteRecoveryRunStartPeerStore(RecoveryRunStartPeerStore):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        handoffs: SqliteCrossStoreHandoffStore,
    ) -> None:
        self._connection = connection
        self._handoffs = handoffs

    def commit_run_start_peer(
        self,
        *,
        binding: RecoveryRunBinding,
        handoff: CrossStoreHandoff,
    ) -> CrossStoreHandoff:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_run_binding(binding.run_id)
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO recovery_runs (
                        run_id,
                        job_id,
                        job_revision_id,
                        plan_id,
                        plan_checksum,
                        start_handoff_id,
                        state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        binding.run_id,
                        binding.job_id,
                        binding.job_revision_id,
                        binding.plan_id,
                        binding.plan_checksum,
                        binding.start_handoff_id,
                        binding.state,
                    ),
                )
            elif existing != binding:
                raise SqliteCrossStoreHandoffError("RECOVERY_RUN_BINDING_CONFLICT")
            recorded = self._handoffs.record_handoff(handoff)
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return recorded
        except (sqlite3.Error, SqliteCrossStoreHandoffError) as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, SqliteCrossStoreHandoffError):
                raise
            raise SqliteCrossStoreHandoffError("RECOVERY_RUN_BINDING_FAILED") from exc

    def load_run_binding(self, run_id: str) -> RecoveryRunBinding | None:
        row = self._connection.execute(
            """
            SELECT
                run_id,
                job_id,
                job_revision_id,
                plan_id,
                plan_checksum,
                start_handoff_id,
                state
            FROM recovery_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RecoveryRunBinding(
            run_id=str(row[0]),
            job_id=str(row[1]),
            job_revision_id=str(row[2]),
            plan_id=str(row[3]),
            plan_checksum=str(row[4]),
            start_handoff_id=str(row[5]),
            state=str(row[6]),
        )


def _handoff_parameters(handoff: CrossStoreHandoff) -> tuple[object, ...]:
    return (
        handoff.handoff_id,
        handoff.handoff_type,
        handoff.direction,
        handoff.payload_schema_version,
        handoff.entity_type,
        handoff.entity_id,
        handoff.payload_json,
        handoff.payload_hash,
        handoff.state.value,
        handoff.expected_peer_state.value,
        handoff.attempt_count,
        handoff.last_error_code,
    )


def _handoff_from_row(row: sqlite3.Row | tuple[object, ...]) -> CrossStoreHandoff:
    return CrossStoreHandoff(
        handoff_id=str(row[0]),
        handoff_type=str(row[1]),
        direction=str(row[2]),
        payload_schema_version=int(str(row[3])),
        entity_type=str(row[4]),
        entity_id=str(row[5]),
        payload_json=str(row[6]),
        payload_hash=str(row[7]),
        state=CrossStoreHandoffState(str(row[8])),
        expected_peer_state=CrossStoreHandoffState(str(row[9])),
        attempt_count=int(str(row[10])),
        last_error_code=None if row[11] is None else str(row[11]),
    )


def _validate_transition(
    source: CrossStoreHandoffState,
    target: CrossStoreHandoffState,
) -> None:
    allowed = {
        CrossStoreHandoffState.PREPARED: {
            CrossStoreHandoffState.SOURCE_CONFIRMED,
            CrossStoreHandoffState.ABORTED,
            CrossStoreHandoffState.AMBIGUOUS,
        },
        CrossStoreHandoffState.PEER_COMMITTED: {
            CrossStoreHandoffState.COMPLETED,
            CrossStoreHandoffState.AMBIGUOUS,
        },
        CrossStoreHandoffState.SOURCE_CONFIRMED: {
            CrossStoreHandoffState.COMPLETED,
            CrossStoreHandoffState.AMBIGUOUS,
        },
    }
    if target not in allowed.get(source, set()):
        raise SqliteCrossStoreHandoffError(
            f"HANDOFF_TRANSITION_FORBIDDEN:{source.value}->{target.value}"
        )


def _expected_peer_after(state: CrossStoreHandoffState) -> CrossStoreHandoffState:
    if state is CrossStoreHandoffState.SOURCE_CONFIRMED:
        return CrossStoreHandoffState.COMPLETED
    if state is CrossStoreHandoffState.COMPLETED:
        return CrossStoreHandoffState.COMPLETED
    if state in {CrossStoreHandoffState.ABORTED, CrossStoreHandoffState.AMBIGUOUS}:
        return CrossStoreHandoffState.COMPLETED
    return CrossStoreHandoffState.PEER_COMMITTED
