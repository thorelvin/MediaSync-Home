from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mediasync_home import __version__
from mediasync_home.application.user_preferences import (
    AppearancePreference,
    DensityPreference,
    UserLanguage,
    UserPreferences,
    UserPreferencesStore,
)
from mediasync_home.presentation.theme.icon_registry import IconRegistry
from mediasync_home.application.job_drafts import (
    DraftTarget,
    StandardBackupJobDraft,
)
from mediasync_home.presentation.view_models.backup_setup import (
    BackupOverviewViewState,
    BackupSetupDraft,
    BackupSetupStep,
    BackupSetupStepViewState,
    BackupTargetDraft,
    BackupJobDetailViewState,
    BackupJobStatusViewState,
    StandardBackupSetupViewState,
    activity_overview_from_response,
    backup_job_detail_from_response,
    backup_overview_from_response,
    build_standard_backup_setup_state,
    empty_backup_overview_state,
    empty_backup_job_detail_state,
    empty_backup_job_status_state,
)
from mediasync_home.presentation.view_models.catalog_preview import (
    CatalogedFilesPreviewState,
    cataloged_files_preview_from_response,
    empty_cataloged_files_preview_state,
)
from mediasync_home.presentation.view_models.engine_status import (
    EngineStatusProvider,
    EngineStatusViewState,
    engine_status_from_response,
)
from mediasync_home.presentation.view_models.history import (
    HistoryActivityViewState,
    HistoryTimelineViewState,
    empty_history_timeline_state,
    history_timeline_from_response,
)
from mediasync_home.presentation.view_models.localization import (
    LanguageCode,
    ShellText,
    localize_display_value,
    normalize_language_code,
    settings_text,
    shell_text,
)
from mediasync_home.presentation.view_models.operation_audit import (
    OperationAuditViewState,
    empty_operation_audit_state,
    operation_audit_from_response,
)
from mediasync_home.presentation.view_models.plan_preview import (
    PlanOperationPreviewState,
    empty_plan_operation_preview_state,
    plan_operation_preview_from_response,
)
from mediasync_home.presentation.view_models.plan_endpoints import (
    PlanEndpointPreviewState,
    empty_plan_endpoint_preview_state,
    plan_endpoint_preview_from_response,
)
from mediasync_home.presentation.view_models.snapshot_health import (
    SnapshotHealthPreviewState,
    empty_snapshot_health_preview_state,
    snapshot_health_preview_from_responses,
)
from mediasync_home.presentation.view_models.run_progress import (
    RunProgressViewState,
    empty_run_progress_state,
    run_progress_from_response,
)
from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


SNAPSHOT_HEALTH_COVERAGE_STATES = (
    "VOLATILE",
    "UNREADABLE",
    "DISAPPEARED",
    "REPARSE_BLOCKED",
    "CASE_CONTEXT_UNKNOWN",
    "CANCELLED",
)


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


class HistoryTimelineProvider(Protocol):
    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...


class OperationAuditProvider(Protocol):
    def get_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
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


class PlanEndpointsProvider(Protocol):
    def get_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse: ...


class SnapshotHealthProvider(Protocol):
    def get_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse: ...

    def get_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse: ...


class CatalogedFilesProvider(Protocol):
    def get_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse: ...


