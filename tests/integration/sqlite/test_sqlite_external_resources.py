from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.external_resources import (
    SqliteExternalResourceStateStore,
    SqliteExternalResourceStateStoreError,
)
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.application.external_resources import (
    ExternalResourceStartupReconciliationRequest,
    ExternalResourceState,
    ExternalResourceType,
)


def test_sqlite_external_resource_state_claims_and_completes_current_desired_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)

        desired = store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            desired_hash="a" * 64,
        )
        claimed = store.claim_next_pending_external_resource(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        )
        completed = store.mark_external_resource_in_sync(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            claim_token="claim-a",
            observed_hash="a" * 64,
        )

        assert desired.state is ExternalResourceState.PENDING
        assert claimed is not None
        assert claimed.state is ExternalResourceState.CLAIMED
        assert claimed.claim_owner_instance_id == "host-a"
        assert claimed.claim_token == "claim-a"
        assert claimed.attempt_count == 1
        assert completed.state is ExternalResourceState.IN_SYNC
        assert completed.observed_generation == 1
        assert completed.observed_hash == "a" * 64
        assert completed.claim_token is None
        assert completed.last_success_utc is not None


def test_sqlite_external_resource_state_rejects_late_completion_after_generation_update(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            desired_hash="a" * 64,
        )
        store.claim_next_pending_external_resource(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        )
        updated = store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=2,
            desired_hash="b" * 64,
        )

        with pytest.raises(
            SqliteExternalResourceStateStoreError,
            match="EXTERNAL_RESOURCE_COMPLETION_CLAIM_MISMATCH",
        ):
            store.mark_external_resource_in_sync(
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                resource_id="schedule-a",
                desired_generation=1,
                claim_token="claim-a",
                observed_hash="a" * 64,
            )

        assert updated.state is ExternalResourceState.PENDING
        assert updated.claim_token is None
        assert store.load_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
        ) == updated


def test_sqlite_external_resource_state_blocks_claimed_resource_with_error(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            desired_hash="a" * 64,
        )
        store.claim_next_pending_external_resource(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        )

        blocked = store.mark_external_resource_blocked(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            claim_token="claim-a",
            error_code="TASK_SCHEDULER_BINARY_DRIFT",
        )

        assert blocked.state is ExternalResourceState.BLOCKED
        assert blocked.claim_token is None
        assert blocked.last_error_code == "TASK_SCHEDULER_BINARY_DRIFT"


def test_sqlite_external_resource_state_claims_pending_in_resource_order(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-b",
            desired_generation=1,
            desired_hash="b" * 64,
        )
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            desired_hash="a" * 64,
        )

        claimed = store.claim_next_pending_external_resource(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        )

        assert claimed is not None
        assert claimed.resource_id == "schedule-a"


def test_sqlite_external_resource_state_requeues_claimed_from_inactive_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=1,
            desired_hash="a" * 64,
        )
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-b",
            desired_generation=1,
            desired_hash="b" * 64,
        )
        store.claim_next_pending_external_resource(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-a",
            claim_token="claim-a",
            claim_ttl_ms=30_000,
        )
        store.claim_next_pending_external_resource(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            owner_instance_id="host-live",
            claim_token="claim-b",
            claim_ttl_ms=30_000,
        )

        report = store.requeue_claimed_after_startup(
            ExternalResourceStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                inactive_owner_instance_ids=("host-a",),
                limit=10,
            )
        )
        requeued = store.load_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
        )
        still_claimed = store.load_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-b",
        )

        assert report.scanned == 1
        assert report.requeued_resource_ids == ("schedule-a",)
        assert requeued is not None
        assert requeued.state is ExternalResourceState.PENDING
        assert requeued.claim_token is None
        assert requeued.claim_owner_instance_id is None
        assert requeued.claim_generation == 2
        assert requeued.last_error_code == "EXTERNAL_RESOURCE_CLAIM_REQUEUED_AFTER_STARTUP"
        assert still_claimed is not None
        assert still_claimed.state is ExternalResourceState.CLAIMED
        assert still_claimed.claim_owner_instance_id == "host-live"


def test_sqlite_external_resource_state_requeue_is_bounded(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)
        for resource_id, desired_hash in (("schedule-a", "a" * 64), ("schedule-b", "b" * 64)):
            store.upsert_desired_resource_state(
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                resource_id=resource_id,
                desired_generation=1,
                desired_hash=desired_hash,
            )
            store.claim_next_pending_external_resource(
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                owner_instance_id="host-a",
                claim_token=f"claim-{resource_id}",
                claim_ttl_ms=30_000,
            )

        report = store.requeue_claimed_after_startup(
            ExternalResourceStartupReconciliationRequest(
                reconciler_instance_id="host-b",
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                inactive_owner_instance_ids=("host-a",),
                limit=1,
            )
        )

        assert report.scanned == 1
        assert report.requeued_resource_ids == ("schedule-a",)
        assert store.load_external_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-b",
        ).state is ExternalResourceState.CLAIMED


def test_sqlite_external_resource_state_rejects_generation_regression_and_hash_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        store = SqliteExternalResourceStateStore(connection)
        store.upsert_desired_resource_state(
            resource_type=ExternalResourceType.TASK_SCHEDULER,
            resource_id="schedule-a",
            desired_generation=2,
            desired_hash="b" * 64,
        )

        with pytest.raises(
            SqliteExternalResourceStateStoreError,
            match="EXTERNAL_RESOURCE_DESIRED_GENERATION_REGRESSION",
        ):
            store.upsert_desired_resource_state(
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                resource_id="schedule-a",
                desired_generation=1,
                desired_hash="a" * 64,
            )
        with pytest.raises(
            SqliteExternalResourceStateStoreError,
            match="EXTERNAL_RESOURCE_DESIRED_HASH_CONFLICT",
        ):
            store.upsert_desired_resource_state(
                resource_type=ExternalResourceType.TASK_SCHEDULER,
                resource_id="schedule-a",
                desired_generation=2,
                desired_hash="c" * 64,
            )


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())
