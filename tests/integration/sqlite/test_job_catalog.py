from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.job_catalog import (
    SqliteJobCatalogError,
    SqliteStandardBackupJobCatalog,
)
from mediasync_home.adapters.sqlite.job_draft_store import SqliteJobDraftStore
from mediasync_home.adapters.sqlite.external_resources import (
    SqliteExternalResourceStateStore,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.schedules import SqliteScheduleStore
from mediasync_home.application.external_resources import ExternalResourceType
from mediasync_home.application.job_creation import (
    CreateStandardBackupJobCommand,
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
    create_standard_backup_job_from_draft,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.job_scheduling import daily_backup_schedule_id
from mediasync_home.application.schedules import ScheduleDefinition
from mediasync_home.application.trigger_occurrences import TriggerKind


class FixedStandardBackupJobIdFactory(StandardBackupJobIdFactory):
    def __init__(
        self,
        ids: tuple[StandardBackupJobIds, ...] = (
            StandardBackupJobIds(
                job_id="job-a",
                job_revision_id="job-rev-a",
                filter_set_id="filter-a",
            ),
        ),
    ) -> None:
        self.calls = 0
        self._ids = ids

    def new_standard_backup_job_ids(self) -> StandardBackupJobIds:
        ids = self._ids[min(self.calls, len(self._ids) - 1)]
        self.calls += 1
        return ids


def test_sqlite_catalog_persists_standard_backup_job_from_draft(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        draft = _complete_draft()
        drafts.save_standard_backup_draft(draft)

        outcome = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )

        assert outcome.created is True
        assert outcome.job is not None
        assert catalog.load_standard_backup_job("job-a") == outcome.job
        assert catalog.load_standard_backup_job_by_idempotency_key("idempotency-a") == outcome.job
        assert _row_count(connection, "jobs") == 1
        assert _row_count(connection, "job_revisions") == 1
        assert _row_count(connection, "filter_set_versions") == 1
        assert _row_count(connection, "job_revision_filter_bindings") == 1
        assert _row_count(connection, "job_heads") == 1
        assert _row_count(connection, "standard_backup_job_revision_details") == 1
        assert connection.execute(
            """
            SELECT version, rules_hash, rules_json
            FROM filter_set_versions
            WHERE job_id = 'job-a' AND filter_set_id = 'filter-a'
            """
        ).fetchone() == (
            1,
            "5b551f66adfe79a9e025a369c44e76ece00928588f965a93fe6cdcfbdb1e4a9b",
            '{"preset":"ALL_USER_FILES","schema_version":1}',
        )
        assert connection.execute(
            """
            SELECT filter_set_id, filter_set_version
            FROM job_revision_filter_bindings
            WHERE job_id = 'job-a' AND job_revision_id = 'job-rev-a'
            """
        ).fetchone() == ("filter-a", 1)
        assert _scalar(connection, "SELECT active_revision_id FROM job_heads WHERE job_id = 'job-a'") == "job-rev-a"
        assert id_factory.calls == 1


def test_sqlite_catalog_replays_standard_backup_job_idempotency_key(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        drafts.save_standard_backup_draft(_complete_draft())
        command = _create_command()

        first = create_standard_backup_job_from_draft(
            command=command,
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )
        second = create_standard_backup_job_from_draft(
            command=command,
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )

        assert first.created is True
        assert second.created is False
        assert second.idempotent_replay is True
        assert second.job == first.job
        assert _row_count(connection, "standard_backup_job_revision_details") == 1
        assert id_factory.calls == 1


def test_sqlite_catalog_does_not_create_job_from_overlapping_roots(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        drafts.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-a")
            .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
            .with_added_target(name="Nested target", path_label="C:/Users/Ada/Pictures/Phone")
        )

        outcome = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )

        assert outcome.created is False
        assert outcome.job is None
        assert outcome.readiness.validation_codes == ("TARGET_ROOT_OVERLAPS_SOURCE",)
        assert _row_count(connection, "jobs") == 0
        assert id_factory.calls == 0


def test_sqlite_catalog_blocks_overlap_with_existing_writable_root(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        id_factory = FixedStandardBackupJobIdFactory()
        drafts.save_standard_backup_draft(_complete_draft())
        first = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )
        assert first.created is True
        drafts.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-b")
            .with_source(name="Camera", path_label="D:/Camera")
            .with_added_target(name="Nested target", path_label="E:/Backup/Phone")
        )

        blocked = create_standard_backup_job_from_draft(
            command=parse_create_standard_backup_job_command(
                request_id="request-b",
                idempotency_key="idempotency-b",
                payload={"draft_id": "draft-b"},
            ),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )

        assert blocked.created is False
        assert blocked.job is None
        assert blocked.readiness.validation_codes == ("STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB",)
        assert _row_count(connection, "jobs") == 1
        assert id_factory.calls == 1


def test_sqlite_catalog_allows_source_only_overlap_with_existing_job(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        id_factory = FixedStandardBackupJobIdFactory(
            (
                StandardBackupJobIds(
                    job_id="job-a",
                    job_revision_id="job-rev-a",
                    filter_set_id="filter-a",
                ),
                StandardBackupJobIds(
                    job_id="job-b",
                    job_revision_id="job-rev-b",
                    filter_set_id="filter-b",
                ),
            )
        )
        drafts.save_standard_backup_draft(_complete_draft())
        first = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )
        assert first.created is True
        drafts.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-b")
            .with_source(name="Pictures child", path_label="C:/Users/Ada/Pictures/Phone")
            .with_added_target(name="USB 2", path_label="F:/Backup")
        )

        second = create_standard_backup_job_from_draft(
            command=parse_create_standard_backup_job_command(
                request_id="request-b",
                idempotency_key="idempotency-b",
                payload={"draft_id": "draft-b"},
            ),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )

        assert second.created is True
        assert second.job is not None
        assert second.job.job_id == "job-b"
        assert _row_count(connection, "jobs") == 2
        assert tuple(job.job_id for job in catalog.list_active_standard_backup_jobs()) == ("job-a", "job-b")
        assert id_factory.calls == 2


