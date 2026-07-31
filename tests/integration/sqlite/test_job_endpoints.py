from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.job_endpoints import (
    SqliteJobEndpointRegistrationError,
    SqliteStandardBackupJobEndpointRegistrar,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.application.job_creation import (
    SealedStandardBackupJob,
    SealedStandardBackupTarget,
)
from mediasync_home.application.job_drafts import StandardBackupDefaults
from mediasync_home.application.job_endpoints import (
    EndpointIdFactory,
    EndpointIds,
    EndpointRegistrationState,
    JobEndpointRole,
)


class FixedEndpointIdFactory(EndpointIdFactory):
    def __init__(self) -> None:
        self.calls = 0

    def new_endpoint_ids(self) -> EndpointIds:
        self.calls += 1
        return EndpointIds(
            endpoint_id=f"endpoint-{self.calls}",
            endpoint_revision_id=f"endpoint-revision-{self.calls}",
        )


def test_registrar_persists_pending_source_and_target_bindings(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        job = _job()
        _insert_job_revision(connection, job)
        id_factory = FixedEndpointIdFactory()
        registrar = SqliteStandardBackupJobEndpointRegistrar(
            connection,
            id_factory=id_factory,
        )

        registered = registrar.register_standard_backup_job_endpoints(job)
        replay = registrar.register_standard_backup_job_endpoints(job)

        assert registered == replay
        assert registered.source.role is JobEndpointRole.SOURCE
        assert registered.source.ordinal == 0
        assert registered.source.endpoint_generation == 1
        assert registered.source.root_uri == "file:///C:/Users/Ada/Pictures"
        assert registered.targets[0].role is JobEndpointRole.TARGET
        assert registered.targets[0].ordinal == 1
        assert registered.targets[0].endpoint_generation == 1
        assert registered.targets[0].root_uri == "file:///E:/Backup"
        assert all(
            binding.registration_state is EndpointRegistrationState.REGISTRATION_PENDING
            for binding in registered.all_bindings
        )
        assert _row_count(connection, "endpoints") == 2
        assert _row_count(connection, "endpoint_revisions") == 2
        assert _row_count(connection, "endpoint_heads") == 2
        assert _row_count(connection, "endpoint_root_claims") == 2
        assert _row_count(connection, "standard_backup_job_endpoint_bindings") == 2
        assert id_factory.calls == 2


def test_registrar_reuses_stable_endpoint_for_shared_source_root(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        first_job = _job()
        second_job = _job(
            job_id="job-b",
            job_revision_id="job-revision-b",
            target_path="F:/Backup",
        )
        _insert_job_revision(connection, first_job)
        _insert_job_revision(connection, second_job)
        id_factory = FixedEndpointIdFactory()
        registrar = SqliteStandardBackupJobEndpointRegistrar(
            connection,
            id_factory=id_factory,
        )

        first = registrar.register_standard_backup_job_endpoints(first_job)
        second = registrar.register_standard_backup_job_endpoints(second_job)

        assert first.source.endpoint_id == second.source.endpoint_id
        assert first.source.endpoint_revision_id == second.source.endpoint_revision_id
        assert first.targets[0].endpoint_id != second.targets[0].endpoint_id
        assert _row_count(connection, "endpoints") == 3
        assert _row_count(connection, "standard_backup_job_endpoint_bindings") == 4
        assert id_factory.calls == 3


def test_registrar_rejects_nonlocal_or_relative_root_before_endpoint_insert(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        job = _job(source_path="../Pictures")
        _insert_job_revision(connection, job)
        registrar = SqliteStandardBackupJobEndpointRegistrar(
            connection,
            id_factory=FixedEndpointIdFactory(),
        )

        with pytest.raises(
            SqliteJobEndpointRegistrationError,
            match="STANDARD_BACKUP_JOB_ENDPOINT_REQUIRES_ABSOLUTE_LOCAL_PATH",
        ):
            registrar.register_standard_backup_job_endpoints(job)

        assert _row_count(connection, "endpoints") == 0
        assert _row_count(connection, "standard_backup_job_endpoint_bindings") == 0


def test_job_endpoint_binding_rejects_revision_from_wrong_endpoint_parent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        job = _job()
        _insert_job_revision(connection, job)
        connection.execute("INSERT INTO endpoints (id) VALUES ('endpoint-a'), ('endpoint-b')")
        connection.execute(
            """
            INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES
                ('endpoint-a', 'revision-a', 'A', 'file:///C:/A'),
                ('endpoint-b', 'revision-b', 'B', 'file:///C:/B')
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
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
                    'job-revision-a',
                    'SOURCE',
                    0,
                    'endpoint-a',
                    'revision-b',
                    'REGISTRATION_PENDING'
                )
                """
            )


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_job_revision(
    connection: sqlite3.Connection,
    job: SealedStandardBackupJob,
) -> None:
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES (?, 'multi_target_backup')",
        (job.job_id,),
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES (?, ?)",
        (job.job_id, job.filter_set_id),
    )
    insert_default_filter_set_version(
        connection,
        job_id=job.job_id,
        filter_set_id=job.filter_set_id,
    )
    connection.execute(
        "INSERT INTO job_revisions (job_id, id, filter_set_id) VALUES (?, ?, ?)",
        (job.job_id, job.job_revision_id, job.filter_set_id),
    )
    connection.commit()


def _job(
    *,
    job_id: str = "job-a",
    job_revision_id: str = "job-revision-a",
    source_path: str = "C:/Users/Ada/Pictures",
    target_path: str = "E:/Backup",
) -> SealedStandardBackupJob:
    return SealedStandardBackupJob(
        job_id=job_id,
        job_revision_id=job_revision_id,
        filter_set_id=f"filter-{job_id}",
        draft_id=f"draft-{job_id}",
        command_request_id=f"request-{job_id}",
        idempotency_key=f"idempotency-{job_id}",
        source_name="Pictures",
        source_path_label=source_path,
        targets=(
            SealedStandardBackupTarget(
                name="Backup",
                path_label=target_path,
            ),
        ),
        defaults=StandardBackupDefaults(),
    )


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
