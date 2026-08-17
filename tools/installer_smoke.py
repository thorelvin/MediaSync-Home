from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
EXE_NAME = "MediaSyncHome0B.exe"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke install, launch, graceful upgrade, scheduled-task cleanup, and "
            "uninstall the installer"
        )
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
    installation_id = "local-dev"
    smoke_environment = dict(os.environ)
    smoke_environment["LOCALAPPDATA"] = str(base / "local-app-data")
    from mediasync_home.adapters.local_host_locator import (
        default_local_preview_state_root,
    )

    state_root = default_local_preview_state_root(
        installation_id,
        environ=smoke_environment,
    )
    base.mkdir(parents=True, exist_ok=False)
    state_root.mkdir(parents=True)
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
    owned_task_path: str | None = None
    try:
        install = _run_setup(
            installer,
            install_dir,
            base / "install.log",
            environment=smoke_environment,
        )
        result["install_exit_code"] = install.returncode
        executable = install_dir / EXE_NAME
        product_license = install_dir / "LICENSE.txt"
        result["product_license"] = (
            "MIT"
            if product_license.is_file()
            and product_license.read_text(encoding="utf-8").startswith("MIT License\n")
            else None
        )
        if (
            install.returncode != 0
            or not executable.is_file()
            or result["product_license"] != "MIT"
        ):
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
            env=smoke_environment,
        )
        time.sleep(3)
        if host.poll() is not None:
            result["reason"] = "UPGRADE_PROTECTION_HOST_DID_NOT_START"
            return result
        upgrade = _run_setup(
            installer,
            install_dir,
            base / "running-upgrade.log",
            environment=smoke_environment,
        )
        result["running_upgrade_exit_code"] = upgrade.returncode
        result["running_upgrade_graceful_shutdown"] = host.wait(timeout=20) == 0
        host = None
        result["upgrade_exit_code"] = upgrade.returncode
        if (
            upgrade.returncode != 0
            or not result["running_upgrade_graceful_shutdown"]
            or not executable.is_file()
        ):
            result["reason"] = "RUNNING_UPGRADE_GRACEFUL_SHUTDOWN_FAILED"
            return result

        owned_task_path = _create_owned_scheduled_task(
            installation_id=installation_id,
            executable=executable,
        )
        result["owned_scheduled_task_created"] = _owned_scheduled_task_exists(
            owned_task_path
        )
        if not result["owned_scheduled_task_created"]:
            result["reason"] = "OWNED_SCHEDULED_TASK_NOT_CREATED"
            return result

        uninstaller = install_dir / "unins000.exe"
        if not uninstaller.is_file():
            result["reason"] = "UNINSTALLER_NOT_FOUND"
            return result
        uninstall = _run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout_seconds=120,
            environment=smoke_environment,
        )
        result["uninstall_exit_code"] = uninstall.returncode
        result["application_removed"] = not executable.exists()
        result["state_preserved"] = marker.read_text(encoding="utf-8") == "preserve me\n"
        result["owned_scheduled_task_removed"] = not _owned_scheduled_task_exists(
            owned_task_path
        )
        if (
            uninstall.returncode != 0
            or executable.exists()
            or not result["state_preserved"]
            or not result["owned_scheduled_task_removed"]
        ):
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
        if owned_task_path is not None:
            _delete_owned_scheduled_task(owned_task_path)
        uninstaller = install_dir / "unins000.exe"
        if "uninstall_exit_code" not in result and uninstaller.is_file():
            cleanup_uninstall = _run(
                [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                timeout_seconds=120,
                environment=smoke_environment,
            )
            result["cleanup_uninstall_exit_code"] = cleanup_uninstall.returncode
        if not keep_work_dir:
            _remove_smoke_work_dir(base)


def _create_owned_scheduled_task(
    *,
    installation_id: str,
    executable: Path,
) -> str:
    from mediasync_home.adapters.task_scheduler import (
        Pywin32TaskSchedulerGateway,
        WindowsTaskSchedulerRegistry,
    )
    from mediasync_home.application.schedules import ScheduleDefinition
    from mediasync_home.application.task_scheduler import (
        bind_same_user_task_scheduler_definition_hash,
        build_same_user_task_scheduler_definition,
    )
    from mediasync_home.application.trigger_occurrences import TriggerKind

    executable_path = str(executable.resolve())
    schedule = ScheduleDefinition(
        schedule_id=f"smoke-{uuid4().hex[:12]}",
        job_id="smoke-job",
        plan_id="smoke-plan",
        plan_checksum="a" * 64,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"hour":23,"kind":"daily","minute":59}',
        definition_generation=1,
        desired_definition_hash="0" * 64,
        time_zone_id=None,
        dst_policy="PRESERVE_WALL_TIME",
        misfire_policy="QUEUE_ONCE",
        coalescing_window_seconds=60,
        task_logon_type="INTERACTIVE_TOKEN",
        requires_network=False,
        run_only_when_logged_on=True,
        enabled=False,
        row_version=1,
    )
    schedule = bind_same_user_task_scheduler_definition_hash(
        schedule,
        installation_id=installation_id,
        executable_path=executable_path,
    )
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id=installation_id,
        executable_path=executable_path,
    )
    registry = WindowsTaskSchedulerRegistry(Pywin32TaskSchedulerGateway())
    registry.apply_task_definition(definition)
    return definition.task_path


def _owned_scheduled_task_exists(task_path: str) -> bool:
    from mediasync_home.adapters.task_scheduler import (
        Pywin32TaskSchedulerGateway,
        WindowsTaskSchedulerRegistry,
    )

    registry = WindowsTaskSchedulerRegistry(Pywin32TaskSchedulerGateway())
    return registry.load_task(task_path) is not None


def _delete_owned_scheduled_task(task_path: str) -> None:
    from mediasync_home.adapters.task_scheduler import (
        Pywin32TaskSchedulerGateway,
        WindowsTaskSchedulerRegistry,
    )

    registry = WindowsTaskSchedulerRegistry(Pywin32TaskSchedulerGateway())
    registry.delete_task(task_path)


def _run_setup(
    installer: Path,
    install_dir: Path,
    log_path: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
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
        environment=environment,
    )


def _run(
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
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
        env=environment,
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
