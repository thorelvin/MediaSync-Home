from __future__ import annotations

import os
from typing import Protocol, cast

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mediasync_home.presentation.theme.icon_registry import IconRegistry
from mediasync_home.presentation.view_models.backup_setup import (
    BackupOverviewViewState,
    BackupSetupDraft,
    BackupSetupStepViewState,
    BackupJobDetailViewState,
    BackupJobStatusViewState,
    StandardBackupSetupViewState,
    activity_overview_from_response,
    backup_job_detail_from_response,
    backup_overview_from_response,
    build_standard_backup_setup_state,
    empty_backup_job_detail_state,
    empty_backup_job_status_state,
)
from mediasync_home.presentation.view_models.engine_status import (
    EngineStatusProvider,
    EngineStatusViewState,
    engine_status_from_response,
)
from mediasync_home.ipc.protocol import IpcResponse


class BackupOverviewProvider(Protocol):
    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...


class ActivityOverviewProvider(Protocol):
    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...


class BackupJobDetailProvider(Protocol):
    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse: ...


class MediaSyncWindow(QMainWindow):
    def __init__(
        self,
        *,
        initial_state: EngineStatusViewState,
        engine_client: EngineStatusProvider | None = None,
        show_component_gallery: bool | None = None,
    ) -> None:
        super().__init__()
        self._engine_client = engine_client
        self._icons = IconRegistry()
        self._connected = False
        self._setup_state = build_standard_backup_setup_state(BackupSetupDraft.empty())
        self._job_status_state = empty_backup_job_status_state()
        self._job_detail_state = empty_backup_job_detail_state()
        self._setup_step_labels: list[QLabel] = []
        self._setup_source_value: QLabel | None = None
        self._setup_target_value: QLabel | None = None
        self._setup_defaults_value: QLabel | None = None
        self._setup_retention_value: QLabel | None = None
        self._setup_primary_button: QPushButton | None = None
        self._job_detail_title: QLabel | None = None
        self._job_detail_source_value: QLabel | None = None
        self._job_detail_revision_value: QLabel | None = None
        self._job_detail_targets_value: QLabel | None = None
        self._job_detail_defaults_value: QLabel | None = None
        self._job_detail_target_rows: list[QLabel] = []
        self._activity_status_title: QLabel | None = None
        self._activity_dimension_rows: list[QLabel] = []
        self._language_options = (
            ("nb", "Norsk"),
            ("en", "English"),
        )
        self._selected_language_code = "nb"
        self._language_actions: dict[str, QAction] = {}
        self._show_component_gallery = (
            os.environ.get("MEDIASYNC_DEV_COMPONENT_GALLERY") == "1"
            if show_component_gallery is None
            else show_component_gallery
        )

        self.setObjectName("mediaSyncWindow")
        self.setWindowTitle("MediaSync Home")
        self.resize(1120, 700)
        self.setMinimumSize(QSize(900, 560))

        self._engine_chip = QLabel()
        self._engine_chip.setObjectName("engineStatusChip")

        self._engine_state = QLabel()
        self._engine_state.setObjectName("engineStateLabel")

        self._engine_detail = QLabel()
        self._engine_detail.setObjectName("engineDetailLabel")
        self._engine_detail.setWordWrap(True)

        self._engine_scope = QLabel()
        self._engine_scope.setObjectName("engineScopeLabel")

        self._engine_protocol = QLabel()
        self._engine_protocol.setObjectName("engineProtocolLabel")

        self._engine_mutation = QLabel()
        self._engine_mutation.setObjectName("engineMutationLabel")

        self._refresh_button = QPushButton()
        self._refresh_button.setObjectName("refreshEngineButton")
        self._refresh_button.setIcon(self._icons.icon("refresh"))
        self._refresh_button.setIconSize(QSize(18, 18))
        self._refresh_button.setToolTip("Refresh engine status")
        self._refresh_button.setEnabled(engine_client is not None)
        self._refresh_button.clicked.connect(self.refresh_engine_status)

        self._language_button = QToolButton()
        self._language_button.setObjectName("languageSelectorButton")
        self._language_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._language_button.setMenu(self._build_language_menu())
        self._language_button.setFixedSize(QSize(36, 32))
        self._language_button.setIconSize(QSize(22, 16))
        self._language_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._apply_selected_language()

        self._build_layout()
        self.apply_engine_status(initial_state)

    def refresh_engine_status(self) -> None:
        if self._engine_client is None:
            return
        if not self._connected:
            handshake = self._engine_client.connect()
            if handshake.reason is not None:
                self.apply_engine_status(engine_status_from_response(handshake))
                return
            self._connected = True
        self.apply_engine_status(engine_status_from_response(self._engine_client.get_status()))
        self._refresh_backup_overview()
        self._refresh_activity_overview()

    def apply_engine_status(self, state: EngineStatusViewState) -> None:
        self._engine_chip.setText(f"{state.connection_label}: {state.state_label}")
        self._engine_chip.setProperty("statusKind", state.status_kind)
        self._engine_state.setText(state.state_label)
        self._engine_detail.setText(state.detail)
        self._engine_scope.setText(state.scope_label)
        self._engine_protocol.setText(state.protocol_label)
        self._engine_mutation.setText(state.mutation_label)
        _refresh_style(self._engine_chip)

    def apply_backup_overview(self, state: BackupOverviewViewState) -> None:
        self._setup_state = state.setup
        self._job_status_state = state.job_status
        self._apply_backup_setup_state(state.setup)
        self._apply_job_status_state(state.job_status)

    def apply_backup_job_detail(self, state: BackupJobDetailViewState) -> None:
        self._job_detail_state = state
        self._apply_backup_job_detail_state(state)

    def _refresh_backup_overview(self) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_backup_overview"):
            return
        provider = cast(BackupOverviewProvider, self._engine_client)
        state = backup_overview_from_response(provider.get_backup_overview())
        self.apply_backup_overview(state)
        if state.selected_job_id is None:
            self.apply_backup_job_detail(empty_backup_job_detail_state())
            return
        self._refresh_backup_job_detail(state.selected_job_id)

    def _refresh_backup_job_detail(self, job_id: str) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_backup_job_detail"):
            return
        provider = cast(BackupJobDetailProvider, self._engine_client)
        self.apply_backup_job_detail(backup_job_detail_from_response(provider.get_backup_job_detail(job_id=job_id)))

    def _refresh_activity_overview(self) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_activity_overview"):
            return
        provider = cast(ActivityOverviewProvider, self._engine_client)
        state = activity_overview_from_response(provider.get_activity_overview())
        if state.job_status is not None:
            self._job_status_state = state.job_status
            self._apply_job_status_state(state.job_status)

    def _apply_backup_setup_state(self, state: StandardBackupSetupViewState) -> None:
        for label, step in zip(self._setup_step_labels, state.steps, strict=False):
            label.setText(f"{step.number}. {step.title}")
            label.setProperty(
                "stepState",
                "current" if step.current else "complete" if step.complete else "upcoming",
            )
            _refresh_style(label)
        if self._setup_source_value is not None:
            self._setup_source_value.setText(state.source_label)
        if self._setup_target_value is not None:
            self._setup_target_value.setText(state.target_label)
        if self._setup_defaults_value is not None:
            self._setup_defaults_value.setText(" · ".join(state.defaults.summary()[:3]))
        if self._setup_retention_value is not None:
            self._setup_retention_value.setText(state.defaults.retention_label)
        if self._setup_primary_button is not None:
            self._setup_primary_button.setText(state.primary_action_label)
            self._setup_primary_button.setEnabled(state.can_create)

    def _apply_backup_job_detail_state(self, state: BackupJobDetailViewState) -> None:
        if self._job_detail_title is not None:
            self._job_detail_title.setText(state.title)
        if self._job_detail_source_value is not None:
            self._job_detail_source_value.setText(state.source_label)
        if self._job_detail_revision_value is not None:
            self._job_detail_revision_value.setText(state.revision_label)
        if self._job_detail_targets_value is not None:
            self._job_detail_targets_value.setText(state.target_summary_label)
        if self._job_detail_defaults_value is not None:
            self._job_detail_defaults_value.setText(state.defaults_summary_label)
        lines = state.target_lines or ("Ingen mål å vise.",)
        for index, row in enumerate(self._job_detail_target_rows):
            if index < len(lines):
                row.setText(lines[index])
                row.setVisible(True)
            else:
                row.setText("")
                row.setVisible(False)

    def _apply_job_status_state(self, state: BackupJobStatusViewState) -> None:
        if self._activity_status_title is not None:
            self._activity_status_title.setText(state.title)
        values = (
            ("Aktivitet", state.activity_label),
            ("Oppmerksomhet", state.attention_label),
            (
                "Ferskhet per mål",
                "Ikke konfigurert" if not state.target_statuses else state.target_statuses[0].freshness_label,
            ),
            ("Neste handling", state.recommended_action),
        )
        for row, (label, value) in zip(self._activity_dimension_rows, values, strict=False):
            row.setText(f"{label}: {value}")

    def _build_layout(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_action_bar())
        root_layout.addWidget(self._build_body(), 1)
        self.setCentralWidget(root)

    def _build_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("actionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(12)

        title = QLabel("MediaSync Home")
        title.setObjectName("productTitle")
        subtitle = QLabel("Local preview")
        subtitle.setObjectName("mutedLabel")

        title_group = QVBoxLayout()
        title_group.setContentsMargins(0, 0, 0, 0)
        title_group.setSpacing(2)
        title_group.addWidget(title)
        title_group.addWidget(subtitle)

        layout.addLayout(title_group)
        layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding))
        layout.addWidget(self._engine_chip)
        layout.addWidget(self._refresh_button)
        layout.addWidget(self._language_button)
        return bar

    def _build_body(self) -> QWidget:
        body = QWidget()
        body.setObjectName("appBody")
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_navigation())
        layout.addWidget(self._build_workspace(), 1)
        layout.addWidget(self._build_activity_bar())
        return body

    def _build_navigation(self) -> QListWidget:
        nav = QListWidget()
        nav.setObjectName("navigationRail")
        nav.setFixedWidth(184)
        for icon_name, label in (
            ("dashboard", "Dashboard"),
            ("activity", "Jobs"),
            ("history", "History"),
            ("settings", "Settings"),
        ):
            nav.addItem(QListWidgetItem(self._icons.icon(icon_name), label))
        nav.setCurrentRow(0)
        return nav

    def _build_workspace(self) -> QFrame:
        workspace = QFrame()
        workspace.setObjectName("workspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        heading = QLabel("Dashboard")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        layout.addWidget(self._build_backup_setup_panel(self._setup_state))
        layout.addWidget(self._build_dashboard_detail_row())
        layout.addStretch(1)
        return workspace

    def _build_dashboard_detail_row(self) -> QWidget:
        row = QWidget()
        row.setObjectName("dashboardDetailRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_backup_job_detail_panel(self._job_detail_state), 1)
        layout.addWidget(self._build_engine_panel(), 1)
        return row

    def _build_backup_setup_panel(self, state: StandardBackupSetupViewState) -> QFrame:
        panel = QFrame()
        panel.setObjectName("standardBackupPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(10)

        title = QLabel("Lag din første backup")
        title.setObjectName("sectionTitle")
        subtitle = QLabel("Velg én mappe og opptil tre mål. Sikker standard er valgt.")
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title, 0, 0, 1, 3)
        layout.addWidget(subtitle, 1, 0, 1, 3)

        stepper = QWidget()
        stepper.setObjectName("backupSetupStepper")
        stepper_layout = QHBoxLayout(stepper)
        stepper_layout.setContentsMargins(0, 4, 0, 4)
        stepper_layout.setSpacing(8)
        for step in state.steps:
            label = _step_label(step)
            self._setup_step_labels.append(label)
            stepper_layout.addWidget(label)
        layout.addWidget(stepper, 2, 0, 1, 3)

        self._setup_source_value = _add_text_value(layout, 3, "Kilde", state.source_label)
        self._setup_source_value.setObjectName("setupSourceValue")
        self._setup_target_value = _add_text_value(layout, 4, "Mål", state.target_label)
        self._setup_target_value.setObjectName("setupTargetValue")
        self._setup_defaults_value = _add_text_value(
            layout,
            5,
            "Standard",
            " · ".join(state.defaults.summary()[:3]),
        )
        self._setup_defaults_value.setObjectName("setupDefaultsValue")
        self._setup_retention_value = _add_text_value(layout, 6, "Bevaring", state.defaults.retention_label)
        self._setup_retention_value.setObjectName("setupRetentionValue")

        primary = QPushButton(state.primary_action_label)
        primary.setObjectName("createBackupButton")
        primary.setEnabled(state.can_create)
        primary.setToolTip("Opprett backup når kilde og minst ett mål er valgt")
        self._setup_primary_button = primary
        layout.addWidget(primary, 7, 2)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_backup_job_detail_panel(self, state: BackupJobDetailViewState) -> QFrame:
        panel = QFrame()
        panel.setObjectName("backupJobDetailPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(8)

        title = QLabel(state.title)
        title.setObjectName("jobDetailTitle")
        self._job_detail_title = title
        layout.addWidget(title, 0, 0, 1, 3)

        self._job_detail_source_value = _add_text_value(layout, 1, "Kilde", state.source_label)
        self._job_detail_source_value.setObjectName("jobDetailSourceValue")
        self._job_detail_targets_value = _add_text_value(layout, 2, "Mål", state.target_summary_label)
        self._job_detail_targets_value.setObjectName("jobDetailTargetsValue")
        self._job_detail_defaults_value = _add_text_value(layout, 3, "Standard", state.defaults_summary_label)
        self._job_detail_defaults_value.setObjectName("jobDetailDefaultsValue")
        self._job_detail_revision_value = _add_text_value(layout, 4, "Revisjon", state.revision_label)
        self._job_detail_revision_value.setObjectName("jobDetailRevisionValue")

        target_heading = QLabel("Målsteder")
        target_heading.setObjectName("mutedLabel")
        layout.addWidget(target_heading, 5, 0)
        self._job_detail_target_rows = []
        target_lines = state.target_lines or ("Ingen mål å vise.",)
        for index in range(3):
            row = QLabel(target_lines[index] if index < len(target_lines) else "")
            row.setObjectName("jobDetailTargetRow")
            row.setWordWrap(True)
            row.setVisible(index < len(target_lines))
            self._job_detail_target_rows.append(row)
            layout.addWidget(row, 5 + index, 1, 1, 2)

        layout.setColumnStretch(1, 1)
        return panel

    def _build_engine_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("engineStatusPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(24)
        layout.setVerticalSpacing(10)

        title = QLabel("Engine Host")
        title.setObjectName("sectionTitle")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title, 0, 0, 1, 2)
        layout.addWidget(self._engine_state, 0, 2)
        layout.addWidget(self._engine_detail, 1, 0, 1, 3)

        _add_key_value(layout, 2, "Scope", self._engine_scope)
        _add_key_value(layout, 3, "Contract", self._engine_protocol)
        _add_key_value(layout, 4, "Mutation policy", self._engine_mutation)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_activity_bar(self) -> QFrame:
        activity = QFrame()
        activity.setObjectName("activityBar")
        activity.setFixedWidth(248)
        layout = QVBoxLayout(activity)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(12)

        title = QLabel("Activity")
        title.setObjectName("sectionTitle")
        empty = QLabel("No active runs")
        empty.setObjectName("activityEmptyLabel")
        layout.addWidget(title)
        layout.addWidget(empty)
        layout.addSpacing(8)
        self._add_activity_status(layout, self._job_status_state)
        if self._show_component_gallery:
            layout.addWidget(self._build_component_gallery())
        layout.addStretch(1)
        return activity

    def _add_activity_status(
        self,
        layout: QVBoxLayout,
        state: BackupJobStatusViewState,
    ) -> None:
        heading = QLabel(state.title)
        heading.setObjectName("activityStatusTitle")
        self._activity_status_title = heading
        layout.addWidget(heading)
        self._activity_dimension_rows = []
        for label, value in (
            ("Aktivitet", state.activity_label),
            ("Oppmerksomhet", state.attention_label),
            ("Ferskhet per mål", "Ikke konfigurert"),
            ("Neste handling", state.recommended_action),
        ):
            row = QLabel(f"{label}: {value}")
            row.setObjectName("activityDimensionLabel")
            row.setWordWrap(True)
            self._activity_dimension_rows.append(row)
            layout.addWidget(row)

    def _build_component_gallery(self) -> QFrame:
        gallery = QFrame()
        gallery.setObjectName("componentGallery")
        layout = QVBoxLayout(gallery)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("Component gallery")
        title.setObjectName("mutedLabel")
        ready = QLabel("Ready chip / warning chip / blocked chip")
        ready.setObjectName("mutedLabel")
        ready.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(ready)
        return gallery

    def _build_language_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setObjectName("languageSelectorMenu")
        for code, label in self._language_options:
            action = QAction(_flag_icon(code), label, self)
            action.setObjectName(f"languageAction_{code}")
            action.setCheckable(True)
            action.setChecked(code == self._selected_language_code)
            action.triggered.connect(
                lambda checked=False, language_code=code: self._select_language(language_code)
            )
            menu.addAction(action)
            self._language_actions[code] = action
        return menu

    def _select_language(self, language_code: str) -> None:
        if language_code not in {code for code, _ in self._language_options}:
            return
        self._selected_language_code = language_code
        self._apply_selected_language()

    def _apply_selected_language(self) -> None:
        for code, label in self._language_options:
            if code == self._selected_language_code:
                self._language_button.setIcon(_flag_icon(code))
                self._language_button.setText("")
                self._language_button.setToolTip(f"Language: {label}")
                self._language_button.setAccessibleName(f"Language: {label}")
                for action_code, action in self._language_actions.items():
                    action.setChecked(action_code == code)
                return


def _add_key_value(layout: QGridLayout, row: int, label_text: str, value: QLabel) -> None:
    label = QLabel(label_text)
    label.setObjectName("mutedLabel")
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label, row, 0)
    layout.addWidget(value, row, 1, 1, 2)


def _add_text_value(layout: QGridLayout, row: int, label_text: str, value_text: str) -> QLabel:
    value = QLabel(value_text)
    value.setWordWrap(True)
    _add_key_value(layout, row, label_text, value)
    return value


def _step_label(step: BackupSetupStepViewState) -> QLabel:
    label = QLabel(f"{step.number}. {step.title}")
    label.setObjectName("setupStepLabel")
    state = "current" if step.current else "complete" if step.complete else "upcoming"
    label.setProperty("stepState", state)
    label.setWordWrap(True)
    return label


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _flag_icon(language_code: str) -> QIcon:
    pixmap = QPixmap(44, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if language_code == "en":
            _paint_union_jack(painter)
        else:
            _paint_norwegian_flag(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def _paint_norwegian_flag(painter: QPainter) -> None:
    painter.fillRect(0, 0, 44, 32, QColor("#ba0c2f"))
    painter.fillRect(12, 0, 8, 32, QColor("#ffffff"))
    painter.fillRect(0, 12, 44, 8, QColor("#ffffff"))
    painter.fillRect(14, 0, 4, 32, QColor("#00205b"))
    painter.fillRect(0, 14, 44, 4, QColor("#00205b"))


def _paint_union_jack(painter: QPainter) -> None:
    painter.fillRect(0, 0, 44, 32, QColor("#012169"))
    painter.setPen(QColor("#ffffff"))
    painter.drawLine(0, 0, 44, 32)
    painter.drawLine(44, 0, 0, 32)
    painter.setPen(QColor("#c8102e"))
    painter.drawLine(0, 2, 42, 32)
    painter.drawLine(44, 2, 2, 32)
    painter.fillRect(18, 0, 8, 32, QColor("#ffffff"))
    painter.fillRect(0, 12, 44, 8, QColor("#ffffff"))
    painter.fillRect(20, 0, 4, 32, QColor("#c8102e"))
    painter.fillRect(0, 14, 44, 4, QColor("#c8102e"))
