from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from mediasync_home.adapters.sqlite.run_progress import SqliteRunProgressSnapshotStore
from mediasync_home.application.progress_read_models import (
    RunProgressSnapshot,
    RunTargetProgressSnapshot,
)
from mediasync_home.application.runs import RunState, RunTargetState


class _CatalogProgress:
    def __init__(self, snapshot: RunProgressSnapshot) -> None:
        self.snapshot = snapshot

    def load_run_progress_snapshot(self, run_id: str) -> RunProgressSnapshot | None:
        return self.snapshot if self.snapshot.run_id == run_id else None


def test_sqlite_run_progress_combines_active_file_transfer_rate_and_eta() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE recovery_operations (
            run_id TEXT NOT NULL,
            run_target_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            source_relative_path TEXT,
                final_relative_path TEXT NOT NULL,
                planned_bytes INTEGER NOT NULL,
                plan_sequence_no INTEGER NOT NULL,
                staging_failure_count INTEGER NOT NULL DEFAULT 0,
                staging_retry_backoff_ms INTEGER,
                staging_retry_not_before_utc TEXT,
                last_error_code TEXT
        );
        CREATE TABLE recovery_events (
            event_id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            run_sequence INTEGER NOT NULL,
            operation_id TEXT,
            to_phase TEXT NOT NULL,
            event_utc TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        """
        INSERT INTO recovery_operations (
            run_id,
            run_target_id,
            operation_id,
            phase,
            source_relative_path,
            final_relative_path,
            planned_bytes,
            plan_sequence_no
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                "run-a",
                "run-target-a",
                "operation-a",
                "CLEANED",
                "photos/a.jpg",
                "photos/a.jpg",
                120_000_000,
                0,
            ),
            (
                "run-a",
                "run-target-a",
                "operation-b",
                "STAGING_ALLOCATED",
                "photos/b.jpg",
                "photos/b.jpg",
                120_000_000,
                1,
            ),
        ),
    )
    connection.execute(
        """
        UPDATE recovery_operations
        SET
            staging_failure_count = 1,
            staging_retry_backoff_ms = 900,
            staging_retry_not_before_utc = '2026-07-31T10:01:01.000Z',
            last_error_code = 'LOCAL_STAGING_TRANSFER_FAILED'
        WHERE operation_id = 'operation-b'
        """
    )
    connection.execute(
        """
        INSERT INTO recovery_events (
            event_id,
            run_id,
            run_sequence,
            operation_id,
            to_phase,
            event_utc
        )
        VALUES (1, 'run-a', 5, 'operation-a', 'TRANSFERRED', '2026-07-31T10:00:30Z')
        """
    )
    store = SqliteRunProgressSnapshotStore(
        catalog_runs=_CatalogProgress(_snapshot()),
        recovery_connection=connection,
        utc_now=lambda: datetime(2026, 7, 31, 10, 1, 0, tzinfo=timezone.utc),
    )

    snapshot = store.load_run_progress_snapshot("run-a")

    assert snapshot is not None
    assert snapshot.sequence_no == 7_000_000_005
    assert snapshot.active_relative_path == "photos/b.jpg"
    assert snapshot.active_phase == "STAGING_ALLOCATED"
    assert snapshot.active_planned_bytes == 120_000_000
    assert snapshot.active_staging_failure_count == 1
    assert snapshot.active_retry_backoff_ms == 900
    assert snapshot.active_retry_not_before_utc == "2026-07-31T10:01:01.000Z"
    assert snapshot.active_last_error_code == "LOCAL_STAGING_TRANSFER_FAILED"
    assert snapshot.completed_operations == 1
    assert snapshot.completed_bytes == 120_000_000
    assert snapshot.transferred_operations == 1
    assert snapshot.transferred_bytes == 120_000_000
    assert snapshot.bytes_per_second == pytest.approx(2_000_000)
    assert snapshot.eta_seconds == 60
    assert snapshot.targets[0].completed_operations == 1


def _snapshot() -> RunProgressSnapshot:
    return RunProgressSnapshot(
        run_id="run-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        plan_id="plan-a",
        sequence_no=7,
        state=RunState.EXECUTING,
        terminal=False,
        started_utc="2026-07-31T10:00:00Z",
        finished_utc=None,
        planned_operations=2,
        completed_operations=0,
        planned_bytes=240_000_000,
        completed_bytes=0,
        warning_count=0,
        error_count=0,
        targets=(
            RunTargetProgressSnapshot(
                run_target_id="run-target-a",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                state=RunTargetState.EXECUTING,
                planned_operations=2,
                completed_operations=0,
                planned_bytes=240_000_000,
                completed_bytes=0,
                warning_count=0,
                error_count=0,
            ),
        ),
    )
