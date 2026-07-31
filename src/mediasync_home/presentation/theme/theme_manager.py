from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from mediasync_home.application.user_preferences import DensityPreference
from mediasync_home.presentation.theme.qss_builder import build_qss
from mediasync_home.presentation.theme.tokens import DARK_TOKENS, LIGHT_TOKENS, ThemeTokens


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


@dataclass
class ThemeManager:
    app: QApplication
    mode: ThemeMode = ThemeMode.SYSTEM

    def apply(
        self,
        mode: ThemeMode | None = None,
        *,
        density: DensityPreference = DensityPreference.COMFORTABLE,
    ) -> ThemeTokens:
        if mode is not None:
            self.mode = mode
        tokens = resolve_tokens(self.mode, self.app)
        self.app.setStyleSheet(
            build_qss(tokens, compact=density is DensityPreference.COMPACT)
        )
        return tokens


def resolve_tokens(mode: ThemeMode, app: QApplication | None = None) -> ThemeTokens:
    if mode == ThemeMode.LIGHT:
        return LIGHT_TOKENS
    if mode == ThemeMode.DARK:
        return DARK_TOKENS
    if app is not None:
        window_color = app.palette().color(QPalette.ColorRole.Window)
        if window_color.lightness() < 128:
            return DARK_TOKENS
    return LIGHT_TOKENS
