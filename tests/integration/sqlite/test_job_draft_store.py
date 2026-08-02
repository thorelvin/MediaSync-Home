from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.job_draft_store import (
    SqliteJobDraftStore,
    SqliteJobDraftStoreError,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.application.job_drafts import (
    AutomationPolicy,
    StandardBackupJobDraft,
)


def test_sqlite_job_draft_store_roundtrips_standard_backup_draft(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteJobDraftStore(connection)
        draft = (
            StandardBackupJobDraft.new("draft-a")
            .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
            .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
        )
        draft = replace(
            draft,
            defaults=replace(
                draft.defaults,
                automation_policy=AutomationPolicy.NEW_AND_CHANGED_WITH_VERSIONS,
            ),
        )

        store.save_standard_backup_draft(draft)

        assert store.load_standard_backup_draft("draft-a") == draft
        assert store.load_standard_backup_draft("missing") is None
        defaults_json = connection.execute(
            "SELECT defaults_json FROM standard_backup_job_drafts WHERE draft_id = 'draft-a'"
        ).fetchone()
        assert defaults_json is not None
        assert json.loads(str(defaults_json[0]))["automation_policy"] == (
            AutomationPolicy.NEW_AND_CHANGED_WITH_VERSIONS.value
        )


def test_sqlite_job_draft_store_defaults_legacy_policy_to_new_files_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        legacy_defaults = json.dumps(
            {
                "behavior": "UPDATE_BACKUP",
                "extra_files": "KEEP_ON_TARGET",
                "file_selection": "ALL_USER_FILES",
                "performance": "AUTO",
                "retention": "THIRTY_DAYS",
                "verification": "STANDARD",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO standard_backup_job_drafts (
                draft_id,
                schema_version,
                defaults_json,
                targets_json
            )
            VALUES ('draft-legacy', 1, ?, '[]')
            """,
            (legacy_defaults,),
        )
        connection.commit()

        loaded = SqliteJobDraftStore(connection).load_standard_backup_draft(
            "draft-legacy"
        )

        assert loaded is not None
        assert loaded.defaults.automation_policy is AutomationPolicy.NEW_FILES_ONLY


def test_sqlite_job_draft_store_overwrites_existing_draft(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteJobDraftStore(connection)
        initial = StandardBackupJobDraft.new("draft-a").with_source(
            name="Pictures",
            path_label="C:/Users/Ada/Pictures",
        )
        updated = initial.with_added_target(name="USB 1", path_label="E:/Backup")

        store.save_standard_backup_draft(initial)
        store.save_standard_backup_draft(updated)

        assert store.load_standard_backup_draft("draft-a") == updated
        assert _row_count(connection, "standard_backup_job_drafts") == 1


def test_sqlite_job_draft_store_rejects_corrupt_defaults_json(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        connection.execute(
            """
            INSERT INTO standard_backup_job_drafts (
                draft_id,
                schema_version,
                defaults_json,
                targets_json
            )
            VALUES ('draft-bad', 1, '{bad-json', '[]')
            """
        )
        connection.commit()

        with pytest.raises(SqliteJobDraftStoreError, match="DRAFT_DEFAULTS_JSON_INVALID"):
            SqliteJobDraftStore(connection).load_standard_backup_draft("draft-bad")


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])
