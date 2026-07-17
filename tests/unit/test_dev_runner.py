from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_local_dev_runner_starts_engine_host_role() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_role.py", "--role", "engine-host"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["role"] == "engine-host"
    assert payload["ready"] is True
    assert payload["mutations_enabled"] is False
    assert payload["runtime_policy"]["evaluated"] is True
