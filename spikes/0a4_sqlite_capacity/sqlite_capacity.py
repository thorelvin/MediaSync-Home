from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


SNAPSHOT_ID = "snapshot-0a4"
SCHEMA_VERSION = 1
PAGE_SIZE = 4096

HANDOFF_STATES = ("PREPARED", "PEER_COMMITTED", "SOURCE_CONFIRMED", "COMPLETED", "ABORTED", "AMBIGUOUS")


class CrashInjected(RuntimeError):
    pass


class RestoreSetError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateStore:
    mode: str
    root: Path
    catalog: Path
    recovery: Path | None = None

    @property
    def members(self) -> dict[str, Path]:
        if self.mode == "one_db":
            return {"state": self.catalog}
        return {"catalog": self.catalog, "recovery": self.recovery_path}

    @property
    def recovery_path(self) -> Path:
        if self.recovery is None:
            return self.catalog
        return self.recovery


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json_hash(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_size(path: Path, suffix: str) -> int:
    sidecar = path.with_name(path.name + suffix)
    return sidecar.stat().st_size if sidecar.exists() else 0


def current_rss_bytes() -> int:
    if os.name == "nt":
        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCountersEx),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(ProcessMemoryCountersEx)
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if ok:
            return int(counters.WorkingSetSize)
        return 0

    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(rss * 1024)
    except Exception:
        return 0


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[index]


def connect(db_path: Path, *, synchronous: str = "NORMAL", query_only: bool = False) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
    except sqlite3.DatabaseError:
        pass
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA temp_store = MEMORY")
    try:
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.DatabaseError):
        pass
    if query_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, kind: str = "IMMEDIATE") -> Iterator[None]:
    conn.execute(f"BEGIN {kind}")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def create_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;

        INSERT OR REPLACE INTO metadata(key, value) VALUES
            ('schema_version', '1'),
            ('candidate', '0A.4');

        CREATE TABLE IF NOT EXISTS file_entries (
            entry_id INTEGER PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            parent_id INTEGER NOT NULL,
            basename TEXT NOT NULL,
            comparison_key TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            content_hash TEXT,
            coverage_state TEXT NOT NULL CHECK (coverage_state IN ('COMPLETE', 'ERROR')),
            path_depth INTEGER NOT NULL
        ) STRICT;

        CREATE INDEX IF NOT EXISTS idx_file_entries_parent_page
            ON file_entries(snapshot_id, parent_id, comparison_key, entry_id);
        CREATE INDEX IF NOT EXISTS idx_file_entries_hash_size
            ON file_entries(size_bytes, content_hash)
            WHERE content_hash IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_file_entries_coverage
            ON file_entries(snapshot_id, coverage_state, parent_id);

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('CREATED_NOT_READY', 'READY', 'COMPLETED')),
            payload_hash TEXT NOT NULL,
            created_utc TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS command_receipts (
            command_key TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('EFFECT_PREPARED', 'ACCEPTED')),
            payload_hash TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS store_handoffs (
            handoff_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('PREPARED', 'PEER_COMMITTED', 'SOURCE_CONFIRMED', 'COMPLETED', 'ABORTED', 'AMBIGUOUS')),
            payload_hash TEXT NOT NULL,
            updated_utc TEXT NOT NULL
        ) STRICT;
        """
    )


def create_recovery_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recovery_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;

        INSERT OR REPLACE INTO recovery_metadata(key, value) VALUES
            ('schema_version', '1'),
            ('candidate', '0A.4');

        CREATE TABLE IF NOT EXISTS recovery_runs (
            run_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('BOUND', 'FINAL_VERIFIED', 'CATALOG_RECORDED')),
            payload_hash TEXT NOT NULL,
            created_utc TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS recovery_handoffs (
            handoff_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('PREPARED', 'PEER_COMMITTED', 'SOURCE_CONFIRMED', 'COMPLETED', 'ABORTED', 'AMBIGUOUS')),
            payload_hash TEXT NOT NULL,
            updated_utc TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS recovery_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            created_utc TEXT NOT NULL
        ) STRICT;
        """
    )


