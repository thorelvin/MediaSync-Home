from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, cast


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mediasync_home.adapters.task_scheduler import (  # noqa: E402
    Pywin32TaskSchedulerGateway,
    TaskSchedulerAdapterError,
    WindowsTaskSchedulerRegistry,
)
from mediasync_home.application.schedules import ScheduleDefinition  # noqa: E402
from mediasync_home.application.task_scheduler import (  # noqa: E402
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
    classify_task_scheduler_reconciliation,
)
from mediasync_home.application.trigger_occurrences import TriggerKind  # noqa: E402


@dataclass(frozen=True)
class TaskSchedulerComSmokeResult:
    status: str
    event: str
    task_path: str | None
    installation_id: str
    schedule_id: str
    pywin32_version: str | None
    applied: bool
    loaded: bool
    in_sync: bool
    task_deleted: bool
    folder_deleted: bool
    root_existed_before: bool
    root_deleted: bool
    error_type: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_task_scheduler_com_smoke(
    *,
    executable_path: Path | None = None,
    installation_id: str | None = None,
    schedule_id: str = "schedule-smoke",
) -> TaskSchedulerComSmokeResult:
    if os.name != "nt":
        return _blocked_result(
            installation_id=installation_id or "smoke-unavailable",
            schedule_id=schedule_id,
            reason="WINDOWS_REQUIRED",
        )

    resolved_installation_id = installation_id or f"smoke-{uuid.uuid4().hex[:16]}"
    resolved_executable = str((executable_path or Path(sys.executable)).resolve())
    schedule = _smoke_schedule(
        installation_id=resolved_installation_id,
        schedule_id=schedule_id,
        executable_path=resolved_executable,
    )
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id=resolved_installation_id,
        executable_path=resolved_executable,
    )
    registry = WindowsTaskSchedulerRegistry(Pywin32TaskSchedulerGateway())
    applied = False
    loaded = False
    in_sync = False
    task_deleted = False
    folder_deleted = False
    root_existed_before = _task_scheduler_folder_exists("\\MediaSync Home")
    root_deleted = False
    error_type: str | None = None
    reason: str | None = None
    try:
        registry.apply_task_definition(definition)
        applied = True
        observed = registry.load_task(definition.task_path)
        loaded = observed is not None
        if observed is not None:
            plan = classify_task_scheduler_reconciliation(
                schedule,
                installation_id=resolved_installation_id,
                executable_path=resolved_executable,
                observed=observed,
            )
            in_sync = plan.action.value == "IN_SYNC"
            reason = plan.reason
    except TaskSchedulerAdapterError as exc:
        error_type = type(exc).__name__
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - smoke artifact records sanitized failure type.
        error_type = type(exc).__name__
        reason = "TASK_SCHEDULER_COM_SMOKE_FAILED"
    finally:
        task_deleted, folder_deleted, root_deleted = _cleanup_task_scheduler_smoke(
            installation_id=resolved_installation_id,
            schedule_id=schedule_id,
            delete_root_if_empty=not root_existed_before,
        )

    root_cleanup_ok = root_existed_before or root_deleted
    passed = applied and loaded and in_sync and task_deleted and folder_deleted and root_cleanup_ok
    status = "PASS" if passed else "BLOCKED_BY_ENVIRONMENT" if error_type else "FAIL"
    return TaskSchedulerComSmokeResult(
        status=status,
        event="TASK_SCHEDULER_COM_SMOKE",
        task_path=definition.task_path,
        installation_id=resolved_installation_id,
        schedule_id=schedule_id,
        pywin32_version=_installed_version("pywin32"),
        applied=applied,
        loaded=loaded,
        in_sync=in_sync,
        task_deleted=task_deleted,
        folder_deleted=folder_deleted,
        root_existed_before=root_existed_before,
        root_deleted=root_deleted,
        error_type=error_type,
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create, load and clean up one disabled Task Scheduler COM smoke task"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/0b/task-scheduler-com-smoke.json")
    parser.add_argument("--executable-path", type=Path)
    parser.add_argument("--installation-id")
    parser.add_argument("--schedule-id", default="schedule-smoke")
    args = parser.parse_args(argv)

    result = run_task_scheduler_com_smoke(
        executable_path=args.executable_path,
        installation_id=args.installation_id,
        schedule_id=args.schedule_id,
    )
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "PASS" else 2


