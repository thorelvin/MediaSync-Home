from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.current_read_hash import (
    CurrentReadHashRequest,
    LocalCurrentReadHasher,
)
from mediasync_home.adapters.local_snapshot_scanner import (
    LocalFilesystemSnapshotScanner,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.duplicates import SqliteDuplicateRelationStore
from mediasync_home.adapters.sqlite.duplicate_scanning import SqliteDuplicateScanner
from mediasync_home.adapters.sqlite.endpoint_classifications import (
    SqliteEndpointClassificationRefresher,
)
from mediasync_home.adapters.sqlite.initial_backup_plans import (
    SqliteInitialBackupPlanMaterializer,
)
from mediasync_home.adapters.sqlite.hash_evidence import (
    SqliteCurrentReadHashEvidenceRefresher,
)
from mediasync_home.adapters.sqlite.job_catalog import SqliteStandardBackupJobCatalog
from mediasync_home.adapters.sqlite.job_snapshots import (
    SqliteJobSnapshotMaterializer,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.snapshots import SqliteSnapshotEntryStore
from mediasync_home.adapters.sqlite.writable_endpoint_registrations import (
    SqliteWritableEndpointRegistrationStore,
)
from mediasync_home.adapters.writable_endpoint_registration import (
    LocalWritableEndpointControlAreaProvisioner,
    LocalWritableEndpointRootOverlapGuard,
)
from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanIdFactory,
)
from mediasync_home.application.hash_evidence import CurrentReadHashEvidence
from mediasync_home.application.endpoint_capabilities import (
    DurabilityLevel,
    EndpointCapabilities,
    EndpointCapabilitiesProbe,
    EndpointCapabilityEvidence,
    FileIdReliability,
)
from mediasync_home.application.plans import (
    PlanOperationPageQuery,
    PlanOperationType,
    TargetPreconditionKind,
)
from mediasync_home.application.snapshot_scanning import (
    DirectoryCaseContext,
    SnapshotMaterializationIds,
)
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCandidate,
    WritableEndpointRegistrationCoordinator,
    WritableEndpointRegistrationIds,
    WritableEndpointTargetIds,
)


INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
JOB_REVISION_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_ENDPOINT_ID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
SOURCE_REVISION_ID = "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb"
TARGET_ENDPOINT_ID = "44444444-4444-4444-8444-444444444444"
TARGET_REVISION_ID = "55555555-5555-4555-8555-555555555555"
NEW_TARGET_REVISION_ID = "66666666-6666-4666-8666-666666666666"
TARGET_B_ENDPOINT_ID = "44444444-4444-4444-8444-444444444445"
TARGET_B_REVISION_ID = "55555555-5555-4555-8555-555555555556"
NEW_TARGET_B_REVISION_ID = "66666666-6666-4666-8666-666666666667"
CONTROL_AREA_ID = "77777777-7777-4777-8777-777777777777"
CONTROL_AREA_B_ID = "77777777-7777-4777-8777-777777777778"
INTENT_ID = "88888888-8888-4888-8888-888888888888"
NEW_JOB_REVISION_ID = "99999999-9999-4999-8999-999999999999"


@dataclass
class _FixedPlanIds(InitialBackupPlanIdFactory):
    calls: int = 0

    def new_initial_backup_plan_id(self) -> str:
        self.calls += 1
        return f"plan-{self.calls}"


class _CountingCurrentReadHasher(LocalCurrentReadHasher):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CurrentReadHashRequest] = []

    def hash_file(self, request: CurrentReadHashRequest) -> CurrentReadHashEvidence:
        self.requests.append(request)
        return super().hash_file(request)


class _FixedRegistrationIds:
    def new_registration_ids(
        self,
        candidates: tuple[WritableEndpointRegistrationCandidate, ...],
    ) -> WritableEndpointRegistrationIds:
        assert len(candidates) in {1, 2}
        revision_ids = (NEW_TARGET_REVISION_ID, NEW_TARGET_B_REVISION_ID)
        control_area_ids = (CONTROL_AREA_ID, CONTROL_AREA_B_ID)
        return WritableEndpointRegistrationIds(
            intent_id=INTENT_ID,
            resulting_job_revision_id=NEW_JOB_REVISION_ID,
            targets=tuple(
                WritableEndpointTargetIds(
                    target_ordinal=candidate.target_ordinal,
                    endpoint_revision_id=revision_ids[index],
                    control_area_id=control_area_ids[index],
                )
                for index, candidate in enumerate(candidates)
            ),
        )


class _FixedSnapshotIds:
    def new_snapshot_materialization_ids(
        self,
        *,
        snapshot_count: int,
    ) -> SnapshotMaterializationIds:
        assert snapshot_count in {2, 3}
        return SnapshotMaterializationIds(
            analysis_id="analysis-a",
            snapshot_ids=(
                "snapshot-source",
                "snapshot-target",
                "snapshot-target-b",
            )[:snapshot_count],
        )


class _FixedCaseModeProbe:
    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext:
        del path
        return DirectoryCaseContext(
            case_mode="CASE_INSENSITIVE",
            evidence="FIXED_TEST_CASE_MODE_V1",
        )


