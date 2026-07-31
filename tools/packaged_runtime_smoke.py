from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mediasync_home.adapters.sqlite.connection_policy import (  # noqa: E402
    apply_sqlite_connection_policy,
    build_state_store_layout,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.migrations import (  # noqa: E402
    apply_sqlite_migrations,
    catalog_migration_plan,
    recovery_migration_plan,
)
from mediasync_home.adapters.sqlite.schedules import SqliteScheduleStore  # noqa: E402
from mediasync_home.adapters.sqlite.state_migration import (  # noqa: E402
    migrate_sqlite_state_stores,
)
from mediasync_home.adapters.task_scheduler import (  # noqa: E402
    Pywin32TaskSchedulerGateway,
    WindowsTaskSchedulerRegistry,
)
from mediasync_home.application.schedules import ScheduleDefinition  # noqa: E402
from mediasync_home.application.task_scheduler import (  # noqa: E402
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
    classify_task_scheduler_reconciliation,
)
from mediasync_home.application.trigger_occurrences import TriggerKind  # noqa: E402
from tools.task_scheduler_com_smoke import (  # noqa: E402
    _cleanup_task_scheduler_smoke,
    _task_scheduler_folder_exists,
)


EXE_NAME = "MediaSyncHome0B.exe"
TEXT_TAIL_LIMIT = 4000


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout_tail: str
    stderr_tail: str
    stdout_json: dict[str, object] | None = None
    stdout_json_lines: tuple[dict[str, object], ...] = ()
    timed_out: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PackagedRuntimeSmokeResult:
    status: str
    event: str
    strategy: str
    installation_id: str
    schedule_id: str
    pywin32_version: str | None
    nuitka_version: str | None
    source_protocol_trigger: CommandResult | None
    build: CommandResult | None
    executable: dict[str, object] | None
    packaged_protocol_trigger: CommandResult | None
    packaged_engine_host: CommandResult | None
    packaged_gui_status: CommandResult | None
    packaged_task_path: str | None
    task_loaded: bool
    task_in_sync: bool
    task_deleted: bool
    folder_deleted: bool
    root_existed_before: bool
    root_deleted: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in (
            "source_protocol_trigger",
            "build",
            "packaged_protocol_trigger",
            "packaged_engine_host",
            "packaged_gui_status",
        ):
            value = getattr(self, key)
            payload[key] = None if value is None else value.to_dict()
        return payload


def run_packaged_runtime_smoke(
    *,
    output: Path,
    work_dir: Path | None = None,
    python_executable: Path | None = None,
    timeout_seconds: int = 900,
) -> PackagedRuntimeSmokeResult:
    installation_id = f"packaged-smoke-{uuid4().hex[:12]}"
    schedule_id = "schedule-packaged-smoke"
    base_work_dir = (work_dir or _default_work_dir()).resolve()
    state_root = base_work_dir / "state-root"
    root_existed_before = _task_scheduler_folder_exists("\\MediaSync Home") if os.name == "nt" else False
    python_path = python_executable or Path(sys.executable)
    replacements = _sanitizer_replacements(base_work_dir, python_path)

    source_protocol_trigger = _run_protocol_trigger(
        [str(python_path), "-m", "mediasync_home"],
        installation_id=installation_id,
        state_root=state_root,
        replacements=replacements,
    )

    if os.name != "nt":
        result = _result(
            status="BLOCKED_BY_ENVIRONMENT",
            installation_id=installation_id,
            schedule_id=schedule_id,
            source_protocol_trigger=source_protocol_trigger,
            root_existed_before=root_existed_before,
            reason="WINDOWS_REQUIRED",
        )
        _write_result(output, result)
        return result

    prerequisites = _packaging_prerequisites()
    if prerequisites["status"] != "PASS":
        result = _result(
            status="BLOCKED_BY_ENVIRONMENT",
            installation_id=installation_id,
            schedule_id=schedule_id,
            source_protocol_trigger=source_protocol_trigger,
            root_existed_before=root_existed_before,
            reason="MISSING_LOCAL_PACKAGING_PREREQUISITES",
        )
        _write_result(output, result)
        return result

    build_work_dir = base_work_dir
    build = _run_nuitka_product_build(
        work_dir=build_work_dir,
        python_executable=python_path,
        replacements=replacements,
        timeout_seconds=timeout_seconds,
    )
    executable = _find_packaged_executable(build_work_dir)
    executable_payload = _executable_payload(executable, build_work_dir) if executable else None
    if build.returncode != 0 or executable is None:
        result = _result(
            status="FAIL",
            installation_id=installation_id,
            schedule_id=schedule_id,
            source_protocol_trigger=source_protocol_trigger,
            build=build,
            executable=executable_payload,
            root_existed_before=root_existed_before,
            reason="NUITKA_PRODUCT_BUILD_FAILED",
        )
        _write_result(output, result)
        return result

    package_replacements = replacements | {str(executable): "<packaged-executable>"}
    packaged_protocol_trigger = _run_protocol_trigger(
        [str(executable)],
        installation_id=installation_id,
        state_root=state_root,
        replacements=package_replacements,
    )

    schedule = _prepare_packaged_scheduler_state(
        state_root=state_root,
        installation_id=installation_id,
        schedule_id=schedule_id,
        executable_path=executable,
    )
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id=installation_id,
        executable_path=str(executable),
    )
    engine_host, gui_status = _run_packaged_engine_host_scheduler_smoke(
        executable=executable,
        state_root=state_root,
        installation_id=installation_id,
        replacements=package_replacements,
    )

    task_loaded = False
    task_in_sync = False
    try:
        observed = WindowsTaskSchedulerRegistry(Pywin32TaskSchedulerGateway()).load_task(
            definition.task_path
        )
        task_loaded = observed is not None
        if observed is not None:
            plan = classify_task_scheduler_reconciliation(
                schedule,
                installation_id=installation_id,
                executable_path=str(executable),
                observed=observed,
            )
            task_in_sync = plan.action.value == "IN_SYNC"
    finally:
        task_deleted, folder_deleted, root_deleted = _cleanup_task_scheduler_smoke(
            installation_id=installation_id,
            schedule_id=schedule_id,
            delete_root_if_empty=not root_existed_before,
        )

    passed = (
        _source_trigger_routed(source_protocol_trigger)
        and _source_trigger_routed(packaged_protocol_trigger)
        and _engine_host_reconciled_task(engine_host)
        and _gui_status_accepted(gui_status)
        and task_loaded
        and task_in_sync
        and task_deleted
        and folder_deleted
        and (root_existed_before or root_deleted)
    )
    result = PackagedRuntimeSmokeResult(
        status="PASS" if passed else "FAIL",
        event="PACKAGED_RUNTIME_SMOKE",
        strategy="nuitka-standalone-product-entrypoint",
        installation_id=installation_id,
        schedule_id=schedule_id,
        pywin32_version=_installed_version("pywin32"),
        nuitka_version=_installed_version("Nuitka"),
        source_protocol_trigger=source_protocol_trigger,
        build=build,
        executable=executable_payload,
        packaged_protocol_trigger=packaged_protocol_trigger,
        packaged_engine_host=engine_host,
        packaged_gui_status=gui_status,
        packaged_task_path=definition.task_path,
        task_loaded=task_loaded,
        task_in_sync=task_in_sync,
        task_deleted=task_deleted,
        folder_deleted=folder_deleted,
        root_existed_before=root_existed_before,
        root_deleted=root_deleted,
        reason=None if passed else "PACKAGED_RUNTIME_SMOKE_FAILED",
    )
    _write_result(output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and smoke the local unsigned 0B product executable"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/0b/packaged-runtime-smoke.json")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--python-executable", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv)

    result = run_packaged_runtime_smoke(
        output=args.output,
        work_dir=args.work_dir,
        python_executable=args.python_executable,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "PASS" else 2


def _run_nuitka_product_build(
    *,
    work_dir: Path,
    python_executable: Path,
    replacements: dict[str, str],
    timeout_seconds: int,
) -> CommandResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(python_executable),
        "-m",
        "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        f"--output-dir={work_dir}",
        f"--output-filename={EXE_NAME}",
        "--include-package=mediasync_home",
        "--include-package=win32com",
        "--include-module=pythoncom",
        "--include-module=pywintypes",
        str(ROOT / "src/mediasync_home/__main__.py"),
    ]
    env = _source_environment()
    return _run_command(command, replacements=replacements, timeout_seconds=timeout_seconds, env=env)


