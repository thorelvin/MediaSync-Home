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

QLabel#sectionTitle,
QLabel#workspaceHeading {{
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

QListWidget#jobsList {{
    background: {tokens.surface};
    border: {tokens.border_hairline}px solid {tokens.border};
    border-radius: {tokens.radius_sm}px;
    outline: 0;
}}

QListWidget#jobsList::item {{
    min-height: 50px;
    padding: {tokens.space_2}px {tokens.space_3}px;
    border-bottom: {tokens.border_hairline}px solid {tokens.border};
}}

QListWidget#jobsList::item:selected {{
    background: {tokens.selection};
    color: {tokens.text};
}}

QFrame#workspace,
QWidget#workspace {{
    background: {tokens.window_background};
}}

QFrame#engineStatusPanel,
QFrame#standardBackupPanel,
QFrame#backupJobDetailPanel,
QFrame#jobsDetailPanel,
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

QLabel#planPreviewTitle,
QLabel#planEndpointTitle,
QLabel#snapshotHealthTitle {{
    font-weight: 600;
}}

QLabel#activityDimensionLabel {{
    color: {tokens.text_muted};
}}

QLabel#jobDetailTargetRow {{
    color: {tokens.text_muted};
}}

QLabel#planPreviewSummary,
QLabel#planPreviewRow,
QLabel#planEndpointSummary,
QLabel#planEndpointRow,
QLabel#snapshotHealthSummary,
QLabel#snapshotHealthRow {{
    color: {tokens.text_muted};
}}

QFrame#activityBar {{
    background: {tokens.surface};
    border-left: {tokens.border_hairline}px solid {tokens.border};
}}

QScrollArea#dashboardScrollArea,
QScrollArea#jobsScrollArea,
QScrollArea#activityScrollArea,
QWidget#activityContent {{
    background: transparent;
    border: none;
}}

QScrollArea#dashboardScrollArea > QWidget > QWidget,
QScrollArea#jobsScrollArea > QWidget > QWidget {{
    background: {tokens.window_background};
}}

QScrollArea#activityScrollArea > QWidget > QWidget,
QWidget#activityContent {{
    background: {tokens.surface};
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

QToolButton#addTargetButton,
QToolButton#removeTargetButton,
QToolButton#setupBackButton,
QToolButton#jobsPreviousButton,
QToolButton#jobsNextButton {{
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    border-radius: {tokens.radius_sm}px;
    border: {tokens.border_hairline}px solid {tokens.border};
    background: {tokens.surface_alt};
    padding: 0;
}}

QToolButton#addTargetButton:hover,
QToolButton#removeTargetButton:hover,
QToolButton#setupBackButton:hover,
QToolButton#jobsPreviousButton:hover,
QToolButton#jobsNextButton:hover {{
    border-color: {tokens.accent};
}}

QToolButton#addTargetButton:focus,
QToolButton#removeTargetButton:focus,
QToolButton#setupBackButton:focus,
QToolButton#jobsPreviousButton:focus,
QToolButton#jobsNextButton:focus {{
    border: {tokens.border_focus}px solid {tokens.focus};
}}

QToolButton#addTargetButton:disabled,
QToolButton#removeTargetButton:disabled,
QToolButton#setupBackButton:disabled,
QToolButton#jobsPreviousButton:disabled,
QToolButton#jobsNextButton:disabled {{
    background: {tokens.surface};
    border-color: {tokens.border};
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

QPushButton#createBackupButton,
QPushButton#startBackupButton,
QPushButton#jobsStartBackupButton {{
    min-height: 32px;
    border-radius: {tokens.radius_sm}px;
    border: {tokens.border_hairline}px solid {tokens.accent};
    background: {tokens.accent};
    color: {tokens.accent_text};
    padding: 0 {tokens.space_4}px;
}}

QPushButton#createBackupButton:hover,
QPushButton#startBackupButton:hover,
QPushButton#jobsStartBackupButton:hover {{
    border: {tokens.border_focus}px solid {tokens.focus};
}}

QPushButton#createBackupButton:pressed,
QPushButton#startBackupButton:pressed,
QPushButton#jobsStartBackupButton:pressed {{
    border-color: {tokens.focus};
    background: {tokens.focus};
}}

QPushButton#createBackupButton:focus,
QPushButton#startBackupButton:focus,
QPushButton#jobsStartBackupButton:focus {{
    border: {tokens.border_focus}px solid {tokens.focus};
}}

QPushButton#createBackupButton:disabled,
QPushButton#startBackupButton:disabled,
QPushButton#jobsStartBackupButton:disabled {{
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
