from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (
    SqliteMigrationViolation,
    apply_sqlite_migrations,
    catalog_migration_plan,
    current_schema_version,
    inspect_sqlite_migration_state,
    migration_checksum,
    recovery_migration_plan,
)

FILTER_RULES_JSON = '{"preset":"ALL_USER_FILES","schema_version":1}'
FILTER_RULES_HASH = "5b551f66adfe79a9e025a369c44e76ece00928588f965a93fe6cdcfbdb1e4a9b"


def test_catalog_migration_creates_contract_skeleton_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        plan = catalog_migration_plan()

        apply_sqlite_migrations(connection, plan)
        apply_sqlite_migrations(connection, plan)

        assert current_schema_version(connection, plan.store) == 55
        assert _table_names(connection) >= {
            "endpoint_heads",
            "endpoint_root_claims",
            "job_heads",
            "installation_state",
            "endpoint_classification_observations",
            "file_entries",
            "directory_coverage",
            "snapshot_issues",
            "case_collision_members",
            "snapshot_batches",
            "operation_dependencies",
            "standard_backup_job_drafts",
            "standard_backup_job_revision_details",
            "standard_backup_job_endpoint_bindings",
            "standard_backup_job_snapshot_materializations",
            "initial_backup_plan_materializations",
            "writable_endpoint_registration_intents",
            "writable_endpoint_registrations",
            "writable_endpoint_capability_observations",
            "controlled_endpoint_takeover_intents",
            "controlled_endpoint_takeovers",
            "command_receipts",
            "command_dedup_tombstones",
            "plan_seal_details",
            "plan_operation_seal_details",
            "plan_endpoints",
            "outbox_messages",
            "effect_dedup_tombstones",
            "trigger_occurrences",
            "schedules",
            "external_resource_state",
            "filter_set_versions",
            "final_file_catalog_handoffs",
            "retained_version_objects",
            "version_retention_holds",
            "version_retention_plans",
            "version_retention_items",
            "version_retention_events",
            "retained_version_restore_operations",
            "retained_version_restore_events",
            "retained_version_restore_rollbacks",
            "retained_version_restore_rollback_events",
            "job_revision_filter_bindings",
            "runs",
            "run_targets",
            "run_stop_requests",
            "backup_analysis_requests",
            "current_read_hash_evidence",
            "run_target_endpoint_wait_events",
            "run_attempts",
            "operation_attempts",
            "operation_outcomes",
            "schema_migrations",
            "store_identity",
        }
        assert _row_count(connection, "schema_migrations") == 55
        assert {
            "idx_initial_backup_materializations_history",
            "idx_initial_backup_materializations_job_history",
            "idx_backup_analysis_requests_history",
            "idx_backup_analysis_requests_job_history",
            "idx_backup_analysis_requests_analysis",
        } <= _index_names(connection)
        assert _column_names(connection, "run_target_endpoint_wait_events") >= {
            "backoff_ms",
            "retry_not_before_utc",
        }
        assert _column_names(connection, "endpoint_revisions") >= {"generation"}
        assert _column_names(connection, "snapshots") >= {"endpoint_generation"}
        assert _column_names(connection, "file_entries") >= {"birthtime_ns"}
        assert _column_names(connection, "endpoint_classification_observations") >= {
            "read_capabilities_json",
            "read_capabilities_hash",
        }
        assert _column_names(connection, "writable_endpoint_registrations") >= {
            "write_capabilities_json",
            "write_capabilities_hash",
        }
        assert _column_names(connection, "plan_endpoints") >= {"endpoint_generation"}
        assert _column_names(connection, "plan_operation_seal_details") >= {
            "target_endpoint_id"
        }
        assert _column_names(connection, "schema_migrations") >= {
            "store",
            "version",
            "name",
            "migration_checksum",
            "applied_utc",
        }
        assert all(
            checksum == migration_checksum(migration)
            for checksum, migration in zip(
                _migration_checksums(connection),
                plan.migrations,
                strict=True,
            )
        )
        assert {
            "trg_schema_migrations_valid_insert",
            "trg_schema_migrations_immutable_update",
            "trg_schema_migrations_immutable_delete",
            "trg_endpoint_revisions_no_update",
            "trg_endpoint_revisions_no_delete",
            "trg_endpoint_revisions_generation_must_advance",
            "trg_snapshots_endpoint_generation_required",
            "trg_snapshots_endpoint_identity_immutable",
            "trg_plan_endpoints_endpoint_generation_required",
            "trg_plan_endpoints_endpoint_identity_immutable",
            "trg_job_revisions_no_update",
            "trg_job_revisions_no_delete",
            "trg_filter_sets_no_update_after_use",
            "trg_filter_sets_no_delete_after_use",
            "trg_filter_set_versions_no_update",
            "trg_filter_set_versions_no_delete",
            "trg_job_revisions_filter_version_required",
            "trg_job_revisions_bind_filter_version",
            "trg_job_revision_filter_bindings_no_update",
            "trg_job_revision_filter_bindings_no_delete",
            "trg_standard_backup_job_revision_details_no_update",
            "trg_standard_backup_job_revision_details_no_delete",
            "trg_standard_backup_job_endpoint_bindings_identity_immutable",
            "trg_standard_backup_job_endpoint_bindings_no_delete",
            "trg_writable_endpoint_registration_intents_identity_immutable",
            "trg_writable_endpoint_registration_intents_transition",
            "trg_writable_endpoint_registration_intents_no_delete",
            "trg_writable_endpoint_registrations_no_update",
            "trg_writable_endpoint_registrations_no_delete",
            "trg_classified_endpoint_requires_read_capabilities_insert",
            "trg_classified_endpoint_requires_read_capabilities_update",
            "trg_writable_registration_requires_capabilities",
            "trg_writable_endpoint_capability_observations_no_update",
            "trg_writable_endpoint_capability_observations_no_delete",
            "trg_initial_backup_plan_materializations_no_update",
            "trg_initial_backup_plan_materializations_no_delete",
            "trg_run_target_endpoint_wait_events_retry_timing_required",
            "trg_run_target_endpoint_wait_events_no_update",
            "trg_run_target_endpoint_wait_events_no_delete",
            "trg_operation_attempts_no_update",
            "trg_operation_attempts_no_delete",
            "trg_operation_attempts_verification_axes_valid",
            "trg_operation_outcomes_no_update",
            "trg_operation_outcomes_no_delete",
            "trg_operation_outcomes_verification_axes_valid",
            "trg_retained_version_restore_rollback_binding_immutable",
            "trg_retained_version_restore_rollback_no_delete",
            "trg_retained_version_restore_rollback_events_no_update",
            "trg_retained_version_restore_rollback_events_no_delete",
        } <= _trigger_names(connection)
        assert _foreign_key(
            connection,
            "endpoint_heads",
            "endpoint_revisions",
            ("endpoint_id", "active_revision_id"),
            ("endpoint_id", "id"),
        )
        assert _foreign_key(
            connection,
            "job_heads",
            "job_revisions",
            ("job_id", "active_revision_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "filter_set_versions",
            "filter_sets",
            ("job_id", "filter_set_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "job_revision_filter_bindings",
            "job_revisions",
            ("job_id", "job_revision_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "job_revision_filter_bindings",
            "filter_set_versions",
            ("job_id", "filter_set_id", "filter_set_version"),
            ("job_id", "filter_set_id", "version"),
        )
        assert _foreign_key(
            connection,
            "operation_dependencies",
            "planned_operations",
            ("plan_id", "before_operation_id"),
            ("plan_id", "id"),
        )
        assert _foreign_key(
            connection,
            "operation_attempts",
            "run_attempts",
            ("run_attempt_id", "run_id"),
            ("id", "run_id"),
        )
        assert _foreign_key(
            connection,
            "operation_attempts",
            "runs",
            ("run_id", "plan_id"),
            ("id", "plan_id"),
        )
        assert _foreign_key(
            connection,
            "operation_attempts",
            "run_targets",
            ("run_id", "run_target_id"),
            ("run_id", "id"),
        )
        assert _foreign_key(
            connection,
            "operation_attempts",
            "planned_operations",
            ("plan_id", "operation_id"),
            ("plan_id", "id"),
        )
        assert _foreign_key(
            connection,
            "operation_outcomes",
            "runs",
            ("run_id", "plan_id"),
            ("id", "plan_id"),
        )
        assert _foreign_key(
            connection,
            "operation_outcomes",
            "run_targets",
            ("run_id", "run_target_id"),
            ("run_id", "id"),
        )
        assert _foreign_key(
            connection,
            "operation_outcomes",
            "planned_operations",
            ("plan_id", "operation_id"),
            ("plan_id", "id"),
        )
        assert _foreign_key(
            connection,
            "standard_backup_job_revision_details",
            "job_revisions",
            ("job_id", "job_revision_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "standard_backup_job_endpoint_bindings",
            "job_revisions",
            ("job_id", "job_revision_id"),
            ("job_id", "id"),
        )


def test_catalog_verification_axes_migration_backfills_and_enforces_claims(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    plan = catalog_migration_plan()
    version_50_plan = replace(plan, migrations=plan.migrations[:50])
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        apply_sqlite_migrations(connection, version_50_plan)
        connection.execute(
            """
            INSERT INTO operation_attempts (
                id, run_attempt_id, run_id, plan_id, run_target_id,
                operation_id, attempt_number, state, transfer_state,
                assurance_level, durability_level, finished_utc
            )
            VALUES (
                'attempt-a', 'run-attempt-a', 'run-a', 'plan-a', 'target-a',
                'operation-a', 1, 'SUCCEEDED', 'TRANSFERRED_TO_STAGING',
                'FULL_HASH', 'FILE_FSYNC_COMPLETED',
                '2026-08-01T10:00:00.000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO operation_outcomes (
                run_id, plan_id, run_target_id, operation_id, final_state,
                transfer_state, assurance_level, durability_level, completed_utc
            )
            VALUES (
                'run-a', 'plan-a', 'target-a', 'operation-a', 'SUCCEEDED',
                'TRANSFERRED_TO_STAGING', 'FULL_HASH',
                'LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED',
                '2026-08-01T10:00:01.000Z'
            )
            """
        )
        connection.commit()

        apply_sqlite_migrations(connection, plan)

        assert connection.execute(
            """
            SELECT transfer_state, assurance_level, durability_level
            FROM operation_attempts
            WHERE id = 'attempt-a'
            """
        ).fetchone() == (
            "TRANSFERRED",
            "PRIMARY_STREAM_HASH_VERIFIED",
            "LOCAL_FILE_FLUSH_CONFIRMED",
        )
        assert connection.execute(
            """
            SELECT transfer_state, assurance_level, durability_level
            FROM operation_outcomes
            WHERE run_id = 'run-a' AND operation_id = 'operation-a'
            """
        ).fetchone() == (
            "TRANSFERRED",
            "PRIMARY_STREAM_HASH_VERIFIED",
            "WRITE_THROUGH_REQUEST_CONFIRMED",
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="OPERATION_OUTCOME_VERIFICATION_AXES_INVALID",
        ):
            connection.execute(
                """
                INSERT INTO operation_outcomes (
                    run_id, plan_id, run_target_id, operation_id, final_state,
                    transfer_state, assurance_level, durability_level,
                    completed_utc
                )
                VALUES (
                    'run-b', 'plan-b', 'target-b', 'operation-b', 'SUCCEEDED',
                    'TRANSFERRED', 'NONE', 'UNKNOWN',
                    '2026-08-01T10:00:02.000Z'
                )
                """
            )
        assert _foreign_key(
            connection,
            "standard_backup_job_endpoint_bindings",
            "endpoint_revisions",
            ("endpoint_id", "endpoint_revision_id"),
            ("endpoint_id", "id"),
        )
        assert _foreign_key(
            connection,
            "endpoint_classification_observations",
            "endpoint_revisions",
            ("endpoint_id", "endpoint_revision_id"),
            ("endpoint_id", "id"),
        )
        assert _foreign_key(
            connection,
            "standard_backup_job_snapshot_materializations",
            "job_revisions",
            ("job_id", "job_revision_id"),
            ("job_id", "id"),
        )
        assert _foreign_key(
            connection,
            "standard_backup_job_snapshot_materializations",
            "analyses",
            ("analysis_id",),
            ("id",),
        )
        assert _foreign_key(
            connection,
            "runs",
            "plan_seal_details",
            ("plan_id", "job_id", "job_revision_id"),
            ("plan_id", "job_id", "job_revision_id"),
        )
        assert _foreign_key(
            connection,
            "runs",
            "command_receipts",
            ("command_receipt_id",),
            ("idempotency_key",),
        )
        assert _foreign_key(
            connection,
            "plan_endpoints",
            "analysis_targets",
            ("analysis_id", "endpoint_id"),
            ("analysis_id", "endpoint_id"),
        )
        assert _foreign_key(
            connection,
            "plan_endpoints",
            "snapshots",
            ("snapshot_id", "endpoint_id"),
            ("id", "endpoint_id"),
        )
        assert _foreign_key(
            connection,
            "trigger_occurrences",
            "jobs",
            ("job_id",),
            ("id",),
        )
        assert _foreign_key(
            connection,
            "trigger_occurrences",
            "runs",
            ("run_id",),
            ("id",),
        )
        assert _foreign_key(
            connection,
            "schedules",
            "jobs",
            ("job_id",),
            ("id",),
        )
        assert _foreign_key(
            connection,
            "schedules",
            "plan_seal_details",
            ("plan_id",),
            ("plan_id",),
        )
        assert (
            _index_is_unique(
                connection, "file_entries", ("snapshot_id", "comparison_key")
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "file_entries",
                ("snapshot_id", "comparison_key", "relative_path", "id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "directory_coverage",
                ("snapshot_id", "comparison_key", "relative_path"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "directory_coverage",
                ("snapshot_id", "coverage_state", "comparison_key", "relative_path"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "snapshot_issues",
                ("snapshot_id", "relative_path", "issue_type", "id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "snapshot_issues",
                (
                    "snapshot_id",
                    "blocks_destructive_actions",
                    "relative_path",
                    "issue_type",
                    "id",
                ),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "plan_operation_seal_details",
                ("plan_id", "execution_phase", "stable_order_key", "operation_id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "plan_endpoints",
                ("plan_id", "role", "target_ordinal", "endpoint_id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "case_collision_groups",
                ("snapshot_id", "comparison_key"),
            )
            is True
        )
        assert _index_is_unique(connection, "command_receipts", ("state",)) is False
        assert _index_is_unique(connection, "runs", ("state",)) is False
        assert _index_is_unique(connection, "runs", ("started_utc", "id")) is False
        assert (
            _index_is_unique(connection, "runs", ("job_id", "started_utc", "id"))
            is False
        )
        assert (
            _index_is_unique(
                connection, "outbox_messages", ("state", "next_attempt_utc")
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "outbox_messages",
                ("state", "claim_owner_instance_id", "claim_started_utc", "id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "trigger_occurrences",
                ("job_id", "received_utc"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "schedules",
                ("job_id", "enabled"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "external_resource_state",
                ("resource_type", "state", "resource_id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "standard_backup_job_endpoint_bindings",
                ("endpoint_id", "endpoint_revision_id"),
            )
            is False
        )
        assert (
            _index_is_unique(
                connection,
                "final_file_catalog_handoffs",
                ("run_id", "operation_id"),
            )
            is True
        )
        assert _trigger_names(connection) >= {
            "trg_plans_no_update_after_seal",
            "trg_planned_operations_no_insert_after_seal",
            "trg_plan_operation_seal_details_no_insert",
            "trg_plan_endpoints_no_update_after_seal",
            "trg_plan_seal_details_no_update",
            "trg_file_entries_no_insert_after_snapshot_immutable",
            "trg_snapshot_v3_requires_birthtime",
            "trg_snapshot_batches_no_insert_after_snapshot_immutable",
            "trg_directory_coverage_no_insert_after_snapshot_immutable",
            "trg_snapshot_issues_no_insert_after_snapshot_immutable",
            "trg_case_collision_members_no_insert_after_snapshot_immutable",
            "trg_snapshots_seal_insert_requires_checksum",
            "trg_snapshots_seal_update_requires_checksum",
            "trg_snapshot_v2_requires_file_identity",
            "trg_plan_operation_v3_requires_source_precondition",
        }
        assert _column_names(connection, "snapshots") >= {
            "complete",
            "checksum_algorithm",
            "serializer_version",
            "snapshot_checksum",
            "scan_error_count",
            "volatile_directory_count",
        }
        assert "identity_fingerprint_hash" in _column_names(connection, "file_entries")
        assert _column_names(connection, "plan_operation_seal_details") >= {
            "source_relative_path",
            "source_precondition_json",
        }
        assert _column_names(connection, "endpoint_revisions") >= {
            "control_area_id",
            "root_identity_hash_algorithm",
            "root_identity_hash",
            "owner_installation_id",
            "ownership_epoch",
            "control_marker_checksum_algorithm",
            "control_marker_checksum",
        }
        assert "registration_reason_code" in _column_names(
            connection,
            "standard_backup_job_endpoint_bindings",
        )
        assert _column_names(connection, "endpoint_classification_observations") >= {
            "endpoint_id",
            "endpoint_revision_id",
            "local_installation_id",
            "inspection_status",
            "classification_state",
            "reason_codes_json",
            "marker_json",
            "error_code",
            "next_action",
            "observed_utc",
            "row_version",
        }
        assert _column_names(connection, "snapshot_batches") >= {
            "coverage_update_count",
            "issue_count",
        }
        assert _column_names(
            connection,
            "standard_backup_job_snapshot_materializations",
        ) >= {
            "analysis_id",
            "state",
            "reason_code",
            "snapshot_count",
            "sealed_snapshot_count",
            "started_utc",
            "completed_utc",
            "row_version",
        }


def test_catalog_revision_rows_are_immutable_but_heads_can_advance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_immutable_revision_rows(connection)

        with pytest.raises(sqlite3.IntegrityError, match="ENDPOINT_REVISION_IMMUTABLE"):
            connection.execute(
                """
                UPDATE endpoint_revisions
                SET display_name = 'Changed'
                WHERE endpoint_id = 'endpoint-a' AND id = 'endpoint-rev-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="ENDPOINT_REVISION_IMMUTABLE"):
            connection.execute(
                """
                DELETE FROM endpoint_revisions
                WHERE endpoint_id = 'endpoint-a' AND id = 'endpoint-rev-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="JOB_REVISION_IMMUTABLE"):
            connection.execute(
                """
                UPDATE job_revisions
                SET filter_set_id = 'filter-b'
                WHERE job_id = 'job-a' AND id = 'job-rev-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="JOB_REVISION_IMMUTABLE"):
            connection.execute(
                """
                DELETE FROM job_revisions
                WHERE job_id = 'job-a' AND id = 'job-rev-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="FILTER_SET_IMMUTABLE"):
            connection.execute(
                """
                UPDATE filter_sets
                SET description = 'Changed'
                WHERE job_id = 'job-a' AND id = 'filter-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="FILTER_SET_IMMUTABLE"):
            connection.execute(
                "DELETE FROM filter_sets WHERE job_id = 'job-a' AND id = 'filter-a'"
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="FILTER_SET_VERSION_IMMUTABLE"
        ):
            connection.execute(
                """
                UPDATE filter_set_versions
                SET rules_json = '{"schema_version":1}'
                WHERE job_id = 'job-a'
                    AND filter_set_id = 'filter-a'
                    AND version = 1
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="FILTER_SET_VERSION_IMMUTABLE"
        ):
            connection.execute(
                """
                DELETE FROM filter_set_versions
                WHERE job_id = 'job-a'
                    AND filter_set_id = 'filter-a'
                    AND version = 1
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="JOB_REVISION_FILTER_BINDING_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE job_revision_filter_bindings
                SET filter_set_version = 2
                WHERE job_id = 'job-a' AND job_revision_id = 'job-rev-a'
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="JOB_REVISION_FILTER_BINDING_IMMUTABLE",
        ):
            connection.execute(
                """
                DELETE FROM job_revision_filter_bindings
                WHERE job_id = 'job-a' AND job_revision_id = 'job-rev-a'
                """
            )
        connection.execute(
            """
            INSERT INTO filter_sets (job_id, id, description)
            VALUES ('job-a', 'filter-without-version', 'Missing version')
            """
        )
        connection.execute(
            "INSERT INTO jobs (id, kind) VALUES ('job-b', 'multi_target_backup')"
        )
        connection.execute(
            """
            INSERT INTO filter_sets (job_id, id, description)
            VALUES ('job-b', 'filter-without-version', 'Other parent')
            """
        )
        _insert_filter_version(
            connection,
            job_id="job-b",
            filter_set_id="filter-without-version",
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="FILTER_SET_VERSION_NOT_FOUND"
        ):
            connection.execute(
                """
                INSERT INTO job_revisions (
                    job_id,
                    id,
                    filter_set_id,
                    filter_set_version
                )
                VALUES ('job-a', 'job-rev-missing-filter-version', 'filter-without-version', 1)
                """
            )

        connection.execute(
            """
            UPDATE endpoint_heads
            SET active_revision_id = 'endpoint-rev-b'
            WHERE endpoint_id = 'endpoint-a'
            """
        )
        connection.execute(
            """
            UPDATE job_heads
            SET active_revision_id = 'job-rev-b'
            WHERE job_id = 'job-a'
            """
        )

        assert connection.execute(
            "SELECT active_revision_id FROM endpoint_heads WHERE endpoint_id = 'endpoint-a'"
        ).fetchone() == ("endpoint-rev-b",)
        assert connection.execute(
            "SELECT active_revision_id FROM job_heads WHERE job_id = 'job-a'"
        ).fetchone() == ("job-rev-b",)


def test_catalog_revision_details_keep_only_registration_status_mutable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_immutable_revision_rows(connection)

        with pytest.raises(sqlite3.IntegrityError, match="JOB_REVISION_IMMUTABLE"):
            connection.execute(
                """
                UPDATE standard_backup_job_revision_details
                SET source_name = 'Changed'
                WHERE job_id = 'job-a' AND job_revision_id = 'job-rev-a'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="JOB_REVISION_IMMUTABLE"):
            connection.execute(
                """
                DELETE FROM standard_backup_job_revision_details
                WHERE job_id = 'job-a' AND job_revision_id = 'job-rev-a'
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="JOB_REVISION_BINDING_IMMUTABLE",
        ):
            connection.execute(
                """
                UPDATE standard_backup_job_endpoint_bindings
                SET endpoint_revision_id = 'endpoint-rev-b'
                WHERE job_id = 'job-a'
                    AND job_revision_id = 'job-rev-a'
                    AND role = 'SOURCE'
                    AND ordinal = 0
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="JOB_REVISION_BINDING_IMMUTABLE",
        ):
            connection.execute(
                """
                DELETE FROM standard_backup_job_endpoint_bindings
                WHERE job_id = 'job-a'
                    AND job_revision_id = 'job-rev-a'
                    AND role = 'SOURCE'
                    AND ordinal = 0
                """
            )

        connection.execute(
            """
            UPDATE standard_backup_job_endpoint_bindings
            SET
                registration_state = 'READ_ONLY_READY',
                registration_reason_code = 'ENDPOINT_READ_ONLY_READY'
            WHERE job_id = 'job-a'
                AND job_revision_id = 'job-rev-a'
                AND role = 'SOURCE'
                AND ordinal = 0
            """
        )

        assert connection.execute(
            """
            SELECT registration_state, registration_reason_code
            FROM standard_backup_job_endpoint_bindings
            WHERE job_id = 'job-a'
                AND job_revision_id = 'job-rev-a'
                AND role = 'SOURCE'
                AND ordinal = 0
            """
        ).fetchone() == ("READ_ONLY_READY", "ENDPOINT_READ_ONLY_READY")


def test_catalog_classification_observation_requires_coherent_status(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        connection.execute("INSERT INTO endpoints (id) VALUES ('endpoint-a')")
        connection.execute(
            """
            INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('endpoint-a', 'revision-a', 'Source', 'file:///C:/Source')
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
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
                    error_code,
                    next_action,
                    observed_utc
                )
                VALUES (
                    'endpoint-a',
                    'revision-a',
                    'installation-a',
                    'FAILED',
                    'ABSENT',
                    '[]',
                    NULL,
                    'FAILED',
                    'Retry.',
                    '2026-07-30T21:00:00Z'
                )
                """
            )


def test_catalog_migration_preserves_case_collision_entries(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())

        _insert_catalog_parent_rows(connection)
        connection.execute(
            """
            INSERT INTO file_entries
                (snapshot_id, endpoint_id, id, relative_path, comparison_key, object_type)
                VALUES ('snapshot-a', 'endpoint-a', 'file-a', 'Readme.txt', 'readme.txt', 'file')
            """
        )
        connection.execute(
            """
            INSERT INTO file_entries
                (snapshot_id, endpoint_id, id, relative_path, comparison_key, object_type)
                VALUES ('snapshot-a', 'endpoint-a', 'file-b', 'README.TXT', 'readme.txt', 'file')
            """
        )
        connection.execute(
            """
            INSERT INTO case_collision_groups (snapshot_id, id, comparison_key)
                VALUES ('snapshot-a', 'group-a', 'readme.txt')
            """
        )
        connection.execute(
            """
            INSERT INTO case_collision_members (snapshot_id, group_id, file_entry_id)
                VALUES ('snapshot-a', 'group-a', 'file-a')
            """
        )
        connection.execute(
            """
            INSERT INTO case_collision_members (snapshot_id, group_id, file_entry_id)
                VALUES ('snapshot-a', 'group-a', 'file-b')
            """
        )

        assert _row_count(connection, "file_entries") == 2
        assert _row_count(connection, "case_collision_members") == 2


def test_catalog_migration_rejects_malformed_snapshot_seal_checksum(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_catalog_parent_rows(connection)

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                """
                UPDATE snapshots
                SET complete = 1,
                    immutable = 1,
                    checksum_algorithm = 'SHA-256',
                    serializer_version = '0B-SNAPSHOT-CANONICAL-JSON-V1',
                    snapshot_checksum = 'gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg'
                WHERE id = 'snapshot-a'
                """
            )


def test_catalog_migration_rejects_v3_seal_without_birthtime(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_catalog_parent_rows(connection)
        connection.execute(
            """
            INSERT INTO file_entries (
                snapshot_id,
                endpoint_id,
                id,
                relative_path,
                comparison_key,
                object_type,
                identity_fingerprint_hash
            )
            VALUES (
                'snapshot-a',
                'endpoint-a',
                'file-a',
                'Readme.txt',
                'readme.txt',
                'file',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            )
            """
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="SNAPSHOT_V3_REQUIRES_BIRTHTIME",
        ):
            connection.execute(
                """
                UPDATE snapshots
                SET complete = 1,
                    immutable = 1,
                    snapshot_schema_version = 3,
                    checksum_algorithm = 'SHA-256',
                    serializer_version = '0B-SNAPSHOT-CANONICAL-JSON-V3',
                    snapshot_checksum = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                WHERE id = 'snapshot-a'
                """
            )


def test_catalog_migration_enforces_composite_head_foreign_key(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())
        connection.execute(
            "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'missing-revision')"
            )


def test_recovery_migration_creates_journal_skeleton_and_enforces_epoch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
        plan = recovery_migration_plan()

        apply_sqlite_migrations(connection, plan)

        assert current_schema_version(connection, plan.store) == 11
        assert _table_names(connection) >= {
            "lease_counters",
            "resource_leases",
            "recovery_intent_segments",
            "recovery_operations",
            "recovery_events",
            "recovery_epochs",
            "recovery_intents",
            "recovery_intent_steps",
            "schema_migrations",
            "store_identity",
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO recovery_intents (epoch_id, id, correlation_id, state)
                    VALUES ('missing-epoch', 'intent-a', 'corr-a', 'PREPARED')
                """
            )
        assert "trg_recovery_intent_segments_immutable_after_durable" in _trigger_names(
            connection
        )
        assert "source_precondition_json" in _column_names(
            connection,
            "recovery_operations",
        )
        assert "staging_failure_count" in _column_names(
            connection,
            "recovery_operations",
        )
        assert {
            "staging_retry_backoff_ms",
            "staging_retry_not_before_utc",
        } <= _column_names(connection, "recovery_operations")


def test_migration_runner_rejects_wrong_store_identity(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())

        with pytest.raises(
            SqliteMigrationViolation, match="MIGRATION_STORE_IDENTITY_MISMATCH"
        ):
            apply_sqlite_migrations(connection, recovery_migration_plan())


def test_migration_history_is_immutable_after_recording(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, catalog_migration_plan())

        with pytest.raises(sqlite3.IntegrityError, match="history is immutable"):
            connection.execute(
                "UPDATE schema_migrations SET name = 'changed' WHERE version = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="history is immutable"):
            connection.execute("DELETE FROM schema_migrations WHERE version = 1")


def test_migration_runner_rejects_changed_historical_sql(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        plan = catalog_migration_plan()
        apply_sqlite_migrations(connection, plan)
        first = plan.migrations[0]
        changed_plan = replace(
            plan,
            migrations=(
                replace(first, statements=(*first.statements, "SELECT 1")),
                *plan.migrations[1:],
            ),
        )

        with pytest.raises(
            SqliteMigrationViolation,
            match="MIGRATION_HISTORY_CHECKSUM_MISMATCH",
        ):
            apply_sqlite_migrations(connection, changed_plan)


def test_migration_runner_rejects_schema_newer_than_runtime(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        plan = catalog_migration_plan()
        apply_sqlite_migrations(connection, plan)
        connection.execute(
            """
            INSERT INTO schema_migrations (
                store,
                version,
                name,
                migration_checksum
            )
                    VALUES ('catalog', 56, 'future_migration', ?)
            """,
            ("f" * 64,),
        )
        connection.commit()

        with pytest.raises(
            SqliteMigrationViolation,
            match="MIGRATION_SCHEMA_NEWER_THAN_RUNTIME",
        ):
            apply_sqlite_migrations(connection, plan)


def test_migration_runner_rejects_legacy_history_gap_without_backfill(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        plan = catalog_migration_plan()
        connection.execute(
            """
            CREATE TABLE store_identity (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                store TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO store_identity (singleton, store) VALUES (1, 'catalog')"
        )
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                store TEXT NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                applied_utc TEXT NOT NULL,
                PRIMARY KEY (store, version),
                UNIQUE (store, name)
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO schema_migrations (store, version, name, applied_utc)
            VALUES ('catalog', ?, ?, '2026-07-31T00:00:00.000Z')
            """,
            (
                (1, plan.migrations[0].name),
                (3, plan.migrations[2].name),
            ),
        )
        connection.commit()

        with pytest.raises(
            SqliteMigrationViolation,
            match="MIGRATION_HISTORY_GAP",
        ):
            apply_sqlite_migrations(connection, plan)

        assert "migration_checksum" not in _column_names(
            connection,
            "schema_migrations",
        )


def test_migration_runner_backfills_valid_legacy_history_checksums(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        plan = catalog_migration_plan()
        apply_sqlite_migrations(connection, plan)
        for trigger_name in (
            "trg_schema_migrations_valid_insert",
            "trg_schema_migrations_immutable_update",
            "trg_schema_migrations_immutable_delete",
        ):
            connection.execute(f"DROP TRIGGER {trigger_name}")
        connection.execute(
            "ALTER TABLE schema_migrations RENAME TO schema_migrations_current"
        )
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                store TEXT NOT NULL CHECK (store IN ('catalog', 'recovery')),
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                applied_utc TEXT NOT NULL,
                PRIMARY KEY (store, version),
                UNIQUE (store, name)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_migrations (store, version, name, applied_utc)
            SELECT store, version, name, applied_utc
            FROM schema_migrations_current
            """
        )
        connection.execute("DROP TABLE schema_migrations_current")
        connection.commit()

        preflight = inspect_sqlite_migration_state(connection, plan)

        assert preflight.initialized
        assert preflight.current_version == 55
        assert preflight.target_version == 55
        assert preflight.checksum_backfill_required
        assert "migration_checksum" not in _column_names(
            connection,
            "schema_migrations",
        )

        apply_sqlite_migrations(connection, plan)

        assert len(_migration_checksums(connection)) == len(plan.migrations)
        assert all(
            checksum == migration_checksum(migration)
            for checksum, migration in zip(
                _migration_checksums(connection),
                plan.migrations,
                strict=True,
            )
        )
        assert {
            "trg_schema_migrations_valid_insert",
            "trg_schema_migrations_immutable_update",
            "trg_schema_migrations_immutable_delete",
        } <= _trigger_names(connection)


def test_migration_preflight_rejects_unmanaged_nonempty_schema(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unknown_owner (id INTEGER PRIMARY KEY)")
        connection.commit()

        with pytest.raises(
            SqliteMigrationViolation,
            match="MIGRATION_UNMANAGED_SCHEMA",
        ):
            inspect_sqlite_migration_state(connection, catalog_migration_plan())

        assert _table_names(connection) == {"unknown_owner"}


def test_catalog_filter_version_migration_backfills_existing_revisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    plan = catalog_migration_plan()
    version_27_plan = replace(plan, migrations=plan.migrations[:27])
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, version_27_plan)
        connection.execute(
            "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
        )
        connection.execute(
            """
            INSERT INTO filter_sets (job_id, id, description)
            VALUES ('job-a', 'filter-a', 'standard backup defaults')
            """
        )
        connection.execute(
            """
            INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
            """
        )
        connection.commit()

        apply_sqlite_migrations(connection, plan)

        assert connection.execute(
            """
            SELECT version, rules_hash, rules_json
            FROM filter_set_versions
            WHERE job_id = 'job-a' AND filter_set_id = 'filter-a'
            """
        ).fetchone() == (1, FILTER_RULES_HASH, FILTER_RULES_JSON)
        assert connection.execute(
            """
            SELECT filter_set_id, filter_set_version
            FROM job_revisions
            WHERE job_id = 'job-a' AND id = 'job-rev-a'
            """
        ).fetchone() == ("filter-a", 1)
        assert connection.execute(
            """
            SELECT filter_set_id, filter_set_version
            FROM job_revision_filter_bindings
            WHERE job_id = 'job-a' AND job_revision_id = 'job-rev-a'
            """
        ).fetchone() == ("filter-a", 1)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_catalog_endpoint_generation_migration_backfills_and_enforces_exact_bindings(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    plan = catalog_migration_plan()
    version_28_plan = replace(plan, migrations=plan.migrations[:28])
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, version_28_plan)
        connection.execute("INSERT INTO endpoints (id) VALUES ('endpoint-a')")
        connection.executemany(
            """
            INSERT INTO endpoint_revisions (
                endpoint_id,
                id,
                display_name,
                root_uri,
                created_utc
            )
            VALUES ('endpoint-a', ?, ?, ?, ?)
            """,
            (
                (
                    "endpoint-rev-a",
                    "Endpoint A",
                    "file:///C:/Endpoint",
                    "2026-07-31T00:00:00.000Z",
                ),
                (
                    "endpoint-rev-b",
                    "Endpoint B",
                    "file:///C:/Endpoint",
                    "2026-07-31T00:00:01.000Z",
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO endpoint_heads (endpoint_id, active_revision_id)
            VALUES ('endpoint-a', 'endpoint-rev-b')
            """
        )
        connection.execute(
            "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
        )
        connection.execute(
            "INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')"
        )
        _insert_filter_version(connection, job_id="job-a", filter_set_id="filter-a")
        connection.execute(
            """
            INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
            """
        )
        connection.execute(
            """
            INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
            """
        )
        connection.execute(
            """
            INSERT INTO analysis_targets (
                analysis_id,
                endpoint_id,
                endpoint_revision_id
            )
            VALUES ('analysis-a', 'endpoint-a', 'endpoint-rev-b')
            """
        )
        connection.execute(
            """
            INSERT INTO snapshots (
                id,
                analysis_id,
                endpoint_id,
                endpoint_revision_id
            )
            VALUES ('snapshot-a', 'analysis-a', 'endpoint-a', 'endpoint-rev-b')
            """
        )
        connection.execute(
            "INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')"
        )
        connection.execute(
            """
            INSERT INTO plan_endpoints (
                plan_id,
                analysis_id,
                endpoint_id,
                endpoint_revision_id,
                snapshot_id,
                role,
                capabilities_hash,
                root_case_context_hash
            )
            VALUES (
                'plan-a',
                'analysis-a',
                'endpoint-a',
                'endpoint-rev-b',
                'snapshot-a',
                'SOURCE',
                'capabilities-a',
                'case-a'
            )
            """
        )
        connection.commit()

        apply_sqlite_migrations(connection, plan)

        assert connection.execute(
            """
            SELECT id, generation
            FROM endpoint_revisions
            WHERE endpoint_id = 'endpoint-a'
            ORDER BY generation
            """
        ).fetchall() == [("endpoint-rev-a", 1), ("endpoint-rev-b", 2)]
        assert connection.execute(
            "SELECT endpoint_generation FROM snapshots WHERE id = 'snapshot-a'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT endpoint_generation FROM plan_endpoints WHERE plan_id = 'plan-a'"
        ).fetchone() == (2,)

        with pytest.raises(
            sqlite3.IntegrityError,
            match="ENDPOINT_GENERATION_MUST_ADVANCE",
        ):
            connection.execute(
                """
                INSERT INTO endpoint_revisions (
                    endpoint_id,
                    id,
                    display_name,
                    root_uri,
                    generation
                )
                VALUES (
                    'endpoint-a',
                    'endpoint-rev-skipped',
                    'Skipped',
                    'file:///C:/Endpoint',
                    4
                )
                """
            )
        connection.execute(
            """
            INSERT INTO endpoint_revisions (
                endpoint_id,
                id,
                display_name,
                root_uri,
                generation
            )
            VALUES (
                'endpoint-a',
                'endpoint-rev-c',
                'Endpoint C',
                'file:///C:/Endpoint',
                3
            )
            """
        )
        connection.execute(
            """
            INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-b', 'job-a', 'job-rev-a')
            """
        )
        connection.execute(
            """
            INSERT INTO analysis_targets (
                analysis_id,
                endpoint_id,
                endpoint_revision_id
            )
            VALUES ('analysis-b', 'endpoint-a', 'endpoint-rev-b')
            """
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="ENDPOINT_GENERATION_MISMATCH"
        ):
            connection.execute(
                """
                INSERT INTO snapshots (
                    id,
                    analysis_id,
                    endpoint_id,
                    endpoint_revision_id,
                    endpoint_generation
                )
                VALUES (
                    'snapshot-wrong',
                    'analysis-b',
                    'endpoint-a',
                    'endpoint-rev-b',
                    1
                )
                """
            )
        connection.execute(
            """
            INSERT INTO snapshots (
                id,
                analysis_id,
                endpoint_id,
                endpoint_revision_id,
                endpoint_generation
            )
            VALUES (
                'snapshot-b',
                'analysis-b',
                'endpoint-a',
                'endpoint-rev-b',
                2
            )
            """
        )
        connection.execute(
            "INSERT INTO plans (id, analysis_id) VALUES ('plan-b', 'analysis-b')"
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="ENDPOINT_GENERATION_MISMATCH"
        ):
            connection.execute(
                """
                INSERT INTO plan_endpoints (
                    plan_id,
                    analysis_id,
                    endpoint_id,
                    endpoint_revision_id,
                    endpoint_generation,
                    snapshot_id,
                    role,
                    capabilities_hash,
                    root_case_context_hash
                )
                VALUES (
                    'plan-b',
                    'analysis-b',
                    'endpoint-a',
                    'endpoint-rev-b',
                    1,
                    'snapshot-b',
                    'SOURCE',
                    'capabilities-a',
                    'case-a'
                )
                """
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_catalog_operation_target_binding_migration_backfills_single_target_plans(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    plan = catalog_migration_plan()
    version_32_plan = replace(plan, migrations=plan.migrations[:32])
    with sqlite3.connect(database) as connection:
        apply_sqlite_connection_policy(
            connection, catalog_critical_writer_policy(database)
        )
        apply_sqlite_migrations(connection, version_32_plan)
        _insert_catalog_parent_rows(connection)
        connection.execute(
            "INSERT INTO plans (id, analysis_id) VALUES ('plan-a', 'analysis-a')"
        )
        connection.execute(
            """
            INSERT INTO planned_operations (plan_id, id, operation_type)
            VALUES ('plan-a', 'op-a', 'COPY_NEW')
            """
        )
        connection.execute(
            """
            INSERT INTO plan_endpoints (
                plan_id,
                analysis_id,
                endpoint_id,
                endpoint_revision_id,
                endpoint_generation,
                snapshot_id,
                role,
                target_ordinal,
                capabilities_hash,
                root_case_context_hash,
                required_owner_installation_id,
                required_ownership_epoch,
                control_schema_version,
                planned_operations,
                planned_bytes
            )
            VALUES (
                'plan-a',
                'analysis-a',
                'endpoint-a',
                'endpoint-rev-a',
                1,
                'snapshot-a',
                'TARGET_WRITABLE',
                0,
                'capabilities-a',
                'case-a',
                'owner-a',
                1,
                1,
                1,
                4
            )
            """
        )
        connection.execute(
            """
            INSERT INTO plan_operation_seal_details (
                plan_id,
                operation_id,
                sequence_no,
                execution_phase,
                stable_order_key,
                target_precondition_kind,
                reason_code,
                risk_level,
                target_relative_path,
                planned_bytes
            )
            VALUES (
                'plan-a',
                'op-a',
                1,
                20,
                '020:A.txt',
                'ABSENT',
                'COPY_NEW',
                'LOW',
                'A.txt',
                4
            )
            """
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
                'legacy',
                1,
                1,
                'MANUAL_REVIEW_REQUIRED',
                'SHA-256',
                '0B-CANONICAL-JSON-V1',
                ?,
                '{"highest":"LOW"}',
                1,
                4
            )
            """,
            ("a" * 64,),
        )
        connection.commit()

        apply_sqlite_migrations(connection, plan)

        assert connection.execute(
            """
            SELECT target_endpoint_id
            FROM plan_operation_seal_details
            WHERE plan_id = 'plan-a' AND operation_id = 'op-a'
            """
        ).fetchone() == ("endpoint-a",)
        with pytest.raises(sqlite3.IntegrityError, match="PLAN_SEAL_IMMUTABLE"):
            connection.execute(
                """
                UPDATE plan_operation_seal_details
                SET target_endpoint_id = NULL
                WHERE plan_id = 'plan-a' AND operation_id = 'op-a'
                """
            )


def _insert_catalog_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES ('endpoint-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('endpoint-a', 'endpoint-rev-a', 'USB', 'file:///E:/Backup')
        """
    )
    connection.execute(
        """
        INSERT INTO endpoint_heads (endpoint_id, active_revision_id)
            VALUES ('endpoint-a', 'endpoint-rev-a')
        """
    )
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
    )
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')"
    )
    _insert_filter_version(connection, job_id="job-a", filter_set_id="filter-a")
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
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'endpoint-a', 'endpoint-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('snapshot-a', 'analysis-a', 'endpoint-a', 'endpoint-rev-a')
        """
    )


def _insert_immutable_revision_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES ('endpoint-a')")
    connection.executemany(
        """
        INSERT INTO endpoint_revisions (
            endpoint_id,
            id,
            display_name,
            root_uri,
            generation
        )
        VALUES ('endpoint-a', ?, ?, ?, ?)
        """,
        (
            ("endpoint-rev-a", "Source A", "file:///C:/Source", 1),
            ("endpoint-rev-b", "Source B", "file:///C:/Source", 2),
        ),
    )
    connection.execute(
        """
        INSERT INTO endpoint_heads (endpoint_id, active_revision_id)
        VALUES ('endpoint-a', 'endpoint-rev-a')
        """
    )
    connection.execute(
        "INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')"
    )
    connection.executemany(
        """
        INSERT INTO filter_sets (job_id, id, description)
        VALUES ('job-a', ?, ?)
        """,
        (
            ("filter-a", "Filter A"),
            ("filter-b", "Filter B"),
        ),
    )
    _insert_filter_version(connection, job_id="job-a", filter_set_id="filter-a")
    _insert_filter_version(connection, job_id="job-a", filter_set_id="filter-b")
    connection.executemany(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
        VALUES ('job-a', ?, ?)
        """,
        (
            ("job-rev-a", "filter-a"),
            ("job-rev-b", "filter-b"),
        ),
    )
    connection.execute(
        """
        INSERT INTO job_heads (job_id, active_revision_id)
        VALUES ('job-a', 'job-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO standard_backup_job_drafts (
            draft_id,
            schema_version,
            defaults_json,
            targets_json
        )
        VALUES ('draft-a', 1, '{}', '[]')
        """
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
            'request-a',
            'idempotency-a',
            'Source A',
            'C:\\Source',
            '{}',
            '[]'
        )
        """
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
            'job-rev-a',
            'SOURCE',
            0,
            'endpoint-a',
            'endpoint-rev-a',
            'REGISTRATION_PENDING'
        )
        """
    )


def _insert_filter_version(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    filter_set_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO filter_set_versions (
            job_id,
            filter_set_id,
            version,
            rules_hash,
            rules_json
        )
        VALUES (?, ?, 1, ?, ?)
        """,
        (job_id, filter_set_id, FILTER_RULES_HASH, FILTER_RULES_JSON),
    )


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _migration_checksums(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            """
            SELECT migration_checksum
            FROM schema_migrations
            ORDER BY version
            """
        )
    )


def _foreign_key(
    connection: sqlite3.Connection,
    table: str,
    parent_table: str,
    child_columns: tuple[str, ...],
    parent_columns: tuple[str, ...],
) -> bool:
    grouped: dict[int, list[sqlite3.Row | tuple[object, ...]]] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
        grouped.setdefault(int(row[0]), []).append(row)
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda row: int(row[1]))
        if str(ordered[0][2]) != parent_table:
            continue
        if (
            tuple(str(row[3]) for row in ordered) == child_columns
            and tuple(str(row[4]) for row in ordered) == parent_columns
        ):
            return True
    return False


def _index_is_unique(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> bool | None:
    for index_row in connection.execute(f"PRAGMA index_list({table})"):
        index_name = str(index_row[1])
        index_columns = tuple(
            str(column_row[2])
            for column_row in connection.execute(f"PRAGMA index_info({index_name})")
        )
        if index_columns == columns:
            return bool(index_row[2])
    return None


def _index_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _trigger_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND name NOT LIKE 'sqlite_%'
            """
        )
    }
