from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = ROOT / "spikes" / "0a1_process_ipc"
HARNESS_PATH = HARNESS_DIR / "win32_ipc_job.py"
sys.path.insert(0, str(HARNESS_DIR))
import win32_ipc_job  # noqa: E402


@unittest.skipUnless(os.name == "nt", "0A.1 process/IPC spike is Windows-only")
class Win32IpcJobSpikeTests(unittest.TestCase):
    def start_host(self, work_dir: Path, *, connections: int = 1) -> tuple[subprocess.Popen[str], str]:
        pipe_name = win32_ipc_job.make_pipe_name(uuid.uuid4().hex)
        ready_file = work_dir / "ready.json"
        receipt_store = work_dir / "receipts.json"
        proc = subprocess.Popen(
            [
                sys.executable,
                str(HARNESS_PATH),
                "host",
                "--pipe-name",
                pipe_name,
                "--receipt-store",
                str(receipt_store),
                "--ready-file",
                str(ready_file),
                "--connections",
                str(connections),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while not ready_file.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate(timeout=1)
                self.fail(f"host exited early stdout={stdout!r} stderr={stderr!r}")
            time.sleep(0.05)
        self.assertTrue(ready_file.exists(), "host did not publish readiness")
        return proc, pipe_name

    def stop_host(self, proc: subprocess.Popen[str]) -> None:
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            self.fail(f"host did not exit stdout={stdout!r} stderr={stderr!r}")
        self.assertEqual(proc.returncode, 0, f"host failed stdout={stdout!r} stderr={stderr!r}")

    def test_handshake_uses_os_sid_and_ignores_claimed_sid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a1-ipc-") as raw:
            proc, pipe_name = self.start_host(Path(raw), connections=1)
            response = win32_ipc_job.send_client_message(
                pipe_name,
                win32_ipc_job.build_message(message_type="HANDSHAKE", role="gui"),
            )
            self.stop_host(proc)

        self.assertEqual(response["status"], "READY")
        self.assertTrue(response["claimed_sid_ignored"])
        self.assertEqual(response["authenticated_sid_hash"], win32_ipc_job.current_token_snapshot()["sid_hash"])

    def test_protocol_mismatch_is_rejected_without_command_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a1-protocol-") as raw:
            proc, pipe_name = self.start_host(Path(raw), connections=1)
            response = win32_ipc_job.send_client_message(
                pipe_name,
                win32_ipc_job.build_message(protocol_version=999),
            )
            self.stop_host(proc)

        self.assertEqual(response, {"reason": "PROTOCOL_MISMATCH", "status": "REJECTED"})

    def test_idempotent_command_receipt_survives_host_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a1-restart-") as raw:
            work_dir = Path(raw)
            key = str(uuid.uuid4())
            proc1, pipe1 = self.start_host(work_dir, connections=1)
            first = win32_ipc_job.send_client_message(
                pipe1,
                win32_ipc_job.build_message(idempotency_key=key, payload={"value": 1}),
            )
            self.stop_host(proc1)

            (work_dir / "ready.json").unlink()
            proc2, pipe2 = self.start_host(work_dir, connections=1)
            second = win32_ipc_job.send_client_message(
                pipe2,
                win32_ipc_job.build_message(idempotency_key=key, payload={"value": 1}),
            )
            self.stop_host(proc2)

        self.assertEqual(first["status"], "ACCEPTED")
        self.assertFalse(first["deduplicated"])
        self.assertEqual(second["status"], "ACCEPTED")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])

    def test_same_idempotency_key_with_different_payload_conflicts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a1-conflict-") as raw:
            work_dir = Path(raw)
            key = str(uuid.uuid4())
            proc, pipe_name = self.start_host(work_dir, connections=2)
            first = win32_ipc_job.send_client_message(
                pipe_name,
                win32_ipc_job.build_message(idempotency_key=key, payload={"value": 1}),
            )
            conflict = win32_ipc_job.send_client_message(
                pipe_name,
                win32_ipc_job.build_message(idempotency_key=key, payload={"value": 2}),
            )
            self.stop_host(proc)

        self.assertEqual(first["status"], "ACCEPTED")
        self.assertEqual(conflict, {"reason": "IDEMPOTENCY_CONFLICT", "status": "REJECTED"})

    def test_suspended_child_is_contained_before_resume_and_killed_on_job_close(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a1-job-") as raw:
            result = win32_ipc_job.prove_job_object_containment(Path(raw))

        self.assertFalse(result["precontain_marker_seen"])
        self.assertTrue(result["marker_seen_after_resume"])
        self.assertTrue(result["kill_on_close_observed"])

    @unittest.skipUnless(
        os.environ.get("MEDIASYNC_RUN_TASKSCHEDULER_SPIKE") == "1",
        "real Task Scheduler probe is opt-in because it creates a dedicated temporary task",
    )
    def test_task_scheduler_trigger_client_reaches_same_sid_host(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msh-0a1-scheduler-") as raw:
            output = Path(raw) / "summary.json"
            result = win32_ipc_job.prove_task_scheduler_trigger(Path(raw), output)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["task_deleted"])
        self.assertTrue(result["task_folder_deleted"])
        self.assertTrue(result["host_client_same_sid"])
        self.assertEqual(result["response"]["status"], "ACCEPTED")


if __name__ == "__main__":
    unittest.main()
