from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.support.sqlite_catalog import insert_default_filter_set_version

from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.endpoint_classifications import (
    SqliteEndpointClassificationRefresher,
)
from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)


INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"


def test_refresher_persists_absent_roots_without_creating_control_areas(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job_and_endpoint(
            connection,
            endpoint_id="endpoint-source",
            endpoint_revision_id="revision-source",
            root=source,
            role="SOURCE",
            ordinal=0,
        )
        _insert_job_and_endpoint(
            connection,
            endpoint_id="endpoint-target",
            endpoint_revision_id="revision-target",
            root=target,
            role="TARGET",
            ordinal=1,
        )
        connection.commit()
        refresher = SqliteEndpointClassificationRefresher(
            connection,
            classifier=LocalEndpointControlAreaClassifier(),
            local_installation_id=INSTALLATION_ID,
        )

        report = refresher.refresh_endpoint_classifications(
            observed_utc="2026-07-30T21:00:00Z",
        )
        replay = refresher.refresh_endpoint_classifications(
            observed_utc="2026-07-30T21:01:00Z",
        )

        assert report.to_dict() == {
            "classified_endpoint_count": 2,
            "failed_endpoint_count": 0,
            "pending_binding_count": 1,
            "read_only_ready_binding_count": 1,
            "writable_ready_binding_count": 0,
            "blocked_binding_count": 0,
        }
        assert replay == report
        assert connection.execute(
            """
            SELECT role, registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            ORDER BY ordinal
            """
        ).fetchall() == [
            (
                "SOURCE",
                "READ_ONLY_READY",
                "ENDPOINT_SOURCE_READ_ONLY_WITHOUT_CONTROL_AREA",
            ),
            (
                "TARGET",
                "REGISTRATION_PENDING",
                "ENDPOINT_TARGET_REGISTRATION_REQUIRED",
            ),
        ]
        observations = connection.execute(
            """
            SELECT
                endpoint_id,
                local_installation_id,
                inspection_status,
                classification_state,
                reason_codes_json,
                marker_json,
                error_code,
                next_action,
                observed_utc,
                row_version
            FROM endpoint_classification_observations
            ORDER BY endpoint_id
            """
        ).fetchall()
        assert len(observations) == 2
        assert all(row[1] == INSTALLATION_ID for row in observations)
        assert all(row[2:4] == ("CLASSIFIED", "ABSENT") for row in observations)
        assert all(json.loads(str(row[4])) == ["ENDPOINT_CONTROL_AREA_ABSENT"] for row in observations)
        assert all(row[5:8] == (None, None, None) for row in observations)
        assert all(row[8:] == ("2026-07-30T21:01:00Z", 2) for row in observations)
        assert not (source / ".mediasync").exists()
        assert not (target / ".mediasync").exists()


def test_refresher_persists_failed_inspection_and_blocks_binding(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job_and_endpoint(
            connection,
            endpoint_id="endpoint-source",
            endpoint_revision_id="revision-source",
            root=missing,
            role="SOURCE",
            ordinal=0,
        )
        connection.commit()

        report = SqliteEndpointClassificationRefresher(
            connection,
            classifier=LocalEndpointControlAreaClassifier(),
            local_installation_id=INSTALLATION_ID,
        ).refresh_endpoint_classifications(
            observed_utc="2026-07-30T21:00:00Z",
        )

        assert report.failed_endpoint_count == 1
        assert report.blocked_binding_count == 1
        assert connection.execute(
            """
            SELECT registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            """
        ).fetchone() == (
            "BLOCKED",
            "ENDPOINT_CLASSIFICATION_ROOT_MISSING",
        )
        assert connection.execute(
            """
            SELECT
                inspection_status,
                classification_state,
                reason_codes_json,
                marker_json,
                error_code,
                next_action
            FROM endpoint_classification_observations
            """
        ).fetchone() == (
            "FAILED",
            None,
            "[]",
            None,
            "ENDPOINT_CLASSIFICATION_ROOT_MISSING",
            "Restore the selected endpoint root before retrying classification.",
        )
        assert not (missing / ".mediasync").exists()


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
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
        VALUES ('job-a', 'revision-a', 'filter-a')
        """
    )


def _insert_job_and_endpoint(
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
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
        VALUES (?, ?, ?, ?)
        """,
        (endpoint_id, endpoint_revision_id, role.title(), root.as_uri()),
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
        VALUES ('job-a', 'revision-a', ?, ?, ?, ?, 'REGISTRATION_PENDING')
        """,
        (role, ordinal, endpoint_id, endpoint_revision_id),
    )
