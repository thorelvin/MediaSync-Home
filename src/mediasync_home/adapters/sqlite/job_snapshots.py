from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable
from mediasync_home.adapters.local_snapshot_scanner import LocalSnapshotScanError
from mediasync_home.adapters.sqlite.endpoint_roots import local_path_from_file_uri
from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteFailureKind,
    classify_sqlite_exception,
)
from mediasync_home.application.endpoint_classification import (
    EXCLUDABLE_CONTROL_AREA_STATES,
    EndpointControlAreaState,
)
from mediasync_home.application.file_filters import (
    FileFilterPolicy,
    FileFilterPolicyError,
    parse_file_filter_policy_json,
)
from mediasync_home.application.snapshot_scanning import (
    FilesystemSnapshotScan,
    FilesystemSnapshotScanner,
    JobSnapshotMaterializationResult,
    SnapshotMaterializationIdFactory,
    SnapshotMaterializationIds,
    SnapshotMaterializationRefreshReport,
    SnapshotScanBatchSink,
)
from mediasync_home.application.state_capacity import (
    StateCapacityGate,
    snapshot_analysis_growth_estimate,
)
from mediasync_home.application.snapshots import (
    SnapshotEntryMaterializationStore,
    SnapshotFileEntry,
    SnapshotFilterDecision,
    SnapshotSealRequest,
    SnapshotSealStore,
    snapshot_entry_batch,
)


MAX_SNAPSHOT_BATCH_ENTRIES = 1000
_CLASSIFIED_READABLE_STATES = frozenset(
    {EndpointControlAreaState.ABSENT, *EXCLUDABLE_CONTROL_AREA_STATES}
)
_SOURCE_READABLE_REGISTRATION_STATES = frozenset({"READ_ONLY_READY"})
_TARGET_READABLE_REGISTRATION_STATES = frozenset(
    {"REGISTRATION_PENDING", "READ_ONLY_READY", "WRITABLE_READY"}
)


class SqliteJobSnapshotMaterializationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _SnapshotEndpointCandidate:
    role: str
    ordinal: int
    endpoint_id: str
    endpoint_revision_id: str
    endpoint_generation: int
    root_uri: str
    registration_state: str
    inspection_status: str | None
    classification_state: str | None
    marker_json: str | None


@dataclass(frozen=True, slots=True)
class _SnapshotJobCandidate:
    job_id: str
    job_revision_id: str
    filter_rules_hash: str
    filter_rules_json: str
    endpoints: tuple[_SnapshotEndpointCandidate, ...]


@dataclass(frozen=True, slots=True)
class _ScannedEndpoint:
    endpoint: _SnapshotEndpointCandidate
    scan: FilesystemSnapshotScan


class _SqliteSnapshotScanBatchSink(SnapshotScanBatchSink):
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        entry_store: SnapshotEntryMaterializationStore,
        snapshot_id: str,
    ) -> None:
        self._connection = connection
        self._entry_store = entry_store
        self._snapshot_id = snapshot_id
        self._batch_count = 0
        self._checkpoint_serial = 0

    @property
    def batch_count(self) -> int:
        return self._batch_count

    def checkpoint(self) -> object:
        self._checkpoint_serial += 1
        name = f"snapshot_scan_{self._checkpoint_serial}"
        self._connection.execute(f"SAVEPOINT {name}")
        return (name, self._batch_count)

    def emit(
        self,
        *,
        entries: tuple[SnapshotFileEntry, ...],
        filter_decisions: tuple[SnapshotFilterDecision, ...],
    ) -> None:
        self._entry_store.commit_snapshot_entry_batch(
            snapshot_entry_batch(
                snapshot_id=self._snapshot_id,
                sequence_no=self._batch_count,
                entries=entries,
                filter_decisions=filter_decisions,
            )
        )
        self._batch_count += 1

    def accept(self, checkpoint: object) -> None:
        name, _ = _scan_sink_checkpoint(checkpoint)
        self._connection.execute(f"RELEASE SAVEPOINT {name}")

    def rollback(self, checkpoint: object) -> None:
        name, batch_count = _scan_sink_checkpoint(checkpoint)
        self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        self._connection.execute(f"RELEASE SAVEPOINT {name}")
        self._batch_count = batch_count


