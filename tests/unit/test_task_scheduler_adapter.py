from __future__ import annotations

import sys
import types
from dataclasses import replace

import pytest

from mediasync_home.adapters.task_scheduler import (
    Pywin32TaskSchedulerGateway,
    TaskSchedulerAdapterError,
    TaskSchedulerGatewayTask,
    WindowsTaskSchedulerRegistry,
)
from mediasync_home.adapters.windows_argv import (
    build_windows_argument_line,
    parse_windows_argument_line,
)
from mediasync_home.application.task_scheduler import (
    TaskSchedulerDefinition,
    TaskSchedulerReconciliationAction,
    bind_same_user_task_scheduler_definition_hash,
    build_same_user_task_scheduler_definition,
    classify_task_scheduler_reconciliation,
)
from mediasync_home.application.trigger_occurrences import TriggerKind


EXECUTABLE = r"C:\Program Files\MediaSync Home\MediaSyncHome.exe"


def test_windows_task_scheduler_registry_applies_com_shaped_task_registration() -> None:
    gateway = _Gateway()
    registry = WindowsTaskSchedulerRegistry(gateway)
    definition = _definition(arguments=("plain", "two words", "", r"trailing\\"))

    registry.apply_task_definition(definition)

    assert len(gateway.applied) == 1
    applied = gateway.applied[0]
    assert applied.task_path == r"\MediaSync Home\install-a\schedule-a"
    assert applied.folder_path == r"\MediaSync Home\install-a"
    assert applied.task_name == "schedule-a"
    assert applied.executable_path == EXECUTABLE
    assert parse_windows_argument_line(applied.argument_line) == definition.arguments
    assert applied.multiple_instances_policy == "PARALLEL"
    assert applied.execution_time_limit_seconds == 0
    assert applied.stop_on_execution_time_limit is False


def test_windows_task_scheduler_registry_loads_observed_definition_from_gateway() -> None:
    definition = _definition()
    registry = WindowsTaskSchedulerRegistry(
        _Gateway(_gateway_task(definition, configuration_json='{ "kind": "daily" }'))
    )

    observed = registry.load_task(definition.task_path)

    assert observed is not None
    assert observed.task_path == definition.task_path
    assert observed.executable_path == definition.executable_path
    assert observed.arguments == definition.arguments
    assert observed.configuration_json == '{ "kind": "daily" }'
    assert observed.task_logon_type == "INTERACTIVE_TOKEN"


def test_windows_task_scheduler_registry_lists_and_deletes_gateway_tasks() -> None:
    first = _definition()
    second = replace(first, task_path=r"\MediaSync Home\install-a\schedule-b")
    first_task = _gateway_task(first)
    second_task = replace(_gateway_task(second), task_name="schedule-b")
    gateway = _Gateway(second_task, first_task)
    registry = WindowsTaskSchedulerRegistry(gateway)

    listed = registry.list_tasks(r"\MediaSync Home\install-a", limit=1)
    registry.delete_task(first.task_path)

    assert tuple(task.task_path for task in listed) == (first.task_path,)
    assert gateway.deleted == (first.task_path,)
    assert gateway.load_task(first.task_path) is None


def test_windows_task_scheduler_registry_turns_unparseable_arguments_into_safe_drift() -> None:
    schedule = bind_same_user_task_scheduler_definition_hash(
        _schedule(),
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    registry = WindowsTaskSchedulerRegistry(
        _Gateway(replace(_gateway_task(definition), argument_line="bad\x00argument"))
    )

    plan = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=registry.load_task(definition.task_path),
    )

    assert plan.action is TaskSchedulerReconciliationAction.BLOCK_ARGUMENT_DRIFT
    assert plan.reason == "TASK_SCHEDULER_ARGUMENTS_NOT_RECOGNIZED"


def test_windows_task_scheduler_registry_rejects_invalid_task_path_before_apply() -> None:
    registry = WindowsTaskSchedulerRegistry(_Gateway())

    with pytest.raises(TaskSchedulerAdapterError, match="TASK_SCHEDULER_TASK_PATH_INVALID"):
        registry.apply_task_definition(replace(_definition(), task_path=r"MediaSync Home\a"))


