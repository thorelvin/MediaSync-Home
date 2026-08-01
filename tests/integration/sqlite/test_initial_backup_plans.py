from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.local_snapshot_scanner import (
    LocalFilesystemSnapshotScanner,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
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
)
from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanIdFactory,
)
from mediasync_home.application.endpoint_capabilities import (
    DurabilityLevel,
    EndpointCapabilitiesProbe,
    EndpointCapabilityEvidence,
)
from mediasync_home.application.plans import (
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
