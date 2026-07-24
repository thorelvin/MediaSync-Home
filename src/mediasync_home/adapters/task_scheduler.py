from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Callable, Literal, Protocol, cast

from mediasync_home.adapters.windows_argv import (
    WindowsCommandLineError,
    build_windows_argument_line,
    build_windows_command_line,
    parse_windows_argument_line,
)
from mediasync_home.application.task_scheduler import (
    ObservedTaskSchedulerDefinition,
    TaskSchedulerDefinition,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


UNPARSEABLE_TASK_SCHEDULER_ARGUMENTS = "<TASK_SCHEDULER_ARGUMENT_PARSE_FAILED>"
TASK_SCHEDULER_METADATA_SCHEMA_VERSION = 1
TASK_ACTION_EXEC = 0
TASK_CREATE_OR_UPDATE = 0x6
TASK_INSTANCES_PARALLEL = 0
TASK_INSTANCES_QUEUE = 1
TASK_INSTANCES_IGNORE_NEW = 2
TASK_INSTANCES_STOP_EXISTING = 3
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0
TASK_TRIGGER_DAILY = 2

_NOT_FOUND_HRESULTS = {0x80070002, 0x80070003}
_MULTIPLE_INSTANCE_POLICY_TO_COM = {
    "PARALLEL": TASK_INSTANCES_PARALLEL,
    "QUEUE": TASK_INSTANCES_QUEUE,
    "IGNORE_NEW": TASK_INSTANCES_IGNORE_NEW,
    "STOP_EXISTING": TASK_INSTANCES_STOP_EXISTING,
}
_MULTIPLE_INSTANCE_POLICY_FROM_COM = {
    value: key for key, value in _MULTIPLE_INSTANCE_POLICY_TO_COM.items()
}
_LOGON_TYPE_TO_COM = {"INTERACTIVE_TOKEN": TASK_LOGON_INTERACTIVE_TOKEN}
_LOGON_TYPE_FROM_COM = {TASK_LOGON_INTERACTIVE_TOKEN: "INTERACTIVE_TOKEN"}


class TaskSchedulerAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSchedulerGatewayTask:
    task_path: str
    folder_path: str
    task_name: str
    executable_path: str
    argument_line: str
    enabled: bool
    trigger_type: TriggerKind
    configuration_json: str
    time_zone_id: str | None
    task_logon_type: str
    run_only_when_logged_on: bool
    requires_network: bool
    multiple_instances_policy: str
    execution_time_limit_seconds: int
    stop_on_execution_time_limit: bool


class TaskSchedulerGateway(Protocol):
    def load_task(self, task_path: str) -> TaskSchedulerGatewayTask | None: ...

    def apply_task(self, task: TaskSchedulerGatewayTask) -> None: ...


class ComApartment(Protocol):
    def __enter__(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]: ...


class WindowsTaskSchedulerRegistry:
    def __init__(self, gateway: TaskSchedulerGateway) -> None:
        self._gateway = gateway

    def load_task(self, task_path: str) -> ObservedTaskSchedulerDefinition | None:
        task = self._gateway.load_task(task_path)
        if task is None:
            return None
        try:
            arguments = parse_windows_argument_line(task.argument_line)
        except WindowsCommandLineError:
            arguments = (UNPARSEABLE_TASK_SCHEDULER_ARGUMENTS,)
        return ObservedTaskSchedulerDefinition(
            task_path=task.task_path,
            executable_path=task.executable_path,
            arguments=arguments,
            enabled=task.enabled,
            trigger_type=task.trigger_type,
            configuration_json=task.configuration_json,
            time_zone_id=task.time_zone_id,
            task_logon_type=task.task_logon_type,
            run_only_when_logged_on=task.run_only_when_logged_on,
            requires_network=task.requires_network,
            multiple_instances_policy=task.multiple_instances_policy,
            execution_time_limit_seconds=task.execution_time_limit_seconds,
            stop_on_execution_time_limit=task.stop_on_execution_time_limit,
        )

    def apply_task_definition(self, definition: TaskSchedulerDefinition) -> None:
        self._gateway.apply_task(_task_from_definition(definition))


class Pywin32TaskSchedulerGateway:
    def __init__(
        self,
        *,
        service_factory: Callable[[], object] | None = None,
        com_apartment_factory: Callable[[], ComApartment] | None = None,
        author: str = "MediaSync Home",
    ) -> None:
        self._service_factory = service_factory or _connect_pywin32_task_scheduler
        self._com_apartment_factory = com_apartment_factory or (
            _Pywin32ComApartment if service_factory is None else _NullComApartment
        )
        self._author = author

    def load_task(self, task_path: str) -> TaskSchedulerGatewayTask | None:
        folder_path, task_name = _split_task_path(task_path)
        try:
            with self._com_apartment_factory():
                folder = _call_method(self._service_factory(), "GetFolder", folder_path)
                registered_task = _call_method(folder, "GetTask", task_name)
        except TaskSchedulerAdapterError:
            raise
        except Exception as exc:
            if _is_not_found_error(exc):
                return None
            raise TaskSchedulerAdapterError("TASK_SCHEDULER_COM_LOAD_FAILED") from exc
        return _gateway_task_from_registered(task_path, folder_path, task_name, registered_task)

    def apply_task(self, task: TaskSchedulerGatewayTask) -> None:
        try:
            with self._com_apartment_factory():
                service = self._service_factory()
                folder = _ensure_folder(service, task.folder_path)
                task_definition = _call_method(service, "NewTask", 0)
                _configure_task_definition(task_definition, task, author=self._author)
                _call_method(
                    folder,
                    "RegisterTaskDefinition",
                    task.task_name,
                    task_definition,
                    TASK_CREATE_OR_UPDATE,
                    None,
                    None,
                    _com_logon_type(task.task_logon_type),
                    None,
                )
        except TaskSchedulerAdapterError:
            raise
        except Exception as exc:
            raise TaskSchedulerAdapterError("TASK_SCHEDULER_COM_APPLY_FAILED") from exc


class _NullComApartment:
    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc, tb
        return False


class _Pywin32ComApartment:
    def __init__(self) -> None:
        self._pythoncom: Any | None = None

    def __enter__(self) -> None:
        try:
            pythoncom = importlib.import_module("pythoncom")
        except ModuleNotFoundError as exc:
            raise TaskSchedulerAdapterError("TASK_SCHEDULER_PYWIN32_UNAVAILABLE") from exc
        co_initialize = cast(Callable[[], object], getattr(pythoncom, "CoInitialize"))
        try:
            co_initialize()
        except Exception as exc:
            raise TaskSchedulerAdapterError("TASK_SCHEDULER_COM_APARTMENT_FAILED") from exc
        self._pythoncom = pythoncom

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc, tb
        if self._pythoncom is not None:
            co_uninitialize = cast(Callable[[], object], getattr(self._pythoncom, "CoUninitialize"))
            co_uninitialize()
        return False


def _task_from_definition(definition: TaskSchedulerDefinition) -> TaskSchedulerGatewayTask:
    folder_path, task_name = _split_task_path(definition.task_path)
    _validate_action_command_line_budget(definition)
    return TaskSchedulerGatewayTask(
        task_path=definition.task_path,
        folder_path=folder_path,
        task_name=task_name,
        executable_path=definition.executable_path,
        argument_line=build_windows_argument_line(definition.arguments),
        enabled=definition.enabled,
        trigger_type=definition.trigger_type,
        configuration_json=definition.configuration_json,
        time_zone_id=definition.time_zone_id,
        task_logon_type=definition.task_logon_type,
        run_only_when_logged_on=definition.run_only_when_logged_on,
        requires_network=definition.requires_network,
        multiple_instances_policy=definition.multiple_instances_policy,
        execution_time_limit_seconds=definition.execution_time_limit_seconds,
        stop_on_execution_time_limit=definition.stop_on_execution_time_limit,
    )


def _validate_action_command_line_budget(definition: TaskSchedulerDefinition) -> None:
    try:
        build_windows_command_line((definition.executable_path, *definition.arguments))
    except WindowsCommandLineError as exc:
        raise TaskSchedulerAdapterError(str(exc)) from exc


def _split_task_path(task_path: str) -> tuple[str, str]:
    if not task_path.startswith("\\") or task_path == "\\" or "\\\\" in task_path:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_TASK_PATH_INVALID")
    parts = tuple(part for part in task_path.split("\\") if part)
    if not parts or any(part in {".", ".."} or part.strip() != part for part in parts):
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_TASK_PATH_INVALID")
    task_name = parts[-1]
    folder_path = "\\" if len(parts) == 1 else "\\" + "\\".join(parts[:-1])
    return folder_path, task_name


def _connect_pywin32_task_scheduler() -> object:
    try:
        win32com_client = importlib.import_module("win32com.client")
    except ModuleNotFoundError as exc:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_PYWIN32_UNAVAILABLE") from exc
    dispatch = cast(Callable[[str], Any], getattr(win32com_client, "Dispatch"))
    service = cast(object, dispatch("Schedule.Service"))
    _call_method(service, "Connect")
    return service


def _ensure_folder(service: object, folder_path: str) -> object:
    if folder_path == "\\":
        return _call_method(service, "GetFolder", "\\")

    current = _call_method(service, "GetFolder", "\\")
    for folder_name in folder_path.strip("\\").split("\\"):
        try:
            current = _call_method(current, "GetFolder", folder_name)
        except Exception as exc:
            if not _is_not_found_error(exc):
                raise
            current = _call_method(current, "CreateFolder", folder_name)
    return current


def _configure_task_definition(
    task_definition: object,
    task: TaskSchedulerGatewayTask,
    *,
    author: str,
) -> None:
    task_metadata = _metadata_json(task)
    _set_attr(task_definition, "Data", task_metadata)

    registration_info = _attr(task_definition, "RegistrationInfo")
    _set_attr(registration_info, "Author", author)
    _set_attr(registration_info, "Description", f"MediaSync Home trigger: {task.task_path}")

    settings = _attr(task_definition, "Settings")
    _set_attr(settings, "Enabled", task.enabled)
    _set_attr(settings, "StartWhenAvailable", True)
    _set_attr(settings, "RunOnlyIfNetworkAvailable", task.requires_network)
    _set_attr(settings, "MultipleInstances", _com_multiple_instances_policy(task))
    _set_attr(settings, "ExecutionTimeLimit", _execution_time_limit(task))
    _set_attr(settings, "AllowDemandStart", True)
    _set_attr(settings, "AllowHardTerminate", task.stop_on_execution_time_limit)
    _set_attr(settings, "DisallowStartIfOnBatteries", False)
    _set_attr(settings, "StopIfGoingOnBatteries", False)

    principal = _attr(task_definition, "Principal")
    _set_attr(principal, "LogonType", _com_logon_type(task.task_logon_type))
    _set_attr(principal, "RunLevel", TASK_RUNLEVEL_LUA)

    _configure_trigger(_attr(task_definition, "Triggers"), task)
    action = _call_method(_attr(task_definition, "Actions"), "Create", TASK_ACTION_EXEC)
    _set_attr(action, "Path", task.executable_path)
    _set_attr(action, "Arguments", task.argument_line)


def _configure_trigger(triggers: object, task: TaskSchedulerGatewayTask) -> None:
    if task.trigger_type is not TriggerKind.SCHEDULED_TIME:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_TRIGGER_TYPE_UNSUPPORTED")
    configuration = _configuration_object(task.configuration_json)
    kind = configuration.get("kind")
    if kind != "daily":
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_TRIGGER_CONFIGURATION_UNSUPPORTED")

    trigger = _call_method(triggers, "Create", TASK_TRIGGER_DAILY)
    _set_attr(trigger, "Id", "MediaSyncHomeDailyTrigger")
    _set_attr(trigger, "Enabled", task.enabled)
    _set_attr(trigger, "StartBoundary", _daily_start_boundary(configuration))
    _set_attr(trigger, "DaysInterval", _daily_days_interval(configuration))


def _gateway_task_from_registered(
    task_path: str,
    folder_path: str,
    task_name: str,
    registered_task: object,
) -> TaskSchedulerGatewayTask:
    task_definition = _attr(registered_task, "Definition")
    metadata = _metadata_from_raw(_optional_str_attr(task_definition, "Data"))
    settings = _attr(task_definition, "Settings")
    principal = _attr(task_definition, "Principal")
    try:
        action = _first_action(_attr(task_definition, "Actions"))
        executable_path = _str_attr(action, "Path")
        argument_line = _optional_str_attr(action, "Arguments")
    except TaskSchedulerAdapterError:
        executable_path = ""
        argument_line = "\x00"
    execution_seconds, stop_on_limit = _execution_limit_from_com(
        _optional_str_attr(settings, "ExecutionTimeLimit")
    )
    logon_type = _logon_type_from_com(_int_attr(principal, "LogonType"))
    return TaskSchedulerGatewayTask(
        task_path=task_path,
        folder_path=folder_path,
        task_name=task_name,
        executable_path=executable_path,
        argument_line=argument_line,
        enabled=_bool_attr(settings, "Enabled"),
        trigger_type=metadata.trigger_type,
        configuration_json=metadata.configuration_json,
        time_zone_id=metadata.time_zone_id,
        task_logon_type=logon_type,
        run_only_when_logged_on=logon_type == "INTERACTIVE_TOKEN",
        requires_network=_bool_attr(settings, "RunOnlyIfNetworkAvailable"),
        multiple_instances_policy=_multiple_instances_policy_from_com(
            _int_attr(settings, "MultipleInstances")
        ),
        execution_time_limit_seconds=execution_seconds,
        stop_on_execution_time_limit=stop_on_limit,
    )


@dataclass(frozen=True)
class _TaskSchedulerMetadata:
    trigger_type: TriggerKind
    configuration_json: str
    time_zone_id: str | None


def _metadata_json(task: TaskSchedulerGatewayTask) -> str:
    return json.dumps(
        {
            "configuration_json": task.configuration_json,
            "schema_version": TASK_SCHEDULER_METADATA_SCHEMA_VERSION,
            "time_zone_id": task.time_zone_id,
            "trigger_type": task.trigger_type.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _metadata_from_raw(raw: str) -> _TaskSchedulerMetadata:
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("metadata must be an object")
        trigger_type = TriggerKind(str(parsed["trigger_type"]))
        configuration_json = str(parsed["configuration_json"])
        time_zone = parsed.get("time_zone_id")
        if time_zone is not None:
            time_zone = str(time_zone)
        return _TaskSchedulerMetadata(
            trigger_type=trigger_type,
            configuration_json=configuration_json,
            time_zone_id=time_zone,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _TaskSchedulerMetadata(
            trigger_type=TriggerKind.SCHEDULED_TIME,
            configuration_json="{}",
            time_zone_id=None,
        )


def _configuration_object(configuration_json: str) -> dict[str, object]:
    try:
        parsed = json.loads(configuration_json)
    except json.JSONDecodeError as exc:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_CONFIGURATION_JSON_INVALID") from exc
    if not isinstance(parsed, dict):
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_CONFIGURATION_MUST_BE_OBJECT")
    return parsed


def _daily_start_boundary(configuration: dict[str, object]) -> str:
    hour = _bounded_int(configuration.get("hour", 0), minimum=0, maximum=23)
    minute = _bounded_int(configuration.get("minute", 0), minimum=0, maximum=59)
    return f"2000-01-01T{hour:02d}:{minute:02d}:00"


def _daily_days_interval(configuration: dict[str, object]) -> int:
    return _bounded_int(configuration.get("days_interval", 1), minimum=1, maximum=255)


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_CONFIGURATION_INTEGER_INVALID")
    if not minimum <= value <= maximum:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_CONFIGURATION_INTEGER_OUT_OF_RANGE")
    return value


def _com_logon_type(task_logon_type: str) -> int:
    try:
        return _LOGON_TYPE_TO_COM[task_logon_type]
    except KeyError as exc:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_LOGON_TYPE_UNSUPPORTED") from exc


def _logon_type_from_com(value: int) -> str:
    return _LOGON_TYPE_FROM_COM.get(value, f"UNKNOWN_COM_LOGON_TYPE_{value}")


def _com_multiple_instances_policy(task: TaskSchedulerGatewayTask) -> int:
    try:
        return _MULTIPLE_INSTANCE_POLICY_TO_COM[task.multiple_instances_policy]
    except KeyError as exc:
        raise TaskSchedulerAdapterError(
            "TASK_SCHEDULER_MULTIPLE_INSTANCES_POLICY_UNSUPPORTED"
        ) from exc


def _multiple_instances_policy_from_com(value: int) -> str:
    return _MULTIPLE_INSTANCE_POLICY_FROM_COM.get(
        value,
        f"UNKNOWN_COM_MULTIPLE_INSTANCES_POLICY_{value}",
    )


def _execution_time_limit(task: TaskSchedulerGatewayTask) -> str:
    if not task.stop_on_execution_time_limit:
        return "PT0S"
    if task.execution_time_limit_seconds < 1:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_EXECUTION_LIMIT_MUST_BE_POSITIVE")
    return _duration_seconds_to_iso(task.execution_time_limit_seconds)


def _duration_seconds_to_iso(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    pieces = ["PT"]
    if hours:
        pieces.append(f"{hours}H")
    if minutes:
        pieces.append(f"{minutes}M")
    if seconds or len(pieces) == 1:
        pieces.append(f"{seconds}S")
    return "".join(pieces)


def _execution_limit_from_com(raw: str) -> tuple[int, bool]:
    if raw in {"", "PT0S"}:
        return 0, False
    if not raw.startswith("PT"):
        return 0, True
    remaining = raw[2:]
    total = 0
    digits = ""
    for character in remaining:
        if character.isdigit():
            digits += character
            continue
        if not digits:
            return 0, True
        value = int(digits)
        digits = ""
        if character == "H":
            total += value * 3600
        elif character == "M":
            total += value * 60
        elif character == "S":
            total += value
        else:
            return 0, True
    if digits:
        return 0, True
    return total, total > 0


def _first_action(actions: object) -> object:
    if _int_attr(actions, "Count") < 1:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_ACTION_MISSING")
    action = _call_method(actions, "Item", 1)
    if _int_attr(action, "Type") != TASK_ACTION_EXEC:
        raise TaskSchedulerAdapterError("TASK_SCHEDULER_ACTION_UNSUPPORTED")
    return action


def _attr(source: object, name: str) -> object:
    return cast(object, getattr(source, name))


def _set_attr(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


def _call_method(target: object, name: str, *args: object) -> object:
    method = cast(Callable[..., object], getattr(target, name))
    return method(*args)


def _str_attr(source: object, name: str) -> str:
    return str(_attr(source, name))


def _optional_str_attr(source: object, name: str) -> str:
    value = _attr(source, name)
    return "" if value is None else str(value)


def _int_attr(source: object, name: str) -> int:
    value = _attr(source, name)
    if not isinstance(value, int):
        raise TaskSchedulerAdapterError(f"TASK_SCHEDULER_COM_{name.upper()}_INVALID")
    return value


def _bool_attr(source: object, name: str) -> bool:
    value = _attr(source, name)
    if not isinstance(value, bool):
        raise TaskSchedulerAdapterError(f"TASK_SCHEDULER_COM_{name.upper()}_INVALID")
    return value


def _is_not_found_error(exc: Exception) -> bool:
    for hresult in _hresult_candidates(exc):
        if hresult & 0xFFFFFFFF in _NOT_FOUND_HRESULTS:
            return True
    return False


def _hresult_candidates(exc: Exception) -> tuple[int, ...]:
    candidates: list[int] = []
    hresult = getattr(exc, "hresult", None)
    if not isinstance(hresult, int):
        hresult = None
    if hresult is not None:
        candidates.append(hresult)
    excepinfo = getattr(exc, "excepinfo", None)
    if isinstance(excepinfo, tuple) and len(excepinfo) >= 6 and isinstance(excepinfo[5], int):
        candidates.append(excepinfo[5])
    return tuple(candidates)
