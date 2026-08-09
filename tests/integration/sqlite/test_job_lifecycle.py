from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from mediasync_home.adapters.sqlite.backup_analysis import (
    SqliteBackupAnalysisRequestStore,
)
from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.job_catalog import SqliteStandardBackupJobCatalog
from mediasync_home.adapters.sqlite.job_lifecycle import SqliteJobLifecycleStore
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from mediasync_home.adapters.sqlite.schedules import SqliteScheduleStore
from mediasync_home.adapters.sqlite.transactions import SqliteImmediateTransactionRunner
from mediasync_home.application.job_lifecycle import (
    ChangeJobLifecycleCommand,
    JobLifecycleState,
)
from mediasync_home.application.schedules import (
    ScheduleDefinition,
    ScheduleTriggerResolutionKind,
    resolve_schedule_for_trigger,
)
from mediasync_home.application.task_scheduler import (
    bind_same_user_task_scheduler_definition_hash,
)
from mediasync_home.application.trigger_occurrences import TriggerKind
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient
from tests.support.sqlite_catalog import insert_default_filter_set_version


INSTALLATION_ID = "installation-a"
EXECUTABLE_PATH = r"C:\MediaSync Home\MediaSyncHome.exe"


def test_archive_disables_schedule_preserves_records_and_replays(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job_and_plan(connection)
        schedules = SqliteScheduleStore(connection)
        enabled = bind_same_user_task_scheduler_definition_hash(
            _schedule(),
            installation_id=INSTALLATION_ID,
            executable_path=EXECUTABLE_PATH,
        )
        schedules.save_schedule(enabled)
        store = _lifecycle_store(connection)
        command = _command(expected_row_version=1)

        archived = store.archive_standard_backup_job(
            command=command,
            occurred_utc="2026-08-01T10:00:00Z",
        )
        replay = store.archive_standard_backup_job(
            command=command,
            occurred_utc="2026-08-01T10:00:01Z",
        )

        assert archived.applied
        assert archived.record is not None
        assert archived.record.state is JobLifecycleState.ARCHIVED
        assert archived.record.row_version == 2
        assert archived.disabled_schedule_count == 1
        assert replay.idempotent_replay
        assert _row_count(connection, "job_lifecycle_events") == 1
        assert _row_count(connection, "job_revisions") == 1
        assert _row_count(connection, "plans") == 1
        disabled = schedules.load_schedule("schedule-a")
        assert disabled is not None
        assert not disabled.enabled
        assert disabled.definition_generation == 2
        assert disabled.row_version == 2
        assert disabled.desired_definition_hash != enabled.desired_definition_hash
        assert (
            resolve_schedule_for_trigger(
                schedules=schedules,
                schedule_id="schedule-a",
                schedule_revision_hash=enabled.desired_definition_hash,
            ).kind
            is ScheduleTriggerResolutionKind.DISABLED
        )
        catalog = SqliteStandardBackupJobCatalog(connection)
        assert catalog.load_standard_backup_job("job-a") is None
        detail = catalog.load_standard_backup_job_detail("job-a")
        assert detail is not None
        assert detail.lifecycle_state is JobLifecycleState.ARCHIVED
        assert catalog.list_active_standard_backup_job_summaries(limit=10, offset=0) == ()
        archived_rows = catalog.list_standard_backup_job_summaries(
            lifecycle_state=JobLifecycleState.ARCHIVED,
            limit=10,
            offset=0,
        )
        assert tuple(row.job_id for row in archived_rows) == ("job-a",)


def test_delete_reports_delete_specific_scheduler_context_error(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job_and_plan(connection)
        enabled = bind_same_user_task_scheduler_definition_hash(
            _schedule(),
            installation_id=INSTALLATION_ID,
            executable_path=EXECUTABLE_PATH,
        )
        SqliteScheduleStore(connection).save_schedule(enabled)
        store = SqliteJobLifecycleStore(
            connection,
            installation_id=INSTALLATION_ID,
            task_scheduler_executable_path=None,
        )

        outcome = store.delete_standard_backup_job(
            command=_command(expected_row_version=1),
            occurred_utc="2026-08-01T10:00:00Z",
        )

        assert not outcome.applied
        assert outcome.validation_code == "JOB_DELETE_SCHEDULER_CONTEXT_UNAVAILABLE"
        assert store.load_job_lifecycle("job-a") is not None


@pytest.mark.parametrize(
    ("run_state", "validation_code"),
    (
        ("EXECUTING", "JOB_ARCHIVE_ACTIVE_RUN"),
        ("RECOVERY_REQUIRED", "JOB_ARCHIVE_RECOVERY_REQUIRED"),
    ),
)
def test_archive_blocks_active_and_recovery_required_runs(
    tmp_path: Path,
    run_state: str,
    validation_code: str,
) -> None:
    database = tmp_path / f"catalog-{run_state}.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job_and_plan(connection)
        _insert_run(connection, state=run_state)

        outcome = _lifecycle_store(connection).archive_standard_backup_job(
            command=_command(expected_row_version=1),
            occurred_utc="2026-08-01T10:00:00Z",
        )

        assert not outcome.applied
        assert outcome.validation_code == validation_code
        assert outcome.record is not None
        assert outcome.record.state is JobLifecycleState.ACTIVE
        assert _row_count(connection, "job_lifecycle_events") == 0


def test_archive_reactivate_delete_ipc_preserves_history_and_survives_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    archive_request_id = str(uuid4())
    archive_key = str(uuid4())
    reactivate_request_id = str(uuid4())
    reactivate_key = str(uuid4())
    delete_request_id = str(uuid4())
    delete_key = str(uuid4())
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job_and_plan(connection)
        lifecycle = _lifecycle_store(connection)
        service = EngineHostIpcService(
            ClientAuthorizationPolicy(
                expected_user_sid_hash="user-a",
                expected_session_id=7,
            ),
            status=replace(
                EngineHostIpcService(
                    ClientAuthorizationPolicy(
                        expected_user_sid_hash="user-a",
                        expected_session_id=7,
                    )
                ).status,
                mutations_enabled=True,
                scope="0B_LOCAL_MUTATION_PREVIEW",
            ),
            job_lifecycle_store=lifecycle,
            job_lifecycle_utc_now=lambda: "2026-08-01T11:00:00Z",
            backup_analysis_request_store=SqliteBackupAnalysisRequestStore(connection),
            command_receipt_store=SqliteCommandReceiptStore(connection),
            command_effect_transaction=SqliteImmediateTransactionRunner(connection),
        )
        ipc = InProcessIpcClient(
            service=service,
            identity=VerifiedClientIdentity(
                user_sid_hash="user-a",
                session_id=7,
                is_remote=False,
                transport="in-process-test",
            ),
            role=ProcessRole.GUI,
        )
        client = EngineClient(ipc)
        assert client.connect().status is IpcStatus.ACCEPTED

        archived = client.archive_standard_backup_job(
            job_id="job-a",
            expected_job_revision_id="job-rev-a",
            expected_lifecycle_row_version=1,
            request_id=archive_request_id,
            idempotency_key=archive_key,
        )
        reactivated = client.reactivate_standard_backup_job(
            job_id="job-a",
            expected_job_revision_id="job-rev-a",
            expected_lifecycle_row_version=2,
            request_id=reactivate_request_id,
            idempotency_key=reactivate_key,
        )
        deleted = client.delete_standard_backup_job(
            job_id="job-a",
            expected_job_revision_id="job-rev-a",
            expected_lifecycle_row_version=3,
            request_id=delete_request_id,
            idempotency_key=delete_key,
        )

        assert archived.status is IpcStatus.ACCEPTED
        assert reactivated.status is IpcStatus.ACCEPTED
        assert deleted.status is IpcStatus.ACCEPTED
        assert deleted.payload["validation_code"] == "JOB_DELETED"
        request = SqliteBackupAnalysisRequestStore(
            connection
        ).load_backup_analysis_request(reactivate_request_id)
        assert request is not None
        assert request.job_id == "job-a"
        assert request.state.value == "BLOCKED"
        assert not request.start_when_safe
        assert connection.execute(
            "SELECT state FROM command_receipts WHERE idempotency_key = ?",
            (reactivate_key,),
        ).fetchone() == ("SUCCEEDED",)
        assert _row_count(connection, "job_deletions") == 1
        catalog = SqliteStandardBackupJobCatalog(connection)
        assert catalog.load_standard_backup_job_detail("job-a") is None
        assert catalog.list_active_standard_backup_job_summaries(limit=10, offset=0) == ()
        assert (
            catalog.list_standard_backup_job_summaries(
                lifecycle_state=JobLifecycleState.ARCHIVED,
                limit=10,
                offset=0,
            )
            == ()
        )
        assert _row_count(connection, "job_revisions") == 1
        assert _row_count(connection, "plans") == 1

    with sqlite3.connect(database) as restarted_connection:
        _prepare_catalog(restarted_connection, database)
        restarted = _lifecycle_store(restarted_connection).load_job_lifecycle("job-a")
        assert restarted is not None
        assert restarted.state is JobLifecycleState.DELETED
        assert restarted.row_version == 4
        queued = SqliteBackupAnalysisRequestStore(
            restarted_connection
        ).load_backup_analysis_request(reactivate_request_id)
        assert queued is not None
        assert queued.state.value == "BLOCKED"


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_job_and_plan(connection: sqlite3.Connection) -> None:
    defaults_json = (
        '{"behavior":"UPDATE_BACKUP","extra_files":"KEEP_ON_TARGET",'
        '"file_selection":"ALL_USER_FILES","performance":"AUTO",'
        '"retention":"THIRTY_DAYS","verification":"STANDARD"}'
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
        VALUES ('draft-a', 1, 'Pictures', 'C:/Pictures', ?, '[]')
        """,
        (defaults_json,),
    )
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
    insert_default_filter_set_version(
        connection,
        job_id="job-a",
        filter_set_id="filter-a",
    )
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
        VALUES ('job-a', 'job-rev-a', 'filter-a')
        """
    )
    connection.execute(
        "INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'job-rev-a')"
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
        VALUES (
            'job-a',
            'job-rev-a',
            'draft-a',
            'create-request-a',
            'create-key-a',
            'Pictures',
            'C:/Pictures',
            ?,
            '[]'
        )
        """,
        (defaults_json,),
    )
    connection.execute(
        "INSERT INTO analyses (id, job_id, job_revision_id) VALUES ('analysis-a', 'job-a', 'job-rev-a')"
    )
    connection.execute("INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')")
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
            'plan-a', 'analysis-a', 'job-a', 'job-rev-a', 'planner', 1, 1,
            'dry-run', 'SHA-256', 'canonical-json', ?, '{}', 1, 0
        )
        """,
        ("a" * 64,),
    )


def _insert_run(connection: sqlite3.Connection, *, state: str) -> None:
    connection.execute(
        """
        INSERT INTO runs (
            id,
            job_id,
            job_revision_id,
            plan_id,
            command_request_id,
            logical_run_group_id,
            trigger_type,
            state,
            summary_json,
            app_version,
            plan_checksum,
            idempotency_key,
            planned_operations,
            planned_bytes
        )
        VALUES (
            'run-a', 'job-a', 'job-rev-a', 'plan-a', 'run-request-a',
            'run-group-a', 'MANUAL_LOCAL_PREVIEW', ?, '{}', 'test', ?,
            'run-key-a', 1, 0
        )
        """,
        (state, "a" * 64),
    )


def _schedule() -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id="job-a",
        plan_id="plan-a",
        plan_checksum="a" * 64,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash="0" * 64,
        time_zone_id="Europe/Oslo",
        enabled=True,
        row_version=1,
    )


def _lifecycle_store(connection: sqlite3.Connection) -> SqliteJobLifecycleStore:
    return SqliteJobLifecycleStore(
        connection,
        installation_id=INSTALLATION_ID,
        task_scheduler_executable_path=EXECUTABLE_PATH,
    )


def _command(*, expected_row_version: int) -> ChangeJobLifecycleCommand:
    return ChangeJobLifecycleCommand(
        request_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        job_id="job-a",
        expected_job_revision_id="job-rev-a",
        expected_lifecycle_row_version=expected_row_version,
        explicit_confirmation=True,
    )


def _row_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()
    assert row is not None
    return int(row[0])