class _FileFlushOnlyCapabilitiesProbe(EndpointCapabilitiesProbe):
    def __init__(self) -> None:
        from mediasync_home.adapters.endpoint_capabilities import (
            LocalWindowsEndpointCapabilitiesProbe,
        )

        self._inner = LocalWindowsEndpointCapabilitiesProbe()

    def probe_read_only(self, root: Path) -> EndpointCapabilityEvidence:
        return self._inner.probe_read_only(root)

    def probe_controlled_writable(
        self,
        root: Path,
        *,
        probe_directory: Path,
        probe_token: str,
    ) -> EndpointCapabilityEvidence:
        measured = self._inner.probe_controlled_writable(
            root,
            probe_directory=probe_directory,
            probe_token=probe_token,
        ).validated_profile()
        return EndpointCapabilityEvidence.from_profile(
            replace(
                measured,
                supports_write_through_move=False,
                durability_level=DurabilityLevel.FILE_FLUSH_CONFIRMED,
            )
        )


class _PrimaryStreamOnlyCapabilitiesProbe(EndpointCapabilitiesProbe):
    def __init__(self) -> None:
        from mediasync_home.adapters.endpoint_capabilities import (
            LocalWindowsEndpointCapabilitiesProbe,
        )

        self._inner = LocalWindowsEndpointCapabilitiesProbe()

    def probe_read_only(self, root: Path) -> EndpointCapabilityEvidence:
        return self._inner.probe_read_only(root)

    def probe_controlled_writable(
        self,
        root: Path,
        *,
        probe_directory: Path,
        probe_token: str,
    ) -> EndpointCapabilityEvidence:
        measured = self._inner.probe_controlled_writable(
            root,
            probe_directory=probe_directory,
            probe_token=probe_token,
        ).validated_profile()
        return EndpointCapabilityEvidence.from_profile(
            replace(measured, supports_named_streams=False)
        )


def test_initial_plan_materializer_seals_exact_registered_snapshots_and_replays(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "Photos").mkdir(parents=True)
    target.mkdir()
    (source / "Readme.txt").write_text("new-readme", encoding="utf-8")
    (source / "Photos" / "Image.jpg").write_bytes(b"image")
    (target / "Readme.txt").write_text("old", encoding="utf-8")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        plan_ids = _FixedPlanIds()
        plans = SqlitePlanStore(connection)
        materializer = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=plans,
            id_factory=plan_ids,
        )

        first = materializer.refresh_initial_backup_plans(
            observed_utc="2026-07-31T15:03:00Z",
        )
        replay = materializer.refresh_initial_backup_plans(
            observed_utc="2026-07-31T15:04:00Z",
        )
        forced = materializer.refresh_initial_backup_plans(
            observed_utc="2026-07-31T15:05:00Z",
            job_id=JOB_ID,
            force=True,
        )

        assert first.sealed_plan_count == 1
        assert first.reused_plan_count == 0
        assert first.results[0].state == "SEALED"
        assert first.results[0].job_revision_id == NEW_JOB_REVISION_ID
        assert first.results[0].plan_id == "plan-1"
        assert first.results[0].operation_count == 3
        assert first.results[0].planned_bytes == 15
        assert first.results[0].plan_runnable is True
        assert replay.sealed_plan_count == 0
        assert replay.reused_plan_count == 1
        assert replay.results[0].idempotent_replay is True
        assert forced.sealed_plan_count == 1
        assert forced.results[0].plan_id == "plan-2"
        assert forced.results[0].idempotent_replay is False
        assert plan_ids.calls == 2

        plan = plans.load_sealed_plan("plan-1")
        assert plan is not None
        assert [operation.target_relative_path for operation in plan.operations] == [
            "Photos",
            "Readme.txt",
            "Photos/Image.jpg",
        ]
        assert [endpoint.snapshot_id for endpoint in plan.endpoints] == [
            "snapshot-source",
            "snapshot-target",
        ]
        assert connection.execute(
            """
            SELECT state, analysis_id, plan_id, operation_count, planned_bytes,
                   plan_runnable, row_version
            FROM initial_backup_plan_materializations
            ORDER BY completed_utc, materialization_id
            """
        ).fetchall() == [
            ("SEALED", "analysis-a", "plan-1", 3, 15, 1, 1),
            ("SEALED", "analysis-a", "plan-2", 3, 15, 1, 1),
        ]
        detail = SqliteStandardBackupJobCatalog(
            connection
        ).load_standard_backup_job_detail(JOB_ID)
        assert detail is not None
        assert detail.initial_plan is not None
        assert detail.initial_plan.plan_id == "plan-2"
        forced_plan = plans.load_sealed_plan("plan-2")
        assert forced_plan is not None
        assert detail.initial_plan.plan_checksum == forced_plan.plan_checksum
        assert detail.initial_plan.plan_runnable is True
        with pytest.raises(
            sqlite3.IntegrityError,
            match="INITIAL_BACKUP_PLAN_MATERIALIZATION_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE initial_backup_plan_materializations
                SET reason_code = 'CHANGED'
                """
            )


def test_initial_plan_materializer_blocks_tampered_capability_hash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Readme.txt").write_text("source", encoding="utf-8")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        connection.execute(
            """
            UPDATE endpoint_classification_observations
            SET read_capabilities_hash = ?
            WHERE endpoint_id = ?
            """,
            ("f" * 64, SOURCE_ENDPOINT_ID),
        )
        connection.commit()

        report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(observed_utc="2026-07-31T15:03:00Z")

        assert report.blocked_job_count == 1
        assert report.results[0].reason_code == (
            "INITIAL_BACKUP_PLAN_ENDPOINT_CAPABILITIES_INVALID"
        )


def test_initial_plan_materializer_blocks_target_without_write_through_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Readme.txt").write_text("source", encoding="utf-8")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
            capabilities_probe=_FileFlushOnlyCapabilitiesProbe(),
        )
        report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(observed_utc="2026-07-31T15:03:00Z")

        assert report.blocked_job_count == 1
        assert report.results[0].reason_code == (
            "INITIAL_BACKUP_PLAN_TARGET_DURABILITY_UNSUPPORTED"
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows named streams only")
def test_initial_plan_blocks_named_stream_copy_to_nonportable_target(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_file = source / "Readme.txt"
    source_file.write_text("source", encoding="utf-8")
    Path(f"{source_file}:metadata").write_text("named", encoding="utf-8")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
            capabilities_probe=_PrimaryStreamOnlyCapabilitiesProbe(),
        )

        report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(observed_utc="2026-07-31T15:03:00Z")

        assert report.blocked_job_count == 1
        assert report.results[0].reason_code == (
            "INITIAL_BACKUP_PLAN_TARGET_NAMED_STREAMS_UNSUPPORTED"
        )


def test_initial_plan_materializer_records_immutable_no_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        plan_ids = _FixedPlanIds()
        materializer = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=plan_ids,
        )

        first = materializer.refresh_initial_backup_plans(
            observed_utc="2026-07-31T16:03:00Z",
        )
        replay = materializer.refresh_initial_backup_plans(
            observed_utc="2026-07-31T16:04:00Z",
        )

        assert first.no_changes_count == 1
        assert first.results[0].state == "NO_CHANGES"
        assert first.results[0].plan_id is None
        assert replay.results[0].idempotent_replay is True
        assert plan_ids.calls == 1
        assert connection.execute("SELECT count(*) FROM plans").fetchone() == (0,)


def test_current_read_hash_evidence_skips_identical_existing_file(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Same.bin").write_bytes(b"identical")
    (target / "Same.bin").write_bytes(b"identical")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        hash_report = SqliteCurrentReadHashEvidenceRefresher(
            connection
        ).refresh_current_read_hash_evidence(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:45Z",
        )
        plan_report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(
            observed_utc="2026-07-31T16:03:00Z",
        )

        assert hash_report.ready is True
        assert hash_report.candidate_pair_count == 1
        assert hash_report.hashed_entry_count == 2
        assert hash_report.identical_pair_count == 1
        assert plan_report.no_changes_count == 1
        assert plan_report.results[0].state == "NO_CHANGES"
        assert connection.execute(
            "SELECT count(*) FROM current_read_hash_evidence"
        ).fetchone() == (2,)
        with pytest.raises(
            sqlite3.IntegrityError,
            match="CURRENT_READ_HASH_EVIDENCE_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE current_read_hash_evidence
                SET computed_utc = 'changed'
                """
            )


