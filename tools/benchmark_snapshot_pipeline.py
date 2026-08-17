from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from contextlib import closing
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mediasync_home.adapters.sqlite.snapshots import SqliteSnapshotEntryStore  # noqa: E402
from mediasync_home.application.snapshots import (  # noqa: E402
    SnapshotDirectoryCoverage,
    SnapshotFileEntry,
    SnapshotSealRequest,
    snapshot_entry_batch,
)


DEFAULT_BATCH_ENTRIES = 1_000
ONE_MILLION_SCAN_RSS_GATE_BYTES = 400 * 1024 * 1024


def run_benchmark(
    entry_count: int,
    *,
    batch_entries: int = DEFAULT_BATCH_ENTRIES,
    database: Path | None = None,
) -> dict[str, object]:
    if entry_count < 1 or batch_entries < 1:
        raise ValueError("entry_count and batch_entries must be positive")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if database is None:
        temporary = tempfile.TemporaryDirectory(prefix="mediasync-snapshot-benchmark-")
        database = Path(temporary.name) / "catalog.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    baseline_rss = _working_set_bytes()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                _create_benchmark_schema(connection)
                store = SqliteSnapshotEntryStore(connection)
                batch_count = 0
                for offset in range(0, entry_count, batch_entries):
                    stop = min(entry_count, offset + batch_entries)
                    entries = tuple(_entry(index) for index in range(offset, stop))
                    store.commit_snapshot_entry_batch(
                        snapshot_entry_batch(
                            snapshot_id="snapshot-benchmark",
                            sequence_no=batch_count,
                            entries=entries,
                            coverage_updates=(
                                (_root_coverage(),) if batch_count == 0 else ()
                            ),
                        )
                    )
                    batch_count += 1
                sealed = store.seal_snapshot(
                    SnapshotSealRequest(
                        snapshot_id="snapshot-benchmark",
                        expected_entry_count=entry_count,
                        expected_total_bytes=entry_count,
                        expected_batch_count=batch_count,
                        expected_directory_coverage_count=1,
                    )
                )
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        elapsed_seconds = time.perf_counter() - started
        _current_bytes, tracemalloc_peak_bytes = tracemalloc.get_traced_memory()
        peak_rss_bytes = _peak_working_set_bytes()
        result = {
            "benchmark": "snapshot-pipeline-bounded-memory-v1",
            "batch_entries": batch_entries,
            "database_bytes": database.stat().st_size,
            "elapsed_seconds": round(elapsed_seconds, 6),
            "entries_per_second": round(entry_count / elapsed_seconds, 2),
            "entry_count": entry_count,
            "peak_rss_bytes": peak_rss_bytes,
            "rss_growth_upper_bound_bytes": max(0, peak_rss_bytes - baseline_rss),
            "snapshot_checksum": sealed.snapshot_checksum,
            "tracemalloc_peak_bytes": tracemalloc_peak_bytes,
            "python": platform.python_version(),
            "platform": platform.platform(),
        }
        if entry_count >= 1_000_000:
            result["peak_rss_gate_bytes"] = ONE_MILLION_SCAN_RSS_GATE_BYTES
            result["peak_rss_gate_passed"] = (
                peak_rss_bytes <= ONE_MILLION_SCAN_RSS_GATE_BYTES
            )
        return result
    finally:
        tracemalloc.stop()
        if temporary is not None:
            temporary.cleanup()


def _entry(index: int) -> SnapshotFileEntry:
    relative_path = f"media/{index // 10_000:04d}/file-{index:09d}.bin"
    return SnapshotFileEntry(
        entry_id=f"entry-{index:09d}",
        relative_path=relative_path,
        comparison_key=relative_path,
        object_type="file",
        size_bytes=1,
        birthtime_ns=index + 1,
        identity_fingerprint_hash=f"{index % (16**64):064x}",
    )


def _root_coverage() -> SnapshotDirectoryCoverage:
    return SnapshotDirectoryCoverage(
        relative_path=".",
        comparison_key=".",
        coverage_state="COMPLETE",
        case_mode="CASE_INSENSITIVE",
        case_mode_evidence="BENCHMARK_FIXED_CASE_MODE_V1",
        case_context_hash="1" * 64,
    )


