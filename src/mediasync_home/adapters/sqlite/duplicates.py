from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from mediasync_home.application.duplicates import (
    DuplicateAnalysisSummary,
    DuplicateRelationMaterializationReport,
)
from mediasync_home.application.endpoint_capabilities import (
    EndpointCapabilities,
    EndpointCapabilityEvidenceError,
    FileIdReliability,
)


class SqliteDuplicateRelationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _AnalysisEndpoint:
    endpoint_id: str
    role: str
    ordinal: int
    snapshot_id: str
    file_ids_stable: bool


class SqliteDuplicateRelationStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.create_function(
            "mediasync_target_path_key",
            2,
            _target_path_key,
            deterministic=True,
        )

    def materialize_known_duplicate_relations(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> DuplicateRelationMaterializationReport:
        if not analysis_id.strip() or not observed_utc.strip():
            raise ValueError("duplicate relation materialization identity is invalid")
        endpoints = self._load_analysis_endpoints(analysis_id)
        sources = tuple(item for item in endpoints if item.role == "SOURCE")
        targets = tuple(item for item in endpoints if item.role == "TARGET")
        if len(sources) != 1 or not targets:
            raise SqliteDuplicateRelationError(
                "DUPLICATE_RELATION_ENDPOINT_SET_INVALID"
            )

        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            before_alias_groups = self._count_alias_groups(analysis_id)
            before_duplicate_groups = self._count_duplicate_groups(analysis_id)
            for endpoint in endpoints:
                if endpoint.file_ids_stable:
                    self._materialize_alias_groups(
                        analysis_id=analysis_id,
                        endpoint=endpoint,
                        observed_utc=observed_utc,
                    )
            self._materialize_alias_members(analysis_id)
            self._materialize_expected_path_keys(analysis_id)
            self._materialize_expected_replica_groups(
                analysis_id=analysis_id,
                observed_utc=observed_utc,
            )
            self._materialize_expected_replica_members(analysis_id)
            self._materialize_internal_duplicate_groups(
                analysis_id=analysis_id,
                observed_utc=observed_utc,
            )
            self._materialize_internal_duplicate_members(analysis_id)
            self._materialize_cross_endpoint_duplicate_groups(
                analysis_id=analysis_id,
                observed_utc=observed_utc,
            )
            self._materialize_cross_endpoint_duplicate_members(analysis_id)
            report = self._load_materialization_report(
                analysis_id,
                before_alias_groups=before_alias_groups,
                before_duplicate_groups=before_duplicate_groups,
            )
            if owns_transaction:
                self._connection.execute("COMMIT")
            return report
        except Exception:
            if owns_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def load_duplicate_analysis_summary(
        self,
        analysis_id: str,
    ) -> DuplicateAnalysisSummary | None:
        exists = self._connection.execute(
            "SELECT 1 FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if exists is None:
            return None
        row = self._connection.execute(
            """
            SELECT
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?),
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'EXPECTED_REPLICA'),
                (SELECT COALESCE(sum(expected_replica_count), 0)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'EXPECTED_REPLICA'),
                (SELECT count(*)
                 FROM file_object_alias_groups
                 WHERE analysis_id = ?),
                (SELECT COALESCE(sum(member_count), 0)
                 FROM file_object_alias_groups
                 WHERE analysis_id = ?),
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'INTRA_ENDPOINT_DUPLICATE'),
                (SELECT COALESCE(sum(physical_object_count), 0)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'INTRA_ENDPOINT_DUPLICATE'),
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'UNRELATED_CROSS_ENDPOINT_DUPLICATE'),
                (SELECT COALESCE(sum(physical_object_count), 0)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'UNRELATED_CROSS_ENDPOINT_DUPLICATE'),
                (SELECT COALESCE(sum(potential_savings_bytes), 0)
                 FROM duplicate_groups
                 WHERE analysis_id = ?)
            """,
            (analysis_id,) * 10,
        ).fetchone()
        if row is None:
            raise SqliteDuplicateRelationError("DUPLICATE_SUMMARY_READ_FAILED")
        return DuplicateAnalysisSummary(
            analysis_id=analysis_id,
            read_model_available=True,
            duplicate_group_count=_required_int(row[0]),
            expected_replica_group_count=_required_int(row[1]),
            expected_replica_count=_required_int(row[2]),
            same_file_alias_group_count=_required_int(row[3]),
            same_file_alias_path_count=_required_int(row[4]),
            internal_duplicate_group_count=_required_int(row[5]),
            internal_duplicate_file_count=_required_int(row[6]),
            cross_endpoint_duplicate_group_count=_required_int(row[7]),
            cross_endpoint_duplicate_file_count=_required_int(row[8]),
            potential_savings_bytes=_required_int(row[9]),
        )

    def _load_analysis_endpoints(
        self,
        analysis_id: str,
    ) -> tuple[_AnalysisEndpoint, ...]:
        rows = self._connection.execute(
            """
            SELECT
                bindings.endpoint_id,
                bindings.role,
                bindings.ordinal,
                snapshots.id,
                observations.read_capabilities_json
            FROM analyses
            INNER JOIN standard_backup_job_endpoint_bindings AS bindings
                ON bindings.job_id = analyses.job_id
                AND bindings.job_revision_id = analyses.job_revision_id
            INNER JOIN snapshots
                ON snapshots.analysis_id = analyses.id
                AND snapshots.endpoint_id = bindings.endpoint_id
                AND snapshots.endpoint_revision_id = bindings.endpoint_revision_id
            LEFT JOIN endpoint_classification_observations AS observations
                ON observations.endpoint_id = bindings.endpoint_id
                AND observations.endpoint_revision_id = bindings.endpoint_revision_id
            WHERE analyses.id = ?
                AND snapshots.complete = 1
                AND snapshots.immutable = 1
            ORDER BY
                CASE bindings.role WHEN 'SOURCE' THEN 0 ELSE 1 END,
                bindings.ordinal,
                bindings.endpoint_id
            """,
            (analysis_id,),
        ).fetchall()
        if not rows:
            raise SqliteDuplicateRelationError("DUPLICATE_RELATION_ANALYSIS_NOT_READY")
        endpoints: list[_AnalysisEndpoint] = []
        for row in rows:
            capabilities_json = None if row[4] is None else str(row[4])
            endpoints.append(
                _AnalysisEndpoint(
                    endpoint_id=str(row[0]),
                    role=str(row[1]),
                    ordinal=_required_int(row[2]),
                    snapshot_id=str(row[3]),
                    file_ids_stable=_file_ids_stable(capabilities_json),
                )
            )
        return tuple(endpoints)

    def _materialize_alias_groups(
        self,
        *,
        analysis_id: str,
        endpoint: _AnalysisEndpoint,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO file_object_alias_groups (
                id,
                analysis_id,
                snapshot_id,
                endpoint_id,
                volume_identity,
                file_id,
                file_id_reliability,
                reported_link_count,
                member_count,
                classification_state,
                created_utc
            )
            SELECT
                'alias:' || ? || ':' || entries.identity_fingerprint_hash,
                ?,
                ?,
                ?,
                ?,
                entries.identity_fingerprint_hash,
                'STABLE',
                NULL,
                count(*),
                'SAME_FILE_MULTIPLE_PATHS',
                ?
            FROM file_entries AS entries
            WHERE entries.snapshot_id = ?
                AND entries.endpoint_id = ?
                AND entries.object_type = 'file'
                AND entries.identity_fingerprint_hash IS NOT NULL
            GROUP BY entries.identity_fingerprint_hash
            HAVING count(*) >= 2
            ON CONFLICT DO NOTHING
            """,
            (
                endpoint.snapshot_id,
                analysis_id,
                endpoint.snapshot_id,
                endpoint.endpoint_id,
                endpoint.endpoint_id,
                observed_utc,
                endpoint.snapshot_id,
                endpoint.endpoint_id,
            ),
        )

    def _materialize_alias_members(self, analysis_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO file_object_alias_members (
                snapshot_id,
                group_id,
                file_entry_id
            )
            SELECT
                groups.snapshot_id,
                groups.id,
                entries.id
            FROM file_object_alias_groups AS groups
            INNER JOIN file_entries AS entries
                ON entries.snapshot_id = groups.snapshot_id
                AND entries.endpoint_id = groups.endpoint_id
                AND entries.identity_fingerprint_hash = groups.file_id
            WHERE groups.analysis_id = ?
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )

    def _materialize_expected_path_keys(self, analysis_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO duplicate_relation_path_keys (
                analysis_id,
                target_endpoint_id,
                snapshot_id,
                endpoint_id,
                file_entry_id,
                endpoint_role,
                path_key
            )
            SELECT
                analyses.id,
                target_bindings.endpoint_id,
                source_snapshots.id,
                source_bindings.endpoint_id,
                source_entries.id,
                'SOURCE',
                mediasync_target_path_key(
                    source_entries.relative_path,
                    target_root_coverage.case_mode
                )
            FROM analyses
            INNER JOIN standard_backup_job_endpoint_bindings AS source_bindings
                ON source_bindings.job_id = analyses.job_id
                AND source_bindings.job_revision_id = analyses.job_revision_id
                AND source_bindings.role = 'SOURCE'
            INNER JOIN snapshots AS source_snapshots
                ON source_snapshots.analysis_id = analyses.id
                AND source_snapshots.endpoint_id = source_bindings.endpoint_id
                AND source_snapshots.endpoint_revision_id =
                    source_bindings.endpoint_revision_id
                AND source_snapshots.complete = 1
                AND source_snapshots.immutable = 1
            INNER JOIN file_entries AS source_entries
                ON source_entries.snapshot_id = source_snapshots.id
                AND source_entries.object_type = 'file'
            INNER JOIN current_read_hash_evidence AS source_hashes
                ON source_hashes.snapshot_id = source_entries.snapshot_id
                AND source_hashes.entry_id = source_entries.id
                AND source_hashes.evidence_kind = 'CURRENT_READ_HASH'
                AND source_hashes.algorithm = 'BLAKE3-256'
                AND source_hashes.hash_schema_version = 1
            INNER JOIN standard_backup_job_endpoint_bindings AS target_bindings
                ON target_bindings.job_id = analyses.job_id
                AND target_bindings.job_revision_id = analyses.job_revision_id
                AND target_bindings.role = 'TARGET'
            INNER JOIN snapshots AS target_snapshots
                ON target_snapshots.analysis_id = analyses.id
                AND target_snapshots.endpoint_id = target_bindings.endpoint_id
                AND target_snapshots.endpoint_revision_id =
                    target_bindings.endpoint_revision_id
                AND target_snapshots.complete = 1
                AND target_snapshots.immutable = 1
            INNER JOIN directory_coverage AS target_root_coverage
                ON target_root_coverage.snapshot_id = target_snapshots.id
                AND target_root_coverage.relative_path = '.'
                AND target_root_coverage.case_mode IN (
                    'CASE_SENSITIVE',
                    'CASE_INSENSITIVE'
                )
            WHERE analyses.id = ?
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )
        self._connection.execute(
            """
            INSERT INTO duplicate_relation_path_keys (
                analysis_id,
                target_endpoint_id,
                snapshot_id,
                endpoint_id,
                file_entry_id,
                endpoint_role,
                path_key
            )
            SELECT
                analyses.id,
                target_bindings.endpoint_id,
                target_snapshots.id,
                target_bindings.endpoint_id,
                target_entries.id,
                'TARGET',
                mediasync_target_path_key(
                    target_entries.relative_path,
                    target_root_coverage.case_mode
                )
            FROM analyses
            INNER JOIN standard_backup_job_endpoint_bindings AS target_bindings
                ON target_bindings.job_id = analyses.job_id
                AND target_bindings.job_revision_id = analyses.job_revision_id
                AND target_bindings.role = 'TARGET'
            INNER JOIN snapshots AS target_snapshots
                ON target_snapshots.analysis_id = analyses.id
                AND target_snapshots.endpoint_id = target_bindings.endpoint_id
                AND target_snapshots.endpoint_revision_id =
                    target_bindings.endpoint_revision_id
                AND target_snapshots.complete = 1
                AND target_snapshots.immutable = 1
            INNER JOIN directory_coverage AS target_root_coverage
                ON target_root_coverage.snapshot_id = target_snapshots.id
                AND target_root_coverage.relative_path = '.'
                AND target_root_coverage.case_mode IN (
                    'CASE_SENSITIVE',
                    'CASE_INSENSITIVE'
                )
            INNER JOIN file_entries AS target_entries
                ON target_entries.snapshot_id = target_snapshots.id
                AND target_entries.object_type = 'file'
            INNER JOIN current_read_hash_evidence AS target_hashes
                ON target_hashes.snapshot_id = target_entries.snapshot_id
                AND target_hashes.entry_id = target_entries.id
                AND target_hashes.evidence_kind = 'CURRENT_READ_HASH'
                AND target_hashes.algorithm = 'BLAKE3-256'
                AND target_hashes.hash_schema_version = 1
            WHERE analyses.id = ?
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )

    def _materialize_expected_replica_groups(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            f"""
            {_EXPECTED_MATCHES_CTE}
            INSERT INTO duplicate_groups (
                id,
                analysis_id,
                relation_key,
                full_hash,
                size_bytes,
                member_count,
                physical_object_count,
                expected_replica_count,
                relationship_class,
                potential_savings_bytes,
                review_state,
                created_utc
            )
            SELECT
                'expected:' || matches.analysis_id || ':'
                    || matches.source_snapshot_id || ':'
                    || matches.source_entry_id || ':'
                    || matches.content_hash,
                matches.analysis_id,
                matches.source_snapshot_id || ':'
                    || matches.source_entry_id || ':'
                    || matches.content_hash,
                matches.content_hash,
                matches.size_bytes,
                1 + count(*),
                1 + count(DISTINCT matches.target_physical_object_key),
                count(DISTINCT matches.target_physical_object_key),
                'EXPECTED_REPLICA',
                0,
                'UNREVIEWED',
                ?
            FROM matches
            GROUP BY
                matches.analysis_id,
                matches.source_snapshot_id,
                matches.source_entry_id,
                matches.content_hash,
                matches.size_bytes
            ON CONFLICT DO NOTHING
            """,
            (analysis_id, observed_utc),
        )

    def _materialize_expected_replica_members(self, analysis_id: str) -> None:
        self._connection.execute(
            f"""
            {_EXPECTED_MATCHES_CTE}
            INSERT INTO duplicate_members (
                group_id,
                snapshot_id,
                endpoint_id,
                file_entry_id,
                relative_path,
                member_role,
                physical_object_key
            )
            SELECT DISTINCT
                'expected:' || matches.analysis_id || ':'
                    || matches.source_snapshot_id || ':'
                    || matches.source_entry_id || ':'
                    || matches.content_hash,
                matches.source_snapshot_id,
                matches.source_endpoint_id,
                matches.source_entry_id,
                matches.source_relative_path,
                'SOURCE_ORIGIN',
                matches.source_physical_object_key
            FROM matches
            WHERE true
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )
        self._connection.execute(
            f"""
            {_EXPECTED_MATCHES_CTE}
            INSERT INTO duplicate_members (
                group_id,
                snapshot_id,
                endpoint_id,
                file_entry_id,
                relative_path,
                member_role,
                physical_object_key
            )
            SELECT
                'expected:' || matches.analysis_id || ':'
                    || matches.source_snapshot_id || ':'
                    || matches.source_entry_id || ':'
                    || matches.content_hash,
                matches.target_snapshot_id,
                matches.target_endpoint_id,
                matches.target_entry_id,
                matches.target_relative_path,
                'EXPECTED_REPLICA',
                matches.target_physical_object_key
            FROM matches
            WHERE true
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )

    def _materialize_internal_duplicate_groups(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            f"""
            {_INTERNAL_DUPLICATES_CTE}
            INSERT INTO duplicate_groups (
                id,
                analysis_id,
                relation_key,
                full_hash,
                size_bytes,
                member_count,
                physical_object_count,
                expected_replica_count,
                relationship_class,
                potential_savings_bytes,
                review_state,
                created_utc
            )
            SELECT
                'internal:' || groups.analysis_id || ':'
                    || groups.snapshot_id || ':' || groups.content_hash,
                groups.analysis_id,
                groups.snapshot_id || ':' || groups.content_hash,
                groups.content_hash,
                groups.size_bytes,
                groups.member_count,
                groups.physical_object_count,
                0,
                'INTRA_ENDPOINT_DUPLICATE',
                groups.size_bytes * (groups.physical_object_count - 1),
                'UNREVIEWED',
                ?
            FROM internal_groups AS groups
            WHERE true
            ON CONFLICT DO NOTHING
            """,
            (analysis_id, observed_utc),
        )

    def _materialize_internal_duplicate_members(self, analysis_id: str) -> None:
        self._connection.execute(
            f"""
            {_INTERNAL_DUPLICATES_CTE}
            INSERT INTO duplicate_members (
                group_id,
                snapshot_id,
                endpoint_id,
                file_entry_id,
                relative_path,
                member_role,
                physical_object_key
            )
            SELECT
                'internal:' || entries.analysis_id || ':'
                    || entries.snapshot_id || ':' || entries.content_hash,
                entries.snapshot_id,
                entries.endpoint_id,
                entries.file_entry_id,
                entries.relative_path,
                'DUPLICATE',
                entries.physical_object_key
            FROM eligible_entries AS entries
            INNER JOIN internal_groups AS groups
                ON groups.analysis_id = entries.analysis_id
                AND groups.snapshot_id = entries.snapshot_id
                AND groups.content_hash = entries.content_hash
                AND groups.size_bytes = entries.size_bytes
            WHERE true
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )

    def _materialize_cross_endpoint_duplicate_groups(
        self,
        *,
        analysis_id: str,
        observed_utc: str,
    ) -> None:
        self._connection.execute(
            f"""
            {_CROSS_ENDPOINT_DUPLICATES_CTE}
            INSERT INTO duplicate_groups (
                id,
                analysis_id,
                relation_key,
                full_hash,
                size_bytes,
                member_count,
                physical_object_count,
                expected_replica_count,
                relationship_class,
                potential_savings_bytes,
                review_state,
                created_utc
            )
            SELECT
                'cross:' || groups.analysis_id || ':'
                    || groups.content_hash || ':' || groups.size_bytes,
                groups.analysis_id,
                groups.content_hash || ':' || groups.size_bytes,
                groups.content_hash,
                groups.size_bytes,
                groups.member_count,
                groups.physical_object_count,
                0,
                'UNRELATED_CROSS_ENDPOINT_DUPLICATE',
                groups.size_bytes * (groups.physical_object_count - 1),
                'UNREVIEWED',
                ?
            FROM cross_groups AS groups
            WHERE true
            ON CONFLICT DO NOTHING
            """,
            (analysis_id, observed_utc),
        )

    def _materialize_cross_endpoint_duplicate_members(self, analysis_id: str) -> None:
        self._connection.execute(
            f"""
            {_CROSS_ENDPOINT_DUPLICATES_CTE}
            INSERT INTO duplicate_members (
                group_id,
                snapshot_id,
                endpoint_id,
                file_entry_id,
                relative_path,
                member_role,
                physical_object_key
            )
            SELECT
                'cross:' || members.analysis_id || ':'
                    || members.content_hash || ':' || members.size_bytes,
                members.snapshot_id,
                members.endpoint_id,
                members.file_entry_id,
                members.relative_path,
                'DUPLICATE',
                members.physical_object_key
            FROM representative_members AS members
            INNER JOIN cross_groups AS groups
                ON groups.analysis_id = members.analysis_id
                AND groups.content_hash = members.content_hash
                AND groups.size_bytes = members.size_bytes
            WHERE true
            ON CONFLICT DO NOTHING
            """,
            (analysis_id,),
        )

    def _load_materialization_report(
        self,
        analysis_id: str,
        *,
        before_alias_groups: int,
        before_duplicate_groups: int,
    ) -> DuplicateRelationMaterializationReport:
        row = self._connection.execute(
            """
            SELECT
                (SELECT count(*)
                 FROM file_object_alias_groups
                 WHERE analysis_id = ?),
                (SELECT count(*)
                 FROM file_object_alias_members AS members
                 INNER JOIN file_object_alias_groups AS groups
                    ON groups.id = members.group_id
                 WHERE groups.analysis_id = ?),
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'EXPECTED_REPLICA'),
                (SELECT count(*)
                 FROM duplicate_members AS members
                 INNER JOIN duplicate_groups AS groups
                    ON groups.id = members.group_id
                 WHERE groups.analysis_id = ?
                   AND members.member_role = 'EXPECTED_REPLICA'),
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'INTRA_ENDPOINT_DUPLICATE'),
                (SELECT COALESCE(sum(physical_object_count), 0)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'INTRA_ENDPOINT_DUPLICATE'),
                (SELECT count(*)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'UNRELATED_CROSS_ENDPOINT_DUPLICATE'),
                (SELECT COALESCE(sum(physical_object_count), 0)
                 FROM duplicate_groups
                 WHERE analysis_id = ?
                   AND relationship_class = 'UNRELATED_CROSS_ENDPOINT_DUPLICATE')
            """,
            (analysis_id,) * 8,
        ).fetchone()
        if row is None:
            raise SqliteDuplicateRelationError(
                "DUPLICATE_RELATION_MATERIALIZATION_NOT_RECORDED"
            )
        alias_groups = _required_int(row[0])
        expected_groups = _required_int(row[2])
        return DuplicateRelationMaterializationReport(
            analysis_id=analysis_id,
            alias_group_count=alias_groups,
            alias_path_count=_required_int(row[1]),
            expected_replica_group_count=expected_groups,
            expected_replica_count=_required_int(row[3]),
            idempotent_replay=(
                alias_groups == before_alias_groups
                and self._count_duplicate_groups(analysis_id)
                == before_duplicate_groups
            ),
            internal_duplicate_group_count=_required_int(row[4]),
            internal_duplicate_file_count=_required_int(row[5]),
            cross_endpoint_duplicate_group_count=_required_int(row[6]),
            cross_endpoint_duplicate_file_count=_required_int(row[7]),
        )

    def _count_alias_groups(self, analysis_id: str) -> int:
        row = self._connection.execute(
            "SELECT count(*) FROM file_object_alias_groups WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
        return 0 if row is None else _required_int(row[0])

    def _count_duplicate_groups(self, analysis_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM duplicate_groups
            WHERE analysis_id = ?
            """,
            (analysis_id,),
        ).fetchone()
        return 0 if row is None else _required_int(row[0])


