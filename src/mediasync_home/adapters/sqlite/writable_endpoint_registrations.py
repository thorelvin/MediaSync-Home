from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediasync_home.application.writable_endpoint_registration import (
    AppliedWritableEndpointCapabilities,
    PreparedWritableEndpoint,
    WritableEndpointRegistrationCandidate,
    WritableEndpointRegistrationError,
    WritableEndpointRegistrationIntent,
    WritableEndpointRegistrationState,
)
from mediasync_home.application.endpoint_capabilities import (
    EndpointCapabilityEvidenceError,
    EndpointCapabilityProbeScope,
)


_WRITABLE_READY_REASON = "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
_MAX_REGISTRATION_PROTECTED_ROOTS = 1_024


class SqliteWritableEndpointRegistrationStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def load_registration_intent(
        self,
        *,
        job_id: str,
        source_job_revision_id: str,
    ) -> WritableEndpointRegistrationIntent | None:
        row = self._connection.execute(
            """
            SELECT
                intent_id,
                job_id,
                source_job_revision_id,
                resulting_job_revision_id,
                command_request_id,
                command_idempotency_key,
                state,
                prepared_targets_json,
                created_utc,
                updated_utc,
                last_error_code,
                last_next_action
            FROM writable_endpoint_registration_intents
            WHERE job_id = ? AND source_job_revision_id = ?
            """,
            (job_id, source_job_revision_id),
        ).fetchone()
        return None if row is None else _intent_from_row(row)

    def load_registration_candidates(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> tuple[WritableEndpointRegistrationCandidate, ...]:
        if self._load_active_job_revision(job_id) != job_revision_id:
            raise _registration_error(
                "WRITABLE_ENDPOINT_JOB_REVISION_STALE",
                "Refresh the active backup job before registering its targets.",
                retryable=False,
            )
        rows = self._connection.execute(
            """
            SELECT
                bindings.ordinal,
                bindings.endpoint_id,
                bindings.endpoint_revision_id,
                revisions.generation,
                revisions.display_name,
                revisions.root_uri,
                bindings.registration_state,
                bindings.registration_reason_code,
                observations.inspection_status,
                observations.classification_state
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = bindings.endpoint_id
                AND observations.endpoint_revision_id = bindings.endpoint_revision_id
            WHERE bindings.job_id = ?
                AND bindings.job_revision_id = ?
                AND bindings.role = 'TARGET'
            ORDER BY bindings.ordinal
            """,
            (job_id, job_revision_id),
        ).fetchall()
        if not rows:
            raise _registration_error(
                "WRITABLE_ENDPOINT_TARGET_BINDINGS_MISSING",
                "Refresh or recreate the backup job before registering targets.",
                retryable=False,
            )
        candidates: list[WritableEndpointRegistrationCandidate] = []
        for row in rows:
            if str(row[6]) == "WRITABLE_READY":
                continue
            if (
                str(row[6]) != "REGISTRATION_PENDING"
                or str(row[8]) != "CLASSIFIED"
                or str(row[9]) != "ABSENT"
            ):
                raise _registration_error(
                    "WRITABLE_ENDPOINT_TARGET_NOT_REGISTRABLE",
                    (
                        "Inspect every target registration status before retrying. "
                        f"The current target reason is {row[7]}."
                    ),
                    retryable=False,
                )
            candidates.append(
                WritableEndpointRegistrationCandidate(
                job_id=job_id,
                job_revision_id=job_revision_id,
                target_ordinal=int(row[0]),
                endpoint_id=str(row[1]),
                endpoint_revision_id=str(row[2]),
                endpoint_generation=int(row[3]),
                display_name=str(row[4]),
                root_uri=str(row[5]),
            )
            )
        return tuple(candidates)

    def load_registration_protected_root_uris(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> tuple[str, ...]:
        active = self._connection.execute(
            """
            SELECT heads.active_revision_id, jobs.lifecycle_state, deletions.job_id
            FROM jobs
            INNER JOIN job_heads AS heads ON heads.job_id = jobs.id
            LEFT JOIN job_deletions AS deletions ON deletions.job_id = jobs.id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
        if (
            active is None
            or str(active[0]) != job_revision_id
            or str(active[1]) != "ACTIVE"
            or active[2] is not None
        ):
            raise _registration_error(
                "WRITABLE_ENDPOINT_JOB_REVISION_STALE",
                "Refresh the active backup job before registering its targets.",
                retryable=False,
            )
        source_count = int(
            self._connection.execute(
                """
                SELECT COUNT(*)
                FROM standard_backup_job_endpoint_bindings
                WHERE job_id = ?
                    AND job_revision_id = ?
                    AND role = 'SOURCE'
                """,
                (job_id, job_revision_id),
            ).fetchone()[0]
        )
        if source_count != 1:
            raise _registration_error(
                "WRITABLE_ENDPOINT_ROOT_OVERLAP_CONTEXT_MISSING",
                "Refresh the active source root before registering this target.",
                retryable=True,
            )
        rows = self._connection.execute(
            """
            SELECT revisions.root_uri
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            INNER JOIN jobs
                ON jobs.id = bindings.job_id
            INNER JOIN job_heads AS heads
                ON heads.job_id = bindings.job_id
                AND heads.active_revision_id = bindings.job_revision_id
            WHERE jobs.lifecycle_state = 'ACTIVE'
                AND NOT EXISTS (
                    SELECT 1
                    FROM job_deletions AS deletions
                    WHERE deletions.job_id = jobs.id
                )
                AND NOT (
                    bindings.job_id = ?
                    AND bindings.job_revision_id = ?
                    AND bindings.role = 'TARGET'
                    AND bindings.registration_state = 'REGISTRATION_PENDING'
                )
            ORDER BY
                bindings.job_id,
                bindings.job_revision_id,
                bindings.role,
                bindings.ordinal
            LIMIT ?
            """,
            (job_id, job_revision_id, _MAX_REGISTRATION_PROTECTED_ROOTS + 1),
        ).fetchall()
        if len(rows) > _MAX_REGISTRATION_PROTECTED_ROOTS:
            raise _registration_error(
                "WRITABLE_ENDPOINT_ROOT_OVERLAP_SET_LIMIT_EXCEEDED",
                "Archive unused jobs before registering another writable target.",
                retryable=False,
            )
        roots = tuple(dict.fromkeys(str(row[0]) for row in rows))
        if not roots:
            raise _registration_error(
                "WRITABLE_ENDPOINT_ROOT_OVERLAP_CONTEXT_MISSING",
                "Refresh every active endpoint root before registering this target.",
                retryable=True,
            )
        return roots

    def save_prepared_registration_intent(
        self,
        intent: WritableEndpointRegistrationIntent,
    ) -> WritableEndpointRegistrationIntent:
        if intent.state is not WritableEndpointRegistrationState.PREPARED:
            raise ValueError("new writable endpoint registration intent must be prepared")
        payload = _serialize_prepared_targets(intent.prepared_targets)
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            self._revalidate_prepared_intent(intent)
            self._connection.execute(
                """
                INSERT INTO writable_endpoint_registration_intents (
                    intent_id,
                    job_id,
                    source_job_revision_id,
                    resulting_job_revision_id,
                    command_request_id,
                    command_idempotency_key,
                    state,
                    prepared_targets_json,
                    created_utc,
                    updated_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    intent.job_id,
                    intent.source_job_revision_id,
                    intent.resulting_job_revision_id,
                    intent.command_request_id,
                    intent.command_idempotency_key,
                    payload,
                    intent.created_utc,
                    intent.updated_utc,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._rollback()
            existing = self.load_registration_intent(
                job_id=intent.job_id,
                source_job_revision_id=intent.source_job_revision_id,
            )
            if existing is not None:
                return existing
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_INTENT_CONFLICT",
                "Refresh the active backup job before retrying registration.",
                retryable=False,
            ) from exc
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        loaded = self.load_registration_intent(
            job_id=intent.job_id,
            source_job_revision_id=intent.source_job_revision_id,
        )
        if loaded is None:
            raise _persistence_error()
        return loaded

    def mark_registration_filesystem_applied(
        self,
        *,
        intent_id: str,
        applied_capabilities: tuple[AppliedWritableEndpointCapabilities, ...],
        updated_utc: str,
    ) -> WritableEndpointRegistrationIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state is WritableEndpointRegistrationState.FILESYSTEM_APPLIED:
            return intent
        if intent.state is not WritableEndpointRegistrationState.PREPARED:
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_STATE_CHANGED",
                "Refresh target registration status before retrying.",
                retryable=True,
            )
        expected = {
            (
                target.target_ordinal,
                target.endpoint_id,
                target.resulting_endpoint_revision_id,
            )
            for target in intent.prepared_targets
        }
        observed = {
            (
                item.target_ordinal,
                item.endpoint_id,
                item.resulting_endpoint_revision_id,
            )
            for item in applied_capabilities
        }
        if expected != observed or len(observed) != len(applied_capabilities):
            raise _registration_error(
                "WRITABLE_ENDPOINT_CAPABILITY_EVIDENCE_INCOMPLETE",
                "Retry the controlled capability probe for every target.",
                retryable=True,
            )
        try:
            for item in applied_capabilities:
                item.evidence.validated_profile(
                    expected_scope=EndpointCapabilityProbeScope.CONTROLLED_WRITABLE
                )
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            self._require_active_job_revision(
                intent.job_id,
                intent.source_job_revision_id,
            )
            for item in applied_capabilities:
                self._connection.execute(
                    """
                    INSERT INTO writable_endpoint_capability_observations (
                        intent_id,
                        endpoint_id,
                        endpoint_revision_id,
                        capabilities_json,
                        capabilities_hash,
                        observed_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent_id,
                        item.endpoint_id,
                        item.resulting_endpoint_revision_id,
                        item.evidence.profile_json,
                        item.evidence.capabilities_hash,
                        updated_utc,
                    ),
                )
            cursor = self._connection.execute(
                """
                UPDATE writable_endpoint_registration_intents
                SET state = 'FILESYSTEM_APPLIED', updated_utc = ?, row_version = row_version + 1
                WHERE intent_id = ? AND state = 'PREPARED'
                """,
                (updated_utc, intent_id),
            )
            if cursor.rowcount != 1:
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REGISTRATION_STATE_CHANGED",
                    "Refresh target registration status before retrying.",
                    retryable=True,
                )
            self._connection.execute("COMMIT")
        except EndpointCapabilityEvidenceError as exc:
            self._rollback()
            raise _registration_error(
                str(exc),
                "Retry the controlled target capability probe.",
                retryable=True,
            ) from exc
        except WritableEndpointRegistrationError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def note_registration_failure(
        self,
        *,
        intent_id: str,
        validation_code: str,
        next_action: str,
        blocked: bool,
        updated_utc: str,
    ) -> WritableEndpointRegistrationIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state in {
            WritableEndpointRegistrationState.COMMITTED,
            WritableEndpointRegistrationState.BLOCKED,
        }:
            return intent
        target_state = (
            WritableEndpointRegistrationState.BLOCKED if blocked else intent.state
        )
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE writable_endpoint_registration_intents
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
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REGISTRATION_STATE_CHANGED",
                    "Refresh target registration status before retrying.",
                    retryable=True,
                )
            self._connection.execute("COMMIT")
        except WritableEndpointRegistrationError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def commit_registration_intent(
        self,
        *,
        intent_id: str,
        committed_utc: str,
    ) -> WritableEndpointRegistrationIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state is WritableEndpointRegistrationState.COMMITTED:
            return intent
        if intent.state is not WritableEndpointRegistrationState.FILESYSTEM_APPLIED:
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_FILESYSTEM_NOT_VERIFIED",
                "Verify the target control area before committing registration.",
                retryable=True,
            )
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            self._require_active_job_revision(
                intent.job_id,
                intent.source_job_revision_id,
            )
            self._commit_endpoint_revisions(intent, committed_utc=committed_utc)
            self._commit_job_revision(intent)
            cursor = self._connection.execute(
                """
                UPDATE writable_endpoint_registration_intents
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
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REGISTRATION_STATE_CHANGED",
                    "Refresh target registration status before retrying.",
                    retryable=True,
                )
            self._connection.execute("COMMIT")
        except WritableEndpointRegistrationError:
            self._rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback()
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_CATALOG_CONFLICT",
                "Refresh the active endpoint and backup revisions before retrying.",
                retryable=False,
            ) from exc
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def list_recoverable_registration_intents(
        self,
        *,
        limit: int,
    ) -> tuple[WritableEndpointRegistrationIntent, ...]:
        if limit < 1 or limit > 128:
            raise ValueError("writable endpoint registration recovery limit is invalid")
        rows = self._connection.execute(
            """
            SELECT
                intent_id,
                job_id,
                source_job_revision_id,
                resulting_job_revision_id,
                command_request_id,
                command_idempotency_key,
                state,
                prepared_targets_json,
                created_utc,
                updated_utc,
                last_error_code,
                last_next_action
            FROM writable_endpoint_registration_intents
            WHERE state IN ('PREPARED', 'FILESYSTEM_APPLIED')
                AND EXISTS (
                    SELECT 1
                    FROM jobs
                    WHERE jobs.id = writable_endpoint_registration_intents.job_id
                        AND jobs.lifecycle_state = 'ACTIVE'
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM job_deletions AS deletions
                    WHERE deletions.job_id = writable_endpoint_registration_intents.job_id
                )
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
        expected_states: tuple[WritableEndpointRegistrationState, ...],
        target_state: WritableEndpointRegistrationState,
        updated_utc: str,
    ) -> WritableEndpointRegistrationIntent:
        intent = self._load_intent_by_id(intent_id)
        if intent.state is target_state:
            return intent
        if intent.state not in expected_states:
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_TRANSITION_INVALID",
                "Refresh target registration status before retrying.",
                retryable=False,
            )
        try:
            self._require_idle()
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                """
                UPDATE writable_endpoint_registration_intents
                SET
                    state = ?,
                    last_error_code = NULL,
                    last_next_action = NULL,
                    updated_utc = ?,
                    row_version = row_version + 1
                WHERE intent_id = ? AND state = ?
                """,
                (target_state.value, updated_utc, intent_id, intent.state.value),
            )
            if cursor.rowcount != 1:
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REGISTRATION_STATE_CHANGED",
                    "Refresh target registration status before retrying.",
                    retryable=True,
                )
            self._connection.execute("COMMIT")
        except WritableEndpointRegistrationError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise _persistence_error() from exc
        return self._load_intent_by_id(intent_id)

    def _revalidate_prepared_intent(
        self,
        intent: WritableEndpointRegistrationIntent,
    ) -> None:
        self._require_active_job_revision(
            intent.job_id,
            intent.source_job_revision_id,
        )
        expected = {
            (target.target_ordinal, target.endpoint_id, target.source_endpoint_revision_id)
            for target in intent.prepared_targets
        }
        rows = self._connection.execute(
            """
            SELECT ordinal, endpoint_id, endpoint_revision_id
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ?
                AND job_revision_id = ?
                AND role = 'TARGET'
                AND registration_state = 'REGISTRATION_PENDING'
            ORDER BY ordinal
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchall()
        actual = {(int(row[0]), str(row[1]), str(row[2])) for row in rows}
        if not expected or actual != expected:
            raise _registration_error(
                "WRITABLE_ENDPOINT_BINDINGS_CHANGED",
                "Refresh the active backup job before registering its targets.",
                retryable=False,
            )
        for target in intent.prepared_targets:
            head = self._connection.execute(
                """
                SELECT heads.active_revision_id, revisions.generation
                FROM endpoint_heads AS heads
                INNER JOIN endpoint_revisions AS revisions
                    ON revisions.endpoint_id = heads.endpoint_id
                    AND revisions.id = heads.active_revision_id
                WHERE heads.endpoint_id = ?
                """,
                (target.endpoint_id,),
            ).fetchone()
            if (
                head is None
                or str(head[0]) != target.source_endpoint_revision_id
                or int(head[1]) + 1 != target.resulting_endpoint_generation
            ):
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REVISION_CHANGED",
                    "Refresh the endpoint revision before retrying registration.",
                    retryable=False,
                )

    def _commit_endpoint_revisions(
        self,
        intent: WritableEndpointRegistrationIntent,
        *,
        committed_utc: str,
    ) -> None:
        for target in intent.prepared_targets:
            head = self._connection.execute(
                """
                SELECT active_revision_id
                FROM endpoint_heads
                WHERE endpoint_id = ?
                """,
                (target.endpoint_id,),
            ).fetchone()
            if head is None or str(head[0]) != target.source_endpoint_revision_id:
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REVISION_CHANGED",
                    "Refresh the endpoint revision before retrying registration.",
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
                    target.endpoint_id,
                    target.resulting_endpoint_revision_id,
                    target.display_name,
                    target.root_uri,
                    target.control_area_id,
                    target.root_identity_hash_algorithm,
                    target.root_identity_hash,
                    target.owner_installation_id,
                    target.ownership_epoch,
                    target.marker_checksum_algorithm,
                    target.marker_checksum,
                    target.resulting_endpoint_generation,
                ),
            )
            cursor = self._connection.execute(
                """
                UPDATE endpoint_heads
                SET active_revision_id = ?
                WHERE endpoint_id = ? AND active_revision_id = ?
                """,
                (
                    target.resulting_endpoint_revision_id,
                    target.endpoint_id,
                    target.source_endpoint_revision_id,
                ),
            )
            if cursor.rowcount != 1:
                raise _registration_error(
                    "WRITABLE_ENDPOINT_REVISION_CHANGED",
                    "Refresh the endpoint revision before retrying registration.",
                    retryable=False,
                )
            self._connection.execute(
                """
                INSERT INTO writable_endpoint_registrations (
                    endpoint_id,
                    endpoint_revision_id,
                    endpoint_generation,
                    intent_id,
                    control_area_id,
                    owner_installation_id,
                    ownership_epoch,
                    root_identity_hash_algorithm,
                    root_identity_hash,
                    marker_checksum_algorithm,
                    marker_checksum,
                    write_capabilities_json,
                    write_capabilities_hash,
                    probe_completed_utc,
                    created_utc
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    capabilities.capabilities_json,
                    capabilities.capabilities_hash,
                    ?, ?
                FROM writable_endpoint_capability_observations AS capabilities
                WHERE capabilities.intent_id = ?
                    AND capabilities.endpoint_id = ?
                    AND capabilities.endpoint_revision_id = ?
                """,
                (
                    target.endpoint_id,
                    target.resulting_endpoint_revision_id,
                    target.resulting_endpoint_generation,
                    intent.intent_id,
                    target.control_area_id,
                    target.owner_installation_id,
                    target.ownership_epoch,
                    target.root_identity_hash_algorithm,
                    target.root_identity_hash,
                    target.marker_checksum_algorithm,
                    target.marker_checksum,
                    committed_utc,
                    committed_utc,
                    intent.intent_id,
                    target.endpoint_id,
                    target.resulting_endpoint_revision_id,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise _registration_error(
                    "WRITABLE_ENDPOINT_CAPABILITY_EVIDENCE_MISSING",
                    "Retry target registration after completing its capability probe.",
                    retryable=True,
                )

    def _commit_job_revision(self, intent: WritableEndpointRegistrationIntent) -> None:
        source = self._connection.execute(
            """
            SELECT filter_set_id, filter_set_version
            FROM job_revisions
            WHERE job_id = ? AND id = ?
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchone()
        if source is None:
            raise _registration_error(
                "WRITABLE_ENDPOINT_SOURCE_JOB_REVISION_MISSING",
                "Restore the source backup revision before retrying registration.",
                retryable=False,
            )
        self._connection.execute(
            """
            INSERT INTO job_revisions (
                job_id,
                id,
                filter_set_id,
                filter_set_version
            )
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
            SELECT
                draft_id,
                source_name,
                source_path_label,
                defaults_json,
                targets_json
            FROM standard_backup_job_revision_details
            WHERE job_id = ? AND job_revision_id = ?
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchone()
        if details is None:
            raise _registration_error(
                "WRITABLE_ENDPOINT_SOURCE_JOB_DETAILS_MISSING",
                "Restore the source backup revision before retrying registration.",
                retryable=False,
            )
        self._connection.execute(
            """
            INSERT INTO standard_backup_job_revision_details (
                job_id,
                job_revision_id,
                draft_id,
                command_request_id,
                idempotency_key,
                source_name,
                source_path_label,
                defaults_json,
                targets_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.job_id,
                intent.resulting_job_revision_id,
                str(details[0]),
                intent.intent_id,
                f"writable-endpoint-registration:{intent.intent_id}",
                str(details[1]),
                str(details[2]),
                str(details[3]),
                str(details[4]),
            ),
        )
        replacements = {
            target.target_ordinal: target for target in intent.prepared_targets
        }
        bindings = self._connection.execute(
            """
            SELECT
                role,
                ordinal,
                endpoint_id,
                endpoint_revision_id,
                registration_state,
                registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = ? AND job_revision_id = ?
            ORDER BY ordinal
            """,
            (intent.job_id, intent.source_job_revision_id),
        ).fetchall()
        source_count = sum(str(row[0]) == "SOURCE" for row in bindings)
        target_ordinals = {
            int(row[1]) for row in bindings if str(row[0]) == "TARGET"
        }
        if (
            source_count != 1
            or not target_ordinals
            or not set(replacements).issubset(target_ordinals)
        ):
            raise _registration_error(
                "WRITABLE_ENDPOINT_SOURCE_BINDINGS_INCOMPLETE",
                "Refresh the backup job endpoint bindings before retrying registration.",
                retryable=False,
            )
        for row in bindings:
            role = str(row[0])
            ordinal = int(row[1])
            replacement = replacements.get(ordinal) if role == "TARGET" else None
            if role == "TARGET" and replacement is None and str(row[4]) != "WRITABLE_READY":
                raise _registration_error(
                    "WRITABLE_ENDPOINT_SOURCE_BINDINGS_INCOMPLETE",
                    "Refresh the backup job endpoint bindings before retrying registration.",
                    retryable=False,
                )
            self._connection.execute(
                """
                INSERT INTO standard_backup_job_endpoint_bindings (
                    job_id,
                    job_revision_id,
                    role,
                    ordinal,
                    endpoint_id,
                    endpoint_revision_id,
                    registration_state,
                    registration_reason_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.job_id,
                    intent.resulting_job_revision_id,
                    role,
                    ordinal,
                    str(row[2]),
                    (
                        str(row[3])
                        if replacement is None
                        else replacement.resulting_endpoint_revision_id
                    ),
                    str(row[4]) if replacement is None else "WRITABLE_READY",
                    str(row[5]) if replacement is None else _WRITABLE_READY_REASON,
                ),
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
            raise _registration_error(
                "WRITABLE_ENDPOINT_JOB_REVISION_CHANGED",
                "Refresh the active backup job before retrying registration.",
                retryable=False,
            )

    def _load_intent_by_id(self, intent_id: str) -> WritableEndpointRegistrationIntent:
        row = self._connection.execute(
            """
            SELECT
                intent_id,
                job_id,
                source_job_revision_id,
                resulting_job_revision_id,
                command_request_id,
                command_idempotency_key,
                state,
                prepared_targets_json,
                created_utc,
                updated_utc,
                last_error_code,
                last_next_action
            FROM writable_endpoint_registration_intents
            WHERE intent_id = ?
            """,
            (intent_id,),
        ).fetchone()
        if row is None:
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_INTENT_MISSING",
                "Restore the registration intent before retrying.",
                retryable=False,
            )
        return _intent_from_row(row)

    def _load_active_job_revision(self, job_id: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT heads.active_revision_id
            FROM jobs
            INNER JOIN job_heads AS heads ON heads.job_id = jobs.id
            LEFT JOIN job_deletions AS deletions ON deletions.job_id = jobs.id
            WHERE jobs.id = ?
                AND jobs.lifecycle_state = 'ACTIVE'
                AND deletions.job_id IS NULL
            """,
            (job_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def _require_active_job_revision(
        self,
        job_id: str,
        expected_revision_id: str,
    ) -> None:
        if self._load_active_job_revision(job_id) != expected_revision_id:
            raise _registration_error(
                "WRITABLE_ENDPOINT_JOB_REVISION_STALE",
                "Refresh the active backup job before registering its targets.",
                retryable=False,
            )

    def _require_idle(self) -> None:
        if self._connection.in_transaction:
            raise _registration_error(
                "WRITABLE_ENDPOINT_REGISTRATION_REQUIRES_IDLE_CONNECTION",
                "Retry registration after the current catalog transaction completes.",
                retryable=True,
            )

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _intent_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> WritableEndpointRegistrationIntent:
    return WritableEndpointRegistrationIntent(
        intent_id=str(row[0]),
        job_id=str(row[1]),
        source_job_revision_id=str(row[2]),
        resulting_job_revision_id=str(row[3]),
        command_request_id=str(row[4]),
        command_idempotency_key=str(row[5]),
        state=WritableEndpointRegistrationState(str(row[6])),
        prepared_targets=_deserialize_prepared_targets(str(row[7])),
        created_utc=str(row[8]),
        updated_utc=str(row[9]),
        last_error_code=None if row[10] is None else str(row[10]),
        last_next_action=None if row[11] is None else str(row[11]),
    )


def _serialize_prepared_targets(
    targets: tuple[PreparedWritableEndpoint, ...],
) -> str:
    return json.dumps(
        [
            {
                "target_ordinal": target.target_ordinal,
                "endpoint_id": target.endpoint_id,
                "source_endpoint_revision_id": target.source_endpoint_revision_id,
                "resulting_endpoint_revision_id": target.resulting_endpoint_revision_id,
                "resulting_endpoint_generation": target.resulting_endpoint_generation,
                "display_name": target.display_name,
                "root_uri": target.root_uri,
                "control_area_id": target.control_area_id,
                "owner_installation_id": target.owner_installation_id,
                "ownership_epoch": target.ownership_epoch,
                "root_identity_hash_algorithm": target.root_identity_hash_algorithm,
                "root_identity_hash": target.root_identity_hash,
                "marker_checksum_algorithm": target.marker_checksum_algorithm,
                "marker_checksum": target.marker_checksum,
                "marker_payload_json": target.marker_payload_json,
                "ownership_payload_json": target.ownership_payload_json,
                "probe_token": target.probe_token,
            }
            for target in targets
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _deserialize_prepared_targets(payload: str) -> tuple[PreparedWritableEndpoint, ...]:
    try:
        data: Any = json.loads(payload)
        if not isinstance(data, list):
            raise ValueError
        targets = tuple(_prepared_target(item) for item in data)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _registration_error(
            "WRITABLE_ENDPOINT_REGISTRATION_INTENT_INVALID",
            "Restore the registration intent from a verified state backup.",
            retryable=False,
        ) from exc
    if not targets or len({target.target_ordinal for target in targets}) != len(targets):
        raise _registration_error(
            "WRITABLE_ENDPOINT_REGISTRATION_INTENT_INVALID",
            "Restore the registration intent from a verified state backup.",
            retryable=False,
        )
    return targets


def _prepared_target(item: object) -> PreparedWritableEndpoint:
    if not isinstance(item, dict):
        raise ValueError
    return PreparedWritableEndpoint(
        target_ordinal=_required_int(item["target_ordinal"]),
        endpoint_id=_required_str(item["endpoint_id"]),
        source_endpoint_revision_id=_required_str(item["source_endpoint_revision_id"]),
        resulting_endpoint_revision_id=_required_str(item["resulting_endpoint_revision_id"]),
        resulting_endpoint_generation=_required_int(item["resulting_endpoint_generation"]),
        display_name=_required_str(item["display_name"]),
        root_uri=_required_str(item["root_uri"]),
        control_area_id=_required_str(item["control_area_id"]),
        owner_installation_id=_required_str(item["owner_installation_id"]),
        ownership_epoch=_required_int(item["ownership_epoch"]),
        root_identity_hash_algorithm=_required_str(item["root_identity_hash_algorithm"]),
        root_identity_hash=_required_str(item["root_identity_hash"]),
        marker_checksum_algorithm=_required_str(item["marker_checksum_algorithm"]),
        marker_checksum=_required_str(item["marker_checksum"]),
        marker_payload_json=_required_str(item["marker_payload_json"]),
        ownership_payload_json=_required_str(item["ownership_payload_json"]),
        probe_token=_required_str(item["probe_token"]),
    )


def _required_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError
    return value


def _persistence_error() -> WritableEndpointRegistrationError:
    return _registration_error(
        "WRITABLE_ENDPOINT_REGISTRATION_PERSISTENCE_FAILED",
        "Retry target registration after catalog storage is writable.",
        retryable=True,
    )


def _registration_error(
    code: str,
    next_action: str,
    *,
    retryable: bool,
) -> WritableEndpointRegistrationError:
    return WritableEndpointRegistrationError(
        code,
        next_action,
        retryable=retryable,
    )