class SqliteJobSnapshotMaterializer:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        scanner: FilesystemSnapshotScanner,
        id_factory: SnapshotMaterializationIdFactory,
        entry_store: SnapshotEntryMaterializationStore,
        seal_store: SnapshotSealStore,
        capacity_gate: StateCapacityGate | None = None,
    ) -> None:
        self._connection = connection
        self._scanner = scanner
        self._id_factory = id_factory
        self._entry_store = entry_store
        self._seal_store = seal_store
        self._capacity_gate = capacity_gate

    def refresh_job_snapshots(
        self,
        *,
        observed_utc: str,
        job_id: str | None = None,
        force: bool = False,
    ) -> SnapshotMaterializationRefreshReport:
        if self._connection.in_transaction:
            raise SqliteJobSnapshotMaterializationError(
                "JOB_SNAPSHOT_REFRESH_REQUIRES_IDLE_CONNECTION"
            )
        results: list[JobSnapshotMaterializationResult] = []
        candidates = self._active_job_candidates()
        if job_id is not None:
            candidates = tuple(
                candidate for candidate in candidates if candidate.job_id == job_id
            )
        for candidate in candidates:
            try:
                filter_policy = parse_file_filter_policy_json(
                    candidate.filter_rules_json,
                    expected_hash=candidate.filter_rules_hash,
                )
            except FileFilterPolicyError as exc:
                persisted_reason = self._persist_blocked_without_analysis(
                    candidate,
                    reason_code=exc.validation_code,
                    observed_utc=observed_utc,
                )
                results.append(
                    _result(
                        candidate,
                        state="BLOCKED",
                        reason_code=persisted_reason,
                    )
                )
                continue
            reused = None if force else self._load_reusable_result(candidate)
            if reused is not None:
                results.append(reused)
                continue
            capacity_reason = self._analysis_capacity_block_reason(
                endpoint_count=len(candidate.endpoints)
            )
            if capacity_reason is not None:
                results.append(
                    _result(
                        candidate,
                        state="BLOCKED",
                        reason_code=capacity_reason,
                    )
                )
                continue
            blocked_reason = _job_scan_precondition_reason(candidate)
            if blocked_reason is not None:
                persisted_reason = self._persist_blocked_without_analysis(
                    candidate,
                    reason_code=blocked_reason,
                    observed_utc=observed_utc,
                )
                results.append(
                    _result(
                        candidate,
                        state="BLOCKED",
                        reason_code=persisted_reason,
                    )
                )
                continue
            results.append(
                self._scan_and_persist_candidate(
                    candidate,
                    filter_policy=filter_policy,
                    observed_utc=observed_utc,
                )
            )
        return _refresh_report(tuple(results))

    def _active_job_candidates(self) -> tuple[_SnapshotJobCandidate, ...]:
        rows = self._connection.execute(
            """
            SELECT
                bindings.job_id,
                bindings.job_revision_id,
                filter_versions.rules_hash,
                filter_versions.rules_json,
                bindings.role,
                bindings.ordinal,
                bindings.endpoint_id,
                bindings.endpoint_revision_id,
                revisions.generation,
                revisions.root_uri,
                bindings.registration_state,
                observations.inspection_status,
                observations.classification_state,
                observations.marker_json
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN job_heads AS heads
                ON heads.job_id = bindings.job_id
                AND heads.active_revision_id = bindings.job_revision_id
            INNER JOIN job_revision_filter_bindings AS filter_bindings
                ON filter_bindings.job_id = bindings.job_id
                AND filter_bindings.job_revision_id = bindings.job_revision_id
            INNER JOIN filter_set_versions AS filter_versions
                ON filter_versions.job_id = filter_bindings.job_id
                AND filter_versions.filter_set_id = filter_bindings.filter_set_id
                AND filter_versions.version = filter_bindings.filter_set_version
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = bindings.endpoint_id
                AND observations.endpoint_revision_id = bindings.endpoint_revision_id
            ORDER BY
                bindings.job_id,
                CASE bindings.role WHEN 'SOURCE' THEN 0 ELSE 1 END,
                bindings.ordinal,
                bindings.endpoint_id
            """
        ).fetchall()
        grouped: list[_SnapshotJobCandidate] = []
        current_key: tuple[str, str] | None = None
        current_filter_evidence: tuple[str, str] | None = None
        endpoints: list[_SnapshotEndpointCandidate] = []
        for row in rows:
            key = (str(row[0]), str(row[1]))
            filter_evidence = (str(row[2]), str(row[3]))
            if current_key is not None and key != current_key:
                if current_filter_evidence is None:
                    raise SqliteJobSnapshotMaterializationError(
                        "JOB_SNAPSHOT_FILTER_EVIDENCE_MISSING"
                    )
                grouped.append(
                    _SnapshotJobCandidate(
                        job_id=current_key[0],
                        job_revision_id=current_key[1],
                        filter_rules_hash=current_filter_evidence[0],
                        filter_rules_json=current_filter_evidence[1],
                        endpoints=tuple(endpoints),
                    )
                )
                endpoints = []
                current_filter_evidence = None
            if current_filter_evidence is not None and (
                filter_evidence != current_filter_evidence
            ):
                raise SqliteJobSnapshotMaterializationError(
                    "JOB_SNAPSHOT_FILTER_EVIDENCE_INCONSISTENT"
                )
            current_key = key
            current_filter_evidence = filter_evidence
            endpoints.append(
                _SnapshotEndpointCandidate(
                    role=str(row[4]),
                    ordinal=int(row[5]),
                    endpoint_id=str(row[6]),
                    endpoint_revision_id=str(row[7]),
                    endpoint_generation=int(row[8]),
                    root_uri=str(row[9]),
                    registration_state=str(row[10]),
                    inspection_status=None if row[11] is None else str(row[11]),
                    classification_state=None if row[12] is None else str(row[12]),
                    marker_json=None if row[13] is None else str(row[13]),
                )
            )
        if current_key is not None:
            if current_filter_evidence is None:
                raise SqliteJobSnapshotMaterializationError(
                    "JOB_SNAPSHOT_FILTER_EVIDENCE_MISSING"
                )
            grouped.append(
                _SnapshotJobCandidate(
                    job_id=current_key[0],
                    job_revision_id=current_key[1],
                    filter_rules_hash=current_filter_evidence[0],
                    filter_rules_json=current_filter_evidence[1],
                    endpoints=tuple(endpoints),
                )
            )
        return tuple(grouped)

    def _load_reusable_result(
        self,
        candidate: _SnapshotJobCandidate,
    ) -> JobSnapshotMaterializationResult | None:
        row = self._connection.execute(
            """
            SELECT
                materialization.analysis_id,
                materialization.snapshot_count,
                materialization.sealed_snapshot_count,
                count(snapshots.id),
                coalesce(sum(snapshots.immutable), 0)
            FROM standard_backup_job_snapshot_materializations AS materialization
            LEFT JOIN snapshots
                ON snapshots.analysis_id = materialization.analysis_id
            WHERE materialization.job_id = ?
                AND materialization.job_revision_id = ?
                AND materialization.state = 'SEALED'
            GROUP BY
                materialization.analysis_id,
                materialization.snapshot_count,
                materialization.sealed_snapshot_count
            """,
            (candidate.job_id, candidate.job_revision_id),
        ).fetchone()
        if row is None:
            return None
        analysis_id = str(row[0])
        expected_snapshot_count = int(row[1])
        if (
            int(row[2]) != expected_snapshot_count
            or int(row[3]) != expected_snapshot_count
            or int(row[4]) != expected_snapshot_count
            or expected_snapshot_count != len(candidate.endpoints)
        ):
            raise SqliteJobSnapshotMaterializationError(
                "JOB_SNAPSHOT_REUSE_INVARIANT_FAILED"
            )
        snapshot_ids = tuple(
            str(item[0])
            for item in self._connection.execute(
                """
                SELECT id
                FROM snapshots
                WHERE analysis_id = ?
                ORDER BY endpoint_id
                """,
                (analysis_id,),
            ).fetchall()
        )
        return _result(
            candidate,
            analysis_id=analysis_id,
            state="REUSED",
            reason_code="JOB_SNAPSHOTS_ALREADY_SEALED",
            snapshot_ids=snapshot_ids,
        )

    def _scan_and_persist_candidate(
        self,
        candidate: _SnapshotJobCandidate,
        *,
        filter_policy: FileFilterPolicy,
        observed_utc: str,
    ) -> JobSnapshotMaterializationResult:
        try:
            ids = self._id_factory.new_snapshot_materialization_ids(
                snapshot_count=len(candidate.endpoints),
            )
            _validate_materialization_ids(
                analysis_id=ids.analysis_id,
                snapshot_ids=ids.snapshot_ids,
                expected_snapshot_count=len(candidate.endpoints),
            )
        except Exception:
            return _result(
                candidate,
                state="FAILED",
                reason_code="JOB_SNAPSHOT_ID_ALLOCATION_FAILED",
            )
        scan_streaming = getattr(self._scanner, "scan_streaming", None)
        if callable(scan_streaming):
            return self._scan_and_persist_streaming_candidate(
                candidate,
                ids=ids,
                filter_policy=filter_policy,
                observed_utc=observed_utc,
                scan_streaming=scan_streaming,
            )
        try:
            scanned = tuple(
                _ScannedEndpoint(
                    endpoint=endpoint,
                    scan=self._scanner.scan(
                        _local_root(endpoint.root_uri),
                        snapshot_id=snapshot_id,
                        exclude_control_area=_exclude_control_area(endpoint),
                        filter_policy=filter_policy,
                    ),
                )
                for endpoint, snapshot_id in zip(
                    candidate.endpoints,
                    ids.snapshot_ids,
                    strict=True,
                )
            )
        except (
            EndpointLeaseUnavailable,
            LocalSnapshotScanError,
            OSError,
        ) as exc:
            reason_code = _scan_failure_reason(exc)
            reason_code = self._persist_blocked_without_analysis(
                candidate,
                reason_code=reason_code,
                observed_utc=observed_utc,
            )
            return _result(
                candidate,
                state="BLOCKED",
                reason_code=reason_code,
            )
        except Exception:
            return _result(
                candidate,
                state="FAILED",
                reason_code="JOB_SNAPSHOT_SCAN_FAILED",
            )
        capacity_reason = self._analysis_capacity_block_reason(
            endpoint_count=len(scanned),
            entry_count=sum(item.scan.entry_count for item in scanned),
            coverage_count=sum(len(item.scan.coverage) for item in scanned),
            issue_count=sum(len(item.scan.issues) for item in scanned),
        )
        if capacity_reason is not None:
            return _result(
                candidate,
                state="BLOCKED",
                reason_code=capacity_reason,
            )
        all_complete = all(item.scan.complete for item in scanned)
        state = "SEALED" if all_complete else "BLOCKED"
        reason_code = (
            "JOB_SNAPSHOTS_SEALED"
            if all_complete
            else "JOB_SNAPSHOT_SCAN_INCOMPLETE"
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._insert_snapshot_parent_rows(
                candidate,
                analysis_id=ids.analysis_id,
                snapshot_ids=ids.snapshot_ids,
            )
            for item in scanned:
                self._commit_scan_batches(item.scan)
            if all_complete:
                for item in scanned:
                    self._seal_scan(item.scan)
            self._upsert_materialization(
                candidate,
                analysis_id=ids.analysis_id,
                state=state,
                reason_code=reason_code,
                snapshot_count=len(scanned),
                sealed_snapshot_count=len(scanned) if all_complete else 0,
                observed_utc=observed_utc,
            )
            self._connection.execute("COMMIT")
        except Exception as exc:
            _rollback(self._connection)
            persistence_reason = self._persistence_failure_reason(
                exc,
                default="JOB_SNAPSHOT_PERSISTENCE_FAILED",
            )
            return _result(
                candidate,
                state=(
                    "BLOCKED"
                    if persistence_reason == "STATE_CAPACITY_SQLITE_FULL"
                    else "FAILED"
                ),
                reason_code=persistence_reason,
            )
        return _result(
            candidate,
            analysis_id=ids.analysis_id,
            state=state,
            reason_code=reason_code,
            snapshot_ids=tuple(item.scan.snapshot_id for item in scanned),
        )

    def _scan_and_persist_streaming_candidate(
        self,
        candidate: _SnapshotJobCandidate,
        *,
        ids: SnapshotMaterializationIds,
        filter_policy: FileFilterPolicy,
        observed_utc: str,
        scan_streaming: Callable[..., FilesystemSnapshotScan],
    ) -> JobSnapshotMaterializationResult:
        analysis_id = ids.analysis_id
        snapshot_ids = ids.snapshot_ids
        scanned: list[_ScannedEndpoint] = []
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._insert_snapshot_parent_rows(
                candidate,
                analysis_id=analysis_id,
                snapshot_ids=snapshot_ids,
            )
            for endpoint, snapshot_id in zip(
                candidate.endpoints,
                snapshot_ids,
                strict=True,
            ):
                sink = _SqliteSnapshotScanBatchSink(
                    connection=self._connection,
                    entry_store=self._entry_store,
                    snapshot_id=snapshot_id,
                )
                scan = scan_streaming(
                    _local_root(endpoint.root_uri),
                    snapshot_id=snapshot_id,
                    exclude_control_area=_exclude_control_area(endpoint),
                    filter_policy=filter_policy,
                    batch_sink=sink,
                )
                if not isinstance(scan, FilesystemSnapshotScan):
                    raise SqliteJobSnapshotMaterializationError(
                        "JOB_SNAPSHOT_STREAM_RESULT_INVALID"
                    )
                if scan.streamed_batch_count != sink.batch_count:
                    raise SqliteJobSnapshotMaterializationError(
                        "JOB_SNAPSHOT_STREAM_BATCH_COUNT_MISMATCH"
                    )
                scanned.append(_ScannedEndpoint(endpoint=endpoint, scan=scan))
                self._commit_scan_batches(scan)

            capacity_reason = self._analysis_capacity_block_reason(
                endpoint_count=len(scanned),
                entry_count=sum(item.scan.entry_count for item in scanned),
                coverage_count=sum(len(item.scan.coverage) for item in scanned),
                issue_count=sum(len(item.scan.issues) for item in scanned),
            )
            if capacity_reason is not None:
                _rollback(self._connection)
                persisted_reason = self._persist_blocked_without_analysis(
                    candidate,
                    reason_code=capacity_reason,
                    observed_utc=observed_utc,
                )
                return _result(
                    candidate,
                    state="BLOCKED",
                    reason_code=persisted_reason,
                )
            all_complete = all(item.scan.complete for item in scanned)
            state = "SEALED" if all_complete else "BLOCKED"
            reason_code = (
                "JOB_SNAPSHOTS_SEALED"
                if all_complete
                else "JOB_SNAPSHOT_SCAN_INCOMPLETE"
            )
            if all_complete:
                for item in scanned:
                    self._seal_scan(item.scan)
            self._upsert_materialization(
                candidate,
                analysis_id=analysis_id,
                state=state,
                reason_code=reason_code,
                snapshot_count=len(scanned),
                sealed_snapshot_count=len(scanned) if all_complete else 0,
                observed_utc=observed_utc,
            )
            self._connection.execute("COMMIT")
        except (EndpointLeaseUnavailable, LocalSnapshotScanError, OSError) as exc:
            _rollback(self._connection)
            reason_code = self._persist_blocked_without_analysis(
                candidate,
                reason_code=_scan_failure_reason(exc),
                observed_utc=observed_utc,
            )
            return _result(candidate, state="BLOCKED", reason_code=reason_code)
        except Exception as exc:
            _rollback(self._connection)
            persistence_reason = self._persistence_failure_reason(
                exc,
                default="JOB_SNAPSHOT_SCAN_OR_PERSISTENCE_FAILED",
            )
            return _result(
                candidate,
                state=(
                    "BLOCKED"
                    if persistence_reason == "STATE_CAPACITY_SQLITE_FULL"
                    else "FAILED"
                ),
                reason_code=persistence_reason,
            )
        return _result(
            candidate,
            analysis_id=analysis_id,
            state=state,
            reason_code=reason_code,
            snapshot_ids=tuple(item.scan.snapshot_id for item in scanned),
        )

    def _insert_snapshot_parent_rows(
        self,
        candidate: _SnapshotJobCandidate,
        *,
        analysis_id: str,
        snapshot_ids: tuple[str, ...],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES (?, ?, ?)
            """,
            (analysis_id, candidate.job_id, candidate.job_revision_id),
        )
        for endpoint, snapshot_id in zip(
            candidate.endpoints,
            snapshot_ids,
            strict=True,
        ):
            self._connection.execute(
                """
                INSERT INTO analysis_targets (
                    analysis_id,
                    endpoint_id,
                    endpoint_revision_id
                )
                VALUES (?, ?, ?)
                """,
                (
                    analysis_id,
                    endpoint.endpoint_id,
                    endpoint.endpoint_revision_id,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO snapshots (
                    id,
                    analysis_id,
                    endpoint_id,
                    endpoint_revision_id,
                    endpoint_generation
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    analysis_id,
                    endpoint.endpoint_id,
                    endpoint.endpoint_revision_id,
                    endpoint.endpoint_generation,
                ),
            )

    def _commit_scan_batches(self, scan: FilesystemSnapshotScan) -> None:
        if scan.streamed_batch_count:
            self._entry_store.commit_snapshot_entry_batch(
                snapshot_entry_batch(
                    snapshot_id=scan.snapshot_id,
                    sequence_no=scan.streamed_batch_count,
                    entries=scan.entries,
                    coverage_updates=scan.coverage,
                    issues=scan.issues,
                    filter_decisions=scan.filter_decisions,
                )
            )
            return
        batch_count = max(
            1,
            _bounded_batch_count(len(scan.entries)),
            _bounded_batch_count(len(scan.filter_decisions)),
        )
        for sequence_no in range(batch_count):
            entry_offset = sequence_no * MAX_SNAPSHOT_BATCH_ENTRIES
            decision_offset = sequence_no * MAX_SNAPSHOT_BATCH_ENTRIES
            self._entry_store.commit_snapshot_entry_batch(
                snapshot_entry_batch(
                    snapshot_id=scan.snapshot_id,
                    sequence_no=sequence_no,
                    entries=scan.entries[
                        entry_offset : entry_offset + MAX_SNAPSHOT_BATCH_ENTRIES
                    ],
                    coverage_updates=scan.coverage if sequence_no == 0 else (),
                    issues=scan.issues if sequence_no == 0 else (),
                    filter_decisions=scan.filter_decisions[
                        decision_offset : decision_offset
                        + MAX_SNAPSHOT_BATCH_ENTRIES
                    ],
                )
            )

    def _seal_scan(self, scan: FilesystemSnapshotScan) -> None:
        expected_batch_count = (
            scan.streamed_batch_count + 1
            if scan.streamed_batch_count
            else max(
                1,
                _bounded_batch_count(len(scan.entries)),
                _bounded_batch_count(len(scan.filter_decisions)),
            )
        )
        self._seal_store.seal_snapshot(
            SnapshotSealRequest(
                snapshot_id=scan.snapshot_id,
                expected_entry_count=scan.entry_count,
                expected_total_bytes=scan.total_bytes,
                expected_batch_count=expected_batch_count,
                expected_directory_coverage_count=len(scan.coverage),
                expected_issue_count=len(scan.issues),
                expected_blocking_issue_count=sum(
                    issue.blocks_destructive_actions for issue in scan.issues
                ),
                expected_case_collision_group_count=(
                    self._persisted_case_collision_group_count(scan.snapshot_id)
                    if scan.streamed_batch_count
                    else _case_collision_group_count(scan)
                ),
                expected_filter_decision_count=scan.filter_decision_count,
            )
        )

    def _persisted_case_collision_group_count(self, snapshot_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM case_collision_groups
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise SqliteJobSnapshotMaterializationError(
                "JOB_SNAPSHOT_CASE_COLLISION_COUNT_FAILED"
            )
        return int(row[0])

    def _persist_blocked_without_analysis(
        self,
        candidate: _SnapshotJobCandidate,
        *,
        reason_code: str,
        observed_utc: str,
    ) -> str:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._upsert_materialization(
                candidate,
                analysis_id=None,
                state="BLOCKED",
                reason_code=reason_code,
                snapshot_count=0,
                sealed_snapshot_count=0,
                observed_utc=observed_utc,
            )
            self._connection.execute("COMMIT")
            return reason_code
        except sqlite3.Error as exc:
            _rollback(self._connection)
            failure_reason = self._persistence_failure_reason(
                exc,
                default="JOB_SNAPSHOT_BLOCK_PERSISTENCE_FAILED",
            )
            if failure_reason == "STATE_CAPACITY_SQLITE_FULL":
                return failure_reason
            raise SqliteJobSnapshotMaterializationError(
                "JOB_SNAPSHOT_BLOCK_PERSISTENCE_FAILED"
            ) from exc

    def _analysis_capacity_block_reason(
        self,
        *,
        endpoint_count: int,
        entry_count: int | None = None,
        coverage_count: int = 0,
        issue_count: int = 0,
    ) -> str | None:
        if self._capacity_gate is None:
            return None
        report = self._capacity_gate.evaluate(
            snapshot_analysis_growth_estimate(
                endpoint_count=endpoint_count,
                entry_count=entry_count,
                coverage_count=coverage_count,
                issue_count=issue_count,
            )
        )
        if report.allows_new_analysis_and_transfers:
            return None
        return report.reason_code

    def _persistence_failure_reason(
        self,
        error: BaseException,
        *,
        default: str,
    ) -> str:
        failure_kind = classify_sqlite_exception(error)
        if failure_kind is not SqliteFailureKind.FULL:
            return default
        if self._capacity_gate is not None:
            self._capacity_gate.latch_sqlite_full("catalog")
        return "STATE_CAPACITY_SQLITE_FULL"

    def _upsert_materialization(
        self,
        candidate: _SnapshotJobCandidate,
        *,
        analysis_id: str | None,
        state: str,
        reason_code: str,
        snapshot_count: int,
        sealed_snapshot_count: int,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO standard_backup_job_snapshot_materializations (
                job_id,
                job_revision_id,
                analysis_id,
                state,
                reason_code,
                snapshot_count,
                sealed_snapshot_count,
                started_utc,
                completed_utc,
                row_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT (job_id, job_revision_id)
            DO UPDATE SET
                analysis_id = excluded.analysis_id,
                state = excluded.state,
                reason_code = excluded.reason_code,
                snapshot_count = excluded.snapshot_count,
                sealed_snapshot_count = excluded.sealed_snapshot_count,
                started_utc = excluded.started_utc,
                completed_utc = excluded.completed_utc,
                row_version =
                    standard_backup_job_snapshot_materializations.row_version + 1
            """,
            (
                candidate.job_id,
                candidate.job_revision_id,
                analysis_id,
                state,
                reason_code,
                snapshot_count,
                sealed_snapshot_count,
                observed_utc,
                observed_utc,
            ),
        )


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _scan_sink_checkpoint(checkpoint: object) -> tuple[str, int]:
    if (
        not isinstance(checkpoint, tuple)
        or len(checkpoint) != 2
        or not isinstance(checkpoint[0], str)
        or not checkpoint[0].startswith("snapshot_scan_")
        or not checkpoint[0][len("snapshot_scan_") :].isdigit()
        or not isinstance(checkpoint[1], int)
        or checkpoint[1] < 0
    ):
        raise SqliteJobSnapshotMaterializationError(
            "JOB_SNAPSHOT_STREAM_CHECKPOINT_INVALID"
        )
    return checkpoint[0], checkpoint[1]


def _job_scan_precondition_reason(
    candidate: _SnapshotJobCandidate,
) -> str | None:
    source_count = sum(endpoint.role == "SOURCE" for endpoint in candidate.endpoints)
    target_count = sum(endpoint.role == "TARGET" for endpoint in candidate.endpoints)
    if source_count != 1 or target_count < 1:
        return "JOB_SNAPSHOT_ENDPOINT_SET_INCOMPLETE"
    for endpoint in candidate.endpoints:
        if endpoint.inspection_status != "CLASSIFIED":
            return "JOB_SNAPSHOT_ENDPOINT_NOT_CLASSIFIED"
        try:
            classification_state = EndpointControlAreaState(
                endpoint.classification_state or ""
            )
        except ValueError:
            return "JOB_SNAPSHOT_ENDPOINT_CLASSIFICATION_UNSAFE"
        if classification_state not in _CLASSIFIED_READABLE_STATES:
            return "JOB_SNAPSHOT_ENDPOINT_CLASSIFICATION_UNSAFE"
        if (
            classification_state in EXCLUDABLE_CONTROL_AREA_STATES
            and endpoint.marker_json is None
        ):
            return "JOB_SNAPSHOT_ENDPOINT_MARKER_EVIDENCE_MISSING"
        allowed_registration_states = (
            _SOURCE_READABLE_REGISTRATION_STATES
            if endpoint.role == "SOURCE"
            else _TARGET_READABLE_REGISTRATION_STATES
        )
        if endpoint.registration_state not in allowed_registration_states:
            return "JOB_SNAPSHOT_ENDPOINT_NOT_READABLE"
    return None


def _exclude_control_area(endpoint: _SnapshotEndpointCandidate) -> bool:
    return (
        EndpointControlAreaState(endpoint.classification_state or "")
        in EXCLUDABLE_CONTROL_AREA_STATES
    )


def _local_root(root_uri: str) -> Path:
    return local_path_from_file_uri(root_uri)


def _validate_materialization_ids(
    *,
    analysis_id: str,
    snapshot_ids: tuple[str, ...],
    expected_snapshot_count: int,
) -> None:
    if not analysis_id.strip():
        raise ValueError("JOB_SNAPSHOT_ANALYSIS_ID_REQUIRED")
    if (
        len(snapshot_ids) != expected_snapshot_count
        or len(set(snapshot_ids)) != len(snapshot_ids)
        or any(not snapshot_id.strip() for snapshot_id in snapshot_ids)
    ):
        raise ValueError("JOB_SNAPSHOT_IDS_INVALID")


def _scan_failure_reason(exc: Exception) -> str:
    validation_code = getattr(exc, "validation_code", None)
    if isinstance(validation_code, str) and validation_code.strip():
        return validation_code
    return "JOB_SNAPSHOT_SCAN_FAILED"


def _case_collision_group_count(scan: FilesystemSnapshotScan) -> int:
    counts: dict[str, int] = {}
    for entry in scan.entries:
        counts[entry.comparison_key] = counts.get(entry.comparison_key, 0) + 1
    return sum(count > 1 for count in counts.values())


def _bounded_batch_count(item_count: int) -> int:
    return (
        item_count + MAX_SNAPSHOT_BATCH_ENTRIES - 1
    ) // MAX_SNAPSHOT_BATCH_ENTRIES


def _result(
    candidate: _SnapshotJobCandidate,
    *,
    state: str,
    reason_code: str,
    analysis_id: str | None = None,
    snapshot_ids: tuple[str, ...] = (),
) -> JobSnapshotMaterializationResult:
    return JobSnapshotMaterializationResult(
        job_id=candidate.job_id,
        job_revision_id=candidate.job_revision_id,
        analysis_id=analysis_id,
        state=state,
        reason_code=reason_code,
        snapshot_ids=snapshot_ids,
    )


def _refresh_report(
    results: tuple[JobSnapshotMaterializationResult, ...],
) -> SnapshotMaterializationRefreshReport:
    return SnapshotMaterializationRefreshReport(
        scanned_job_count=sum(
            result.state in {"SEALED", "BLOCKED"} for result in results
        ),
        reused_job_count=sum(result.state == "REUSED" for result in results),
        blocked_job_count=sum(result.state == "BLOCKED" for result in results),
        failed_job_count=sum(result.state == "FAILED" for result in results),
        sealed_snapshot_count=sum(
            len(result.snapshot_ids)
            for result in results
            if result.state in {"SEALED", "REUSED"}
        ),
        results=results,
    )
