from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = ROOT / "spikes" / "0a5_windows_packaging"
sys.path.insert(0, str(HARNESS_DIR))
import windows_packaging  # noqa: E402


@unittest.skipUnless(os.name == "nt", "0A.5 Windows packaging spike requires Windows")
class WindowsPackagingSpikeTests(unittest.TestCase):
    def test_resolver_uses_windows_system_directory_not_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a5-fake-path-") as raw:
            fake = Path(raw) / "Robocopy.exe"
            fake.write_text("not real", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PATH": f"{raw}{os.pathsep}{os.environ.get('PATH', '')}"}):
                resolved = windows_packaging.resolve_system_executable("Robocopy.exe")
            self.assertEqual(Path(resolved.executable_path).name.lower(), "robocopy.exe")
            self.assertNotEqual(Path(resolved.executable_path).parent, Path(raw))
            self.assertEqual(
                windows_packaging.normalize_dos_path(Path(resolved.executable_path).parent.as_posix()),
                windows_packaging.normalize_dos_path(resolved.system_directory),
            )
            self.assertTrue(resolved.sha256)

    def test_windows_argv_builder_round_trips_command_line_corpus(self) -> None:
        for payload in windows_packaging.argv_round_trip_payloads():
            with self.subTest(payload=payload[:2]):
                argv = ["C:\\Windows\\System32\\Robocopy.exe", *payload]
                command_line = windows_packaging.build_windows_command_line(argv)
                parsed = windows_packaging.parse_windows_command_line(command_line)
                self.assertEqual(parsed, tuple(argv))

    def test_instrumented_child_receives_exact_argv(self) -> None:
        corpus = [
            ["contains spaces", "unicode-æøå-雪"],
            ["", "C:\\path with spaces\\trailing\\"],
            ["quote\"inside", "/looks-like-switch"],
            ["\\\\server\\share name\\folder\\"],
        ]
        for payload in corpus:
            with self.subTest(payload=payload):
                result = windows_packaging.child_round_trip(payload)
                self.assertEqual(result["status"], "PASS")

    def test_command_line_limit_accepts_near_limit_and_rejects_over_limit(self) -> None:
        fixed = ["C:\\Windows\\System32\\Robocopy.exe"]
        fixed_length = len(windows_packaging.build_windows_command_line(fixed + [""]))
        near_payload = "x" * (windows_packaging.MAX_WINDOWS_COMMAND_LINE - fixed_length - 1)
        near = windows_packaging.build_windows_command_line(fixed + [near_payload])
        self.assertLessEqual(len(near), windows_packaging.MAX_WINDOWS_COMMAND_LINE)
        with self.assertRaises(ValueError):
            windows_packaging.build_windows_command_line(fixed + ["x" * windows_packaging.MAX_WINDOWS_COMMAND_LINE])

    def test_robocopy_plan_validates_final_parsed_switches(self) -> None:
        resolved = windows_packaging.resolve_system_executable("Robocopy.exe")
        plan = windows_packaging.build_robocopy_launch_plan(
            resolved,
            Path("C:/MediaSyncHome-Spike/source root"),
            Path("C:/MediaSyncHome-Spike/staging inbox"),
            Path("C:/MediaSyncHome-Spike/logs/batch.log"),
        )
        self.assertEqual(plan.parsed_argv, plan.argv)
        self.assertIn("/E", plan.parsed_argv)
        self.assertFalse(set(argument.upper() for argument in plan.parsed_argv) & windows_packaging.FORBIDDEN_ROBOCOPY_SWITCHES)
        self.assertEqual(sorted(plan.environment), ["PATH", "SystemRoot", "TEMP", "TMP", "WINDIR"])

        command_line = windows_packaging.build_windows_command_line([resolved.executable_path, "C:\\src", "C:\\dst", "/PURGE"])
        with self.assertRaises(windows_packaging.RobocopyArgumentError):
            windows_packaging.validate_robocopy_command_line(command_line, resolved.executable_path)

    def test_typed_forbidden_flags_are_rejected_before_launch_plan(self) -> None:
        resolved = windows_packaging.resolve_system_executable("Robocopy.exe")
        for forbidden in windows_packaging.FORBIDDEN_ROBOCOPY_SWITCHES:
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(windows_packaging.RobocopyArgumentError):
                    windows_packaging.build_robocopy_launch_plan(
                        resolved,
                        Path("C:/MediaSyncHome-Spike/source"),
                        Path("C:/MediaSyncHome-Spike/staging"),
                        Path("C:/MediaSyncHome-Spike/logs/batch.log"),
                        switches=windows_packaging.DEFAULT_ROBOCOPY_SWITCHES + (forbidden,),
                    )

    def test_relative_roots_are_rejected(self) -> None:
        resolved = windows_packaging.resolve_system_executable("Robocopy.exe")
        with self.assertRaises(windows_packaging.RobocopyArgumentError):
            windows_packaging.build_robocopy_launch_plan(
                resolved,
                Path("relative-source"),
                Path("C:/MediaSyncHome-Spike/staging"),
                Path("C:/MediaSyncHome-Spike/logs/batch.log"),
            )

    def test_packaging_probe_reports_pass_or_environment_blocker(self) -> None:
        result = windows_packaging.package_toolchain_probe()
        self.assertIn(result["status"], {"PASS", "BLOCKED_BY_ENVIRONMENT"})
        if result["status"] == "BLOCKED_BY_ENVIRONMENT":
            self.assertTrue(result["missing_modules"] or result["missing_tools"])
        self.assertEqual(result["clean_windows_vm"], "BLOCKED_BY_ENVIRONMENT")

    def test_demo_writes_sanitized_summary_without_running_robocopy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a5-artifact-") as raw:
            output = Path(raw) / "summary.json"
            self.assertEqual(windows_packaging.run_demo(output), 0)
            summary = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(summary["real_robocopy_started"])
            self.assertTrue(summary["round_trip"]["all_passed"])
            self.assertNotIn("C:\\Users\\", output.read_text(encoding="utf-8"))
            self.assertEqual(
                set(summary["forbidden_switch_validation"].values()),
                {"REJECTED"},
            )


if __name__ == "__main__":
    unittest.main()