def _smoke_schedule(
    *,
    installation_id: str,
    schedule_id: str,
    executable_path: str,
) -> ScheduleDefinition:
    return bind_same_user_task_scheduler_definition_hash(
        ScheduleDefinition(
            schedule_id=schedule_id,
            job_id="job-smoke",
            plan_id="plan-smoke",
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
        executable_path=executable_path,
    )


def _cleanup_task_scheduler_smoke(
    *,
    installation_id: str,
    schedule_id: str,
    delete_root_if_empty: bool,
) -> tuple[bool, bool, bool]:
    task_deleted = False
    folder_deleted = False
    root_deleted = False
    try:
        with _task_scheduler_com_apartment():
            service = _connect_task_scheduler_service()
            folder = _call_method(
                service,
                "GetFolder",
                f"\\MediaSync Home\\{installation_id}",
            )
            _call_method(folder, "DeleteTask", schedule_id, 0)
            task_deleted = True
            del folder
            root = _call_method(service, "GetFolder", "\\MediaSync Home")
            _call_method(root, "DeleteFolder", installation_id, 0)
            folder_deleted = True
            if delete_root_if_empty and _folder_is_empty(root):
                scheduler_root = _call_method(service, "GetFolder", "\\")
                _call_method(scheduler_root, "DeleteFolder", "MediaSync Home", 0)
                root_deleted = True
                del scheduler_root
            del root, service
    except Exception:
        pass
    return task_deleted, folder_deleted, root_deleted


def _task_scheduler_folder_exists(folder_path: str) -> bool:
    try:
        with _task_scheduler_com_apartment():
            service = _connect_task_scheduler_service()
            folder = _call_method(service, "GetFolder", folder_path)
            del folder, service
        return True
    except Exception:
        return False


def _folder_is_empty(folder: object) -> bool:
    folders = _call_method(folder, "GetFolders", 0)
    tasks = _call_method(folder, "GetTasks", 0)
    is_empty = int(getattr(folders, "Count")) == 0 and int(getattr(tasks, "Count")) == 0
    del folders, tasks
    return is_empty


@contextmanager
def _task_scheduler_com_apartment() -> Iterator[None]:
    pythoncom = __import__("pythoncom")
    co_initialize = cast(Callable[[], object], getattr(pythoncom, "CoInitialize"))
    co_uninitialize = cast(Callable[[], object], getattr(pythoncom, "CoUninitialize"))
    co_initialize()
    try:
        yield
    finally:
        co_uninitialize()


def _connect_task_scheduler_service() -> object:
    win32com_client = __import__("win32com.client", fromlist=["Dispatch"])
    dispatch = cast(Callable[[str], Any], getattr(win32com_client, "Dispatch"))
    service = cast(object, dispatch("Schedule.Service"))
    _call_method(service, "Connect")
    return service


def _call_method(target: object, name: str, *args: object) -> object:
    method = cast(Callable[..., object], getattr(target, name))
    return method(*args)


def _installed_version(distribution_name: str) -> str | None:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return None


def _blocked_result(
    *,
    installation_id: str,
    schedule_id: str,
    reason: str,
) -> TaskSchedulerComSmokeResult:
    return TaskSchedulerComSmokeResult(
        status="BLOCKED_BY_ENVIRONMENT",
        event="TASK_SCHEDULER_COM_SMOKE",
        task_path=None,
        installation_id=installation_id,
        schedule_id=schedule_id,
        pywin32_version=_installed_version("pywin32"),
        applied=False,
        loaded=False,
        in_sync=False,
        task_deleted=False,
        folder_deleted=False,
        root_existed_before=False,
        root_deleted=False,
        error_type=None,
        reason=reason,
    )


if __name__ == "__main__":
    raise SystemExit(main())
