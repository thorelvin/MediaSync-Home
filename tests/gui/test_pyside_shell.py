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
