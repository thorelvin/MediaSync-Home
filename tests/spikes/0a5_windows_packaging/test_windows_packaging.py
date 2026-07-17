from __future__ import annotations

import json
import os
import subprocess
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
            self.assertTrue(result["missing_modules"] or result["missing_tools"] or result["missing_sdk_tools"])
        self.assertIn(result["runtime_modules_status"], {"PASS", "BLOCKED_BY_ENVIRONMENT"})
        self.assertIn(result["packaging_scripts_status"], {"PASS", "BLOCKED_BY_ENVIRONMENT"})
        self.assertIn(result["windows_sdk_status"], {"PASS", "BLOCKED_BY_ENVIRONMENT"})
        self.assertEqual(result["clean_windows_vm"], "BLOCKED_BY_ENVIRONMENT")

    @unittest.skipUnless(
        all(windows_packaging.importlib.util.find_spec(name) is not None for name in ("PySide6", "blake3", "nuitka")),
        "minimal runtime probe requires PySide6, blake3, and Nuitka in the active Python",
    )
    def test_minimal_runtime_probe_uses_pyside6_blake3_and_win32_api(self) -> None:
        result = windows_packaging.minimal_runtime_probe()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["probe_digest_algorithm"], "BLAKE3-256")
        self.assertEqual(len(result["probe_digest"]), 64)
        self.assertTrue(result["pyside6_version"])
        self.assertTrue(result["blake3_version"])
        self.assertTrue(result["nuitka_version"])
        self.assertEqual(result["win32_get_system_directory_basename"].lower(), "system32")

    @unittest.skipUnless(
        all(windows_packaging.importlib.util.find_spec(name) is not None for name in ("PySide6", "blake3")),
        "minimal runtime app requires PySide6 and blake3 in the active Python",
    )
    def test_minimal_runtime_app_emits_sanitized_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(windows_packaging.minimal_runtime_app_path())],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["probe_digest_algorithm"], "BLAKE3-256")
        self.assertEqual(len(payload["probe_digest"]), 64)
        self.assertNotIn("C:\\Users\\", completed.stdout)

    def test_nuitka_build_probe_writes_sanitized_smoke_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a5-nuitka-") as raw:
            work = Path(raw) / "work with spaces"
            output = Path(raw) / "summary.json"
            fake_python = Path(raw) / "fake-python.exe"
            fake_home = str(Path.home())
            smoke_payload = {
                "status": "PASS",
                "probe_digest_algorithm": "BLAKE3-256",
                "probe_digest": "a" * 64,
                "pyside6_version": "6.11.1",
                "qt_version": "6.11.1",
                "blake3_version": "1.0.9",
                "win32_get_system_directory_basename": "system32",
            }

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if "-m" in command and "nuitka" in command:
                    exe_dir = work / "minimal_runtime_app.dist"
                    exe_dir.mkdir(parents=True, exist_ok=True)
                    (exe_dir / windows_packaging.NUITKA_PROBE_EXE_NAME).write_bytes(b"fake packaged exe")
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=f"built under {work} for {fake_home}",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(smoke_payload) + "\n",
                    stderr=f"smoked under {work}",
                )

            prereq = {
                "status": "PASS",
                "missing_required": [],
                "module_versions": {"PySide6": "6.11.1", "blake3": "1.0.9", "nuitka": "4.1.3"},
                "packaging_tools": {"pyside6-deploy": True, "nuitka": True},
                "sdk_tools": {"cl": False, "rc": False, "signtool": False},
                "windows_sdk_status": "BLOCKED_BY_ENVIRONMENT",
            }
            with (
                mock.patch.object(windows_packaging, "nuitka_build_prerequisites", return_value=prereq),
                mock.patch.object(windows_packaging.subprocess, "run", side_effect=fake_run),
            ):
                self.assertEqual(
                    windows_packaging.run_nuitka_build_probe(
                        output,
                        work_dir=work,
                        python_executable=fake_python,
                    ),
                    0,
                )

            raw_summary = output.read_text(encoding="utf-8")
            summary = json.loads(raw_summary)
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["executable"]["sha256"], windows_packaging.sha256_file(work / "minimal_runtime_app.dist" / windows_packaging.NUITKA_PROBE_EXE_NAME))
            self.assertEqual(summary["smoke"]["stdout_json"]["status"], "PASS")
            self.assertNotIn(str(work), raw_summary)
            self.assertNotIn(fake_home, raw_summary)
            self.assertNotIn("C:\\Users\\", raw_summary)

    def test_sdk_signing_inventory_discovers_off_path_tools_without_cert(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a5-sdk-") as raw:
            root = Path(raw)
            vs_root = root / "Microsoft Visual Studio"
            kits_root = root / "Windows Kits" / "10" / "bin"
            cl = (
                vs_root
                / "2022"
                / "BuildTools"
                / "VC"
                / "Tools"
                / "MSVC"
                / "14.42.34433"
                / "bin"
                / "Hostx64"
                / "x64"
                / "cl.exe"
            )
            rc = kits_root / "10.0.22000.0" / "x64" / "rc.exe"
            signtool = kits_root / "10.0.22000.0" / "x64" / "signtool.exe"
            for path in (cl, rc, signtool):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fake exe")

            def fake_launch(tool_name: str, path: Path) -> dict[str, object]:
                return {
                    "can_start": True,
                    "returncode": 0,
                    "banner": f"{tool_name} started from {path.name}",
                    "error": None,
                }

            with (
                mock.patch.object(windows_packaging.shutil, "which", return_value=None),
                mock.patch.object(windows_packaging, "get_file_version", return_value="1.2.3.4"),
                mock.patch.object(windows_packaging, "launch_tool_probe", side_effect=fake_launch),
                mock.patch.object(
                    windows_packaging,
                    "current_user_code_signing_certificate_count",
                    return_value={
                        "status": "BLOCKED_BY_ENVIRONMENT",
                        "count": 0,
                        "reason": "NO_CURRENT_USER_CODE_SIGNING_CERTIFICATE",
                    },
                ),
            ):
                result = windows_packaging.sdk_signing_inventory(
                    visual_studio_root=vs_root,
                    windows_kits_root=kits_root,
                )

            self.assertEqual(result["status"], "PASS")
            self.assertFalse(result["all_tools_on_path"])
            self.assertEqual(result["signing_status"], "BLOCKED_BY_ENVIRONMENT")
            self.assertEqual(result["signing_certificate"]["count"], 0)
            for tool_name in windows_packaging.SDK_TOOL_NAMES:
                with self.subTest(tool_name=tool_name):
                    self.assertEqual(result["tools"][tool_name]["status"], "PASS")
                    self.assertFalse(result["tools"][tool_name]["on_path"])
                    self.assertEqual(result["tools"][tool_name]["candidate_count"], 1)
                    self.assertTrue(result["tools"][tool_name]["launch_probe"]["can_start"])

    def test_demo_writes_sanitized_summary_without_running_robocopy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a5-artifact-") as raw:
            output = Path(raw) / "summary.json"
            self.assertEqual(windows_packaging.run_demo(output), 0)
            summary = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(summary["real_robocopy_started"])
            self.assertTrue(summary["round_trip"]["all_passed"])
            self.assertIn(summary["minimal_runtime_preflight"]["status"], {"PASS", "BLOCKED_BY_ENVIRONMENT"})
            self.assertNotIn("C:\\Users\\", output.read_text(encoding="utf-8"))
            self.assertEqual(
                set(summary["forbidden_switch_validation"].values()),
                {"REJECTED"},
            )


if __name__ == "__main__":
    unittest.main()
