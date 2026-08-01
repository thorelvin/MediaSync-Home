from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediasync_home.application.endpoint_takeover import (
    EndpointTakeoverCandidate,
    EndpointTakeoverError,
    EndpointTakeoverIntent,
    EndpointTakeoverState,
    PreparedEndpointTakeover,
)


_FOREIGN_REASON = "ENDPOINT_TARGET_FOREIGN_READ_ONLY"
_TAKEOVER_READY_REASON = "ENDPOINT_TARGET_TAKEOVER_PROBE_VERIFIED"
_TERMINAL_RUN_STATES = (
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "PARTIAL_FAILURE",
    "FAILED",
    "CANCELLED",
    "BLOCKED_BY_SAFETY",
    "RECOVERY_REQUIRED",
)
_TERMINAL_RUN_TARGET_STATES = (
    "SUCCEEDED",
    "SUCCEEDED_WITH_WARNINGS",
    "FAILED",
    "CANCELLED",
    "BLOCKED",
    "RECOVERY_REQUIRED",
)


class SqliteEndpointTakeoverStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def load_takeover_intent(
        self,
        *,
        job_id: str,
        source_job_revision_id: str,
        target_ordinal: int,
    ) -> EndpointTakeoverIntent | None:
        row = self._connection.execute(
            f"""
            SELECT {_INTENT_COLUMNS}
            FROM controlled_endpoint_takeover_intents
            WHERE job_id = ?
                AND source_job_revision_id = ?
                AND target_ordinal = ?
            """,
            (job_id, source_job_revision_id, target_ordinal),
        ).fetchone()
        return None if row is None else _intent_from_row(row)

    def load_takeover_candidate(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        target_ordinal: int,
        endpoint_id: str,
        expected_foreign_owner_installation_id: str,
        expected_ownership_epoch: int,
    ) -> EndpointTakeoverCandidate:
        active = self._connection.execute(
            "SELECT active_revision_id FROM job_heads WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if active is None or str(active[0]) != job_revision_id:
            raise _error(
                "ENDPOINT_TAKEOVER_JOB_REVISION_STALE",
                "Refresh the active backup job before confirming takeover.",
                retryable=False,
            )
        row = self._connection.execute(
            """
            SELECT
                bindings.endpoint_id,
                bindings.endpoint_revision_id,
                revisions.generation,
                revisions.display_name,
                revisions.root_uri,
                bindings.registration_state,
                bindings.registration_reason_code,
                observations.inspection_status,
                observations.classification_state,
                observations.marker_json,
                heads.active_revision_id
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            INNER JOIN endpoint_heads AS heads
                ON heads.endpoint_id = bindings.endpoint_id
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = bindings.endpoint_id
                AND observations.endpoint_revision_id = bindings.endpoint_revision_id
            WHERE bindings.job_id = ?
                AND bindings.job_revision_id = ?
                AND bindings.role = 'TARGET'
                AND bindings.ordinal = ?
            """,
            (job_id, job_revision_id, target_ordinal),
        ).fetchone()
        if row is None or str(row[0]) != endpoint_id:
            raise _error(
                "ENDPOINT_TAKEOVER_TARGET_CHANGED",
                "Refresh the selected target before confirming takeover.",
                retryable=False,
            )
        if str(row[10]) != str(row[1]):
            raise _error(
                "ENDPOINT_TAKEOVER_ENDPOINT_REVISION_STALE",
                "Refresh endpoint details before confirming takeover.",
                retryable=False,
            )
        if (
            str(row[5]) != "READ_ONLY_READY"
            or str(row[6]) != _FOREIGN_REASON
            or str(row[7]) != "CLASSIFIED"
            or str(row[8]) != "VALID_FOREIGN"
        ):
            raise _error(
                "ENDPOINT_TAKEOVER_FOREIGN_TARGET_REQUIRED",
                "Refresh endpoint classification before confirming takeover.",
                retryable=False,
            )
        marker = _marker_payload(row[9])
        try:
            foreign_owner = _required_str(marker.get("owner_installation_id"))
            foreign_epoch = _required_positive_int(marker.get("ownership_epoch"))
            control_area_id = _required_str(marker.get("control_area_id"))
            root_identity_hash_algorithm = _required_str(
                marker.get("root_identity_hash_algorithm")
            )
            root_identity_hash = _required_hash(marker.get("root_identity_hash"))
            marker_checksum_algorithm = _required_str(
                marker.get("marker_checksum_algorithm")
            )
            marker_checksum = _required_hash(marker.get("marker_checksum"))
        except ValueError as exc:
            raise _invalid_observation() from exc
        if (
            foreign_owner != expected_foreign_owner_installation_id
            or foreign_epoch != expected_ownership_epoch
        ):
            raise _error(
                "ENDPOINT_TAKEOVER_EXPECTED_OWNER_CHANGED",
                "Review the current foreign owner and confirm takeover again.",
                retryable=False,
            )
        self._require_no_active_mutation(job_id=job_id, endpoint_id=endpoint_id)
        return EndpointTakeoverCandidate(
            job_id=job_id,
            job_revision_id=job_revision_id,
            target_ordinal=target_ordinal,
            endpoint_id=endpoint_id,
            endpoint_revision_id=str(row[1]),
            endpoint_generation=int(row[2]),
            display_name=str(row[3]),
            root_uri=str(row[4]),
            control_area_id=control_area_id,
            foreign_owner_installation_id=foreign_owner,
            foreign_ownership_epoch=foreign_epoch,
            root_identity_hash_algorithm=root_identity_hash_algorithm,
            root_identity_hash=root_identity_hash,
            marker_checksum_algorithm=marker_checksum_algorithm,
            marker_checksum=marker_checksum,
        )

    def save_prepared_takeover_intent(
        self,
        intent: EndpointTakeoverIntent,
    ) -> EndpointTakeoverIntent:
        if intent.state is not EndpointTakeoverState.PREPARED:
            raise ValueError("new endpoint takeover intent must be prepared")
        prepared = intent.prepared
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            self._revalidate_prepared_intent(intent)
            self._connection.execute(
                """
                INSERT INTO controlled_endpoint_takeover_intents (
                    intent_id,
                    job_id,
                    source_job_revision_id,
                    resulting_job_revision_id,
                    analysis_request_id,
                    target_ordinal,
                    endpoint_id,
                    source_endpoint_revision_id,
                    resulting_endpoint_revision_id,
                    foreign_owner_installation_id,
                    foreign_ownership_epoch,
                    owner_installation_id,
                    ownership_epoch,
                    command_request_id,
                    command_idempotency_key,
                    state,
                    prepared_takeover_json,
                    created_utc,
                    updated_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    intent.job_id,
                    intent.source_job_revision_id,
                    intent.resulting_job_revision_id,
                    intent.analysis_request_id,
                    prepared.target_ordinal,
                    prepared.endpoint_id,
                    prepared.source_endpoint_revision_id,
                    prepared.resulting_endpoint_revision_id,
                    prepared.foreign_owner_installation_id,
                    prepared.foreign_ownership_epoch,
                    prepared.owner_installation_id,
                    prepared.ownership_epoch,
                    intent.command_request_id,
                    intent.command_idempotency_key,
                    _serialize_prepared(prepared),
                    intent.created_utc,
                    intent.updated_utc,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._rollback()
            existing = self.load_takeover_intent(
                job_id=intent.job_id,
                source_job_revision_id=intent.source_job_revision_id,
                target_ordinal=prepared.target_ordinal,
            )
            if existing is not None:
                return existing
            raise _error(
                "ENDPOINT_TAKEOVER_INTENT_CONFLICT",
                "Refresh endpoint details before retrying takeover.",
                retryable=False,
            ) from exc
        except EndpointTakeoverError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent.intent_id)

    def mark_takeover_filesystem_applied(
        self,
        *,
        intent_id: str,
        updated_utc: str,
    ) -> EndpointTakeoverIntent:
        return self._transition(
            intent_id=intent_id,
            target_state=EndpointTakeoverState.FILESYSTEM_APPLIED,
            updated_utc=updated_utc,
        )

    def note_takeover_failure(
        self,
        *,
        intent_id: str,
        validation_code: str,
        next_action: str,
        blocked: bool,
        updated_utc: str,
    ) -> EndpointTakeoverIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state in {
            EndpointTakeoverState.COMMITTED,
            EndpointTakeoverState.BLOCKED,
        }:
            return intent
        target_state = EndpointTakeoverState.BLOCKED if blocked else intent.state
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE controlled_endpoint_takeover_intents
                SET
                    state = ?,
                    last_error_code = ?,
                    last_next_action = ?,
                    updated_utc = ?,
                    row_version = row_version + 1
                WHERE intent_id = ? AND state = ?
                """,
                (
                    target_state.value,
                    validation_code,
                    next_action,
                    updated_utc,
                    intent_id,
                    intent.state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise _state_changed()
            self._connection.execute("COMMIT")
        except EndpointTakeoverError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def commit_takeover_intent(
        self,
        *,
        intent_id: str,
        committed_utc: str,
    ) -> EndpointTakeoverIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state is EndpointTakeoverState.COMMITTED:
            return intent
        if intent.state is not EndpointTakeoverState.FILESYSTEM_APPLIED:
            raise _error(
                "ENDPOINT_TAKEOVER_FILESYSTEM_NOT_VERIFIED",
                "Verify the endpoint takeover marker before committing catalog ownership.",
                retryable=True,
            )
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            self._commit_endpoint_revision(intent, committed_utc=committed_utc)
            self._commit_job_revision(intent)
            self._enqueue_full_analysis(intent, requested_utc=committed_utc)
            cursor = self._connection.execute(
                """
                UPDATE controlled_endpoint_takeover_intents
                SET
                    state = 'COMMITTED',
                    last_error_code = NULL,
                    last_next_action = NULL,
                    updated_utc = ?,
                    committed_utc = ?,
                    row_version = row_version + 1
                WHERE intent_id = ? AND state = 'FILESYSTEM_APPLIED'
                """,
                (committed_utc, committed_utc, intent.intent_id),
            )
            if cursor.rowcount != 1:
                raise _state_changed()
            self._connection.execute("COMMIT")
        except EndpointTakeoverError:
            self._rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback()
            raise _error(
                "ENDPOINT_TAKEOVER_CATALOG_CONFLICT",
                "Refresh the active endpoint and backup revisions before retrying.",
                retryable=False,
            ) from exc
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def list_recoverable_takeover_intents(
        self,
        *,
        limit: int,
    ) -> tuple[EndpointTakeoverIntent, ...]:
        if limit < 1 or limit > 128:
            raise ValueError("endpoint takeover recovery limit is invalid")
        rows = self._connection.execute(
            f"""
            SELECT {_INTENT_COLUMNS}
            FROM controlled_endpoint_takeover_intents
            WHERE state IN ('PREPARED', 'FILESYSTEM_APPLIED')
            ORDER BY created_utc, intent_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(_intent_from_row(row) for row in rows)

    def _transition(
        self,
        *,
        intent_id: str,
        target_state: EndpointTakeoverState,
        updated_utc: str,
    ) -> EndpointTakeoverIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state is target_state:
            return intent
        if (
            intent.state is not EndpointTakeoverState.PREPARED
            or target_state is not EndpointTakeoverState.FILESYSTEM_APPLIED
        ):
            raise _error(
                "ENDPOINT_TAKEOVER_TRANSITION_INVALID",
                "Refresh takeover status before retrying.",
                retryable=False,
            )
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE controlled_endpoint_takeover_intents
                SET
                    state = 'FILESYSTEM_APPLIED',
                    last_error_code = NULL,
                    last_next_action = NULL,
                    updated_utc = ?,
                    row_version = row_version + 1
                WHERE intent_id = ? AND state = 'PREPARED'
                """,
                (updated_utc, intent_id),
            )
            if cursor.rowcount != 1:
                raise _state_changed()
            self._connection.execute("COMMIT")
        except EndpointTakeoverError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def _revalidate_prepared_intent(self, intent: EndpointTakeoverIntent) -> None:
        prepared = intent.prepared
        active_job = self._connection.execute(
            "SELECT active_revision_id FROM job_heads WHERE job_id = ?",
            (intent.job_id,),
        ).fetchone()
        active_endpoint = self._connection.execute(
            "SELECT active_revision_id FROM endpoint_heads WHERE endpoint_id = ?",
            (prepared.endpoint_id,),
        ).fetchone()
        if active_job is None or str(active_job[0]) != intent.source_job_revision_id:
            raise _error(
                "ENDPOINT_TAKEOVER_JOB_REVISION_STALE",
                "Refresh the active backup job before confirming takeover.",
                retryable=False,
            )
        if (
            active_endpoint is None
            or str(active_endpoint[0]) != prepared.source_endpoint_revision_id
        ):
            raise _error(
                "ENDPOINT_TAKEOVER_ENDPOINT_REVISION_STALE",
                "Refresh endpoint details before confirming takeover.",
                retryable=False,
            )
        row = self._connection.execute(
            """
            SELECT endpoint_id, endpoint_revision_id, registration_state,
                   registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ? AND job_revision_id = ?
                AND role = 'TARGET' AND ordinal = ?
            """,
            (intent.job_id, intent.source_job_revision_id, prepared.target_ordinal),
        ).fetchone()
        if row is None or (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
        ) != (
            prepared.endpoint_id,
            prepared.source_endpoint_revision_id,
            "READ_ONLY_READY",
            _FOREIGN_REASON,
        ):
            raise _error(
                "ENDPOINT_TAKEOVER_BINDING_CHANGED",
                "Refresh the selected target before confirming takeover.",
                retryable=False,
            )
        self._require_no_active_mutation(
            job_id=intent.job_id,
            endpoint_id=prepared.endpoint_id,
        )

    def _require_no_active_mutation(self, *, job_id: str, endpoint_id: str) -> None:
        run_placeholders = ",".join("?" for _ in _TERMINAL_RUN_STATES)
        target_placeholders = ",".join("?" for _ in _TERMINAL_RUN_TARGET_STATES)
        row = self._connection.execute(
            f"""
            SELECT 1
            FROM runs
            LEFT JOIN run_targets ON run_targets.run_id = runs.id
            WHERE (runs.job_id = ? OR run_targets.endpoint_id = ?)
                AND (
                    runs.state NOT IN ({run_placeholders})
                    OR run_targets.state NOT IN ({target_placeholders})
                )
            LIMIT 1
            """,
            (
                job_id,
                endpoint_id,
                *_TERMINAL_RUN_STATES,
                *_TERMINAL_RUN_TARGET_STATES,
            ),
        ).fetchone()
        if row is not None:
            raise _error(
                "ENDPOINT_TAKEOVER_ACTIVE_MUTATION",
                "Wait for active endpoint work to finish before takeover.",
                retryable=True,
            )

    def _commit_endpoint_revision(
        self,
        intent: EndpointTakeoverIntent,
        *,
        committed_utc: str,
    ) -> None:
        prepared = intent.prepared
        head = self._connection.execute(
            "SELECT active_revision_id FROM endpoint_heads WHERE endpoint_id = ?",
            (prepared.endpoint_id,),
        ).fetchone()
        if head is None or str(head[0]) != prepared.source_endpoint_revision_id:
            raise _error(
                "ENDPOINT_TAKEOVER_ENDPOINT_REVISION_CHANGED",
                "Refresh endpoint details before retrying takeover.",
                retryable=False,
            )
        self._connection.execute(
            """
            INSERT INTO endpoint_revisions (
                endpoint_id,
                id,
                display_name,
                root_uri,
                control_area_id,
                root_identity_hash_algorithm,
                root_identity_hash,
                owner_installation_id,
                ownership_epoch,
                control_marker_checksum_algorithm,
                control_marker_checksum,
                generation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared.endpoint_id,
                prepared.resulting_endpoint_revision_id,
                prepared.display_name,
                prepared.root_uri,
                prepared.control_area_id,
                prepared.root_identity_hash_algorithm,
                prepared.root_identity_hash,
                prepared.owner_installation_id,
                prepared.ownership_epoch,
                prepared.marker_checksum_algorithm,
                prepared.marker_checksum,
                prepared.resulting_endpoint_generation,
            ),
        )
        cursor = self._connection.execute(
            """
            UPDATE endpoint_heads
            SET active_revision_id = ?
            WHERE endpoint_id = ? AND active_revision_id = ?
            """,
            (
                prepared.resulting_endpoint_revision_id,
                prepared.endpoint_id,
                prepared.source_endpoint_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            raise _state_changed()
        self._connection.execute(
            """
            INSERT INTO controlled_endpoint_takeovers (
                endpoint_id,
                endpoint_revision_id,
                endpoint_generation,
                intent_id,
                control_area_id,
                previous_owner_installation_id,
                previous_ownership_epoch,
                owner_installation_id,
                ownership_epoch,
                root_identity_hash_algorithm,
                root_identity_hash,
                marker_checksum_algorithm,
                marker_checksum,
                takeover_record_path,
                ownership_record_path,
                probe_completed_utc,
                created_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared.endpoint_id,
                prepared.resulting_endpoint_revision_id,
                prepared.resulting_endpoint_generation,
                intent.intent_id,
                prepared.control_area_id,
                prepared.foreign_owner_installation_id,
                prepared.foreign_ownership_epoch,
                prepared.owner_installation_id,
                prepared.ownership_epoch,
                prepared.root_identity_hash_algorithm,
                prepared.root_identity_hash,
                prepared.marker_checksum_algorithm,
                prepared.marker_checksum,
                prepared.takeover_record_path,
                prepared.ownership_record_path,
                committed_utc,
                committed_utc,
            ),
        )

    def _commit_job_revision(self, intent: EndpointTakeoverIntent) -> None:
        prepared = intent.prepared
        source = self._connection.execute(
            """
            SELECT filter_set_id, filter_set_version
            FROM job_revisions
            WHERE job_id = ? AND id = ?
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchone()
        if source is None:
            raise _error(
                "ENDPOINT_TAKEOVER_SOURCE_JOB_REVISION_MISSING",
                "Restore the source backup revision before retrying takeover.",
                retryable=False,
            )
        self._connection.execute(
            """
            INSERT INTO job_revisions (job_id, id, filter_set_id, filter_set_version)
            VALUES (?, ?, ?, ?)
            """,
            (
                intent.job_id,
                intent.resulting_job_revision_id,
                str(source[0]),
                int(source[1]),
            ),
        )
        details = self._connection.execute(
            """
            SELECT draft_id, source_name, source_path_label, defaults_json, targets_json
            FROM standard_backup_job_revision_details
            WHERE job_id = ? AND job_revision_id = ?
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchone()
        if details is None:
            raise _error(
                "ENDPOINT_TAKEOVER_SOURCE_JOB_DETAILS_MISSING",
                "Restore the source backup details before retrying takeover.",
                retryable=False,
            )
        self._connection.execute(
            """
            INSERT INTO standard_backup_job_revision_details (
                job_id, job_revision_id, draft_id, command_request_id, idempotency_key,
                source_name, source_path_label, defaults_json, targets_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.job_id,
                intent.resulting_job_revision_id,
                str(details[0]),
                intent.intent_id,
                f"controlled-endpoint-takeover:{intent.intent_id}",
                str(details[1]),
                str(details[2]),
                str(details[3]),
                str(details[4]),
            ),
        )
        bindings = self._connection.execute(
            """
            SELECT role, ordinal, endpoint_id, endpoint_revision_id,
                   registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ? AND job_revision_id = ?
            ORDER BY role, ordinal
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchall()
        replaced = False
        for row in bindings:
            replacement = (
                str(row[0]) == "TARGET"
                and int(row[1]) == prepared.target_ordinal
                and str(row[2]) == prepared.endpoint_id
                and str(row[3]) == prepared.source_endpoint_revision_id
            )
            replaced = replaced or replacement
            self._connection.execute(
                """
                INSERT INTO standard_backup_job_endpoint_bindings (
                    job_id, job_revision_id, role, ordinal, endpoint_id,
                    endpoint_revision_id, registration_state, registration_reason_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.job_id,
                    intent.resulting_job_revision_id,
                    str(row[0]),
                    int(row[1]),
                    str(row[2]),
                    (
                        prepared.resulting_endpoint_revision_id
                        if replacement
                        else str(row[3])
                    ),
                    "WRITABLE_READY" if replacement else str(row[4]),
                    _TAKEOVER_READY_REASON if replacement else str(row[5]),
                ),
            )
        if not replaced:
            raise _error(
                "ENDPOINT_TAKEOVER_SOURCE_BINDING_MISSING",
                "Restore the source target binding before retrying takeover.",
                retryable=False,
            )
        cursor = self._connection.execute(
            """
            UPDATE job_heads
            SET active_revision_id = ?
            WHERE job_id = ? AND active_revision_id = ?
            """,
            (
                intent.resulting_job_revision_id,
                intent.job_id,
                intent.source_job_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            raise _state_changed()

    def _enqueue_full_analysis(
        self,
        intent: EndpointTakeoverIntent,
        *,
        requested_utc: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO backup_analysis_requests (
                request_id,
                command_idempotency_key,
                job_id,
                job_revision_id,
                state,
                requested_utc,
                start_when_safe
            )
            VALUES (?, ?, ?, ?, 'QUEUED', ?, 0)
            """,
            (
                intent.analysis_request_id,
                f"controlled-endpoint-takeover:{intent.intent_id}",
                intent.job_id,
                intent.resulting_job_revision_id,
                requested_utc,
            ),
        )

    def _load_intent_by_id(self, intent_id: str) -> EndpointTakeoverIntent:
        row = self._connection.execute(
            f"""
            SELECT {_INTENT_COLUMNS}
            FROM controlled_endpoint_takeover_intents
            WHERE intent_id = ?
            """,
            (intent_id,),
        ).fetchone()
        if row is None:
            raise _error(
                "ENDPOINT_TAKEOVER_INTENT_MISSING",
                "Restore the takeover intent from a verified state backup.",
                retryable=False,
            )
        return _intent_from_row(row)

    def _require_idle(self) -> None:
        if self._connection.in_transaction:
            raise _error(
                "ENDPOINT_TAKEOVER_REQUIRES_IDLE_CONNECTION",
                "Retry takeover after the current catalog transaction completes.",
                retryable=True,
            )

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


_INTENT_COLUMNS = """
    intent_id,
    job_id,
    source_job_revision_id,
    resulting_job_revision_id,
    analysis_request_id,
    command_request_id,
    command_idempotency_key,
    state,
    prepared_takeover_json,
    created_utc,
    updated_utc,
    last_error_code,
    last_next_action
"""


def _intent_from_row(row: sqlite3.Row | tuple[object, ...]) -> EndpointTakeoverIntent:
    return EndpointTakeoverIntent(
        intent_id=str(row[0]),
        job_id=str(row[1]),
        source_job_revision_id=str(row[2]),
        resulting_job_revision_id=str(row[3]),
        analysis_request_id=str(row[4]),
        command_request_id=str(row[5]),
        command_idempotency_key=str(row[6]),
        state=EndpointTakeoverState(str(row[7])),
        prepared=_deserialize_prepared(str(row[8])),
        created_utc=str(row[9]),
        updated_utc=str(row[10]),
        last_error_code=None if row[11] is None else str(row[11]),
        last_next_action=None if row[12] is None else str(row[12]),
    )


def _serialize_prepared(prepared: PreparedEndpointTakeover) -> str:
    return json.dumps(
        {
            name: getattr(prepared, name)
            for name in PreparedEndpointTakeover.__dataclass_fields__
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _deserialize_prepared(payload: str) -> PreparedEndpointTakeover:
    try:
        data: Any = json.loads(payload)
        if not isinstance(data, dict) or set(data) != set(
            PreparedEndpointTakeover.__dataclass_fields__
        ):
            raise ValueError
        return PreparedEndpointTakeover(
            target_ordinal=_required_positive_int(data["target_ordinal"]),
            endpoint_id=_required_str(data["endpoint_id"]),
            source_endpoint_revision_id=_required_str(
                data["source_endpoint_revision_id"]
            ),
            resulting_endpoint_revision_id=_required_str(
                data["resulting_endpoint_revision_id"]
            ),
            resulting_endpoint_generation=_required_positive_int(
                data["resulting_endpoint_generation"]
            ),
            display_name=_required_str(data["display_name"]),
            root_uri=_required_str(data["root_uri"]),
            control_area_id=_required_str(data["control_area_id"]),
            foreign_owner_installation_id=_required_str(
                data["foreign_owner_installation_id"]
            ),
            foreign_ownership_epoch=_required_positive_int(
                data["foreign_ownership_epoch"]
            ),
            owner_installation_id=_required_str(data["owner_installation_id"]),
            ownership_epoch=_required_positive_int(data["ownership_epoch"]),
            root_identity_hash_algorithm=_required_str(
                data["root_identity_hash_algorithm"]
            ),
            root_identity_hash=_required_hash(data["root_identity_hash"]),
            old_marker_checksum_algorithm=_required_str(
                data["old_marker_checksum_algorithm"]
            ),
            old_marker_checksum=_required_hash(data["old_marker_checksum"]),
            marker_checksum_algorithm=_required_str(data["marker_checksum_algorithm"]),
            marker_checksum=_required_hash(data["marker_checksum"]),
            marker_payload_json=_required_str(data["marker_payload_json"]),
            ownership_record_path=_required_str(data["ownership_record_path"]),
            ownership_payload_json=_required_str(data["ownership_payload_json"]),
            takeover_record_path=_required_str(data["takeover_record_path"]),
            takeover_payload_json=_required_str(data["takeover_payload_json"]),
            probe_token=_required_hash(data["probe_token"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error(
            "ENDPOINT_TAKEOVER_INTENT_INVALID",
            "Restore the takeover intent from a verified state backup.",
            retryable=False,
        ) from exc


def _marker_payload(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise _invalid_observation()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise _invalid_observation() from exc
    if not isinstance(payload, dict):
        raise _invalid_observation()
    return payload


def _required_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("required takeover string is invalid")
    return value


def _required_positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("required takeover integer is invalid")
    return value


def _required_hash(value: object) -> str:
    result = _required_str(value)
    if len(result) != 64:
        raise ValueError("required takeover hash is invalid")
    return result


def _invalid_observation() -> EndpointTakeoverError:
    return _error(
        "ENDPOINT_TAKEOVER_OBSERVATION_INVALID",
        "Refresh endpoint classification before confirming takeover.",
        retryable=False,
    )


def _state_changed() -> EndpointTakeoverError:
    return _error(
        "ENDPOINT_TAKEOVER_STATE_CHANGED",
        "Refresh takeover status before retrying.",
        retryable=True,
    )


def _persistence_error() -> EndpointTakeoverError:
    return _error(
        "ENDPOINT_TAKEOVER_PERSISTENCE_FAILED",
        "Retry takeover after catalog storage is writable.",
        retryable=True,
    )


def _error(code: str, next_action: str, *, retryable: bool) -> EndpointTakeoverError:
    return EndpointTakeoverError(code, next_action, retryable=retryable)
