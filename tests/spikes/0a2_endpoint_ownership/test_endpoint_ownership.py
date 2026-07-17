from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover - optional outside the 0A.2 BLAKE3 evidence venv
    Draft202012Validator = None
    FormatChecker = None


ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = ROOT / "spikes" / "0a2_endpoint_ownership"
sys.path.insert(0, str(HARNESS_DIR))
import endpoint_ownership  # noqa: E402


@unittest.skipUnless(
    endpoint_ownership.blake3_dependency_version() != "MISSING",
    "0A.2 final endpoint marker evidence requires the blake3 package",
)
class EndpointOwnershipLocalSpikeTests(unittest.TestCase):
    def make_lab(self) -> endpoint_ownership.LabRoot:
        return endpoint_ownership.create_lab_root()

    def cleanup_lab(self, lab: endpoint_ownership.LabRoot) -> None:
        shutil.rmtree(lab.root, ignore_errors=True)

    def classify(self, lab: endpoint_ownership.LabRoot, owner: str) -> str:
        return endpoint_ownership.classify_control_area(lab.root, owner)["state"]

    def test_classifier_covers_all_documented_states(self) -> None:
        observed: set[str] = set()

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            lab.control_dir.mkdir()
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            lab.control_dir.mkdir()
            (lab.control_dir / "not-control.txt").write_text("user data", encoding="utf-8")
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            (lab.root / ".MEDIASYNC").mkdir()
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            (lab.control_dir / "locks").mkdir(parents=True)
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            lab.control_dir.mkdir()
            (lab.control_dir / "endpoint.json").write_text("{bad-json", encoding="utf-8")
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(lab, owner)
            observed.add(self.classify(lab, owner))
            observed.add(self.classify(lab, str(uuid.uuid4())))
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(
                lab,
                owner,
                schema_version=endpoint_ownership.SUPPORTED_CONTROL_SCHEMA + 1,
            )
            observed.add(self.classify(lab, owner))
        finally:
            self.cleanup_lab(lab)

        self.assertEqual(observed, set(endpoint_ownership.CLASSIFICATION_STATES))

    def test_unknown_nonempty_control_name_is_not_excluded(self) -> None:
        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            lab.control_dir.mkdir()
            (lab.control_dir / "family-photo.txt").write_text("data", encoding="utf-8")
            result = endpoint_ownership.classify_control_area(lab.root, owner)
            self.assertEqual(result["state"], "UNKNOWN_NONEMPTY_DIRECTORY")
            self.assertFalse(result["exclude_from_snapshot"])
            self.assertFalse(result["mutating_allowed"])
        finally:
            self.cleanup_lab(lab)

    def test_checksum_tamper_and_root_identity_mismatch_block_mutation(self) -> None:
        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(lab, owner)
            marker_path = lab.control_dir / "endpoint.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["owner_installation_id"] = str(uuid.uuid4())
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            result = endpoint_ownership.classify_control_area(lab.root, owner)
            self.assertEqual(result["state"], "CORRUPT_MARKER")
        finally:
            self.cleanup_lab(lab)

    def test_marker_uses_final_blake3_contract_and_validates_schema(self) -> None:
        if Draft202012Validator is None or FormatChecker is None:
            self.skipTest("jsonschema package is required for draft marker schema validation")

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            marker = endpoint_ownership.write_endpoint_control_area(lab, owner)
            schema = json.loads((ROOT / "schema" / "endpoint-marker.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(marker)

            self.assertEqual(marker["root_identity_hash_algorithm"], "BLAKE3-256")
            self.assertEqual(marker["marker_checksum_algorithm"], "BLAKE3-256")
            self.assertEqual(marker["canonicalization_algorithm"], "JCS-RFC8785")
            self.assertEqual(marker["marker_checksum"], endpoint_ownership.checksum_payload(marker))
            self.assertEqual(len(marker["marker_checksum"]), 64)
            self.assertEqual(len(marker["root_identity_hash"]), 64)
        finally:
            self.cleanup_lab(lab)

        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(lab, owner)
            marker_path = lab.control_dir / "endpoint.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["root_identity_hash"] = "0" * 64
            marker["marker_checksum"] = endpoint_ownership.checksum_payload(marker)
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            result = endpoint_ownership.classify_control_area(lab.root, owner)
            self.assertEqual(result["state"], "PARTIAL_CONTROL_AREA")
        finally:
            self.cleanup_lab(lab)

    @unittest.skipUnless(os.name == "nt", "local lock probe requires Windows")
    def test_local_mutation_lock_is_exclusive_until_handle_close(self) -> None:
        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(lab, owner)
            first = endpoint_ownership.acquire_mutation_lock(lab.root)
            try:
                with self.assertRaises(endpoint_ownership.EndpointOwnershipError):
                    endpoint_ownership.acquire_mutation_lock(lab.root)
            finally:
                first.close()
            second = endpoint_ownership.acquire_mutation_lock(lab.root)
            second.close()
        finally:
            self.cleanup_lab(lab)

    @unittest.skipUnless(os.name == "nt", "takeover probe uses the local mutation lock")
    def test_foreign_owner_takeover_increments_epoch_and_stales_old_permit(self) -> None:
        lab = self.make_lab()
        try:
            old_owner = str(uuid.uuid4())
            new_owner = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(lab, old_owner, ownership_epoch=3)
            before = endpoint_ownership.read_marker(lab.root)
            stale_permit = {
                "endpoint_id": before["endpoint_id"],
                "owner_installation_id": before["owner_installation_id"],
                "ownership_epoch": before["ownership_epoch"],
            }

            result = endpoint_ownership.classify_control_area(lab.root, new_owner)
            self.assertEqual(result["state"], "VALID_FOREIGN")
            self.assertFalse(result["mutating_allowed"])

            takeover = endpoint_ownership.takeover_foreign_control_area(lab, new_owner)
            self.assertEqual(takeover["new_epoch"], 4)
            after = endpoint_ownership.read_marker(lab.root)
            self.assertFalse(endpoint_ownership.permit_is_current(stale_permit, after))
            self.assertTrue(
                endpoint_ownership.permit_is_current(
                    {
                        "endpoint_id": after["endpoint_id"],
                        "owner_installation_id": after["owner_installation_id"],
                        "ownership_epoch": after["ownership_epoch"],
                    },
                    after,
                )
            )
        finally:
            self.cleanup_lab(lab)

    def test_cleanup_refuses_foreign_namespace_and_checks_lab_marker(self) -> None:
        lab = self.make_lab()
        try:
            owner = str(uuid.uuid4())
            foreign = str(uuid.uuid4())
            endpoint_ownership.write_endpoint_control_area(lab, owner)
            foreign_namespace = lab.control_dir / "installations" / foreign[:8]
            foreign_namespace.mkdir(parents=True)
            (foreign_namespace / "foreign.txt").write_text("keep", encoding="utf-8")

            refused = endpoint_ownership.cleanup_installation_namespace(lab, owner, foreign)
            self.assertEqual(refused["status"], "REFUSED_FOREIGN_NAMESPACE")
            self.assertTrue(foreign_namespace.exists())

            own_namespace = lab.control_dir / "installations" / owner[:8]
            self.assertTrue(own_namespace.exists())
            cleaned = endpoint_ownership.cleanup_installation_namespace(lab, owner, owner)
            self.assertEqual(cleaned["status"], "CLEANED_OWN_NAMESPACE")
            self.assertFalse(own_namespace.exists())

            lab.marker_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(endpoint_ownership.LabRootError):
                endpoint_ownership.cleanup_installation_namespace(lab, owner, owner)
        finally:
            shutil.rmtree(lab.root, ignore_errors=True)

    def test_demo_writes_sanitized_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a2-artifact-") as raw:
            output = Path(raw) / "summary.json"
            self.assertEqual(endpoint_ownership.run_demo(output), 0)
            text = output.read_text(encoding="utf-8")
            summary = json.loads(text)
            self.assertEqual(summary["smb_cross_machine"], "BLOCKED_BY_ENVIRONMENT")
            self.assertFalse(summary["old_permit_current_after_takeover"])
            self.assertTrue(summary["local_lock_second_open_blocked"])
            self.assertEqual(summary["checksum_algorithm"], "BLAKE3-256")
            self.assertEqual(summary["canonicalization_algorithm"], "JCS-RFC8785")
            self.assertNotEqual(summary["blake3_dependency_version"], "MISSING")
            self.assertNotIn("C:\\Users\\", text)


if __name__ == "__main__":
    unittest.main()
