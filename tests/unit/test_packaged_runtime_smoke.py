from __future__ import annotations

import sqlite3
from pathlib import Path

from mediasync_home.adapters.sqlite.migrations import (
    apply_sqlite_migrations,
    catalog_migration_plan,
)
from tools.packaged_runtime_smoke import (
    CommandResult,
    _engine_host_reconciled_task,
    _insert_plan_parent_rows,
    _gui_status_accepted,
    _parse_json_object_lines,
    _prepare_packaged_scheduler_state,
    _source_trigger_routed,
)


def test_packaged_runtime_smoke_parses_json_object_lines() -> None:
    objects = _parse_json_object_lines('{"event":"one"}\nnot-json\n{"event":"two","value":2}\n')

    assert objects == (
        {"event": "one"},
        {"event": "two", "value": 2},
    )


def test_packaged_runtime_smoke_accepts_host_locator_trigger_rejection() -> None:
    result = CommandResult(
        returncode=2,
        stdout_tail="",
        stderr_tail="",
        stdout_json={
            "status": "REJECTED",
            "reason": "ENGINE_HOST_UNAVAILABLE",
            "payload": {
                "reason": "HOST_LOCATOR_PUBLICATION_UNAVAILABLE",
                "scope": "0B_SAME_USER_LOCAL_PREVIEW",
            },
        },
    )

    assert _source_trigger_routed(result) is True


def test_packaged_runtime_smoke_rejects_launcher_default_output() -> None:
    result = CommandResult(
        returncode=0,
        stdout_tail="",
        stderr_tail="",
        stdout_json={
            "role": "launcher",
            "ready": True,
        },
    )

    assert _source_trigger_routed(result) is False


def test_packaged_runtime_smoke_requires_scheduler_reconciliation_counts() -> None:
    result = CommandResult(
        returncode=0,
        stdout_tail="",
        stderr_tail="",
        stdout_json_lines=(
            {
                "event": "ENGINE_HOST_PIPE_STARTING",
                "run_executor_staging_backend": "robocopy",
                "task_scheduler_reconciliation": {
                    "resources_applied": 1,
                    "resources_blocked": 0,
                    "resources_completed": 1,
                    "resources_reconciled": 1,
                    "resources_staged": 1,
                },
            },
            {"event": "ENGINE_HOST_PIPE_STOPPED", "served_requests": 1},
        ),
    )

    assert _engine_host_reconciled_task(result) is True


def test_packaged_runtime_smoke_requires_accepted_gui_status() -> None:
    accepted = CommandResult(
        returncode=0,
        stdout_tail="",
        stderr_tail="",
        stdout_json={"status": "ACCEPTED", "payload": {"status": "ok"}},
    )
    rejected = CommandResult(
        returncode=2,
        stdout_tail="",
        stderr_tail="",
        stdout_json={"status": "REJECTED", "reason": "ENGINE_HOST_UNAVAILABLE"},
    )

    assert _gui_status_accepted(accepted) is True
    assert _gui_status_accepted(rejected) is False


def test_packaged_runtime_smoke_plan_fixture_matches_current_catalog_contract() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        apply_sqlite_migrations(connection, catalog_migration_plan())

        _insert_plan_parent_rows(connection)

        binding = connection.execute(
            """
            SELECT filter_set_id, filter_set_version
            FROM job_revision_filter_bindings
            WHERE job_id = 'job-packaged-smoke'
                AND job_revision_id = 'job-rev-packaged-smoke'
            """
        ).fetchone()

    assert binding == ("filter-packaged-smoke", 1)


def test_packaged_runtime_smoke_fixture_initializes_both_state_stores(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"

    schedule = _prepare_packaged_scheduler_state(
        state_root=state_root,
        installation_id="packaged-smoke-test",
        schedule_id="schedule-packaged-smoke",
        executable_path=tmp_path / "MediaSyncHome0B.exe",
    )

    assert schedule.schedule_id == "schedule-packaged-smoke"
    assert (state_root / "catalog.sqlite").is_file()
    assert (state_root / "recovery.sqlite").is_file()