def _run_protocol_trigger(
    executable_argv: list[str],
    *,
    installation_id: str,
    state_root: Path,
    replacements: dict[str, str],
) -> CommandResult:
    command = [
        *executable_argv,
        "--enqueue-trigger-occurrence",
        "--installation-id",
        installation_id,
        "--state-root",
        str(state_root),
        "--schedule-id",
        "schedule-packaged-smoke",
        "--schedule-revision-hash",
        "a" * 64,
        "--task-definition-hash",
        "a" * 64,
        "--delivery-id",
        "11111111-1111-4111-8111-111111111111",
        "--observed-start-utc",
        "2026-07-24T00:00:00.000Z",
        "--timeout-seconds",
        "1",
    ]
    return _run_command(command, replacements=replacements, timeout_seconds=30, env=_source_environment())


def _run_packaged_engine_host_scheduler_smoke(
    *,
    executable: Path,
    state_root: Path,
    installation_id: str,
    replacements: dict[str, str],
) -> tuple[CommandResult, CommandResult]:
    pipe_name = f"MediaSyncHome-0B-packaged-smoke-{uuid4().hex[:12]}"
    engine_command = [
        str(executable),
        "--role",
        "engine-host",
        "--pipe-name",
        pipe_name,
        "--serve-requests",
        "2",
        "--installation-id",
        installation_id,
        "--state-root",
        str(state_root),
        "--reconcile-task-scheduler-resources",
        "--task-scheduler-executable-path",
        str(executable),
        "--task-scheduler-schedule-page-limit",
        "10",
        "--task-scheduler-max-schedule-pages",
        "1",
        "--task-scheduler-max-claims",
        "2",
        "--task-scheduler-claim-token-prefix",
        "packaged-smoke",
    ]
    gui_command = [
        str(executable),
        "--role",
        "gui",
        "--pipe-name",
        pipe_name,
        "--query-status",
        "--timeout-seconds",
        "10",
    ]
    engine = subprocess.Popen(
        engine_command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_source_environment(),
    )
    try:
        gui_status = _run_command(
            gui_command,
            replacements=replacements,
            timeout_seconds=20,
            env=_source_environment(),
        )
        try:
            stdout, stderr = engine.communicate(timeout=20)
            engine_result = _completed_command(
                returncode=engine.returncode,
                stdout=stdout,
                stderr=stderr,
                replacements=replacements,
            )
        except subprocess.TimeoutExpired:
            engine.kill()
            stdout, stderr = engine.communicate(timeout=10)
            engine_result = _completed_command(
                returncode=engine.returncode,
                stdout=stdout,
                stderr=stderr,
                replacements=replacements,
                timed_out=True,
            )
    except Exception:
        if engine.poll() is None:
            engine.kill()
            engine.communicate(timeout=10)
        raise
    return engine_result, gui_status