_EXPECTED_MATCHES_CTE = """
WITH matches AS (
    SELECT
        source_keys.analysis_id AS analysis_id,
        source_keys.snapshot_id AS source_snapshot_id,
        source_keys.endpoint_id AS source_endpoint_id,
        source_entries.id AS source_entry_id,
        source_entries.relative_path AS source_relative_path,
        source_hashes.content_hash AS content_hash,
        source_hashes.size_bytes AS size_bytes,
        COALESCE(
            source_alias_members.group_id,
            'entry:' || source_keys.snapshot_id || ':' || source_entries.id
        ) AS source_physical_object_key,
        target_keys.snapshot_id AS target_snapshot_id,
        target_keys.endpoint_id AS target_endpoint_id,
        target_entries.id AS target_entry_id,
        target_entries.relative_path AS target_relative_path,
        COALESCE(
            target_alias_members.group_id,
            'entry:' || target_keys.snapshot_id || ':' || target_entries.id
        ) AS target_physical_object_key
    FROM duplicate_relation_path_keys AS source_keys
    INNER JOIN duplicate_relation_path_keys AS target_keys
        ON target_keys.analysis_id = source_keys.analysis_id
        AND target_keys.target_endpoint_id = source_keys.target_endpoint_id
        AND target_keys.path_key = source_keys.path_key
        AND target_keys.endpoint_role = 'TARGET'
    INNER JOIN file_entries AS source_entries
        ON source_entries.snapshot_id = source_keys.snapshot_id
        AND source_entries.id = source_keys.file_entry_id
        AND source_entries.object_type = 'file'
    INNER JOIN current_read_hash_evidence AS source_hashes
        ON source_hashes.snapshot_id = source_entries.snapshot_id
        AND source_hashes.entry_id = source_entries.id
        AND source_hashes.evidence_kind = 'CURRENT_READ_HASH'
        AND source_hashes.algorithm = 'BLAKE3-256'
        AND source_hashes.hash_schema_version = 1
    INNER JOIN file_entries AS target_entries
        ON target_entries.snapshot_id = target_keys.snapshot_id
        AND target_entries.id = target_keys.file_entry_id
        AND target_entries.object_type = 'file'
    INNER JOIN current_read_hash_evidence AS target_hashes
        ON target_hashes.snapshot_id = target_entries.snapshot_id
        AND target_hashes.entry_id = target_entries.id
        AND target_hashes.content_hash = source_hashes.content_hash
        AND target_hashes.size_bytes = source_hashes.size_bytes
        AND target_hashes.evidence_kind = 'CURRENT_READ_HASH'
        AND target_hashes.algorithm = source_hashes.algorithm
        AND target_hashes.hash_schema_version = source_hashes.hash_schema_version
    LEFT JOIN file_object_alias_members AS source_alias_members
        ON source_alias_members.snapshot_id = source_entries.snapshot_id
        AND source_alias_members.file_entry_id = source_entries.id
    LEFT JOIN file_object_alias_members AS target_alias_members
        ON target_alias_members.snapshot_id = target_entries.snapshot_id
        AND target_alias_members.file_entry_id = target_entries.id
    WHERE source_keys.analysis_id = ?
        AND source_keys.endpoint_role = 'SOURCE'
)
"""