def test_sqlite_catalog_lists_active_standard_backup_job_summaries_page(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        id_factory = FixedStandardBackupJobIdFactory(
            (
                StandardBackupJobIds(
                    job_id="job-a",
                    job_revision_id="job-rev-a",
                    filter_set_id="filter-a",
                ),
                StandardBackupJobIds(
                    job_id="job-b",
                    job_revision_id="job-rev-b",
                    filter_set_id="filter-b",
                ),
            )
        )
        drafts.save_standard_backup_draft(_complete_draft())
        create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )
        drafts.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-b")
            .with_source(name="Documents", path_label="C:/Users/Ada/Documents")
            .with_added_target(name="USB 2", path_label="F:/Backup", independent_device_id="disk-b")
        )
        create_standard_backup_job_from_draft(
            command=parse_create_standard_backup_job_command(
                request_id="request-b",
                idempotency_key="idempotency-b",
                payload={"draft_id": "draft-b"},
            ),
            drafts=drafts,
            catalog=catalog,
            id_factory=id_factory,
        )

        summaries = catalog.list_active_standard_backup_job_summaries(limit=1, offset=1)

        assert [summary.job_id for summary in summaries] == ["job-b"]
        assert summaries[0].source_name == "Documents"
        assert summaries[0].targets[0].independent_device_id == "disk-b"


def test_sqlite_catalog_loads_active_standard_backup_job_detail(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        drafts.save_standard_backup_draft(_complete_draft())
        create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=FixedStandardBackupJobIdFactory(),
        )

        detail = catalog.load_standard_backup_job_detail("job-a")
        missing = catalog.load_standard_backup_job_detail("job-missing")

        assert detail is not None
        assert detail.job_id == "job-a"
        assert detail.job_revision_id == "job-rev-a"
        assert detail.filter_set_id == "filter-a"
        assert detail.filter_set_version == 1
        assert detail.source_path_label == "C:/Users/Ada/Pictures"
        assert detail.defaults.retention.value == "THIRTY_DAYS"
        assert detail.targets[0].name == "USB 1"
        assert detail.targets[0].independent_device_id == "disk-a"
        assert missing is None


