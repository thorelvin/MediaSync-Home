from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
EXE_NAME = "MediaSyncHome0B.exe"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke install, launch, upgrade-protect, upgrade, and uninstall the installer"
    )
    parser.add_argument("installer", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/0b/installer-smoke.json",
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work-dir", action="store_true")
    args = parser.parse_args(argv)

    result = run_installer_smoke(
        installer=args.installer,
        work_dir=args.work_dir,
        keep_work_dir=args.keep_work_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = _sanitize_evidence(result)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 2


def run_installer_smoke(
    *,
    installer: Path,
    work_dir: Path | None = None,
    keep_work_dir: bool = False,
) -> dict[str, object]:
    if os.name != "nt":
        return {"status": "BLOCKED_BY_ENVIRONMENT", "reason": "WINDOWS_REQUIRED"}
    installer = installer.resolve()
    if not installer.is_file():
        return {"status": "FAIL", "reason": "INSTALLER_NOT_FOUND"}

    base = _smoke_work_dir(work_dir)
    install_dir = base / "app"
    state_root = base / "state"
    installation_id = f"installer-smoke-{uuid4().hex[:12]}"
    base.mkdir(parents=True, exist_ok=False)
    state_root.mkdir()
    marker = state_root / "uninstall-preservation-marker.txt"
    marker.write_text("preserve me\n", encoding="utf-8")

    result: dict[str, object] = {
        "status": "FAIL",
        "event": "INSTALLER_SMOKE_FAILED",
        "work_dir": str(base),
        "install_dir": str(install_dir),
        "installation_id": installation_id,
    }
    host: subprocess.Popen[str] | None = None
    try:
        install = _run_setup(installer, install_dir, base / "install.log")
        result["install_exit_code"] = install.returncode
        executable = install_dir / EXE_NAME
        if install.returncode != 0 or not executable.is_file():
            result["reason"] = "INSTALL_FAILED"
            return result

        status = _run(
            [
                str(executable),
                "--local-preview-status",
                "--installation-id",
                installation_id,
                "--state-root",
                str(state_root),
                "--timeout-seconds",
                "10",
            ],
            timeout_seconds=40,
            cwd=install_dir,
        )
        status_payload = _last_json_object(status.stdout)
        result["installed_runtime_exit_code"] = status.returncode
        result["installed_runtime_status"] = status_payload
        result["installed_runtime_stdout_tail"] = status.stdout[-2000:]
        result["installed_runtime_stderr_tail"] = status.stderr[-2000:]
        if status.returncode != 0 or not status_payload or not status_payload.get("accepted"):
            result["reason"] = "INSTALLED_RUNTIME_FAILED"
            return result

        host = subprocess.Popen(
            [
                str(executable),
                "--local-preview-host",
                "--installation-id",
                installation_id,
                "--state-root",
                str(state_root),
            ],
            cwd=install_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        time.sleep(3)
        if host.poll() is not None:
            result["reason"] = "UPGRADE_PROTECTION_HOST_DID_NOT_START"
            return result
        blocked_upgrade = _run_setup(installer, install_dir, base / "blocked-upgrade.log")
        result["running_upgrade_exit_code"] = blocked_upgrade.returncode
        result["running_upgrade_blocked"] = blocked_upgrade.returncode != 0
        if blocked_upgrade.returncode == 0:
            result["reason"] = "RUNNING_UPGRADE_NOT_BLOCKED"
            return result

        _stop_process(host)
        host = None
        upgrade = _run_setup(installer, install_dir, base / "upgrade.log")
        result["upgrade_exit_code"] = upgrade.returncode
        if upgrade.returncode != 0 or not executable.is_file():
            result["reason"] = "STOPPED_UPGRADE_FAILED"
            return result

        uninstaller = install_dir / "unins000.exe"
        if not uninstaller.is_file():
            result["reason"] = "UNINSTALLER_NOT_FOUND"
            return result
        uninstall = _run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout_seconds=120,
        )
        result["uninstall_exit_code"] = uninstall.returncode
        result["application_removed"] = not executable.exists()
        result["state_preserved"] = marker.read_text(encoding="utf-8") == "preserve me\n"
        if uninstall.returncode != 0 or executable.exists() or not result["state_preserved"]:
            result["reason"] = "UNINSTALL_FAILED"
            return result

        result.update(
            {
                "status": "PASS",
                "event": "INSTALLER_SMOKE_COMPLETED",
                "reason": None,
            }
        )
        return result
    finally:
        if host is not None:
            _stop_process(host)
        uninstaller = install_dir / "unins000.exe"
        if "uninstall_exit_code" not in result and uninstaller.is_file():
            cleanup_uninstall = _run(
                [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                timeout_seconds=120,
            )
            result["cleanup_uninstall_exit_code"] = cleanup_uninstall.returncode
        if not keep_work_dir:
            _remove_smoke_work_dir(base)


def _run_setup(installer: Path, install_dir: Path, log_path: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOCLOSEAPPLICATIONS",
            f"/DIR={install_dir}",
            f"/LOG={log_path}",
        ],
        timeout_seconds=180,
    )


def _run(
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )


def _last_json_object(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _smoke_work_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    return Path(tempfile.gettempdir()).resolve() / "MediaSyncHome-Installer-Smoke" / uuid4().hex


def _remove_smoke_work_dir(path: Path) -> None:
    allowed_root = (Path(tempfile.gettempdir()).resolve() / "MediaSyncHome-Installer-Smoke").resolve()
    resolved = path.resolve()
    if allowed_root not in resolved.parents:
        raise RuntimeError(f"refusing to remove installer smoke path outside {allowed_root}")
    shutil.rmtree(resolved, ignore_errors=False)


def _sanitize_evidence(payload: object) -> object:
    replacements = {
        str(Path.home()): "<USER_HOME>",
        str(ROOT): "<REPOSITORY_ROOT>",
        str(Path(tempfile.gettempdir()).resolve()): "<TEMP_ROOT>",
    }
    return _replace_evidence_paths(payload, replacements)


def _replace_evidence_paths(payload: object, replacements: dict[str, str]) -> object:
    if isinstance(payload, dict):
        return {key: _replace_evidence_paths(value, replacements) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_replace_evidence_paths(value, replacements) for value in payload]
    if not isinstance(payload, str):
        return payload
    sanitized = payload
    for original, replacement in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        sanitized = sanitized.replace(original, replacement)
        sanitized = sanitized.replace(original.replace("\\", "\\\\"), replacement)
    return sanitized


if __name__ == "__main__":
    raise SystemExit(main())
