from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = ROOT / "spikes" / "0a3_recovery_paths"
sys.path.insert(0, str(HARNESS_DIR))
import recovery_paths  # noqa: E402


class RecoveryPathsSpikeTests(unittest.TestCase):
    def make_lab(self) -> recovery_paths.LabRoot:
        return recovery_paths.create_lab_root()

    def cleanup_lab(self, lab: recovery_paths.LabRoot) -> None:
        recovery_paths.validate_lab_root(lab)
        shutil.rmtree(lab.root, ignore_errors=True)

    def test_lab_root_must_have_matching_marker(self) -> None:
        lab = self.make_lab()
        try:
            recovery_paths.validate_lab_root(lab)
            lab.marker_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(recovery_paths.LabRootError):
                recovery_paths.validate_lab_root(lab)
        finally:
            shutil.rmtree(lab.root, ignore_errors=True)

    def test_short_managed_object_manifest_avoids_mirrored_path_growth(self) -> None:
        lab = self.make_lab()
        try:
            logical = recovery_paths.make_long_relative_path(component_count=10, component_len=25)
            source = lab.root / "source.bin"
            source.write_bytes(b"payload")
            managed = recovery_paths.allocate_managed_object(lab, logical, source, "STAGING")

            mirrored_len = recovery_paths.mirrored_control_path_length(lab, logical)
            payload_len = len(managed["paths"]["payload"])
            manifest = managed["manifest"]

            self.assertGreater(mirrored_len, recovery_paths.MAX_LEGACY_WINDOWS_PATH)
            self.assertLess(payload_len, recovery_paths.MAX_LEGACY_WINDOWS_PATH)
            self.assertEqual(manifest["original_relative_path"], Path(logical).as_posix())
            self.assertNotIn(Path(logical).parts[0], managed["paths"]["payload"])
        finally:
            self.cleanup_lab(lab)

    def test_fallback_replace_preserves_old_target_and_restore_uses_manifest(self) -> None:
        lab = self.make_lab()
        try:
            final = lab.root / "target" / "final.txt"
            final.parent.mkdir(parents=True)
            final.write_text("old", encoding="utf-8")

            result = recovery_paths.fallback_replace_with_recovery(lab, "target/final.txt", b"new")
            self.assertEqual(final.read_text(encoding="utf-8"), "new")
            version_allocation = result["version_manifest"]["allocation_id"]

            final.unlink()
            restored = recovery_paths.restore_managed_object(lab, version_allocation)
            self.assertEqual(restored["restored_relative_path"], "target/final.txt")
            self.assertEqual(final.read_text(encoding="utf-8"), "old")
        finally:
            self.cleanup_lab(lab)

    def test_quarantine_uses_opaque_object_and_restore_uses_manifest(self) -> None:
        lab = self.make_lab()
        try:
            target = lab.root / "target" / "remove-me.txt"
            target.parent.mkdir(parents=True)
            target.write_text("quarantine me", encoding="utf-8")

            result = recovery_paths.quarantine_managed_object(lab, "target/remove-me.txt")
            self.assertEqual(result["status"], "QUARANTINED")
            self.assertFalse(target.exists())
            self.assertEqual(result["manifest"]["logical_role"], "QUARANTINE")
            self.assertNotIn("target/remove-me.txt", result["manifest"]["payload_relative_path"])

            restored = recovery_paths.restore_managed_object(lab, result["allocation_id"])
            self.assertEqual(restored["restored_relative_path"], "target/remove-me.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "quarantine me")
        finally:
            self.cleanup_lab(lab)

    def test_replacefilew_probe_documents_same_volume_backup(self) -> None:
        lab = self.make_lab()
        try:
            result = recovery_paths.replace_filew_probe(lab)
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["same_volume"])
            self.assertEqual(result["final_content"], "new")
            self.assertEqual(result["backup_content"], "old")
        finally:
            self.cleanup_lab(lab)

    def test_recovery_after_crash_after_apply_records_final_verification(self) -> None:
        lab = self.make_lab()
        try:
            final = lab.root / "target" / "final.txt"
            final.parent.mkdir(parents=True)
            final.write_text("old", encoding="utf-8")
            allocation_id = None
            try:
                recovery_paths.fallback_replace_with_recovery(
                    lab,
                    "target/final.txt",
                    b"new",
                    crash_at="after_apply",
                )
            except recovery_paths.CrashInjected:
                journals = list((lab.control_root / "recovery").glob("*/journal.jsonl"))
                self.assertEqual(len(journals), 1)
                allocation_id = journals[0].parent.name
            self.assertIsNotNone(allocation_id)
            recovered = recovery_paths.recover_fallback_replace(lab, allocation_id, "target/final.txt")
            self.assertEqual(recovered["status"], "RECOVERED")
            self.assertEqual(final.read_text(encoding="utf-8"), "new")
        finally:
            self.cleanup_lab(lab)

    def test_crash_windows_before_apply_do_not_overwrite_old_target(self) -> None:
        for crash_point, expected_status in [
            ("before_intent", "BLOCKED"),
            ("after_flush", "BLOCKED"),
            ("after_intent", "SAFE_TO_RETRY_OR_KEEP_OLD"),
            ("after_preserve", "SAFE_TO_RETRY_OR_KEEP_OLD"),
        ]:
            with self.subTest(crash_point=crash_point):
                lab = self.make_lab()
                try:
                    final = lab.root / "target" / "final.txt"
                    final.parent.mkdir(parents=True)
                    final.write_text("old", encoding="utf-8")
                    try:
                        recovery_paths.fallback_replace_with_recovery(
                            lab,
                            "target/final.txt",
                            b"new",
                            crash_at=crash_point,
                        )
                    except recovery_paths.CrashInjected:
                        journals = list((lab.control_root / "recovery").glob("*/journal.jsonl"))
                        self.assertEqual(len(journals), 1)
                        allocation_id = journals[0].parent.name
                    recovered = recovery_paths.recover_fallback_replace(lab, allocation_id, "target/final.txt")
                    self.assertEqual(recovered["status"], expected_status)
                    self.assertEqual(final.read_text(encoding="utf-8"), "old")
                finally:
                    self.cleanup_lab(lab)

    def test_crash_window_after_verify_recovers_catalog_record(self) -> None:
        lab = self.make_lab()
        try:
            final = lab.root / "target" / "final.txt"
            final.parent.mkdir(parents=True)
            final.write_text("old", encoding="utf-8")
            try:
                recovery_paths.fallback_replace_with_recovery(
                    lab,
                    "target/final.txt",
                    b"new",
                    crash_at="after_verify",
                )
            except recovery_paths.CrashInjected:
                journals = list((lab.control_root / "recovery").glob("*/journal.jsonl"))
                self.assertEqual(len(journals), 1)
                allocation_id = journals[0].parent.name
            recovered = recovery_paths.recover_fallback_replace(lab, allocation_id, "target/final.txt")
            self.assertEqual(recovered["status"], "RECOVERED")
            events = [
                item["event"]
                for item in recovery_paths.read_journal(journals[0])
            ]
            self.assertIn("CATALOG_RECORDED", events)
            self.assertEqual(final.read_text(encoding="utf-8"), "new")
        finally:
            self.cleanup_lab(lab)

    def test_recovery_after_crash_before_apply_does_not_overwrite_old_target(self) -> None:
        lab = self.make_lab()
        try:
            final = lab.root / "target" / "final.txt"
            final.parent.mkdir(parents=True)
            final.write_text("old", encoding="utf-8")
            try:
                recovery_paths.fallback_replace_with_recovery(
                    lab,
                    "target/final.txt",
                    b"new",
                    crash_at="after_preserve",
                )
            except recovery_paths.CrashInjected:
                journals = list((lab.control_root / "recovery").glob("*/journal.jsonl"))
                allocation_id = journals[0].parent.name
            recovered = recovery_paths.recover_fallback_replace(lab, allocation_id, "target/final.txt")
            self.assertEqual(recovered["status"], "SAFE_TO_RETRY_OR_KEEP_OLD")
            self.assertEqual(final.read_text(encoding="utf-8"), "old")
        finally:
            self.cleanup_lab(lab)

    def test_directory_type_conflict_is_not_idempotent_success(self) -> None:
        lab = self.make_lab()
        try:
            conflict = lab.root / "photos"
            conflict.write_text("file", encoding="utf-8")
            result = recovery_paths.create_directory_with_recovery(lab, "photos")
            self.assertEqual(result["status"], "TARGET_TYPE_CONFLICT")
        finally:
            self.cleanup_lab(lab)

    def test_source_guard_reports_ntfs_or_fallback_policy(self) -> None:
        lab = self.make_lab()
        try:
            source = lab.root / "source.txt"
            source.write_text("stable", encoding="utf-8")
            result = recovery_paths.source_guard_probe(lab, "source.txt")
            self.assertIn(
                result["guard_level"],
                {"DENY_WRITE_AND_DELETE", "POST_TRANSFER_HASH_ONLY"},
            )
        finally:
            self.cleanup_lab(lab)

    def test_demo_writes_summary_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a3-artifact-") as raw:
            output = Path(raw) / "summary.json"
            self.assertEqual(recovery_paths.run_demo(output), 0)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
