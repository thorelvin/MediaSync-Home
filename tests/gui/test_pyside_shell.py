from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel, QListWidget, QPushButton, QToolButton, QWidget  # noqa: E402

from mediasync_home.application.runtime_status import startup_status  # noqa: E402
from mediasync_home.domain.process_roles import ProcessRole  # noqa: E402
from mediasync_home.ipc.protocol import IpcResponse  # noqa: E402
from mediasync_home.presentation.app import build_main_window, ensure_qapplication  # noqa: E402
from mediasync_home.presentation.theme.icon_registry import IconRegistry  # noqa: E402
from mediasync_home.presentation.theme.theme_manager import ThemeManager, ThemeMode  # noqa: E402
from mediasync_home.presentation.view_models.engine_status import (  # noqa: E402
    EngineStatusViewState,
    engine_status_from_response,
)


@pytest.fixture
def qapp():
    app = ensure_qapplication([])
    yield app
    app.processEvents()


def test_theme_manager_applies_generated_qss(qapp) -> None:
    tokens = ThemeManager(qapp).apply(ThemeMode.DARK)

    assert tokens.name == "dark"
    assert "QMainWindow#mediaSyncWindow" in qapp.styleSheet()


def test_icon_registry_returns_semantic_icons() -> None:
    registry = IconRegistry()

    assert not registry.icon("refresh").isNull()
    assert not registry.icon("status-ready").isNull()
    with pytest.raises(KeyError):
        registry.icon("unknown")


def test_main_window_displays_engine_status(qapp) -> None:
    state = _ready_state()
    window = build_main_window(initial_state=state, theme_mode=ThemeMode.LIGHT)

    try:
        assert window.objectName() == "mediaSyncWindow"
        nav = window.findChild(QListWidget, "navigationRail")
        chip = window.findChild(QLabel, "engineStatusChip")
        refresh = window.findChild(QPushButton, "refreshEngineButton")
        language = window.findChild(QToolButton, "languageSelectorButton")
        setup_panel = window.findChild(QWidget, "standardBackupPanel")
        setup_steps = window.findChildren(QLabel, "setupStepLabel")
        create_backup = window.findChild(QPushButton, "createBackupButton")
        detail_panel = window.findChild(QWidget, "backupJobDetailPanel")
        detail_title = window.findChild(QLabel, "jobDetailTitle")
        plan_preview_title = window.findChild(QLabel, "planPreviewTitle")

        assert nav is not None
        assert nav.count() == 4
        assert chip is not None
        assert chip.text() == "Connected: Ready"
        assert chip.property("statusKind") == "ready"
        assert refresh is not None
        assert refresh.isEnabled() is False
        assert language is not None
        assert language.text() == ""
        assert not language.icon().isNull()
        assert language.toolTip() == "Language: Norsk"
        assert language.menu() is not None
        action_bar = window.findChild(QWidget, "actionBar")
        assert action_bar is not None
        assert action_bar.layout() is not None
        assert action_bar.layout().itemAt(action_bar.layout().count() - 1).widget() is language
        assert [action.text() for action in language.menu().actions()] == [
            "Norsk",
            "English",
        ]
        assert [action.isChecked() for action in language.menu().actions()] == [
            True,
            False,
        ]
        assert all(not action.icon().isNull() for action in language.menu().actions())
        assert setup_panel is not None
        assert [step.text() for step in setup_steps] == [
            "1. Hva vil du beskytte?",
            "2. Hvor vil du ha kopier?",
            "3. Hvordan skal backupen fungere?",
            "4. Kontroller og opprett",
        ]
        assert setup_steps[0].property("stepState") == "current"
        assert create_backup is not None
        assert create_backup.text() == "Fortsett"
        assert create_backup.isEnabled() is False
        assert detail_panel is not None
        assert detail_title is not None
        assert detail_title.text() == "Ingen lagret backupjobb"
        assert plan_preview_title is not None
        assert plan_preview_title.text() == "Plan preview"
    finally:
        window.close()
        window.deleteLater()


def test_language_selector_updates_selected_flag(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.LIGHT)

    try:
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert language is not None
        assert language.menu() is not None
        language.menu().actions()[1].trigger()

        assert language.text() == ""
        assert not language.icon().isNull()
        assert language.toolTip() == "Language: English"
        assert [action.isChecked() for action in language.menu().actions()] == [
            False,
            True,
        ]
    finally:
        window.close()
        window.deleteLater()