def prepare_candidate(root: Path, mode: str) -> CandidateStore:
    candidate_root = root / mode
    if mode == "one_db":
        store = CandidateStore(mode=mode, root=candidate_root, catalog=candidate_root / "state.sqlite")
        with connect(store.catalog, synchronous="FULL") as conn:
            create_catalog_schema(conn)
            create_recovery_schema(conn)
        return store
    if mode == "two_db":
        store = CandidateStore(
            mode=mode,
            root=candidate_root,
            catalog=candidate_root / "catalog.sqlite",
            recovery=candidate_root / "recovery.sqlite",
        )
        with connect(store.catalog, synchronous="NORMAL") as catalog:
            create_catalog_schema(catalog)
        with connect(store.recovery_path, synchronous="FULL") as recovery:
            create_recovery_schema(recovery)
        return store
    raise ValueError(f"unknown candidate mode: {mode}")


def file_row(index: int) -> tuple[Any, ...]:
    parent_id = index // 512
    stem = f"IMG_{index:08d}"
    extension = "jpg" if index % 17 else "raw"
    basename = f"{stem}.{extension}"
    size_bytes = 4096 + ((index * 1_315_423_911) % 75_000_000)
    content_key = index % 1000 if index % 10 == 0 else index
    content_hash = f"{content_key:064x}"
    coverage_state = "ERROR" if index % 997 == 0 else "COMPLETE"
    mtime_ns = 1_700_000_000_000_000_000 + index * 1_000_000
    return (
        SNAPSHOT_ID,
        parent_id,
        basename,
        basename.casefold(),
        size_bytes,
        mtime_ns,
        content_hash,
        coverage_state,
        1 + (index % 9),
    )


