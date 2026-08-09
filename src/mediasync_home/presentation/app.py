from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from mediasync_home.application.user_preferences import (
    AppearancePreference,
    DensityPreference,
    UserPreferences,
    UserPreferencesStore,
    load_user_preferences,
)
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
        return cast(QApplication, existing)

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
    engine_client_factory: Callable[[], EngineStatusProvider | None] | None = None,
    theme_mode: ThemeMode = ThemeMode.SYSTEM,
    user_preferences: UserPreferences | None = None,
    user_preferences_store: UserPreferencesStore | None = None,
    data_root: Path | None = None,
    open_data_folder: Callable[[Path], bool] | None = None,
    open_local_path: Callable[[Path], bool] | None = None,
    show_component_gallery: bool | None = None,
) -> MediaSyncWindow:
    app = ensure_qapplication([])
    preferences = _launch_preferences(
        theme_mode=theme_mode,
        user_preferences=user_preferences,
        store=user_preferences_store,
    )
    theme_manager = ThemeManager(app)
    _apply_preferences_theme(theme_manager, preferences.appearance, preferences.density)
    state = initial_state or load_engine_status(engine_client)
    return MediaSyncWindow(
        initial_state=state,
        engine_client=engine_client,
        engine_client_factory=engine_client_factory,
        user_preferences=preferences,
        user_preferences_store=user_preferences_store,
        apply_appearance=lambda appearance, density: _apply_preferences_theme(
            theme_manager,
            appearance,
            density,
        ),
        data_root=data_root,
        open_data_folder=open_data_folder or _open_local_folder,
        open_local_path=open_local_path,
        show_component_gallery=show_component_gallery,
    )


def run_gui(
    argv: Sequence[str] | None = None,
    *,
    engine_client: EngineStatusProvider | None = None,
    engine_client_factory: Callable[[], EngineStatusProvider | None] | None = None,
    theme_mode: ThemeMode = ThemeMode.SYSTEM,
    user_preferences: UserPreferences | None = None,
    user_preferences_store: UserPreferencesStore | None = None,
    data_root: Path | None = None,
    show_component_gallery: bool | None = None,
) -> int:
    app = ensure_qapplication(argv)
    preferences = _launch_preferences(
        theme_mode=theme_mode,
        user_preferences=user_preferences,
        store=user_preferences_store,
    )
    theme_manager = ThemeManager(app)
    _apply_preferences_theme(theme_manager, preferences.appearance, preferences.density)
    background_startup = engine_client_factory is not None and engine_client is not None
    window = MediaSyncWindow(
        initial_state=(
            EngineStatusViewState.disconnected()
            if background_startup
            else load_engine_status(engine_client)
        ),
        engine_client=engine_client,
        engine_client_factory=engine_client_factory,
        user_preferences=preferences,
        user_preferences_store=user_preferences_store,
        apply_appearance=lambda appearance, density: _apply_preferences_theme(
            theme_manager,
            appearance,
            density,
        ),
        data_root=data_root,
        open_data_folder=_open_local_folder,
        show_component_gallery=show_component_gallery,
    )
    window.show()
    if background_startup:
        QTimer.singleShot(0, window.refresh_engine_status)
    return app.exec()


def _launch_preferences(
    *,
    theme_mode: ThemeMode,
    user_preferences: UserPreferences | None,
    store: UserPreferencesStore | None,
) -> UserPreferences:
    if user_preferences is not None:
        return user_preferences
    return load_user_preferences(
        store,
        fallback=UserPreferences(
            appearance=AppearancePreference(theme_mode.value),
        ),
    )


def _apply_preferences_theme(
    theme_manager: ThemeManager,
    appearance: AppearancePreference,
    density: DensityPreference,
) -> None:
    theme_manager.apply(ThemeMode(appearance.value), density=density)


def _open_local_folder(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def show_startup_error(reason_code: str) -> int:
    app = ensure_qapplication([])
    messages = {
        "DESKTOP_ENGINE_HOST_EXITED": (
            "The local backup engine stopped before MediaSync Home could connect."
        ),
        "DESKTOP_ENGINE_HOST_INCOMPATIBLE": (
            "A different or incompatible MediaSync Home engine is already running."
        ),
        "DESKTOP_ENGINE_HOST_START_TIMEOUT": (
            "The local backup engine did not become ready in time."
        ),
        "DESKTOP_ENGINE_HOST_START_FAILED": (
            "Windows could not start the local backup engine."
        ),
    }
    detail = messages.get(reason_code, "MediaSync Home could not start its local backup engine.")
    QMessageBox.critical(
        None,
        "MediaSync Home",
        f"{detail}\n\nRestart MediaSync Home. If this continues, open diagnostics.",
    )
    app.processEvents()
    return 2
