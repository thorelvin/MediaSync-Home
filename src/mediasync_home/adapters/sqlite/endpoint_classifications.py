from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable
from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointClassificationError,
)
from mediasync_home.adapters.sqlite.endpoint_roots import local_path_from_file_uri
from mediasync_home.application.endpoint_classification import (
    EndpointControlAreaClassification,
    EndpointControlAreaClassifier,
)
from mediasync_home.application.endpoint_registration import (
    EndpointClassificationRefreshReport,
    decide_endpoint_registration,
)
from mediasync_home.application.job_endpoints import (
    EndpointRegistrationState,
    JobEndpointRole,
)


class SqliteEndpointClassificationRefreshError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _RegisteredEndpointRevision:
    endpoint_id: str
    endpoint_revision_id: str
    root_uri: str


@dataclass(frozen=True, slots=True)
class _ClassificationCandidate:
    endpoint: _RegisteredEndpointRevision
    classification: EndpointControlAreaClassification | None
    error_code: str | None = None
    next_action: str | None = None


class SqliteEndpointClassificationRefresher:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        classifier: EndpointControlAreaClassifier,
        local_installation_id: str,
    ) -> None:
        self._connection = connection
        self._classifier = classifier
        self._local_installation_id = local_installation_id

    def refresh_endpoint_classifications(
        self,
        *,
        observed_utc: str,
    ) -> EndpointClassificationRefreshReport:
        if self._connection.in_transaction:
            raise SqliteEndpointClassificationRefreshError(
                "ENDPOINT_CLASSIFICATION_REFRESH_REQUIRES_IDLE_CONNECTION"
            )
        endpoints = self._registered_endpoint_revisions()
        candidates = tuple(self._classify(endpoint) for endpoint in endpoints)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                self._persist_observation(candidate, observed_utc=observed_utc)
                self._update_binding_states(candidate)
            report = self._build_report(candidates)
            self._connection.execute("COMMIT")
            return report
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteEndpointClassificationRefreshError(
                "ENDPOINT_CLASSIFICATION_REFRESH_PERSISTENCE_FAILED"
            ) from exc

    def _registered_endpoint_revisions(
        self,
    ) -> tuple[_RegisteredEndpointRevision, ...]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT
                bindings.endpoint_id,
                bindings.endpoint_revision_id,
                revisions.root_uri
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            ORDER BY bindings.endpoint_id, bindings.endpoint_revision_id
            """
        ).fetchall()
        return tuple(
            _RegisteredEndpointRevision(
                endpoint_id=str(row[0]),
                endpoint_revision_id=str(row[1]),
                root_uri=str(row[2]),
            )
            for row in rows
        )

    def _classify(
        self,
        endpoint: _RegisteredEndpointRevision,
    ) -> _ClassificationCandidate:
        try:
            root = local_path_from_file_uri(endpoint.root_uri)
            classification = self._classifier.classify_control_area(
                root,
                local_installation_id=self._local_installation_id,
            )
        except (EndpointLeaseUnavailable, LocalEndpointClassificationError) as exc:
            return _ClassificationCandidate(
                endpoint=endpoint,
                classification=None,
                error_code=exc.validation_code,
                next_action=exc.next_action,
            )
        except OSError:
            return _ClassificationCandidate(
                endpoint=endpoint,
                classification=None,
                error_code="ENDPOINT_CLASSIFICATION_IO_FAILED",
                next_action=(
                    "Reconnect the local endpoint and retry read-only classification."
                ),
            )
        return _ClassificationCandidate(
            endpoint=endpoint,
            classification=classification,
        )

    def _persist_observation(
        self,
        candidate: _ClassificationCandidate,
        *,
        observed_utc: str,
    ) -> None:
        classification = candidate.classification
        reason_codes = () if classification is None else classification.reason_codes
        marker = None if classification is None else classification.marker
        self._connection.execute(
            """
            INSERT INTO endpoint_classification_observations (
                endpoint_id,
                endpoint_revision_id,
                local_installation_id,
                inspection_status,
                classification_state,
                reason_codes_json,
                marker_json,
                error_code,
                next_action,
                observed_utc,
                row_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT (endpoint_id, endpoint_revision_id)
            DO UPDATE SET
                local_installation_id = excluded.local_installation_id,
                inspection_status = excluded.inspection_status,
                classification_state = excluded.classification_state,
                reason_codes_json = excluded.reason_codes_json,
                marker_json = excluded.marker_json,
                error_code = excluded.error_code,
                next_action = excluded.next_action,
                observed_utc = excluded.observed_utc,
                row_version = endpoint_classification_observations.row_version + 1
            """,
            (
                candidate.endpoint.endpoint_id,
                candidate.endpoint.endpoint_revision_id,
                self._local_installation_id,
                "FAILED" if classification is None else "CLASSIFIED",
                None if classification is None else classification.state.value,
                _canonical_json(list(reason_codes)),
                None if marker is None else _canonical_json(marker.to_dict()),
                candidate.error_code,
                candidate.next_action,
                observed_utc,
            ),
        )

    def _update_binding_states(
        self,
        candidate: _ClassificationCandidate,
    ) -> None:
        endpoint = candidate.endpoint
        classification = candidate.classification
        if classification is None:
            self._connection.execute(
                """
                UPDATE standard_backup_job_endpoint_bindings
                SET
                    registration_state = ?,
                    registration_reason_code = ?
                WHERE endpoint_id = ?
                    AND endpoint_revision_id = ?
                """,
                (
                    EndpointRegistrationState.BLOCKED.value,
                    candidate.error_code or "ENDPOINT_CLASSIFICATION_FAILED",
                    endpoint.endpoint_id,
                    endpoint.endpoint_revision_id,
                ),
            )
            return
        for role in JobEndpointRole:
            decision = decide_endpoint_registration(
                role=role,
                expected_endpoint_id=endpoint.endpoint_id,
                classification=classification,
            )
            self._connection.execute(
                """
                UPDATE standard_backup_job_endpoint_bindings
                SET
                    registration_state = ?,
                    registration_reason_code = ?
                WHERE endpoint_id = ?
                    AND endpoint_revision_id = ?
                    AND role = ?
                """,
                (
                    decision.state.value,
                    decision.reason_code,
                    endpoint.endpoint_id,
                    endpoint.endpoint_revision_id,
                    role.value,
                ),
            )

    def _build_report(
        self,
        candidates: tuple[_ClassificationCandidate, ...],
    ) -> EndpointClassificationRefreshReport:
        counts = {
            EndpointRegistrationState(str(row[0])): int(row[1])
            for row in self._connection.execute(
                """
                SELECT registration_state, count(*)
                FROM standard_backup_job_endpoint_bindings
                GROUP BY registration_state
                """
            ).fetchall()
        }
        return EndpointClassificationRefreshReport(
            classified_endpoint_count=sum(
                candidate.classification is not None for candidate in candidates
            ),
            failed_endpoint_count=sum(
                candidate.classification is None for candidate in candidates
            ),
            pending_binding_count=counts.get(
                EndpointRegistrationState.REGISTRATION_PENDING,
                0,
            ),
            read_only_ready_binding_count=counts.get(
                EndpointRegistrationState.READ_ONLY_READY,
                0,
            ),
            writable_ready_binding_count=counts.get(
                EndpointRegistrationState.WRITABLE_READY,
                0,
            ),
            blocked_binding_count=counts.get(
                EndpointRegistrationState.BLOCKED,
                0,
            ),
        )


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