def test_windows_task_scheduler_registry_rejects_overlong_action_before_apply() -> None:
    registry = WindowsTaskSchedulerRegistry(_Gateway())

    with pytest.raises(TaskSchedulerAdapterError, match="WINDOWS_COMMAND_LINE_TOO_LONG"):
        registry.apply_task_definition(
            replace(_definition(), arguments=("x" * 40000,))
        )


def test_pywin32_task_scheduler_gateway_registers_daily_interactive_task() -> None:
    service = _FakeTaskSchedulerService()
    gateway = Pywin32TaskSchedulerGateway(service_factory=_connected_factory(service))
    definition = _definition(
        arguments=("plain", "two words"),
    )
    task = replace(
        _gateway_task(
            definition,
            configuration_json='{"kind":"daily","hour":2,"minute":30,"days_interval":3}',
        ),
        requires_network=True,
    )

    gateway.apply_task(task)

    folder = service.GetFolder(r"\MediaSync Home\install-a")
    registered = folder.registered[0]
    task_definition = registered.definition
    trigger = task_definition.Triggers.Item(1)
    action = task_definition.Actions.Item(1)

    assert service.connected is True
    assert registered.task_name == "schedule-a"
    assert registered.flags == 0x6
    assert registered.user_id is None
    assert registered.password is None
    assert registered.logon_type == 3
    assert task_definition.RegistrationInfo.Author == "MediaSync Home"
    assert task_definition.Settings.Enabled is True
    assert task_definition.Settings.RunOnlyIfNetworkAvailable is True
    assert task_definition.Settings.MultipleInstances == 0
    assert task_definition.Settings.ExecutionTimeLimit == "PT0S"
    assert task_definition.Settings.AllowHardTerminate is False
    assert task_definition.Principal.LogonType == 3
    assert task_definition.Principal.RunLevel == 0
    assert trigger.Type == 2
    assert trigger.Enabled is True
    assert trigger.StartBoundary == "2000-01-01T02:30:00"
    assert trigger.DaysInterval == 3
    assert action.Type == 0
    assert action.Path == EXECUTABLE
    assert parse_windows_argument_line(action.Arguments) == definition.arguments


def test_pywin32_task_scheduler_gateway_wraps_custom_factory_in_requested_apartment() -> None:
    service = _FakeTaskSchedulerService()
    events: list[str] = []
    gateway = Pywin32TaskSchedulerGateway(
        service_factory=_connected_factory(service),
        com_apartment_factory=lambda: _RecordingComApartment(events),
    )
    definition = _definition()
    task = _gateway_task(definition)

    gateway.apply_task(task)
    observed = gateway.load_task(definition.task_path)

    assert observed == task
    assert events == ["enter", "exit", "enter", "exit"]


def test_pywin32_task_scheduler_gateway_default_factory_initializes_com_apartment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeTaskSchedulerService()
    events: list[str] = []
    pythoncom = types.ModuleType("pythoncom")
    win32com = types.ModuleType("win32com")
    win32com_client = types.ModuleType("win32com.client")

    def co_initialize() -> None:
        events.append("coinit")

    def co_uninitialize() -> None:
        events.append("couninit")

    def dispatch(progid: str) -> object:
        events.append(f"dispatch:{progid}")
        return service

    pythoncom.CoInitialize = co_initialize  # type: ignore[attr-defined]
    pythoncom.CoUninitialize = co_uninitialize  # type: ignore[attr-defined]
    win32com_client.Dispatch = dispatch  # type: ignore[attr-defined]
    win32com.client = win32com_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", win32com_client)

    gateway = Pywin32TaskSchedulerGateway()

    assert gateway.load_task(r"\MediaSync Home\install-a\missing") is None
    assert service.connected is True
    assert events == ["coinit", "dispatch:Schedule.Service", "couninit"]


def test_pywin32_task_scheduler_gateway_preserves_com_apartment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    pythoncom = types.ModuleType("pythoncom")

    def co_initialize() -> None:
        events.append("coinit")
        raise RuntimeError("COM init failed")

    def co_uninitialize() -> None:
        events.append("couninit")

    pythoncom.CoInitialize = co_initialize  # type: ignore[attr-defined]
    pythoncom.CoUninitialize = co_uninitialize  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)

    gateway = Pywin32TaskSchedulerGateway()

    with pytest.raises(TaskSchedulerAdapterError, match="TASK_SCHEDULER_COM_APARTMENT_FAILED"):
        gateway.load_task(r"\MediaSync Home\install-a\missing")
    assert events == ["coinit"]