def test_current_read_hash_shares_one_source_read_across_two_targets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target_a = tmp_path / "target-a"
    target_b = tmp_path / "target-b"
    payload = b"one source read, two target comparisons"
    for root in (source, target_a, target_b):
        root.mkdir()
        (root / "Same.bin").write_bytes(payload)

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target_a,
            additional_target=target_b,
        )
        hasher = _CountingCurrentReadHasher()
        hash_report = SqliteCurrentReadHashEvidenceRefresher(
            connection,
            hasher=hasher,
        ).refresh_current_read_hash_evidence(
            analysis_id="analysis-a",
            observed_utc="2026-08-09T10:00:00Z",
        )
        plan_report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(
            observed_utc="2026-08-09T10:00:01Z",
        )

        assert hash_report.ready is True
        assert hash_report.candidate_pair_count == 2
        assert hash_report.hashed_entry_count == 3
        assert hash_report.identical_pair_count == 2
        assert len(hasher.requests) == 3
        assert [
            request.endpoint_id
            for request in hasher.requests
            if request.endpoint_id == SOURCE_ENDPOINT_ID
        ] == [SOURCE_ENDPOINT_ID]
        assert {request.endpoint_id for request in hasher.requests} == {
            SOURCE_ENDPOINT_ID,
            TARGET_ENDPOINT_ID,
            TARGET_B_ENDPOINT_ID,
        }
        assert plan_report.no_changes_count == 1
        assert plan_report.results[0].state == "NO_CHANGES"
        assert connection.execute(
            "SELECT count(*) FROM current_read_hash_evidence"
        ).fetchone() == (3,)