_INTERNAL_DUPLICATES_CTE = """
WITH eligible_entries AS (
    SELECT
        snapshots.analysis_id,
        entries.snapshot_id,
        entries.endpoint_id,
        entries.id AS file_entry_id,
        entries.relative_path,
        hashes.content_hash,
        hashes.size_bytes,
        COALESCE(
            alias_members.group_id,
            'entry:' || entries.snapshot_id || ':' || entries.id
        ) AS physical_object_key
    FROM snapshots
    INNER JOIN file_entries AS entries
        ON entries.snapshot_id = snapshots.id
        AND entries.endpoint_id = snapshots.endpoint_id
        AND entries.object_type = 'file'
    INNER JOIN current_read_hash_evidence AS hashes
        ON hashes.snapshot_id = entries.snapshot_id
        AND hashes.entry_id = entries.id
        AND hashes.endpoint_id = entries.endpoint_id
        AND hashes.evidence_kind = 'CURRENT_READ_HASH'
        AND hashes.algorithm = 'BLAKE3-256'
        AND hashes.hash_schema_version = 1
    LEFT JOIN file_object_alias_members AS alias_members
        ON alias_members.snapshot_id = entries.snapshot_id
        AND alias_members.file_entry_id = entries.id
    WHERE snapshots.analysis_id = ?
        AND snapshots.complete = 1
        AND snapshots.immutable = 1
        AND NOT EXISTS (
            SELECT 1
            FROM duplicate_members AS replica_members
            INNER JOIN duplicate_groups AS replica_groups
                ON replica_groups.id = replica_members.group_id
                AND replica_groups.analysis_id = snapshots.analysis_id
                AND replica_groups.relationship_class = 'EXPECTED_REPLICA'
            WHERE replica_members.snapshot_id = entries.snapshot_id
                AND replica_members.file_entry_id = entries.id
                AND replica_members.member_role = 'EXPECTED_REPLICA'
        )
),
internal_groups AS (
    SELECT
        analysis_id,
        snapshot_id,
        endpoint_id,
        content_hash,
        size_bytes,
        count(*) AS member_count,
        count(DISTINCT physical_object_key) AS physical_object_count
    FROM eligible_entries
    GROUP BY
        analysis_id,
        snapshot_id,
        endpoint_id,
        content_hash,
        size_bytes
    HAVING count(DISTINCT physical_object_key) >= 2
)
"""