def _prepare_packaged_scheduler_state(
    *,
    state_root: Path,
    installation_id: str,
    schedule_id: str,
    executable_path: Path,
) -> ScheduleDefinition:
    layout = build_state_store_layout(state_root)
    migration_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    migrate_sqlite_state_stores(
        layout,
        catalog_plan=catalog_migration_plan(),
        recovery_plan=recovery_migration_plan(),
        app_version="packaged-runtime-smoke",
        started_utc=migration_utc,
        completed_utc=migration_utc,
    )
    with sqlite3.connect(layout.catalog) as connection:
        apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(layout.catalog))
        apply_sqlite_migrations(connection, catalog_migration_plan())
        _insert_plan_parent_rows(connection)
        schedule = bind_same_user_task_scheduler_definition_hash(
            ScheduleDefinition(
                schedule_id=schedule_id,
                job_id="job-packaged-smoke",
                plan_id="plan-packaged-smoke",
                plan_checksum="a" * 64,
                trigger_type=TriggerKind.SCHEDULED_TIME,
                configuration_json='{"days_interval":1,"hour":3,"kind":"daily","minute":17}',
                definition_generation=1,
                desired_definition_hash="0" * 64,
                time_zone_id="Europe/Oslo",
                dst_policy="PRESERVE_WALL_TIME",
                misfire_policy="QUEUE_ONCE",
                coalescing_window_seconds=60,
                task_logon_type="INTERACTIVE_TOKEN",
                requires_network=False,
                run_only_when_logged_on=True,
                enabled=False,
                row_version=1,
            ),
            installation_id=installation_id,
            executable_path=str(executable_path),
        )
        SqliteScheduleStore(connection).save_schedule(schedule)
        return schedule