def test_pywin32_task_scheduler_gateway_loads_registered_task_back() -> None:
    service = _FakeTaskSchedulerService()
    gateway = Pywin32TaskSchedulerGateway(service_factory=_connected_factory(service))
    definition = _definition()
    task = replace(
        _gateway_task(definition),
        execution_time_limit_seconds=3661,
        stop_on_execution_time_limit=True,
        multiple_instances_policy="QUEUE",
        requires_network=True,
    )
    gateway.apply_task(task)

    observed = gateway.load_task(definition.task_path)

    assert observed == task


def test_pywin32_task_scheduler_gateway_lists_and_deletes_tasks_idempotently() -> None:
    service = _FakeTaskSchedulerService()
    gateway = Pywin32TaskSchedulerGateway(service_factory=_connected_factory(service))
    first = _gateway_task(_definition())
    second = replace(
        _gateway_task(replace(_definition(), task_path=r"\MediaSync Home\install-a\schedule-b")),
        task_name="schedule-b",
    )
    gateway.apply_task(second)
    gateway.apply_task(first)

    first_page = gateway.list_tasks(r"\MediaSync Home\install-a", limit=1)
    second_page = gateway.list_tasks(
        r"\MediaSync Home\install-a",
        limit=2,
        after_task_name="schedule-a",
    )
    gateway.delete_task(first.task_path)
    gateway.delete_task(first.task_path)

    assert first_page == (first,)
    assert second_page == (second,)
    assert gateway.load_task(first.task_path) is None
    assert gateway.load_task(second.task_path) == second


