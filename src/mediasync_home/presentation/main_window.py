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
from mediasync_home.presentation.view_models.localization import (
    LanguageCode,
    ShellText,
    localize_display_value,
    normalize_language_code,
    shell_text,
)
from mediasync_home.presentation.view_models.plan_preview import (
    PlanOperationPreviewState,
    empty_plan_operation_preview_state,
    plan_operation_preview_from_response,
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


class PlanOperationsProvider(Protocol):
    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse: ...


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
        self._plan_preview_state = empty_plan_operation_preview_state()
        self._engine_status_state = initial_state
        self._subtitle_label: QLabel | None = None
        self._navigation_items: list[QListWidgetItem] = []
        self._workspace_heading: QLabel | None = None
        self._setup_title_label: QLabel | None = None
        self._setup_subtitle_label: QLabel | None = None
        self._setup_source_label: QLabel | None = None
        self._setup_target_label: QLabel | None = None
        self._setup_defaults_label: QLabel | None = None
        self._setup_retention_label: QLabel | None = None
        self._setup_step_labels: list[QLabel] = []
        self._setup_source_value: QLabel | None = None
        self._setup_target_value: QLabel | None = None
        self._setup_defaults_value: QLabel | None = None
        self._setup_retention_value: QLabel | None = None
        self._setup_primary_button: QPushButton | None = None
        self._job_detail_title: QLabel | None = None
        self._job_detail_source_label: QLabel | None = None
        self._job_detail_targets_label: QLabel | None = None
        self._job_detail_defaults_label: QLabel | None = None
        self._job_detail_revision_label: QLabel | None = None
        self._job_detail_target_heading: QLabel | None = None
        self._job_detail_source_value: QLabel | None = None
        self._job_detail_revision_value: QLabel | None = None
        self._job_detail_targets_value: QLabel | None = None
        self._job_detail_defaults_value: QLabel | None = None
        self._job_detail_target_rows: list[QLabel] = []
        self._engine_title_label: QLabel | None = None
        self._engine_scope_label: QLabel | None = None
        self._engine_contract_label: QLabel | None = None
        self._engine_mutation_label: QLabel | None = None
        self._activity_title_label: QLabel | None = None
        self._activity_empty_label: QLabel | None = None
        self._activity_status_title: QLabel | None = None
        self._activity_dimension_rows: list[QLabel] = []
        self._plan_preview_title: QLabel | None = None
        self._plan_preview_summary: QLabel | None = None
        self._plan_preview_rows: list[QLabel] = []
        self._language_options = (
            ("nb", "Norsk"),
            ("en", "English"),
        )
        self._selected_language_code = LanguageCode.NORWEGIAN
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
        self._refresh_button.setToolTip(self._texts().refresh_engine_status)
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

    def _texts(self) -> ShellText:
        return shell_text(self._selected_language_code)

    def _display(self, value: str) -> str:
        return localize_display_value(self._selected_language_code, value)

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
        self._engine_status_state = state
        self._engine_chip.setText(f"{self._display(state.connection_label)}: {self._display(state.state_label)}")
        self._engine_chip.setProperty("statusKind", state.status_kind)
        self._engine_state.setText(self._display(state.state_label))
        self._engine_detail.setText(self._display(state.detail))
        self._engine_scope.setText(self._display(state.scope_label))
        self._engine_protocol.setText(self._display(state.protocol_label))
        self._engine_mutation.setText(self._display(state.mutation_label))
        _refresh_style(self._engine_chip)

    def apply_backup_overview(self, state: BackupOverviewViewState) -> None:
        self._setup_state = state.setup
        self._job_status_state = state.job_status
        self._apply_backup_setup_state(state.setup)
        self._apply_job_status_state(state.job_status)

    def apply_backup_job_detail(self, state: BackupJobDetailViewState) -> None:
        self._job_detail_state = state
        self._apply_backup_job_detail_state(state)

    def apply_plan_operation_preview(self, state: PlanOperationPreviewState) -> None:
        self._plan_preview_state = state
        self._apply_plan_operation_preview_state(state)

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
        if state.latest_plan_id is None:
            self.apply_plan_operation_preview(empty_plan_operation_preview_state())
            return
        self._refresh_plan_operation_preview(state.latest_plan_id)

    def _refresh_plan_operation_preview(self, plan_id: str) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_plan_operations"):
            return
        provider = cast(PlanOperationsProvider, self._engine_client)
        self.apply_plan_operation_preview(
            plan_operation_preview_from_response(provider.get_plan_operations(plan_id=plan_id, limit=3))
        )

    def _apply_backup_setup_state(self, state: StandardBackupSetupViewState) -> None:
        for label, step, title in zip(
            self._setup_step_labels,
            state.steps,
            self._texts().setup_steps,
            strict=False,
        ):
            label.setText(f"{step.number}. {title}")
            label.setProperty(
                "stepState",
                "current" if step.current else "complete" if step.complete else "upcoming",
            )
            _refresh_style(label)
        if self._setup_source_value is not None:
            self._setup_source_value.setText(self._display(state.source_label))
        if self._setup_target_value is not None:
            self._setup_target_value.setText(self._display(state.target_label))
        if self._setup_defaults_value is not None:
            self._setup_defaults_value.setText(
                " · ".join(self._display(label) for label in state.defaults.summary()[:3])
            )
        if self._setup_retention_value is not None:
            self._setup_retention_value.setText(self._display(state.defaults.retention_label))
        if self._setup_primary_button is not None:
            self._setup_primary_button.setText(self._display(state.primary_action_label))
            self._setup_primary_button.setEnabled(state.can_create)

    def _apply_backup_job_detail_state(self, state: BackupJobDetailViewState) -> None:
        if self._job_detail_title is not None:
            self._job_detail_title.setText(self._display(state.title))
        if self._job_detail_source_value is not None:
            self._job_detail_source_value.setText(self._display(state.source_label))
        if self._job_detail_revision_value is not None:
            self._job_detail_revision_value.setText(self._display(state.revision_label))
        if self._job_detail_targets_value is not None:
            self._job_detail_targets_value.setText(self._display(state.target_summary_label))
        if self._job_detail_defaults_value is not None:
            self._job_detail_defaults_value.setText(self._display(state.defaults_summary_label))
        lines = state.target_lines or ("Ingen mål å vise.",)
        for index, row in enumerate(self._job_detail_target_rows):
            if index < len(lines):
                row.setText(self._display(lines[index]))
                row.setVisible(True)
            else:
                row.setText("")
                row.setVisible(False)

    def _apply_job_status_state(self, state: BackupJobStatusViewState) -> None:
        if self._activity_status_title is not None:
            self._activity_status_title.setText(self._display(state.title))
        texts = self._texts()
        values = (
            (texts.activity, state.activity_label),
            (texts.attention, state.attention_label),
            (
                texts.target_freshness,
                "Ikke konfigurert" if not state.target_statuses else state.target_statuses[0].freshness_label,
            ),
            (texts.next_action, state.recommended_action),
        )
        for row, (label, value) in zip(self._activity_dimension_rows, values, strict=False):
            row.setText(f"{label}: {self._display(value)}")

    def _apply_plan_operation_preview_state(self, state: PlanOperationPreviewState) -> None:
        if self._plan_preview_title is not None:
            self._plan_preview_title.setText(self._display(state.title))
        if self._plan_preview_summary is not None:
            self._plan_preview_summary.setText(self._display(state.summary_label))
        lines = tuple(f"{row.risk_label}: {row.display_line}" for row in state.rows) or (
            "No plan operations.",
        )
        for index, row in enumerate(self._plan_preview_rows):
            if index < len(lines):
                row.setText(self._display(lines[index]))
                row.setVisible(True)
            else:
                row.setText("")
                row.setVisible(False)

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
        texts = self._texts()
        bar = QFrame()
        bar.setObjectName("actionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(12)

        title = QLabel("MediaSync Home")
        title.setObjectName("productTitle")
        subtitle = QLabel(texts.local_preview)
        subtitle.setObjectName("mutedLabel")
        self._subtitle_label = subtitle

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
        texts = self._texts()
        nav = QListWidget()
        nav.setObjectName("navigationRail")
        nav.setFixedWidth(184)
        for icon_name, label in (
            ("dashboard", texts.dashboard),
            ("activity", texts.jobs),
            ("history", texts.history),
            ("settings", texts.settings),
        ):
            item = QListWidgetItem(self._icons.icon(icon_name), label)
            nav.addItem(item)
            self._navigation_items.append(item)
        nav.setCurrentRow(0)
        return nav

    def _build_workspace(self) -> QFrame:
        workspace = QFrame()
        workspace.setObjectName("workspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        heading = QLabel(self._texts().dashboard)
        heading.setObjectName("sectionTitle")
        self._workspace_heading = heading
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
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("standardBackupPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(10)

        title = QLabel(texts.setup_title)
        title.setObjectName("sectionTitle")
        self._setup_title_label = title
        subtitle = QLabel(texts.setup_subtitle)
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        self._setup_subtitle_label = subtitle
        layout.addWidget(title, 0, 0, 1, 3)
        layout.addWidget(subtitle, 1, 0, 1, 3)

        stepper = QWidget()
        stepper.setObjectName("backupSetupStepper")
        stepper_layout = QHBoxLayout(stepper)
        stepper_layout.setContentsMargins(0, 4, 0, 4)
        stepper_layout.setSpacing(8)
        for index, step in enumerate(state.steps):
            label = _step_label(step, texts.setup_steps[index])
            self._setup_step_labels.append(label)
            stepper_layout.addWidget(label)
        layout.addWidget(stepper, 2, 0, 1, 3)

        self._setup_source_label, self._setup_source_value = _add_labeled_text_value(
            layout,
            3,
            texts.source,
            self._display(state.source_label),
        )
        self._setup_source_value.setObjectName("setupSourceValue")
        self._setup_target_label, self._setup_target_value = _add_labeled_text_value(
            layout,
            4,
            texts.target,
            self._display(state.target_label),
        )
        self._setup_target_value.setObjectName("setupTargetValue")
        self._setup_defaults_label, self._setup_defaults_value = _add_labeled_text_value(
            layout,
            5,
            texts.defaults,
            " · ".join(self._display(label) for label in state.defaults.summary()[:3]),
        )
        self._setup_defaults_value.setObjectName("setupDefaultsValue")
        self._setup_retention_label, self._setup_retention_value = _add_labeled_text_value(
            layout,
            6,
            texts.retention,
            self._display(state.defaults.retention_label),
        )
        self._setup_retention_value.setObjectName("setupRetentionValue")

        primary = QPushButton(self._display(state.primary_action_label))
        primary.setObjectName("createBackupButton")
        primary.setEnabled(state.can_create)
        primary.setToolTip(texts.create_backup_tooltip)
        self._setup_primary_button = primary
        layout.addWidget(primary, 7, 2)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_backup_job_detail_panel(self, state: BackupJobDetailViewState) -> QFrame:
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("backupJobDetailPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(8)

        title = QLabel(self._display(state.title))
        title.setObjectName("jobDetailTitle")
        self._job_detail_title = title
        layout.addWidget(title, 0, 0, 1, 3)

        self._job_detail_source_label, self._job_detail_source_value = _add_labeled_text_value(
            layout,
            1,
            texts.source,
            self._display(state.source_label),
        )
        self._job_detail_source_value.setObjectName("jobDetailSourceValue")
        self._job_detail_targets_label, self._job_detail_targets_value = _add_labeled_text_value(
            layout,
            2,
            texts.target,
            self._display(state.target_summary_label),
        )
        self._job_detail_targets_value.setObjectName("jobDetailTargetsValue")
        self._job_detail_defaults_label, self._job_detail_defaults_value = _add_labeled_text_value(
            layout,
            3,
            texts.defaults,
            self._display(state.defaults_summary_label),
        )
        self._job_detail_defaults_value.setObjectName("jobDetailDefaultsValue")
        self._job_detail_revision_label, self._job_detail_revision_value = _add_labeled_text_value(
            layout,
            4,
            texts.revision,
            self._display(state.revision_label),
        )
        self._job_detail_revision_value.setObjectName("jobDetailRevisionValue")

        target_heading = QLabel(texts.job_detail_targets_heading)
        target_heading.setObjectName("mutedLabel")
        self._job_detail_target_heading = target_heading
        layout.addWidget(target_heading, 5, 0)
        self._job_detail_target_rows = []
        target_lines = state.target_lines or ("Ingen mål å vise.",)
        for index in range(3):
            row = QLabel(self._display(target_lines[index]) if index < len(target_lines) else "")
            row.setObjectName("jobDetailTargetRow")
            row.setWordWrap(True)
            row.setVisible(index < len(target_lines))
            self._job_detail_target_rows.append(row)
            layout.addWidget(row, 5 + index, 1, 1, 2)

        layout.setColumnStretch(1, 1)
        return panel

    def _build_engine_panel(self) -> QFrame:
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("engineStatusPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(24)
        layout.setVerticalSpacing(10)

        title = QLabel(texts.engine_host)
        title.setObjectName("sectionTitle")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._engine_title_label = title
        layout.addWidget(title, 0, 0, 1, 2)
        layout.addWidget(self._engine_state, 0, 2)
        layout.addWidget(self._engine_detail, 1, 0, 1, 3)

        self._engine_scope_label = _add_key_value(layout, 2, texts.scope, self._engine_scope)
        self._engine_contract_label = _add_key_value(layout, 3, texts.contract, self._engine_protocol)
        self._engine_mutation_label = _add_key_value(
            layout,
            4,
            texts.mutation_policy,
            self._engine_mutation,
        )
        layout.setColumnStretch(1, 1)
        return panel

    def _build_activity_bar(self) -> QFrame:
        texts = self._texts()
        activity = QFrame()
        activity.setObjectName("activityBar")
        activity.setFixedWidth(248)
        layout = QVBoxLayout(activity)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(12)

        title = QLabel(texts.activity)
        title.setObjectName("sectionTitle")
        self._activity_title_label = title
        empty = QLabel(texts.no_active_runs)
        empty.setObjectName("activityEmptyLabel")
        self._activity_empty_label = empty
        layout.addWidget(title)
        layout.addWidget(empty)
        layout.addSpacing(8)
        self._add_activity_status(layout, self._job_status_state)
        layout.addSpacing(8)
        self._add_plan_operation_preview(layout, self._plan_preview_state)
        if self._show_component_gallery:
            layout.addWidget(self._build_component_gallery())
        layout.addStretch(1)
        return activity

    def _add_activity_status(
        self,
        layout: QVBoxLayout,
        state: BackupJobStatusViewState,
    ) -> None:
        heading = QLabel(self._display(state.title))
        heading.setObjectName("activityStatusTitle")
        self._activity_status_title = heading
        layout.addWidget(heading)
        self._activity_dimension_rows = []
        texts = self._texts()
        for label, value in (
            (texts.activity, state.activity_label),
            (texts.attention, state.attention_label),
            (texts.target_freshness, "Ikke konfigurert"),
            (texts.next_action, state.recommended_action),
        ):
            row = QLabel(f"{label}: {self._display(value)}")
            row.setObjectName("activityDimensionLabel")
            row.setWordWrap(True)
            self._activity_dimension_rows.append(row)
            layout.addWidget(row)

    def _add_plan_operation_preview(
        self,
        layout: QVBoxLayout,
        state: PlanOperationPreviewState,
    ) -> None:
        title = QLabel(self._display(state.title))
        title.setObjectName("planPreviewTitle")
        self._plan_preview_title = title
        summary = QLabel(self._display(state.summary_label))
        summary.setObjectName("planPreviewSummary")
        summary.setWordWrap(True)
        self._plan_preview_summary = summary
        layout.addWidget(title)
        layout.addWidget(summary)
        self._plan_preview_rows = []
        lines = tuple(f"{row.risk_label}: {row.display_line}" for row in state.rows) or (
            "No plan operations.",
        )
        for index in range(3):
            row = QLabel(self._display(lines[index]) if index < len(lines) else "")
            row.setObjectName("planPreviewRow")
            row.setWordWrap(True)
            row.setVisible(index < len(lines))
            self._plan_preview_rows.append(row)
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
            action.setChecked(code == self._selected_language_code.value)
            action.triggered.connect(
                lambda checked=False, language_code=code: self._select_language(language_code)
            )
            menu.addAction(action)
            self._language_actions[code] = action
        return menu

    def _select_language(self, language_code: str) -> None:
        normalized = normalize_language_code(language_code)
        if normalized is None:
            return
        self._selected_language_code = normalized
        self._apply_selected_language()
        self._apply_localized_text()

    def _apply_selected_language(self) -> None:
        for code, label in self._language_options:
            if code == self._selected_language_code.value:
                self._language_button.setIcon(_flag_icon(code))
                self._language_button.setText("")
                tooltip = f"{self._texts().language_tooltip_prefix}: {label}"
                self._language_button.setToolTip(tooltip)
                self._language_button.setAccessibleName(tooltip)
                for action_code, action in self._language_actions.items():
                    action.setChecked(action_code == code)
                return

    def _apply_localized_text(self) -> None:
        texts = self._texts()
        self._refresh_button.setToolTip(texts.refresh_engine_status)
        if self._subtitle_label is not None:
            self._subtitle_label.setText(texts.local_preview)
        for item, navigation_label in zip(
            self._navigation_items,
            (texts.dashboard, texts.jobs, texts.history, texts.settings),
            strict=False,
        ):
            item.setText(navigation_label)
        if self._workspace_heading is not None:
            self._workspace_heading.setText(texts.dashboard)
        if self._setup_title_label is not None:
            self._setup_title_label.setText(texts.setup_title)
        if self._setup_subtitle_label is not None:
            self._setup_subtitle_label.setText(texts.setup_subtitle)
        for text_label, text in (
            (self._setup_source_label, texts.source),
            (self._setup_target_label, texts.target),
            (self._setup_defaults_label, texts.defaults),
            (self._setup_retention_label, texts.retention),
            (self._job_detail_source_label, texts.source),
            (self._job_detail_targets_label, texts.target),
            (self._job_detail_defaults_label, texts.defaults),
            (self._job_detail_revision_label, texts.revision),
            (self._job_detail_target_heading, texts.job_detail_targets_heading),
            (self._engine_title_label, texts.engine_host),
            (self._engine_scope_label, texts.scope),
            (self._engine_contract_label, texts.contract),
            (self._engine_mutation_label, texts.mutation_policy),
            (self._activity_title_label, texts.activity),
            (self._activity_empty_label, texts.no_active_runs),
        ):
            if text_label is not None:
                text_label.setText(text)
        if self._setup_primary_button is not None:
            self._setup_primary_button.setToolTip(texts.create_backup_tooltip)
        self.apply_engine_status(self._engine_status_state)
        self._apply_backup_setup_state(self._setup_state)
        self._apply_backup_job_detail_state(self._job_detail_state)
        self._apply_job_status_state(self._job_status_state)
        self._apply_plan_operation_preview_state(self._plan_preview_state)


def _add_key_value(layout: QGridLayout, row: int, label_text: str, value: QLabel) -> QLabel:
    label = QLabel(label_text)
    label.setObjectName("mutedLabel")
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label, row, 0)
    layout.addWidget(value, row, 1, 1, 2)
    return label


def _add_text_value(layout: QGridLayout, row: int, label_text: str, value_text: str) -> QLabel:
    return _add_labeled_text_value(layout, row, label_text, value_text)[1]


def _add_labeled_text_value(
    layout: QGridLayout,
    row: int,
    label_text: str,
    value_text: str,
) -> tuple[QLabel, QLabel]:
    value = QLabel(value_text)
    value.setWordWrap(True)
    label = _add_key_value(layout, row, label_text, value)
    return label, value


def _step_label(step: BackupSetupStepViewState, title: str) -> QLabel:
    label = QLabel(f"{step.number}. {title}")
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