def _insert_plan_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-packaged-smoke', 'multi_target_backup')")
    connection.execute(
        "INSERT INTO filter_sets (job_id, id) VALUES ('job-packaged-smoke', 'filter-packaged-smoke')"
    )
    filter_rules_json = '{"preset":"ALL_USER_FILES","schema_version":1}'
    connection.execute(
        """
        INSERT INTO filter_set_versions (
            job_id,
            filter_set_id,
            version,
            rules_hash,
            rules_json
        )
        VALUES ('job-packaged-smoke', 'filter-packaged-smoke', 1, ?, ?)
        """,
        (
            hashlib.sha256(filter_rules_json.encode("utf-8")).hexdigest(),
            filter_rules_json,
        ),
    )
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id, filter_set_version)
            VALUES (
                'job-packaged-smoke',
                'job-rev-packaged-smoke',
                'filter-packaged-smoke',
                1
            )
        """
    )
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-packaged-smoke', 'job-packaged-smoke', 'job-rev-packaged-smoke')
        """
    )
    connection.execute(
        "INSERT INTO plans (id, analysis_id) VALUES ('plan-packaged-smoke', 'analysis-packaged-smoke')"
    )
    connection.execute(
        """
        INSERT INTO plan_seal_details (
            plan_id,
            analysis_id,
            job_id,
            job_revision_id,
            planner_version,
            plan_schema_version,
            operation_schema_version,
            execution_policy,
            checksum_algorithm,
            serializer_version,
            plan_checksum,
            risk_summary_json,
            operation_count,
            planned_bytes
        )
        VALUES (
            'plan-packaged-smoke',
            'analysis-packaged-smoke',
            'job-packaged-smoke',
            'job-rev-packaged-smoke',
            'planner',
            1,
            1,
            'dry-run',
            'SHA-256',
            'canonical-json',
            ?,
            '{}',
            1,
            0
        )
        """,
        ("a" * 64,),
    )


def _run_command(
    command: list[str],
    *,
    replacements: dict[str, str],
    timeout_seconds: int,
    env: dict[str, str],
) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=env,
        )
        return _completed_command(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            replacements=replacements,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _text_from_timeout_payload(exc.stdout)
        stderr = _text_from_timeout_payload(exc.stderr)
        return _completed_command(
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            replacements=replacements,
            timed_out=True,
        )


def _completed_command(
    *,
    returncode: int | None,
    stdout: str,
    stderr: str,
    replacements: dict[str, str],
    timed_out: bool = False,
) -> CommandResult:
    stdout_json = _parse_json_object(stdout)
    stdout_json_lines = _parse_json_object_lines(stdout)
    return CommandResult(
        returncode=returncode,
        stdout_tail=_text_tail(stdout, replacements),
        stderr_tail=_text_tail(stderr, replacements),
        stdout_json=None
        if stdout_json is None
        else _sanitize_json_object(stdout_json, replacements),
        stdout_json_lines=tuple(
            _sanitize_json_object(item, replacements) for item in stdout_json_lines
        ),
        timed_out=timed_out,
    )


def _parse_json_object(value: str) -> dict[str, object] | None:
    try:
        payload = json.loads(value.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): item for key, item in payload.items()}


def _parse_json_object_lines(value: str) -> tuple[dict[str, object], ...]:
    objects: list[dict[str, object]] = []
    for line in value.splitlines():
        parsed = _parse_json_object(line)
        if parsed is not None:
            objects.append(parsed)
    return tuple(objects)


def _source_trigger_routed(result: CommandResult | None) -> bool:
    if result is None or result.returncode != 2 or result.stdout_json is None:
        return False
    payload = result.stdout_json.get("payload")
    return (
        result.stdout_json.get("status") == "REJECTED"
        and result.stdout_json.get("reason") == "ENGINE_HOST_UNAVAILABLE"
        and isinstance(payload, dict)
        and payload.get("reason") == "HOST_LOCATOR_PUBLICATION_UNAVAILABLE"
    )


def _engine_host_reconciled_task(result: CommandResult | None) -> bool:
    if result is None or result.returncode != 0:
        return False
    starting = next(
        (line for line in result.stdout_json_lines if line.get("event") == "ENGINE_HOST_PIPE_STARTING"),
        None,
    )
    stopped = next(
        (line for line in result.stdout_json_lines if line.get("event") == "ENGINE_HOST_PIPE_STOPPED"),
        None,
    )
    if starting is None or stopped is None:
        return False
    reconciliation = starting.get("task_scheduler_reconciliation")
    if not isinstance(reconciliation, dict):
        return False
    return (
        reconciliation.get("resources_staged") == 1
        and reconciliation.get("resources_reconciled") == 1
        and reconciliation.get("resources_completed") == 1
        and reconciliation.get("resources_blocked") == 0
        and reconciliation.get("resources_applied") == 1
    )


def _gui_status_accepted(result: CommandResult | None) -> bool:
    return (
        result is not None
        and result.returncode == 0
        and result.stdout_json is not None
        and result.stdout_json.get("status") == "ACCEPTED"
    )


def _find_packaged_executable(work_dir: Path) -> Path | None:
    matches = sorted(work_dir.rglob(EXE_NAME))
    return matches[0] if matches else None


