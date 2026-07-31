from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mediasync_home.application.progress_read_models import (
    RunProgressSnapshot,
    RunProgressSnapshotStore,
)
from mediasync_home.application.recovery_operations import TERMINAL_PHASES
from mediasync_home.application.runs import RunState


_SEQUENCE_FACTOR = 1_000_000_000
_RATE_WINDOW = timedelta(seconds=60)
_MAX_RATE_SAMPLES = 128


class SqliteRunProgressSnapshotStore(RunProgressSnapshotStore):
    def __init__(
        self,
        *,
        catalog_runs: RunProgressSnapshotStore,
        recovery_connection: sqlite3.Connection,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog_runs = catalog_runs
        self._recovery_connection = recovery_connection
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))

    def load_run_progress_snapshot(self, run_id: str) -> RunProgressSnapshot | None:
        snapshot = self._catalog_runs.load_run_progress_snapshot(run_id)
        if snapshot is None:
            return None

        sequence_row = self._recovery_connection.execute(
            """
            SELECT coalesce(max(run_sequence), 0)
            FROM recovery_events
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        recovery_sequence = 0 if sequence_row is None else int(sequence_row[0])
        active = self._load_active_operation(run_id)
        transferred_operations, transferred_bytes = self._load_transfer_totals(run_id)
        completed_by_target = self._load_completed_target_totals(run_id)
        targets = tuple(
            replace(
                target,
                completed_operations=max(
                    target.completed_operations,
                    completed_by_target.get(target.run_target_id, (0, 0))[0],
                ),
                completed_bytes=max(
                    target.completed_bytes,
                    completed_by_target.get(target.run_target_id, (0, 0))[1],
                ),
            )
            for target in snapshot.targets
        )
        completed_operations = sum(target.completed_operations for target in targets)
        completed_bytes = sum(target.completed_bytes for target in targets)

        bytes_per_second: float | None = None
        eta_seconds: int | None = None
        if snapshot.state is RunState.EXECUTING:
            bytes_per_second = self._load_average_rate(
                run_id=run_id,
                started_utc=snapshot.started_utc,
            )
            if bytes_per_second is not None and bytes_per_second > 0:
                remaining_bytes = max(snapshot.planned_bytes - transferred_bytes, 0)
                eta_seconds = int(remaining_bytes / bytes_per_second)

        terminal = snapshot.terminal
        return replace(
            snapshot,
            sequence_no=(snapshot.sequence_no * _SEQUENCE_FACTOR) + recovery_sequence,
            completed_operations=completed_operations,
            completed_bytes=completed_bytes,
            targets=targets,
            transferred_operations=transferred_operations,
            transferred_bytes=transferred_bytes,
            active_relative_path=None if terminal or active is None else active[0],
            active_phase=None if terminal or active is None else active[1],
            active_planned_bytes=None if terminal or active is None else active[2],
            bytes_per_second=None if terminal else bytes_per_second,
            eta_seconds=None if terminal else eta_seconds,
        )

    def _load_active_operation(self, run_id: str) -> tuple[str, str, int] | None:
        terminal_values = tuple(phase.value for phase in TERMINAL_PHASES)
        placeholders = ", ".join("?" for _ in terminal_values)
        row = self._recovery_connection.execute(
            f"""
            SELECT
                coalesce(source_relative_path, final_relative_path),
                phase,
                planned_bytes
            FROM recovery_operations
            WHERE run_id = ?
                AND phase NOT IN ({placeholders})
            ORDER BY
                CASE WHEN phase = 'PLANNED' THEN 1 ELSE 0 END,
                plan_sequence_no,
                operation_id
            LIMIT 1
            """,
            (run_id, *terminal_values),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), str(row[1]), int(row[2])

    def _load_transfer_totals(self, run_id: str) -> tuple[int, int]:
        row = self._recovery_connection.execute(
            """
            SELECT
                count(*),
                coalesce(sum(operations.planned_bytes), 0)
            FROM recovery_events AS events
            INNER JOIN recovery_operations AS operations
                ON operations.run_id = events.run_id
                AND operations.operation_id = events.operation_id
            WHERE events.run_id = ?
                AND events.to_phase = 'TRANSFERRED'
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return 0, 0
        return int(row[0]), int(row[1])

    def _load_completed_target_totals(self, run_id: str) -> dict[str, tuple[int, int]]:
        rows = self._recovery_connection.execute(
            """
            SELECT
                run_target_id,
                count(*),
                coalesce(sum(planned_bytes), 0)
            FROM recovery_operations
            WHERE run_id = ?
                AND phase IN ('CATALOG_RECORDED', 'CLEANED')
            GROUP BY run_target_id
            """,
            (run_id,),
        ).fetchall()
        return {
            str(row[0]): (int(row[1]), int(row[2]))
            for row in rows
        }

    def _load_average_rate(self, *, run_id: str, started_utc: str) -> float | None:
        now = self._utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        started = _parse_utc(started_utc)
        window_start = max(started, now - _RATE_WINDOW)
        rows = self._recovery_connection.execute(
            """
            SELECT
                events.event_utc,
                operations.planned_bytes
            FROM recovery_events AS events
            INNER JOIN recovery_operations AS operations
                ON operations.run_id = events.run_id
                AND operations.operation_id = events.operation_id
            WHERE events.run_id = ?
                AND events.to_phase = 'TRANSFERRED'
            ORDER BY events.event_id DESC
            LIMIT ?
            """,
            (run_id, _MAX_RATE_SAMPLES),
        ).fetchall()
        bytes_in_window = sum(
            int(row[1])
            for row in rows
            if window_start <= _parse_utc(str(row[0])) <= now
        )
        elapsed_seconds = (now - window_start).total_seconds()
        if bytes_in_window <= 0 or elapsed_seconds < 1:
            return None
        return bytes_in_window / elapsed_seconds


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
