from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any, Mapping

from mediasync_home.application.directory_recovery import (
    CONFLICT_STATE_BY_KIND,
    INITIAL_STATE_BY_KIND,
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryState,
    DirectoryRecoveryStore,
    DirectoryRecoveryTransition,
    canonical_directory_recovery_payload,
    validate_directory_recovery_operation,
    validate_directory_recovery_transition,
)
from mediasync_home.generated.contract_types import (
    DirectoryCreateState,
    DirectoryMetadataState,
    DirectoryQuarantineState,
    DirectoryRestoreState,
)


class SqliteDirectoryRecoveryStoreError(ValueError):
    pass


_TERMINAL_STATE_VALUES = tuple(
    state.value
    for kind in DirectoryRecoveryKind
    for state in (
        SUCCESS_PATH_BY_KIND[kind][-1],
        CONFLICT_STATE_BY_KIND[kind],
    )
)


class SqliteDirectoryRecoveryStore(DirectoryRecoveryStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_directory_recovery_operation(
        self,
        operation: DirectoryRecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> DirectoryRecoveryOperation:
        validate_directory_recovery_operation(operation)
        _validate_process_instance_id(process_instance_id)
        if (
            operation.state is not INITIAL_STATE_BY_KIND[operation.kind]
            or operation.event_sequence != 0
            or operation.event_hash is not None
            or operation.row_version != 1
            or operation.managed_object_id is not None
            or operation.last_error_code is not None
        ):
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_INSERT_STATE_INVALID"
            )

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_directory_recovery_operation(operation.recovery_id)
            if existing is not None:
                if _immutable_signature(existing) != _immutable_signature(operation):
                    raise SqliteDirectoryRecoveryStoreError(
                        "DIRECTORY_RECOVERY_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing

            self._connection.execute(
                """
                INSERT INTO directory_recovery_operations (
                    recovery_id,
                    operation_id,
                    run_id,
                    run_target_id,
                    target_endpoint_id,
                    target_endpoint_revision_id,
                    owner_installation_id,
                    ownership_epoch,
                    kind,
                    state,
                    final_relative_path,
                    expected_precondition_json,
                    desired_metadata_json,
                    managed_object_id,
                    last_error_code,
                    event_sequence,
                    event_hash,
                    row_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.recovery_id,
                    operation.operation_id,
                    operation.run_id,
                    operation.run_target_id,
                    operation.target_endpoint_id,
                    operation.target_endpoint_revision_id,
                    operation.owner_installation_id,
                    operation.ownership_epoch,
                    operation.kind.value,
                    operation.state.value,
                    operation.final_relative_path,
                    operation.expected_precondition_json,
                    operation.desired_metadata_json,
                    None,
                    None,
                    0,
                    None,
                    1,
                ),
            )
            payload_json = canonical_directory_recovery_payload(payload or {})
            event_hash = _event_hash(
                recovery_id=operation.recovery_id,
                event_sequence=1,
                from_state=None,
                to_state=operation.state,
                process_instance_id=process_instance_id,
                payload_json=payload_json,
                previous_event_hash=None,
            )
            self._insert_event(
                recovery_id=operation.recovery_id,
                event_sequence=1,
                from_state=None,
                to_state=operation.state,
                process_instance_id=process_instance_id,
                payload_json=payload_json,
                previous_event_hash=None,
                event_hash=event_hash,
            )
            self._connection.execute(
                """
                UPDATE directory_recovery_operations
                SET event_sequence = 1, event_hash = ?
                WHERE recovery_id = ? AND event_sequence = 0
                """,
                (event_hash, operation.recovery_id),
            )
            recorded = self.load_directory_recovery_operation(operation.recovery_id)
            if recorded is None:
                raise SqliteDirectoryRecoveryStoreError(
                    "DIRECTORY_RECOVERY_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return recorded
        except SqliteDirectoryRecoveryStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_PERSISTENCE_FAILED"
            ) from exc

    def transition_directory_recovery_operation(
        self,
        transition: DirectoryRecoveryTransition,
    ) -> DirectoryRecoveryOperation | None:
        _validate_process_instance_id(transition.process_instance_id)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            current = self.load_directory_recovery_operation(transition.recovery_id)
            if current is None:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None
            if current.state is transition.next_state:
                expected = replace(current, state=transition.expected_state)
                validate_directory_recovery_transition(expected, transition)
                _validate_replay_evidence(current, transition)
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return current
            if current.state is not transition.expected_state:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            validate_directory_recovery_transition(current, transition)
            managed_object_id = (
                current.managed_object_id
                if transition.managed_object_id is None
                else _normalized_optional_evidence(transition.managed_object_id)
            )
            last_error_code = (
                current.last_error_code
                if transition.last_error_code is None
                else _normalized_optional_evidence(transition.last_error_code)
            )
            payload_json = canonical_directory_recovery_payload(transition.payload)
            next_sequence = current.event_sequence + 1
            event_hash = _event_hash(
                recovery_id=current.recovery_id,
                event_sequence=next_sequence,
                from_state=current.state,
                to_state=transition.next_state,
                process_instance_id=transition.process_instance_id,
                payload_json=payload_json,
                previous_event_hash=current.event_hash,
            )
            self._insert_event(
                recovery_id=current.recovery_id,
                event_sequence=next_sequence,
                from_state=current.state,
                to_state=transition.next_state,
                process_instance_id=transition.process_instance_id,
                payload_json=payload_json,
                previous_event_hash=current.event_hash,
                event_hash=event_hash,
            )
            cursor = self._connection.execute(
                """
                UPDATE directory_recovery_operations
                SET
                    state = ?,
                    managed_object_id = ?,
                    last_error_code = ?,
                    event_sequence = ?,
                    event_hash = ?,
                    row_version = row_version + 1,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE recovery_id = ?
                    AND state = ?
                    AND event_sequence = ?
                    AND row_version = ?
                """,
                (
                    transition.next_state.value,
                    managed_object_id,
                    last_error_code,
                    next_sequence,
                    event_hash,
                    current.recovery_id,
                    current.state.value,
                    current.event_sequence,
                    current.row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteDirectoryRecoveryStoreError(
                    "DIRECTORY_RECOVERY_CONCURRENT_UPDATE"
                )
            updated = self.load_directory_recovery_operation(current.recovery_id)
            if updated is None:
                raise SqliteDirectoryRecoveryStoreError(
                    "DIRECTORY_RECOVERY_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return updated
        except SqliteDirectoryRecoveryStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_TRANSITION_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_TRANSITION_FAILED"
            ) from exc

    def load_directory_recovery_operation(
        self,
        recovery_id: str,
    ) -> DirectoryRecoveryOperation | None:
        row = self._connection.execute(
            """
            SELECT
                recovery_id,
                operation_id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                owner_installation_id,
                ownership_epoch,
                kind,
                state,
                final_relative_path,
                expected_precondition_json,
                desired_metadata_json,
                managed_object_id,
                last_error_code,
                event_sequence,
                event_hash,
                row_version
            FROM directory_recovery_operations
            WHERE recovery_id = ?
            """,
            (recovery_id,),
        ).fetchone()
        return None if row is None else _operation_from_row(row)

    def list_unresolved_directory_recovery_operations(
        self,
        *,
        limit: int,
    ) -> tuple[DirectoryRecoveryOperation, ...]:
        if limit < 1 or limit > 1000:
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_LIST_LIMIT_INVALID"
            )
        placeholders = ", ".join("?" for _ in _TERMINAL_STATE_VALUES)
        rows = self._connection.execute(
            f"""
            SELECT
                recovery_id,
                operation_id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                owner_installation_id,
                ownership_epoch,
                kind,
                state,
                final_relative_path,
                expected_precondition_json,
                desired_metadata_json,
                managed_object_id,
                last_error_code,
                event_sequence,
                event_hash,
                row_version
            FROM directory_recovery_operations
            WHERE state NOT IN ({placeholders})
            ORDER BY updated_utc, recovery_id
            LIMIT ?
            """,
            (*_TERMINAL_STATE_VALUES, limit),
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def list_conflicted_directory_recovery_operations(
        self,
        *,
        limit: int,
    ) -> tuple[DirectoryRecoveryOperation, ...]:
        if limit < 1 or limit > 1000:
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_LIST_LIMIT_INVALID"
            )
        conflict_states = tuple(
            state.value for state in CONFLICT_STATE_BY_KIND.values()
        )
        placeholders = ", ".join("?" for _ in conflict_states)
        rows = self._connection.execute(
            f"""
            SELECT
                recovery_id,
                operation_id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                owner_installation_id,
                ownership_epoch,
                kind,
                state,
                final_relative_path,
                expected_precondition_json,
                desired_metadata_json,
                managed_object_id,
                last_error_code,
                event_sequence,
                event_hash,
                row_version
            FROM directory_recovery_operations
            WHERE state IN ({placeholders})
            ORDER BY updated_utc, recovery_id
            LIMIT ?
            """,
            (*conflict_states, limit),
        ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def _insert_event(
        self,
        *,
        recovery_id: str,
        event_sequence: int,
        from_state: DirectoryRecoveryState | None,
        to_state: DirectoryRecoveryState,
        process_instance_id: str,
        payload_json: str,
        previous_event_hash: str | None,
        event_hash: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO directory_recovery_events (
                recovery_id,
                event_sequence,
                from_state,
                to_state,
                process_instance_id,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recovery_id,
                event_sequence,
                None if from_state is None else from_state.value,
                to_state.value,
                process_instance_id,
                payload_json,
                previous_event_hash,
                event_hash,
            ),
        )


def _operation_from_row(
    row: sqlite3.Row | tuple[Any, ...],
) -> DirectoryRecoveryOperation:
    kind = DirectoryRecoveryKind(str(row[8]))
    operation = DirectoryRecoveryOperation(
        recovery_id=str(row[0]),
        operation_id=str(row[1]),
        run_id=str(row[2]),
        run_target_id=str(row[3]),
        target_endpoint_id=str(row[4]),
        target_endpoint_revision_id=str(row[5]),
        owner_installation_id=str(row[6]),
        ownership_epoch=int(row[7]),
        kind=kind,
        state=_state_from_value(kind, str(row[9])),
        final_relative_path=str(row[10]),
        expected_precondition_json=None if row[11] is None else str(row[11]),
        desired_metadata_json=None if row[12] is None else str(row[12]),
        managed_object_id=None if row[13] is None else str(row[13]),
        last_error_code=None if row[14] is None else str(row[14]),
        event_sequence=int(row[15]),
        event_hash=None if row[16] is None else str(row[16]),
        row_version=int(row[17]),
    )
    validate_directory_recovery_operation(operation)
    return operation


def _state_from_value(
    kind: DirectoryRecoveryKind,
    value: str,
) -> DirectoryRecoveryState:
    enum_type = {
        DirectoryRecoveryKind.CREATE: DirectoryCreateState,
        DirectoryRecoveryKind.METADATA: DirectoryMetadataState,
        DirectoryRecoveryKind.QUARANTINE: DirectoryQuarantineState,
        DirectoryRecoveryKind.RESTORE: DirectoryRestoreState,
    }[kind]
    return enum_type(value)


def _immutable_signature(operation: DirectoryRecoveryOperation) -> tuple[object, ...]:
    return (
        operation.recovery_id,
        operation.operation_id,
        operation.run_id,
        operation.run_target_id,
        operation.target_endpoint_id,
        operation.target_endpoint_revision_id,
        operation.owner_installation_id,
        operation.ownership_epoch,
        operation.kind,
        operation.final_relative_path,
        operation.expected_precondition_json,
        operation.desired_metadata_json,
    )


def _validate_replay_evidence(
    operation: DirectoryRecoveryOperation,
    transition: DirectoryRecoveryTransition,
) -> None:
    for persisted, requested in (
        (operation.managed_object_id, transition.managed_object_id),
        (operation.last_error_code, transition.last_error_code),
    ):
        if requested is not None and persisted != requested.strip():
            raise SqliteDirectoryRecoveryStoreError(
                "DIRECTORY_RECOVERY_REPLAY_EVIDENCE_CONFLICT"
            )


def _normalized_optional_evidence(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized != value:
        raise SqliteDirectoryRecoveryStoreError(
            "DIRECTORY_RECOVERY_EVIDENCE_INVALID"
        )
    return normalized


def _validate_process_instance_id(value: str) -> None:
    if not value.strip() or value != value.strip():
        raise SqliteDirectoryRecoveryStoreError(
            "DIRECTORY_RECOVERY_PROCESS_INSTANCE_INVALID"
        )


def _event_hash(
    *,
    recovery_id: str,
    event_sequence: int,
    from_state: DirectoryRecoveryState | None,
    to_state: DirectoryRecoveryState,
    process_instance_id: str,
    payload_json: str,
    previous_event_hash: str | None,
) -> str:
    material = json.dumps(
        {
            "event_sequence": event_sequence,
            "from_state": None if from_state is None else from_state.value,
            "payload_json": payload_json,
            "previous_event_hash": previous_event_hash,
            "process_instance_id": process_instance_id,
            "recovery_id": recovery_id,
            "schema_version": 1,
            "to_state": to_state.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