def test_pywin32_task_scheduler_gateway_loads_missing_action_as_safe_drift() -> None:
    service = _FakeTaskSchedulerService()
    gateway = Pywin32TaskSchedulerGateway(service_factory=_connected_factory(service))
    registry = WindowsTaskSchedulerRegistry(gateway)
    schedule = bind_same_user_task_scheduler_definition_hash(
        _schedule(),
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    definition = build_same_user_task_scheduler_definition(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
    )
    task_definition = _FakeTaskDefinition()
    task_definition.Data = (
        '{"configuration_json":"{\\"kind\\":\\"daily\\"}",'
        '"schema_version":1,"time_zone_id":"Europe/Oslo",'
        '"trigger_type":"SCHEDULED_TIME"}'
    )
    service.root.CreateFolder("MediaSync Home").CreateFolder("install-a").RegisterTaskDefinition(
        "schedule-a",
        task_definition,
        0x6,
        None,
        None,
        3,
        None,
    )

    plan = classify_task_scheduler_reconciliation(
        schedule,
        installation_id="install-a",
        executable_path=EXECUTABLE,
        observed=registry.load_task(definition.task_path),
    )

    assert plan.action is TaskSchedulerReconciliationAction.BLOCK_ARGUMENT_DRIFT


def test_pywin32_task_scheduler_gateway_returns_none_for_missing_task() -> None:
    service = _FakeTaskSchedulerService()
    gateway = Pywin32TaskSchedulerGateway(service_factory=_connected_factory(service))

    assert gateway.load_task(r"\MediaSync Home\install-a\missing") is None


def test_pywin32_task_scheduler_gateway_rejects_unsupported_local_mvp_options() -> None:
    service = _FakeTaskSchedulerService()
    gateway = Pywin32TaskSchedulerGateway(service_factory=_connected_factory(service))

    with pytest.raises(TaskSchedulerAdapterError, match="TASK_SCHEDULER_LOGON_TYPE_UNSUPPORTED"):
        gateway.apply_task(replace(_gateway_task(_definition()), task_logon_type="PASSWORD"))
    with pytest.raises(
        TaskSchedulerAdapterError,
        match="TASK_SCHEDULER_TRIGGER_CONFIGURATION_UNSUPPORTED",
    ):
        gateway.apply_task(
            replace(
                _gateway_task(_definition(), configuration_json='{"kind":"weekly"}'),
            )
        )


class _Gateway:
    def __init__(self, *tasks: TaskSchedulerGatewayTask) -> None:
        self.tasks = {task.task_path: task for task in tasks}
        self.applied: list[TaskSchedulerGatewayTask] = []
        self.deleted: tuple[str, ...] = ()

    def load_task(self, task_path: str) -> TaskSchedulerGatewayTask | None:
        return self.tasks.get(task_path)

    def list_tasks(
        self,
        folder_path: str,
        *,
        limit: int,
        after_task_name: str | None = None,
    ) -> tuple[TaskSchedulerGatewayTask, ...]:
        tasks = sorted(
            (task for task in self.tasks.values() if task.folder_path == folder_path),
            key=lambda task: task.task_name,
        )
        if after_task_name is not None:
            tasks = [task for task in tasks if task.task_name > after_task_name]
        return tuple(tasks[:limit])

    def apply_task(self, task: TaskSchedulerGatewayTask) -> None:
        self.applied.append(task)
        self.tasks[task.task_path] = task

    def delete_task(self, task_path: str) -> None:
        self.deleted = (*self.deleted, task_path)
        self.tasks.pop(task_path, None)


class _RecordingComApartment:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> None:
        self._events.append("enter")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        del exc_type, exc, tb
        self._events.append("exit")
        return False


def _connected_factory(service: "_FakeTaskSchedulerService"):
    def factory() -> "_FakeTaskSchedulerService":
        service.Connect()
        return service

    return factory


class _ComNotFoundError(Exception):
    hresult = -2147352567
    excepinfo = (0, None, None, None, 0, -2147024894)


class _FakeTaskSchedulerService:
    def __init__(self) -> None:
        self.connected = False
        self.root = _FakeTaskFolder("\\")

    def Connect(self) -> None:
        self.connected = True

    def GetFolder(self, path: str) -> "_FakeTaskFolder":
        if path == "\\":
            return self.root
        folder = self.root
        for part in path.strip("\\").split("\\"):
            folder = folder.GetFolder(part)
        return folder

    def NewTask(self, flags: int) -> "_FakeTaskDefinition":
        assert flags == 0
        return _FakeTaskDefinition()


class _FakeTaskFolder:
    def __init__(self, path: str) -> None:
        self.path = path
        self.folders: dict[str, _FakeTaskFolder] = {}
        self.tasks: dict[str, _FakeRegisteredTask] = {}
        self.registered: list[_FakeRegisteredTask] = []

    def GetFolder(self, name: str) -> "_FakeTaskFolder":
        try:
            return self.folders[name]
        except KeyError as exc:
            raise _ComNotFoundError from exc

    def CreateFolder(self, name: str) -> "_FakeTaskFolder":
        child_path = "\\" + name if self.path == "\\" else f"{self.path}\\{name}"
        folder = _FakeTaskFolder(child_path)
        self.folders[name] = folder
        return folder

    def GetTask(self, name: str) -> "_FakeRegisteredTask":
        try:
            return self.tasks[name]
        except KeyError as exc:
            raise _ComNotFoundError from exc

    def GetTasks(self, flags: int) -> "_FakeRegisteredTaskCollection":
        assert flags == 0
        return _FakeRegisteredTaskCollection(tuple(self.tasks.values()))

    def RegisterTaskDefinition(
        self,
        task_name: str,
        definition: "_FakeTaskDefinition",
        flags: int,
        user_id: object,
        password: object,
        logon_type: int,
        sddl: object,
    ) -> "_FakeRegisteredTask":
        assert sddl is None
        task = _FakeRegisteredTask(
            task_name=task_name,
            definition=definition,
            flags=flags,
            user_id=user_id,
            password=password,
            logon_type=logon_type,
        )
        self.tasks[task_name] = task
        self.registered.append(task)
        return task

    def DeleteTask(self, task_name: str, flags: int) -> None:
        assert flags == 0
        try:
            del self.tasks[task_name]
        except KeyError as exc:
            raise _ComNotFoundError from exc


class _FakeRegisteredTask:
    def __init__(
        self,
        *,
        task_name: str,
        definition: "_FakeTaskDefinition",
        flags: int,
        user_id: object,
        password: object,
        logon_type: int,
    ) -> None:
        self.task_name = task_name
        self.Name = task_name
        self.Definition = definition
        self.definition = definition
        self.flags = flags
        self.user_id = user_id
        self.password = password
        self.logon_type = logon_type


class _FakeRegisteredTaskCollection:
    def __init__(self, tasks: tuple[_FakeRegisteredTask, ...]) -> None:
        self._tasks = tasks

    @property
    def Count(self) -> int:
        return len(self._tasks)

    def Item(self, index: int) -> "_FakeRegisteredTask":
        return self._tasks[index - 1]


class _FakeTaskDefinition:
    def __init__(self) -> None:
        self.Data = ""
        self.RegistrationInfo = _FakeRegistrationInfo()
        self.Settings = _FakeSettings()
        self.Principal = _FakePrincipal()
        self.Triggers = _FakeCollection(_FakeTrigger)
        self.Actions = _FakeCollection(_FakeAction)


class _FakeRegistrationInfo:
    def __init__(self) -> None:
        self.Author = ""
        self.Description = ""


class _FakeSettings:
    def __init__(self) -> None:
        self.Enabled = True
        self.StartWhenAvailable = False
        self.RunOnlyIfNetworkAvailable = False
        self.MultipleInstances = 2
        self.ExecutionTimeLimit = ""
        self.AllowDemandStart = False
        self.AllowHardTerminate = False
        self.DisallowStartIfOnBatteries = True
        self.StopIfGoingOnBatteries = True


class _FakePrincipal:
    def __init__(self) -> None:
        self.LogonType = 0
        self.RunLevel = 1


class _FakeTrigger:
    def __init__(self, trigger_type: int) -> None:
        self.Type = trigger_type
        self.Id = ""
        self.Enabled = False
        self.StartBoundary = ""
        self.DaysInterval = 0


class _FakeAction:
    def __init__(self, action_type: int) -> None:
        self.Type = action_type
        self.Path = ""
        self.Arguments = ""


class _FakeCollection:
    def __init__(self, factory) -> None:
        self._factory = factory
        self._items = []

    @property
    def Count(self) -> int:
        return len(self._items)

    def Create(self, item_type: int):
        item = self._factory(item_type)
        self._items.append(item)
        return item

    def Item(self, index: int):
        return self._items[index - 1]


def _definition(
    *,
    arguments: tuple[str, ...] = (
        "--enqueue-trigger-occurrence",
        "--installation-id",
        "install-a",
        "--schedule-id",
        "schedule-a",
        "--schedule-revision-hash",
        "a" * 64,
        "--trigger-kind",
        "SCHEDULED_TIME",
        "--task-definition-hash",
        "a" * 64,
    ),
) -> TaskSchedulerDefinition:
    return TaskSchedulerDefinition(
        task_path=r"\MediaSync Home\install-a\schedule-a",
        executable_path=EXECUTABLE,
        arguments=arguments,
        definition_hash="a" * 64,
        enabled=True,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        time_zone_id="Europe/Oslo",
        task_logon_type="INTERACTIVE_TOKEN",
        run_only_when_logged_on=True,
        requires_network=False,
    )


def _gateway_task(
    definition: TaskSchedulerDefinition,
    *,
    configuration_json: str | None = None,
) -> TaskSchedulerGatewayTask:
    return TaskSchedulerGatewayTask(
        task_path=definition.task_path,
        folder_path=r"\MediaSync Home\install-a",
        task_name="schedule-a",
        executable_path=definition.executable_path,
        argument_line=build_windows_argument_line(definition.arguments),
        enabled=definition.enabled,
        trigger_type=definition.trigger_type,
        configuration_json=configuration_json or definition.configuration_json,
        time_zone_id=definition.time_zone_id,
        task_logon_type=definition.task_logon_type,
        run_only_when_logged_on=definition.run_only_when_logged_on,
        requires_network=definition.requires_network,
        multiple_instances_policy=definition.multiple_instances_policy,
        execution_time_limit_seconds=definition.execution_time_limit_seconds,
        stop_on_execution_time_limit=definition.stop_on_execution_time_limit,
    )


def _schedule() -> object:
    from mediasync_home.application.schedules import ScheduleDefinition

    return ScheduleDefinition(
        schedule_id="schedule-a",
        job_id="job-a",
        plan_id="plan-a",
        plan_checksum="a" * 64,
        trigger_type=TriggerKind.SCHEDULED_TIME,
        configuration_json='{"kind":"daily"}',
        definition_generation=1,
        desired_definition_hash="0" * 64,
        time_zone_id="Europe/Oslo",
        dst_policy="PRESERVE_WALL_TIME",
        misfire_policy="QUEUE_ONCE",
        coalescing_window_seconds=60,
        task_logon_type="INTERACTIVE_TOKEN",
        requires_network=False,
        run_only_when_logged_on=True,
        enabled=True,
        row_version=1,
    )