def iter_batches(row_count: int, batch_size: int) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for index in range(row_count):
        batch.append(file_row(index))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def explain_plan(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[str]:
    return [str(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def timed_query_p95(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
    *,
    repetitions: int = 30,
) -> dict[str, Any]:
    durations_ms: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        list(conn.execute(sql, params))
        durations_ms.append((time.perf_counter() - started) * 1000.0)
    return {
        "p50_ms": round(percentile(durations_ms, 50), 3),
        "p95_ms": round(percentile(durations_ms, 95), 3),
        "max_ms": round(max(durations_ms), 3),
        "plan": explain_plan(conn, sql, params),
    }


def measure_catalog_load(
    db_path: Path,
    *,
    row_count: int,
    batch_size: int = 10_000,
    query_repetitions: int = 30,
) -> dict[str, Any]:
    rss_peak = current_rss_bytes()
    started = time.perf_counter()
    with connect(db_path, synchronous="NORMAL") as conn:
        create_catalog_schema(conn)
        insert_sql = """
            INSERT INTO file_entries(
                snapshot_id, parent_id, basename, comparison_key, size_bytes,
                mtime_ns, content_hash, coverage_state, path_depth
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        inserted = 0
        for batch in iter_batches(row_count, batch_size):
            with transaction(conn):
                conn.executemany(insert_sql, batch)
            inserted += len(batch)
            rss_peak = max(rss_peak, current_rss_bytes())

        insert_seconds = time.perf_counter() - started
        wal_bytes_before_checkpoint = sidecar_size(db_path, "-wal")

        sample_hash_row = file_row(10)
        parent_id = max(0, (row_count // 2) // 512)
        queries = {
            "parent_page": timed_query_p95(
                conn,
                """
                SELECT entry_id, basename, size_bytes
                FROM file_entries
                WHERE snapshot_id = ? AND parent_id = ?
                ORDER BY comparison_key, entry_id
                LIMIT 100
                """,
                (SNAPSHOT_ID, parent_id),
                repetitions=query_repetitions,
            ),
            "hash_lookup": timed_query_p95(
                conn,
                """
                SELECT entry_id
                FROM file_entries
                WHERE size_bytes = ? AND content_hash = ?
                LIMIT 50
                """,
                (sample_hash_row[4], sample_hash_row[6]),
                repetitions=query_repetitions,
            ),
            "coverage_count": timed_query_p95(
                conn,
                """
                SELECT COUNT(*)
                FROM file_entries
                WHERE snapshot_id = ? AND coverage_state = ?
                """,
                (SNAPSHOT_ID, "ERROR"),
                repetitions=query_repetitions,
            ),
        }
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        wal_bytes_after_checkpoint = sidecar_size(db_path, "-wal")
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])

    return {
        "rows_inserted": inserted,
        "batch_size": batch_size,
        "insert_seconds": round(insert_seconds, 3),
        "rows_per_second": round(inserted / insert_seconds, 1) if insert_seconds else 0,
        "db_bytes": db_path.stat().st_size,
        "wal_bytes_before_checkpoint": wal_bytes_before_checkpoint,
        "wal_bytes_after_checkpoint": wal_bytes_after_checkpoint,
        "page_count": page_count,
        "page_size": page_size,
        "peak_rss_bytes": rss_peak,
        "queries": queries,
    }


def seed_recovery_events(store: CandidateStore, count: int = 1000) -> dict[str, Any]:
    started = time.perf_counter()
    db_path = store.recovery_path
    with connect(db_path, synchronous="FULL") as conn:
        create_recovery_schema(conn)
        with transaction(conn):
            for index in range(count):
                run_id = f"run-{index:06d}"
                payload_hash = stable_json_hash({"run_id": run_id, "index": index})
                conn.execute(
                    """
                    INSERT OR IGNORE INTO recovery_runs(run_id, state, payload_hash, created_utc)
                    VALUES (?, 'BOUND', ?, ?)
                    """,
                    (run_id, payload_hash, utc_now()),
                )
                conn.execute(
                    """
                    INSERT INTO recovery_events(run_id, phase, payload_hash, created_utc)
                    VALUES (?, 'BOUND', ?, ?)
                    """,
                    (run_id, payload_hash, utc_now()),
                )
        wal_before = sidecar_size(db_path, "-wal")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    return {
        "events": count,
        "insert_seconds": round(time.perf_counter() - started, 3),
        "db_bytes": db_path.stat().st_size,
        "wal_bytes_before_checkpoint": wal_before,
        "page_count": page_count,
    }


def one_db_start_run(store: CandidateStore, command_key: str, run_id: str, payload: dict[str, Any], crash_at: str | None = None) -> None:
    if store.mode != "one_db":
        raise ValueError("one_db_start_run requires one_db store")
    payload_hash = stable_json_hash(payload)
    with connect(store.catalog, synchronous="FULL") as conn:
        create_catalog_schema(conn)
        create_recovery_schema(conn)
        with transaction(conn):
            conn.execute(
                "INSERT INTO runs(run_id, state, payload_hash, created_utc) VALUES (?, 'READY', ?, ?)",
                (run_id, payload_hash, utc_now()),
            )
            conn.execute(
                """
                INSERT INTO command_receipts(command_key, run_id, state, payload_hash)
                VALUES (?, ?, 'ACCEPTED', ?)
                """,
                (command_key, run_id, payload_hash),
            )
            conn.execute(
                "INSERT INTO recovery_runs(run_id, state, payload_hash, created_utc) VALUES (?, 'BOUND', ?, ?)",
                (run_id, payload_hash, utc_now()),
            )
            if crash_at == "before_commit":
                raise CrashInjected("before_commit")


def two_db_start_run(
    store: CandidateStore,
    command_key: str,
    run_id: str,
    payload: dict[str, Any],
    crash_at: str | None = None,
) -> str:
    if store.mode != "two_db":
        raise ValueError("two_db_start_run requires two_db store")
    handoff_id = stable_json_hash({"command_key": command_key, "run_id": run_id})[:32]
    payload_hash = stable_json_hash(payload)

    with connect(store.catalog, synchronous="FULL") as catalog:
        create_catalog_schema(catalog)
        with transaction(catalog):
            catalog.execute(
                "INSERT INTO runs(run_id, state, payload_hash, created_utc) VALUES (?, 'CREATED_NOT_READY', ?, ?)",
                (run_id, payload_hash, utc_now()),
            )
            catalog.execute(
                """
                INSERT INTO command_receipts(command_key, run_id, state, payload_hash)
                VALUES (?, ?, 'EFFECT_PREPARED', ?)
                """,
                (command_key, run_id, payload_hash),
            )
            catalog.execute(
                """
                INSERT INTO store_handoffs(handoff_id, direction, state, payload_hash, updated_utc)
                VALUES (?, 'catalog_to_recovery', 'PREPARED', ?, ?)
                """,
                (handoff_id, payload_hash, utc_now()),
            )
    if crash_at == "after_catalog_prepared":
        raise CrashInjected(crash_at)

    with connect(store.recovery_path, synchronous="FULL") as recovery:
        create_recovery_schema(recovery)
        with transaction(recovery):
            recovery.execute(
                "INSERT INTO recovery_runs(run_id, state, payload_hash, created_utc) VALUES (?, 'BOUND', ?, ?)",
                (run_id, payload_hash, utc_now()),
            )
            recovery.execute(
                """
                INSERT INTO recovery_handoffs(handoff_id, direction, state, payload_hash, updated_utc)
                VALUES (?, 'catalog_to_recovery', 'PEER_COMMITTED', ?, ?)
                """,
                (handoff_id, payload_hash, utc_now()),
            )
    if crash_at == "after_recovery_peer_committed":
        raise CrashInjected(crash_at)

    with connect(store.catalog, synchronous="FULL") as catalog:
        with transaction(catalog):
            catalog.execute("UPDATE runs SET state = 'READY' WHERE run_id = ?", (run_id,))
            catalog.execute("UPDATE command_receipts SET state = 'ACCEPTED' WHERE command_key = ?", (command_key,))
            catalog.execute(
                "UPDATE store_handoffs SET state = 'SOURCE_CONFIRMED', updated_utc = ? WHERE handoff_id = ?",
                (utc_now(), handoff_id),
            )
    if crash_at == "after_catalog_source_confirmed":
        raise CrashInjected(crash_at)

    reconcile_two_db_runstart(store, handoff_id)
    return handoff_id


def fetch_scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def reconcile_two_db_runstart(store: CandidateStore, handoff_id: str) -> dict[str, Any]:
    with connect(store.catalog, synchronous="FULL") as catalog, connect(store.recovery_path, synchronous="FULL") as recovery:
        create_catalog_schema(catalog)
        create_recovery_schema(recovery)
        catalog_row = catalog.execute(
            "SELECT state, payload_hash FROM store_handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        recovery_row = recovery.execute(
            "SELECT state, payload_hash FROM recovery_handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        if catalog_row is None:
            return {"status": "BLOCKED", "reason": "MISSING_SOURCE_HANDOFF"}

        catalog_state, payload_hash = catalog_row
        if recovery_row is None:
            run_id = fetch_scalar(catalog, "SELECT run_id FROM command_receipts WHERE payload_hash = ?", (payload_hash,))
            with transaction(recovery):
                recovery.execute(
                    "INSERT OR IGNORE INTO recovery_runs(run_id, state, payload_hash, created_utc) VALUES (?, 'BOUND', ?, ?)",
                    (run_id, payload_hash, utc_now()),
                )
                recovery.execute(
                    """
                    INSERT INTO recovery_handoffs(handoff_id, direction, state, payload_hash, updated_utc)
                    VALUES (?, 'catalog_to_recovery', 'PEER_COMMITTED', ?, ?)
                    """,
                    (handoff_id, payload_hash, utc_now()),
                )
            recovery_row = ("PEER_COMMITTED", payload_hash)

        recovery_state, recovery_hash = recovery_row
        if recovery_hash != payload_hash:
            return {"status": "AMBIGUOUS", "reason": "PAYLOAD_HASH_MISMATCH"}

        if catalog_state == "PREPARED" and recovery_state == "PEER_COMMITTED":
            with transaction(catalog):
                catalog.execute(
                    "UPDATE runs SET state = 'READY' WHERE payload_hash = ?",
                    (payload_hash,),
                )
                catalog.execute(
                    "UPDATE command_receipts SET state = 'ACCEPTED' WHERE payload_hash = ?",
                    (payload_hash,),
                )
                catalog.execute(
                    "UPDATE store_handoffs SET state = 'SOURCE_CONFIRMED', updated_utc = ? WHERE handoff_id = ?",
                    (utc_now(), handoff_id),
                )
            catalog_state = "SOURCE_CONFIRMED"

        if catalog_state == "SOURCE_CONFIRMED":
            with transaction(catalog):
                catalog.execute(
                    "UPDATE store_handoffs SET state = 'COMPLETED', updated_utc = ? WHERE handoff_id = ?",
                    (utc_now(), handoff_id),
                )
            with transaction(recovery):
                recovery.execute(
                    "UPDATE recovery_handoffs SET state = 'COMPLETED', updated_utc = ? WHERE handoff_id = ?",
                    (utc_now(), handoff_id),
                )
            return {"status": "COMPLETED"}
        if catalog_state == "COMPLETED" and recovery_state == "COMPLETED":
            return {"status": "COMPLETED"}
        return {"status": "BLOCKED", "catalog_state": catalog_state, "recovery_state": recovery_state}


def inspect_runstart(store: CandidateStore, command_key: str, handoff_id: str | None = None) -> dict[str, Any]:
    with connect(store.catalog, query_only=True) as catalog:
        receipt = catalog.execute(
            "SELECT run_id, state, payload_hash FROM command_receipts WHERE command_key = ?",
            (command_key,),
        ).fetchone()
        result: dict[str, Any] = {"receipt": None}
        if receipt is not None:
            result["receipt"] = {"run_id": receipt[0], "state": receipt[1], "payload_hash": receipt[2]}
        if handoff_id:
            catalog_handoff = catalog.execute(
                "SELECT state, payload_hash FROM store_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            result["catalog_handoff"] = None if catalog_handoff is None else {"state": catalog_handoff[0], "payload_hash": catalog_handoff[1]}
    recovery_path = store.recovery_path
    if recovery_path.exists():
        with connect(recovery_path, query_only=True) as recovery:
            if handoff_id and store.mode == "two_db":
                recovery_handoff = recovery.execute(
                    "SELECT state, payload_hash FROM recovery_handoffs WHERE handoff_id = ?",
                    (handoff_id,),
                ).fetchone()
                result["recovery_handoff"] = None if recovery_handoff is None else {"state": recovery_handoff[0], "payload_hash": recovery_handoff[1]}
            result["recovery_run_count"] = fetch_scalar(recovery, "SELECT COUNT(*) FROM recovery_runs")
    return result


def create_backup_set(store: CandidateStore, backup_root: Path, backup_set_id: str | None = None) -> dict[str, Any]:
    backup_set_id = backup_set_id or uuid.uuid4().hex
    target = backup_root / backup_set_id
    target.mkdir(parents=True, exist_ok=True)
    intent = {
        "backup_set_id": backup_set_id,
        "mode": store.mode,
        "schema_version": SCHEMA_VERSION,
        "created_utc": utc_now(),
        "members": list(store.members),
    }
    (target / "backup-set.intent.json").write_text(json.dumps(intent, sort_keys=True) + "\n", encoding="utf-8")
    members: dict[str, dict[str, Any]] = {}
    for name, source_path in store.members.items():
        backup_path = target / f"{name}.sqlite.backup"
        with sqlite3.connect(str(source_path)) as source, sqlite3.connect(str(backup_path)) as backup:
            source.backup(backup)
        with sqlite3.connect(str(backup_path)) as verify:
            quick_check = verify.execute("PRAGMA quick_check").fetchone()[0]
            page_count = int(verify.execute("PRAGMA page_count").fetchone()[0])
            schema_version = int(verify.execute("PRAGMA user_version").fetchone()[0])
        members[name] = {
            "file": backup_path.name,
            "sha256": sha256_file(backup_path),
            "bytes": backup_path.stat().st_size,
            "quick_check": quick_check,
            "page_count": page_count,
            "schema_version": schema_version,
        }
    manifest = {
        "backup_set_id": backup_set_id,
        "mode": store.mode,
        "state": "COMMITTED",
        "created_utc": utc_now(),
        "member_count": len(members),
        "members": members,
    }
    (target / "backup-set.manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_backup_set(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / "backup-set.manifest.json"
    if not manifest_path.is_file():
        raise RestoreSetError("missing manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") != "COMMITTED":
        raise RestoreSetError("backup set is not committed")
    backup_set_id = manifest.get("backup_set_id")
    for name, member in manifest["members"].items():
        path = backup_dir / member["file"]
        if not path.is_file():
            raise RestoreSetError(f"missing member {name}")
        if sha256_file(path) != member["sha256"]:
            raise RestoreSetError(f"checksum mismatch for {name}")
        if backup_set_id != manifest["backup_set_id"]:
            raise RestoreSetError("backup set id mismatch")
    return manifest


def verify_restore_members(backup_dirs_by_member: dict[str, Path]) -> dict[str, Any]:
    manifests = {name: verify_backup_set(path) for name, path in backup_dirs_by_member.items()}
    backup_set_ids = {manifest["backup_set_id"] for manifest in manifests.values()}
    if len(backup_set_ids) != 1:
        raise RestoreSetError("mixed backup-set epochs are not restorable")
    modes = {manifest["mode"] for manifest in manifests.values()}
    if len(modes) != 1:
        raise RestoreSetError("mixed backup modes are not restorable")
    expected_members = set(next(iter(manifests.values()))["members"])
    if set(backup_dirs_by_member) != expected_members:
        raise RestoreSetError("restore member set does not match manifest")
    return {"status": "RESTORE_SET_VERIFIED", "backup_set_id": next(iter(backup_set_ids)), "mode": next(iter(modes))}


def sqlite_full_probe(root: Path) -> dict[str, Any]:
    probe = root / "sqlite-full"
    store = prepare_candidate(probe, "two_db")
    recovery_payload = stable_json_hash({"proof": "committed-before-full"})
    with connect(store.recovery_path, synchronous="FULL") as recovery:
        create_recovery_schema(recovery)
        with transaction(recovery):
            recovery.execute(
                "INSERT INTO recovery_runs(run_id, state, payload_hash, created_utc) VALUES ('proof-run', 'BOUND', ?, ?)",
                (recovery_payload, utc_now()),
            )

    full_classification = "NOT_TRIGGERED"
    inserted = 0
    with connect(store.catalog, synchronous="NORMAL") as catalog:
        catalog.execute(f"PRAGMA page_size = {PAGE_SIZE}")
        catalog.execute("PRAGMA max_page_count = 48")
        catalog.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_growth(
                id INTEGER PRIMARY KEY,
                payload BLOB NOT NULL
            ) STRICT
            """
        )
        blob = b"x" * 4096
        try:
            while inserted < 10_000:
                with transaction(catalog):
                    catalog.execute("INSERT INTO cache_growth(payload) VALUES (?)", (blob,))
                inserted += 1
        except sqlite3.DatabaseError as exc:
            message = str(exc).lower()
            if "full" in message:
                full_classification = "SQLITE_FULL"
            else:
                full_classification = f"SQLITE_ERROR:{exc}"

    with connect(store.recovery_path, query_only=True) as recovery:
        proof_count = fetch_scalar(recovery, "SELECT COUNT(*) FROM recovery_runs WHERE run_id = 'proof-run'")
    return {
        "catalog_fill_rows_before_stop": inserted,
        "classification": full_classification,
        "committed_recovery_proof_preserved": proof_count == 1,
        "policy": "stop_new_analysis_and_keep_recovery_writers_authoritative",
    }


def measure_candidate(root: Path, mode: str, rows: int, query_repetitions: int) -> dict[str, Any]:
    store = prepare_candidate(root, mode)
    catalog_metrics = measure_catalog_load(store.catalog, row_count=rows, query_repetitions=query_repetitions)
    recovery_metrics = seed_recovery_events(store)
    member_sizes = {
        name: {
            "db_bytes": path.stat().st_size,
            "wal_bytes": sidecar_size(path, "-wal"),
            "shm_bytes": sidecar_size(path, "-shm"),
        }
        for name, path in store.members.items()
    }
    return {
        "mode": mode,
        "db_file_count": len(store.members),
        "catalog": catalog_metrics,
        "recovery": recovery_metrics,
        "member_sizes": member_sizes,
        "total_db_bytes": sum(item["db_bytes"] for item in member_sizes.values()),
    }


def run_benchmark(rows: int, output: Path, query_repetitions: int = 30) -> int:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="msh-0a4-") as raw:
        root = Path(raw)
        one = measure_candidate(root, "one_db", rows, query_repetitions)
        two = measure_candidate(root, "two_db", rows, query_repetitions)

        backup_root = root / "backups"
        one_store = prepare_candidate(root / "backup-one", "one_db")
        one_db_start_run(one_store, "cmd-one", "run-one", {"kind": "backup"})
        one_backup = create_backup_set(one_store, backup_root)

        two_store = prepare_candidate(root / "backup-two", "two_db")
        two_db_start_run(two_store, "cmd-two", "run-two", {"kind": "backup"})
        two_backup = create_backup_set(two_store, backup_root)

        full_probe = sqlite_full_probe(root)
        summary = {
            "created_utc": utc_now(),
            "python": {
                "version": ".".join(str(part) for part in os.sys.version_info[:3]),
                "sqlite_version": sqlite3.sqlite_version,
            },
            "rows_requested": rows,
            "one_db": one,
            "two_db": two,
            "backup_sets": {
                "one_db": {
                    "member_count": one_backup["member_count"],
                    "members": sorted(one_backup["members"]),
                },
                "two_db": {
                    "member_count": two_backup["member_count"],
                    "members": sorted(two_backup["members"]),
                },
            },
            "sqlite_full_probe": full_probe,
            "complexity": {
                "one_db": {
                    "runstart_write_transactions": 1,
                    "authoritative_db_files": 1,
                    "required_handoff_tables": 0,
                    "backup_members": 1,
                    "crash_states_before_readiness": ["rolled_back", "ready"],
                },
                "two_db": {
                    "runstart_write_transactions": 3,
                    "authoritative_db_files": 2,
                    "required_handoff_tables": 2,
                    "backup_members": 2,
                    "crash_states_before_readiness": ["PREPARED", "PEER_COMMITTED", "SOURCE_CONFIRMED", "COMPLETED"],
                },
            },
            "recommendation": {
                "adr_003": "RECOMMEND_TWO_DATABASES_WITH_EXPLICIT_HANDOFFS",
                "reason": (
                    "The two-db candidate costs more recovery states and paired backup handling, "
                    "but preserves a small FULL-synchronous recovery store when catalog bulk growth hits SQLITE_FULL."
                ),
            },
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MediaSync Home 0A.4 SQLite/capacity spike")
    sub = parser.add_subparsers(dest="command", required=True)
    bench = sub.add_parser("benchmark")
    bench.add_argument("--rows", type=int, default=1_000_000)
    bench.add_argument("--query-repetitions", type=int, default=30)
    bench.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "benchmark":
        return run_benchmark(args.rows, Path(args.output), query_repetitions=args.query_repetitions)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
