from __future__ import annotations

from mediasync_home.presentation.theme.tokens import ThemeTokens


def build_qss(tokens: ThemeTokens) -> str:
    return f"""
* {{
    font-family: "Segoe UI", "Arial";
    font-size: {tokens.font_size_body}px;
    color: {tokens.text};
}}

QMainWindow#mediaSyncWindow {{
    background: {tokens.window_background};
}}

QWidget#appRoot {{
    background: {tokens.window_background};
}}

QFrame#actionBar {{
    background: {tokens.surface};
    border-bottom: {tokens.border_hairline}px solid {tokens.border};
}}

QLabel#productTitle {{
    font-size: {tokens.font_size_title}px;
    font-weight: 600;
}}

QLabel#sectionTitle {{
    font-size: {tokens.font_size_title}px;
    font-weight: 600;
}}

QLabel#mutedLabel,
QLabel#activityEmptyLabel,
QLabel#engineDetailLabel {{
    color: {tokens.text_muted};
}}

QLabel#engineStatusChip {{
    border: {tokens.border_hairline}px solid {tokens.border};
    border-radius: {tokens.radius_sm}px;
    padding: {tokens.space_1}px {tokens.space_3}px;
    background: {tokens.surface_alt};
    color: {tokens.text};
}}

QLabel#engineStatusChip[statusKind="ready"] {{
    background: {tokens.success_surface};
    border-color: {tokens.success};
    color: {tokens.success};
}}

QLabel#engineStatusChip[statusKind="warning"] {{
    background: {tokens.warning_surface};
    border-color: {tokens.warning};
    color: {tokens.warning};
}}

QLabel#engineStatusChip[statusKind="blocked"] {{
    background: {tokens.danger_surface};
    border-color: {tokens.danger};
    color: {tokens.danger};
}}

QListWidget#navigationRail {{
    background: {tokens.surface};
    border: 0;
    border-right: {tokens.border_hairline}px solid {tokens.border};
    outline: 0;
}}

QListWidget#navigationRail::item {{
    min-height: 36px;
    padding: 0 {tokens.space_4}px;
    border-left: {tokens.border_focus}px solid transparent;
}}

QListWidget#navigationRail::item:selected {{
    background: {tokens.selection};
    border-left-color: {tokens.accent};
    color: {tokens.text};
}}

QFrame#workspace,
QWidget#workspace {{
    background: {tokens.window_background};
}}

QFrame#engineStatusPanel,
QFrame#standardBackupPanel,
QFrame#backupJobDetailPanel,
QFrame#componentGallery {{
    background: {tokens.panel};
    border: {tokens.border_hairline}px solid {tokens.border};
    border-radius: {tokens.radius_lg}px;
}}

QLabel#setupStepLabel {{
    border: {tokens.border_hairline}px solid {tokens.border};
    border-radius: {tokens.radius_sm}px;
    padding: {tokens.space_2}px {tokens.space_3}px;
    color: {tokens.text_muted};
    background: {tokens.surface_alt};
}}

QLabel#setupStepLabel[stepState="current"] {{
    border-color: {tokens.accent};
    color: {tokens.text};
    background: {tokens.selection};
}}

QLabel#setupStepLabel[stepState="complete"] {{
    border-color: {tokens.success};
    color: {tokens.success};
    background: {tokens.success_surface};
}}

QLabel#activityStatusTitle {{
    font-weight: 600;
}}

QLabel#jobDetailTitle {{
    font-weight: 600;
}}

QLabel#planPreviewTitle {{
    font-weight: 600;
}}

QLabel#activityDimensionLabel {{
    color: {tokens.text_muted};
}}

QLabel#jobDetailTargetRow {{
    color: {tokens.text_muted};
}}

QLabel#planPreviewSummary,
QLabel#planPreviewRow {{
    color: {tokens.text_muted};
}}

QFrame#activityBar {{
    background: {tokens.surface};
    border-left: {tokens.border_hairline}px solid {tokens.border};
}}

QPushButton#refreshEngineButton {{
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    border-radius: {tokens.radius_sm}px;
    border: {tokens.border_hairline}px solid {tokens.border};
    background: {tokens.surface_alt};
}}

QToolButton#languageSelectorButton {{
    min-width: 36px;
    max-width: 36px;
    min-height: 32px;
    max-height: 32px;
    border-radius: {tokens.radius_sm}px;
    border: {tokens.border_hairline}px solid {tokens.border};
    background: {tokens.surface_alt};
    padding: 0;
}}

QToolButton#languageSelectorButton::menu-indicator {{
    image: none;
    width: 0px;
}}

QToolButton#languageSelectorButton:hover {{
    border-color: {tokens.accent};
}}

QToolButton#languageSelectorButton:focus {{
    border: {tokens.border_focus}px solid {tokens.focus};
}}

QMenu#languageSelectorMenu {{
    background: {tokens.surface};
    border: {tokens.border_hairline}px solid {tokens.border};
}}

QMenu#languageSelectorMenu::item {{
    min-height: 28px;
    padding: {tokens.space_2}px {tokens.space_6}px {tokens.space_2}px {tokens.space_3}px;
}}

QMenu#languageSelectorMenu::item:selected {{
    background: {tokens.selection};
}}

QPushButton#createBackupButton {{
    min-height: 32px;
    border-radius: {tokens.radius_sm}px;
    border: {tokens.border_hairline}px solid {tokens.accent};
    background: {tokens.accent};
    color: {tokens.accent_text};
    padding: 0 {tokens.space_4}px;
}}

QPushButton#createBackupButton:disabled {{
    border-color: {tokens.border};
    background: {tokens.surface_alt};
    color: {tokens.text_muted};
}}

QPushButton#refreshEngineButton:hover {{
    border-color: {tokens.accent};
}}

QPushButton#refreshEngineButton:focus {{
    border: {tokens.border_focus}px solid {tokens.focus};
}}

QPushButton#refreshEngineButton:disabled {{
    color: {tokens.text_muted};
    background: {tokens.surface};
}}
"""
