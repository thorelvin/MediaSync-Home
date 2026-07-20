from __future__ import annotations

import sqlite3
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
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.application.job_creation import (
    CreateStandardBackupJobCommand,
    SealedStandardBackupJob,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
    create_standard_backup_job_from_draft,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_drafts import StandardBackupJobDraft


class FixedStandardBackupJobIdFactory(StandardBackupJobIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_standard_backup_job_ids(self) -> StandardBackupJobIds:
        self.calls += 1
        return StandardBackupJobIds(
            job_id="job-a",
            job_revision_id="job-rev-a",
            filter_set_id="filter-a",
        )


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
        assert _row_count(connection, "job_heads") == 1
        assert _row_count(connection, "standard_backup_job_revision_details") == 1
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


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


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


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _scalar(connection: sqlite3.Connection, query: str) -> object:
    row = connection.execute(query).fetchone()
    assert row is not None
    return row[0]