def test_duplicate_relations_exclude_expected_replicas_and_hardlink_aliases_from_savings(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_file = source / "Family.bin"
    source_file.write_bytes(b"one physical source object")
    os.link(source_file, source / "Family alias.bin")
    (target / "Family.bin").write_bytes(source_file.read_bytes())

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        hash_report = SqliteCurrentReadHashEvidenceRefresher(
            connection
        ).refresh_current_read_hash_evidence(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:45Z",
        )
        relations = SqliteDuplicateRelationStore(connection)

        first = relations.materialize_known_duplicate_relations(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:46Z",
        )
        replay = relations.materialize_known_duplicate_relations(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:47Z",
        )
        summary = relations.load_duplicate_analysis_summary("analysis-a")

        assert hash_report.identical_pair_count == 1
        assert first.alias_group_count == 1
        assert first.alias_path_count == 2
        assert first.expected_replica_group_count == 1
        assert first.expected_replica_count == 1
        assert first.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert summary is not None
        assert summary.same_file_alias_group_count == 1
        assert summary.same_file_alias_path_count == 2
        assert summary.expected_replica_group_count == 1
        assert summary.expected_replica_count == 1
        assert summary.potential_savings_bytes == 0
        assert connection.execute(
            """
            SELECT relationship_class, potential_savings_bytes
            FROM duplicate_groups
            """
        ).fetchall() == [("EXPECTED_REPLICA", 0)]
        assert connection.execute(
            """
            SELECT classification_state, member_count
            FROM file_object_alias_groups
            """
        ).fetchall() == [("SAME_FILE_MULTIPLE_PATHS", 2)]
        assert connection.execute(
            """
            SELECT endpoint_role, relative_path, path_key
            FROM duplicate_relation_path_keys AS path_keys
            INNER JOIN file_entries AS entries
                ON entries.snapshot_id = path_keys.snapshot_id
                AND entries.id = path_keys.file_entry_id
            ORDER BY endpoint_role, relative_path
            """
        ).fetchall() == [
            ("SOURCE", "Family.bin", "family.bin"),
            ("TARGET", "Family.bin", "family.bin"),
        ]
        with pytest.raises(
            sqlite3.IntegrityError,
            match="DUPLICATE_RELATION_PATH_KEY_IMMUTABLE",
        ):
            connection.execute(
                "UPDATE duplicate_relation_path_keys SET path_key = 'changed'"
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="DUPLICATE_GROUP_IDENTITY_IMMUTABLE",
        ):
            connection.execute(
                "UPDATE duplicate_groups SET potential_savings_bytes = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                """
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
                VALUES (
                    'invalid-savings',
                    'analysis-a',
                    'invalid-savings',
                    ?,
                    100,
                    2,
                    2,
                    1,
                    'EXPECTED_REPLICA',
                    100,
                    'UNREVIEWED',
                    '2026-07-31T16:02:48Z'
                )
                """,
                ("a" * 64,),
            )


def test_duplicate_relations_classify_distinct_same_content_files_with_real_hashes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    content = b"two distinct files with the same current bytes"
    (source / "First.bin").write_bytes(content)
    (source / "Second.bin").write_bytes(content)

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        rows = connection.execute(
            """
            SELECT id, relative_path, size_bytes
            FROM file_entries
            WHERE snapshot_id = 'snapshot-source'
                AND object_type = 'file'
            ORDER BY relative_path
            """
        ).fetchall()
        hasher = LocalCurrentReadHasher()
        evidence = tuple(
            hasher.hash_file(
                CurrentReadHashRequest(
                    snapshot_id="snapshot-source",
                    entry_id=str(row[0]),
                    endpoint_id=SOURCE_ENDPOINT_ID,
                    root=source,
                    relative_path=str(row[1]),
                    expected_size_bytes=int(row[2]),
                    computed_utc="2026-07-31T16:02:45Z",
                )
            )
            for row in rows
        )
        SqliteCurrentReadHashEvidenceRefresher(
            connection
        ).persist_current_read_hash_evidence(
            analysis_id="analysis-a",
            evidence=evidence,
        )
        relations = SqliteDuplicateRelationStore(connection)

        first = relations.materialize_known_duplicate_relations(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:46Z",
        )
        replay = relations.materialize_known_duplicate_relations(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:47Z",
        )
        summary = relations.load_duplicate_analysis_summary("analysis-a")

        assert len(evidence) == 2
        assert evidence[0].content_hash == evidence[1].content_hash
        assert first.alias_group_count == 0
        assert first.expected_replica_group_count == 0
        assert first.internal_duplicate_group_count == 1
        assert first.internal_duplicate_file_count == 2
        assert first.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert summary is not None
        assert summary.duplicate_group_count == 1
        assert summary.internal_duplicate_group_count == 1
        assert summary.internal_duplicate_file_count == 2
        assert summary.potential_savings_bytes == len(content)
        assert connection.execute(
            """
            SELECT
                relationship_class,
                member_count,
                physical_object_count,
                expected_replica_count,
                potential_savings_bytes
            FROM duplicate_groups
            """
        ).fetchall() == [
            ("INTRA_ENDPOINT_DUPLICATE", 2, 2, 0, len(content))
        ]
        members = connection.execute(
            """
            SELECT member_role, relative_path, physical_object_key
            FROM duplicate_members
            ORDER BY relative_path
            """
        ).fetchall()
        assert [row[:2] for row in members] == [
            ("DUPLICATE", "First.bin"),
            ("DUPLICATE", "Second.bin"),
        ]
        assert len({str(row[2]) for row in members}) == 2


def test_duplicate_relations_classify_unrelated_cross_endpoint_files_and_report_them(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    content = b"same bytes at unrelated paths on separate endpoints"
    source_path = source / "Original.bin"
    target_path = target / "Elsewhere.bin"
    source_path.write_bytes(content)
    target_path.write_bytes(content)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (source_path, target_path)
    }

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        rows = connection.execute(
            """
            SELECT entries.id, entries.snapshot_id, entries.endpoint_id,
                   entries.relative_path, entries.size_bytes
            FROM file_entries AS entries
            WHERE entries.object_type = 'file'
            ORDER BY entries.endpoint_id, entries.relative_path
            """
        ).fetchall()
        roots = {SOURCE_ENDPOINT_ID: source, TARGET_ENDPOINT_ID: target}
        evidence = tuple(
            LocalCurrentReadHasher().hash_file(
                CurrentReadHashRequest(
                    snapshot_id=str(row[1]),
                    entry_id=str(row[0]),
                    endpoint_id=str(row[2]),
                    root=roots[str(row[2])],
                    relative_path=str(row[3]),
                    expected_size_bytes=int(row[4]),
                    computed_utc="2026-08-02T11:00:00Z",
                )
            )
            for row in rows
        )
        SqliteCurrentReadHashEvidenceRefresher(
            connection
        ).persist_current_read_hash_evidence(
            analysis_id="analysis-a",
            evidence=evidence,
        )
        plan_report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(
            observed_utc="2026-08-02T11:00:00.500Z",
        )
        plan_id = plan_report.results[0].plan_id
        assert plan_id is not None
        relations = SqliteDuplicateRelationStore(connection)
        report = relations.materialize_known_duplicate_relations(
            analysis_id="analysis-a",
            observed_utc="2026-08-02T11:00:01Z",
        )
        summary = relations.load_duplicate_analysis_summary("analysis-a")
        scanner = SqliteDuplicateScanner(connection)

        assert report.expected_replica_group_count == 0
        assert report.internal_duplicate_group_count == 0
        assert report.cross_endpoint_duplicate_group_count == 1
        assert report.cross_endpoint_duplicate_file_count == 2
        assert summary is not None
        assert summary.cross_endpoint_duplicate_group_count == 1
        assert summary.cross_endpoint_duplicate_file_count == 2
        assert summary.potential_savings_bytes == len(content)

        groups = scanner.page_duplicate_groups(
            analysis_id="analysis-a",
            limit=10,
            relationship_classes=("UNRELATED_CROSS_ENDPOINT_DUPLICATE",),
        )
        assert len(groups.groups) == 1
        group = groups.groups[0]
        assert group.physical_object_count == 2
        assert group.potential_savings_bytes == len(content)
        filtered_operations = SqlitePlanStore(connection).page_plan_operations(
            PlanOperationPageQuery(
                plan_id=plan_id,
                limit=100,
                duplicate_group_id=group.group_id,
            )
        )
        assert filtered_operations.operations
        assert {
            operation.target_relative_path
            for operation in filtered_operations.operations
        } <= {"Original.bin", "Elsewhere.bin"}
        members = scanner.page_duplicate_members(group_id=group.group_id, limit=10)
        assert {member.endpoint_role for member in members.members} == {
            "SOURCE",
            "TARGET",
        }
        assert {Path(member.absolute_path) for member in members.members} == {
            source_path,
            target_path,
        }
        assert {member.evidence_kind for member in members.members} == {
            "CURRENT_READ_HASH"
        }

        first_page = scanner.page_duplicate_report(
            analysis_id="analysis-a",
            limit=1,
        )
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        second_page = scanner.page_duplicate_report(
            analysis_id="analysis-a",
            limit=1,
            after=first_page.next_cursor,
        )
        assert second_page.has_more is False
        assert len(first_page.rows) + len(second_page.rows) == 2
        assert {
            item.group.relationship_class
            for item in (*first_page.rows, *second_page.rows)
        } == {"UNRELATED_CROSS_ENDPOINT_DUPLICATE"}

        reviewed = scanner.mark_duplicate_group_reviewed(
            group_id=group.group_id,
            expected_review_state="UNREVIEWED",
        )
        assert reviewed is not None and reviewed.review_state == "REVIEWED"
        assert (
            scanner.mark_duplicate_group_reviewed(
                group_id=group.group_id,
                expected_review_state="UNREVIEWED",
            )
            is None
        )

    for path, expected in before.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == expected


def test_duplicate_scan_resumes_bounded_real_file_hashing_and_pages_results(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    duplicate_content = b"duplicate scan reads these complete bytes"
    first_path = source / "First.bin"
    second_path = source / "Second.bin"
    unique_path = source / "Unique.bin"
    first_path.write_bytes(duplicate_content)
    second_path.write_bytes(duplicate_content)
    unique_path.write_bytes(b"different bytes with the same byte length!!!")
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (first_path, second_path, unique_path)
    }

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        scanner = SqliteDuplicateScanner(
            connection,
            work_batch_size=1,
            max_persisted_requests=2,
        )
        started = scanner.start_scan(
            analysis_id="analysis-a",
            requested_utc="2026-07-31T16:02:45Z",
        )

        assert started.state.value == "QUEUED"
        assert started.candidate_file_count == 2
        first_cycle = scanner.run_cycle(
            observed_utc="2026-07-31T16:02:46Z",
            max_files=1,
        )
        assert first_cycle.files_attempted == 1
        interrupted_id = connection.execute(
            """
            SELECT id
            FROM hash_requests
            WHERE state = 'SUCCEEDED'
            LIMIT 1
            """
        ).fetchone()
        assert interrupted_id is not None
        connection.execute(
            """
            UPDATE hash_requests
            SET state = 'RUNNING', completed_utc = NULL
            WHERE id = ?
            """,
            (str(interrupted_id[0]),),
        )
        connection.commit()

        restarted = SqliteDuplicateScanner(
            connection,
            work_batch_size=1,
            max_persisted_requests=2,
        )
        assert restarted.recover_interrupted_requests(
            observed_utc="2026-07-31T16:02:47Z"
        ) == 1
        latest = restarted.load_duplicate_scan("analysis-a")
        for cycle_no in range(20):
            if latest is not None and latest.terminal:
                break
            restarted.run_cycle(
                observed_utc=f"2026-07-31T16:03:{cycle_no:02d}Z",
                max_files=1,
            )
            latest = restarted.load_duplicate_scan("analysis-a")

        assert latest is not None
        assert latest.state.value == "COMPLETED"
        assert latest.stage.value == "DONE"
        assert latest.candidate_file_count == 2
        assert latest.quick_completed_count == 2
        assert latest.full_hash_candidate_count == 2
        assert latest.full_hash_completed_count == 2
        assert latest.issue_count == 0
        assert connection.execute("SELECT count(*) FROM hash_requests").fetchone() == (
            0,
        )
        assert connection.execute(
            "SELECT count(*) FROM current_read_hash_evidence"
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT evidence_kind, active, count(*)
            FROM hash_cache
            GROUP BY evidence_kind, active
            ORDER BY evidence_kind, active
            """
        ).fetchall() == [
            ("CURRENT_READ_HASH", 1, 2),
            ("QUICK_SIGNATURE_ONLY", 0, 2),
        ]

        group_page = restarted.page_duplicate_groups(
            analysis_id="analysis-a",
            limit=1,
            relationship_classes=("INTRA_ENDPOINT_DUPLICATE",),
        )
        assert len(group_page.groups) == 1
        assert group_page.has_more is False
        group = group_page.groups[0]
        assert group.physical_object_count == 2
        assert group.potential_savings_bytes == len(duplicate_content)
        member_page = restarted.page_duplicate_members(
            group_id=group.group_id,
            limit=1,
        )
        assert len(member_page.members) == 1
        assert member_page.has_more is True
        assert member_page.next_cursor is not None
        remaining = restarted.page_duplicate_members(
            group_id=group.group_id,
            limit=1,
            after=member_page.next_cursor,
        )
        assert len(remaining.members) == 1
        assert remaining.has_more is False

    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (first_path, second_path, unique_path)
    } == before
    assert [path for path in target.iterdir() if path.name != ".mediasync"] == []


