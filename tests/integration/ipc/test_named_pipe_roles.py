from __future__ import annotations

import json
import os
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
