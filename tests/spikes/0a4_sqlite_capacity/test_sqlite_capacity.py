from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = ROOT / "spikes" / "0a4_sqlite_capacity"
sys.path.insert(0, str(HARNESS_DIR))
import sqlite_capacity  # noqa: E402


class SQLiteCapacitySpikeTests(unittest.TestCase):
    def make_root(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="msh-0a4-test-"))

    def cleanup_root(self, root: Path) -> None:
        shutil.rmtree(root, ignore_errors=True)

    def test_one_db_runstart_is_atomic_at_readiness_boundary(self) -> None:
        root = self.make_root()
        try:
            store = sqlite_capacity.prepare_candidate(root, "one_db")
            with self.assertRaises(sqlite_capacity.CrashInjected):
                sqlite_capacity.one_db_start_run(
                    store,
                    "cmd-crash",
                    "run-crash",
                    {"kind": "scan"},
                    crash_at="before_commit",
                )
            crashed = sqlite_capacity.inspect_runstart(store, "cmd-crash")
            self.assertIsNone(crashed["receipt"])
            self.assertEqual(crashed["recovery_run_count"], 0)

            sqlite_capacity.one_db_start_run(store, "cmd-ok", "run-ok", {"kind": "scan"})
            committed = sqlite_capacity.inspect_runstart(store, "cmd-ok")
            self.assertEqual(committed["receipt"]["state"], "ACCEPTED")
            self.assertEqual(committed["recovery_run_count"], 1)
        finally:
            self.cleanup_root(root)

    def test_read_pool_query_only_rejects_writes(self) -> None:
        root = self.make_root()
        try:
            store = sqlite_capacity.prepare_candidate(root, "one_db")
            with sqlite_capacity.connect(store.catalog, query_only=True) as conn:
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(
                        "INSERT INTO runs(run_id, state, payload_hash, created_utc) VALUES ('x', 'READY', 'h', 'now')"
                    )
        finally:
            self.cleanup_root(root)

    def test_two_db_handoff_recovers_each_crash_window(self) -> None:
        for crash_point in [
            "after_catalog_prepared",
            "after_recovery_peer_committed",
            "after_catalog_source_confirmed",
        ]:
            with self.subTest(crash_point=crash_point):
                root = self.make_root()
                try:
                    store = sqlite_capacity.prepare_candidate(root, "two_db")
                    command_key = f"cmd-{crash_point}"
                    run_id = f"run-{crash_point}"
                    handoff_id = sqlite_capacity.stable_json_hash(
                        {"command_key": command_key, "run_id": run_id}
                    )[:32]
                    with self.assertRaises(sqlite_capacity.CrashInjected):
                        sqlite_capacity.two_db_start_run(
                            store,
                            command_key,
                            run_id,
                            {"kind": "scan", "crash": crash_point},
                            crash_at=crash_point,
                        )

                    recovered = sqlite_capacity.reconcile_two_db_runstart(store, handoff_id)
                    self.assertEqual(recovered["status"], "COMPLETED")
                    inspected = sqlite_capacity.inspect_runstart(store, command_key, handoff_id)
                    self.assertEqual(inspected["receipt"]["state"], "ACCEPTED")
                    self.assertEqual(inspected["catalog_handoff"]["state"], "COMPLETED")
                    self.assertEqual(inspected["recovery_handoff"]["state"], "COMPLETED")
                    self.assertEqual(inspected["recovery_run_count"], 1)
                finally:
                    self.cleanup_root(root)

    def test_backup_restore_verification_rejects_mixed_two_db_epochs(self) -> None:
        root = self.make_root()
        try:
            store = sqlite_capacity.prepare_candidate(root, "two_db")
            sqlite_capacity.two_db_start_run(store, "cmd-a", "run-a", {"kind": "backup", "n": 1})
            backup_root = root / "backups"
            first = sqlite_capacity.create_backup_set(store, backup_root)
            sqlite_capacity.two_db_start_run(store, "cmd-b", "run-b", {"kind": "backup", "n": 2})
            second = sqlite_capacity.create_backup_set(store, backup_root)

            first_dir = backup_root / first["backup_set_id"]
            second_dir = backup_root / second["backup_set_id"]
            verified = sqlite_capacity.verify_restore_members({"catalog": first_dir, "recovery": first_dir})
            self.assertEqual(verified["status"], "RESTORE_SET_VERIFIED")
            with self.assertRaises(sqlite_capacity.RestoreSetError):
                sqlite_capacity.verify_restore_members({"catalog": first_dir, "recovery": second_dir})
        finally:
            self.cleanup_root(root)

    def test_sqlite_full_probe_preserves_committed_recovery_proof(self) -> None:
        root = self.make_root()
        try:
            result = sqlite_capacity.sqlite_full_probe(root)
            self.assertEqual(result["classification"], "SQLITE_FULL")
            self.assertTrue(result["committed_recovery_proof_preserved"])
        finally:
            self.cleanup_root(root)

    def test_small_benchmark_uses_indexes_and_records_backup_complexity(self) -> None:
        root = self.make_root()
        try:
            output = root / "summary.json"
            sqlite_capacity.run_benchmark(2000, output, query_repetitions=3)
            summary = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["rows_requested"], 2000)
            self.assertEqual(summary["one_db"]["catalog"]["rows_inserted"], 2000)
            self.assertEqual(summary["two_db"]["catalog"]["rows_inserted"], 2000)
            self.assertEqual(summary["backup_sets"]["one_db"]["member_count"], 1)
            self.assertEqual(summary["backup_sets"]["two_db"]["member_count"], 2)
            parent_plan = "\n".join(summary["two_db"]["catalog"]["queries"]["parent_page"]["plan"])
            self.assertIn("USING", parent_plan)
            self.assertEqual(
                summary["recommendation"]["adr_003"],
                "RECOMMEND_TWO_DATABASES_WITH_EXPLICIT_HANDOFFS",
            )
        finally:
            self.cleanup_root(root)


if __name__ == "__main__":
    unittest.main()