def test_main_window_refreshes_through_engine_client(qapp) -> None:
    provider = _FakeEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.refresh_engine_status()
        chip = window.findChild(QLabel, "engineStatusChip")

        assert provider.calls == ["connect", "get_status"]
        assert chip is not None
        assert chip.text() == "Connected: Ready"
    finally:
        window.close()
        window.deleteLater()


def test_main_window_refreshes_backup_overview_when_provider_supports_it(qapp) -> None:
    provider = _FakeDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.refresh_engine_status()
        source = window.findChild(QLabel, "setupSourceValue")
        target = window.findChild(QLabel, "setupTargetValue")
        create_backup = window.findChild(QPushButton, "createBackupButton")
        activity_title = window.findChild(QLabel, "activityStatusTitle")
        activity_rows = window.findChildren(QLabel, "activityDimensionLabel")
        job_detail_title = window.findChild(QLabel, "jobDetailTitle")
        job_detail_source = window.findChild(QLabel, "jobDetailSourceValue")
        job_detail_targets = window.findChild(QLabel, "jobDetailTargetsValue")
        job_detail_defaults = window.findChild(QLabel, "jobDetailDefaultsValue")
        job_detail_revision = window.findChild(QLabel, "jobDetailRevisionValue")
        job_detail_rows = window.findChildren(QLabel, "jobDetailTargetRow")
        plan_preview_summary = window.findChild(QLabel, "planPreviewSummary")
        plan_preview_rows = window.findChildren(QLabel, "planPreviewRow")

        assert provider.calls == [
            "connect",
            "get_status",
            "get_backup_overview",
            "get_backup_job_detail",
            "get_activity_overview",
            "get_plan_operations",
        ]
        assert source is not None
        assert source.text() == "C:/Users/Ada/Pictures"
        assert target is not None
        assert target.text() == "1 mål: USB 1"
        assert create_backup is not None
        assert create_backup.isEnabled() is True
        assert job_detail_title is not None
        assert job_detail_title.text() == "Pictures"
        assert job_detail_source is not None
        assert job_detail_source.text() == "C:/Users/Ada/Pictures"
        assert job_detail_targets is not None
        assert job_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert job_detail_defaults is not None
        assert job_detail_defaults.text() == "Oppdater backup - Alle brukerfiler - Standard kontroll"
        assert job_detail_revision is not None
        assert job_detail_revision.text() == "Revisjon: job-rev-a - Filter: filter-a"
        assert job_detail_rows[0].text() == "USB 1: E:/Backup"
        assert plan_preview_summary is not None
        assert plan_preview_summary.text() == "2 operations from plan-a."
        assert plan_preview_rows[0].text() == "Low: Create folder: Photos"
        assert plan_preview_rows[1].text() == "Low: Copy new: Photos/2026/a.jpg - 2.0 KiB"
        assert activity_title is not None
        assert activity_title.text() == "Siste kjøring: run-a"
        assert activity_rows[0].text() == "Aktivitet: Kontrollerer"
        assert activity_rows[1].text() == "Oppmerksomhet: Venter"
    finally:
        window.close()
        window.deleteLater()


