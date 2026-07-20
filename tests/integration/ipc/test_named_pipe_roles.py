from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 named-pipe role wiring is Windows-only")


if os.name == "nt":
    from mediasync_home.ipc import win32_named_pipe


def test_engine_host_and_gui_roles_complete_non_mutating_status_roundtrip() -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-integration-test",
        suffix=uuid4().hex,
    )
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-status",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["host_status"]["role"] == "engine-host"
    assert gui_response["payload"]["host_status"]["mutations_enabled"] is False
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_persists_gui_submitted_disabled_command(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-state-root-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    idempotency_key = "66666666-6666-4666-8666-666666666666"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--submit-command",
                "UNKNOWN_MUTATION",
                "--request-id",
                "44444444-4444-4444-8444-444444444444",
                "--idempotency-key",
                idempotency_key,
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    with sqlite3.connect(state_root / "catalog.sqlite") as connection:
        row = connection.execute(
            """
            SELECT state, rejection_reason
            FROM command_receipts
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()

    assert stderr == ""
    assert gui.returncode == 2
    assert gui_response["status"] == "REJECTED"
    assert gui_response["reason"] == "MUTATING_COMMANDS_DISABLED"
    assert gui_response["payload"]["receipt"]["state"] == "REJECTED"
    assert row == ("REJECTED", "MUTATING_COMMANDS_DISABLED")
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[0]["startup_reconciliation"]["command_receipts"]["scanned"] == 0
    assert host_events[-1]["served_requests"] == 2
