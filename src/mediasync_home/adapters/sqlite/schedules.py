from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from mediasync_home.application.schedules import (
    ScheduleDefinition,
    ScheduleStore,
    ScheduleViolation,
    validate_schedule_definition,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


class SqliteScheduleStoreError(ValueError):
    pass


class SqliteScheduleStore(ScheduleStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_schedule(self, schedule: ScheduleDefinition) -> None:
        try:
            validate_schedule_definition(schedule)
            self._connection.execute(
                """
                INSERT INTO schedules (
                    id,
                    job_id,
                    plan_id,
                    plan_checksum,
                    trigger_type,
                    configuration_json,
                    definition_generation,
                    desired_definition_hash,
                    time_zone_id,
                    dst_policy,
                    misfire_policy,
                    coalescing_window_seconds,
                    task_logon_type,
                    requires_network,
                    run_only_when_logged_on,
                    enabled,
                    row_version,
                    last_triggered_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    job_id = excluded.job_id,
                    plan_id = excluded.plan_id,
                    plan_checksum = excluded.plan_checksum,
                    trigger_type = excluded.trigger_type,
                    configuration_json = excluded.configuration_json,
                    definition_generation = excluded.definition_generation,
                    desired_definition_hash = excluded.desired_definition_hash,
                    time_zone_id = excluded.time_zone_id,
                    dst_policy = excluded.dst_policy,
                    misfire_policy = excluded.misfire_policy,
                    coalescing_window_seconds = excluded.coalescing_window_seconds,
                    task_logon_type = excluded.task_logon_type,
                    requires_network = excluded.requires_network,
                    run_only_when_logged_on = excluded.run_only_when_logged_on,
                    enabled = excluded.enabled,
                    row_version = excluded.row_version,
                    last_triggered_utc = excluded.last_triggered_utc
                """,
                _schedule_parameters(schedule),
            )
            self._connection.commit()
        except ScheduleViolation as exc:
            raise SqliteScheduleStoreError("SCHEDULE_VALIDATION_FAILED") from exc
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteScheduleStoreError("SCHEDULE_SAVE_FAILED") from exc

    def load_schedule(self, schedule_id: str) -> ScheduleDefinition | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                job_id,
                plan_id,
                plan_checksum,
                trigger_type,
                configuration_json,
                definition_generation,
                desired_definition_hash,
                time_zone_id,
                dst_policy,
                misfire_policy,
                coalescing_window_seconds,
                task_logon_type,
                requires_network,
                run_only_when_logged_on,
                enabled,
                row_version,
                last_triggered_utc
            FROM schedules
            WHERE id = ?
            """,
            (schedule_id,),
        ).fetchone()
        if row is None:
            return None
        return _schedule_from_row(row)


def _schedule_from_row(row: Sequence[object]) -> ScheduleDefinition:
    return ScheduleDefinition(
        schedule_id=str(row[0]),
        job_id=str(row[1]),
        plan_id=str(row[2]),
        plan_checksum=str(row[3]),
        trigger_type=TriggerKind(str(row[4])),
        configuration_json=str(row[5]),
        definition_generation=_int_field(row[6]),
        desired_definition_hash=str(row[7]),
        time_zone_id=None if row[8] is None else str(row[8]),
        dst_policy=str(row[9]),
        misfire_policy=str(row[10]),
        coalescing_window_seconds=_int_field(row[11]),
        task_logon_type=str(row[12]),
        requires_network=bool(row[13]),
        run_only_when_logged_on=bool(row[14]),
        enabled=bool(row[15]),
        row_version=_int_field(row[16]),
        last_triggered_utc=None if row[17] is None else str(row[17]),
    )


def _schedule_parameters(schedule: ScheduleDefinition) -> tuple[object, ...]:
    return (
        schedule.schedule_id,
        schedule.job_id,
        schedule.plan_id,
        schedule.plan_checksum,
        schedule.trigger_type.value,
        schedule.configuration_json,
        schedule.definition_generation,
        schedule.desired_definition_hash,
        schedule.time_zone_id,
        schedule.dst_policy,
        schedule.misfire_policy,
        schedule.coalescing_window_seconds,
        schedule.task_logon_type,
        int(schedule.requires_network),
        int(schedule.run_only_when_logged_on),
        int(schedule.enabled),
        schedule.row_version,
        schedule.last_triggered_utc,
    )


def _int_field(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError("SQLite integer field must be int-compatible")
