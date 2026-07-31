from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.validate_contracts import require_dependencies


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"


REQUIRED_COMMANDS = {
    "python -m pip install -r requirements-dev.txt",
    "python tools\\validate_contracts.py",
    "python tools\\build_contract_types.py --check",
    "python tools\\validate_handoff.py",
    "python tools\\build_adr_docs.py --check",
    "python tools\\build_master.py --check",
    "python -m pytest -q",
    "python -m ruff check .",
    "python -m mypy src tools\\audit_dependencies.py tools\\audit_vulnerabilities.py",
    "python tools\\check_imports.py",
    "python tools\\audit_dependencies.py",
    "python tools\\audit_vulnerabilities.py",
}


def test_windows_ci_workflow_runs_0b_quality_gates() -> None:
    yaml, _, _ = require_dependencies()
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    windows_job = _require_mapping(workflow["jobs"])["windows"]
    assert windows_job["runs-on"] == "windows-latest"
    assert windows_job["env"]["QT_QPA_PLATFORM"] == "offscreen"

    run_scripts = "\n".join(
        str(step["run"]) for step in windows_job["steps"] if isinstance(step, dict) and "run" in step
    )
    missing = sorted(command for command in REQUIRED_COMMANDS if command not in run_scripts)

    assert missing == []


def _require_mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value
