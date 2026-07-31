from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanIdFactory,
    InitialBackupPlanMaterializationResult,
    InitialBackupPlanRefreshReport,
    InitialBackupPlanningEndpoint,
    InitialBackupPlanningError,
    build_initial_backup_plan,
    endpoint_capabilities_hash,
    initial_backup_plan_runnable,
)
from mediasync_home.application.hash_evidence import (
    CurrentReadHashEvidence,
    HashEvidenceKind,
)
from mediasync_home.application.plans import PlanEndpointRole
from mediasync_home.application.snapshots import SnapshotFileEntry
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore


class SqliteInitialBackupPlanMaterializationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _JobCandidate:
    job_id: str
    job_revision_id: str
    analysis_id: str | None
    snapshot_state: str | None
    snapshot_reason_code: str | None


class SqliteInitialBackupPlanMaterializer:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        plans: SqlitePlanStore,
        id_factory: InitialBackupPlanIdFactory,
    ) -> None:
        self._connection = connection
        self._plans = plans
        self._id_factory = id_factory

    def refresh_initial_backup_plans(
        self,
        *,
        observed_utc: str,
        job_id: str | None = None,
        force: bool = False,
    ) -> InitialBackupPlanRefreshReport:
        if self._connection.in_transaction:
            raise SqliteInitialBackupPlanMaterializationError(
                "INITIAL_BACKUP_PLAN_REFRESH_REQUIRES_IDLE_CONNECTION"
            )
        candidates = self._active_candidates()
        if job_id is not None:
            candidates = tuple(
                candidate for candidate in candidates if candidate.job_id == job_id
            )
        results = tuple(
            self._refresh_candidate(
                candidate,
                observed_utc=observed_utc,
                force=force,
            )
            for candidate in candidates
        )
        return InitialBackupPlanRefreshReport(
            sealed_plan_count=sum(
                result.state == "SEALED" and not result.idempotent_replay
                for result in results
            ),
            reused_plan_count=sum(
                result.state == "SEALED" and result.idempotent_replay
                for result in results
            ),
            no_changes_count=sum(result.state == "NO_CHANGES" for result in results),
            blocked_job_count=sum(result.state == "BLOCKED" for result in results),
            failed_job_count=sum(result.state == "FAILED" for result in results),
            results=results,
        )

    def _active_candidates(self) -> tuple[_JobCandidate, ...]:
        rows = self._connection.execute(
            """
            SELECT
                heads.job_id,
                heads.active_revision_id,
                snapshots.analysis_id,
                snapshots.state,
                snapshots.reason_code
            FROM job_heads AS heads
            INNER JOIN jobs
                ON jobs.id = heads.job_id
                AND jobs.kind = 'multi_target_backup'
            LEFT JOIN standard_backup_job_snapshot_materializations AS snapshots
                ON snapshots.job_id = heads.job_id
                AND snapshots.job_revision_id = heads.active_revision_id
            ORDER BY heads.job_id
            """
        ).fetchall()
        return tuple(
            _JobCandidate(
                job_id=str(row[0]),
                job_revision_id=str(row[1]),
                analysis_id=None if row[2] is None else str(row[2]),
                snapshot_state=None if row[3] is None else str(row[3]),
                snapshot_reason_code=None if row[4] is None else str(row[4]),
            )
            for row in rows
        )

    def _refresh_candidate(
        self,
        candidate: _JobCandidate,
        *,
        observed_utc: str,
        force: bool,
    ) -> InitialBackupPlanMaterializationResult:
        existing = (
            None
            if force
            else self._load_terminal_result(
                job_id=candidate.job_id,
                job_revision_id=candidate.job_revision_id,
            )
        )
        if existing is not None:
            return existing
        if candidate.snapshot_state != "SEALED" or candidate.analysis_id is None:
            reason = candidate.snapshot_reason_code or "INITIAL_BACKUP_PLAN_SNAPSHOTS_NOT_READY"
            return self._save_nonplan_result(
                candidate,
                state="BLOCKED",
                reason_code=reason,
                next_action="Complete sealed source and target snapshots before planning changes.",
                observed_utc=observed_utc,
            )
        try:
            endpoints = self._load_planning_endpoints(candidate)
            build = build_initial_backup_plan(
                plan_id=self._id_factory.new_initial_backup_plan_id(),
                analysis_id=candidate.analysis_id,
                job_id=candidate.job_id,
                job_revision_id=candidate.job_revision_id,
                endpoints=endpoints,
            )
        except InitialBackupPlanningError as exc:
            return self._save_nonplan_result(
                candidate,
                state="BLOCKED",
                reason_code=exc.validation_code,
                next_action=exc.next_action,
                observed_utc=observed_utc,
            )
        except (sqlite3.Error, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._save_nonplan_result(
                candidate,
                state="FAILED",
                reason_code="INITIAL_BACKUP_PLAN_BUILD_FAILED",
                next_action="Refresh the sealed snapshots and retry initial planning.",
                observed_utc=observed_utc,
            )
        if build.plan is None:
            return self._save_nonplan_result(
                candidate,
                state=build.state,
                reason_code=build.reason_code,
                next_action=build.next_action,
                observed_utc=observed_utc,
            )

        plan = build.plan
        plan_runnable = initial_backup_plan_runnable(plan)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._revalidate_active_candidate(candidate)
            self._plans.save_sealed_plan(plan)
            self._connection.execute(
                """
                INSERT INTO initial_backup_plan_materializations (
                    materialization_id,
                    job_id,
                    job_revision_id,
                    analysis_id,
                    plan_id,
                    state,
                    reason_code,
                    operation_count,
                    planned_bytes,
                    plan_runnable,
                    next_action,
                    started_utc,
                    completed_utc
                )
                VALUES (?, ?, ?, ?, ?, 'SEALED', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _materialization_id(
                        candidate,
                        analysis_id=candidate.analysis_id,
                        plan_id=plan.plan_id,
                        state="SEALED",
                        reason_code=build.reason_code,
                        observed_utc=observed_utc,
                    ),
                    candidate.job_id,
                    candidate.job_revision_id,
                    candidate.analysis_id,
                    plan.plan_id,
                    build.reason_code,
                    plan.operation_count,
                    plan.planned_bytes,
                    int(plan_runnable),
                    build.next_action,
                    observed_utc,
                    observed_utc,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError:
            self._rollback()
            replay = self._load_terminal_result(
                job_id=candidate.job_id,
                job_revision_id=candidate.job_revision_id,
            )
            if replay is not None:
                return replay
            return self._save_nonplan_result(
                candidate,
                state="FAILED",
                reason_code="INITIAL_BACKUP_PLAN_PERSISTENCE_CONFLICT",
                next_action="Refresh the active job revision before retrying planning.",
                observed_utc=observed_utc,
            )
        except (sqlite3.Error, ValueError):
            self._rollback()
            return self._save_nonplan_result(
                candidate,
                state="FAILED",
                reason_code="INITIAL_BACKUP_PLAN_PERSISTENCE_FAILED",
                next_action="Retry planning after catalog storage is writable.",
                observed_utc=observed_utc,
            )
        return self._result_from_plan(
            job_id=candidate.job_id,
            job_revision_id=candidate.job_revision_id,
            analysis_id=candidate.analysis_id,
            plan_id=plan.plan_id,
            plan_checksum=plan.plan_checksum,
            state="SEALED",
            reason_code=build.reason_code,
            operation_count=plan.operation_count,
            planned_bytes=plan.planned_bytes,
            plan_runnable=plan_runnable,
            idempotent_replay=False,
            next_action=build.next_action,
        )

    def _load_planning_endpoints(
        self,
        candidate: _JobCandidate,
    ) -> tuple[InitialBackupPlanningEndpoint, ...]:
        if candidate.analysis_id is None:
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_ANALYSIS_MISSING",
                "Refresh sealed snapshots before planning changes.",
            )
        rows = self._connection.execute(
            """
            SELECT
                bindings.role,
                bindings.ordinal,
                bindings.endpoint_id,
                bindings.endpoint_revision_id,
                bindings.registration_state,
                revisions.generation,
                revisions.owner_installation_id,
                revisions.ownership_epoch,
                revisions.control_marker_checksum_algorithm,
                revisions.control_marker_checksum,
                snapshots.id,
                snapshots.snapshot_checksum,
                snapshots.immutable,
                snapshots.complete,
                root_coverage.case_context_hash,
                root_coverage.case_mode,
                EXISTS (
                    SELECT 1
                    FROM directory_coverage AS other_coverage
                    WHERE other_coverage.snapshot_id = snapshots.id
                        AND other_coverage.case_mode <> root_coverage.case_mode
                ),
                observations.classification_state,
                observations.marker_json,
                registrations.marker_checksum
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN snapshots
                ON snapshots.analysis_id = ?
                AND snapshots.endpoint_id = bindings.endpoint_id
                AND snapshots.endpoint_revision_id = bindings.endpoint_revision_id
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
                AND revisions.generation = snapshots.endpoint_generation
            INNER JOIN directory_coverage AS root_coverage
                ON root_coverage.snapshot_id = snapshots.id
                AND root_coverage.relative_path = '.'
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = bindings.endpoint_id
                AND observations.endpoint_revision_id = bindings.endpoint_revision_id
            LEFT JOIN writable_endpoint_registrations AS registrations
                ON registrations.endpoint_id = bindings.endpoint_id
                AND registrations.endpoint_revision_id = bindings.endpoint_revision_id
            WHERE bindings.job_id = ?
                AND bindings.job_revision_id = ?
            ORDER BY
                CASE bindings.role WHEN 'SOURCE' THEN 0 ELSE 1 END,
                bindings.ordinal
            """,
            (
                candidate.analysis_id,
                candidate.job_id,
                candidate.job_revision_id,
            ),
        ).fetchall()
        if not rows:
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_ENDPOINTS_MISSING",
                "Refresh endpoint bindings and sealed snapshots before planning.",
            )
        endpoints = tuple(self._planning_endpoint(row) for row in rows)
        if sum(endpoint.role is PlanEndpointRole.SOURCE for endpoint in endpoints) != 1:
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_REQUIRES_SINGLE_SOURCE",
                "Refresh the source endpoint binding before planning.",
            )
        if any(
            endpoint.role is PlanEndpointRole.TARGET_WRITABLE
            and endpoint.required_owner_installation_id is None
            for endpoint in endpoints
        ):
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_TARGET_REGISTRATION_EVIDENCE_MISSING",
                "Register every writable target before planning changes.",
            )
        return endpoints

    def _planning_endpoint(
        self,
        row: sqlite3.Row | tuple[object, ...],
    ) -> InitialBackupPlanningEndpoint:
        role = str(row[0])
        registration_state = str(row[4])
        snapshot_checksum = "" if row[11] is None else str(row[11])
        root_case_context_hash = str(row[14])
        root_case_mode = str(row[15])
        if not bool(row[12]) or not bool(row[13]) or len(snapshot_checksum) != 64:
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_SNAPSHOT_NOT_SEALED",
                "Seal complete endpoint snapshots before planning changes.",
            )
        snapshot_id = str(row[10])
        entries = self._load_snapshot_entries(snapshot_id)
        hash_evidence = self._load_current_read_hash_evidence(snapshot_id)
        if role == "SOURCE":
            if registration_state != "READ_ONLY_READY":
                raise InitialBackupPlanningError(
                    "INITIAL_BACKUP_PLAN_SOURCE_NOT_READY",
                    "Refresh source endpoint classification before planning.",
                )
            capabilities_hash = endpoint_capabilities_hash(
                {
                    "mode": "LOCAL_READ_ONLY_SNAPSHOT",
                    "snapshot_checksum": snapshot_checksum,
                    "snapshot_schema_version": 1,
                }
            )
            return InitialBackupPlanningEndpoint(
                endpoint_id=str(row[2]),
                endpoint_revision_id=str(row[3]),
                endpoint_generation=_catalog_int(row[5], minimum=1),
                snapshot_id=snapshot_id,
                snapshot_checksum=snapshot_checksum,
                root_case_context_hash=root_case_context_hash,
                root_case_mode=root_case_mode,
                capabilities_hash=capabilities_hash,
                entries=entries,
                role=PlanEndpointRole.SOURCE,
                hash_evidence=hash_evidence,
            )
        if role != "TARGET":
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_ENDPOINT_ROLE_INVALID",
                "Refresh the backup endpoint bindings before planning.",
            )
        if bool(row[16]):
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_TARGET_MIXED_CASE_CONTEXT_UNSUPPORTED",
                "Use a target with one known case mode until mixed case planning is available.",
            )
        marker = _json_object(row[18])
        control_schema_version = _positive_int(marker.get("control_schema_version"))
        marker_checksum = None if row[9] is None else str(row[9])
        registration_marker_checksum = None if row[19] is None else str(row[19])
        if (
            registration_state != "WRITABLE_READY"
            or str(row[17]) != "VALID_OWNED"
            or row[6] is None
            or row[7] is None
            or marker_checksum is None
            or marker_checksum != registration_marker_checksum
            or marker.get("marker_checksum") != marker_checksum
        ):
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_TARGET_NOT_WRITABLE",
                "Complete target registration and classification before planning.",
            )
        capabilities_hash = endpoint_capabilities_hash(
            {
                "control_schema_version": control_schema_version,
                "directory_create": "NO_OVERWRITE_JOURNALED_REQUIRED",
                "local_endpoint_lock": "WIN32_HANDLE",
                "marker_checksum": marker_checksum,
                "mode": "LOCAL_WRITABLE_PREVIEW",
                "replace": "VERSIONED_COMPARE_AND_SWAP",
                "snapshot_checksum": snapshot_checksum,
            }
        )
        return InitialBackupPlanningEndpoint(
            endpoint_id=str(row[2]),
            endpoint_revision_id=str(row[3]),
            endpoint_generation=_catalog_int(row[5], minimum=1),
            snapshot_id=snapshot_id,
            snapshot_checksum=snapshot_checksum,
            root_case_context_hash=root_case_context_hash,
            root_case_mode=root_case_mode,
            capabilities_hash=capabilities_hash,
            entries=entries,
            role=PlanEndpointRole.TARGET_WRITABLE,
            hash_evidence=hash_evidence,
            target_ordinal=_catalog_int(row[1], minimum=0),
            required_owner_installation_id=str(row[6]),
            required_ownership_epoch=_catalog_int(row[7], minimum=1),
            control_schema_version=control_schema_version,
        )

    def _load_snapshot_entries(
        self,
        snapshot_id: str,
    ) -> tuple[SnapshotFileEntry, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                relative_path,
                comparison_key,
                object_type,
                size_bytes,
                identity_fingerprint_hash
            FROM file_entries
            WHERE snapshot_id = ?
            ORDER BY comparison_key, relative_path, id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SnapshotFileEntry(
                entry_id=str(row[0]),
                relative_path=str(row[1]),
                comparison_key=str(row[2]),
                object_type=str(row[3]),
                size_bytes=None if row[4] is None else int(row[4]),
                identity_fingerprint_hash=None if row[5] is None else str(row[5]),
            )
            for row in rows
        )

    def _load_current_read_hash_evidence(
        self,
        snapshot_id: str,
    ) -> tuple[CurrentReadHashEvidence, ...]:
        rows = self._connection.execute(
            """
            SELECT
                snapshot_id,
                entry_id,
                endpoint_id,
                content_hash,
                size_bytes,
                algorithm,
                hash_schema_version,
                evidence_kind,
                read_started_fingerprint_hash,
                read_completed_fingerprint_hash,
                computed_utc
            FROM current_read_hash_evidence
            WHERE snapshot_id = ?
            ORDER BY entry_id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            CurrentReadHashEvidence(
                snapshot_id=str(row[0]),
                entry_id=str(row[1]),
                endpoint_id=str(row[2]),
                content_hash=str(row[3]),
                size_bytes=_catalog_int(row[4], minimum=0),
                algorithm=str(row[5]),
                hash_schema_version=_catalog_int(row[6], minimum=1),
                evidence_kind=HashEvidenceKind(str(row[7])),
                read_started_fingerprint_hash=str(row[8]),
                read_completed_fingerprint_hash=str(row[9]),
                computed_utc=str(row[10]),
            )
            for row in rows
        )

    def _revalidate_active_candidate(self, candidate: _JobCandidate) -> None:
        row = self._connection.execute(
            """
            SELECT
                heads.active_revision_id,
                snapshots.analysis_id,
                snapshots.state
            FROM job_heads AS heads
            LEFT JOIN standard_backup_job_snapshot_materializations AS snapshots
                ON snapshots.job_id = heads.job_id
                AND snapshots.job_revision_id = heads.active_revision_id
            WHERE heads.job_id = ?
            """,
            (candidate.job_id,),
        ).fetchone()
        if (
            row is None
            or str(row[0]) != candidate.job_revision_id
            or str(row[1]) != candidate.analysis_id
            or str(row[2]) != "SEALED"
        ):
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_CANDIDATE_CHANGED",
                "Refresh the active job and snapshots before retrying planning.",
            )

    def _save_nonplan_result(
        self,
        candidate: _JobCandidate,
        *,
        state: str,
        reason_code: str,
        next_action: str,
        observed_utc: str,
    ) -> InitialBackupPlanMaterializationResult:
        if state not in {"NO_CHANGES", "BLOCKED", "FAILED"}:
            raise ValueError("initial backup non-plan result state is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                INSERT INTO initial_backup_plan_materializations (
                    materialization_id,
                    job_id,
                    job_revision_id,
                    analysis_id,
                    plan_id,
                    state,
                    reason_code,
                    operation_count,
                    planned_bytes,
                    plan_runnable,
                    next_action,
                    started_utc,
                    completed_utc
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?, 0, 0, 0, ?, ?, ?)
                """,
                (
                    _materialization_id(
                        candidate,
                        analysis_id=candidate.analysis_id,
                        plan_id=None,
                        state=state,
                        reason_code=reason_code,
                        observed_utc=observed_utc,
                    ),
                    candidate.job_id,
                    candidate.job_revision_id,
                    candidate.analysis_id,
                    state,
                    reason_code,
                    next_action,
                    observed_utc,
                    observed_utc,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback()
            raise SqliteInitialBackupPlanMaterializationError(
                "INITIAL_BACKUP_PLAN_RESULT_PERSISTENCE_FAILED"
            ) from exc
        return self._result_from_plan(
            job_id=candidate.job_id,
            job_revision_id=candidate.job_revision_id,
            analysis_id=candidate.analysis_id,
            plan_id=None,
            plan_checksum=None,
            state=state,
            reason_code=reason_code,
            operation_count=0,
            planned_bytes=0,
            plan_runnable=False,
            idempotent_replay=False,
            next_action=next_action,
        )

    def _load_terminal_result(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> InitialBackupPlanMaterializationResult | None:
        row = self._connection.execute(
            """
            SELECT
                materializations.analysis_id,
                materializations.plan_id,
                seals.plan_checksum,
                materializations.state,
                materializations.reason_code,
                materializations.operation_count,
                materializations.planned_bytes,
                materializations.plan_runnable,
                materializations.next_action
            FROM initial_backup_plan_materializations AS materializations
            LEFT JOIN plan_seal_details AS seals
                ON seals.plan_id = materializations.plan_id
            WHERE materializations.job_id = ?
                AND materializations.job_revision_id = ?
                AND materializations.state IN ('SEALED', 'NO_CHANGES')
            ORDER BY
                materializations.completed_utc DESC,
                materializations.materialization_id DESC
            LIMIT 1
            """,
            (job_id, job_revision_id),
        ).fetchone()
        if row is None:
            return None
        state = str(row[3])
        plan_id = None if row[1] is None else str(row[1])
        plan_checksum = None if row[2] is None else str(row[2])
        if state == "SEALED" and (plan_id is None or plan_checksum is None):
            raise SqliteInitialBackupPlanMaterializationError(
                "INITIAL_BACKUP_PLAN_REUSE_INVARIANT_FAILED"
            )
        return self._result_from_plan(
            job_id=job_id,
            job_revision_id=job_revision_id,
            analysis_id=None if row[0] is None else str(row[0]),
            plan_id=plan_id,
            plan_checksum=plan_checksum,
            state=state,
            reason_code=str(row[4]),
            operation_count=int(row[5]),
            planned_bytes=int(row[6]),
            plan_runnable=bool(row[7]),
            idempotent_replay=True,
            next_action=str(row[8]),
        )

    @staticmethod
    def _result_from_plan(
        *,
        job_id: str,
        job_revision_id: str,
        analysis_id: str | None,
        plan_id: str | None,
        plan_checksum: str | None,
        state: str,
        reason_code: str,
        operation_count: int,
        planned_bytes: int,
        plan_runnable: bool,
        idempotent_replay: bool,
        next_action: str,
    ) -> InitialBackupPlanMaterializationResult:
        return InitialBackupPlanMaterializationResult(
            job_id=job_id,
            job_revision_id=job_revision_id,
            analysis_id=analysis_id,
            plan_id=plan_id,
            plan_checksum=plan_checksum,
            state=state,
            reason_code=reason_code,
            operation_count=operation_count,
            planned_bytes=planned_bytes,
            plan_runnable=plan_runnable,
            idempotent_replay=idempotent_replay,
            next_action=next_action,
        )

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_MARKER_EVIDENCE_MISSING",
            "Refresh target marker classification before planning.",
        )
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_MARKER_EVIDENCE_INVALID",
            "Refresh target marker classification before planning.",
        )
    return {str(key): item for key, item in payload.items()}


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_CONTROL_SCHEMA_INVALID",
            "Refresh target marker classification before planning.",
        )
    return value


def _catalog_int(value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_CATALOG_EVIDENCE_INVALID",
            "Refresh catalog endpoint evidence before planning changes.",
        )
    return value


def _materialization_id(
    candidate: _JobCandidate,
    *,
    analysis_id: str | None,
    plan_id: str | None,
    state: str,
    reason_code: str,
    observed_utc: str,
) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (
                candidate.job_id,
                candidate.job_revision_id,
                analysis_id or "",
                plan_id or "",
                state,
                reason_code,
                observed_utc,
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"plan-materialization:{digest[:32]}"