def test_sqlite_catalog_loads_daily_schedule_reconciliation_summary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        drafts.save_standard_backup_draft(_complete_draft())
        create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=FixedStandardBackupJobIdFactory(),
        )
        _insert_sealed_plan(connection)
        schedule_id = daily_backup_schedule_id("job-a")
        schedule = ScheduleDefinition(
            schedule_id=schedule_id,
            job_id="job-a",
            plan_id="plan-a",
            plan_checksum="a" * 64,
            trigger_type=TriggerKind.SCHEDULED_TIME,
            configuration_json=(
                '{"days_interval":1,"hour":21,"kind":"daily","minute":30}'
            ),
            definition_generation=2,
            desired_definition_hash="b" * 64,
            time_zone_id="W. Europe Standard Time",
            dst_policy="PRESERVE_WALL_TIME",
            misfire_policy="QUEUE_ONCE",
            coalescing_window_seconds=60,
            task_logon_type="INTERACTIVE_TOKEN",
            requires_network=True,
            run_only_when_logged_on=True,
            enabled=True,
            row_version=2,
        )
        SqliteScheduleStore(connection).save_schedule(schedule)
        SqliteExternalResourceStateStore(connection).upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id=schedule_id,
            desired_generation=2,
            desired_hash="b" * 64,
        )

        detail = catalog.load_standard_backup_job_detail("job-a")

        assert detail is not None
        assert detail.automation_schedule is not None
        assert detail.automation_schedule.schedule_id == schedule_id
        assert detail.automation_schedule.daily_local_time == "21:30"
        assert detail.automation_schedule.enabled is True
        assert detail.automation_schedule.row_version == 2
        assert detail.automation_schedule.time_zone_id == "W. Europe Standard Time"
        assert detail.automation_schedule.task_logon_type == "INTERACTIVE_TOKEN"
        assert detail.automation_schedule.requires_network is True
        assert detail.automation_schedule.run_only_when_logged_on is True
        assert detail.automation_schedule.reconciliation_state == "PENDING"


def test_sqlite_catalog_appends_immutable_standard_backup_job_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        drafts.save_standard_backup_draft(_complete_draft())
        created = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=FixedStandardBackupJobIdFactory(),
        )
        assert created.job is not None
        edit_draft = replace(
            _complete_draft(),
            draft_id="draft-edit-a",
            source_name="Pictures renamed",
        )
        drafts.save_standard_backup_draft(edit_draft)
        edited = replace(
            created.job,
            job_revision_id="job-rev-b",
            draft_id=edit_draft.draft_id,
            command_request_id="request-b",
            idempotency_key="idempotency-b",
            source_name="Pictures renamed",
        )

        catalog.append_standard_backup_job_revision(
            edited,
            expected_active_revision_id="job-rev-a",
        )

        assert catalog.load_standard_backup_job("job-a") == edited
        assert catalog.load_standard_backup_job_revision(
            job_id="job-a",
            job_revision_id="job-rev-a",
        ) == created.job
        assert catalog.load_standard_backup_job_revision(
            job_id="job-a",
            job_revision_id="job-rev-b",
        ) == edited
        assert _row_count(connection, "job_revisions") == 2
        assert _row_count(connection, "standard_backup_job_revision_details") == 2
        assert _row_count(connection, "filter_set_versions") == 1
        assert _scalar(
            connection,
            "SELECT active_revision_id FROM job_heads WHERE job_id = 'job-a'",
        ) == "job-rev-b"


def test_sqlite_catalog_revision_append_rejects_stale_active_head(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        drafts.save_standard_backup_draft(_complete_draft())
        created = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=FixedStandardBackupJobIdFactory(),
        )
        assert created.job is not None
        edit_draft = replace(_complete_draft(), draft_id="draft-edit-a")
        drafts.save_standard_backup_draft(edit_draft)

        with pytest.raises(
            SqliteJobCatalogError,
            match="STANDARD_BACKUP_JOB_REVISION_STALE",
        ):
            catalog.append_standard_backup_job_revision(
                replace(
                    created.job,
                    job_revision_id="job-rev-b",
                    draft_id=edit_draft.draft_id,
                    command_request_id="request-b",
                    idempotency_key="idempotency-b",
                ),
                expected_active_revision_id="job-rev-stale",
            )

        assert _row_count(connection, "job_revisions") == 1
        assert _row_count(connection, "standard_backup_job_revision_details") == 1