_CROSS_ENDPOINT_DUPLICATES_CTE = """
WITH eligible_entries AS (
    SELECT
        snapshots.analysis_id,
        entries.snapshot_id,
        entries.endpoint_id,
        entries.id AS file_entry_id,
        entries.relative_path,
        hashes.content_hash,
        hashes.size_bytes,
        COALESCE(
            alias_members.group_id,
            'entry:' || entries.snapshot_id || ':' || entries.id
        ) AS physical_object_key
    FROM snapshots
    INNER JOIN file_entries AS entries
        ON entries.snapshot_id = snapshots.id
        AND entries.endpoint_id = snapshots.endpoint_id
        AND entries.object_type = 'file'
    INNER JOIN current_read_hash_evidence AS hashes
        ON hashes.snapshot_id = entries.snapshot_id
        AND hashes.entry_id = entries.id
        AND hashes.endpoint_id = entries.endpoint_id
        AND hashes.evidence_kind = 'CURRENT_READ_HASH'
        AND hashes.algorithm = 'BLAKE3-256'
        AND hashes.hash_schema_version = 1
    LEFT JOIN file_object_alias_members AS alias_members
        ON alias_members.snapshot_id = entries.snapshot_id
        AND alias_members.file_entry_id = entries.id
    WHERE snapshots.analysis_id = ?
        AND snapshots.complete = 1
        AND snapshots.immutable = 1
        AND NOT EXISTS (
            SELECT 1
            FROM duplicate_members AS replica_members
            INNER JOIN duplicate_groups AS replica_groups
                ON replica_groups.id = replica_members.group_id
                AND replica_groups.analysis_id = snapshots.analysis_id
                AND replica_groups.relationship_class = 'EXPECTED_REPLICA'
            WHERE replica_members.snapshot_id = entries.snapshot_id
                AND replica_members.file_entry_id = entries.id
                AND replica_members.member_role = 'EXPECTED_REPLICA'
        )
),
ranked_physical_objects AS (
    SELECT
        physical.analysis_id,
        physical.snapshot_id,
        physical.endpoint_id,
        physical.content_hash,
        physical.size_bytes,
        physical.physical_object_key,
        row_number() OVER (
            PARTITION BY
                physical.analysis_id,
                physical.endpoint_id,
                physical.content_hash,
                physical.size_bytes
            ORDER BY physical.physical_object_key
        ) AS endpoint_rank
    FROM (
        SELECT DISTINCT
            analysis_id,
            snapshot_id,
            endpoint_id,
            content_hash,
            size_bytes,
            physical_object_key
        FROM eligible_entries
    ) AS physical
),
endpoint_representatives AS (
    SELECT
        analysis_id,
        snapshot_id,
        endpoint_id,
        content_hash,
        size_bytes,
        physical_object_key
    FROM ranked_physical_objects
    WHERE endpoint_rank = 1
),
representative_members AS (
    SELECT entries.*
    FROM eligible_entries AS entries
    INNER JOIN endpoint_representatives AS representatives
        ON representatives.analysis_id = entries.analysis_id
        AND representatives.snapshot_id = entries.snapshot_id
        AND representatives.endpoint_id = entries.endpoint_id
        AND representatives.content_hash = entries.content_hash
        AND representatives.size_bytes = entries.size_bytes
        AND representatives.physical_object_key = entries.physical_object_key
),
cross_groups AS (
    SELECT
        analysis_id,
        content_hash,
        size_bytes,
        count(*) AS member_count,
        count(DISTINCT endpoint_id) AS physical_object_count
    FROM representative_members
    GROUP BY analysis_id, content_hash, size_bytes
    HAVING count(DISTINCT endpoint_id) >= 2
)
"""


def _file_ids_stable(capabilities_json: str | None) -> bool:
    if capabilities_json is None:
        return False
    try:
        profile = EndpointCapabilities.from_json(capabilities_json)
    except EndpointCapabilityEvidenceError as exc:
        raise SqliteDuplicateRelationError(
            "DUPLICATE_RELATION_CAPABILITY_EVIDENCE_INVALID"
        ) from exc
    return (
        profile.supports_file_ids
        and profile.file_id_reliability is FileIdReliability.STABLE
    )


def _target_path_key(relative_path: object, case_mode: object) -> str:
    if not isinstance(relative_path, str) or not isinstance(case_mode, str):
        raise ValueError("target path key inputs are invalid")
    normalized = "/".join(relative_path.replace("\\", "/").split("/"))
    if case_mode == "CASE_INSENSITIVE":
        return normalized.casefold()
    if case_mode == "CASE_SENSITIVE":
        return normalized
    raise ValueError("target path key case mode is invalid")


def _required_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("duplicate relation integer column is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("duplicate relation integer column is invalid")
