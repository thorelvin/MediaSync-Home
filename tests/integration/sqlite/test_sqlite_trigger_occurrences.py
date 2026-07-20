from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.trigger_occurrences import (
    SqliteTriggerOccurrenceStore,
    SqliteTriggerOccurrenceStoreError,
)
from mediasync_home.application.trigger_occurrences import (
    TriggerDeliveryContext,
    TriggerKind,
    TriggerOccurrenceState,
    build_enqueue_trigger_occurrence_payload,
    build_trigger_occurrence,
    parse_enqueue_trigger_occurrence_command,
)


DELIVERY_ID = "11111111-1111-4111-8111-111111111111"
RETRY_DELIVERY_ID = "22222222-2222-4222-8222-222222222222"


def test_sqlite_trigger_occurrence_records_and_deduplicates_scheduled_retry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job(connection)
        store = SqliteTriggerOccurrenceStore(connection)
        first = _occurrence(delivery_id=DELIVERY_ID)
        retry = _occurrence(delivery_id=RETRY_DELIVERY_ID)

        stored = store.record_received(first)
        replay = store.record_received(retry)

        assert stored.deduplicated is False
        assert replay.deduplicated is True
        assert replay.occurrence == first
        assert replay.occurrence.first_delivery_id == DELIVERY_ID
        assert _row_count(connection, "trigger_occurrences") == 1


def test_sqlite_trigger_occurrence_rejects_same_key_payload_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job(connection)
        store = SqliteTriggerOccurrenceStore(connection)
        store.record_received(_occurrence(delivery_id=DELIVERY_ID))

        with pytest.raises(SqliteTriggerOccurrenceStoreError, match="TRIGGER_OCCURRENCE_CONFLICT"):
            store.record_received(
                _occurrence(
                    delivery_id=RETRY_DELIVERY_ID,
                    task_definition_hash="c" * 64,
                )
            )


def test_sqlite_trigger_occurrence_replay_uses_tombstone_after_compaction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_job(connection)
        store = SqliteTriggerOccurrenceStore(connection)
        occurrence = _occurrence(delivery_id=DELIVERY_ID)
        store.record_received(occurrence)
        store.mark_terminal(
            deduplication_key=occurrence.deduplication_key,
            state=TriggerOccurrenceState.SUCCEEDED,
            terminal_effect_hash="d" * 64,
        )
        compacted = store.compact_terminal_trigger_occurrence(occurrence.deduplication_key)

        replay = store.record_received(_occurrence(delivery_id=RETRY_DELIVERY_ID))

        assert compacted.state is TriggerOccurrenceState.SUCCEEDED
        assert replay.deduplicated is True
        assert replay.compacted is True
        assert replay.occurrence.state is TriggerOccurrenceState.SUCCEEDED
        assert replay.occurrence.terminal_effect_hash == "d" * 64
        assert _row_count(connection, "trigger_occurrences") == 0
        assert _row_count(connection, "effect_dedup_tombstones") == 1
        with pytest.raises(SqliteTriggerOccurrenceStoreError, match="TRIGGER_OCCURRENCE_CONFLICT"):
            store.record_received(
                _occurrence(
                    delivery_id=RETRY_DELIVERY_ID,
                    task_definition_hash="c" * 64,
                )
            )


def _occurrence(
    *,
    delivery_id: str,
    task_definition_hash: str = "b" * 64,
):
    payload = build_enqueue_trigger_occurrence_payload(
        schedule_id="schedule-a",
        schedule_revision_hash="a" * 64,
        delivery=TriggerDeliveryContext(
            delivery_id=delivery_id,
            observed_start_utc="2026-07-20T12:00:02.000Z",
            trigger_kind=TriggerKind.SCHEDULED_TIME,
            task_definition_hash=task_definition_hash,
            scheduled_slot_utc="2026-07-20T12:00:00.000Z",
        ),
    )
    command = parse_enqueue_trigger_occurrence_command(
        request_id=delivery_id,
        idempotency_key=delivery_id,
        payload=payload,
    )
    return build_trigger_occurrence(
        installation_id="preview-a",
        job_id="job-a",
        command=command,
    )


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_job(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")


def _row_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()
    assert row is not None
    return int(row[0])
