from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from mediasync_home.presentation.main_window import MediaSyncWindow
from mediasync_home.presentation.theme.theme_manager import ThemeManager, ThemeMode
from mediasync_home.presentation.view_models.engine_status import (
    EngineStatusProvider,
    EngineStatusViewState,
    load_engine_status,
)


def ensure_qapplication(argv: Sequence[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(list(argv or []))
    app.setApplicationName("MediaSync Home")
    app.setOrganizationName("MediaSync Home")
    return app


def build_main_window(
    *,
    initial_state: EngineStatusViewState | None = None,
    engine_client: EngineStatusProvider | None = None,
    theme_mode: ThemeMode = ThemeMode.SYSTEM,
    show_component_gallery: bool | None = None,
) -> MediaSyncWindow:
    app = ensure_qapplication([])
    ThemeManager(app).apply(theme_mode)
    state = initial_state or load_engine_status(engine_client)
    return MediaSyncWindow(
        initial_state=state,
        engine_client=engine_client,
        show_component_gallery=show_component_gallery,
    )


def run_gui(
    argv: Sequence[str] | None = None,
    *,
    engine_client: EngineStatusProvider | None = None,
    theme_mode: ThemeMode = ThemeMode.SYSTEM,
    show_component_gallery: bool | None = None,
) -> int:
    app = ensure_qapplication(argv)
    ThemeManager(app).apply(theme_mode)
    window = MediaSyncWindow(
        initial_state=load_engine_status(engine_client),
        engine_client=engine_client,
        show_component_gallery=show_component_gallery,
    )
    window.show()
    return app.exec()
