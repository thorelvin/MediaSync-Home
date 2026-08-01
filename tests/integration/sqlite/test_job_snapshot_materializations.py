from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.local_snapshot_scanner import (
    LocalFilesystemSnapshotScanner,
)
from mediasync_home.adapters.endpoint_capabilities import (
    LocalWindowsEndpointCapabilitiesProbe,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.job_snapshots import (
    SqliteJobSnapshotMaterializer,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.snapshots import SqliteSnapshotEntryStore
from mediasync_home.application.snapshot_scanning import (
    DirectoryCaseContext,
    SnapshotMaterializationIds,
)
from mediasync_home.application.state_capacity import (
    StateCapacityGate,
    StateCapacityObservation,
    StateCapacityPolicy,
)


@dataclass
class _FixedMaterializationIdFactory:
    calls: int = 0

    def new_snapshot_materialization_ids(
        self,
        *,
        snapshot_count: int,
    ) -> SnapshotMaterializationIds:
        self.calls += 1
        return SnapshotMaterializationIds(
            analysis_id=f"analysis-{self.calls}",
            snapshot_ids=tuple(
                f"snapshot-{self.calls}-{ordinal}"
                for ordinal in range(snapshot_count)
            ),
        )


class _FixedCaseModeProbe:
    def __init__(self, case_mode: str = "CASE_INSENSITIVE") -> None:
        self._case_mode = case_mode

    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext:
        del path
        return DirectoryCaseContext(
            case_mode=self._case_mode,
            evidence="FIXED_TEST_CASE_MODE_V1",
            error_code=(
                "CASE_MODE_UNAVAILABLE" if self._case_mode == "UNKNOWN" else None
            ),
        )


@dataclass(frozen=True)
class _FixedCapacityProbe:
    observation: StateCapacityObservation

    def measure(self) -> StateCapacityObservation:
        return self.observation


def test_job_snapshot_materializer_hard_capacity_stop_starts_no_analysis(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "must-not-be-scanned.txt").write_text("content", encoding="utf-8")
    database = tmp_path / "catalog.sqlite"
    factory = _FixedMaterializationIdFactory()
    capacity_gate = StateCapacityGate(
        probe=_FixedCapacityProbe(
            StateCapacityObservation(
                state_size_bytes=2,
                local_free_space_bytes=1_000_000_000,
                measurement_complete=True,
                scanned_entry_count=0,
            )
        ),
        policy=StateCapacityPolicy(
            soft_quota_bytes=1,
            hard_stop_quota_bytes=2,
            minimum_free_space_bytes=0,
            internal_backup_reserve_bytes=0,
        ),
    )

    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_active_job_with_endpoints(connection, source=source, target=target)
        store = SqliteSnapshotEntryStore(connection)

        report = SqliteJobSnapshotMaterializer(
            connection,
            scanner=LocalFilesystemSnapshotScanner(
                case_mode_probe=_FixedCaseModeProbe(),
            ),
            id_factory=factory,
            entry_store=store,
            seal_store=store,
            capacity_gate=capacity_gate,
        ).refresh_job_snapshots(
            observed_utc="2026-07-30T22:00:00Z",
        )

        assert report.blocked_job_count == 1
        assert report.failed_job_count == 0
        assert report.results[0].reason_code == "STATE_CAPACITY_HARD_QUOTA"
        assert factory.calls == 0
        assert connection.execute("SELECT count(*) FROM analyses").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM standard_backup_job_snapshot_materializations"
        ).fetchone() == (0,)


def test_job_snapshot_materializer_seals_source_and_target_and_reuses_them(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "Photos").mkdir(parents=True)
    target.mkdir()
    (source / "Readme.txt").write_text("readme", encoding="utf-8")
    (source / "Photos" / "Image.jpg").write_bytes(b"image")
    database = tmp_path / "catalog.sqlite"
    factory = _FixedMaterializationIdFactory()

    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_active_job_with_endpoints(connection, source=source, target=target)
        store = SqliteSnapshotEntryStore(connection)
        materializer = SqliteJobSnapshotMaterializer(
            connection,
            scanner=LocalFilesystemSnapshotScanner(
                case_mode_probe=_FixedCaseModeProbe(),
            ),
            id_factory=factory,
            entry_store=store,
            seal_store=store,
        )

        first = materializer.refresh_job_snapshots(
            observed_utc="2026-07-30T22:00:00Z",
        )
        replay = materializer.refresh_job_snapshots(
            observed_utc="2026-07-30T22:01:00Z",
        )

        assert first.to_dict() == {
            "scanned_job_count": 1,
            "reused_job_count": 0,
            "blocked_job_count": 0,
            "failed_job_count": 0,
            "sealed_snapshot_count": 2,
            "results": [
                {
                    "job_id": "job-a",
                    "job_revision_id": "revision-a",
                    "analysis_id": "analysis-1",
                    "state": "SEALED",
                    "reason_code": "JOB_SNAPSHOTS_SEALED",
                    "snapshot_ids": ["snapshot-1-0", "snapshot-1-1"],
                }
            ],
        }
        assert replay.reused_job_count == 1
        assert replay.scanned_job_count == 0
        assert replay.sealed_snapshot_count == 2
        assert replay.results[0].state == "REUSED"
        assert factory.calls == 1
        assert connection.execute(
            """
            SELECT
                state,
                reason_code,
                snapshot_count,
                sealed_snapshot_count,
                row_version
            FROM standard_backup_job_snapshot_materializations
            """
        ).fetchone() == ("SEALED", "JOB_SNAPSHOTS_SEALED", 2, 2, 1)
        assert connection.execute(
            """
            SELECT count(*), sum(complete), sum(immutable)
            FROM snapshots
            """
        ).fetchone() == (2, 2, 2)
        assert [
            row[0]
            for row in connection.execute(
                """
                SELECT entries.relative_path
                FROM file_entries AS entries
                INNER JOIN snapshots
                    ON snapshots.id = entries.snapshot_id
                WHERE snapshots.endpoint_id = 'endpoint-source'
                ORDER BY entries.relative_path
                """
            ).fetchall()
        ] == ["Photos", "Photos/Image.jpg", "Readme.txt"]
        assert not (source / ".mediasync").exists()
        assert not (target / ".mediasync").exists()


def test_job_snapshot_materializer_blocks_unclassified_endpoint_without_scanning(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    database = tmp_path / "catalog.sqlite"

    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_active_job_with_endpoints(connection, source=source, target=target)
        connection.execute(
            """
            DELETE FROM endpoint_classification_observations
            WHERE endpoint_id = 'endpoint-target'
            """
        )
        connection.execute(
            """
            UPDATE standard_backup_job_endpoint_bindings
            SET registration_state = 'BLOCKED'
            WHERE endpoint_id = 'endpoint-target'
            """
        )
        connection.commit()
        store = SqliteSnapshotEntryStore(connection)

        report = SqliteJobSnapshotMaterializer(
            connection,
            scanner=LocalFilesystemSnapshotScanner(
                case_mode_probe=_FixedCaseModeProbe(),
            ),
            id_factory=_FixedMaterializationIdFactory(),
            entry_store=store,
            seal_store=store,
        ).refresh_job_snapshots(
            observed_utc="2026-07-30T22:00:00Z",
        )

        assert report.blocked_job_count == 1
        assert report.failed_job_count == 0
        assert report.results[0].analysis_id is None
        assert report.results[0].reason_code == "JOB_SNAPSHOT_ENDPOINT_NOT_CLASSIFIED"
        assert connection.execute("SELECT count(*) FROM analyses").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone() == (0,)
        assert connection.execute(
            """
            SELECT state, analysis_id, snapshot_count, sealed_snapshot_count
            FROM standard_backup_job_snapshot_materializations
            """
        ).fetchone() == ("BLOCKED", None, 0, 0)


def test_job_snapshot_materializer_persists_incomplete_evidence_without_sealing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    database = tmp_path / "catalog.sqlite"

    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_active_job_with_endpoints(connection, source=source, target=target)
        store = SqliteSnapshotEntryStore(connection)

        report = SqliteJobSnapshotMaterializer(
            connection,
            scanner=LocalFilesystemSnapshotScanner(
                case_mode_probe=_FixedCaseModeProbe("UNKNOWN"),
            ),
            id_factory=_FixedMaterializationIdFactory(),
            entry_store=store,
            seal_store=store,
        ).refresh_job_snapshots(
            observed_utc="2026-07-30T22:00:00Z",
        )

        assert report.blocked_job_count == 1
        assert report.sealed_snapshot_count == 0
        assert report.results[0].analysis_id == "analysis-1"
        assert report.results[0].reason_code == "JOB_SNAPSHOT_SCAN_INCOMPLETE"
        assert connection.execute(
            """
            SELECT state, analysis_id, snapshot_count, sealed_snapshot_count
            FROM standard_backup_job_snapshot_materializations
            """
        ).fetchone() == ("BLOCKED", "analysis-1", 2, 0)
        assert connection.execute(
            """
            SELECT count(*), sum(complete), sum(immutable)
            FROM snapshots
            """
        ).fetchone() == (2, 0, 0)
        assert connection.execute(
            """
            SELECT count(*)
            FROM directory_coverage
            WHERE coverage_state = 'CASE_CONTEXT_UNKNOWN'
            """
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM snapshot_issues
            WHERE blocks_destructive_actions = 1
            """
        ).fetchone() == (2,)


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_active_job_with_endpoints(
    connection: sqlite3.Connection,
    *,
    source: Path,
    target: Path,
) -> None:
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')"
    )
    insert_default_filter_set_version(
        connection,
        job_id="job-a",
        filter_set_id="filter-a",
    )
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
        VALUES ('job-a', 'revision-a', 'filter-a')
        """
    )
    connection.execute(
        """
        INSERT INTO job_heads (job_id, active_revision_id)
        VALUES ('job-a', 'revision-a')
        """
    )
    _insert_endpoint(
        connection,
        endpoint_id="endpoint-source",
        endpoint_revision_id="endpoint-source-revision",
        root=source,
        role="SOURCE",
        ordinal=0,
        registration_state="READ_ONLY_READY",
    )
    _insert_endpoint(
        connection,
        endpoint_id="endpoint-target",
        endpoint_revision_id="endpoint-target-revision",
        root=target,
        role="TARGET",
        ordinal=1,
        registration_state="REGISTRATION_PENDING",
    )
    connection.commit()


def _insert_endpoint(
    connection: sqlite3.Connection,
    *,
    endpoint_id: str,
    endpoint_revision_id: str,
    root: Path,
    role: str,
    ordinal: int,
    registration_state: str,
) -> None:
    capability_evidence = LocalWindowsEndpointCapabilitiesProbe().probe_read_only(root)
    connection.execute("INSERT INTO endpoints (id) VALUES (?)", (endpoint_id,))
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
        VALUES (?, ?, ?, ?)
        """,
        (endpoint_id, endpoint_revision_id, role.title(), root.as_uri()),
    )
    connection.execute(
        """
        INSERT INTO endpoint_heads (endpoint_id, active_revision_id)
        VALUES (?, ?)
        """,
        (endpoint_id, endpoint_revision_id),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_endpoint_bindings (
            job_id,
            job_revision_id,
            role,
            ordinal,
            endpoint_id,
            endpoint_revision_id,
            registration_state
        )
        VALUES (
            'job-a',
            'revision-a',
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            role,
            ordinal,
            endpoint_id,
            endpoint_revision_id,
            registration_state,
        ),
    )
    connection.execute(
        """
        INSERT INTO endpoint_classification_observations (
            endpoint_id,
            endpoint_revision_id,
            local_installation_id,
            inspection_status,
            classification_state,
            reason_codes_json,
            marker_json,
            read_capabilities_json,
            read_capabilities_hash,
            observed_utc
        )
        VALUES (
            ?, ?, 'installation-a', 'CLASSIFIED', 'ABSENT', '[]', NULL, ?, ?, ?
        )
        """,
        (
            endpoint_id,
            endpoint_revision_id,
            capability_evidence.profile_json,
            capability_evidence.capabilities_hash,
            "2026-07-30T21:00:00Z",
        ),
    )
