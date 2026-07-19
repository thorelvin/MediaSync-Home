from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any, Mapping

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
    RecoveryTargetPreconditionKind,
    validate_recovery_operation,
    validate_recovery_phase_transition,
)


class SqliteRecoveryOperationStoreError(ValueError):
    pass


class SqliteRecoveryOperationStore(RecoveryOperationStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        if operation.phase is not RecoveryOperationPhase.PLANNED:
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_REQUIRES_PLANNED_PHASE")
        _validate_process_instance_id(process_instance_id)
        validate_recovery_operation(operation)

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_operation(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
            )
            if existing is not None:
                if existing != operation:
                    raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_IDEMPOTENCY_CONFLICT")
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing

            self._require_active_matching_lease(operation)
            self._insert_operation(operation)
            self._append_event(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
                from_phase=None,
                to_phase=operation.phase,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            planned = self.load_operation(run_id=operation.run_id, operation_id=operation.operation_id)
            if planned is None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return planned
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_PERSISTENCE_CONFLICT") from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_PERSISTENCE_FAILED") from exc

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
    ) -> RecoveryOperation | None:
        _validate_process_instance_id(process_instance_id)
        validate_recovery_phase_transition(expected_phase, next_phase)

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            operation = self.load_operation(run_id=run_id, operation_id=operation_id)
            if operation is None or operation.phase is not expected_phase:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            updated = self._operation_after_transition(
                operation,
                next_phase=next_phase,
                intent_segment_id=intent_segment_id,
                intent_ordinal=intent_ordinal,
                catalog_handoff_id=catalog_handoff_id,
            )
            self._require_active_matching_lease(updated)
            if next_phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED:
                self._require_matching_intent_segment(updated)

            cursor = self._connection.execute(
                """
                UPDATE recovery_operations
                SET
                    phase = ?,
                    intent_segment_id = ?,
                    intent_ordinal = ?,
                    catalog_handoff_id = ?,
                    updated_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE run_id = ?
                    AND operation_id = ?
                    AND phase = ?
                """,
                (
                    updated.phase.value,
                    updated.intent_segment_id,
                    updated.intent_ordinal,
                    updated.catalog_handoff_id,
                    run_id,
                    operation_id,
                    expected_phase.value,
                ),
            )
            if cursor.rowcount != 1:
                if not outer_transaction:
                    self._connection.execute("ROLLBACK")
                return None

            self._append_event(
                run_id=run_id,
                operation_id=operation_id,
                from_phase=expected_phase,
                to_phase=next_phase,
                process_instance_id=process_instance_id,
                payload=payload,
            )
            loaded = self.load_operation(run_id=run_id, operation_id=operation_id)
            if loaded is None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteRecoveryOperationStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_PERSISTENCE_CONFLICT") from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_PERSISTENCE_FAILED") from exc

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        row = self._connection.execute(
            f"""
            SELECT
                {RECOVERY_OPERATION_COLUMNS}
            FROM recovery_operations
            WHERE run_id = ?
                AND operation_id = ?
            """,
            (run_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        return _operation_from_row(row)

    def _operation_after_transition(
        self,
        operation: RecoveryOperation,
        *,
        next_phase: RecoveryOperationPhase,
        intent_segment_id: str | None,
        intent_ordinal: int | None,
        catalog_handoff_id: str | None,
    ) -> RecoveryOperation:
        if next_phase is RecoveryOperationPhase.COMMIT_INTENT_RECORDED:
            if intent_segment_id is None or intent_ordinal is None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_REQUIRES_INTENT_SEGMENT")
            if catalog_handoff_id is not None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_UNEXPECTED_CATALOG_HANDOFF")
            updated = replace(
                operation,
                phase=next_phase,
                intent_segment_id=intent_segment_id,
                intent_ordinal=intent_ordinal,
            )
        elif next_phase is RecoveryOperationPhase.CATALOG_RECORDED:
            if intent_segment_id is not None or intent_ordinal is not None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_UNEXPECTED_INTENT_SEGMENT")
            if catalog_handoff_id is None or not catalog_handoff_id.strip():
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_REQUIRES_CATALOG_HANDOFF")
            updated = replace(
                operation,
                phase=next_phase,
                catalog_handoff_id=catalog_handoff_id,
            )
        else:
            if intent_segment_id is not None or intent_ordinal is not None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_UNEXPECTED_INTENT_SEGMENT")
            if catalog_handoff_id is not None:
                raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_UNEXPECTED_CATALOG_HANDOFF")
            updated = replace(operation, phase=next_phase)
        validate_recovery_operation(updated)
        return updated

    def _require_active_matching_lease(self, operation: RecoveryOperation) -> None:
        row = self._connection.execute(
            """
            SELECT
                resource_key,
                owner_instance_id,
                ownership_epoch,
                fencing_token,
                endpoint_id,
                state
            FROM resource_leases
            WHERE lease_id = ?
            """,
            (operation.lease_id,),
        ).fetchone()
        if row is None:
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_LEASE_MISMATCH")
        if (
            str(row[0]) != operation.lease_resource_key
            or str(row[1]) != operation.owner_installation_id
            or int(row[2]) != operation.ownership_epoch
            or int(row[3]) != operation.fencing_token
            or str(row[4]) != operation.target_endpoint_id
            or str(row[5]) != "ACQUIRED"
        ):
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_LEASE_MISMATCH")

    def _require_matching_intent_segment(self, operation: RecoveryOperation) -> None:
        row = self._connection.execute(
            """
            SELECT
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                lease_id,
                fencing_token,
                durability_state,
                state
            FROM recovery_intent_segments
            WHERE id = ?
            """,
            (operation.intent_segment_id,),
        ).fetchone()
        if row is None:
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_INTENT_SEGMENT_MISMATCH")
        if (
            str(row[0]) != operation.run_id
            or str(row[1]) != operation.run_target_id
            or str(row[2]) != operation.target_endpoint_id
            or str(row[3]) != operation.target_endpoint_revision_id
            or int(row[4]) != operation.endpoint_generation
            or str(row[5]) != operation.owner_installation_id
            or int(row[6]) != operation.ownership_epoch
            or str(row[7]) != operation.lease_id
            or int(row[8]) != operation.fencing_token
            or str(row[9]) != "DURABLE"
            or str(row[10]) != "DURABLE"
        ):
            raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_INTENT_SEGMENT_MISMATCH")

    def _insert_operation(self, operation: RecoveryOperation) -> None:
        self._connection.execute(
            f"""
            INSERT INTO recovery_operations (
                {RECOVERY_OPERATION_COLUMNS}
            )
            VALUES ({RECOVERY_OPERATION_PLACEHOLDERS})
            """,
            _operation_values(operation),
        )

    def _append_event(
        self,
        *,
        run_id: str,
        operation_id: str,
        from_phase: RecoveryOperationPhase | None,
        to_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT run_sequence, event_hash
            FROM recovery_events
            WHERE run_id = ?
            ORDER BY run_sequence DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            run_sequence = 0
            previous_event_hash = None
        else:
            run_sequence = int(row[0]) + 1
            previous_event_hash = str(row[1])
        payload_json = _canonical_json({} if payload is None else payload)
        event_hash = _event_hash(
            run_id=run_id,
            run_sequence=run_sequence,
            operation_id=operation_id,
            from_phase=from_phase,
            to_phase=to_phase,
            process_instance_id=process_instance_id,
            payload_json=payload_json,
            previous_event_hash=previous_event_hash,
        )
        self._connection.execute(
            """
            INSERT INTO recovery_events (
                run_id,
                run_sequence,
                operation_id,
                from_phase,
                to_phase,
                process_instance_id,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_sequence,
                operation_id,
                None if from_phase is None else from_phase.value,
                to_phase.value,
                process_instance_id,
                payload_json,
                previous_event_hash,
                event_hash,
            ),
        )


RECOVERY_OPERATION_COLUMN_NAMES = (
    "run_id",
    "run_target_id",
    "operation_id",
    "source_endpoint_id",
    "source_endpoint_revision_id",
    "target_endpoint_id",
    "target_endpoint_revision_id",
    "endpoint_generation",
    "owner_installation_id",
    "ownership_epoch",
    "lease_id",
    "lease_resource_key",
    "fencing_token",
    "phase",
    "source_relative_path",
    "source_guard_kind",
    "source_guard_evidence_hash",
    "source_hash_evidence_kind",
    "source_path_chain_hash",
    "source_case_context_hash",
    "staging_object_id",
    "final_relative_path",
    "version_object_id",
    "quarantine_object_id",
    "intent_segment_id",
    "intent_ordinal",
    "target_precondition_kind",
    "expected_source_fingerprint_json",
    "expected_target_fingerprint_json",
    "expected_source_parent_identity_json",
    "expected_target_parent_identity_json",
    "expected_target_path_chain_hash",
    "expected_staging_fingerprint_json",
    "expected_final_fingerprint_json",
    "observed_target_file_id",
    "transfer_state",
    "assurance_level",
    "staging_durability_state",
    "final_durability_state",
    "catalog_handoff_id",
    "last_error_code",
)
RECOVERY_OPERATION_COLUMNS = ", ".join(RECOVERY_OPERATION_COLUMN_NAMES)
RECOVERY_OPERATION_PLACEHOLDERS = ", ".join("?" for _ in RECOVERY_OPERATION_COLUMN_NAMES)


def _operation_values(operation: RecoveryOperation) -> tuple[object, ...]:
    return (
        operation.run_id,
        operation.run_target_id,
        operation.operation_id,
        operation.source_endpoint_id,
        operation.source_endpoint_revision_id,
        operation.target_endpoint_id,
        operation.target_endpoint_revision_id,
        operation.endpoint_generation,
        operation.owner_installation_id,
        operation.ownership_epoch,
        operation.lease_id,
        operation.lease_resource_key,
        operation.fencing_token,
        operation.phase.value,
        operation.source_relative_path,
        operation.source_guard_kind,
        operation.source_guard_evidence_hash,
        operation.source_hash_evidence_kind,
        operation.source_path_chain_hash,
        operation.source_case_context_hash,
        operation.staging_object_id,
        operation.final_relative_path,
        operation.version_object_id,
        operation.quarantine_object_id,
        operation.intent_segment_id,
        operation.intent_ordinal,
        operation.target_precondition_kind.value,
        operation.expected_source_fingerprint_json,
        operation.expected_target_fingerprint_json,
        operation.expected_source_parent_identity_json,
        operation.expected_target_parent_identity_json,
        operation.expected_target_path_chain_hash,
        operation.expected_staging_fingerprint_json,
        operation.expected_final_fingerprint_json,
        operation.observed_target_file_id,
        operation.transfer_state,
        operation.assurance_level,
        operation.staging_durability_state,
        operation.final_durability_state,
        operation.catalog_handoff_id,
        operation.last_error_code,
    )


def _operation_from_row(row: sqlite3.Row | tuple[Any, ...]) -> RecoveryOperation:
    return RecoveryOperation(
        run_id=str(row[0]),
        run_target_id=str(row[1]),
        operation_id=str(row[2]),
        source_endpoint_id=None if row[3] is None else str(row[3]),
        source_endpoint_revision_id=None if row[4] is None else str(row[4]),
        target_endpoint_id=str(row[5]),
        target_endpoint_revision_id=str(row[6]),
        endpoint_generation=int(row[7]),
        owner_installation_id=str(row[8]),
        ownership_epoch=int(row[9]),
        lease_id=str(row[10]),
        lease_resource_key=str(row[11]),
        fencing_token=int(row[12]),
        phase=RecoveryOperationPhase(str(row[13])),
        source_relative_path=None if row[14] is None else str(row[14]),
        source_guard_kind=None if row[15] is None else str(row[15]),
        source_guard_evidence_hash=None if row[16] is None else str(row[16]),
        source_hash_evidence_kind=None if row[17] is None else str(row[17]),
        source_path_chain_hash=None if row[18] is None else str(row[18]),
        source_case_context_hash=None if row[19] is None else str(row[19]),
        staging_object_id=None if row[20] is None else str(row[20]),
        final_relative_path=str(row[21]),
        version_object_id=None if row[22] is None else str(row[22]),
        quarantine_object_id=None if row[23] is None else str(row[23]),
        intent_segment_id=None if row[24] is None else str(row[24]),
        intent_ordinal=None if row[25] is None else int(row[25]),
        target_precondition_kind=RecoveryTargetPreconditionKind(str(row[26])),
        expected_source_fingerprint_json=None if row[27] is None else str(row[27]),
        expected_target_fingerprint_json=None if row[28] is None else str(row[28]),
        expected_source_parent_identity_json=None if row[29] is None else str(row[29]),
        expected_target_parent_identity_json=None if row[30] is None else str(row[30]),
        expected_target_path_chain_hash=None if row[31] is None else str(row[31]),
        expected_staging_fingerprint_json=None if row[32] is None else str(row[32]),
        expected_final_fingerprint_json=None if row[33] is None else str(row[33]),
        observed_target_file_id=None if row[34] is None else str(row[34]),
        transfer_state=None if row[35] is None else str(row[35]),
        assurance_level=None if row[36] is None else str(row[36]),
        staging_durability_state=None if row[37] is None else str(row[37]),
        final_durability_state=None if row[38] is None else str(row[38]),
        catalog_handoff_id=None if row[39] is None else str(row[39]),
        last_error_code=None if row[40] is None else str(row[40]),
    )


def _validate_process_instance_id(process_instance_id: str) -> None:
    if not process_instance_id.strip():
        raise SqliteRecoveryOperationStoreError("RECOVERY_OPERATION_REQUIRES_PROCESS_INSTANCE")


def _event_hash(
    *,
    run_id: str,
    run_sequence: int,
    operation_id: str,
    from_phase: RecoveryOperationPhase | None,
    to_phase: RecoveryOperationPhase,
    process_instance_id: str,
    payload_json: str,
    previous_event_hash: str | None,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schema_version": 1,
                "run_id": run_id,
                "run_sequence": run_sequence,
                "operation_id": operation_id,
                "from_phase": None if from_phase is None else from_phase.value,
                "to_phase": to_phase.value,
                "process_instance_id": process_instance_id,
                "payload_json": payload_json,
                "previous_event_hash": previous_event_hash,
            }
        ).encode("utf-8")
    ).hexdigest()


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