def test_duplicate_scan_pauses_for_active_backup_and_resumes_automatically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "A.bin").write_bytes(b"matching")
    (source / "B.bin").write_bytes(b"matching")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        scanner = SqliteDuplicateScanner(connection, work_batch_size=1)
        scanner.start_scan(
            analysis_id="analysis-a",
            requested_utc="2026-07-31T16:02:45Z",
        )

        paused = scanner.run_cycle(
            observed_utc="2026-07-31T16:02:46Z",
            active_backup=True,
            max_files=1,
        )
        resumed = scanner.run_cycle(
            observed_utc="2026-07-31T16:02:47Z",
            active_backup=False,
            max_files=1,
        )

        assert paused.scan is not None
        assert paused.scan.state.value == "PAUSED"
        assert paused.scan.reason_code == "ACTIVE_BACKUP"
        assert paused.files_attempted == 0
        assert resumed.scan is not None
        assert resumed.scan.state.value == "RUNNING"
        assert resumed.files_attempted == 1


def test_duplicate_scan_full_hash_rejects_matching_quick_signature_collision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    prefix = b"a" * (1024 * 1024)
    suffix = b"z" * (1024 * 1024)
    (source / "A.bin").write_bytes(prefix + b"x" * (1024 * 1024) + suffix)
    (source / "B.bin").write_bytes(prefix + b"y" * (1024 * 1024) + suffix)

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        scanner = SqliteDuplicateScanner(connection)
        scanner.start_scan(
            analysis_id="analysis-a",
            requested_utc="2026-07-31T16:02:45Z",
        )
        latest = scanner.load_duplicate_scan("analysis-a")
        for cycle_no in range(10):
            if latest is not None and latest.terminal:
                break
            scanner.run_cycle(
                observed_utc=f"2026-07-31T16:03:{cycle_no:02d}Z"
            )
            latest = scanner.load_duplicate_scan("analysis-a")

        assert latest is not None
        assert latest.state.value == "COMPLETED"
        assert latest.full_hash_candidate_count == 2
        hashes = connection.execute(
            """
            SELECT content_hash
            FROM current_read_hash_evidence
            ORDER BY entry_id
            """
        ).fetchall()
        assert len(hashes) == 2
        assert hashes[0] != hashes[1]
        assert connection.execute(
            "SELECT count(*) FROM duplicate_groups"
        ).fetchone() == (0,)