def _create_benchmark_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        CREATE TABLE snapshots (
            id TEXT PRIMARY KEY,
            endpoint_id TEXT NOT NULL,
            immutable INTEGER NOT NULL DEFAULT 0,
            complete INTEGER NOT NULL DEFAULT 0,
            entry_count INTEGER NOT NULL DEFAULT 0,
            total_bytes INTEGER NOT NULL DEFAULT 0,
            filter_decision_count INTEGER NOT NULL DEFAULT 0,
            snapshot_schema_version INTEGER,
            checksum_algorithm TEXT,
            serializer_version TEXT,
            snapshot_checksum TEXT,
            sealed_utc TEXT,
            scan_error_count INTEGER NOT NULL DEFAULT 0,
            volatile_directory_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE snapshot_batches (
            snapshot_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            payload_hash TEXT NOT NULL,
            entry_count INTEGER NOT NULL,
            coverage_update_count INTEGER NOT NULL,
            issue_count INTEGER NOT NULL,
            filter_decision_count INTEGER NOT NULL,
            approximate_bytes INTEGER NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, sequence_no)
        );
        CREATE TABLE file_entries (
            snapshot_id TEXT NOT NULL,
            endpoint_id TEXT NOT NULL,
            id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            comparison_key TEXT NOT NULL,
            object_type TEXT NOT NULL,
            size_bytes INTEGER,
            birthtime_ns INTEGER,
            identity_fingerprint_hash TEXT,
            PRIMARY KEY (snapshot_id, id)
        );
        CREATE INDEX file_entries_comparison
            ON file_entries(snapshot_id, comparison_key, relative_path, id);
        CREATE TABLE directory_coverage (
            snapshot_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            comparison_key TEXT NOT NULL,
            coverage_state TEXT NOT NULL,
            case_mode TEXT NOT NULL,
            case_mode_evidence TEXT NOT NULL,
            case_context_hash TEXT NOT NULL,
            case_probe_error TEXT,
            PRIMARY KEY (snapshot_id, relative_path)
        );
        CREATE TABLE snapshot_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            error_code TEXT,
            sanitized_message TEXT,
            blocks_destructive_actions INTEGER NOT NULL
        );
        CREATE TABLE snapshot_filter_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            object_type TEXT NOT NULL,
            decision_state TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            matched_rule_id TEXT,
            evaluation_stage TEXT NOT NULL
        );
        CREATE TABLE case_collision_groups (
            snapshot_id TEXT NOT NULL,
            id TEXT NOT NULL,
            comparison_key TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, id),
            UNIQUE (snapshot_id, comparison_key)
        );
        CREATE TABLE case_collision_members (
            snapshot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            file_entry_id TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, group_id, file_entry_id)
        );
        INSERT INTO snapshots (id, endpoint_id)
        VALUES ('snapshot-benchmark', 'endpoint-benchmark');
        """
    )


def _working_set_bytes() -> int:
    if os.name != "nt":
        return 0
    counters = _process_memory_counters()
    return 0 if counters is None else int(counters.working_set_size)


def _peak_working_set_bytes() -> int:
    if os.name != "nt":
        return _working_set_bytes()
    counters = _process_memory_counters()
    return 0 if counters is None else int(counters.peak_working_set_size)


def _process_memory_counters() -> _ProcessMemoryCounters | None:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = ctypes.c_void_p
    get_process_memory_info = psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    get_process_memory_info.restype = ctypes.c_int
    if not get_process_memory_info(
        get_current_process(),
        ctypes.byref(counters),
        counters.cb,
    ):
        return None
    return counters


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark bounded snapshot persistence and sealing."
    )
    parser.add_argument("--entries", type=int, default=1_000_000)
    parser.add_argument("--batch-entries", type=int, default=DEFAULT_BATCH_ENTRIES)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    result = run_benchmark(
        args.entries,
        batch_entries=args.batch_entries,
        database=args.database,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    sys.stdout.write(rendered + "\n")
    if args.enforce and result.get("peak_rss_gate_passed") is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
