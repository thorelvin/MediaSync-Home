from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, recovery_migration_plan
from mediasync_home.adapters.sqlite.recovery_intents import (
    SqliteRecoveryIntentSegmentStore,
    SqliteRecoveryIntentSegmentStoreError,
)
from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    durable_recovery_intent_segment,
)


def test_sqlite_recovery_intent_segment_store_publishes_and_loads_durable_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)
        segment = _segment()

        published = store.publish_intent_segment(segment)

        assert published == segment
        assert store.load_intent_segment("segment-a") == segment
        row = connection.execute(
            """
            SELECT lease_id, fencing_token, durability_state, state
            FROM recovery_intent_segments
            WHERE id = ?
            """,
            ("segment-a",),
        ).fetchone()
        assert row == ("lease-a", 1, "DURABLE", "DURABLE")
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_is_idempotent_for_same_segment(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)
        segment = _segment()

        assert store.publish_intent_segment(segment) == segment
        assert store.publish_intent_segment(segment) == segment

        conflicting = _segment(segment_hash="b" * 64)
        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="IDEMPOTENCY_CONFLICT"):
            store.publish_intent_segment(conflicting)
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_requires_active_matching_lease(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        lease_store = _register_resource_lease(connection)
        lease_store.release_resource_lease(lease_id="lease-a")
        store = SqliteRecoveryIntentSegmentStore(connection)

        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="LEASE_MISMATCH"):
            store.publish_intent_segment(_segment())

        assert connection.in_transaction is False
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_chains_segments_by_previous_hash(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)

        first = _segment()
        second = _segment(
            segment_id="segment-b",
            segment_sequence=1,
            relative_path="installations/owner-a/recovery/run-a/segment-000001.intent.jsonl",
            segment_hash="b" * 64,
            previous_segment_hash=first.segment_hash,
        )
        broken = _segment(
            segment_id="segment-c",
            segment_sequence=2,
            relative_path="installations/owner-a/recovery/run-a/segment-000002.intent.jsonl",
            segment_hash="c" * 64,
            previous_segment_hash="0" * 64,
        )

        assert store.publish_intent_segment(first) == first
        assert store.publish_intent_segment(second) == second
        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="CHAIN_MISMATCH"):
            store.publish_intent_segment(broken)
    finally:
        connection.close()


def test_sqlite_recovery_intent_segment_store_rejects_duplicate_sequence(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        store = SqliteRecoveryIntentSegmentStore(connection)

        store.publish_intent_segment(_segment())
        duplicate_sequence = _segment(
            segment_id="segment-b",
            relative_path="installations/owner-a/recovery/run-a/segment-duplicate.intent.jsonl",
            segment_hash="b" * 64,
        )

        with pytest.raises(SqliteRecoveryIntentSegmentStoreError, match="PERSISTENCE_CONFLICT"):
            store.publish_intent_segment(duplicate_sequence)
    finally:
        connection.close()


def test_recovery_intent_segment_migration_blocks_durable_field_mutation(
    tmp_path: Path,
) -> None:
    connection = _prepared_recovery_connection(tmp_path)
    try:
        _register_resource_lease(connection)
        SqliteRecoveryIntentSegmentStore(connection).publish_intent_segment(_segment())

        with pytest.raises(sqlite3.IntegrityError, match="INTENT_SEGMENT_IMMUTABLE"):
            connection.execute(
                """
                UPDATE recovery_intent_segments
                SET segment_hash = ?
                WHERE id = ?
                """,
                ("b" * 64, "segment-a"),
            )
    finally:
        connection.close()


def _prepared_recovery_connection(tmp_path: Path) -> sqlite3.Connection:
    database = tmp_path / "recovery.sqlite"
    connection = sqlite3.connect(database)
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())
    return connection


def _register_resource_lease(connection: sqlite3.Connection) -> SqliteResourceLeaseStore:
    store = SqliteResourceLeaseStore(connection)
    assert store.register_acquired_resource_lease(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    ) == 1
    return store


def _segment(**overrides: object) -> RecoveryIntentSegment:
    values: dict[str, object] = {
        "segment_id": "segment-a",
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "owner_installation_id": "owner-a",
        "ownership_epoch": 1,
        "lease_id": "lease-a",
        "fencing_token": 1,
        "segment_sequence": 0,
        "relative_path": "installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        "schema_version": 1,
        "operation_count": 2,
        "byte_count": 256,
        "segment_hash": "a" * 64,
        "previous_segment_hash": None,
    }
    values.update(overrides)
    return durable_recovery_intent_segment(**values)