def _executable_payload(executable: Path, work_dir: Path) -> dict[str, object]:
    file_count, total_bytes = _directory_size(executable.parent)
    return {
        "relative_to_work_dir": str(executable.relative_to(work_dir)),
        "sha256": _sha256_file(executable),
        "size_bytes": executable.stat().st_size,
        "dist_file_count": file_count,
        "dist_size_bytes": total_bytes,
    }


def _packaging_prerequisites() -> dict[str, object]:
    modules = {
        "mediasync_home": importlib.util.find_spec("mediasync_home") is not None,
        "nuitka": importlib.util.find_spec("nuitka") is not None,
        "PySide6": importlib.util.find_spec("PySide6") is not None,
        "pywin32": _installed_version("pywin32") is not None,
        "win32com.client": importlib.util.find_spec("win32com.client") is not None,
    }
    tools = {
        "nuitka": _script_exists("nuitka"),
    }
    return {
        "status": "PASS" if all(modules.values()) and all(tools.values()) else "BLOCKED_BY_ENVIRONMENT",
        "modules": modules,
        "tools": tools,
    }


def _source_environment() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) if not existing else os.pathsep.join((str(SRC), existing))
    return env


def _script_exists(name: str) -> bool:
    scripts_dir = Path(sys.executable).resolve().parent
    candidates = [scripts_dir / name]
    if os.name == "nt":
        candidates.extend([scripts_dir / f"{name}.exe", scripts_dir / f"{name}.cmd", scripts_dir / f"{name}.bat"])
    return any(candidate.exists() for candidate in candidates) or shutil.which(name) is not None


def _default_work_dir() -> Path:
    run_id = f"0b-packaged-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    return Path(tempfile.gettempdir()) / "MediaSyncHome-0B" / run_id


def _sanitizer_replacements(work_dir: Path, python_executable: Path) -> dict[str, str]:
    return {
        str(work_dir): "<packaged-work-dir>",
        str(ROOT): "<repo-root>",
        str(SRC): "<repo-src>",
        str(python_executable): "<python-executable>",
        os.environ.get("USERPROFILE", ""): "<user-profile>",
        os.environ.get("TEMP", ""): "<temp-root>",
        os.environ.get("TMP", ""): "<temp-root>",
    }


def _text_tail(value: str, replacements: dict[str, str]) -> str:
    return _sanitize_text(value[-TEXT_TAIL_LIMIT:], replacements)


def _sanitize_text(value: str, replacements: dict[str, str]) -> str:
    sanitized = value
    for raw, token in replacements.items():
        if raw:
            sanitized = sanitized.replace(raw, token)
            sanitized = sanitized.replace(raw.replace("\\", "/"), token)
            sanitized = sanitized.replace(raw.replace("\\", "\\\\"), token)
    return sanitized


def _sanitize_json_object(
    value: dict[str, object],
    replacements: dict[str, str],
) -> dict[str, object]:
    return {
        key: _sanitize_json_value(item, replacements)
        for key, item in value.items()
    }


def _sanitize_json_value(value: object, replacements: dict[str, str]) -> object:
    if isinstance(value, str):
        return _sanitize_text(value, replacements)
    if isinstance(value, dict):
        return {
            str(key): _sanitize_json_value(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json_value(item, replacements) for item in value]
    return value


def _text_from_timeout_payload(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _directory_size(root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
    return file_count, total_bytes


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_version(distribution_name: str) -> str | None:
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _result(
    *,
    status: str,
    installation_id: str,
    schedule_id: str,
    source_protocol_trigger: CommandResult | None = None,
    build: CommandResult | None = None,
    executable: dict[str, object] | None = None,
    root_existed_before: bool = False,
    reason: str | None = None,
) -> PackagedRuntimeSmokeResult:
    return PackagedRuntimeSmokeResult(
        status=status,
        event="PACKAGED_RUNTIME_SMOKE",
        strategy="nuitka-standalone-product-entrypoint",
        installation_id=installation_id,
        schedule_id=schedule_id,
        pywin32_version=_installed_version("pywin32"),
        nuitka_version=_installed_version("Nuitka"),
        source_protocol_trigger=source_protocol_trigger,
        build=build,
        executable=executable,
        packaged_protocol_trigger=None,
        packaged_engine_host=None,
        packaged_gui_status=None,
        packaged_task_path=None,
        task_loaded=False,
        task_in_sync=False,
        task_deleted=False,
        folder_deleted=False,
        root_existed_before=root_existed_before,
        root_deleted=False,
        reason=reason,
    )


def _write_result(output: Path, result: PackagedRuntimeSmokeResult) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