def test_sqlite_catalog_direct_save_rejects_cross_job_root_overlap(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        drafts = SqliteJobDraftStore(connection)
        catalog = SqliteStandardBackupJobCatalog(connection)
        drafts.save_standard_backup_draft(_complete_draft())
        existing = create_standard_backup_job_from_draft(
            command=_create_command(),
            drafts=drafts,
            catalog=catalog,
            id_factory=FixedStandardBackupJobIdFactory(),
        )
        assert existing.created is True
        drafts.save_standard_backup_draft(
            StandardBackupJobDraft.new("draft-b")
            .with_source(name="Camera", path_label="D:/Camera")
            .with_added_target(name="Nested target", path_label="E:/Backup/Phone")
        )

        with pytest.raises(SqliteJobCatalogError, match="STANDARD_BACKUP_JOB_ROOT_OVERLAP"):
            catalog.save_standard_backup_job(_sealed_job_b())

        assert _row_count(connection, "jobs") == 1


def test_sqlite_catalog_requires_source_draft_row(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        catalog = SqliteStandardBackupJobCatalog(connection)

        with pytest.raises(SqliteJobCatalogError, match="STANDARD_BACKUP_JOB_PERSISTENCE_FAILED"):
            catalog.save_standard_backup_job(
                SealedStandardBackupJob(
                    job_id="job-a",
                    job_revision_id="job-rev-a",
                    filter_set_id="filter-a",
                    draft_id="missing-draft",
                    command_request_id="request-a",
                    idempotency_key="idempotency-a",
                    source_name="Pictures",
                    source_path_label="C:/Users/Ada/Pictures",
                    targets=(),
                    defaults=StandardBackupJobDraft.new("draft-a").defaults,
                )
            )


def test_sqlite_catalog_requires_initial_filter_version(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        catalog = SqliteStandardBackupJobCatalog(connection)

        with pytest.raises(
            SqliteJobCatalogError,
            match="FILTER_SET_INITIAL_VERSION_INVALID",
        ):
            catalog.save_standard_backup_job(
                replace(_sealed_job_b(), filter_set_version=2)
            )

        assert _row_count(connection, "jobs") == 0


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_sealed_plan(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
        VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute(
        "INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')"
    )
    connection.execute(
        """
        INSERT INTO plan_seal_details (
            plan_id,
            analysis_id,
            job_id,
            job_revision_id,
            planner_version,
            plan_schema_version,
            operation_schema_version,
            execution_policy,
            checksum_algorithm,
            serializer_version,
            plan_checksum,
            risk_summary_json,
            operation_count,
            planned_bytes
        )
        VALUES (
            'plan-a',
            'analysis-a',
            'job-a',
            'job-rev-a',
            'planner',
            1,
            1,
            'dry-run',
            'SHA-256',
            'canonical-json',
            ?,
            '{}',
            1,
            0
        )
        """,
        ("a" * 64,),
    )


def _complete_draft() -> StandardBackupJobDraft:
    return (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )


def _create_command() -> CreateStandardBackupJobCommand:
    return parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-a"},
    )


def _sealed_job_b() -> SealedStandardBackupJob:
    return SealedStandardBackupJob(
        job_id="job-b",
        job_revision_id="job-rev-b",
        filter_set_id="filter-b",
        draft_id="draft-b",
        command_request_id="request-b",
        idempotency_key="idempotency-b",
        source_name="Camera",
        source_path_label="D:/Camera",
        targets=(
            _target(
                name="Nested target",
                path_label="E:/Backup/Phone",
            ),
        ),
        defaults=StandardBackupJobDraft.new("draft-b").defaults,
    )


def _target(*, name: str, path_label: str) -> SealedStandardBackupTarget:
    return SealedStandardBackupTarget(
        name=name,
        path_label=path_label,
    )


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _scalar(connection: sqlite3.Connection, query: str) -> object:
    row = connection.execute(query).fetchone()
    assert row is not None
    return row[0]