def test_component_gallery_is_development_only(qapp) -> None:
    window = build_main_window(
        initial_state=_ready_state(),
        show_component_gallery=True,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        assert window.findChild(QWidget, "componentGallery") is not None
    finally:
        window.close()
        window.deleteLater()


def _ready_state() -> EngineStatusViewState:
    return engine_status_from_response(
        IpcResponse.accepted({"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()})
    )


class _FakeEngineClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def connect(self) -> IpcResponse:
        self.calls.append("connect")
        return IpcResponse.accepted(
            {"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()}
        )

    def get_status(self) -> IpcResponse:
        self.calls.append("get_status")
        return IpcResponse.accepted(
            {"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()}
        )


class _FakeDashboardEngineClient(_FakeEngineClient):
    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del draft_id, limit, offset
        self.calls.append("get_backup_overview")
        return IpcResponse.accepted(
            {
                "backup_overview": {
                    "read_model_available": True,
                    "has_more": False,
                    "draft": {
                        "draft_id": "draft-a",
                        "source_name": "Pictures",
                        "source_path_label": "C:/Users/Ada/Pictures",
                        "targets": [
                            {
                                "name": "USB 1",
                                "path_label": "E:/Backup",
                                "independent_device_id": "disk-a",
                            }
                        ],
                    },
                    "jobs": [
                        {
                            "job_id": "job-a",
                            "title": "Pictures",
                            "source_name": "Pictures",
                            "source_path_label": "C:/Users/Ada/Pictures",
                            "targets": [
                                {
                                    "name": "USB 1",
                                    "path_label": "E:/Backup",
                                    "independent_device_id": "disk-a",
                                }
                            ],
                        }
                    ],
                }
            }
        )

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        self.calls.append("get_backup_job_detail")
        return IpcResponse.accepted(
            {
                "backup_job_detail": {
                    "job_id": job_id,
                    "read_model_available": True,
                    "found": True,
                    "job": {
                        "job_id": "job-a",
                        "job_revision_id": "job-rev-a",
                        "filter_set_id": "filter-a",
                        "title": "Pictures",
                        "source_name": "Pictures",
                        "source_path_label": "C:/Users/Ada/Pictures",
                        "configured_target_count": 1,
                        "independent_device_count": 1,
                        "defaults": {
                            "behavior": "UPDATE_BACKUP",
                            "file_selection": "ALL_USER_FILES",
                            "verification": "STANDARD",
                            "retention": "THIRTY_DAYS",
                            "extra_files": "KEEP_ON_TARGET",
                            "performance": "AUTO",
                        },
                        "targets": [
                            {
                                "name": "USB 1",
                                "path_label": "E:/Backup",
                                "independent_device_id": "disk-a",
                            }
                        ],
                    },
                }
            }
        )

    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del job_id, limit, offset
        self.calls.append("get_activity_overview")
        return IpcResponse.accepted(
            {
                "activity_overview": {
                    "read_model_available": True,
                    "has_more": False,
                    "runs": [
                        {
                            "run_id": "run-a",
                            "job_id": "job-a",
                            "job_revision_id": "job-rev-a",
                            "plan_id": "plan-a",
                            "state": "PREFLIGHT",
                            "trigger_type": "MANUAL_LOCAL_PREVIEW",
                            "started_utc": "2026-07-20T12:00:00.000Z",
                            "finished_utc": None,
                            "planned_operations": 1,
                            "planned_bytes": 128,
                            "warning_count": 0,
                            "error_count": 0,
                            "targets": [
                                {
                                    "run_target_id": "run-a-target-0000",
                                    "endpoint_id": "target-a",
                                    "endpoint_revision_id": "target-rev-a",
                                    "state": "REVALIDATING",
                                    "planned_operations": 1,
                                    "completed_operations": 0,
                                    "planned_bytes": 128,
                                    "completed_bytes": 0,
                                    "warning_count": 0,
                                    "error_count": 0,
                                }
                            ],
                        }
                    ],
                }
            }
        )

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        del limit, after
        self.calls.append("get_plan_operations")
        return IpcResponse.accepted(
            {
                "plan_operations": {
                    "plan_id": plan_id,
                    "limit": 3,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "operations": [
                        {
                            "operation_id": "op-a",
                            "operation_type": "CREATE_DIRECTORY",
                            "sequence_no": 0,
                            "execution_phase": 10,
                            "stable_order_key": "photos",
                            "target_precondition_kind": "ABSENT",
                            "reason_code": "TARGET_DIRECTORY_MISSING",
                            "risk_level": "LOW",
                            "target_relative_path": "Photos",
                            "planned_bytes": 0,
                        },
                        {
                            "operation_id": "op-b",
                            "operation_type": "COPY_NEW",
                            "sequence_no": 1,
                            "execution_phase": 20,
                            "stable_order_key": "photos/2026",
                            "target_precondition_kind": "ABSENT",
                            "reason_code": "SOURCE_ONLY",
                            "risk_level": "LOW",
                            "target_relative_path": "Photos/2026/a.jpg",
                            "planned_bytes": 2048,
                        },
                    ],
                }
            }
        )