class BackupJobCreationProvider(Protocol):
    def create_standard_backup_job(
        self,
        *,
        draft: StandardBackupJobDraft,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse: ...


class BackupStartProvider(Protocol):
    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse: ...


class BackupCheckProvider(Protocol):
    def check_backup(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse: ...


class RunProgressProvider(Protocol):
    def get_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse: ...


class RunControlProvider(Protocol):
    def pause_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse: ...

    def resume_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse: ...

    def stop_backup_after_active_file(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse: ...


class MediaSyncWindow(QMainWindow):
    def __init__(
        self,
        *,
        initial_state: EngineStatusViewState,
        engine_client: EngineStatusProvider | None = None,
        engine_client_factory: Callable[[], EngineStatusProvider | None] | None = None,
        user_preferences: UserPreferences | None = None,
        user_preferences_store: UserPreferencesStore | None = None,
        apply_appearance: (
            Callable[[AppearancePreference, DensityPreference], None] | None
        ) = None,
        data_root: Path | None = None,
        open_data_folder: Callable[[Path], bool] | None = None,
        show_component_gallery: bool | None = None,
    ) -> None:
        super().__init__()
        self._engine_client = engine_client
        self._engine_client_factory = engine_client_factory
        self._user_preferences = user_preferences or UserPreferences()
        self._user_preferences_store = user_preferences_store
        self._apply_appearance = apply_appearance
        self._data_root = data_root
        self._open_data_folder = open_data_folder
        self._icons = IconRegistry()
        self._connected = False
        self._setup_draft = BackupSetupDraft.empty()
        self._setup_draft_id: str | None = None
        self._setup_request_id: str | None = None
        self._setup_idempotency_key: str | None = None
        self._start_request_id: str | None = None
        self._start_idempotency_key: str | None = None
        self._analysis_request_id: str | None = None
        self._analysis_idempotency_key: str | None = None
        self._latest_run_job_id: str | None = None
        self._latest_run_plan_id: str | None = None
        self._latest_run_state: str | None = None
        self._queued_backup_job_ids: set[str] = set()
        self._active_run_id: str | None = None
        self._run_control_pending = False
        self._run_progress_state = empty_run_progress_state()
        self._setup_state = build_standard_backup_setup_state(self._setup_draft)
        self._backup_overview_state = empty_backup_overview_state()
        self._job_status_state = empty_backup_job_status_state()
        self._job_detail_state = empty_backup_job_detail_state()
        self._plan_preview_state = empty_plan_operation_preview_state()
        self._plan_endpoint_preview_state = empty_plan_endpoint_preview_state()
        self._snapshot_health_preview_state = empty_snapshot_health_preview_state()
        self._cataloged_files_preview_state = empty_cataloged_files_preview_state()
        self._history_timeline_state = empty_history_timeline_state()
        self._history_operation_page_state = empty_plan_operation_preview_state()
        self._history_operation_audit_state = empty_operation_audit_state()
        self._engine_status_state = initial_state
        self._subtitle_label: QLabel | None = None
        self._navigation: QListWidget | None = None
        self._navigation_items: list[QListWidgetItem] = []
        self._selected_navigation_index = 0
        self._workspace_heading: QLabel | None = None
        self._workspace_stack: QStackedWidget | None = None
        self._dashboard_page: QWidget | None = None
        self._dashboard_scroll_area: QScrollArea | None = None
        self._jobs_page: QWidget | None = None
        self._jobs_scroll_area: QScrollArea | None = None
        self._jobs_list: QListWidget | None = None
        self._jobs_empty_label: QLabel | None = None
        self._jobs_title_label: QLabel | None = None
        self._jobs_page_label: QLabel | None = None
        self._jobs_previous_button: QToolButton | None = None
        self._jobs_next_button: QToolButton | None = None
        self._jobs_page_limit = 25
        self._jobs_page_offset = 0
        self._selected_job_id: str | None = None
        self._history_page: QWidget | None = None
        self._history_scroll_area: QScrollArea | None = None
        self._history_title_label: QLabel | None = None
        self._history_list: QListWidget | None = None
        self._history_empty_label: QLabel | None = None
        self._history_filter_group: QButtonGroup | None = None
        self._history_filter_buttons: dict[str, QPushButton] = {}
        self._history_job_filter: QComboBox | None = None
        self._history_page_label: QLabel | None = None
        self._history_previous_button: QToolButton | None = None
        self._history_next_button: QToolButton | None = None
        self._history_page_limit = 25
        self._history_page_offset = 0
        self._history_activity_filter = "ALL"
        self._history_job_id: str | None = None
        self._selected_history_activity_id: str | None = None
        self._history_detail_title: QLabel | None = None
        self._history_detail_labels: dict[str, QLabel] = {}
        self._history_detail_values: dict[str, QLabel] = {}
        self._history_target_heading: QLabel | None = None
        self._history_target_rows: list[QLabel] = []
        self._history_operation_activity_key: str | None = None
        self._history_operation_run_id: str | None = None
        self._history_operation_plan_id: str | None = None
        self._history_operation_list: QListWidget | None = None
        self._history_operation_empty_label: QLabel | None = None
        self._history_operation_heading: QLabel | None = None
        self._history_operation_header: QWidget | None = None
        self._history_operation_page_label: QLabel | None = None
        self._history_operation_previous_button: QToolButton | None = None
        self._history_operation_next_button: QToolButton | None = None
        self._history_operation_page_limit = 25
        self._history_operation_page_index = 0
        self._history_operation_page_cursors: list[dict[str, object] | None] = [None]
        self._selected_history_operation_id: str | None = None
        self._history_operation_detail_title: QLabel | None = None
        self._history_operation_detail_labels: dict[str, QLabel] = {}
        self._history_operation_detail_values: dict[str, QLabel] = {}
        self._history_attempt_heading: QLabel | None = None
        self._history_attempt_list: QListWidget | None = None
        self._dashboard_detail_layout: QBoxLayout | None = None
        self._setup_stepper_layout: QGridLayout | None = None
        self._compact_dashboard_layout: bool | None = None
        self._stacked_dashboard_details: bool | None = None
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
        self._setup_target_controls: QWidget | None = None
        self._setup_target_rows: list[QWidget] = []
        self._setup_target_path_labels: list[QLabel] = []
        self._setup_remove_target_buttons: list[QToolButton] = []
        self._setup_add_target_button: QToolButton | None = None
        self._setup_back_button: QToolButton | None = None
        self._setup_primary_button: QPushButton | None = None
        self._job_detail_title: QLabel | None = None
        self._job_detail_source_label: QLabel | None = None
        self._job_detail_targets_label: QLabel | None = None
        self._job_detail_defaults_label: QLabel | None = None
        self._job_detail_revision_label: QLabel | None = None
        self._job_detail_plan_label: QLabel | None = None
        self._job_detail_target_heading: QLabel | None = None
        self._job_detail_source_value: QLabel | None = None
        self._job_detail_revision_value: QLabel | None = None
        self._job_detail_targets_value: QLabel | None = None
        self._job_detail_defaults_value: QLabel | None = None
        self._job_detail_plan_value: QLabel | None = None
        self._start_backup_button: QPushButton | None = None
        self._job_detail_target_rows: list[QLabel] = []
        self._jobs_detail_title: QLabel | None = None
        self._jobs_detail_source_label: QLabel | None = None
        self._jobs_detail_targets_label: QLabel | None = None
        self._jobs_detail_defaults_label: QLabel | None = None
        self._jobs_detail_revision_label: QLabel | None = None
        self._jobs_detail_plan_label: QLabel | None = None
        self._jobs_detail_target_heading: QLabel | None = None
        self._jobs_detail_source_value: QLabel | None = None
        self._jobs_detail_targets_value: QLabel | None = None
        self._jobs_detail_defaults_value: QLabel | None = None
        self._jobs_detail_revision_value: QLabel | None = None
        self._jobs_detail_plan_value: QLabel | None = None
        self._jobs_detail_target_rows: list[QLabel] = []
        self._jobs_start_backup_button: QPushButton | None = None
        self._jobs_run_progress_title: QLabel | None = None
        self._jobs_run_progress_state: QLabel | None = None
        self._jobs_run_progress_bar: QProgressBar | None = None
        self._jobs_run_progress_detail: QLabel | None = None
        self._jobs_run_active_file: QLabel | None = None
        self._jobs_run_target_rows: list[QLabel] = []
        self._jobs_pause_button: QPushButton | None = None
        self._jobs_resume_button: QPushButton | None = None
        self._jobs_stop_button: QPushButton | None = None
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
        self._plan_endpoint_title: QLabel | None = None
        self._plan_endpoint_summary: QLabel | None = None
        self._plan_endpoint_rows: list[QLabel] = []
        self._snapshot_health_title: QLabel | None = None
        self._snapshot_health_summary: QLabel | None = None
        self._snapshot_health_rows: list[QLabel] = []
        self._cataloged_files_title: QLabel | None = None
        self._cataloged_files_summary: QLabel | None = None
        self._cataloged_files_rows: list[QLabel] = []
        self._settings_page: QWidget | None = None
        self._settings_scroll_area: QScrollArea | None = None
        self._settings_labels: dict[str, QLabel] = {}
        self._settings_theme_buttons: dict[AppearancePreference, QPushButton] = {}
        self._settings_theme_layout: QBoxLayout | None = None
        self._settings_action_layout: QBoxLayout | None = None
        self._settings_density_combo: QComboBox | None = None
        self._settings_language_combo: QComboBox | None = None
        self._settings_reduced_motion: QCheckBox | None = None
        self._settings_status_label: QLabel | None = None
        self._settings_open_data_button: QPushButton | None = None
        self._settings_copy_diagnostics_button: QPushButton | None = None
        self._language_options = (
            ("nb", "Norsk"),
            ("en", "English"),
        )
        self._selected_language_code = LanguageCode(self._user_preferences.language.value)
        self._language_actions: dict[str, QAction] = {}
        self._run_progress_timer = QTimer(self)
        self._run_progress_timer.setInterval(1000)
        self._run_progress_timer.timeout.connect(self._poll_active_run_progress)
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(750)
        self._analysis_timer.timeout.connect(self._poll_backup_analysis)
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
        _configure_responsive_label(self._engine_detail)

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
        self._update_responsive_dashboard_layout()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_responsive_dashboard_layout()
        QTimer.singleShot(0, self._refresh_dashboard_geometry)

    def _texts(self) -> ShellText:
        return shell_text(self._selected_language_code)

    def _display(self, value: str) -> str:
        return localize_display_value(self._selected_language_code, value)

    def refresh_engine_status(self) -> None:
        self._refresh_button.setEnabled(False)
        try:
            self._refresh_engine_status_now()
        except (OSError, TimeoutError):
            self._connected = False
            self.apply_engine_status(
                EngineStatusViewState.disconnected("Engine status is unavailable.")
            )
        finally:
            self._refresh_button.setEnabled(True)

    def _refresh_engine_status_now(self) -> None:
        if self._engine_client is None and self._engine_client_factory is not None:
            try:
                self._engine_client = self._engine_client_factory()
            except Exception:
                self.apply_engine_status(
                    EngineStatusViewState.disconnected("Engine status is unavailable.")
                )
                return
        if self._engine_client is None:
            self.apply_engine_status(
                EngineStatusViewState.disconnected(
                    "Start a local Engine Host to refresh live status."
                )
            )
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
        self._refresh_history_timeline()
        self._refresh_cataloged_files_preview()

    def apply_engine_status(self, state: EngineStatusViewState) -> None:
        self._engine_status_state = state
        self._engine_chip.setText(f"{self._display(state.connection_label)}: {self._display(state.state_label)}")
        self._engine_chip.setProperty("statusKind", state.status_kind)
        self._engine_state.setText(self._display(state.state_label))
        self._engine_detail.setText(self._display(state.detail))
        self._engine_scope.setText(self._display(state.scope_label))
        self._engine_protocol.setText(self._display(state.protocol_label))
        self._engine_mutation.setText(self._display(state.mutation_label))
        self._apply_settings_storage_state()
        _refresh_style(self._engine_chip)

    def apply_backup_overview(self, state: BackupOverviewViewState) -> None:
        self._backup_overview_state = state
        self._setup_state = state.setup
        self._job_status_state = state.job_status
        self._apply_backup_setup_state(state.setup)
        self._apply_job_status_state(state.job_status)
        self._apply_jobs_overview_state(state)

    def apply_backup_job_detail(self, state: BackupJobDetailViewState) -> None:
        self._job_detail_state = state
        self._apply_backup_job_detail_state(state)

    def apply_plan_operation_preview(self, state: PlanOperationPreviewState) -> None:
        self._plan_preview_state = state
        self._apply_plan_operation_preview_state(state)

    def apply_plan_endpoint_preview(self, state: PlanEndpointPreviewState) -> None:
        self._plan_endpoint_preview_state = state
        self._apply_plan_endpoint_preview_state(state)

    def apply_snapshot_health_preview(self, state: SnapshotHealthPreviewState) -> None:
        self._snapshot_health_preview_state = state
        self._apply_snapshot_health_preview_state(state)

    def apply_cataloged_files_preview(self, state: CatalogedFilesPreviewState) -> None:
        self._cataloged_files_preview_state = state
        self._apply_cataloged_files_preview_state(state)

    def apply_history_timeline(self, state: HistoryTimelineViewState) -> None:
        self._history_timeline_state = state
        self._apply_history_timeline_state(state)

    def _refresh_backup_overview(self) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_backup_overview"):
            return
        provider = cast(BackupOverviewProvider, self._engine_client)
        state = backup_overview_from_response(
            provider.get_backup_overview(
                limit=self._jobs_page_limit,
                offset=self._jobs_page_offset,
            )
        )
        job_ids = {job.job_id for job in state.jobs}
        if self._selected_job_id not in job_ids:
            self._selected_job_id = state.selected_job_id
        self.apply_backup_overview(state)
        if self._selected_job_id is None:
            self.apply_backup_job_detail(empty_backup_job_detail_state())
            return
        self._refresh_backup_job_detail(self._selected_job_id)

    def _refresh_backup_job_detail(self, job_id: str) -> BackupJobDetailViewState | None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_backup_job_detail"):
            return None
        provider = cast(BackupJobDetailProvider, self._engine_client)
        state = backup_job_detail_from_response(
            provider.get_backup_job_detail(job_id=job_id)
        )
        if (
            state.analysis_request_id is not None
            and state.analysis_request_state in {"QUEUED", "RUNNING"}
        ):
            self._analysis_request_id = state.analysis_request_id
            if not self._analysis_timer.isActive():
                self._analysis_timer.start()
        self.apply_backup_job_detail(state)
        return state

    def _apply_jobs_overview_state(self, state: BackupOverviewViewState) -> None:
        jobs_list = self._jobs_list
        if jobs_list is None:
            return
        jobs_list.blockSignals(True)
        jobs_list.clear()
        selected_row = -1
        for index, job in enumerate(state.jobs):
            item = QListWidgetItem(
                f"{self._display(job.title)}\n"
                f"{self._display(job.target_summary_label)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, job.job_id)
            item.setToolTip(job.source_label)
            item.setSizeHint(QSize(0, 58))
            jobs_list.addItem(item)
            if job.job_id == self._selected_job_id:
                selected_row = index
        if selected_row >= 0:
            jobs_list.setCurrentRow(selected_row)
        jobs_list.blockSignals(False)

        has_jobs = bool(state.jobs)
        jobs_list.setVisible(state.read_model_available and has_jobs)
        jobs_list.setEnabled(state.read_model_available)
        if self._jobs_empty_label is not None:
            self._jobs_empty_label.setText(
                self._texts().jobs_empty
                if state.read_model_available
                else self._texts().jobs_unavailable
            )
            self._jobs_empty_label.setVisible(not has_jobs or not state.read_model_available)
        if self._jobs_page_label is not None:
            first = state.offset + 1 if has_jobs else 0
            last = state.offset + len(state.jobs)
            self._jobs_page_label.setText(f"{first}-{last}")
        if self._jobs_previous_button is not None:
            self._jobs_previous_button.setEnabled(state.offset > 0)
            self._jobs_previous_button.setToolTip(
                self._texts().previous_page_tooltip
            )
            self._jobs_previous_button.setAccessibleName(
                self._texts().previous_page_tooltip
            )
        if self._jobs_next_button is not None:
            self._jobs_next_button.setEnabled(state.has_more_jobs)
            self._jobs_next_button.setToolTip(self._texts().next_page_tooltip)
            self._jobs_next_button.setAccessibleName(
                self._texts().next_page_tooltip
            )
        self._apply_history_job_filter_options(state)
        self._refresh_dashboard_geometry()

    def _select_job_item(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            return
        job_id = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(job_id, str) or not job_id:
            return
        if job_id != self._selected_job_id:
            self._start_request_id = None
            self._start_idempotency_key = None
            self._analysis_request_id = None
            self._analysis_idempotency_key = None
            self._analysis_timer.stop()
        self._selected_job_id = job_id
        state = self._refresh_backup_job_detail(job_id)
        if state is None or state.plan_id is None:
            self.apply_plan_operation_preview(empty_plan_operation_preview_state())
            self.apply_plan_endpoint_preview(empty_plan_endpoint_preview_state())
            self.apply_snapshot_health_preview(empty_snapshot_health_preview_state())
            return
        self._refresh_plan_previews(state.plan_id)

    def _show_previous_jobs_page(self) -> None:
        if self._jobs_page_offset <= 0:
            return
        self._jobs_page_offset = max(0, self._jobs_page_offset - self._jobs_page_limit)
        self._selected_job_id = None
        self._refresh_backup_overview()

    def _show_next_jobs_page(self) -> None:
        if not self._backup_overview_state.has_more_jobs:
            return
        self._jobs_page_offset += self._jobs_page_limit
        self._selected_job_id = None
        self._refresh_backup_overview()

    def _refresh_history_timeline(self) -> None:
        if self._engine_client is None or not hasattr(
            self._engine_client,
            "get_history_timeline",
        ):
            self.apply_history_timeline(empty_history_timeline_state())
            return
        provider = cast(HistoryTimelineProvider, self._engine_client)
        state = history_timeline_from_response(
            provider.get_history_timeline(
                activity_filter=self._history_activity_filter,
                job_id=self._history_job_id,
                limit=self._history_page_limit,
                offset=self._history_page_offset,
            )
        )
        activity_ids = {activity.selection_key for activity in state.activities}
        if self._selected_history_activity_id not in activity_ids:
            self._selected_history_activity_id = state.selected_activity_id
        self.apply_history_timeline(state)

    def _apply_history_timeline_state(self, state: HistoryTimelineViewState) -> None:
        history_list = self._history_list
        if history_list is None:
            return
        history_list.blockSignals(True)
        history_list.clear()
        selected_row = -1
        for index, activity in enumerate(state.activities):
            kind = self._history_kind_label(activity.activity_kind)
            status = self._history_state_label(activity.state)
            started = self._format_history_timestamp(activity.started_utc)
            target_count = len(activity.targets)
            target_label = (
                f"{target_count} {self._texts().target.lower()}"
                if target_count == 1
                else f"{target_count} {self._texts().activity_targets.lower()}"
            )
            item = QListWidgetItem(
                f"{kind} · {activity.job_title}\n"
                f"{status} · {started} · {target_label}"
            )
            item.setData(Qt.ItemDataRole.UserRole, activity.selection_key)
            item.setToolTip(activity.job_id)
            item.setSizeHint(QSize(0, 58))
            history_list.addItem(item)
            if activity.selection_key == self._selected_history_activity_id:
                selected_row = index
        if selected_row >= 0:
            history_list.setCurrentRow(selected_row)
        history_list.blockSignals(False)

        has_activities = bool(state.activities)
        history_list.setVisible(state.read_model_available and has_activities)
        history_list.setEnabled(state.read_model_available)
        if self._history_empty_label is not None:
            self._history_empty_label.setText(
                self._texts().history_empty
                if state.read_model_available
                else self._texts().history_unavailable
            )
            self._history_empty_label.setVisible(
                not has_activities or not state.read_model_available
            )
        if self._history_page_label is not None:
            first = state.offset + 1 if has_activities else 0
            last = state.offset + len(state.activities)
            self._history_page_label.setText(f"{first}-{last}")
        if self._history_previous_button is not None:
            self._history_previous_button.setEnabled(state.offset > 0)
            self._history_previous_button.setToolTip(
                self._texts().previous_page_tooltip
            )
            self._history_previous_button.setAccessibleName(
                self._texts().previous_page_tooltip
            )
        if self._history_next_button is not None:
            self._history_next_button.setEnabled(state.has_more)
            self._history_next_button.setToolTip(self._texts().next_page_tooltip)
            self._history_next_button.setAccessibleName(
                self._texts().next_page_tooltip
            )
        selected = next(
            (
                activity
                for activity in state.activities
                if activity.selection_key == self._selected_history_activity_id
            ),
            None,
        )
        self._apply_history_activity_detail(selected)
        self._refresh_dashboard_geometry()

    def _select_history_item(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            return
        selection_key = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(selection_key, str) or not selection_key:
            return
        self._selected_history_activity_id = selection_key
        activity = next(
            (
                row
                for row in self._history_timeline_state.activities
                if row.selection_key == selection_key
            ),
            None,
        )
        self._apply_history_activity_detail(activity)

    def _set_history_activity_filter(self, activity_filter: str) -> None:
        if activity_filter not in {"ALL", "CONTROLS", "BACKUPS"}:
            return
        self._history_activity_filter = activity_filter
        self._history_page_offset = 0
        self._selected_history_activity_id = None
        self._refresh_history_timeline()

    def _set_history_job_filter(self, index: int) -> None:
        if self._history_job_filter is None or index < 0:
            return
        job_id = self._history_job_filter.itemData(index, Qt.ItemDataRole.UserRole)
        self._history_job_id = job_id if isinstance(job_id, str) and job_id else None
        self._history_page_offset = 0
        self._selected_history_activity_id = None
        self._refresh_history_timeline()

    def _show_previous_history_page(self) -> None:
        if self._history_page_offset <= 0:
            return
        self._history_page_offset = max(
            0,
            self._history_page_offset - self._history_page_limit,
        )
        self._selected_history_activity_id = None
        self._refresh_history_timeline()

    def _show_next_history_page(self) -> None:
        if not self._history_timeline_state.has_more:
            return
        self._history_page_offset += self._history_page_limit
        self._selected_history_activity_id = None
        self._refresh_history_timeline()

    def _apply_history_job_filter_options(
        self,
        state: BackupOverviewViewState,
    ) -> None:
        job_filter = self._history_job_filter
        if job_filter is None:
            return
        current_job_id = self._history_job_id
        job_filter.blockSignals(True)
        job_filter.clear()
        job_filter.addItem(self._texts().all_jobs, None)
        selected_index = 0
        for job in state.jobs:
            job_filter.addItem(job.title, job.job_id)
            if job.job_id == current_job_id:
                selected_index = job_filter.count() - 1
        if current_job_id is not None and selected_index == 0:
            job_filter.addItem(current_job_id, current_job_id)
            selected_index = job_filter.count() - 1
        job_filter.setCurrentIndex(selected_index)
        job_filter.setAccessibleName(self._texts().all_jobs)
        job_filter.blockSignals(False)

    def _apply_history_activity_detail(
        self,
        activity: HistoryActivityViewState | None,
    ) -> None:
        if self._history_detail_title is None:
            return
        if activity is None:
            self._history_detail_title.setText(self._texts().history_empty)
            for empty_value_label in self._history_detail_values.values():
                empty_value_label.setText("-")
            for target_row in self._history_target_rows:
                target_row.clear()
                target_row.setVisible(False)
            self._set_history_operation_activity(None)
            self._refresh_dashboard_geometry()
            return

        kind = self._history_kind_label(activity.activity_kind)
        self._history_detail_title.setText(f"{kind} · {activity.job_title}")
        finished = (
            self._format_history_timestamp(activity.finished_utc)
            if activity.finished_utc is not None
            else ("In progress" if self._selected_language_code is LanguageCode.ENGLISH else "Pågår")
        )
        if activity.activity_kind == "CONTROL":
            operations = (
                f"{activity.planned_operations} planned changes"
                if self._selected_language_code is LanguageCode.ENGLISH
                else f"{activity.planned_operations} planlagte endringer"
            )
            transferred = (
                "No transfer during a control"
                if self._selected_language_code is LanguageCode.ENGLISH
                else "Ingen overføring under en kontroll"
            )
        else:
            operations = (
                f"{activity.completed_operations} / "
                f"{activity.planned_operations}"
            )
            transferred = (
                f"{_format_bytes(activity.completed_bytes)} / "
                f"{_format_bytes(activity.planned_bytes)}"
            )
        identifiers = " · ".join(
            part
            for part in (
                f"run {activity.run_id}" if activity.run_id else None,
                f"analysis {activity.analysis_id}" if activity.analysis_id else None,
                f"plan {activity.plan_id}" if activity.plan_id else None,
                f"revision {activity.job_revision_id}",
            )
            if part is not None
        )
        values = {
            "activity_type": kind,
            "status": self._history_state_label(activity.state),
            "started": self._format_history_timestamp(activity.started_utc),
            "finished": finished,
            "duration": self._format_history_duration(activity.duration_seconds),
            "operations": operations,
            "transferred": transferred,
            "average_speed": self._format_history_average_speed(activity),
            "warnings_errors": (
                f"{activity.warning_count} / {activity.error_count}"
            ),
            "trigger": self._format_history_trigger(activity.trigger_type),
            "identifiers": identifiers,
        }
        for key, detail_text in values.items():
            detail_value_label = self._history_detail_values.get(key)
            if detail_value_label is not None:
                detail_value_label.setText(detail_text)
        for index, target_row in enumerate(self._history_target_rows):
            if index >= len(activity.targets):
                target_row.clear()
                target_row.setVisible(False)
                continue
            target = activity.targets[index]
            target_detail = self._target_state_label(target.state)
            if activity.activity_kind == "BACKUP":
                target_detail = (
                    f"{target_detail} · "
                    f"{target.completed_operations}/{target.planned_operations} · "
                    f"{_format_bytes(target.completed_bytes)}"
                )
            target_row.setText(f"{target.endpoint_id} · {target_detail}")
            target_row.setVisible(True)
        self._set_history_operation_activity(activity)
        self._refresh_dashboard_geometry()

    def _set_history_operation_activity(
        self,
        activity: HistoryActivityViewState | None,
    ) -> None:
        if (
            activity is None
            or activity.activity_kind != "BACKUP"
            or activity.run_id is None
            or activity.plan_id is None
        ):
            self._history_operation_activity_key = None
            self._history_operation_run_id = None
            self._history_operation_plan_id = None
            self._history_operation_page_state = empty_plan_operation_preview_state()
            self._history_operation_audit_state = empty_operation_audit_state()
            self._selected_history_operation_id = None
            self._history_operation_page_index = 0
            self._history_operation_page_cursors = [None]
            self._set_history_operation_widgets_visible(False)
            return

        activity_key = f"{activity.run_id}:{activity.plan_id}"
        self._set_history_operation_widgets_visible(True)
        if activity_key == self._history_operation_activity_key:
            if (
                self._selected_navigation_index == 2
                and self._history_operation_page_state.plan_id != activity.plan_id
            ):
                self._refresh_history_operation_page()
            else:
                self._apply_history_operation_page_state(
                    self._history_operation_page_state
                )
            return

        self._history_operation_activity_key = activity_key
        self._history_operation_run_id = activity.run_id
        self._history_operation_plan_id = activity.plan_id
        self._history_operation_page_index = 0
        self._history_operation_page_cursors = [None]
        self._selected_history_operation_id = None
        self._history_operation_audit_state = empty_operation_audit_state(
            run_id=activity.run_id
        )
        if self._selected_navigation_index == 2:
            self._refresh_history_operation_page()
        else:
            self._apply_history_operation_page_state(
                self._history_operation_page_state
            )

    def _refresh_history_operation_page(self) -> None:
        plan_id = self._history_operation_plan_id
        if (
            plan_id is None
            or self._engine_client is None
            or not hasattr(self._engine_client, "get_plan_operations")
        ):
            self._history_operation_page_state = empty_plan_operation_preview_state()
            self._apply_history_operation_page_state(
                self._history_operation_page_state
            )
            return
        provider = cast(PlanOperationsProvider, self._engine_client)
        cursor = self._history_operation_page_cursors[
            self._history_operation_page_index
        ]
        self._history_operation_page_state = plan_operation_preview_from_response(
            provider.get_plan_operations(
                plan_id=plan_id,
                limit=self._history_operation_page_limit,
                after=cursor,
            )
        )
        self._apply_history_operation_page_state(self._history_operation_page_state)

    def _apply_history_operation_page_state(
        self,
        state: PlanOperationPreviewState,
    ) -> None:
        operation_list = self._history_operation_list
        if operation_list is None:
            return
        operation_list.blockSignals(True)
        operation_list.clear()
        selected_row = -1
        for index, row in enumerate(state.rows):
            path = row.target_relative_path or row.operation_id
            operation_type = self._history_operation_type_label(row.operation_type)
            target = row.target_endpoint_id or self._texts().target
            item = QListWidgetItem(
                f"{operation_type} · {path}\n"
                f"{target} · {_format_bytes(row.planned_bytes)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, row.operation_id)
            item.setToolTip(path)
            item.setSizeHint(QSize(0, 58))
            operation_list.addItem(item)
            if row.operation_id == self._selected_history_operation_id:
                selected_row = index

        operation_ids = {row.operation_id for row in state.rows}
        if self._selected_history_operation_id not in operation_ids:
            self._selected_history_operation_id = (
                state.rows[0].operation_id if state.rows else None
            )
            selected_row = 0 if state.rows else -1
        if selected_row >= 0:
            operation_list.setCurrentRow(selected_row)
        operation_list.blockSignals(False)

        has_rows = bool(state.rows)
        operation_list.setVisible(state.read_model_available and has_rows)
        operation_list.setEnabled(state.read_model_available)
        self._set_history_operation_detail_widgets_visible(
            state.read_model_available and has_rows
        )
        if self._history_operation_empty_label is not None:
            self._history_operation_empty_label.setText(
                self._texts().file_results_empty
                if state.read_model_available
                else self._texts().file_results_unavailable
            )
            self._history_operation_empty_label.setVisible(
                not has_rows or not state.read_model_available
            )
        first = (
            self._history_operation_page_index * self._history_operation_page_limit
            + 1
            if has_rows
            else 0
        )
        last = first + len(state.rows) - 1 if has_rows else 0
        if self._history_operation_page_label is not None:
            self._history_operation_page_label.setText(f"{first}-{last}")
        if self._history_operation_previous_button is not None:
            self._history_operation_previous_button.setEnabled(
                self._history_operation_page_index > 0
            )
            self._history_operation_previous_button.setToolTip(
                self._texts().previous_page_tooltip
            )
            self._history_operation_previous_button.setAccessibleName(
                self._texts().previous_page_tooltip
            )
        if self._history_operation_next_button is not None:
            self._history_operation_next_button.setEnabled(
                state.has_more_operations and state.next_cursor is not None
            )
            self._history_operation_next_button.setToolTip(
                self._texts().next_page_tooltip
            )
            self._history_operation_next_button.setAccessibleName(
                self._texts().next_page_tooltip
            )

        operation_id = self._selected_history_operation_id
        run_id = self._history_operation_run_id
        if operation_id is None or run_id is None:
            self._history_operation_audit_state = empty_operation_audit_state(
                run_id=run_id,
                operation_id=operation_id,
            )
            self._apply_history_operation_audit_state(
                self._history_operation_audit_state
            )
        elif (
            self._history_operation_audit_state.run_id == run_id
            and self._history_operation_audit_state.operation_id == operation_id
        ):
            self._apply_history_operation_audit_state(
                self._history_operation_audit_state
            )
        else:
            self._refresh_history_operation_audit(operation_id)
        self._refresh_dashboard_geometry()

    def _select_history_operation(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            return
        operation_id = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(operation_id, str) or not operation_id:
            return
        if operation_id == self._selected_history_operation_id:
            return
        self._selected_history_operation_id = operation_id
        self._refresh_history_operation_audit(operation_id)

    def _refresh_history_operation_audit(self, operation_id: str) -> None:
        run_id = self._history_operation_run_id
        if (
            run_id is None
            or self._engine_client is None
            or not hasattr(self._engine_client, "get_operation_audit")
        ):
            self._history_operation_audit_state = empty_operation_audit_state(
                run_id=run_id,
                operation_id=operation_id,
            )
        else:
            provider = cast(OperationAuditProvider, self._engine_client)
            self._history_operation_audit_state = operation_audit_from_response(
                provider.get_operation_audit(
                    run_id=run_id,
                    operation_id=operation_id,
                    limit=25,
                )
            )
        self._apply_history_operation_audit_state(
            self._history_operation_audit_state
        )

    def _apply_history_operation_audit_state(
        self,
        state: OperationAuditViewState,
    ) -> None:
        detail_title = self._history_operation_detail_title
        attempt_list = self._history_attempt_list
        if detail_title is None or attempt_list is None:
            return
        if not state.read_model_available:
            detail_title.setText(self._texts().file_results_unavailable)
        elif not state.found:
            detail_title.setText(self._texts().file_audit_not_found)
        else:
            detail_title.setText(
                state.target_relative_path or state.operation_id or self._texts().file_result
            )

        outcome = state.outcome
        last_attempt_error = next(
            (
                attempt.error_code
                for attempt in reversed(state.attempts)
                if attempt.error_code is not None
            ),
            None,
        )
        values = {
            "result": (
                self._history_file_state_label(outcome.final_state)
                if outcome is not None
                else self._texts().no_terminal_outcome
            ),
            "finished": (
                self._format_history_timestamp(outcome.completed_utc)
                if outcome is not None
                else "-"
            ),
            "transferred": (
                _format_bytes(outcome.bytes_transferred)
                if outcome is not None
                else "-"
            ),
            "verification": (
                self._history_evidence_label(outcome.assurance_level)
                if outcome is not None
                else "-"
            ),
            "durability": (
                self._history_evidence_label(outcome.durability_level)
                if outcome is not None
                else "-"
            ),
            "attempts": str(len(state.attempts)) if state.found else "-",
            "last_error": (
                (outcome.error_code if outcome is not None else None)
                or last_attempt_error
                or self._texts().no_error
            ),
        }
        for key, text in values.items():
            label = self._history_operation_detail_values.get(key)
            if label is not None:
                label.setText(text)

        attempt_list.clear()
        for attempt in state.attempts:
            result = self._history_file_state_label(attempt.state)
            finished = self._format_history_timestamp(attempt.finished_utc)
            detail = attempt.error_code or _format_bytes(attempt.bytes_transferred)
            prefix = "Attempt" if self._selected_language_code is LanguageCode.ENGLISH else "Forsøk"
            item = QListWidgetItem(
                f"{prefix} {attempt.attempt_number} · {result}\n"
                f"{finished} · {detail}"
            )
            item.setToolTip(attempt.error_code or result)
            item.setSizeHint(QSize(0, 54))
            attempt_list.addItem(item)
        attempt_list.setVisible(bool(state.attempts))
        if self._history_attempt_heading is not None:
            self._history_attempt_heading.setVisible(bool(state.attempts))
        self._refresh_dashboard_geometry()

    def _show_previous_history_operation_page(self) -> None:
        if self._history_operation_page_index <= 0:
            return
        self._history_operation_page_index -= 1
        self._selected_history_operation_id = None
        self._history_operation_audit_state = empty_operation_audit_state(
            run_id=self._history_operation_run_id
        )
        self._refresh_history_operation_page()

    def _show_next_history_operation_page(self) -> None:
        state = self._history_operation_page_state
        if not state.has_more_operations or state.next_cursor is None:
            return
        next_index = self._history_operation_page_index + 1
        if next_index == len(self._history_operation_page_cursors):
            self._history_operation_page_cursors.append(state.next_cursor)
        else:
            self._history_operation_page_cursors[next_index] = state.next_cursor
            del self._history_operation_page_cursors[next_index + 1 :]
        self._history_operation_page_index = next_index
        self._selected_history_operation_id = None
        self._history_operation_audit_state = empty_operation_audit_state(
            run_id=self._history_operation_run_id
        )
        self._refresh_history_operation_page()

    def _set_history_operation_widgets_visible(self, visible: bool) -> None:
        widgets: tuple[QWidget | None, ...] = (
            self._history_operation_header,
            self._history_operation_heading,
            self._history_operation_page_label,
            self._history_operation_previous_button,
            self._history_operation_next_button,
            self._history_operation_list,
            self._history_operation_empty_label,
        )
        for widget in widgets:
            if widget is not None:
                widget.setVisible(visible)
        if not visible:
            self._set_history_operation_detail_widgets_visible(False)

    def _set_history_operation_detail_widgets_visible(self, visible: bool) -> None:
        widgets: tuple[QWidget | None, ...] = (
            self._history_operation_detail_title,
            self._history_attempt_heading,
            self._history_attempt_list,
            *self._history_operation_detail_labels.values(),
            *self._history_operation_detail_values.values(),
        )
        for widget in widgets:
            if widget is not None:
                widget.setVisible(visible)

    def _history_operation_type_label(self, operation_type: str | None) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "COPY_NEW": ("Copy new", "Kopier ny"),
            "CREATE_DIRECTORY": ("Create folder", "Opprett mappe"),
            "REPLACE_VERSIONED": ("Replace with version", "Erstatt med versjon"),
            "QUARANTINE": ("Quarantine", "Karantene"),
        }
        if operation_type is None:
            return "Operation" if english else "Operasjon"
        pair = labels.get(
            operation_type,
            (operation_type.replace("_", " ").title(), operation_type),
        )
        return pair[0] if english else pair[1]

    def _history_file_state_label(self, state: str) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "SUCCEEDED": ("Completed", "Fullført"),
            "FAILED": ("Failed", "Feilet"),
            "SKIPPED": ("Skipped", "Hoppet over"),
        }
        pair = labels.get(state, (state.replace("_", " ").title(), state))
        return pair[0] if english else pair[1]

    def _history_evidence_label(self, value: str) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "FULL_HASH": ("Full content hash", "Full innholdshash"),
            "DURABLE": ("Durably written", "Varig skrevet"),
            "NOT_RECORDED": ("Not recorded", "Ikke registrert"),
        }
        pair = labels.get(value, (value.replace("_", " ").title(), value))
        return pair[0] if english else pair[1]

    def _history_kind_label(self, activity_kind: str) -> str:
        return (
            self._texts().activity_control
            if activity_kind == "CONTROL"
            else self._texts().activity_backup
        )

    def _history_state_label(self, state: str) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "SEALED": ("Ready for backup", "Klar for backup"),
            "NO_CHANGES": ("No changes", "Ingen endringer"),
            "BLOCKED": ("Action required", "Handling nødvendig"),
            "FAILED": ("Failed", "Feilet"),
            "CREATED": ("Created", "Opprettet"),
            "QUEUED": ("Queued", "I kø"),
            "PREFLIGHT": ("Checking targets", "Kontrollerer mål"),
            "EXECUTING": ("Running", "Kjører"),
            "PAUSING": ("Pausing", "Pauser"),
            "PAUSED": ("Paused", "Pauset"),
            "COMPLETED": ("Completed", "Fullført"),
            "COMPLETED_WITH_WARNINGS": (
                "Completed with warnings",
                "Fullført med varsler",
            ),
            "PARTIAL_FAILURE": ("Partially completed", "Delvis fullført"),
            "CANCELLED": ("Cancelled", "Avbrutt"),
            "BLOCKED_BY_SAFETY": ("Blocked by safety", "Blokkert av sikkerhet"),
            "RECOVERY_REQUIRED": (
                "Recovery required",
                "Gjenoppretting kreves",
            ),
        }
        pair = labels.get(state, (state.replace("_", " ").title(), state))
        return pair[0] if english else pair[1]

    def _target_state_label(self, state: str) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "WRITABLE_READY": ("Writable and ready", "Skrivbar og klar"),
            "REGISTRATION_PENDING": (
                "Registration pending",
                "Registrering venter",
            ),
            "READ_ONLY_READY": ("Read-only", "Skrivebeskyttet"),
            "PENDING": ("Pending", "Venter"),
            "ACQUIRING_LEASE": ("Acquiring access", "Henter tilgang"),
            "REVALIDATING": ("Checking", "Kontrollerer"),
            "EXECUTING": ("Running", "Kjører"),
            "PAUSED": ("Paused", "Pauset"),
            "WAITING_FOR_ENDPOINT": ("Waiting for target", "Venter på mål"),
            "NEEDS_REVIEW": ("Needs review", "Må vurderes"),
            "SUCCEEDED": ("Completed", "Fullført"),
            "SUCCEEDED_WITH_WARNINGS": (
                "Completed with warnings",
                "Fullført med varsler",
            ),
            "FAILED": ("Failed", "Feilet"),
            "CANCELLED": ("Cancelled", "Avbrutt"),
            "BLOCKED": ("Blocked", "Blokkert"),
            "RECOVERY_REQUIRED": (
                "Recovery required",
                "Gjenoppretting kreves",
            ),
        }
        pair = labels.get(state, (state.replace("_", " ").title(), state))
        return pair[0] if english else pair[1]

    def _format_endpoint_retry_time(self, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone().strftime("%H:%M:%S")

    def _endpoint_wait_reason_label(self, reason_code: str) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "NETWORK_INTERRUPTED": (
                "Network connection interrupted",
                "Nettverksforbindelsen ble avbrutt",
            ),
        }
        pair = labels.get(reason_code)
        if pair is None:
            return reason_code
        label = pair[0] if english else pair[1]
        return f"{label} ({reason_code})"

    def _run_operation_phase_label(self, phase: str | None) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "PLANNED": ("Waiting", "Venter"),
            "SOURCE_VALIDATED": ("Checking source", "Kontrollerer kilde"),
            "SOURCE_STABILITY_BOUND": ("Checking source", "Kontrollerer kilde"),
            "TARGET_PRECONDITION_VALIDATED": ("Preparing target", "Klargjør mål"),
            "STAGING_ALLOCATED": ("Copying", "Kopierer"),
            "TRANSFERRED": ("Making copy durable", "Sikrer kopien"),
            "STAGING_DURABLE": ("Verifying copy", "Verifiserer kopi"),
            "STAGING_VERIFIED": ("Safely placing file", "Setter inn fil trygt"),
            "COMMIT_INTENT_RECORDED": ("Safely placing file", "Setter inn fil trygt"),
            "COMMIT_PRECONDITIONS_REVALIDATED": (
                "Safely placing file",
                "Setter inn fil trygt",
            ),
            "OLD_TARGET_PRESERVED": ("Preserving version", "Bevarer versjon"),
            "FILESYSTEM_APPLIED": ("Verifying final file", "Verifiserer sluttfil"),
            "FINAL_DURABLE": ("Verifying final file", "Verifiserer sluttfil"),
            "FINAL_VERIFIED": ("Recording result", "Registrerer resultat"),
            "CATALOG_RECORDED": ("Cleaning temporary data", "Rydder midlertidige data"),
        }
        if phase is None:
            return "Working" if english else "Arbeider"
        pair = labels.get(phase, (phase.replace("_", " ").title(), phase))
        return pair[0] if english else pair[1]

    def _format_history_timestamp(self, value: str) -> str:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone()
        if self._selected_language_code is LanguageCode.ENGLISH:
            return timestamp.strftime("%Y-%m-%d %H:%M")
        return timestamp.strftime("%d.%m.%Y %H:%M")

    def _format_history_duration(self, seconds: int | None) -> str:
        if seconds is None:
            return "-"
        minutes, remaining_seconds = divmod(seconds, 60)
        hours, remaining_minutes = divmod(minutes, 60)
        if hours:
            return f"{hours} h {remaining_minutes} min"
        if minutes:
            return f"{minutes} min {remaining_seconds} s"
        return f"{remaining_seconds} s"

    def _format_history_average_speed(
        self,
        activity: HistoryActivityViewState,
    ) -> str:
        if (
            activity.activity_kind != "BACKUP"
            or activity.completed_bytes < 1
            or activity.duration_seconds is None
            or activity.duration_seconds < 1
        ):
            return "-"
        return f"{_format_bytes(activity.completed_bytes // activity.duration_seconds)}/s"

    def _format_history_trigger(self, trigger_type: str) -> str:
        english = self._selected_language_code is LanguageCode.ENGLISH
        labels = {
            "MANUAL_LOCAL_PREVIEW": ("Manual", "Manuell"),
            "INITIAL_JOB_SETUP": (
                "Job creation control",
                "Kontroll ved jobboppretting",
            ),
            "MANUAL_BACKUP_CHECK": (
                "Manual backup check",
                "Manuell backupkontroll",
            ),
        }
        pair = labels.get(
            trigger_type,
            (trigger_type.replace("_", " ").title(), trigger_type),
        )
        return pair[0] if english else pair[1]

    def _refresh_activity_overview(self) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_activity_overview"):
            return
        provider = cast(ActivityOverviewProvider, self._engine_client)
        state = activity_overview_from_response(
            provider.get_activity_overview(job_id=self._selected_job_id)
        )
        self._latest_run_job_id = state.latest_job_id
        self._latest_run_plan_id = state.latest_plan_id
        self._latest_run_state = state.latest_run_state
        if state.job_status is not None:
            self._job_status_state = state.job_status
            self._apply_job_status_state(state.job_status)
        if state.active_run_id is not None:
            self._set_active_run(state.active_run_id)
            self._poll_active_run_progress()
        elif self._run_progress_state.terminal:
            self._active_run_id = None
            self._run_progress_timer.stop()
        plan_id = state.latest_plan_id or self._job_detail_state.plan_id
        self._apply_backup_job_detail_state(self._job_detail_state)
        if plan_id is None:
            self.apply_plan_operation_preview(empty_plan_operation_preview_state())
            self.apply_plan_endpoint_preview(empty_plan_endpoint_preview_state())
            self.apply_snapshot_health_preview(empty_snapshot_health_preview_state())
            return
        self._refresh_plan_previews(plan_id)

    def _set_active_run(self, run_id: str) -> None:
        if run_id != self._active_run_id:
            self._active_run_id = run_id
            self._run_progress_state = empty_run_progress_state()
        if not self._run_progress_timer.isActive():
            self._run_progress_timer.start()

    def _poll_active_run_progress(self) -> None:
        run_id = self._active_run_id
        if (
            run_id is None
            or self._engine_client is None
            or not hasattr(self._engine_client, "get_run_progress")
        ):
            self._run_progress_timer.stop()
            return
        provider = cast(RunProgressProvider, self._engine_client)
        response = provider.get_run_progress(
            run_id=run_id,
            after_sequence_no=self._run_progress_state.sequence_no,
        )
        state = run_progress_from_response(
            response,
            previous=self._run_progress_state,
        )
        self._run_progress_state = state
        self._apply_run_progress_state(state)
        if not state.run_found:
            return
        if state.terminal:
            self._run_progress_timer.stop()
            self._active_run_id = None
            if state.job_id is not None:
                self._queued_backup_job_ids.discard(state.job_id)
            self._refresh_backup_overview()
            self._refresh_activity_overview()
            self._refresh_history_timeline()

    def _apply_run_progress_state(self, state: RunProgressViewState) -> None:
        visible = (
            state.run_found
            and state.job_id is not None
            and state.job_id == self._job_detail_state.job_id
        )
        widgets = (
            self._jobs_run_progress_title,
            self._jobs_run_progress_state,
            self._jobs_run_progress_bar,
            self._jobs_run_progress_detail,
            self._jobs_run_active_file,
        )
        for widget in widgets:
            if widget is not None:
                widget.setVisible(visible)
        if not visible:
            for row in self._jobs_run_target_rows:
                row.setVisible(False)
            if self._jobs_pause_button is not None:
                self._jobs_pause_button.setVisible(False)
            if self._jobs_resume_button is not None:
                self._jobs_resume_button.setVisible(False)
            if self._jobs_stop_button is not None:
                self._jobs_stop_button.setVisible(False)
            return
        if state.active:
            for start_button in (
                self._start_backup_button,
                self._jobs_start_backup_button,
            ):
                if start_button is not None:
                    start_button.setVisible(False)
                    start_button.setEnabled(False)

        if self._jobs_run_progress_title is not None:
            self._jobs_run_progress_title.setText(self._texts().run_progress)
        if self._jobs_run_progress_state is not None:
            self._jobs_run_progress_state.setText(
                self._texts().stopping_after_active_file
                if state.stop_requested
                else self._history_state_label(state.state or "CREATED")
            )
        if self._jobs_run_progress_bar is not None:
            if state.planned_bytes > 0:
                maximum = 1000
                value = min(
                    int((state.transferred_bytes / state.planned_bytes) * maximum),
                    maximum,
                )
                if state.active and value == maximum and not state.terminal:
                    value = maximum - 1
            else:
                maximum = max(state.planned_operations, 1)
                value = min(state.completed_operations, maximum)
            self._jobs_run_progress_bar.setRange(0, maximum)
            self._jobs_run_progress_bar.setValue(value)
        if self._jobs_run_progress_detail is not None:
            details = [
                f"{state.completed_operations} / {state.planned_operations} "
                f"{self._texts().operation_count}",
                f"{_format_bytes(state.transferred_bytes)} / "
                f"{_format_bytes(state.planned_bytes)} {self._texts().transferred.lower()}",
            ]
            if state.bytes_per_second is not None:
                details.append(_format_rate(state.bytes_per_second))
            details.append(
                (
                    f"{_format_eta(state.eta_seconds)} {self._texts().remaining}"
                    if state.eta_seconds is not None
                    else self._texts().calculating_eta
                )
            )
            self._jobs_run_progress_detail.setText(" · ".join(details))
        if self._jobs_run_active_file is not None:
            active_path = state.active_relative_path
            active_visible = visible and active_path is not None
            if active_visible:
                phase = self._run_operation_phase_label(state.active_phase)
                active_size = (
                    ""
                    if state.active_planned_bytes is None
                    else f" · {_format_bytes(state.active_planned_bytes)}"
                )
                retry_detail = ""
                active_tooltip_lines = [active_path or ""]
                if (
                    state.active_retry_not_before_utc is not None
                    and state.active_staging_failure_count is not None
                ):
                    english = self._selected_language_code is LanguageCode.ENGLISH
                    attempt = state.active_staging_failure_count + 1
                    retry_time = self._format_endpoint_retry_time(
                        state.active_retry_not_before_utc
                    )
                    retry_label = "Retry" if english else "Nytt forsøk"
                    retry_detail = f" · {retry_label} {attempt}"
                    if retry_time is not None:
                        after_label = "after" if english else "etter"
                        retry_detail += f" {after_label} {retry_time}"
                    if state.active_last_error_code is not None:
                        reason_label = "Reason" if english else "Årsak"
                        active_tooltip_lines.append(
                            f"{reason_label}: {state.active_last_error_code}"
                        )
                    if state.active_retry_backoff_ms is not None:
                        backoff_label = (
                            "Scheduled backoff" if english else "Planlagt ventetid"
                        )
                        active_tooltip_lines.append(
                            f"{backoff_label}: "
                            f"{state.active_retry_backoff_ms / 1000:.1f} s"
                        )
                self._jobs_run_active_file.setText(
                    f"{self._texts().current_file}: {active_path}{active_size} "
                    f"· {phase}{retry_detail}"
                )
                self._jobs_run_active_file.setToolTip(
                    "\n".join(active_tooltip_lines)
                )
            else:
                self._jobs_run_active_file.setText("")
                self._jobs_run_active_file.setToolTip("")
            self._jobs_run_active_file.setVisible(active_visible)
        for index, row in enumerate(self._jobs_run_target_rows):
            if index >= len(state.targets):
                row.setText("")
                row.setVisible(False)
                continue
            target = state.targets[index]
            details = [
                target.endpoint_id,
                self._target_state_label(target.state),
                f"{target.completed_operations}/{target.planned_operations}",
            ]
            tooltip_lines: list[str] = []
            if (
                target.state == "WAITING_FOR_ENDPOINT"
                and target.endpoint_wait_attempts > 0
            ):
                english = self._selected_language_code is LanguageCode.ENGLISH
                attempt_label = "Attempt" if english else "Forsøk"
                details.append(f"{attempt_label} {target.endpoint_wait_attempts}")
                retry_time = self._format_endpoint_retry_time(
                    target.endpoint_retry_not_before_utc
                )
                if retry_time is not None:
                    retry_label = "next retry after" if english else "nytt forsøk etter"
                    details.append(f"{retry_label} {retry_time}")
                if target.endpoint_wait_reason_code is not None:
                    reason_label = "Reason" if english else "Årsak"
                    tooltip_lines.append(
                        f"{reason_label}: "
                        f"{self._endpoint_wait_reason_label(target.endpoint_wait_reason_code)}"
                    )
                backoff_label = "Scheduled backoff" if english else "Planlagt ventetid"
                total_label = (
                    "Total scheduled backoff"
                    if english
                    else "Samlet planlagt ventetid"
                )
                if target.endpoint_retry_backoff_ms is not None:
                    tooltip_lines.append(
                        f"{backoff_label}: "
                        f"{target.endpoint_retry_backoff_ms / 1000:.1f} s"
                    )
                tooltip_lines.append(
                    f"{total_label}: "
                    f"{target.endpoint_wait_total_backoff_ms / 1000:.1f} s"
                )
            row.setText(" · ".join(details))
            row.setToolTip("\n".join(tooltip_lines))
            row.setVisible(True)
        pausable = (
            not state.stop_requested
            and state.state in {"CREATED", "QUEUED", "PREFLIGHT", "EXECUTING"}
        )
        resumable = not state.stop_requested and state.state == "PAUSED"
        stoppable = state.active and state.state in {
            "CREATED",
            "QUEUED",
            "PREFLIGHT",
            "EXECUTING",
            "PAUSING",
            "PAUSED",
        }
        if self._jobs_pause_button is not None:
            self._jobs_pause_button.setText(self._texts().pause_backup)
            self._jobs_pause_button.setToolTip(self._texts().pause_backup_tooltip)
            self._jobs_pause_button.setVisible(pausable)
            self._jobs_pause_button.setEnabled(
                pausable
                and not self._run_control_pending
                and self._engine_client is not None
                and hasattr(self._engine_client, "pause_backup")
            )
        if self._jobs_resume_button is not None:
            self._jobs_resume_button.setText(self._texts().resume_backup)
            self._jobs_resume_button.setToolTip(self._texts().resume_backup_tooltip)
            self._jobs_resume_button.setVisible(resumable)
            self._jobs_resume_button.setEnabled(
                resumable
                and not self._run_control_pending
                and self._engine_client is not None
                and hasattr(self._engine_client, "resume_backup")
            )
        if self._jobs_stop_button is not None:
            self._jobs_stop_button.setText(
                self._texts().stopping_after_active_file
                if state.stop_requested
                else self._texts().stop_after_active_file
            )
            self._jobs_stop_button.setToolTip(
                self._texts().stop_after_active_file_tooltip
            )
            self._jobs_stop_button.setVisible(stoppable)
            self._jobs_stop_button.setEnabled(
                stoppable
                and not state.stop_requested
                and not self._run_control_pending
                and self._engine_client is not None
                and hasattr(self._engine_client, "stop_backup_after_active_file")
            )

    def _pause_active_backup(self) -> None:
        self._submit_run_control("pause")

    def _resume_active_backup(self) -> None:
        self._submit_run_control("resume")

    def _stop_active_backup_after_file(self) -> None:
        self._submit_run_control("stop")

    def _submit_run_control(self, action: str) -> None:
        run_id = self._active_run_id or self._run_progress_state.run_id
        method_name = {
            "pause": "pause_backup",
            "resume": "resume_backup",
            "stop": "stop_backup_after_active_file",
        }.get(action)
        if (
            run_id is None
            or method_name is None
            or self._engine_client is None
            or not hasattr(self._engine_client, method_name)
            or self._run_control_pending
        ):
            return
        self._run_control_pending = True
        self._apply_run_progress_state(self._run_progress_state)
        provider = cast(RunControlProvider, self._engine_client)
        request_id = str(uuid4())
        idempotency_key = str(uuid4())
        if action == "pause":
            response = provider.pause_backup(
                run_id=run_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        elif action == "resume":
            response = provider.resume_backup(
                run_id=run_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        else:
            response = provider.stop_backup_after_active_file(
                run_id=run_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        self._run_control_pending = False
        if response.status is IpcStatus.REJECTED:
            codes = response.payload.get("validation_codes")
            reason = (
                str(codes[0])
                if isinstance(codes, list) and codes
                else response.reason.value
                if response.reason is not None
                else "UNKNOWN"
            )
            self.apply_engine_status(
                replace(
                    self._engine_status_state,
                    detail=f"Run control failed: {reason}",
                    status_kind="warning",
                )
            )
            self._apply_run_progress_state(self._run_progress_state)
            return
        self._set_active_run(run_id)
        self._poll_active_run_progress()

    def _refresh_plan_previews(self, plan_id: str) -> None:
        self._refresh_plan_operation_preview(plan_id)
        endpoint_state = self._refresh_plan_endpoint_preview(plan_id)
        if endpoint_state.source_snapshot_id is None:
            self.apply_snapshot_health_preview(empty_snapshot_health_preview_state())
            return
        self._refresh_snapshot_health_preview(endpoint_state.source_snapshot_id)

    def _refresh_plan_operation_preview(self, plan_id: str) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_plan_operations"):
            return
        provider = cast(PlanOperationsProvider, self._engine_client)
        self.apply_plan_operation_preview(
            plan_operation_preview_from_response(provider.get_plan_operations(plan_id=plan_id, limit=3))
        )

    def _refresh_plan_endpoint_preview(self, plan_id: str) -> PlanEndpointPreviewState:
        if self._engine_client is None or not hasattr(self._engine_client, "get_plan_endpoints"):
            state = empty_plan_endpoint_preview_state()
            self.apply_plan_endpoint_preview(state)
            return state
        provider = cast(PlanEndpointsProvider, self._engine_client)
        state = plan_endpoint_preview_from_response(provider.get_plan_endpoints(plan_id=plan_id, limit=4))
        self.apply_plan_endpoint_preview(state)
        return state

    def _refresh_snapshot_health_preview(self, snapshot_id: str) -> None:
        if (
            self._engine_client is None
            or not hasattr(self._engine_client, "get_snapshot_issues")
            or not hasattr(self._engine_client, "get_snapshot_coverage")
        ):
            self.apply_snapshot_health_preview(empty_snapshot_health_preview_state())
            return
        provider = cast(SnapshotHealthProvider, self._engine_client)
        self.apply_snapshot_health_preview(
            snapshot_health_preview_from_responses(
                snapshot_id=snapshot_id,
                blocking_issues_response=provider.get_snapshot_issues(
                    snapshot_id=snapshot_id,
                    limit=2,
                    blocking_only=True,
                ),
                coverage_response=provider.get_snapshot_coverage(
                    snapshot_id=snapshot_id,
                    limit=2,
                    coverage_states=SNAPSHOT_HEALTH_COVERAGE_STATES,
                ),
            )
        )

    def _refresh_cataloged_files_preview(self) -> None:
        if self._engine_client is None or not hasattr(self._engine_client, "get_cataloged_files"):
            self.apply_cataloged_files_preview(empty_cataloged_files_preview_state())
            return
        provider = cast(CatalogedFilesProvider, self._engine_client)
        self.apply_cataloged_files_preview(
            cataloged_files_preview_from_response(provider.get_cataloged_files(limit=3))
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
        self._apply_setup_target_controls(state)
        if self._setup_back_button is not None:
            can_go_back = self._setup_can_go_back(state)
            self._setup_back_button.setVisible(can_go_back)
            self._setup_back_button.setEnabled(can_go_back)
            self._setup_back_button.setToolTip(self._texts().back_tooltip)
            self._setup_back_button.setAccessibleName(self._texts().back_tooltip)
        if self._setup_primary_button is not None:
            action_label = (
                "Choose source folder"
                if state.current_step is BackupSetupStep.SOURCE
                else state.primary_action_label
            )
            self._setup_primary_button.setText(self._display(action_label))
            self._setup_primary_button.setEnabled(self._setup_primary_enabled(state))
            self._setup_primary_button.setToolTip(self._setup_primary_tooltip(state))
        self._refresh_dashboard_geometry()
        QTimer.singleShot(0, self._refresh_dashboard_geometry)

    def _setup_primary_enabled(self, state: StandardBackupSetupViewState) -> bool:
        if state.current_step is BackupSetupStep.SOURCE:
            return True
        return state.can_continue

    def _setup_can_go_back(self, state: StandardBackupSetupViewState) -> bool:
        return (
            state.current_step is not BackupSetupStep.SOURCE
            and self._setup_request_id is None
            and self._setup_draft.source_path_label == state.source_label
            and len(self._setup_draft.targets) == state.configured_targets
        )

    def _setup_primary_tooltip(self, state: StandardBackupSetupViewState) -> str:
        if state.current_step is BackupSetupStep.SOURCE:
            return self._display("Choose a source folder.")
        if state.current_step is BackupSetupStep.TARGETS:
            return self._display("Continue with selected target folders.")
        if state.current_step is BackupSetupStep.DEFAULTS:
            return self._display("Review safe defaults.")
        return self._texts().create_backup_tooltip

    def _handle_setup_primary_action(self) -> None:
        step = self._setup_state.current_step
        if step is BackupSetupStep.SOURCE:
            selected = self._choose_directory(self._display("Choose source folder"))
            if selected is None:
                return
            self._setup_draft = BackupSetupDraft(
                source_name=_display_name_for_path(selected),
                source_path_label=selected,
                targets=self._setup_draft.targets,
            )
            self._apply_local_setup_draft(
                BackupSetupStep.TARGETS,
                reveal_action=False,
            )
            self._apply_local_preview_job_detail()
            return
        if step is BackupSetupStep.TARGETS:
            if not self._setup_state.can_continue:
                return
            self._apply_local_setup_draft(BackupSetupStep.DEFAULTS)
            self._apply_local_preview_job_detail()
            return
        if step is BackupSetupStep.DEFAULTS:
            self._apply_local_setup_draft(BackupSetupStep.REVIEW)
            self._apply_local_preview_job_detail()
            return
        if self._setup_state.can_create:
            if self._engine_client is None:
                self.apply_engine_status(
                    EngineStatusViewState.disconnected(
                        "Local preview draft is ready. Connect an Engine Host before creating durable backup changes."
                    )
                )
            elif not hasattr(self._engine_client, "create_standard_backup_job"):
                self.apply_engine_status(
                    replace(
                        self._engine_status_state,
                        detail="Connected Engine Host does not support backup creation.",
                        status_kind="warning",
                    )
                )
            else:
                self._create_standard_backup_job()
                return
            self._apply_local_preview_job_detail()

    def _add_setup_target(self) -> None:
        if (
            self._setup_state.current_step is not BackupSetupStep.TARGETS
            or len(self._setup_draft.targets) >= self._setup_state.max_targets
        ):
            return
        selected = self._choose_directory(self._display("Choose target folder"))
        if selected is None:
            return
        selected_key = _path_identity(selected)
        if any(
            _path_identity(target.path_label) == selected_key
            for target in self._setup_draft.targets
        ):
            return
        self._setup_draft = BackupSetupDraft(
            source_name=self._setup_draft.source_name,
            source_path_label=self._setup_draft.source_path_label,
            targets=(
                *self._setup_draft.targets,
                BackupTargetDraft(
                    name=_display_name_for_path(selected),
                    path_label=selected,
                ),
            ),
        )
        self._apply_local_setup_draft(
            BackupSetupStep.TARGETS,
            reveal_action=False,
        )
        self._apply_local_preview_job_detail()

    def _remove_setup_target(self, index: int) -> None:
        if (
            self._setup_state.current_step is not BackupSetupStep.TARGETS
            or not 0 <= index < len(self._setup_draft.targets)
        ):
            return
        self._setup_draft = BackupSetupDraft(
            source_name=self._setup_draft.source_name,
            source_path_label=self._setup_draft.source_path_label,
            targets=(
                *self._setup_draft.targets[:index],
                *self._setup_draft.targets[index + 1 :],
            ),
        )
        self._apply_local_setup_draft(
            BackupSetupStep.TARGETS,
            reveal_action=False,
        )
        self._apply_local_preview_job_detail()

    def _handle_setup_back_action(self) -> None:
        if self._setup_request_id is not None:
            return
        previous_step = {
            BackupSetupStep.TARGETS: BackupSetupStep.SOURCE,
            BackupSetupStep.DEFAULTS: BackupSetupStep.TARGETS,
            BackupSetupStep.REVIEW: BackupSetupStep.DEFAULTS,
        }.get(self._setup_state.current_step)
        if previous_step is not None:
            self._apply_local_setup_draft(previous_step)
            self._apply_local_preview_job_detail()

    def _apply_setup_target_controls(
        self,
        state: StandardBackupSetupViewState,
    ) -> None:
        controls = self._setup_target_controls
        add_button = self._setup_add_target_button
        if controls is None or add_button is None:
            return
        editing_targets = state.current_step is BackupSetupStep.TARGETS
        controls.setVisible(editing_targets)
        add_button.setVisible(editing_targets)
        add_button.setEnabled(
            editing_targets and state.configured_targets < state.max_targets
        )
        add_button.setToolTip(self._texts().add_target_tooltip)
        add_button.setAccessibleName(self._texts().add_target_tooltip)
        for index, (row, label, button) in enumerate(
            zip(
                self._setup_target_rows,
                self._setup_target_path_labels,
                self._setup_remove_target_buttons,
                strict=True,
            )
        ):
            has_target = index < len(self._setup_draft.targets)
            row.setVisible(editing_targets and has_target)
            if not has_target:
                label.clear()
                continue
            target = self._setup_draft.targets[index]
            label.setText(f"{target.name}: {target.path_label}")
            remove_tooltip = f"{self._texts().remove_target_tooltip}: {target.name}"
            button.setToolTip(remove_tooltip)
            button.setAccessibleName(remove_tooltip)
        controls_layout = controls.layout()
        if controls_layout is not None:
            controls_layout.invalidate()
            controls_layout.activate()
            controls.setMinimumHeight(
                controls_layout.minimumSize().height() if editing_targets else 0
            )
        controls.updateGeometry()

    def _create_standard_backup_job(self) -> None:
        if self._engine_client is None:
            return
        source_name = self._setup_draft.source_name
        source_path_label = self._setup_draft.source_path_label
        if source_name is None or source_path_label is None:
            return
        if not self._connected:
            handshake = self._engine_client.connect()
            if handshake.reason is not None:
                self.apply_engine_status(engine_status_from_response(handshake))
                return
            self._connected = True

        self._setup_draft_id = self._setup_draft_id or str(uuid4())
        self._setup_request_id = self._setup_request_id or str(uuid4())
        self._setup_idempotency_key = self._setup_idempotency_key or str(uuid4())
        if self._setup_back_button is not None:
            self._setup_back_button.hide()
        draft = StandardBackupJobDraft(
            draft_id=self._setup_draft_id,
            source_name=source_name,
            source_path_label=source_path_label,
            targets=tuple(
                DraftTarget(
                    name=target.name,
                    path_label=target.path_label,
                    independent_device_id=target.independent_device_id,
                )
                for target in self._setup_draft.targets
            ),
        )
        provider = cast(BackupJobCreationProvider, self._engine_client)
        response = provider.create_standard_backup_job(
            draft=draft,
            request_id=self._setup_request_id,
            idempotency_key=self._setup_idempotency_key,
        )
        if response.status is IpcStatus.REJECTED:
            reason = response.reason.value if response.reason is not None else "UNKNOWN"
            self.apply_engine_status(
                replace(
                    self._engine_status_state,
                    detail=f"Backup creation failed: {reason}",
                    status_kind="warning",
                )
            )
            return

        registration = response.payload.get("writable_endpoint_registration")
        registration_incomplete = (
            isinstance(registration, dict)
            and registration.get("completed") is not True
        )
        success_detail = "Backup job and writable target registration were saved."
        success_kind = "ready"
        if registration_incomplete:
            assert isinstance(registration, dict)
            codes = registration.get("validation_codes")
            reason = (
                str(codes[0])
                if isinstance(codes, list) and codes and isinstance(codes[0], str)
                else "WRITABLE_ENDPOINT_REGISTRATION_INCOMPLETE"
            )
            success_detail = (
                f"{self._display('Backup job was saved, but target registration needs attention.')} "
                f"{reason}"
            )
            success_kind = "warning"
            if self._setup_primary_button is not None:
                self._setup_primary_button.setText(
                    self._display("Prøv målregistrering igjen")
                )
        self.apply_engine_status(
            replace(
                self._engine_status_state,
                detail=success_detail,
                status_kind=success_kind,
            )
        )
        if registration_incomplete:
            self._refresh_backup_job_detail_from_response(response)
            self._refresh_activity_overview()
            self._refresh_dashboard_geometry()
            return
        self._setup_draft = BackupSetupDraft.empty()
        self._setup_draft_id = None
        self._setup_request_id = None
        self._setup_idempotency_key = None
        self._selected_job_id = None
        self._jobs_page_offset = 0
        self._refresh_backup_overview()
        self._refresh_activity_overview()

    def _refresh_backup_job_detail_from_response(self, response: IpcResponse) -> None:
        job = response.payload.get("job")
        if not isinstance(job, dict):
            return
        job_id = job.get("job_id")
        if isinstance(job_id, str):
            self._refresh_backup_job_detail(job_id)

    def _choose_directory(self, title: str) -> str | None:
        dialog = self._build_directory_picker(title)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        selected = dialog.selectedFiles()
        return selected[0] if selected else None

    def _build_directory_picker(self, title: str) -> QFileDialog:
        dialog = QFileDialog(self)
        dialog.setObjectName("directoryPickerDialog")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setModal(True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setWindowTitle(title)
        dialog.setDirectory(str(Path.home()))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        return dialog

    def _apply_local_setup_draft(
        self,
        step: BackupSetupStep,
        *,
        reveal_action: bool = True,
    ) -> None:
        self._setup_state = build_standard_backup_setup_state(
            self._setup_draft,
            current_step=step,
        )
        self._apply_backup_setup_state(self._setup_state)
        settle_layout = (
            self._settle_setup_layout
            if reveal_action
            else self._refresh_dashboard_geometry
        )
        QTimer.singleShot(0, settle_layout)

    def _settle_setup_layout(self) -> None:
        self._refresh_dashboard_geometry()
        self._ensure_setup_action_visible()

    def _ensure_setup_action_visible(self) -> None:
        if self._dashboard_scroll_area is None or self._setup_primary_button is None:
            return
        scroll_area = self._dashboard_scroll_area
        page = scroll_area.widget()
        if page is None:
            return
        margin = 12
        button_top = self._setup_primary_button.mapTo(
            page,
            self._setup_primary_button.rect().topLeft(),
        ).y()
        button_bottom = (
            self._setup_primary_button.mapTo(
                page,
                self._setup_primary_button.rect().bottomLeft(),
            ).y()
            + 1
        )
        scroll_bar = scroll_area.verticalScrollBar()
        viewport_height = scroll_area.viewport().height()
        current_value = scroll_bar.value()
        if button_bottom - current_value > viewport_height - margin:
            scroll_bar.setValue(button_bottom - viewport_height + margin)
        elif button_top - current_value < margin:
            scroll_bar.setValue(max(0, button_top - margin))

    def _apply_local_preview_job_detail(self) -> None:
        if self._setup_draft.source_path_label is None:
            self.apply_backup_job_detail(empty_backup_job_detail_state())
            return
        target_count = len(self._setup_draft.targets)
        target_word = "target" if target_count == 1 else "targets"
        self.apply_backup_job_detail(
            BackupJobDetailViewState(
                job_id=None,
                title=self._setup_draft.source_name or "Local preview draft",
                source_label=self._setup_draft.source_path_label,
                revision_label="Local preview draft",
                target_summary_label=f"{target_count} {target_word}",
                defaults_summary_label="Update backup - All user files - Standard verification",
                target_lines=tuple(
                    f"{target.name}: {target.path_label}" for target in self._setup_draft.targets
                ),
                read_model_available=False,
                found=False,
            )
        )

    def _apply_backup_job_detail_state(self, state: BackupJobDetailViewState) -> None:
        for label in (self._job_detail_title, self._jobs_detail_title):
            if label is not None:
                label.setText(self._display(state.title))
        for labels, value in (
            (
                (self._job_detail_source_value, self._jobs_detail_source_value),
                state.source_label,
            ),
            (
                (self._job_detail_revision_value, self._jobs_detail_revision_value),
                state.revision_label,
            ),
            (
                (self._job_detail_targets_value, self._jobs_detail_targets_value),
                state.target_summary_label,
            ),
            (
                (self._job_detail_defaults_value, self._jobs_detail_defaults_value),
                state.defaults_summary_label,
            ),
            (
                (self._job_detail_plan_value, self._jobs_detail_plan_value),
                state.plan_summary_label,
            ),
        ):
            for label in labels:
                if label is not None:
                    label.setText(self._display(value))
        lines = state.target_lines or ("Ingen mål å vise.",)
        for target_rows in (
            self._job_detail_target_rows,
            self._jobs_detail_target_rows,
        ):
            for index, row in enumerate(target_rows):
                if index < len(lines):
                    row.setText(self._display(lines[index]))
                    row.setVisible(True)
                else:
                    row.setText("")
                    row.setVisible(False)
        analysis_pending = state.analysis_request_state in {"QUEUED", "RUNNING"}
        completed_plan_already_run = (
            state.job_id is not None
            and state.job_id == self._latest_run_job_id
            and state.plan_id is not None
            and state.plan_id == self._latest_run_plan_id
            and self._latest_run_state
            in {
                "COMPLETED",
                "COMPLETED_WITH_WARNINGS",
                "PARTIAL_FAILURE",
                "FAILED",
                "CANCELLED",
                "BLOCKED_BY_SAFETY",
                "RECOVERY_REQUIRED",
            }
        )
        check_mode = (
            state.found
            and (
                state.plan_id is None
                or state.plan_state != "SEALED"
                or completed_plan_already_run
            )
        )
        for start_button in (
            self._start_backup_button,
            self._jobs_start_backup_button,
        ):
            if start_button is None:
                continue
            has_plan = (
                state.found
                and state.plan_state == "SEALED"
                and state.plan_id is not None
                and state.plan_checksum is not None
            )
            queued = state.job_id in self._queued_backup_job_ids
            active_for_job = (
                self._run_progress_state.active
                and self._run_progress_state.job_id == state.job_id
            )
            start_button.setVisible(
                state.found and not active_for_job and (has_plan or check_mode)
            )
            start_button.setEnabled(
                not analysis_pending
                and not queued
                and not active_for_job
                and self._engine_client is not None
                and (
                    hasattr(self._engine_client, "check_backup")
                    if check_mode
                    else (
                        has_plan
                        and state.plan_runnable
                        and hasattr(self._engine_client, "start_backup")
                    )
                )
            )
            if analysis_pending:
                start_button.setText(self._texts().checking_backup)
                start_button.setToolTip(self._texts().checking_backup_tooltip)
            elif queued:
                start_button.setText(self._texts().backup_queued)
                start_button.setToolTip(self._texts().start_backup_tooltip)
            elif check_mode:
                start_button.setText(self._texts().run_backup)
                start_button.setToolTip(self._texts().run_backup_tooltip)
            else:
                start_button.setText(self._texts().start_backup)
                start_button.setToolTip(self._texts().start_backup_tooltip)
        self._apply_run_progress_state(self._run_progress_state)
        self._refresh_dashboard_geometry()

    def _invoke_primary_backup_action(self) -> None:
        state = self._job_detail_state
        completed_plan_already_run = (
            state.job_id is not None
            and state.job_id == self._latest_run_job_id
            and state.plan_id is not None
            and state.plan_id == self._latest_run_plan_id
            and self._latest_run_state
            in {
                "COMPLETED",
                "COMPLETED_WITH_WARNINGS",
                "PARTIAL_FAILURE",
                "FAILED",
                "CANCELLED",
                "BLOCKED_BY_SAFETY",
                "RECOVERY_REQUIRED",
            }
        )
        if (
            state.found
            and (
                state.plan_id is None
                or state.plan_state != "SEALED"
                or completed_plan_already_run
            )
        ):
            self._check_selected_backup()
            return
        self._start_selected_backup()

    def _check_selected_backup(self) -> None:
        state = self._job_detail_state
        if (
            self._engine_client is None
            or not hasattr(self._engine_client, "check_backup")
            or state.job_id is None
            or self._analysis_request_id is not None
        ):
            return
        request_id = str(uuid4())
        idempotency_key = str(uuid4())
        provider = cast(BackupCheckProvider, self._engine_client)
        response = provider.check_backup(
            job_id=state.job_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        if response.status is IpcStatus.REJECTED:
            reason = (
                str(response.payload.get("reason_code"))
                if response.payload.get("reason_code") is not None
                else response.reason.value
                if response.reason is not None
                else "UNKNOWN"
            )
            self.apply_engine_status(
                replace(
                    self._engine_status_state,
                    detail=f"Backup check failed: {reason}",
                    status_kind="warning",
                )
            )
            return
        request_payload = response.payload.get("analysis_request")
        accepted_request_id = (
            request_payload.get("request_id")
            if isinstance(request_payload, dict)
            else None
        )
        if not isinstance(accepted_request_id, str) or not accepted_request_id:
            return
        self._analysis_request_id = accepted_request_id
        self._analysis_idempotency_key = idempotency_key
        self._job_detail_state = replace(
            state,
            analysis_request_id=accepted_request_id,
            analysis_request_state="QUEUED",
        )
        self._apply_backup_job_detail_state(self._job_detail_state)
        self.apply_engine_status(
            replace(
                self._engine_status_state,
                detail=self._texts().checking_backup,
                status_kind="ready",
            )
        )
        self._analysis_timer.start()

    def _poll_backup_analysis(self) -> None:
        request_id = self._analysis_request_id
        job_id = self._selected_job_id
        if (
            request_id is None
            or job_id is None
            or self._engine_client is None
            or not hasattr(self._engine_client, "get_backup_job_detail")
        ):
            self._analysis_timer.stop()
            return
        provider = cast(BackupJobDetailProvider, self._engine_client)
        response = provider.get_backup_job_detail(job_id=job_id)
        if response.status is IpcStatus.REJECTED:
            return
        state = backup_job_detail_from_response(response)
        if state.analysis_request_id != request_id:
            return
        self.apply_backup_job_detail(state)
        if state.analysis_request_state in {"QUEUED", "RUNNING"}:
            return
        self._analysis_timer.stop()
        self._analysis_request_id = None
        self._analysis_idempotency_key = None
        terminal_state = state.analysis_request_state or "FAILED"
        started_run_id = state.analysis_request_started_run_id
        if (
            terminal_state == "SUCCEEDED"
            and started_run_id is not None
        ):
            self._queued_backup_job_ids.add(job_id)
            detail = self._texts().backup_queued
            status_kind = "ready"
            self._set_active_run(started_run_id)
            self._poll_active_run_progress()
        elif terminal_state in {"SUCCEEDED", "NO_CHANGES"}:
            detail = (
                self._texts().no_backup_changes
                if terminal_state == "NO_CHANGES"
                else self._texts().backup_check_complete
            )
            status_kind = "ready"
        else:
            detail = (
                state.analysis_request_reason_code
                or self._texts().backup_check_failed
            )
            status_kind = "warning"
        self.apply_engine_status(
            replace(
                self._engine_status_state,
                detail=detail,
                status_kind=status_kind,
            )
        )
        self._refresh_backup_overview()
        self._refresh_activity_overview()
        self._refresh_history_timeline()

    def _start_selected_backup(self) -> None:
        state = self._job_detail_state
        if (
            self._engine_client is None
            or not hasattr(self._engine_client, "start_backup")
            or state.job_id is None
            or state.plan_id is None
            or state.plan_checksum is None
            or not state.plan_runnable
        ):
            return
        self._start_request_id = self._start_request_id or str(uuid4())
        self._start_idempotency_key = self._start_idempotency_key or str(uuid4())
        provider = cast(BackupStartProvider, self._engine_client)
        response = provider.start_backup(
            plan_id=state.plan_id,
            plan_checksum=state.plan_checksum,
            request_id=self._start_request_id,
            idempotency_key=self._start_idempotency_key,
        )
        if response.status is IpcStatus.REJECTED:
            readiness = response.payload.get("readiness")
            codes = readiness.get("validation_codes") if isinstance(readiness, dict) else None
            reason = (
                str(codes[0])
                if isinstance(codes, list) and codes
                else response.reason.value
                if response.reason is not None
                else "UNKNOWN"
            )
            self.apply_engine_status(
                replace(
                    self._engine_status_state,
                    detail=f"Backup start failed: {reason}",
                    status_kind="warning",
                )
            )
            return
        run_payload = response.payload.get("run")
        run_id = (
            run_payload.get("run_id")
            if isinstance(run_payload, dict)
            else None
        )
        self._queued_backup_job_ids.add(state.job_id)
        self._start_request_id = None
        self._start_idempotency_key = None
        self.apply_engine_status(
            replace(
                self._engine_status_state,
                detail=self._texts().backup_queued,
                status_kind="ready",
            )
        )
        self._apply_backup_job_detail_state(state)
        self._refresh_activity_overview()
        if isinstance(run_id, str) and run_id:
            self._set_active_run(run_id)
            self._poll_active_run_progress()

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

    def _apply_plan_endpoint_preview_state(self, state: PlanEndpointPreviewState) -> None:
        if self._plan_endpoint_title is not None:
            self._plan_endpoint_title.setText(self._display(state.title))
        if self._plan_endpoint_summary is not None:
            self._plan_endpoint_summary.setText(self._display(state.summary_label))
        lines = tuple(row.display_line for row in state.rows) or (
            "No endpoint rows.",
        )
        for index, row in enumerate(self._plan_endpoint_rows):
            if index < len(lines):
                row.setText(self._display(lines[index]))
                row.setVisible(True)
            else:
                row.setText("")
                row.setVisible(False)

    def _apply_snapshot_health_preview_state(self, state: SnapshotHealthPreviewState) -> None:
        if self._snapshot_health_title is not None:
            self._snapshot_health_title.setText(self._display(state.title))
        if self._snapshot_health_summary is not None:
            self._snapshot_health_summary.setText(self._display(state.summary_label))
        lines = tuple(row.display_line for row in state.rows) or (
            "No snapshot health rows.",
        )
        for index, row in enumerate(self._snapshot_health_rows):
            if index < len(lines):
                row.setText(self._display(lines[index]))
                row.setVisible(True)
            else:
                row.setText("")
                row.setVisible(False)

    def _apply_cataloged_files_preview_state(self, state: CatalogedFilesPreviewState) -> None:
        if self._cataloged_files_title is not None:
            self._cataloged_files_title.setText(self._display(state.title))
        if self._cataloged_files_summary is not None:
            self._cataloged_files_summary.setText(self._display(state.summary_label))
        lines = tuple(row.display_line for row in state.rows) or (
            "No cataloged files.",
        )
        for index, row in enumerate(self._cataloged_files_rows):
            if index < len(lines):
                row.setText(self._display(lines[index]))
                row.setVisible(True)
            else:
                row.setText("")
                row.setVisible(False)

    def _build_layout(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        root.setProperty("densityMode", self._user_preferences.density.value)
        root.setProperty("reducedMotion", self._user_preferences.reduced_motion)
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
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav.setTextElideMode(Qt.TextElideMode.ElideRight)
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
        nav.currentRowChanged.connect(self._select_navigation_row)
        self._navigation = nav
        return nav

    def _build_workspace(self) -> QFrame:
        workspace = QFrame()
        workspace.setObjectName("workspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        heading = QLabel(self._texts().dashboard)
        heading.setObjectName("workspaceHeading")
        self._workspace_heading = heading
        layout.addWidget(heading)

        stack = QStackedWidget()
        stack.setObjectName("workspaceStack")
        dashboard_page = self._build_dashboard_page()
        dashboard_scroll = _scrollable_page(dashboard_page, "dashboardScrollArea")
        self._dashboard_page = dashboard_page
        self._dashboard_scroll_area = dashboard_scroll
        stack.addWidget(dashboard_scroll)
        jobs_page = self._build_jobs_page()
        jobs_scroll = _scrollable_page(jobs_page, "jobsScrollArea")
        self._jobs_page = jobs_page
        self._jobs_scroll_area = jobs_scroll
        stack.addWidget(jobs_scroll)
        history_page = self._build_history_page()
        history_scroll = _scrollable_page(history_page, "historyScrollArea")
        self._history_page = history_page
        self._history_scroll_area = history_scroll
        stack.addWidget(history_scroll)
        settings_page = self._build_settings_page()
        settings_scroll = _scrollable_page(settings_page, "settingsScrollArea")
        self._settings_page = settings_page
        self._settings_scroll_area = settings_scroll
        stack.addWidget(settings_scroll)
        self._workspace_stack = stack
        layout.addWidget(stack, 1)
        return workspace

    def _select_navigation_row(self, row: int) -> None:
        if row < 0:
            return
        self._selected_navigation_index = min(row, 3)
        if self._workspace_stack is not None:
            self._workspace_stack.setCurrentIndex(self._selected_navigation_index)
        if self._workspace_heading is not None:
            self._workspace_heading.setText(self._current_navigation_label())
        if self._selected_navigation_index == 1 and self._engine_client is not None:
            self._refresh_backup_overview()
        if self._selected_navigation_index == 2:
            self._refresh_history_timeline()
        if self._selected_navigation_index == 3:
            self._apply_settings_storage_state()

    def _current_navigation_label(self) -> str:
        labels = (
            self._texts().dashboard,
            self._texts().jobs,
            self._texts().history,
            self._texts().settings,
        )
        return labels[self._selected_navigation_index]

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("dashboardPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setSizeConstraints(
            QLayout.SizeConstraint.SetNoConstraint,
            QLayout.SizeConstraint.SetMinAndMaxSize,
        )
        layout.addWidget(self._build_backup_setup_panel(self._setup_state))
        layout.addWidget(self._build_dashboard_detail_row())
        layout.addStretch(1)
        return page

    def _build_jobs_page(self) -> QWidget:
        texts = self._texts()
        page = QWidget()
        page.setObjectName("jobsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setSizeConstraints(
            QLayout.SizeConstraint.SetNoConstraint,
            QLayout.SizeConstraint.SetMinAndMaxSize,
        )

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title = QLabel(texts.saved_jobs)
        title.setObjectName("sectionTitle")
        self._jobs_title_label = title
        page_label = QLabel("0-0")
        page_label.setObjectName("mutedLabel")
        page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._jobs_page_label = page_label
        previous = QToolButton()
        previous.setObjectName("jobsPreviousButton")
        previous.setIcon(self._icons.icon("back"))
        previous.setIconSize(QSize(20, 20))
        previous.setToolTip(texts.previous_page_tooltip)
        previous.setAccessibleName(texts.previous_page_tooltip)
        previous.setEnabled(False)
        previous.clicked.connect(self._show_previous_jobs_page)
        self._jobs_previous_button = previous
        next_button = QToolButton()
        next_button.setObjectName("jobsNextButton")
        next_button.setIcon(self._icons.icon("next"))
        next_button.setIconSize(QSize(20, 20))
        next_button.setToolTip(texts.next_page_tooltip)
        next_button.setAccessibleName(texts.next_page_tooltip)
        next_button.setEnabled(False)
        next_button.clicked.connect(self._show_next_jobs_page)
        self._jobs_next_button = next_button
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(previous)
        header_layout.addWidget(page_label)
        header_layout.addWidget(next_button)
        layout.addWidget(header)

        jobs_list = QListWidget()
        jobs_list.setObjectName("jobsList")
        jobs_list.setAccessibleName(texts.saved_jobs)
        jobs_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        jobs_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        jobs_list.setWordWrap(True)
        jobs_list.setMinimumHeight(148)
        jobs_list.setMaximumHeight(232)
        jobs_list.currentItemChanged.connect(self._select_job_item)
        self._jobs_list = jobs_list
        layout.addWidget(jobs_list)

        empty = QLabel(texts.jobs_unavailable)
        empty.setObjectName("jobsEmptyLabel")
        _configure_responsive_label(empty)
        self._jobs_empty_label = empty
        layout.addWidget(empty)
        layout.addWidget(self._build_jobs_detail_panel(self._job_detail_state))
        layout.addStretch(1)
        self._apply_jobs_overview_state(self._backup_overview_state)
        return page

    def _build_jobs_detail_panel(self, state: BackupJobDetailViewState) -> QFrame:
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("jobsDetailPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(8)

        title = _ElidingPathLabel()
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title.setText(self._display(state.title))
        title.setObjectName("jobsDetailTitle")
        self._jobs_detail_title = title
        layout.addWidget(title, 0, 0, 1, 3)

        self._jobs_detail_source_label, self._jobs_detail_source_value = (
            _add_labeled_eliding_path_value(
                layout,
                1,
                texts.source,
                self._display(state.source_label),
            )
        )
        self._jobs_detail_source_value.setObjectName("jobsDetailSourceValue")
        self._jobs_detail_targets_label, self._jobs_detail_targets_value = _add_labeled_text_value(
            layout,
            2,
            texts.target,
            self._display(state.target_summary_label),
        )
        self._jobs_detail_targets_value.setObjectName("jobsDetailTargetsValue")
        self._jobs_detail_defaults_label, self._jobs_detail_defaults_value = _add_labeled_text_value(
            layout,
            3,
            texts.defaults,
            self._display(state.defaults_summary_label),
        )
        self._jobs_detail_defaults_value.setObjectName("jobsDetailDefaultsValue")
        self._jobs_detail_revision_label, self._jobs_detail_revision_value = _add_labeled_text_value(
            layout,
            4,
            texts.revision,
            self._display(state.revision_label),
        )
        self._jobs_detail_revision_value.setObjectName("jobsDetailRevisionValue")
        self._jobs_detail_plan_label, self._jobs_detail_plan_value = _add_labeled_text_value(
            layout,
            5,
            texts.plan,
            self._display(state.plan_summary_label),
        )
        self._jobs_detail_plan_value.setObjectName("jobsDetailPlanValue")

        target_heading = QLabel(texts.job_detail_targets_heading)
        target_heading.setObjectName("mutedLabel")
        self._jobs_detail_target_heading = target_heading
        layout.addWidget(target_heading, 6, 0)
        target_lines = state.target_lines or ("Ingen mål å vise.",)
        for index in range(3):
            target_row = _ElidingPathLabel()
            target_row.setObjectName("jobsDetailTargetRow")
            target_row.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            target_row.setText(
                self._display(target_lines[index]) if index < len(target_lines) else ""
            )
            target_row.setVisible(index < len(target_lines))
            self._jobs_detail_target_rows.append(target_row)
            layout.addWidget(target_row, 6 + index, 1, 1, 2)

        progress_title = QLabel(texts.run_progress)
        progress_title.setObjectName("mutedLabel")
        progress_title.setVisible(False)
        self._jobs_run_progress_title = progress_title
        layout.addWidget(progress_title, 9, 0)

        progress_state = QLabel()
        progress_state.setObjectName("jobsRunProgressState")
        progress_state.setVisible(False)
        _configure_responsive_label(progress_state)
        self._jobs_run_progress_state = progress_state
        layout.addWidget(progress_state, 9, 1, 1, 2)

        progress_bar = QProgressBar()
        progress_bar.setObjectName("jobsRunProgressBar")
        progress_bar.setRange(0, 1)
        progress_bar.setValue(0)
        progress_bar.setTextVisible(False)
        progress_bar.setFixedHeight(16)
        progress_bar.setVisible(False)
        self._jobs_run_progress_bar = progress_bar
        layout.addWidget(progress_bar, 10, 0, 1, 3)

        progress_detail = QLabel()
        progress_detail.setObjectName("jobsRunProgressDetail")
        progress_detail.setVisible(False)
        _configure_responsive_label(progress_detail)
        self._jobs_run_progress_detail = progress_detail
        layout.addWidget(progress_detail, 11, 0, 1, 3)

        active_file = QLabel()
        active_file.setObjectName("jobsRunActiveFile")
        active_file.setVisible(False)
        _configure_responsive_label(active_file, selectable=True)
        self._jobs_run_active_file = active_file
        layout.addWidget(active_file, 12, 0, 1, 3)

        for index in range(3):
            row = QLabel()
            row.setObjectName("jobsRunTargetRow")
            row.setVisible(False)
            _configure_responsive_label(row)
            self._jobs_run_target_rows.append(row)
            layout.addWidget(row, 13 + index, 0, 1, 3)

        stop_button = QPushButton(texts.stop_after_active_file)
        stop_button.setObjectName("jobsStopBackupButton")
        stop_button.setToolTip(texts.stop_after_active_file_tooltip)
        stop_button.setVisible(False)
        stop_button.clicked.connect(self._stop_active_backup_after_file)
        self._jobs_stop_button = stop_button
        layout.addWidget(
            stop_button,
            16,
            0,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        pause_button = QPushButton(texts.pause_backup)
        pause_button.setObjectName("jobsPauseBackupButton")
        pause_button.setToolTip(texts.pause_backup_tooltip)
        pause_button.setVisible(False)
        pause_button.clicked.connect(self._pause_active_backup)
        self._jobs_pause_button = pause_button
        layout.addWidget(
            pause_button,
            16,
            1,
            alignment=Qt.AlignmentFlag.AlignRight,
        )

        resume_button = QPushButton(texts.resume_backup)
        resume_button.setObjectName("jobsResumeBackupButton")
        resume_button.setToolTip(texts.resume_backup_tooltip)
        resume_button.setVisible(False)
        resume_button.clicked.connect(self._resume_active_backup)
        self._jobs_resume_button = resume_button
        layout.addWidget(
            resume_button,
            16,
            2,
            alignment=Qt.AlignmentFlag.AlignRight,
        )

        start_backup = QPushButton(texts.start_backup)
        start_backup.setObjectName("jobsStartBackupButton")
        start_backup.setToolTip(texts.start_backup_tooltip)
        start_backup.setVisible(False)
        start_backup.clicked.connect(self._invoke_primary_backup_action)
        self._jobs_start_backup_button = start_backup
        layout.addWidget(start_backup, 17, 2)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_history_page(self) -> QWidget:
        texts = self._texts()
        page = QWidget()
        page.setObjectName("historyPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setSizeConstraints(
            QLayout.SizeConstraint.SetNoConstraint,
            QLayout.SizeConstraint.SetMinAndMaxSize,
        )

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title = QLabel(texts.history_activities)
        title.setObjectName("sectionTitle")
        self._history_title_label = title
        previous = QToolButton()
        previous.setObjectName("historyPreviousButton")
        previous.setIcon(self._icons.icon("back"))
        previous.setIconSize(QSize(20, 20))
        previous.setToolTip(texts.previous_page_tooltip)
        previous.setAccessibleName(texts.previous_page_tooltip)
        previous.setEnabled(False)
        previous.clicked.connect(self._show_previous_history_page)
        self._history_previous_button = previous
        page_label = QLabel("0-0")
        page_label.setObjectName("mutedLabel")
        page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._history_page_label = page_label
        next_button = QToolButton()
        next_button.setObjectName("historyNextButton")
        next_button.setIcon(self._icons.icon("next"))
        next_button.setIconSize(QSize(20, 20))
        next_button.setToolTip(texts.next_page_tooltip)
        next_button.setAccessibleName(texts.next_page_tooltip)
        next_button.setEnabled(False)
        next_button.clicked.connect(self._show_next_history_page)
        self._history_next_button = next_button
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(previous)
        header_layout.addWidget(page_label)
        header_layout.addWidget(next_button)
        layout.addWidget(header)

        filters = QWidget()
        filters.setObjectName("historyFilters")
        filter_layout = QGridLayout(filters)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        filter_group = QButtonGroup(filters)
        filter_group.setExclusive(True)
        for column, (activity_filter, label) in enumerate((
            ("ALL", texts.all_activities),
            ("CONTROLS", texts.controls),
            ("BACKUPS", texts.backup_runs),
        )):
            button = QPushButton(label)
            button.setObjectName("historyFilterButton")
            button.setCheckable(True)
            button.setChecked(activity_filter == self._history_activity_filter)
            button.setProperty("activityFilter", activity_filter)
            button.clicked.connect(
                lambda checked=False, value=activity_filter: (
                    self._set_history_activity_filter(value) if checked else None
                )
            )
            filter_group.addButton(button)
            filter_layout.addWidget(button, 0, column)
            filter_layout.setColumnStretch(column, 1)
            self._history_filter_buttons[activity_filter] = button
        self._history_filter_group = filter_group
        job_filter = QComboBox()
        job_filter.setObjectName("historyJobFilter")
        job_filter.setAccessibleName(texts.all_jobs)
        job_filter.addItem(texts.all_jobs, None)
        job_filter.currentIndexChanged.connect(self._set_history_job_filter)
        self._history_job_filter = job_filter
        filter_layout.addWidget(job_filter, 1, 0, 1, 3)
        layout.addWidget(filters)

        history_list = QListWidget()
        history_list.setObjectName("historyList")
        history_list.setAccessibleName(texts.history_activities)
        history_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        history_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        history_list.setWordWrap(True)
        history_list.setMinimumHeight(148)
        history_list.setMaximumHeight(232)
        history_list.currentItemChanged.connect(self._select_history_item)
        self._history_list = history_list
        layout.addWidget(history_list)

        empty = QLabel(texts.history_unavailable)
        empty.setObjectName("historyEmptyLabel")
        _configure_responsive_label(empty)
        self._history_empty_label = empty
        layout.addWidget(empty)
        layout.addWidget(self._build_history_detail_panel())
        layout.addStretch(1)
        self._apply_history_timeline_state(self._history_timeline_state)
        return page

    def _build_history_detail_panel(self) -> QFrame:
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("historyDetailPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(8)

        title = QLabel(texts.history_empty)
        title.setObjectName("historyDetailTitle")
        _configure_responsive_label(title)
        self._history_detail_title = title
        layout.addWidget(title, 0, 0, 1, 3)
        detail_rows = (
            ("activity_type", texts.activity_type),
            ("status", texts.status),
            ("started", texts.started),
            ("finished", texts.finished),
            ("duration", texts.duration),
            ("operations", texts.operations),
            ("transferred", texts.transferred),
            ("average_speed", texts.average_speed),
            ("warnings_errors", texts.warnings_errors),
            ("trigger", texts.trigger),
            ("identifiers", texts.identifiers),
        )
        for row_index, (key, label_text) in enumerate(detail_rows, start=1):
            label, value = _add_labeled_text_value(
                layout,
                row_index,
                label_text,
                "-",
            )
            label.setObjectName("historyDetailLabel")
            value.setObjectName(f"historyDetail{key.title().replace('_', '')}Value")
            self._history_detail_labels[key] = label
            self._history_detail_values[key] = value

        target_heading = QLabel(texts.activity_targets)
        target_heading.setObjectName("mutedLabel")
        self._history_target_heading = target_heading
        layout.addWidget(target_heading, 12, 0)
        for index in range(3):
            target_row = QLabel("")
            target_row.setObjectName("historyTargetRow")
            _configure_responsive_label(target_row, selectable=True)
            target_row.setVisible(False)
            self._history_target_rows.append(target_row)
            layout.addWidget(target_row, 12 + index, 1, 1, 2)

        operation_header = QWidget()
        operation_header_layout = QHBoxLayout(operation_header)
        operation_header_layout.setContentsMargins(0, 10, 0, 0)
        operation_header_layout.setSpacing(8)
        operation_heading = QLabel(texts.file_results)
        operation_heading.setObjectName("historyOperationHeading")
        operation_header_layout.addWidget(operation_heading)
        operation_header_layout.addStretch(1)
        operation_previous = QToolButton()
        operation_previous.setObjectName("historyOperationPreviousButton")
        operation_previous.setIcon(self._icons.icon("back"))
        operation_previous.setIconSize(QSize(18, 18))
        operation_previous.setToolTip(texts.previous_page_tooltip)
        operation_previous.setAccessibleName(texts.previous_page_tooltip)
        operation_previous.clicked.connect(
            self._show_previous_history_operation_page
        )
        operation_page_label = QLabel("0-0")
        operation_page_label.setObjectName("mutedLabel")
        operation_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        operation_next = QToolButton()
        operation_next.setObjectName("historyOperationNextButton")
        operation_next.setIcon(self._icons.icon("next"))
        operation_next.setIconSize(QSize(18, 18))
        operation_next.setToolTip(texts.next_page_tooltip)
        operation_next.setAccessibleName(texts.next_page_tooltip)
        operation_next.clicked.connect(self._show_next_history_operation_page)
        operation_header_layout.addWidget(operation_previous)
        operation_header_layout.addWidget(operation_page_label)
        operation_header_layout.addWidget(operation_next)
        self._history_operation_header = operation_header
        self._history_operation_heading = operation_heading
        self._history_operation_previous_button = operation_previous
        self._history_operation_page_label = operation_page_label
        self._history_operation_next_button = operation_next
        layout.addWidget(operation_header, 16, 0, 1, 3)

        operation_list = QListWidget()
        operation_list.setObjectName("historyOperationList")
        operation_list.setAccessibleName(texts.file_results)
        operation_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        operation_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        operation_list.setWordWrap(True)
        operation_list.setMinimumHeight(132)
        operation_list.setMaximumHeight(220)
        operation_list.currentItemChanged.connect(
            self._select_history_operation
        )
        self._history_operation_list = operation_list
        layout.addWidget(operation_list, 17, 0, 1, 3)

        operation_empty = QLabel(texts.file_results_unavailable)
        operation_empty.setObjectName("historyOperationEmptyLabel")
        _configure_responsive_label(operation_empty)
        self._history_operation_empty_label = operation_empty
        layout.addWidget(operation_empty, 18, 0, 1, 3)

        operation_detail_title = QLabel(texts.file_results_unavailable)
        operation_detail_title.setObjectName("historyOperationDetailTitle")
        _configure_responsive_label(operation_detail_title, selectable=True)
        self._history_operation_detail_title = operation_detail_title
        layout.addWidget(operation_detail_title, 19, 0, 1, 3)
        operation_detail_rows = (
            ("result", texts.file_result),
            ("finished", texts.finished),
            ("transferred", texts.transferred),
            ("verification", texts.verification),
            ("durability", texts.durability),
            ("attempts", texts.attempts),
            ("last_error", texts.last_error),
        )
        for row_index, (key, label_text) in enumerate(
            operation_detail_rows,
            start=20,
        ):
            label, value = _add_labeled_text_value(
                layout,
                row_index,
                label_text,
                "-",
            )
            label.setObjectName("historyOperationDetailLabel")
            value.setObjectName(
                f"historyOperationDetail{key.title().replace('_', '')}Value"
            )
            self._history_operation_detail_labels[key] = label
            self._history_operation_detail_values[key] = value

        attempt_heading = QLabel(texts.file_attempts)
        attempt_heading.setObjectName("mutedLabel")
        self._history_attempt_heading = attempt_heading
        layout.addWidget(attempt_heading, 27, 0, 1, 3)
        attempt_list = QListWidget()
        attempt_list.setObjectName("historyAttemptList")
        attempt_list.setAccessibleName(texts.file_attempts)
        attempt_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        attempt_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        attempt_list.setWordWrap(True)
        attempt_list.setMinimumHeight(112)
        attempt_list.setMaximumHeight(190)
        self._history_attempt_list = attempt_list
        layout.addWidget(attempt_list, 28, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        return panel

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setSizeConstraints(
            QLayout.SizeConstraint.SetMinimumSize,
            QLayout.SizeConstraint.SetMinimumSize,
        )
        layout.addWidget(self._build_appearance_settings_panel())
        layout.addWidget(self._build_default_settings_panel())
        layout.addWidget(self._build_storage_settings_panel())
        layout.addWidget(self._build_about_settings_panel())
        layout.addStretch(1)
        return page

    def _build_appearance_settings_panel(self) -> QFrame:
        text = settings_text(self._selected_language_code)
        panel, layout = self._new_settings_panel(
            "appearance_title",
            text.appearance_title,
            "appearance_detail",
            text.appearance_detail,
        )

        self._settings_labels["theme"] = self._settings_row_label(text.theme)
        layout.addWidget(self._settings_labels["theme"], 2, 0)
        theme_controls = QWidget()
        theme_controls.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        theme_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, theme_controls)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        theme_layout.setSpacing(8)
        group = QButtonGroup(panel)
        group.setExclusive(True)
        for mode, label in (
            (AppearancePreference.SYSTEM, text.theme_system),
            (AppearancePreference.LIGHT, text.theme_light),
            (AppearancePreference.DARK, text.theme_dark),
        ):
            button = QPushButton(label)
            button.setObjectName("settingsModeButton")
            button.setCheckable(True)
            button.setChecked(mode is self._user_preferences.appearance)
            button.clicked.connect(
                lambda checked=False, selected=mode: self._select_appearance(selected)
            )
            group.addButton(button)
            theme_layout.addWidget(button)
            self._settings_theme_buttons[mode] = button
        theme_layout.addStretch(1)
        self._settings_theme_layout = theme_layout
        layout.addWidget(theme_controls, 2, 1, 1, 2)

        self._settings_labels["density"] = self._settings_row_label(text.density)
        layout.addWidget(self._settings_labels["density"], 3, 0)
        density = QComboBox()
        density.setObjectName("settingsDensityCombo")
        density.addItem(text.density_comfortable, DensityPreference.COMFORTABLE.value)
        density.addItem(text.density_compact, DensityPreference.COMPACT.value)
        density.setCurrentIndex(density.findData(self._user_preferences.density.value))
        density.currentIndexChanged.connect(self._select_density)
        self._settings_density_combo = density
        layout.addWidget(density, 3, 1, 1, 2)

        reduced_motion = QCheckBox(text.reduced_motion)
        reduced_motion.setObjectName("settingsReducedMotionCheck")
        reduced_motion.setChecked(self._user_preferences.reduced_motion)
        reduced_motion.toggled.connect(self._select_reduced_motion)
        self._settings_reduced_motion = reduced_motion
        layout.addWidget(reduced_motion, 4, 1, 1, 2)

        self._settings_labels["language"] = self._settings_row_label(text.language)
        layout.addWidget(self._settings_labels["language"], 5, 0)
        language = QComboBox()
        language.setObjectName("settingsLanguageCombo")
        for code, label in self._language_options:
            language.addItem(_flag_icon(code), label, code)
        language.setCurrentIndex(language.findData(self._selected_language_code.value))
        language.currentIndexChanged.connect(self._select_settings_language)
        self._settings_language_combo = language
        layout.addWidget(language, 5, 1, 1, 2)
        return panel

    def _build_default_settings_panel(self) -> QFrame:
        text = settings_text(self._selected_language_code)
        panel, layout = self._new_settings_panel(
            "defaults_title",
            text.defaults_title,
            "defaults_detail",
            text.defaults_detail,
        )
        for row, key, label_text, value_text in (
            (2, "retention", text.retention, text.retention_value),
            (3, "performance", text.performance, text.performance_value),
            (4, "quarantine", text.quarantine, text.quarantine_value),
            (5, "notifications", text.notifications, text.notifications_value),
        ):
            label, value = _add_labeled_text_value(layout, row, label_text, value_text)
            self._configure_settings_key_label(label)
            value.setObjectName(f"settings{key.title().replace('_', '')}Value")
            self._settings_labels[key] = label
            self._settings_labels[f"{key}_value"] = value
        return panel

    def _build_storage_settings_panel(self) -> QFrame:
        text = settings_text(self._selected_language_code)
        panel, layout = self._new_settings_panel(
            "storage_title",
            text.storage_title,
            "storage_detail",
            text.storage_detail,
        )
        for row, key, label_text in (
            (2, "storage_status", text.storage_status),
            (3, "state_usage", text.state_usage),
            (4, "free_space", text.free_space),
            (5, "data_location", text.data_location),
        ):
            label, value = _add_labeled_text_value(layout, row, label_text, "")
            self._configure_settings_key_label(label)
            value.setObjectName(f"settings{key.title().replace('_', '')}Value")
            self._settings_labels[key] = label
            self._settings_labels[f"{key}_value"] = value
        return panel

    def _build_about_settings_panel(self) -> QFrame:
        text = settings_text(self._selected_language_code)
        panel, layout = self._new_settings_panel(
            "about_title",
            text.about_title,
            "about_detail",
            text.about_detail,
        )
        version_label, version_value = _add_labeled_text_value(
            layout,
            2,
            text.version,
            __version__,
        )
        self._settings_labels["version"] = version_label
        self._settings_labels["version_value"] = version_value
        self._configure_settings_key_label(version_label)
        version_value.setObjectName("settingsVersionValue")

        report_label, report_value = _add_labeled_text_value(
            layout,
            3,
            text.privacy_report,
            text.about_detail,
        )
        self._settings_labels["privacy_report"] = report_label
        self._settings_labels["privacy_report_value"] = report_value
        self._configure_settings_key_label(report_label)
        report_value.setObjectName("settingsPrivacyReportValue")

        actions = QWidget()
        actions.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        action_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, actions)
        action_layout.setContentsMargins(0, 4, 0, 0)
        action_layout.setSpacing(8)
        open_button = QPushButton(text.open_data_folder)
        open_button.setObjectName("settingsActionButton")
        open_button.setEnabled(self._data_root is not None and self._open_data_folder is not None)
        open_button.clicked.connect(self._open_local_data_folder)
        self._settings_open_data_button = open_button
        copy_button = QPushButton(text.copy_diagnostics)
        copy_button.setObjectName("settingsActionButton")
        copy_button.clicked.connect(self._copy_diagnostics)
        self._settings_copy_diagnostics_button = copy_button
        action_layout.addWidget(open_button)
        action_layout.addWidget(copy_button)
        action_layout.addStretch(1)
        self._settings_action_layout = action_layout
        layout.addWidget(actions, 4, 0, 1, 3)

        status = QLabel()
        status.setObjectName("settingsStatusLabel")
        status.setVisible(False)
        _configure_responsive_label(status)
        self._settings_status_label = status
        layout.addWidget(status, 5, 0, 1, 3)
        return panel

    def _new_settings_panel(
        self,
        title_key: str,
        title_text: str,
        detail_key: str,
        detail_text: str,
    ) -> tuple[QFrame, QGridLayout]:
        panel = QFrame()
        panel.setObjectName("settingsSectionPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(10)
        title = QLabel(title_text)
        title.setObjectName("sectionTitle")
        _configure_responsive_label(title)
        detail = QLabel(detail_text)
        detail.setObjectName("mutedLabel")
        _configure_responsive_label(detail)
        self._settings_labels[title_key] = title
        self._settings_labels[detail_key] = detail
        layout.addWidget(title, 0, 0, 1, 3)
        layout.addWidget(detail, 1, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        return panel, layout

    @staticmethod
    def _settings_row_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        MediaSyncWindow._configure_settings_key_label(label)
        return label

    @staticmethod
    def _configure_settings_key_label(label: QLabel) -> None:
        label.setWordWrap(True)
        label.setMinimumWidth(100)
        label.setProperty("responsiveText", True)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _build_placeholder_page(self, title: str, detail: str) -> QFrame:
        page = QFrame()
        page.setObjectName(f"{title.lower()}Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        title_label = QLabel(self._display(title))
        title_label.setObjectName("sectionTitle")
        detail_label = QLabel(self._display(detail))
        detail_label.setObjectName("mutedLabel")
        detail_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(detail_label)
        layout.addStretch(1)
        return page

    def _build_dashboard_detail_row(self) -> QWidget:
        row = QWidget()
        row.setObjectName("dashboardDetailRow")
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_backup_job_detail_panel(self._job_detail_state), 1)
        layout.addWidget(self._build_engine_panel(), 1)
        self._dashboard_detail_layout = layout
        return row

    def _build_backup_setup_panel(self, state: StandardBackupSetupViewState) -> QFrame:
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("standardBackupPanel")
        panel.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(10)

        title = QLabel(texts.setup_title)
        title.setObjectName("sectionTitle")
        self._setup_title_label = title
        subtitle = QLabel(texts.setup_subtitle)
        subtitle.setObjectName("mutedLabel")
        _configure_responsive_label(subtitle)
        self._setup_subtitle_label = subtitle
        layout.addWidget(title, 0, 0, 1, 3)
        layout.addWidget(subtitle, 1, 0, 1, 3)

        stepper = QWidget()
        stepper.setObjectName("backupSetupStepper")
        stepper_layout = QGridLayout(stepper)
        stepper_layout.setContentsMargins(0, 4, 0, 4)
        stepper_layout.setSpacing(8)
        for index, step in enumerate(state.steps):
            label = _step_label(step, texts.setup_steps[index])
            self._setup_step_labels.append(label)
            stepper_layout.addWidget(label, 0, index)
            stepper_layout.setColumnStretch(index, 1)
        self._setup_stepper_layout = stepper_layout
        layout.addWidget(stepper, 2, 0, 1, 3)

        self._setup_source_label, self._setup_source_value = (
            _add_labeled_eliding_path_value(
                layout,
                3,
                texts.source,
                self._display(state.source_label),
            )
        )
        self._setup_source_value.setObjectName("setupSourceValue")
        self._setup_target_label, self._setup_target_value = (
            _add_labeled_eliding_path_value(
                layout,
                4,
                texts.target,
                self._display(state.target_label),
            )
        )
        self._setup_target_value.setObjectName("setupTargetValue")
        target_controls = QWidget()
        target_controls.setObjectName("setupTargetControls")
        target_controls.setMinimumWidth(0)
        target_controls.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Minimum,
        )
        target_controls_layout = QVBoxLayout(target_controls)
        target_controls_layout.setContentsMargins(0, 0, 0, 0)
        target_controls_layout.setSpacing(6)
        for index in range(state.max_targets):
            target_row = QWidget()
            target_row.setObjectName(f"setupTargetRow{index + 1}")
            target_row.setMinimumWidth(0)
            target_row.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Minimum,
            )
            target_row_layout = QHBoxLayout(target_row)
            target_row_layout.setContentsMargins(0, 0, 0, 0)
            target_row_layout.setSpacing(8)
            target_path = _ElidingPathLabel()
            target_path.setObjectName("setupTargetPathRow")
            remove_target = QToolButton()
            remove_target.setObjectName("removeTargetButton")
            remove_target.setIcon(self._icons.icon("remove-target"))
            remove_target.setIconSize(QSize(20, 20))
            remove_target.clicked.connect(
                lambda checked=False, target_index=index: self._remove_setup_target(
                    target_index
                )
            )
            target_row_layout.addWidget(target_path, 1)
            target_row_layout.addWidget(remove_target, 0, Qt.AlignmentFlag.AlignTop)
            target_controls_layout.addWidget(target_row)
            self._setup_target_rows.append(target_row)
            self._setup_target_path_labels.append(target_path)
            self._setup_remove_target_buttons.append(remove_target)
        add_target = QToolButton()
        add_target.setObjectName("addTargetButton")
        add_target.setIcon(self._icons.icon("add-target"))
        add_target.setIconSize(QSize(20, 20))
        add_target.clicked.connect(self._add_setup_target)
        target_controls_layout.addWidget(
            add_target,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )
        self._setup_target_controls = target_controls
        self._setup_add_target_button = add_target
        layout.addWidget(target_controls, 5, 1, 1, 2)
        self._setup_defaults_label, self._setup_defaults_value = _add_labeled_text_value(
            layout,
            6,
            texts.defaults,
            " · ".join(self._display(label) for label in state.defaults.summary()[:3]),
        )
        self._setup_defaults_value.setObjectName("setupDefaultsValue")
        self._setup_retention_label, self._setup_retention_value = _add_labeled_text_value(
            layout,
            7,
            texts.retention,
            self._display(state.defaults.retention_label),
        )
        self._setup_retention_value.setObjectName("setupRetentionValue")

        back = QToolButton()
        back.setObjectName("setupBackButton")
        back.setIcon(self._icons.icon("back"))
        back.setIconSize(QSize(20, 20))
        back.setVisible(self._setup_can_go_back(state))
        back.setEnabled(self._setup_can_go_back(state))
        back.setToolTip(texts.back_tooltip)
        back.setAccessibleName(texts.back_tooltip)
        back.clicked.connect(self._handle_setup_back_action)
        self._setup_back_button = back
        primary = QPushButton(self._display(state.primary_action_label))
        primary.setObjectName("createBackupButton")
        primary.setEnabled(self._setup_primary_enabled(state))
        primary.setToolTip(self._setup_primary_tooltip(state))
        primary.clicked.connect(self._handle_setup_primary_action)
        self._setup_primary_button = primary
        actions = QWidget()
        actions.setObjectName("setupActions")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        actions_layout.addStretch(1)
        actions_layout.addWidget(back)
        actions_layout.addWidget(primary)
        layout.addWidget(actions, 8, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        self._apply_setup_target_controls(state)
        return panel

    def _build_backup_job_detail_panel(self, state: BackupJobDetailViewState) -> QFrame:
        texts = self._texts()
        panel = QFrame()
        panel.setObjectName("backupJobDetailPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(8)

        title = _ElidingPathLabel()
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title.setText(self._display(state.title))
        title.setObjectName("jobDetailTitle")
        self._job_detail_title = title
        layout.addWidget(title, 0, 0, 1, 3)

        self._job_detail_source_label, self._job_detail_source_value = (
            _add_labeled_eliding_path_value(
                layout,
                1,
                texts.source,
                self._display(state.source_label),
            )
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

        self._job_detail_plan_label, self._job_detail_plan_value = _add_labeled_text_value(
            layout,
            5,
            texts.plan,
            self._display(state.plan_summary_label),
        )
        self._job_detail_plan_value.setObjectName("jobDetailPlanValue")

        target_heading = QLabel(texts.job_detail_targets_heading)
        target_heading.setObjectName("mutedLabel")
        self._job_detail_target_heading = target_heading
        layout.addWidget(target_heading, 6, 0)
        self._job_detail_target_rows = []
        target_lines = state.target_lines or ("Ingen mål å vise.",)
        for index in range(3):
            target_row = _ElidingPathLabel()
            target_row.setObjectName("jobDetailTargetRow")
            target_row.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            target_row.setText(
                self._display(target_lines[index]) if index < len(target_lines) else ""
            )
            target_row.setVisible(index < len(target_lines))
            self._job_detail_target_rows.append(target_row)
            layout.addWidget(target_row, 6 + index, 1, 1, 2)

        start_backup = QPushButton(texts.start_backup)
        start_backup.setObjectName("startBackupButton")
        start_backup.setToolTip(texts.start_backup_tooltip)
        start_backup.setVisible(False)
        start_backup.clicked.connect(self._invoke_primary_backup_action)
        self._start_backup_button = start_backup
        layout.addWidget(start_backup, 9, 2)

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
        outer_layout = QVBoxLayout(activity)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        content = QWidget()
        content.setObjectName("activityContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(12)
        layout.setSizeConstraints(
            QLayout.SizeConstraint.SetNoConstraint,
            QLayout.SizeConstraint.SetMinAndMaxSize,
        )

        title = QLabel(texts.activity)
        title.setObjectName("sectionTitle")
        self._activity_title_label = title
        empty = QLabel(texts.no_active_runs)
        empty.setObjectName("activityEmptyLabel")
        _configure_responsive_label(empty)
        self._activity_empty_label = empty
        layout.addWidget(title)
        layout.addWidget(empty)
        layout.addSpacing(8)
        self._add_activity_status(layout, self._job_status_state)
        layout.addSpacing(8)
        self._add_plan_operation_preview(layout, self._plan_preview_state)
        layout.addSpacing(8)
        self._add_plan_endpoint_preview(layout, self._plan_endpoint_preview_state)
        layout.addSpacing(8)
        self._add_snapshot_health_preview(layout, self._snapshot_health_preview_state)
        layout.addSpacing(8)
        self._add_cataloged_files_preview(layout, self._cataloged_files_preview_state)
        if self._show_component_gallery:
            layout.addWidget(self._build_component_gallery())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("activityScrollArea")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        return activity

    def _add_activity_status(
        self,
        layout: QVBoxLayout,
        state: BackupJobStatusViewState,
    ) -> None:
        heading = QLabel(self._display(state.title))
        heading.setObjectName("activityStatusTitle")
        _configure_responsive_label(heading)
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
            _configure_responsive_label(row)
            self._activity_dimension_rows.append(row)
            layout.addWidget(row)

    def _add_plan_operation_preview(
        self,
        layout: QVBoxLayout,
        state: PlanOperationPreviewState,
    ) -> None:
        title = QLabel(self._display(state.title))
        title.setObjectName("planPreviewTitle")
        _configure_responsive_label(title)
        self._plan_preview_title = title
        summary = QLabel(self._display(state.summary_label))
        summary.setObjectName("planPreviewSummary")
        _configure_responsive_label(summary)
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
            _configure_responsive_label(row)
            row.setVisible(index < len(lines))
            self._plan_preview_rows.append(row)
            layout.addWidget(row)

    def _add_plan_endpoint_preview(
        self,
        layout: QVBoxLayout,
        state: PlanEndpointPreviewState,
    ) -> None:
        title = QLabel(self._display(state.title))
        title.setObjectName("planEndpointTitle")
        _configure_responsive_label(title)
        self._plan_endpoint_title = title
        summary = QLabel(self._display(state.summary_label))
        summary.setObjectName("planEndpointSummary")
        _configure_responsive_label(summary)
        self._plan_endpoint_summary = summary
        layout.addWidget(title)
        layout.addWidget(summary)
        self._plan_endpoint_rows = []
        lines = tuple(row.display_line for row in state.rows) or (
            "No endpoint rows.",
        )
        for index in range(4):
            row = QLabel(self._display(lines[index]) if index < len(lines) else "")
            row.setObjectName("planEndpointRow")
            _configure_responsive_label(row)
            row.setVisible(index < len(lines))
            self._plan_endpoint_rows.append(row)
            layout.addWidget(row)

    def _add_snapshot_health_preview(
        self,
        layout: QVBoxLayout,
        state: SnapshotHealthPreviewState,
    ) -> None:
        title = QLabel(self._display(state.title))
        title.setObjectName("snapshotHealthTitle")
        _configure_responsive_label(title)
        self._snapshot_health_title = title
        summary = QLabel(self._display(state.summary_label))
        summary.setObjectName("snapshotHealthSummary")
        _configure_responsive_label(summary)
        self._snapshot_health_summary = summary
        layout.addWidget(title)
        layout.addWidget(summary)
        self._snapshot_health_rows = []
        lines = tuple(row.display_line for row in state.rows) or (
            "No snapshot health rows.",
        )
        for index in range(3):
            row = QLabel(self._display(lines[index]) if index < len(lines) else "")
            row.setObjectName("snapshotHealthRow")
            _configure_responsive_label(row)
            row.setVisible(index < len(lines))
            self._snapshot_health_rows.append(row)
            layout.addWidget(row)

    def _add_cataloged_files_preview(
        self,
        layout: QVBoxLayout,
        state: CatalogedFilesPreviewState,
    ) -> None:
        title = QLabel(self._display(state.title))
        title.setObjectName("catalogedFilesTitle")
        _configure_responsive_label(title)
        self._cataloged_files_title = title
        summary = QLabel(self._display(state.summary_label))
        summary.setObjectName("catalogedFilesSummary")
        _configure_responsive_label(summary)
        self._cataloged_files_summary = summary
        layout.addWidget(title)
        layout.addWidget(summary)
        self._cataloged_files_rows = []
        lines = tuple(row.display_line for row in state.rows) or (
            "No cataloged files.",
        )
        for index in range(3):
            row = QLabel(self._display(lines[index]) if index < len(lines) else "")
            row.setObjectName("catalogedFilesRow")
            _configure_responsive_label(row)
            row.setVisible(index < len(lines))
            self._cataloged_files_rows.append(row)
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
        self._user_preferences = replace(
            self._user_preferences,
            language=UserLanguage(normalized.value),
        )
        self._save_user_preferences()
        self._apply_selected_language()
        self._apply_localized_text()

    def _select_settings_language(self, index: int) -> None:
        if self._settings_language_combo is None:
            return
        language_code = self._settings_language_combo.itemData(index)
        if isinstance(language_code, str):
            self._select_language(language_code)

    def _select_appearance(self, appearance: AppearancePreference) -> None:
        if appearance is self._user_preferences.appearance:
            return
        self._user_preferences = replace(self._user_preferences, appearance=appearance)
        self._apply_current_appearance()
        self._save_user_preferences()

    def _select_density(self, index: int) -> None:
        if self._settings_density_combo is None:
            return
        value = self._settings_density_combo.itemData(index)
        try:
            density = DensityPreference(value)
        except (TypeError, ValueError):
            return
        if density is self._user_preferences.density:
            return
        self._user_preferences = replace(self._user_preferences, density=density)
        self._apply_current_appearance()
        root = self.centralWidget()
        if root is not None:
            root.setProperty("densityMode", density.value)
        self._refresh_dashboard_geometry()
        self._save_user_preferences()

    def _select_reduced_motion(self, enabled: bool) -> None:
        if enabled is self._user_preferences.reduced_motion:
            return
        self._user_preferences = replace(self._user_preferences, reduced_motion=enabled)
        root = self.centralWidget()
        if root is not None:
            root.setProperty("reducedMotion", enabled)
        self._save_user_preferences()

    def _apply_current_appearance(self) -> None:
        if self._apply_appearance is not None:
            self._apply_appearance(
                self._user_preferences.appearance,
                self._user_preferences.density,
            )

    def _save_user_preferences(self) -> None:
        if self._user_preferences_store is None:
            return
        try:
            self._user_preferences_store.save(self._user_preferences)
        except (OSError, ValueError):
            self._set_settings_status(
                settings_text(
                    self._selected_language_code
                ).preference_save_failed,
                status_kind="error",
            )

    def _open_local_data_folder(self) -> None:
        if self._data_root is None or self._open_data_folder is None:
            return
        self._data_root.mkdir(parents=True, exist_ok=True)
        if not self._open_data_folder(self._data_root):
            self._set_settings_status(
                settings_text(
                    self._selected_language_code
                ).open_data_folder_failed,
                status_kind="error",
            )

    def _copy_diagnostics(self) -> None:
        application = QApplication.instance()
        if application is None:
            return
        app = cast(QApplication, application)
        state = self._engine_status_state
        report = "\n".join(
            (
                "MediaSync Home diagnostics",
                f"version: {__version__}",
                "privacy: user names and private paths omitted",
                f"language: {self._user_preferences.language.value}",
                f"appearance: {self._user_preferences.appearance.value}",
                f"density: {self._user_preferences.density.value}",
                f"reduced_motion: {str(self._user_preferences.reduced_motion).lower()}",
                f"engine_connection: {state.connection_label}",
                f"engine_state: {state.state_label}",
                f"engine_scope: {state.scope_label}",
                f"engine_contract: {state.protocol_label}",
                f"mutation_policy: {state.mutation_label}",
                f"capacity_status: {state.capacity_status or 'unavailable'}",
                f"capacity_reason: {state.capacity_reason_code or 'unavailable'}",
                (
                    "state_size_bytes: "
                    f"{state.state_size_bytes if state.state_size_bytes is not None else 'unavailable'}"
                ),
                (
                    "local_free_space_bytes: "
                    f"{state.local_free_space_bytes if state.local_free_space_bytes is not None else 'unavailable'}"
                ),
                f"data_root: {'configured' if self._data_root is not None else 'unavailable'}",
            )
        )
        app.clipboard().setText(report)
        self._set_settings_status(
            settings_text(self._selected_language_code).diagnostics_copied,
            status_kind="saved",
        )

    def _set_settings_status(self, text: str, *, status_kind: str) -> None:
        if self._settings_status_label is None:
            return
        self._settings_status_label.setText(text)
        self._settings_status_label.setProperty("statusKind", status_kind)
        self._settings_status_label.setVisible(True)
        style = self._settings_status_label.style()
        style.unpolish(self._settings_status_label)
        style.polish(self._settings_status_label)

    def _apply_settings_storage_state(self) -> None:
        if not self._settings_labels:
            return
        text = settings_text(self._selected_language_code)
        state = self._engine_status_state
        capacity_status = state.capacity_status
        if state.capacity_measurement_complete is False or capacity_status is None:
            status_text = text.capacity_unavailable
        elif capacity_status == "READY":
            status_text = text.capacity_ready
        elif capacity_status == "SOFT_QUOTA":
            status_text = text.capacity_warning
        elif capacity_status == "HARD_STOP":
            status_text = text.capacity_blocked
        else:
            status_text = capacity_status
        values = {
            "storage_status_value": status_text,
            "state_usage_value": (
                _format_bytes(state.state_size_bytes)
                if state.state_size_bytes is not None
                else text.capacity_unavailable
            ),
            "free_space_value": (
                _format_bytes(state.local_free_space_bytes)
                if state.local_free_space_bytes is not None
                else text.capacity_unavailable
            ),
            "data_location_value": (
                str(self._data_root)
                if self._data_root is not None
                else text.capacity_unavailable
            ),
        }
        for key, value in values.items():
            label = self._settings_labels.get(key)
            if label is not None:
                label.setText(value)

    def _update_responsive_dashboard_layout(self) -> None:
        self._update_responsive_settings_layout()
        compact_steps = self.width() < 1040
        stacked_details = self.width() < 1360
        if (
            compact_steps == self._compact_dashboard_layout
            and stacked_details == self._stacked_dashboard_details
        ):
            self._refresh_dashboard_geometry()
            return
        self._compact_dashboard_layout = compact_steps
        self._stacked_dashboard_details = stacked_details
        if self._dashboard_detail_layout is not None:
            self._dashboard_detail_layout.setDirection(
                QBoxLayout.Direction.TopToBottom
                if stacked_details
                else QBoxLayout.Direction.LeftToRight
            )
        if self._setup_stepper_layout is not None:
            columns = 2 if compact_steps else 4
            for index, label in enumerate(self._setup_step_labels):
                self._setup_stepper_layout.removeWidget(label)
                self._setup_stepper_layout.addWidget(
                    label,
                    index // columns,
                    index % columns,
                )
            for column in range(4):
                self._setup_stepper_layout.setColumnStretch(
                    column,
                    1 if column < columns else 0,
                )
        self._refresh_dashboard_geometry()

    def _update_responsive_settings_layout(self) -> None:
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.width() < 1040
            else QBoxLayout.Direction.LeftToRight
        )
        if self._settings_theme_layout is not None:
            self._settings_theme_layout.setDirection(direction)
        if self._settings_action_layout is not None:
            self._settings_action_layout.setDirection(direction)

    def _refresh_dashboard_geometry(self) -> None:
        self._refresh_responsive_page_geometry(
            self._dashboard_page,
            self._dashboard_scroll_area,
        )
        self._refresh_responsive_page_geometry(
            self._jobs_page,
            self._jobs_scroll_area,
        )
        self._refresh_responsive_page_geometry(
            self._history_page,
            self._history_scroll_area,
        )
        self._refresh_responsive_page_geometry(
            self._settings_page,
            self._settings_scroll_area,
        )

    def _refresh_responsive_page_geometry(
        self,
        page: QWidget | None,
        scroll_area: QScrollArea | None,
    ) -> None:
        if page is None:
            return
        layout = page.layout()
        if layout is not None:
            responsive_labels = tuple(
                label
                for label in page.findChildren(QLabel)
                if label.property("responsiveText")
            )
            page.setMinimumHeight(0)
            for label in responsive_labels:
                label.setMinimumHeight(0)
            layout.invalidate()
            layout.activate()
            for label in responsive_labels:
                required_height = (
                    0
                    if label.isHidden()
                    else max(0, label.heightForWidth(max(1, label.width())))
                )
                label.setMinimumHeight(required_height)
            layout.invalidate()
            layout.activate()
            required_height = max(0, layout.minimumSize().height())
            page.setMinimumHeight(required_height)
            if scroll_area is not None:
                page.resize(
                    page.width(),
                    max(required_height, scroll_area.viewport().height()),
                )
        page.updateGeometry()
        if scroll_area is not None:
            scroll_area.updateGeometry()
            scroll_area.viewport().updateGeometry()

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
                if self._settings_language_combo is not None:
                    self._settings_language_combo.blockSignals(True)
                    self._settings_language_combo.setCurrentIndex(
                        self._settings_language_combo.findData(code)
                    )
                    self._settings_language_combo.blockSignals(False)
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
        if self._jobs_title_label is not None:
            self._jobs_title_label.setText(texts.saved_jobs)
        if self._jobs_list is not None:
            self._jobs_list.setAccessibleName(texts.saved_jobs)
        if self._history_title_label is not None:
            self._history_title_label.setText(texts.history_activities)
        if self._history_list is not None:
            self._history_list.setAccessibleName(texts.history_activities)
        for activity_filter, label in (
            ("ALL", texts.all_activities),
            ("CONTROLS", texts.controls),
            ("BACKUPS", texts.backup_runs),
        ):
            button = self._history_filter_buttons.get(activity_filter)
            if button is not None:
                button.setText(label)
        for text_label, text in (
            (self._setup_source_label, texts.source),
            (self._setup_target_label, texts.target),
            (self._setup_defaults_label, texts.defaults),
            (self._setup_retention_label, texts.retention),
            (self._job_detail_source_label, texts.source),
            (self._job_detail_targets_label, texts.target),
            (self._job_detail_defaults_label, texts.defaults),
            (self._job_detail_revision_label, texts.revision),
            (self._job_detail_plan_label, texts.plan),
            (self._job_detail_target_heading, texts.job_detail_targets_heading),
            (self._jobs_detail_source_label, texts.source),
            (self._jobs_detail_targets_label, texts.target),
            (self._jobs_detail_defaults_label, texts.defaults),
            (self._jobs_detail_revision_label, texts.revision),
            (self._jobs_detail_plan_label, texts.plan),
            (self._jobs_detail_target_heading, texts.job_detail_targets_heading),
            (self._engine_title_label, texts.engine_host),
            (self._engine_scope_label, texts.scope),
            (self._engine_contract_label, texts.contract),
            (self._engine_mutation_label, texts.mutation_policy),
            (self._activity_title_label, texts.activity),
            (self._activity_empty_label, texts.no_active_runs),
            (self._history_target_heading, texts.activity_targets),
            (self._history_operation_heading, texts.file_results),
            (self._history_attempt_heading, texts.file_attempts),
        ):
            if text_label is not None:
                text_label.setText(text)
        for key, text in (
            ("activity_type", texts.activity_type),
            ("status", texts.status),
            ("started", texts.started),
            ("finished", texts.finished),
            ("duration", texts.duration),
            ("operations", texts.operations),
            ("transferred", texts.transferred),
            ("average_speed", texts.average_speed),
            ("warnings_errors", texts.warnings_errors),
            ("trigger", texts.trigger),
            ("identifiers", texts.identifiers),
        ):
            history_label = self._history_detail_labels.get(key)
            if history_label is not None:
                history_label.setText(text)
        for key, text in (
            ("result", texts.file_result),
            ("finished", texts.finished),
            ("transferred", texts.transferred),
            ("verification", texts.verification),
            ("durability", texts.durability),
            ("attempts", texts.attempts),
            ("last_error", texts.last_error),
        ):
            operation_label = self._history_operation_detail_labels.get(key)
            if operation_label is not None:
                operation_label.setText(text)
        if self._history_operation_list is not None:
            self._history_operation_list.setAccessibleName(texts.file_results)
        if self._history_attempt_list is not None:
            self._history_attempt_list.setAccessibleName(texts.file_attempts)
        if self._workspace_heading is not None:
            self._workspace_heading.setText(self._current_navigation_label())
        self.apply_engine_status(self._engine_status_state)
        self._apply_backup_setup_state(self._setup_state)
        self._apply_jobs_overview_state(self._backup_overview_state)
        self._apply_history_timeline_state(self._history_timeline_state)
        self._apply_backup_job_detail_state(self._job_detail_state)
        self._apply_job_status_state(self._job_status_state)
        self._apply_plan_operation_preview_state(self._plan_preview_state)
        self._apply_plan_endpoint_preview_state(self._plan_endpoint_preview_state)
        self._apply_snapshot_health_preview_state(self._snapshot_health_preview_state)
        self._apply_cataloged_files_preview_state(self._cataloged_files_preview_state)
        self._apply_settings_localized_text()
        self._apply_settings_storage_state()

    def _apply_settings_localized_text(self) -> None:
        text = settings_text(self._selected_language_code)
        values = {
            "appearance_title": text.appearance_title,
            "appearance_detail": text.appearance_detail,
            "theme": text.theme,
            "density": text.density,
            "language": text.language,
            "defaults_title": text.defaults_title,
            "defaults_detail": text.defaults_detail,
            "retention": text.retention,
            "retention_value": text.retention_value,
            "performance": text.performance,
            "performance_value": text.performance_value,
            "quarantine": text.quarantine,
            "quarantine_value": text.quarantine_value,
            "notifications": text.notifications,
            "notifications_value": text.notifications_value,
            "storage_title": text.storage_title,
            "storage_detail": text.storage_detail,
            "storage_status": text.storage_status,
            "state_usage": text.state_usage,
            "free_space": text.free_space,
            "data_location": text.data_location,
            "about_title": text.about_title,
            "about_detail": text.about_detail,
            "version": text.version,
            "privacy_report": text.privacy_report,
            "privacy_report_value": text.about_detail,
        }
        for key, value in values.items():
            label = self._settings_labels.get(key)
            if label is not None:
                label.setText(value)
        for mode, mode_label in (
            (AppearancePreference.SYSTEM, text.theme_system),
            (AppearancePreference.LIGHT, text.theme_light),
            (AppearancePreference.DARK, text.theme_dark),
        ):
            button = self._settings_theme_buttons.get(mode)
            if button is not None:
                button.setText(mode_label)
        if self._settings_density_combo is not None:
            current = self._settings_density_combo.currentData()
            self._settings_density_combo.blockSignals(True)
            self._settings_density_combo.setItemText(0, text.density_comfortable)
            self._settings_density_combo.setItemText(1, text.density_compact)
            self._settings_density_combo.setCurrentIndex(
                self._settings_density_combo.findData(current)
            )
            self._settings_density_combo.blockSignals(False)
        if self._settings_reduced_motion is not None:
            self._settings_reduced_motion.setText(text.reduced_motion)
        if self._settings_open_data_button is not None:
            self._settings_open_data_button.setText(text.open_data_folder)
        if self._settings_copy_diagnostics_button is not None:
            self._settings_copy_diagnostics_button.setText(text.copy_diagnostics)


def _add_key_value(layout: QGridLayout, row: int, label_text: str, value: QLabel) -> QLabel:
    label = QLabel(label_text)
    label.setObjectName("mutedLabel")
    _configure_responsive_label(value, selectable=True)
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
    label = _add_key_value(layout, row, label_text, value)
    return label, value


def _add_labeled_eliding_path_value(
    layout: QGridLayout,
    row: int,
    label_text: str,
    value_text: str,
) -> tuple[QLabel, QLabel]:
    label = QLabel(label_text)
    label.setObjectName("mutedLabel")
    value = _ElidingPathLabel()
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    value.setText(value_text)
    layout.addWidget(label, row, 0)
    layout.addWidget(value, row, 1, 1, 2)
    return label, value


def _step_label(step: BackupSetupStepViewState, title: str) -> QLabel:
    label = QLabel(f"{step.number}. {title}")
    label.setObjectName("setupStepLabel")
    state = "current" if step.current else "complete" if step.complete else "upcoming"
    label.setProperty("stepState", state)
    _configure_responsive_label(label)
    return label


def _scrollable_page(widget: QWidget, object_name: str) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName(object_name)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    scroll.setWidget(widget)
    return scroll


def _configure_responsive_label(label: QLabel, *, selectable: bool = False) -> None:
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setProperty("responsiveText", True)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class _ElidingPathLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._full_text = ""
        self.setMinimumWidth(0)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.setProperty("fullText", text)
        self._sync_display_text()

    def text(self) -> str:
        return self._full_text

    def clear(self) -> None:
        self.setText("")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_display_text()

    def _sync_display_text(self) -> None:
        available_width = max(0, self.contentsRect().width())
        display_text = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideMiddle,
            available_width,
        )
        self.setProperty("displayText", display_text)
        super().setText(display_text)


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{int(value)} B"


def _format_rate(bytes_per_second: float) -> str:
    return f"{max(bytes_per_second, 0.0) / 1_000_000:.1f} MB/s"


def _format_eta(seconds: int) -> str:
    minutes = max(seconds, 0) // 60
    if minutes < 1:
        return "< 1 min"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {remaining_minutes} min"
    return f"{remaining_minutes} min"


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _display_name_for_path(path: str) -> str:
    parsed = Path(path)
    return parsed.name or str(parsed)


def _path_identity(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).casefold()


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