def test_duplicate_relations_do_not_collapse_hint_only_file_ids(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_file = source / "Original.bin"
    source_file.write_bytes(b"same object")
    os.link(source_file, source / "Alias.bin")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        row = connection.execute(
            """
            SELECT read_capabilities_json
            FROM endpoint_classification_observations
            WHERE endpoint_id = ?
            """,
            (SOURCE_ENDPOINT_ID,),
        ).fetchone()
        assert row is not None
        hinted = EndpointCapabilityEvidence.from_profile(
            replace(
                EndpointCapabilities.from_json(str(row[0])),
                file_id_reliability=FileIdReliability.HINT,
            )
        )
        connection.execute(
            """
            UPDATE endpoint_classification_observations
            SET
                read_capabilities_json = ?,
                read_capabilities_hash = ?
            WHERE endpoint_id = ?
            """,
            (
                hinted.profile_json,
                hinted.capabilities_hash,
                SOURCE_ENDPOINT_ID,
            ),
        )
        connection.commit()

        report = SqliteDuplicateRelationStore(
            connection
        ).materialize_known_duplicate_relations(
            analysis_id="analysis-a",
            observed_utc="2026-07-31T16:02:46Z",
        )

        assert report.alias_group_count == 0
        assert report.alias_path_count == 0
        assert connection.execute(
            "SELECT count(*) FROM file_object_alias_groups"
        ).fetchone() == (0,)


def test_incomplete_coverage_evidence_blocks_persisted_replacement_plan(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Readme.txt").write_text("new", encoding="utf-8")
    (target / "Readme.txt").write_text("old", encoding="utf-8")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        # Simulate persisted coverage corruption past the normal immutable guard.
        connection.execute(
            "DROP TRIGGER trg_directory_coverage_no_update_after_snapshot_immutable"
        )
        connection.execute(
            """
            UPDATE directory_coverage
            SET coverage_state = 'UNREADABLE'
            WHERE snapshot_id = 'snapshot-source'
                AND relative_path = '.'
            """
        )
        connection.commit()
        plans = SqlitePlanStore(connection)

        report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=plans,
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(
            observed_utc="2026-07-31T16:03:00Z",
        )

        assert report.sealed_plan_count == 1
        assert report.results[0].plan_runnable is False
        plan = plans.load_sealed_plan("plan-1")
        assert plan is not None
        assert plan.operation_count == 1
        operation = plan.operations[0]
        assert (
            operation.operation_type
            is PlanOperationType.BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN
        )
        assert operation.target_precondition_kind is TargetPreconditionKind.NONE
        assert operation.reason_code == "DESTRUCTIVE_SCAN_COVERAGE_INCOMPLETE"


def test_snapshot_endpoint_identity_drift_blocks_plan_materialization(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Readme.txt").write_text("new", encoding="utf-8")
    (target / "Readme.txt").write_text("old", encoding="utf-8")

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target,
        )
        # Simulate identity drift past the normal immutable catalog guards.
        connection.execute("DROP TRIGGER trg_snapshots_no_update_after_immutable")
        connection.execute("DROP TRIGGER trg_snapshots_endpoint_identity_immutable")
        connection.execute(
            """
            UPDATE snapshots
            SET endpoint_generation = endpoint_generation + 1
            WHERE id = 'snapshot-target'
            """
        )
        connection.commit()

        report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=SqlitePlanStore(connection),
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(
            observed_utc="2026-07-31T16:03:00Z",
        )

        assert report.blocked_job_count == 1
        assert report.results[0].state == "BLOCKED"
        assert report.results[0].reason_code == (
            "INITIAL_BACKUP_PLAN_ENDPOINT_SNAPSHOT_IDENTITY_MISMATCH"
        )
        assert connection.execute("SELECT count(*) FROM plans").fetchone() == (0,)


def test_initial_plan_materializer_persists_operations_for_two_targets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    source = tmp_path / "source"
    target_a = tmp_path / "target-a"
    target_b = tmp_path / "target-b"
    (source / "Photos").mkdir(parents=True)
    (source / "Photos" / "A.jpg").write_bytes(b"photo")
    (target_a / "Photos").mkdir(parents=True)
    target_b.mkdir()

    with sqlite3.connect(database) as connection:
        _prepare_registered_snapshots(
            connection,
            database=database,
            source=source,
            target=target_a,
            additional_target=target_b,
        )
        plans = SqlitePlanStore(connection)
        report = SqliteInitialBackupPlanMaterializer(
            connection,
            plans=plans,
            id_factory=_FixedPlanIds(),
        ).refresh_initial_backup_plans(
            observed_utc="2026-07-31T17:03:00Z",
        )

        assert report.sealed_plan_count == 1
        plan = plans.load_sealed_plan("plan-1")
        assert plan is not None
        assert [
            (
                operation.target_endpoint_id,
                operation.target_relative_path,
            )
            for operation in plan.operations
        ] == [
            (TARGET_ENDPOINT_ID, "Photos/A.jpg"),
            (TARGET_B_ENDPOINT_ID, "Photos"),
            (TARGET_B_ENDPOINT_ID, "Photos/A.jpg"),
        ]
        target_counts = {
            endpoint.endpoint_id: endpoint.planned_operations
            for endpoint in plan.endpoints
            if endpoint.role.value == "TARGET_WRITABLE"
        }
        assert target_counts == {
            TARGET_ENDPOINT_ID: 1,
            TARGET_B_ENDPOINT_ID: 2,
        }


def _prepare_registered_snapshots(
    connection: sqlite3.Connection,
    *,
    database: Path,
    source: Path,
    target: Path,
    additional_target: Path | None = None,
    capabilities_probe: EndpointCapabilitiesProbe | None = None,
) -> None:
    _prepare_catalog(
        connection,
        database=database,
        source=source,
        target=target,
        additional_target=additional_target,
    )
    refresher = SqliteEndpointClassificationRefresher(
        connection,
        classifier=LocalEndpointControlAreaClassifier(),
        local_installation_id=INSTALLATION_ID,
    )
    refresher.refresh_endpoint_classifications(
        observed_utc="2026-07-31T15:00:00Z",
    )
    WritableEndpointRegistrationCoordinator(
        store=SqliteWritableEndpointRegistrationStore(connection),
        provisioner=LocalWritableEndpointControlAreaProvisioner(
            capabilities_probe=capabilities_probe,
        ),
        root_overlap_guard=LocalWritableEndpointRootOverlapGuard(),
        id_factory=_FixedRegistrationIds(),
        owner_installation_id=INSTALLATION_ID,
    ).register_job_targets(
        job_id=JOB_ID,
        job_revision_id=JOB_REVISION_ID,
        command_request_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        command_idempotency_key="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        observed_utc="2026-07-31T15:01:00Z",
    )
    refresher.refresh_endpoint_classifications(
        observed_utc="2026-07-31T15:02:00Z",
    )
    snapshots = SqliteSnapshotEntryStore(connection)
    report = SqliteJobSnapshotMaterializer(
        connection,
        scanner=LocalFilesystemSnapshotScanner(
            case_mode_probe=_FixedCaseModeProbe(),
        ),
        id_factory=_FixedSnapshotIds(),
        entry_store=snapshots,
        seal_store=snapshots,
    ).refresh_job_snapshots(
        observed_utc="2026-07-31T15:02:30Z",
    )
    assert report.scanned_job_count == 1
    assert report.sealed_snapshot_count == (3 if additional_target is not None else 2)


def _prepare_catalog(
    connection: sqlite3.Connection,
    *,
    database: Path,
    source: Path,
    target: Path,
    additional_target: Path | None = None,
) -> None:
    apply_sqlite_connection_policy(
        connection,
        catalog_critical_writer_policy(database),
    )
    apply_sqlite_migrations(connection, catalog_migration_plan())
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES (?, 'multi_target_backup')",
        (JOB_ID,),
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES (?, 'filter-a')",
        (JOB_ID,),
    )
    insert_default_filter_set_version(
        connection,
        job_id=JOB_ID,
        filter_set_id="filter-a",
    )
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id, filter_set_version)
        VALUES (?, ?, 'filter-a', 1)
        """,
        (JOB_ID, JOB_REVISION_ID),
    )
    defaults_json = (
        '{"behavior":"UPDATE_BACKUP","extra_files":"KEEP_ON_TARGET",'
        '"file_selection":"ALL_USER_FILES","performance":"AUTO",'
        '"retention":"THIRTY_DAYS","verification":"STANDARD"}'
    )
    target_payloads = [
        {
            "independent_device_id": None,
            "name": "Target",
            "path_label": str(target),
        }
    ]
    if additional_target is not None:
        target_payloads.append(
            {
                "independent_device_id": None,
                "name": "Target B",
                "path_label": str(additional_target),
            }
        )
    targets_json = json.dumps(
        target_payloads,
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_drafts (
            draft_id,
            schema_version,
            source_name,
            source_path_label,
            defaults_json,
            targets_json
        )
        VALUES ('draft-a', 1, 'Source', ?, ?, ?)
        """,
        (str(source), defaults_json, targets_json),
    )
    connection.execute(
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
        VALUES (?, ?, 'draft-a', 'request-a', 'create-a', 'Source', ?, ?, ?)
        """,
        (
            JOB_ID,
            JOB_REVISION_ID,
            str(source),
            defaults_json,
            targets_json,
        ),
    )
    connection.execute(
        "INSERT INTO job_heads (job_id, active_revision_id) VALUES (?, ?)",
        (JOB_ID, JOB_REVISION_ID),
    )
    _insert_endpoint(
        connection,
        endpoint_id=SOURCE_ENDPOINT_ID,
        endpoint_revision_id=SOURCE_REVISION_ID,
        root=source,
        role="SOURCE",
        ordinal=0,
    )
    _insert_endpoint(
        connection,
        endpoint_id=TARGET_ENDPOINT_ID,
        endpoint_revision_id=TARGET_REVISION_ID,
        root=target,
        role="TARGET",
        ordinal=1,
    )
    if additional_target is not None:
        _insert_endpoint(
            connection,
            endpoint_id=TARGET_B_ENDPOINT_ID,
            endpoint_revision_id=TARGET_B_REVISION_ID,
            root=additional_target,
            role="TARGET",
            ordinal=2,
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
) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES (?)", (endpoint_id,))
    connection.execute(
        """
        INSERT INTO endpoint_revisions (
            endpoint_id,
            id,
            display_name,
            root_uri,
            generation
        )
        VALUES (?, ?, ?, ?, 1)
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
            registration_state,
            registration_reason_code
        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            'REGISTRATION_PENDING',
            'ENDPOINT_CLASSIFICATION_PENDING'
        )
        """,
        (
            JOB_ID,
            JOB_REVISION_ID,
            role,
            ordinal,
            endpoint_id,
            endpoint_revision_id,
        ),
    )
