from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

from mediasync_home.domain.capabilities import MutationPermit
from mediasync_home.application.version_retention import (
    RetainedVersionRecord,
    RetainedVersionState,
    VersionRetentionDeleteReceipt,
    VersionRetentionItemState,
    VersionRetentionPlan,
    VersionRetentionWorkItem,
)


class SqliteVersionRetentionStoreError(ValueError):
    pass


class SqliteVersionRetentionStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_due_retained_versions(
        self,
        *,
        cutoff_utc: str,
        limit: int,
    ) -> tuple[RetainedVersionRecord, ...]:
        if limit < 1 or limit > 1000:
            raise SqliteVersionRetentionStoreError("VERSION_RETENTION_QUERY_LIMIT_INVALID")
        rows = self._connection.execute(
            """
            SELECT
                versions.version_object_id,
                versions.handoff_id,
                versions.run_id,
                versions.run_target_id,
                versions.operation_id,
                versions.job_id,
                versions.job_revision_id,
                versions.target_endpoint_id,
                versions.target_endpoint_revision_id,
                versions.endpoint_generation,
                versions.owner_installation_id,
                versions.ownership_epoch,
                versions.final_relative_path,
                versions.original_fingerprint_json,
                versions.created_utc,
                versions.retention_policy,
                versions.retention_until_utc,
                versions.manifest_hash,
                versions.state,
                versions.row_version
            FROM retained_version_objects AS versions
            INNER JOIN jobs
                ON jobs.id = versions.job_id
            WHERE versions.state = 'RETAINED'
                AND versions.retention_until_utc <= ?
                AND jobs.lifecycle_state = 'ACTIVE'
                AND NOT EXISTS (
                    SELECT 1
                    FROM version_retention_holds AS holds
                    WHERE holds.version_object_id = versions.version_object_id
                        AND holds.released_utc IS NULL
                )
            ORDER BY versions.retention_until_utc, versions.version_object_id
            LIMIT ?
            """,
            (cutoff_utc, limit),
        ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def create_version_retention_plan(
        self,
        plan: VersionRetentionPlan,
    ) -> VersionRetentionPlan:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                """
                SELECT cutoff_utc, created_utc, candidate_count, manifest_json,
                       manifest_hash, state
                FROM version_retention_plans
                WHERE plan_id = ?
                """,
                (plan.plan_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != plan.cutoff_utc
                    or str(existing[1]) != plan.created_utc
                    or int(existing[2]) != len(plan.candidates)
                    or str(existing[3]) != plan.manifest_json
                    or str(existing[4]) != plan.manifest_hash
                    or str(existing[5]) != plan.state.value
                    or not self._items_match_plan(plan)
                ):
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RETENTION_PLAN_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return plan

            self._connection.execute(
                """
                INSERT INTO version_retention_plans (
                    plan_id,
                    cutoff_utc,
                    created_utc,
                    candidate_count,
                    manifest_json,
                    manifest_hash,
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.cutoff_utc,
                    plan.created_utc,
                    len(plan.candidates),
                    plan.manifest_json,
                    plan.manifest_hash,
                    plan.state.value,
                ),
            )
            for ordinal, record in enumerate(plan.candidates):
                updated = self._connection.execute(
                    """
                    UPDATE retained_version_objects
                    SET
                        state = 'DELETE_PENDING',
                        deletion_plan_id = ?,
                        row_version = row_version + 1
                    WHERE version_object_id = ?
                        AND state = 'RETAINED'
                        AND row_version = ?
                        AND manifest_hash = ?
                        AND retention_until_utc <= ?
                        AND EXISTS (
                            SELECT 1
                            FROM jobs
                            WHERE jobs.id = retained_version_objects.job_id
                                AND jobs.lifecycle_state = 'ACTIVE'
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM version_retention_holds AS holds
                            WHERE holds.version_object_id = retained_version_objects.version_object_id
                                AND holds.released_utc IS NULL
                        )
                    """,
                    (
                        plan.plan_id,
                        record.version_object_id,
                        record.row_version,
                        record.manifest_hash,
                        plan.cutoff_utc,
                    ),
                )
                if updated.rowcount != 1:
                    raise SqliteVersionRetentionStoreError(
                        "VERSION_RETENTION_PLAN_CANDIDATE_CHANGED"
                    )
                self._connection.execute(
                    """
                    INSERT INTO version_retention_items (
                        plan_id,
                        ordinal,
                        version_object_id,
                        expected_object_row_version,
                        expected_manifest_hash,
                        target_endpoint_id,
                        target_endpoint_revision_id,
                        endpoint_generation,
                        owner_installation_id,
                        ownership_epoch,
                        final_relative_path,
                        state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED')
                    """,
                    (
                        plan.plan_id,
                        ordinal,
                        record.version_object_id,
                        record.row_version + 1,
                        record.manifest_hash,
                        record.target_endpoint_id,
                        record.target_endpoint_revision_id,
                        record.endpoint_generation,
                        record.owner_installation_id,
                        record.ownership_epoch,
                        record.final_relative_path,
                    ),
                )
            self._append_planned_event(plan)
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return plan
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_PLAN_PERSISTENCE_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_PLAN_PERSISTENCE_FAILED"
            ) from exc

    def load_next_version_retention_item(self) -> VersionRetentionWorkItem | None:
        row = self._connection.execute(
            """
            SELECT
                plans.plan_id,
                plans.manifest_hash,
                items.ordinal,
                items.state,
                items.expected_object_row_version,
                versions.version_object_id,
                versions.handoff_id,
                versions.run_id,
                versions.run_target_id,
                versions.operation_id,
                versions.job_id,
                versions.job_revision_id,
                versions.target_endpoint_id,
                versions.target_endpoint_revision_id,
                versions.endpoint_generation,
                versions.owner_installation_id,
                versions.ownership_epoch,
                versions.final_relative_path,
                versions.original_fingerprint_json,
                versions.created_utc,
                versions.retention_policy,
                versions.retention_until_utc,
                versions.manifest_hash,
                versions.state,
                versions.row_version
            FROM version_retention_plans AS plans
            INNER JOIN version_retention_items AS items
                ON items.plan_id = plans.plan_id
            INNER JOIN retained_version_objects AS versions
                ON versions.version_object_id = items.version_object_id
            WHERE plans.state IN ('PLANNED', 'APPLYING')
                AND items.state IN (
                    'PLANNED',
                    'DELETE_INTENT_RECORDED',
                    'FILESYSTEM_DELETED'
                )
            ORDER BY plans.created_utc, plans.plan_id, items.ordinal
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return _work_item_from_row(row)

    def record_version_delete_intent(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRetentionWorkItem:
        _require_permit_binding(item=item, permit=permit)
        if item.state not in {
            VersionRetentionItemState.PLANNED,
            VersionRetentionItemState.DELETE_INTENT_RECORDED,
        }:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_DELETE_INTENT_STATE_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE version_retention_items
                SET
                    state = 'DELETE_INTENT_RECORDED',
                    delete_lease_id = ?,
                    delete_fencing_token = ?,
                    delete_intent_utc = ?,
                    row_version = row_version + 1
                WHERE plan_id = ?
                    AND ordinal = ?
                    AND version_object_id = ?
                    AND state = ?
                    AND expected_object_row_version = ?
                    AND expected_manifest_hash = ?
                    AND EXISTS (
                        SELECT 1
                        FROM version_retention_plans AS plans
                        WHERE plans.plan_id = version_retention_items.plan_id
                            AND plans.manifest_hash = ?
                            AND plans.state IN ('PLANNED', 'APPLYING')
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM retained_version_objects AS versions
                        INNER JOIN jobs ON jobs.id = versions.job_id
                        WHERE versions.version_object_id = version_retention_items.version_object_id
                            AND versions.state = 'DELETE_PENDING'
                            AND versions.deletion_plan_id = version_retention_items.plan_id
                            AND versions.row_version = version_retention_items.expected_object_row_version
                            AND versions.manifest_hash = version_retention_items.expected_manifest_hash
                            AND jobs.lifecycle_state = 'ACTIVE'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM version_retention_holds AS holds
                                WHERE holds.version_object_id = versions.version_object_id
                                    AND holds.released_utc IS NULL
                            )
                    )
                """,
                (
                    permit.lease_id,
                    permit.fencing_token,
                    event_utc,
                    item.plan_id,
                    item.ordinal,
                    item.record.version_object_id,
                    item.state.value,
                    item.expected_object_row_version,
                    item.record.manifest_hash,
                    item.plan_manifest_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_DELETE_INTENT_REVALIDATION_FAILED"
                )
            self._connection.execute(
                """
                UPDATE version_retention_plans
                SET state = 'APPLYING', row_version = row_version + 1
                WHERE plan_id = ? AND state IN ('PLANNED', 'APPLYING')
                """,
                (item.plan_id,),
            )
            event_kind = (
                "DELETE_INTENT_RECORDED"
                if item.state is VersionRetentionItemState.PLANNED
                else "DELETE_INTENT_REFRESHED"
            )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind=event_kind,
                event_utc=event_utc,
                payload={
                    "fencing_token": permit.fencing_token,
                    "lease_id": permit.lease_id,
                    "manifest_hash": item.record.manifest_hash,
                },
            )
            loaded = self._load_work_item(plan_id=item.plan_id, ordinal=item.ordinal)
            if loaded is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_ITEM_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_DELETE_INTENT_PERSISTENCE_FAILED"
            ) from exc

    def record_version_filesystem_deleted(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
        receipt: VersionRetentionDeleteReceipt,
        event_utc: str,
    ) -> VersionRetentionWorkItem:
        _require_permit_binding(item=item, permit=permit)
        if item.state is not VersionRetentionItemState.DELETE_INTENT_RECORDED:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_FILESYSTEM_DELETED_STATE_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE version_retention_items
                SET
                    state = 'FILESYSTEM_DELETED',
                    filesystem_deleted_utc = ?,
                    row_version = row_version + 1
                WHERE plan_id = ?
                    AND ordinal = ?
                    AND state = 'DELETE_INTENT_RECORDED'
                    AND delete_lease_id = ?
                    AND delete_fencing_token = ?
                    AND expected_manifest_hash = ?
                """,
                (
                    event_utc,
                    item.plan_id,
                    item.ordinal,
                    permit.lease_id,
                    permit.fencing_token,
                    receipt.manifest_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_FILESYSTEM_DELETED_CONFLICT"
                )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind="FILESYSTEM_DELETED",
                event_utc=event_utc,
                payload={"manifest_hash": receipt.manifest_hash},
            )
            loaded = self._load_work_item(plan_id=item.plan_id, ordinal=item.ordinal)
            if loaded is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_ITEM_LOAD_FAILED"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return loaded
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_FILESYSTEM_DELETED_PERSISTENCE_FAILED"
            ) from exc

    def complete_version_retention_item(
        self,
        *,
        item: VersionRetentionWorkItem,
        event_utc: str,
    ) -> VersionRetentionWorkItem:
        if item.state is not VersionRetentionItemState.FILESYSTEM_DELETED:
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_COMPLETE_STATE_INVALID"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            object_cursor = self._connection.execute(
                """
                UPDATE retained_version_objects
                SET
                    state = 'DELETED',
                    deleted_utc = ?,
                    row_version = row_version + 1
                WHERE version_object_id = ?
                    AND state = 'DELETE_PENDING'
                    AND deletion_plan_id = ?
                    AND row_version = ?
                    AND manifest_hash = ?
                """,
                (
                    event_utc,
                    item.record.version_object_id,
                    item.plan_id,
                    item.expected_object_row_version,
                    item.record.manifest_hash,
                ),
            )
            if object_cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_COMPLETE_OBJECT_CONFLICT"
                )
            item_cursor = self._connection.execute(
                """
                UPDATE version_retention_items
                SET state = 'DELETED', deleted_utc = ?, row_version = row_version + 1
                WHERE plan_id = ? AND ordinal = ? AND state = 'FILESYSTEM_DELETED'
                """,
                (event_utc, item.plan_id, item.ordinal),
            )
            if item_cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_COMPLETE_ITEM_CONFLICT"
                )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind="ITEM_DELETED",
                event_utc=event_utc,
                payload={"manifest_hash": item.record.manifest_hash},
            )
            remaining = self._connection.execute(
                """
                SELECT count(*)
                FROM version_retention_items
                WHERE plan_id = ? AND state NOT IN ('DELETED', 'BLOCKED')
                """,
                (item.plan_id,),
            ).fetchone()
            if remaining is None:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_COMPLETE_COUNT_FAILED"
                )
            plan_state = "COMPLETED" if _int_column(remaining[0]) == 0 else "APPLYING"
            self._connection.execute(
                """
                UPDATE version_retention_plans
                SET
                    state = ?,
                    completed_utc = CASE WHEN ? = 'COMPLETED' THEN ? ELSE NULL END,
                    row_version = row_version + 1
                WHERE plan_id = ? AND state IN ('PLANNED', 'APPLYING')
                """,
                (plan_state, plan_state, event_utc, item.plan_id),
            )
            completed = VersionRetentionWorkItem(
                plan_id=item.plan_id,
                plan_manifest_hash=item.plan_manifest_hash,
                ordinal=item.ordinal,
                state=VersionRetentionItemState.DELETED,
                expected_object_row_version=item.expected_object_row_version,
                record=replace(
                    item.record,
                    state=RetainedVersionState.DELETED,
                    row_version=item.expected_object_row_version + 1,
                ),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return completed
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_COMPLETE_PERSISTENCE_FAILED"
            ) from exc

    def block_version_retention_plan(
        self,
        *,
        item: VersionRetentionWorkItem,
        validation_code: str,
        event_utc: str,
    ) -> None:
        if not validation_code.strip():
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_BLOCK_CODE_MISSING"
            )
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            requeue_blocked_object = _is_retryable_plan_block(validation_code)
            self._connection.execute(
                """
                UPDATE retained_version_objects
                SET
                    state = CASE
                        WHEN version_object_id = ? AND ? = 0 THEN 'BLOCKED'
                        ELSE 'RETAINED'
                    END,
                    deletion_plan_id = NULL,
                    last_validation_code = ?,
                    row_version = row_version + 1
                WHERE deletion_plan_id = ? AND state = 'DELETE_PENDING'
                """,
                (
                    item.record.version_object_id,
                    int(requeue_blocked_object),
                    validation_code,
                    item.plan_id,
                ),
            )
            self._connection.execute(
                """
                UPDATE version_retention_items
                SET
                    state = 'BLOCKED',
                    last_validation_code = ?,
                    row_version = row_version + 1
                WHERE plan_id = ?
                    AND state IN ('PLANNED', 'DELETE_INTENT_RECORDED')
                """,
                (validation_code, item.plan_id),
            )
            cursor = self._connection.execute(
                """
                UPDATE version_retention_plans
                SET state = 'BLOCKED', completed_utc = ?, row_version = row_version + 1
                WHERE plan_id = ? AND state IN ('PLANNED', 'APPLYING')
                """,
                (event_utc, item.plan_id),
            )
            if cursor.rowcount != 1:
                raise SqliteVersionRetentionStoreError(
                    "VERSION_RETENTION_BLOCK_PLAN_CONFLICT"
                )
            self._append_event(
                plan_id=item.plan_id,
                version_object_id=item.record.version_object_id,
                event_kind="PLAN_BLOCKED",
                event_utc=event_utc,
                payload={"validation_code": validation_code},
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
        except SqliteVersionRetentionStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteVersionRetentionStoreError(
                "VERSION_RETENTION_BLOCK_PERSISTENCE_FAILED"
            ) from exc

    def _load_work_item(
        self,
        *,
        plan_id: str,
        ordinal: int,
    ) -> VersionRetentionWorkItem | None:
        row = self._connection.execute(
            """
            SELECT
                plans.plan_id,
                plans.manifest_hash,
                items.ordinal,
                items.state,
                items.expected_object_row_version,
                versions.version_object_id,
                versions.handoff_id,
                versions.run_id,
                versions.run_target_id,
                versions.operation_id,
                versions.job_id,
                versions.job_revision_id,
                versions.target_endpoint_id,
                versions.target_endpoint_revision_id,
                versions.endpoint_generation,
                versions.owner_installation_id,
                versions.ownership_epoch,
                versions.final_relative_path,
                versions.original_fingerprint_json,
                versions.created_utc,
                versions.retention_policy,
                versions.retention_until_utc,
                versions.manifest_hash,
                versions.state,
                versions.row_version
            FROM version_retention_plans AS plans
            INNER JOIN version_retention_items AS items
                ON items.plan_id = plans.plan_id
            INNER JOIN retained_version_objects AS versions
                ON versions.version_object_id = items.version_object_id
            WHERE plans.plan_id = ? AND items.ordinal = ?
            """,
            (plan_id, ordinal),
        ).fetchone()
        if row is None:
            return None
        return _work_item_from_row(row)

    def _append_event(
        self,
        *,
        plan_id: str,
        version_object_id: str | None,
        event_kind: str,
        event_utc: str,
        payload: dict[str, object],
    ) -> None:
        previous = self._connection.execute(
            """
            SELECT plan_sequence, event_hash
            FROM version_retention_events
            WHERE plan_id = ?
            ORDER BY plan_sequence DESC
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        sequence = 0 if previous is None else _int_column(previous[0]) + 1
        previous_hash = None if previous is None else str(previous[1])
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event_hash = hashlib.sha256(
            json.dumps(
                {
                    "event_kind": event_kind,
                    "event_utc": event_utc,
                    "payload_json": payload_json,
                    "plan_id": plan_id,
                    "plan_sequence": sequence,
                    "previous_event_hash": previous_hash,
                    "version_object_id": version_object_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            """
            INSERT INTO version_retention_events (
                plan_id,
                plan_sequence,
                version_object_id,
                event_kind,
                event_utc,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                sequence,
                version_object_id,
                event_kind,
                event_utc,
                payload_json,
                previous_hash,
                event_hash,
            ),
        )

    def _items_match_plan(self, plan: VersionRetentionPlan) -> bool:
        rows = self._connection.execute(
            """
            SELECT ordinal, version_object_id, expected_object_row_version,
                   expected_manifest_hash, target_endpoint_id,
                   target_endpoint_revision_id, endpoint_generation,
                   owner_installation_id, ownership_epoch, final_relative_path
            FROM version_retention_items
            WHERE plan_id = ?
            ORDER BY ordinal
            """,
            (plan.plan_id,),
        ).fetchall()
        expected = [
            (
                ordinal,
                record.version_object_id,
                record.row_version + 1,
                record.manifest_hash,
                record.target_endpoint_id,
                record.target_endpoint_revision_id,
                record.endpoint_generation,
                record.owner_installation_id,
                record.ownership_epoch,
                record.final_relative_path,
            )
            for ordinal, record in enumerate(plan.candidates)
        ]
        return [tuple(row) for row in rows] == expected

    def _append_planned_event(self, plan: VersionRetentionPlan) -> None:
        payload_json = json.dumps(
            {
                "candidate_count": len(plan.candidates),
                "manifest_hash": plan.manifest_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        event_utc = plan.created_utc
        event_hash = hashlib.sha256(
            json.dumps(
                {
                    "event_kind": "PLAN_CREATED",
                    "event_utc": event_utc,
                    "payload_json": payload_json,
                    "plan_id": plan.plan_id,
                    "plan_sequence": 0,
                    "previous_event_hash": None,
                    "version_object_id": None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            """
            INSERT INTO version_retention_events (
                plan_id,
                plan_sequence,
                version_object_id,
                event_kind,
                event_utc,
                payload_json,
                previous_event_hash,
                event_hash
            )
            VALUES (?, 0, NULL, 'PLAN_CREATED', ?, ?, NULL, ?)
            """,
            (plan.plan_id, event_utc, payload_json, event_hash),
        )


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> RetainedVersionRecord:
    return RetainedVersionRecord(
        version_object_id=str(row[0]),
        handoff_id=str(row[1]),
        run_id=str(row[2]),
        run_target_id=str(row[3]),
        operation_id=str(row[4]),
        job_id=str(row[5]),
        job_revision_id=str(row[6]),
        target_endpoint_id=str(row[7]),
        target_endpoint_revision_id=str(row[8]),
        endpoint_generation=_int_column(row[9]),
        owner_installation_id=str(row[10]),
        ownership_epoch=_int_column(row[11]),
        final_relative_path=str(row[12]),
        original_fingerprint_json=str(row[13]),
        created_utc=str(row[14]),
        retention_policy=str(row[15]),
        retention_until_utc=str(row[16]),
        manifest_hash=str(row[17]),
        state=RetainedVersionState(str(row[18])),
        row_version=_int_column(row[19]),
    )


def _work_item_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> VersionRetentionWorkItem:
    return VersionRetentionWorkItem(
        plan_id=str(row[0]),
        plan_manifest_hash=str(row[1]),
        ordinal=_int_column(row[2]),
        state=VersionRetentionItemState(str(row[3])),
        expected_object_row_version=_int_column(row[4]),
        record=_record_from_row(tuple(row[5:25])),
    )


def _require_permit_binding(
    *,
    item: VersionRetentionWorkItem,
    permit: MutationPermit,
) -> None:
    if (
        permit.run_id != f"version-retention:{item.plan_id}"
        or permit.run_target_id != f"version-retention:{item.plan_id}:{item.ordinal}"
        or permit.endpoint_id != item.record.target_endpoint_id
        or permit.endpoint_revision_id != item.record.target_endpoint_revision_id
        or permit.endpoint_generation != item.record.endpoint_generation
        or permit.owner_installation_id != item.record.owner_installation_id
        or permit.ownership_epoch != item.record.ownership_epoch
        or permit.resource_key != f"endpoint:{item.record.target_endpoint_id}"
    ):
        raise SqliteVersionRetentionStoreError("VERSION_RETENTION_PERMIT_MISMATCH")


def _int_column(value: object) -> int:
    if isinstance(value, bool):
        raise SqliteVersionRetentionStoreError("VERSION_RETENTION_INTEGER_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise SqliteVersionRetentionStoreError("VERSION_RETENTION_INTEGER_INVALID")


def _is_retryable_plan_block(validation_code: str) -> bool:
    return (
        validation_code.startswith("VERSION_RETENTION_RECOVERY_REFERENCE_")
        or validation_code
        == "VERSION_RETENTION_DELETE_INTENT_REVALIDATION_FAILED"
    )
