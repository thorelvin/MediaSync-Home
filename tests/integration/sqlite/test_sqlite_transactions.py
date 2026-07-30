from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from mediasync_home.adapters.sqlite.connection_policy import SqliteFailureKind
from mediasync_home.adapters.sqlite.transactions import SqliteImmediateTransactionRunner
from mediasync_home.application.command_receipts import CommandEffectStorageFailure
from mediasync_home.application.state_capacity import (
    GIB,
    StateCapacityGate,
    StateCapacityObservation,
    StateCapacityStatus,
)


@dataclass(frozen=True)
class _HealthyCapacityProbe:
    def measure(self) -> StateCapacityObservation:
        return StateCapacityObservation(
            state_size_bytes=0,
            local_free_space_bytes=20 * GIB,
            measurement_complete=True,
            scanned_entry_count=0,
        )


def test_sqlite_full_stops_writer_once_and_preserves_committed_recovery_evidence(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    recovery_path = tmp_path / "recovery.sqlite"
    with sqlite3.connect(recovery_path) as recovery:
        recovery.execute("CREATE TABLE recovery_evidence (id TEXT PRIMARY KEY)")
        recovery.execute("INSERT INTO recovery_evidence (id) VALUES ('proof-before-full')")
        recovery.commit()

    gate = StateCapacityGate(probe=_HealthyCapacityProbe())
    observed_failures: list[SqliteFailureKind] = []

    def observe_failure(failure_kind: SqliteFailureKind) -> None:
        observed_failures.append(failure_kind)
        if failure_kind is SqliteFailureKind.FULL:
            gate.latch_sqlite_full("catalog")

    with sqlite3.connect(catalog_path) as catalog:
        catalog.execute("PRAGMA journal_mode = DELETE")
        catalog.execute(
            "CREATE TABLE catalog_growth (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
        )
        catalog.commit()
        page_count_row = catalog.execute("PRAGMA page_count").fetchone()
        assert page_count_row is not None
        catalog.execute(f"PRAGMA max_page_count = {int(page_count_row[0]) + 1}")
        runner = SqliteImmediateTransactionRunner(
            catalog,
            failure_observer=observe_failure,
        )
        calls = 0

        def fill_catalog() -> None:
            nonlocal calls
            calls += 1
            catalog.execute(
                "INSERT INTO catalog_growth (payload) VALUES (?)",
                (b"x" * 100_000,),
            )

        with pytest.raises(CommandEffectStorageFailure) as exc_info:
            runner.run(fill_catalog)

        assert exc_info.value.error_code == "SQLITE_FULL"
        assert exc_info.value.retryable is False
        assert calls == 1
        assert observed_failures == [SqliteFailureKind.FULL]
        assert catalog.in_transaction is False
        assert catalog.execute("SELECT count(*) FROM catalog_growth").fetchone() == (0,)

    capacity_report = gate.latest_report()
    assert capacity_report.status is StateCapacityStatus.HARD_STOP
    assert capacity_report.reason_code == "STATE_CAPACITY_SQLITE_FULL"
    assert capacity_report.sqlite_full_store == "catalog"
    with sqlite3.connect(recovery_path) as recovery:
        assert recovery.execute(
            "SELECT id FROM recovery_evidence"
        ).fetchone() == ("proof-before-full",)
