from __future__ import annotations

import os
from dataclasses import replace
from threading import Event, Lock, get_ident
from time import monotonic

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from mediasync_home.application.runtime_status import startup_status  # noqa: E402
from mediasync_home.application.job_drafts import StandardBackupJobDraft  # noqa: E402
from mediasync_home.adapters.local_user_preferences import (  # noqa: E402
    LocalUserPreferencesStore,
)
from mediasync_home.application.user_preferences import (  # noqa: E402
    AppearancePreference,
    DensityPreference,
    UserLanguage,
    UserPreferences,
)
from mediasync_home.domain.process_roles import ProcessRole  # noqa: E402
from mediasync_home.ipc.protocol import IpcResponse  # noqa: E402
from mediasync_home.presentation.app import build_main_window, ensure_qapplication  # noqa: E402
from mediasync_home.presentation.background_queries import (  # noqa: E402
    BackgroundQueryController,
    BoundedPagePrefetchCache,
    CommandSubmissionController,
    UiUpdateCoalescer,
)
from mediasync_home.presentation.theme.icon_registry import IconRegistry  # noqa: E402
from mediasync_home.presentation.theme.theme_manager import ThemeManager, ThemeMode  # noqa: E402
from mediasync_home.presentation.virtual_tables import (  # noqa: E402
    BoundedVirtualTableView,
)
from mediasync_home.presentation.view_models.engine_status import (  # noqa: E402
    EngineStatusViewState,
    engine_status_from_response,
)
from mediasync_home.presentation.view_models.backup_setup import (  # noqa: E402
    BackupSetupStep,
)


def _virtual_row_count(table: BoundedVirtualTableView) -> int:
    return table.bounded_model.cached_row_count


def _virtual_row_text(table: BoundedVirtualTableView, row: int) -> str:
    return " · ".join(
        str(table.bounded_model.index(row, column).data())
        for column in range(table.bounded_model.columnCount())
    )


def _virtual_row_id(table: BoundedVirtualTableView, row: int) -> object:
    return table.bounded_model.index(row, 0).data(Qt.ItemDataRole.UserRole)


def _select_virtual_row(table: BoundedVirtualTableView, row: int) -> None:
    table.setCurrentIndex(table.bounded_model.index(row, 0))
    table.selectRow(row)


def _click_virtual_row(table: BoundedVirtualTableView, row: int) -> None:
    QTest.mouseClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=table.visualRect(table.bounded_model.index(row, 0)).center(),
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
    assert not registry.icon("add-target").isNull()
    assert not registry.icon("remove-target").isNull()
    assert not registry.icon("back").isNull()
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
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        jobs_page = window.findChild(QWidget, "jobsPage")
        jobs_list = window.findChild(QListWidget, "jobsList")
        jobs_empty = window.findChild(QLabel, "jobsEmptyLabel")
        detail_panel = window.findChild(QWidget, "backupJobDetailPanel")
        detail_title = window.findChild(QLabel, "jobDetailTitle")
        plan_preview_title = window.findChild(QLabel, "planPreviewTitle")
        plan_endpoint_title = window.findChild(QLabel, "planEndpointTitle")
        snapshot_health_title = window.findChild(QLabel, "snapshotHealthTitle")

        assert nav is not None
        assert nav.count() == 4
        assert [nav.item(index).text() for index in range(nav.count())] == [
            "Oversikt",
            "Jobber",
            "Historikk",
            "Innstillinger",
        ]
        assert chip is not None
        assert chip.text() == "Tilkoblet: Klar"
        assert chip.property("statusKind") == "ready"
        assert refresh is not None
        assert refresh.isEnabled() is True
        assert refresh.toolTip() == "Oppdater motorstatus"
        assert language is not None
        assert language.text() == ""
        assert not language.icon().isNull()
        assert language.toolTip() == "Språk: Norsk"
        assert language.menu() is not None
        action_bar = window.findChild(QWidget, "actionBar")
        assert action_bar is not None
        assert action_bar.layout() is not None
        assert (
            action_bar.layout().itemAt(action_bar.layout().count() - 1).widget()
            is language
        )
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
        assert create_backup.text() == "Velg kildemappe"
        assert create_backup.isEnabled() is True
        assert add_target is not None
        assert add_target.isHidden() is True
        assert setup_back is not None
        assert setup_back.isHidden() is True
        assert jobs_page is not None
        assert jobs_list is not None
        assert jobs_list.count() == 0
        assert jobs_empty is not None
        assert jobs_empty.text() == "Jobblisten er ikke tilgjengelig."
        assert detail_panel is not None
        assert detail_title is not None
        assert detail_title.text() == "Ingen lagret backupjobb"
        assert plan_preview_title is not None
        assert plan_preview_title.text() == "Planforhåndsvisning"
        assert plan_endpoint_title is not None
        assert plan_endpoint_title.text() == "Planendepunkter"
        assert snapshot_health_title is not None
        assert snapshot_health_title.text() == "Snapshothelse"
    finally:
        window.close()
        window.deleteLater()


def test_language_selector_updates_selected_flag(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.LIGHT)

    try:
        window.show()
        qapp.processEvents()
        language = window.findChild(QToolButton, "languageSelectorButton")
        nav = window.findChild(QListWidget, "navigationRail")
        refresh = window.findChild(QPushButton, "refreshEngineButton")
        setup_steps = window.findChildren(QLabel, "setupStepLabel")
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        jobs_empty = window.findChild(QLabel, "jobsEmptyLabel")
        jobs_list = window.findChild(QListWidget, "jobsList")
        detail_title = window.findChild(QLabel, "jobDetailTitle")
        plan_preview_title = window.findChild(QLabel, "planPreviewTitle")
        plan_endpoint_title = window.findChild(QLabel, "planEndpointTitle")
        snapshot_health_title = window.findChild(QLabel, "snapshotHealthTitle")

        assert language is not None
        assert language.menu() is not None
        menu_opened: list[bool] = []
        language.menu().aboutToShow.connect(lambda: menu_opened.append(True))
        QTimer.singleShot(0, language.menu().close)
        QTest.mouseClick(language, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert menu_opened == [True]
        language.menu().actions()[1].trigger()

        assert language.text() == ""
        assert not language.icon().isNull()
        assert language.toolTip() == "Language: English"
        assert [action.isChecked() for action in language.menu().actions()] == [
            False,
            True,
        ]
        assert nav is not None
        assert [nav.item(index).text() for index in range(nav.count())] == [
            "Dashboard",
            "Jobs",
            "History",
            "Settings",
        ]
        assert refresh is not None
        assert refresh.toolTip() == "Refresh engine status"
        assert [step.text() for step in setup_steps] == [
            "1. What do you want to protect?",
            "2. Where should copies go?",
            "3. How should backup work?",
            "4. Review and create",
        ]
        assert create_backup is not None
        assert create_backup.text() == "Choose source folder"
        assert add_target is not None
        assert add_target.toolTip() == "Add target folder"
        assert setup_back is not None
        assert setup_back.toolTip() == "Back"
        assert jobs_empty is not None
        assert jobs_empty.text() == "The job list is not available."
        assert jobs_list is not None
        assert jobs_list.accessibleName() == "Saved backup jobs"
        assert detail_title is not None
        assert detail_title.text() == "No saved backup job"
        assert plan_preview_title is not None
        assert plan_preview_title.text() == "Plan preview"
        assert plan_endpoint_title is not None
        assert plan_endpoint_title.text() == "Plan endpoints"
        assert snapshot_health_title is not None
        assert snapshot_health_title.text() == "Snapshot health"
    finally:
        window.close()
        window.deleteLater()


def test_navigation_rail_switches_workspace_pages(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.LIGHT)

    try:
        window.show()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        heading = window.findChild(QLabel, "workspaceHeading")
        stack = window.findChild(QStackedWidget, "workspaceStack")
        jobs_list = window.findChild(QListWidget, "jobsList")
        jobs_empty = window.findChild(QLabel, "jobsEmptyLabel")

        assert nav is not None
        assert heading is not None
        assert stack is not None
        assert stack.currentIndex() == 0

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(1)).center(),
        )
        qapp.processEvents()

        assert stack.currentIndex() == 1
        assert heading.text() == "Jobber"
        assert jobs_list is not None
        assert jobs_empty is not None
        assert jobs_empty.text() == "Jobblisten er ikke tilgjengelig."

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        qapp.processEvents()

        assert stack.currentIndex() == 3
        assert heading.text() == "Innstillinger"
    finally:
        window.close()
        window.deleteLater()


def test_setup_primary_button_collects_local_preview_draft(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.LIGHT)

    try:
        window.show()
        qapp.processEvents()
        choices = ["C:/Users/Ada/Pictures", "E:/MediaSyncBackup"]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]

        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        source = window.findChild(QLabel, "setupSourceValue")
        target = window.findChild(QLabel, "setupTargetValue")
        detail_title = window.findChild(QLabel, "jobDetailTitle")
        detail_source = window.findChild(QLabel, "jobDetailSourceValue")
        detail_targets = window.findChildren(QLabel, "jobDetailTargetRow")
        chip = window.findChild(QLabel, "engineStatusChip")

        assert create_backup is not None
        assert source is not None
        assert target is not None
        assert detail_title is not None
        assert detail_source is not None
        assert chip is not None
        assert add_target is not None
        assert setup_back is not None

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert source.text() == "C:/Users/Ada/Pictures"
        assert target.text() == "Ingen mål valgt"
        assert detail_title.text() == "Pictures"
        assert create_backup.isEnabled() is False
        assert create_backup.toolTip() == "Fortsett med valgte målmapper."
        assert add_target.isVisible() is True
        assert add_target.toolTip() == "Legg til målmappe"
        assert setup_back.isVisible() is True

        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert target.text() == "1 mål: MediaSyncBackup"
        assert create_backup.isEnabled() is True
        assert detail_source.text() == "C:/Users/Ada/Pictures"
        assert detail_targets[0].text() == "MediaSyncBackup: E:/MediaSyncBackup"

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert add_target.isHidden() is True

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert create_backup.text() == "Opprett og registrer"
        assert create_backup.isEnabled() is True

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert chip.text() == "Frakoblet: Venter"
    finally:
        window.close()
        window.deleteLater()


def test_disconnected_source_step_keeps_an_explicit_enabled_picker_action(qapp) -> None:
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.show()
        qapp.processEvents()
        window._apply_backup_setup_state(  # noqa: SLF001
            replace(window._setup_state, primary_action_label="Fortsett")  # noqa: SLF001
        )
        selected_titles: list[str] = []
        window._choose_directory = (  # type: ignore[method-assign]
            lambda title: selected_titles.append(title) or None
        )
        source_action = window.findChild(QPushButton, "createBackupButton")

        assert source_action is not None
        assert source_action.text() == "Velg kildemappe"
        assert source_action.isEnabled() is True
        QTest.mouseClick(source_action, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert selected_titles == ["Velg kildemappe"]
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize(
    ("window_width", "window_height"),
    ((900, 560), (1000, 650), (1120, 700)),
)
def test_target_selection_reflows_without_horizontal_clipping(
    qapp,
    window_width: int,
    window_height: int,
) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.DARK)

    try:
        window.resize(1120, 700)
        window.show()
        qapp.processEvents()
        choices = [
            "C:/Users/Example/Documents/"
            "ImportantDocumentsAndFamilyPicturesCollectionWithoutBreaks",
            "E:/MediaSync Home Backups/Primary External Drive/"
            "CompleteComputerBackupTargetFolderWithoutBreaksOne",
            "F:/MediaSync Home Backups/Secondary External Drive/"
            "CompleteComputerBackupTargetFolderWithoutBreaksTwo",
            "G:/MediaSync Home Backups/Offsite External Drive/"
            "CompleteComputerBackupTargetFolderWithoutBreaksThree",
        ]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        source_path = window.findChild(QLabel, "setupSourceValue")
        target_paths = window.findChildren(QLabel, "setupTargetPathRow")
        target_summary = window.findChild(QLabel, "setupTargetValue")
        detail_title = window.findChild(QLabel, "jobDetailTitle")
        detail_source = window.findChild(QLabel, "jobDetailSourceValue")
        detail_targets = window.findChildren(QLabel, "jobDetailTargetRow")
        target_controls = window.findChild(QWidget, "setupTargetControls")
        setup_panel = window.findChild(QFrame, "standardBackupPanel")
        setup_actions = window.findChild(QWidget, "setupActions")
        dashboard_scroll = window.findChild(QScrollArea, "dashboardScrollArea")
        activity_scroll = window.findChild(QScrollArea, "activityScrollArea")
        detail_row = window.findChild(QWidget, "dashboardDetailRow")
        activity_bar = window.findChild(QFrame, "activityBar")

        assert create_backup is not None
        assert add_target is not None
        assert setup_back is not None
        assert source_path is not None
        assert len(target_paths) == 3
        assert target_summary is not None
        assert detail_title is not None
        assert detail_source is not None
        assert len(detail_targets) == 3
        assert target_controls is not None
        assert setup_panel is not None
        assert setup_actions is not None
        assert dashboard_scroll is not None
        assert activity_scroll is not None
        assert detail_row is not None
        assert activity_bar is not None
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        for _ in range(3):
            QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
            qapp.processEvents()
        window.resize(window_width, window_height)
        qapp.processEvents()
        qapp.processEvents()

        assert (
            target_paths[0]
            .text()
            .endswith("CompleteComputerBackupTargetFolderWithoutBreaksOne")
        )
        assert source_path.text().endswith(
            "ImportantDocumentsAndFamilyPicturesCollectionWithoutBreaks"
        )
        assert source_path.toolTip() == source_path.text()
        assert "\N{HORIZONTAL ELLIPSIS}" in str(source_path.property("displayText"))
        assert source_path.wordWrap() is False
        assert all(target.isVisible() for target in target_paths)
        assert all(target.toolTip() == target.text() for target in target_paths)
        assert all(
            "\N{HORIZONTAL ELLIPSIS}" in str(target.property("displayText"))
            for target in target_paths
        )
        assert all(target.wordWrap() is False for target in target_paths)
        assert window._dashboard_detail_layout is not None
        assert (
            window._dashboard_detail_layout.direction()
            is QBoxLayout.Direction.TopToBottom
        )
        assert window._setup_stepper_layout is not None
        positions = [
            window._setup_stepper_layout.getItemPosition(
                window._setup_stepper_layout.indexOf(label)
            )[:2]
            for label in window._setup_step_labels
        ]
        assert positions == (
            [(0, 0), (0, 1), (1, 0), (1, 1)]
            if window_width < 1040
            else [(0, 0), (0, 1), (0, 2), (0, 3)]
        )
        assert dashboard_scroll.horizontalScrollBar().maximum() == 0
        assert activity_scroll.horizontalScrollBar().maximum() == 0
        assert detail_row.isHidden()
        assert activity_bar.isHidden()
        if window_width >= 1000:
            assert dashboard_scroll.verticalScrollBar().maximum() == 0
        assert target_controls.height() >= target_controls.minimumSizeHint().height()
        assert setup_panel.height() >= setup_panel.minimumSizeHint().height()
        assert setup_panel.rect().contains(setup_actions.geometry())
        for target_path in target_paths:
            target_position = target_path.mapTo(
                target_controls,
                target_path.rect().topLeft(),
            )
            assert target_position.x() >= 0
            assert target_position.y() >= 0
            assert target_position.x() + target_path.width() <= target_controls.width()
            assert (
                target_position.y() + target_path.height() <= target_controls.height()
            )
        bounded_folder_labels = (
            source_path,
            target_summary,
            detail_title,
            detail_source,
            *target_paths,
            *detail_targets,
        )
        for label in bounded_folder_labels:
            rendered_text = str(label.property("displayText"))
            assert label.wordWrap() is False
            assert label.minimumWidth() == 0
            assert label.toolTip() == label.text()
            assert (
                label.fontMetrics().horizontalAdvance(rendered_text)
                <= label.contentsRect().width()
            )
        dashboard_page = dashboard_scroll.widget()
        assert dashboard_page is not None
        assert dashboard_page.height() >= dashboard_page.minimumSizeHint().height()
        assert dashboard_scroll.verticalScrollBar().value() == 0
        setup_position = setup_panel.mapTo(
            dashboard_scroll.viewport(), setup_panel.rect().topLeft()
        )
        setup_title = window._setup_title_label
        assert setup_title is not None
        title_position = setup_title.mapTo(
            dashboard_scroll.viewport(), setup_title.rect().topLeft()
        )
        assert setup_position.y() >= 0
        assert title_position.y() >= 0
        assert (
            title_position.y() + setup_title.height()
            <= dashboard_scroll.viewport().height()
        )
        responsive_labels = [
            label
            for label in dashboard_page.findChildren(QLabel)
            if label.property("responsiveText") and not label.isHidden()
        ]
        assert responsive_labels
        for label in responsive_labels:
            assert label.wordWrap() is True
            assert label.minimumWidth() == 0
            assert label.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Ignored
            assert label.width() > 0
            assert label.height() >= label.heightForWidth(label.width())
        dashboard_scroll.ensureWidgetVisible(create_backup, 12, 12)
        qapp.processEvents()
        action_position = create_backup.mapTo(
            dashboard_scroll.viewport(), create_backup.rect().topLeft()
        )
        assert action_position.y() >= 0
        assert (
            action_position.y() + create_backup.height()
            <= dashboard_scroll.viewport().height()
        )
    finally:
        window.close()
        window.deleteLater()


def test_compact_target_picker_return_restores_setup_top(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.DARK)

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        choices = [
            "C:/Users/Ada/Pictures",
            "E:/MediaSync Backups/Primary target",
        ]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_title = window._setup_title_label
        target_summary = window.findChild(QLabel, "setupTargetValue")
        dashboard_scroll = window.findChild(QScrollArea, "dashboardScrollArea")

        assert create_backup is not None
        assert add_target is not None
        assert setup_title is not None
        assert target_summary is not None
        assert dashboard_scroll is not None
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        dashboard_scroll.verticalScrollBar().setValue(
            dashboard_scroll.verticalScrollBar().maximum()
        )

        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        qapp.processEvents()

        assert target_summary.text().endswith("Primary target")
        assert dashboard_scroll.verticalScrollBar().value() == 0
        title_position = setup_title.mapTo(
            dashboard_scroll.viewport(),
            setup_title.rect().topLeft(),
        )
        target_position = target_summary.mapTo(
            dashboard_scroll.viewport(),
            target_summary.rect().topLeft(),
        )
        assert title_position.y() >= 0
        assert target_position.y() >= 0
        assert (
            target_position.y() + target_summary.height()
            <= dashboard_scroll.viewport().height()
        )
        assert dashboard_scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        window.deleteLater()


def test_compact_target_selection_keeps_complete_step_actions_visible(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.DARK)

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        choices = [
            "C:/Users/Ada/Pictures",
            "E:/MediaSync Backups/Primary target",
        ]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        setup_panel = window.findChild(QFrame, "standardBackupPanel")
        dashboard_scroll = window.findChild(QScrollArea, "dashboardScrollArea")
        detail_row = window.findChild(QWidget, "dashboardDetailRow")
        activity_bar = window.findChild(QFrame, "activityBar")

        assert create_backup is not None
        assert add_target is not None
        assert setup_back is not None
        assert setup_panel is not None
        assert dashboard_scroll is not None
        assert detail_row is not None
        assert activity_bar is not None
        assert window._setup_defaults_label is not None
        assert window._setup_retention_label is not None

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        qapp.processEvents()

        assert window._setup_defaults_label.isHidden()
        assert window._setup_retention_label.isHidden()
        assert create_backup.isEnabled()
        assert create_backup.text() == "Fortsett"
        assert detail_row.isHidden()
        assert activity_bar.isHidden()
        assert dashboard_scroll.verticalScrollBar().value() == 0
        panel_position = setup_panel.mapTo(
            dashboard_scroll.viewport(), setup_panel.rect().topLeft()
        )
        action_position = create_backup.mapTo(
            dashboard_scroll.viewport(), create_backup.rect().topLeft()
        )
        assert panel_position.y() >= 0
        assert action_position.y() >= 0
        assert (
            action_position.y() + create_backup.height()
            <= dashboard_scroll.viewport().height()
        )
        assert setup_panel.rect().contains(
            create_backup.mapTo(setup_panel, create_backup.rect().topLeft())
        )
        assert dashboard_scroll.horizontalScrollBar().maximum() == 0
        assert dashboard_scroll.verticalScrollBar().maximum() == 0

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert window._setup_state.current_step is BackupSetupStep.DEFAULTS
        assert setup_back.isVisible() and setup_back.isEnabled()
        QTest.mouseClick(setup_back, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert window._setup_state.current_step is BackupSetupStep.TARGETS
    finally:
        window.close()
        window.deleteLater()


def test_directory_picker_is_parented_and_uses_visible_qt_dialog(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.LIGHT)

    try:
        dialog = window._build_directory_picker("Choose source folder")

        assert dialog.parent() is window
        assert dialog.objectName() == "directoryPickerDialog"
        assert dialog.windowTitle() == "Choose source folder"
        assert dialog.fileMode() is QFileDialog.FileMode.Directory
        assert dialog.acceptMode() is QFileDialog.AcceptMode.AcceptOpen
        assert dialog.isModal() is True
        assert dialog.windowModality() is Qt.WindowModality.WindowModal
        assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog) is True
        assert dialog.testOption(QFileDialog.Option.ShowDirsOnly) is True
    finally:
        window.close()
        window.deleteLater()


def test_directory_picker_returns_packaged_qt_acceptance_value(qapp) -> None:
    window = build_main_window(initial_state=_ready_state(), theme_mode=ThemeMode.LIGHT)

    class AcceptedDirectoryDialog:
        def show(self) -> None:
            calls.append("show")

        def raise_(self) -> None:
            calls.append("raise")

        def activateWindow(self) -> None:
            calls.append("activate")

        def exec(self) -> int:
            calls.append("exec")
            return 1

        def selectedFiles(self) -> list[str]:
            return ["C:/Users/Ada/Pictures"]

    calls: list[str] = []
    try:
        window._build_directory_picker = (  # type: ignore[method-assign]
            lambda title: AcceptedDirectoryDialog()
        )

        assert (
            window._choose_directory("Choose source folder") == "C:/Users/Ada/Pictures"
        )
        assert calls == ["show", "raise", "activate", "exec"]
    finally:
        window.close()
        window.deleteLater()


def test_setup_target_controls_persist_multiple_reviewed_targets(qapp) -> None:
    provider = _FakeBackupCreationEngineClient()
    window = build_main_window(
        initial_state=_ready_state(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        qapp.processEvents()
        choices = [
            "C:/Users/Ada/Pictures",
            "E:/MediaSyncBackup",
            "F:/OffsiteBackup",
            "G:/TemporaryBackup",
        ]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        remove_targets = window.findChildren(QToolButton, "removeTargetButton")
        target_paths = window.findChildren(QLabel, "setupTargetPathRow")
        engine_detail = window.findChild(QLabel, "engineDetailLabel")

        assert create_backup is not None
        assert add_target is not None
        assert setup_back is not None
        assert len(remove_targets) == 3
        assert len(target_paths) == 3
        assert engine_detail is not None
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        for _ in range(3):
            QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
            qapp.processEvents()

        assert add_target.isEnabled() is False
        assert [label.text() for label in target_paths] == [
            "E:/MediaSyncBackup",
            "F:/OffsiteBackup",
            "G:/TemporaryBackup",
        ]
        assert [label.accessibleName() for label in target_paths] == [
            "MediaSyncBackup: E:/MediaSyncBackup",
            "OffsiteBackup: F:/OffsiteBackup",
            "TemporaryBackup: G:/TemporaryBackup",
        ]

        QTest.mouseClick(remove_targets[1], Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert add_target.isEnabled() is True
        assert [label.text() for label in target_paths[:2]] == [
            "E:/MediaSyncBackup",
            "G:/TemporaryBackup",
        ]
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert add_target.isHidden() is True
        QTest.mouseClick(setup_back, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert add_target.isVisible() is True
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.calls == ["connect", "create_standard_backup_job"]
        assert provider.draft is not None
        assert provider.draft.source_path_label == "C:/Users/Ada/Pictures"
        assert [target.path_label for target in provider.draft.targets] == [
            "E:/MediaSyncBackup",
            "G:/TemporaryBackup",
        ]
        assert provider.draft.can_create() is True
        assert engine_detail.text() == (
            "Backupjobben og registreringen av skrivbart mål ble lagret."
        )
    finally:
        window.close()
        window.deleteLater()


def test_failed_target_registration_keeps_review_and_retry_without_clipping(
    qapp,
) -> None:
    provider = _FakeFailedRegistrationEngineClient()
    window = build_main_window(
        initial_state=_ready_state(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        choices = [
            "C:/Users/Ada/A very long source folder for important files",
            "E:/MediaSync/A very long target folder for the complete backup",
        ]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        target = window.findChild(QLabel, "setupTargetValue")
        engine_detail = window.findChild(QLabel, "engineDetailLabel")
        dashboard_scroll = window.findChild(QScrollArea, "dashboardScrollArea")

        assert create_backup is not None
        assert add_target is not None
        assert setup_back is not None
        assert target is not None
        assert engine_detail is not None
        assert dashboard_scroll is not None
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        for _ in range(3):
            QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
            qapp.processEvents()

        assert create_backup.text() == "Prøv målregistrering igjen"
        assert create_backup.isEnabled() is True
        assert setup_back.isHidden() is True
        assert target.text().startswith("1 mål:")
        assert "WRITABLE_ENDPOINT_PROBE_FAILED" in engine_detail.text()
        assert dashboard_scroll.horizontalScrollBar().maximum() == 0

        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.calls.count("create_standard_backup_job") == 2
        assert target.text().startswith("1 mål:")
        assert dashboard_scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        window.deleteLater()


def test_settings_apply_and_persist_with_private_diagnostics(
    qapp,
    tmp_path,
) -> None:
    state = engine_status_from_response(
        IpcResponse.accepted(
            {
                "host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict(),
                "state_capacity": {
                    "status": "READY",
                    "reason_code": "STATE_CAPACITY_READY",
                    "state_size_bytes": 4096,
                    "local_free_space_bytes": 8192,
                    "measurement_complete": True,
                },
            }
        )
    )
    store = _MemoryUserPreferencesStore()
    opened: list[object] = []
    data_root = tmp_path / "private-user" / "state"
    window = build_main_window(
        initial_state=state,
        user_preferences=UserPreferences(appearance=AppearancePreference.LIGHT),
        user_preferences_store=store,
        data_root=data_root,
        open_data_folder=lambda path: opened.append(path) is None,
    )

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        settings_scroll = window.findChild(QScrollArea, "settingsScrollArea")
        theme_buttons = window.findChildren(QPushButton, "settingsModeButton")
        density = window.findChild(QComboBox, "settingsDensityCombo")
        language = window.findChild(QComboBox, "settingsLanguageCombo")
        reduced_motion = window.findChild(QCheckBox, "settingsReducedMotionCheck")
        state_usage = window.findChild(QLabel, "settingsStateUsageValue")
        free_space = window.findChild(QLabel, "settingsFreeSpaceValue")
        open_button = window.findChildren(QPushButton, "settingsActionButton")[0]
        copy_button = window.findChildren(QPushButton, "settingsActionButton")[1]

        assert nav is not None
        nav.setCurrentRow(3)
        qapp.processEvents()
        assert settings_scroll is not None
        assert settings_scroll.horizontalScrollBar().maximum() == 0
        assert state_usage is not None
        assert state_usage.text() == "4.0 KiB"
        assert free_space is not None
        assert free_space.text() == "8.0 KiB"
        retention_label = next(
            label
            for label in window.findChildren(QLabel)
            if label.text() == "Versjonsbevaring"
        )
        assert retention_label.isVisible()
        assert retention_label.width() >= 100

        dark = next(button for button in theme_buttons if button.text() == "Mørk")
        QTest.mouseClick(dark, Qt.MouseButton.LeftButton)
        density.setCurrentIndex(density.findData(DensityPreference.COMPACT.value))
        reduced_motion.setChecked(True)
        language.setCurrentIndex(language.findData(UserLanguage.ENGLISH.value))
        qapp.processEvents()

        assert store.saved[-1] == UserPreferences(
            appearance=AppearancePreference.DARK,
            density=DensityPreference.COMPACT,
            reduced_motion=True,
            language=UserLanguage.ENGLISH,
        )
        assert window.centralWidget().property("densityMode") == "compact"
        assert window.centralWidget().property("reducedMotion") is True
        assert nav.item(3).text() == "Settings"
        assert "#151a1d" in qapp.styleSheet()
        assert settings_scroll.horizontalScrollBar().maximum() == 0

        QTest.mouseClick(open_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(copy_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        report = qapp.clipboard().text()
        assert opened == [data_root]
        assert "MediaSync Home diagnostics" in report
        assert "capacity_status: READY" in report
        assert str(data_root) not in report
        assert "private-user" not in report
    finally:
        window.close()
        window.deleteLater()


def test_main_window_loads_stored_preferences_on_next_launch(qapp, tmp_path) -> None:
    store = LocalUserPreferencesStore(tmp_path / "user-preferences.json")
    store.save(
        UserPreferences(
            appearance=AppearancePreference.DARK,
            density=DensityPreference.COMPACT,
            reduced_motion=True,
            language=UserLanguage.ENGLISH,
        )
    )

    window = build_main_window(
        initial_state=_ready_state(),
        theme_mode=ThemeMode.LIGHT,
        user_preferences_store=store,
    )

    try:
        nav = window.findChild(QListWidget, "navigationRail")

        assert nav is not None
        assert nav.item(0).text() == "Dashboard"
        assert window.centralWidget().property("densityMode") == "compact"
        assert window.centralWidget().property("reducedMotion") is True
        assert "#151a1d" in qapp.styleSheet()
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
        window.show()
        qapp.processEvents()
        refresh = window.findChild(QPushButton, "refreshEngineButton")
        assert refresh is not None
        QTest.mouseClick(refresh, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        chip = window.findChild(QLabel, "engineStatusChip")

        assert provider.calls == ["connect", "get_status"]
        assert chip is not None
        assert chip.text() == "Tilkoblet: Klar"
    finally:
        window.close()
        window.deleteLater()


def test_main_window_refresh_recovers_after_engine_timeout(qapp) -> None:
    provider = _FakeEngineClient()

    def fail_connect() -> IpcResponse:
        provider.calls.append("connect")
        raise TimeoutError("host did not answer")

    provider.connect = fail_connect  # type: ignore[method-assign]
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        qapp.processEvents()
        refresh = window.findChild(QPushButton, "refreshEngineButton")
        chip = window.findChild(QLabel, "engineStatusChip")
        detail = window.findChild(QLabel, "engineDetailLabel")

        assert refresh is not None
        assert chip is not None
        assert detail is not None
        QTest.mouseClick(refresh, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.calls == ["connect"]
        assert refresh.isEnabled() is True
        assert chip.text() == "Frakoblet: Venter"
        assert detail.text() == "Motorstatus er utilgjengelig."
    finally:
        window.close()
        window.deleteLater()


def test_main_window_refresh_recovers_engine_client_from_factory(qapp) -> None:
    provider = _FakeEngineClient()
    factory_calls: list[str] = []

    def engine_client_factory() -> _FakeEngineClient:
        factory_calls.append("resolve")
        return provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client_factory=engine_client_factory,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.refresh_engine_status()
        chip = window.findChild(QLabel, "engineStatusChip")

        assert factory_calls == ["resolve"]
        assert provider.calls == ["connect", "get_status"]
        assert chip is not None
        assert chip.text() == "Tilkoblet: Klar"
    finally:
        window.close()
        window.deleteLater()


def test_background_status_stall_keeps_navigation_and_language_responsive(
    qapp,
) -> None:
    provider = _FakeDashboardEngineClient()
    worker_provider = _BlockingStatusDashboardEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingStatusDashboardEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        refresh = window.findChild(QPushButton, "refreshEngineButton")
        nav = window.findChild(QListWidget, "navigationRail")
        heading = window.findChild(QLabel, "workspaceHeading")
        chip = window.findChild(QLabel, "engineStatusChip")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert refresh is not None
        assert nav is not None
        assert heading is not None
        assert chip is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(refresh, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        assert not refresh.isEnabled()
        assert not worker_provider.release.is_set()

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(1)).center(),
        )
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert nav.currentRow() == 3
        assert heading.text() == "Settings"
        assert not worker_provider.release.is_set()
        assert window._background_queries is not None
        assert window._background_queries.pending_count == 1
        worker_provider.release.set()

        def dashboard_settled() -> bool:
            return (
                not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and window._job_detail_state.job_id == "job-a"
                and window._plan_preview_state.plan_id == "plan-a"
            )

        deadline = monotonic() + 4
        while not dashboard_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert dashboard_settled()

        assert len(factory_calls) == 1
        assert worker_provider.calls.count("connect") == 1
        assert worker_provider.calls.count("get_status") == 1
        assert worker_provider.calls.count("get_backup_overview") == 2
        assert worker_provider.calls.count("get_backup_job_detail") == 1
        assert refresh.isEnabled()
        assert chip.text() == "Connected: Ready"
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_background_status_completion_preserves_active_local_setup(qapp) -> None:
    provider = _FakeDashboardEngineClient()
    worker_provider = _BlockingStatusDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        refresh = window.findChild(QPushButton, "refreshEngineButton")
        source_action = window.findChild(QPushButton, "createBackupButton")
        source_value = window.findChild(QLabel, "setupSourceValue")
        detail_row = window.findChild(QWidget, "dashboardDetailRow")
        activity_bar = window.findChild(QFrame, "activityBar")
        assert refresh is not None
        assert source_action is not None
        assert source_value is not None
        assert detail_row is not None
        assert activity_bar is not None

        QTest.mouseClick(refresh, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        assert not refresh.isEnabled()
        selected_source = "C:/Users/Ada/Pictures"
        window._choose_directory = lambda _title: selected_source  # type: ignore[method-assign]
        QTest.mouseClick(source_action, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert window._setup_state.current_step is BackupSetupStep.TARGETS
        assert window._setup_draft.source_path_label == selected_source
        assert source_value.text() == selected_source
        assert detail_row.isHidden()
        assert activity_bar.isHidden()

        worker_provider.release.set()

        def reads_settled() -> bool:
            return (
                window._background_queries is not None
                and not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
            )

        deadline = monotonic() + 4
        while not reads_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)

        assert reads_settled()
        assert refresh.isEnabled()
        assert window._setup_state.current_step is BackupSetupStep.TARGETS
        assert window._setup_draft.source_path_label == selected_source
        assert source_value.text() == selected_source
        assert source_action.text() == "Fortsett"
        assert source_action.isEnabled() is False
        assert detail_row.isHidden()
        assert activity_bar.isHidden()
    finally:
        worker_provider.release.set()
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
        setup_back = window.findChild(QToolButton, "setupBackButton")
        jobs_list = window.findChild(QListWidget, "jobsList")
        jobs_empty = window.findChild(QLabel, "jobsEmptyLabel")
        jobs_detail_title = window.findChild(QLabel, "jobsDetailTitle")
        jobs_detail_source = window.findChild(QLabel, "jobsDetailSourceValue")
        jobs_detail_targets = window.findChild(QLabel, "jobsDetailTargetsValue")
        jobs_start = window.findChild(QPushButton, "jobsStartBackupButton")
        activity_title = window.findChild(QLabel, "activityStatusTitle")
        activity_rows = window.findChildren(QLabel, "activityDimensionLabel")
        job_detail_title = window.findChild(QLabel, "jobDetailTitle")
        job_detail_source = window.findChild(QLabel, "jobDetailSourceValue")
        job_detail_targets = window.findChild(QLabel, "jobDetailTargetsValue")
        job_detail_defaults = window.findChild(QLabel, "jobDetailDefaultsValue")
        job_detail_revision = window.findChild(QLabel, "jobDetailRevisionValue")
        job_detail_plan = window.findChild(QLabel, "jobDetailPlanValue")
        job_detail_rows = window.findChildren(QLabel, "jobDetailTargetRow")
        plan_preview_summary = window.findChild(QLabel, "planPreviewSummary")
        plan_preview_rows = window.findChildren(QLabel, "planPreviewRow")
        plan_endpoint_summary = window.findChild(QLabel, "planEndpointSummary")
        plan_endpoint_rows = window.findChildren(QLabel, "planEndpointRow")
        snapshot_health_summary = window.findChild(QLabel, "snapshotHealthSummary")
        snapshot_health_rows = window.findChildren(QLabel, "snapshotHealthRow")
        cataloged_files_summary = window.findChild(QLabel, "catalogedFilesSummary")
        cataloged_files_rows = window.findChildren(QLabel, "catalogedFilesRow")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert provider.calls == [
            "connect",
            "get_status",
            "get_backup_overview",
            "get_backup_job_detail",
            "get_activity_overview",
            "get_plan_operations",
            "get_plan_endpoints",
            "get_snapshot_issues",
            "get_snapshot_coverage",
            "get_cataloged_files",
        ]
        assert source is not None
        assert source.text() == "C:/Users/Ada/Pictures"
        assert target is not None
        assert target.text() == "1 mål: USB 1"
        assert create_backup is not None
        assert create_backup.isEnabled() is True
        assert setup_back is not None
        assert setup_back.isHidden() is True
        assert jobs_list is not None
        assert jobs_list.count() == 1
        assert jobs_list.currentItem() is not None
        assert jobs_list.currentItem().data(Qt.ItemDataRole.UserRole) == "job-a"
        assert jobs_list.currentItem().text() == ("Pictures\n1 mål / 1 uavhengig enhet")
        assert jobs_empty is not None
        assert jobs_empty.isHidden() is True
        assert jobs_detail_title is not None
        assert jobs_detail_title.text() == "Pictures"
        assert jobs_detail_source is not None
        assert jobs_detail_source.text() == "C:/Users/Ada/Pictures"
        assert jobs_detail_targets is not None
        assert jobs_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert jobs_start is not None
        assert jobs_start.isHidden() is False
        assert jobs_start.isEnabled() is False
        assert job_detail_title is not None
        assert job_detail_title.text() == "Pictures"
        assert job_detail_source is not None
        assert job_detail_source.text() == "C:/Users/Ada/Pictures"
        assert job_detail_targets is not None
        assert job_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert job_detail_defaults is not None
        assert (
            job_detail_defaults.text()
            == "Oppdater backup - Alle brukerfiler - Standard kontroll"
        )
        assert job_detail_revision is not None
        assert job_detail_revision.text() == "Revisjon: job-rev-a - Filter: filter-a"
        assert job_detail_plan is not None
        assert job_detail_plan.text().startswith("2 operasjoner fra plan-a.")
        assert "2048 B" in job_detail_plan.text()
        assert job_detail_rows[0].text() == (
            "USB 1: E:/Backup · Skrivbar og registrert"
        )
        assert plan_preview_summary is not None
        assert plan_preview_summary.text() == "2 operasjoner fra plan-a."
        assert plan_preview_rows[0].text() == "Lav: Opprett mappe: Photos -> target-a"
        assert plan_preview_rows[1].text() == (
            "Lav: Kopier ny: Photos/2026/a.jpg - 2.0 KiB -> target-a"
        )
        assert plan_endpoint_summary is not None
        assert plan_endpoint_summary.text() == "2 endepunkter fra plan-a."
        assert (
            plan_endpoint_rows[0].text()
            == "Kildeendepunkt: source-a · snapshot source-snapshot-a"
        )
        assert (
            plan_endpoint_rows[1].text()
            == "Målendepunkt 1: target-a · snapshot target-snapshot-a"
        )
        assert snapshot_health_summary is not None
        assert (
            snapshot_health_summary.text()
            == "1 blokkerende problem i source-snapshot-a."
        )
        assert (
            snapshot_health_rows[0].text()
            == "Blokkerende problem: Archive · UNREADABLE_DIRECTORY"
        )
        assert snapshot_health_rows[1].text() == "Dekningsadvarsel: Videos · VOLATILE"
        assert cataloged_files_summary is not None
        assert cataloged_files_summary.text() == (
            "1 katalogf\u00f8rt fil. Flere katalogf\u00f8rte filer finnes."
        )
        assert (
            cataloged_files_rows[0].text()
            == "Photos/2026/a.jpg · target-a · sha abcdef01"
        )
        assert activity_title is not None
        assert activity_title.text() == "Siste kjøring: run-a"
        assert activity_rows[0].text() == "Aktivitet: Kontrollerer"
        assert activity_rows[1].text() == "Oppmerksomhet: Venter"
        assert (
            activity_rows[2]
            .text()
            .startswith("Ferskhet per mål: target-a: Sist sikkerhetskopiert")
        )
        assert "Siste vellykkede:" in activity_rows[2].text()
        assert "19.07.2026" in activity_rows[2].text()
        assert "target-a: Kontrollerer måltilgang." in activity_rows[3].text()
        assert language is not None
        assert language.menu() is not None
        language.menu().actions()[1].trigger()

        assert target.text() == "1 target: USB 1"
        assert jobs_list.currentItem().text() == (
            "Pictures\n1 target / 1 independent device"
        )
        assert jobs_detail_targets.text() == "1 target / 1 independent device"
        assert job_detail_targets.text() == "1 target / 1 independent device"
        assert (
            job_detail_defaults.text()
            == "Update backup - All user files - Standard verification"
        )
        assert job_detail_revision.text() == "Revision: job-rev-a - Filter: filter-a"
        assert job_detail_plan.text().startswith("2 operations from plan-a.")
        assert job_detail_plan.text().endswith("Preview only")
        assert plan_preview_summary.text() == "2 operations from plan-a."
        assert plan_preview_rows[0].text() == ("Low: Create folder: Photos -> target-a")
        assert plan_preview_rows[1].text() == (
            "Low: Copy new: Photos/2026/a.jpg - 2.0 KiB -> target-a"
        )
        assert plan_endpoint_summary.text() == "2 endpoints from plan-a."
        assert (
            plan_endpoint_rows[0].text()
            == "Source endpoint: source-a · snapshot source-snapshot-a"
        )
        assert (
            plan_endpoint_rows[1].text()
            == "Target endpoint 1: target-a · snapshot target-snapshot-a"
        )
        assert (
            snapshot_health_summary.text() == "1 blocking issue in source-snapshot-a."
        )
        assert (
            snapshot_health_rows[0].text()
            == "Blocking issue: Archive · UNREADABLE_DIRECTORY"
        )
        assert snapshot_health_rows[1].text() == "Coverage warning: Videos · VOLATILE"
        assert cataloged_files_summary.text() == (
            "1 cataloged file. More cataloged files exist."
        )
        assert (
            cataloged_files_rows[0].text()
            == "Photos/2026/a.jpg · target-a · sha abcdef01"
        )
        assert activity_title.text() == "Latest run: run-a"
        assert activity_rows[0].text() == "Activity: Checking"
        assert activity_rows[1].text() == "Attention: Waiting"
        assert (
            activity_rows[2]
            .text()
            .startswith("Freshness per target: target-a: Last backed up")
        )
        assert "Last successful:" in activity_rows[2].text()
        assert "2026-07-19" in activity_rows[2].text()
        assert "target-a: Checking target access." in activity_rows[3].text()

        language.menu().actions()[0].trigger()

        assert target.text() == "1 mål: USB 1"
        assert jobs_list.currentItem().text() == ("Pictures\n1 mål / 1 uavhengig enhet")
        assert jobs_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert job_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert (
            job_detail_defaults.text()
            == "Oppdater backup - Alle brukerfiler - Standard kontroll"
        )
        assert job_detail_revision.text() == "Revisjon: job-rev-a - Filter: filter-a"
        assert job_detail_plan.text().startswith("2 operasjoner fra plan-a.")
        assert plan_preview_summary.text() == "2 operasjoner fra plan-a."
        assert plan_preview_rows[0].text() == "Lav: Opprett mappe: Photos -> target-a"
        assert plan_preview_rows[1].text() == (
            "Lav: Kopier ny: Photos/2026/a.jpg - 2.0 KiB -> target-a"
        )
        assert plan_endpoint_summary.text() == "2 endepunkter fra plan-a."
        assert (
            plan_endpoint_rows[0].text()
            == "Kildeendepunkt: source-a · snapshot source-snapshot-a"
        )
        assert (
            plan_endpoint_rows[1].text()
            == "Målendepunkt 1: target-a · snapshot target-snapshot-a"
        )
        assert (
            snapshot_health_summary.text()
            == "1 blokkerende problem i source-snapshot-a."
        )
        assert (
            snapshot_health_rows[0].text()
            == "Blokkerende problem: Archive · UNREADABLE_DIRECTORY"
        )
        assert snapshot_health_rows[1].text() == "Dekningsadvarsel: Videos · VOLATILE"
        assert cataloged_files_summary.text() == (
            "1 katalogf\u00f8rt fil. Flere katalogf\u00f8rte filer finnes."
        )
        assert activity_title.text() == "Siste kjøring: run-a"
        assert activity_rows[0].text() == "Aktivitet: Kontrollerer"
        assert activity_rows[1].text() == "Oppmerksomhet: Venter"
        assert (
            activity_rows[2]
            .text()
            .startswith("Ferskhet per mål: target-a: Sist sikkerhetskopiert")
        )
        assert "target-a: Kontrollerer måltilgang." in activity_rows[3].text()
    finally:
        window.close()
        window.deleteLater()


def test_jobs_workspace_selects_job_and_starts_its_exact_plan(qapp) -> None:
    provider = _FakeMultiJobDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        jobs_list = window.findChild(QListWidget, "jobsList")
        jobs_detail_title = window.findChild(QLabel, "jobsDetailTitle")
        jobs_detail_source = window.findChild(QLabel, "jobsDetailSourceValue")
        jobs_start = window.findChild(QPushButton, "jobsStartBackupButton")
        dashboard_detail_title = window.findChild(QLabel, "jobDetailTitle")
        jobs_scroll = window.findChild(QScrollArea, "jobsScrollArea")

        assert nav is not None
        assert jobs_list is not None
        assert jobs_list.count() == 2
        assert jobs_detail_title is not None
        assert jobs_detail_source is not None
        assert jobs_start is not None
        assert dashboard_detail_title is not None
        assert jobs_scroll is not None

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(1)).center(),
        )
        qapp.processEvents()
        QTest.mouseClick(
            jobs_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=jobs_list.visualItemRect(jobs_list.item(1)).center(),
        )
        qapp.processEvents()

        assert jobs_list.currentItem() is not None
        assert jobs_list.currentItem().data(Qt.ItemDataRole.UserRole) == "job-b"
        assert provider.requested_job_ids[-1] == "job-b"
        assert jobs_detail_title.text() == "Documents"
        assert jobs_detail_source.text() == "C:/Users/Ada/Documents"
        assert dashboard_detail_title.text() == "Documents"
        assert jobs_start.isVisible() is True
        assert jobs_start.isEnabled() is True
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
        jobs_page = jobs_scroll.widget()
        assert jobs_page is not None
        for label in jobs_page.findChildren(QLabel):
            if label.property("responsiveText") and not label.isHidden():
                assert label.height() >= label.heightForWidth(label.width())

        QTest.mouseClick(jobs_start, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.started_plan == ("plan-b", "b" * 64)
        assert jobs_start.text() == "Backup er lagt i kø"
        assert jobs_start.isEnabled() is False
    finally:
        window.close()
        window.deleteLater()


def test_jobs_workspace_pages_without_losing_bounded_query_state(qapp) -> None:
    provider = _FakePagedJobsEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        jobs_list = window.findChild(QListWidget, "jobsList")
        previous = window.findChild(QToolButton, "jobsPreviousButton")
        next_button = window.findChild(QToolButton, "jobsNextButton")

        assert nav is not None
        assert jobs_list is not None
        assert previous is not None
        assert next_button is not None
        assert jobs_list.item(0).data(Qt.ItemDataRole.UserRole) == "job-a"
        assert previous.isEnabled() is False
        assert next_button.isEnabled() is True
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(1)).center(),
        )
        qapp.processEvents()

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.requested_offsets[-1] == 25
        assert jobs_list.count() == 1
        assert jobs_list.item(0).data(Qt.ItemDataRole.UserRole) == "job-z"
        assert previous.isEnabled() is True
        assert next_button.isEnabled() is False

        QTest.mouseClick(previous, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.requested_offsets[-1] == 0
        assert jobs_list.item(0).data(Qt.ItemDataRole.UserRole) == "job-a"
    finally:
        window.close()
        window.deleteLater()


def test_jobs_workspace_filters_archived_jobs_and_reactivates_without_clipping(
    qapp,
) -> None:
    provider = _FakeJobLifecycleDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(760, 520)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        lifecycle_filter = window.findChild(QComboBox, "jobsLifecycleFilter")
        lifecycle_button = window.findChild(QPushButton, "jobsLifecycleButton")
        jobs_scroll = window.findChild(QScrollArea, "jobsScrollArea")
        detail_panel = window.findChild(QFrame, "jobsDetailPanel")

        assert lifecycle_filter is not None
        assert [lifecycle_filter.itemText(index) for index in range(2)] == [
            "Aktive",
            "Arkiverte",
        ]
        assert lifecycle_button is not None
        assert lifecycle_button.text() == "Arkiver jobb"
        assert lifecycle_button.isEnabled()

        lifecycle_filter.setCurrentIndex(lifecycle_filter.findData("ARCHIVED"))
        qapp.processEvents()

        assert provider.lifecycle_queries[-1] == "ARCHIVED"
        assert lifecycle_button.text() == "Aktiver igjen"
        assert lifecycle_button.isEnabled()
        language = window.findChild(QToolButton, "languageSelectorButton")
        assert language is not None and language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert lifecycle_filter.itemText(0) == "Active"
        assert lifecycle_filter.itemText(1) == "Archived"
        assert lifecycle_button.text() == "Reactivate"

        assert window._change_selected_job_lifecycle()
        qapp.processEvents()
        assert provider.lifecycle_commands == ["REACTIVATE"]
        assert jobs_scroll is not None
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
        assert detail_panel is not None
        assert (
            lifecycle_button.geometry().right() <= detail_panel.contentsRect().right()
        )
    finally:
        window.close()
        window.deleteLater()


def test_background_job_selection_keeps_only_latest_detail(qapp) -> None:
    provider = _FakeMultiJobDashboardEngineClient()
    worker_provider = _BlockingMultiJobDashboardEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingMultiJobDashboardEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._selected_navigation_index = 1
        assert window._workspace_stack is not None
        window._workspace_stack.setCurrentIndex(1)
        qapp.processEvents()
        jobs_list = window.findChild(QListWidget, "jobsList")
        title = window.findChild(QLabel, "jobsDetailTitle")
        start = window.findChild(QPushButton, "jobsStartBackupButton")
        jobs_scroll = window.findChild(QScrollArea, "jobsScrollArea")

        assert jobs_list is not None and jobs_list.count() == 2
        assert title is not None
        assert start is not None
        assert jobs_scroll is not None
        QTest.mouseClick(
            jobs_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=jobs_list.visualItemRect(jobs_list.item(1)).center(),
        )
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()

        assert worker_provider.attempted_job_ids == ["job-b"]
        assert jobs_list.isEnabled()
        assert not start.isEnabled()
        assert not worker_provider.release.is_set()
        QTest.mouseClick(
            jobs_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=jobs_list.visualItemRect(jobs_list.item(0)).center(),
        )
        assert window._background_queries is not None
        assert window._background_queries.pending_count == 2
        worker_provider.release.set()

        def selection_settled() -> bool:
            return (
                not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and window._selected_job_id == "job-a"
                and window._job_detail_state.job_id == "job-a"
                and window._plan_preview_state.plan_id == "plan-a"
            )

        deadline = monotonic() + 4
        while not selection_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert selection_settled()

        assert len(factory_calls) == 1
        assert worker_provider.max_active == 1
        assert worker_provider.requested_job_ids == ["job-b", "job-a"]
        assert jobs_list.currentItem() is not None
        assert jobs_list.currentItem().data(Qt.ItemDataRole.UserRole) == "job-a"
        assert title.text() == "Pictures"
        assert jobs_list.isEnabled()
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_background_jobs_page_locks_stale_rows_but_not_navigation(qapp) -> None:
    provider = _FakePagedJobsEngineClient()
    worker_provider = _BlockingPagedJobsEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingPagedJobsEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._selected_navigation_index = 1
        assert window._workspace_stack is not None
        window._workspace_stack.setCurrentIndex(1)
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        jobs_list = window.findChild(QListWidget, "jobsList")
        previous = window.findChild(QToolButton, "jobsPreviousButton")
        next_button = window.findChild(QToolButton, "jobsNextButton")

        assert nav is not None
        assert jobs_list is not None
        assert previous is not None
        assert next_button is not None and next_button.isEnabled()
        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()

        assert not jobs_list.isEnabled()
        assert not previous.isEnabled()
        assert not next_button.isEnabled()
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        qapp.processEvents()
        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()
        worker_provider.release.set()

        assert window._background_queries is not None

        def page_settled() -> bool:
            return (
                not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and window._backup_overview_state.offset == 25
                and window._job_detail_state.job_id == "job-z"
            )

        deadline = monotonic() + 4
        while not page_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert page_settled()

        assert len(factory_calls) == 1
        assert worker_provider.max_active == 1
        assert worker_provider.requested_offsets == [25]
        assert jobs_list.count() == 1
        assert jobs_list.item(0).data(Qt.ItemDataRole.UserRole) == "job-z"
        assert jobs_list.isEnabled()
        assert previous.isEnabled()
        assert not next_button.isEnabled()
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_jobs_changes_workspace_filters_pages_and_localizes_without_clipping(
    qapp,
) -> None:
    provider = _FakeChangesDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        title = window.findChild(QLabel, "changesTitle")
        banner = window.findChild(QLabel, "changesAttentionBanner")
        target_filter = window.findChild(QComboBox, "changesTargetFilter")
        risk_filter = window.findChild(QComboBox, "changesRiskFilter")
        changes_list = window.findChild(BoundedVirtualTableView, "changesList")
        next_button = window.findChild(QToolButton, "changesNextButton")
        detail_reason = window.findChild(QLabel, "changesDetailReasonValue")
        detail_precondition = window.findChild(
            QLabel,
            "changesDetailPreconditionValue",
        )
        detail_target = window.findChild(QLabel, "changesDetailTargetValue")
        jobs_scroll = window.findChild(QScrollArea, "jobsScrollArea")
        changes_panel = window.findChild(QFrame, "changesPanel")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert title is not None and title.text() == "Endringer"
        assert banner is not None
        assert banner.text() == ("Krever oppmerksomhet: 1 blokkert, 3 må vurderes.")
        assert banner.property("attentionKind") == "blocked"
        assert target_filter is not None
        assert [target_filter.itemText(index) for index in range(3)] == [
            "Alle mål",
            "target-a",
            "target-b",
        ]
        assert risk_filter is not None
        assert [risk_filter.itemText(index) for index in range(3)] == [
            "Alle endringer",
            "Krever oppmerksomhet",
            "Trygge endringer",
        ]
        assert changes_list is not None and _virtual_row_count(changes_list) == 2
        assert changes_list.bounded_model.max_cached_rows == 200
        assert jobs_scroll is not None
        jobs_scroll.ensureWidgetVisible(changes_list)
        qapp.processEvents()
        assert changes_list.horizontalScrollBar().maximum() == 0
        assert changes_list.isColumnHidden(3)
        assert _virtual_row_text(changes_list, 0).startswith("Trygg · Kopier ny")
        assert _virtual_row_text(changes_list, 1).startswith("Vurder · Kopier ny")

        _select_virtual_row(changes_list, 1)
        qapp.processEvents()
        assert detail_reason is not None
        assert detail_reason.text() == "TARGET_CONTENT_DIFFERS"
        assert detail_precondition is not None
        assert detail_precondition.text() == "Må samsvare med kontrollert fil"
        assert detail_target is not None and detail_target.text() == "target-b"

        assert next_button is not None and next_button.isEnabled()
        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert window._changes_page_index == 1
        assert window._changes_page_label is not None
        assert window._changes_page_label.text() == "Side 2"
        assert _virtual_row_count(changes_list) == 2
        assert _virtual_row_text(changes_list, 0).startswith("Høy risiko · Utsatt")
        assert _virtual_row_text(changes_list, 1).startswith("Blokkert · Blokkert")

        risk_filter.setCurrentIndex(risk_filter.findData("ATTENTION"))
        qapp.processEvents()
        assert window._changes_page_index == 0
        target_filter.setCurrentIndex(target_filter.findData("target-b"))
        qapp.processEvents()
        assert _virtual_row_count(changes_list) == 2
        assert all(
            "target-b" in _virtual_row_text(changes_list, index) for index in range(2)
        )
        assert provider.changes_queries[-1][3:] == (
            "target-b",
            ("MEDIUM", "HIGH", "BLOCKED"),
        )

        assert language is not None and language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert title.text() == "Changes"
        assert banner.text() == "Needs attention: 1 blocked, 3 require review."
        assert risk_filter.itemText(1) == "Needs attention"
        assert _virtual_row_text(changes_list, 0).startswith("Review · Copy new")
        assert jobs_scroll is not None
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
        assert changes_panel is not None
        for label in changes_panel.findChildren(QLabel):
            if label.property("responsiveText") and not label.isHidden():
                assert label.height() >= label.heightForWidth(label.width())
    finally:
        window.close()
        window.deleteLater()


def test_changes_next_page_uses_one_page_prefetch(qapp) -> None:
    provider = _FakeChangesDashboardEngineClient()
    worker_provider = _FakeChangesDashboardEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _FakeChangesDashboardEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.show()
        window._changes_plan_id = "plan-a"
        window._refresh_changes_page()
        changes_list = window.findChild(BoundedVirtualTableView, "changesList")
        next_button = window.findChild(QToolButton, "changesNextButton")
        assert changes_list is not None
        assert next_button is not None

        def prefetched() -> bool:
            return (
                len(worker_provider.changes_queries) == 2
                and window._changes_page_prefetch.count == 1
                and window._page_prefetch_queries is not None
                and not window._page_prefetch_queries.active
                and window._ui_update_coalescer.pending_count == 0
            )

        deadline = monotonic() + 3
        while not prefetched() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert prefetched()
        assert len(factory_calls) == 2
        assert worker_provider.changes_queries[1][2] == {
            "execution_phase": 20,
            "stable_order_key": "photos/review.jpg",
            "operation_id": "op-review",
        }
        assert next_button.isEnabled()

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(worker_provider.changes_queries) == 2
        assert window._changes_page_index == 1
        assert window._changes_page_prefetch.count == 0
        assert _virtual_row_id(changes_list, 0) == "op-high"
        assert not window._changes_query_pending
    finally:
        window.close()
        window.deleteLater()


def test_changes_query_stall_does_not_block_navigation_and_keeps_latest_filter(
    qapp,
) -> None:
    provider = _FakeChangesDashboardEngineClient()
    worker_provider = _BlockingChangesDashboardEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingChangesDashboardEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._selected_navigation_index = 1
        assert window._workspace_stack is not None
        window._workspace_stack.setCurrentIndex(1)
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        target_filter = window.findChild(QComboBox, "changesTargetFilter")
        risk_filter = window.findChild(QComboBox, "changesRiskFilter")
        changes_list = window.findChild(BoundedVirtualTableView, "changesList")
        next_button = window.findChild(QToolButton, "changesNextButton")

        assert nav is not None
        assert target_filter is not None
        assert risk_filter is not None
        assert changes_list is not None
        assert next_button is not None and next_button.isEnabled()
        target_filter.setCurrentIndex(target_filter.findData("target-a"))
        assert worker_provider.started.wait(timeout=1)
        assert not worker_provider.release.is_set()
        assert not next_button.isEnabled()

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        qapp.processEvents()

        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()
        target_filter.setCurrentIndex(target_filter.findData("target-b"))
        risk_filter.setCurrentIndex(risk_filter.findData("ATTENTION"))
        assert window._background_queries is not None
        assert window._background_queries.pending_count == 1
        worker_provider.release.set()

        def query_settled() -> bool:
            return (
                len(worker_provider.changes_queries) == 2
                and not window._background_queries.active
                and window._ui_update_coalescer.pending_count == 0
                and len(window._changes_page_state.rows) == 2
                and all(
                    row.target_endpoint_id == "target-b"
                    for row in window._changes_page_state.rows
                )
            )

        deadline = monotonic() + 3
        while not query_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert query_settled()

        assert worker_provider.max_active == 1
        assert len(factory_calls) == 1
        assert worker_provider.changes_queries[0][3:] == (
            "target-a",
            (),
        )
        assert worker_provider.changes_queries[1][3:] == (
            "target-b",
            ("MEDIUM", "HIGH", "BLOCKED"),
        )
        assert _virtual_row_count(changes_list) == 2
        assert not next_button.isEnabled()
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_ui_update_coalescer_is_bounded_and_applies_only_latest_value(
    qapp,
) -> None:
    coalescer = UiUpdateCoalescer(interval_ms=250, max_channels=2)
    applied: list[int] = []

    for value in range(100):
        assert coalescer.submit(
            channel="progress",
            value=value,
            apply=lambda item: applied.append(int(item)),
        )
    assert coalescer.submit(
        channel="eta",
        value=1,
        apply=lambda item: None,
    )
    assert not coalescer.submit(
        channel="third-channel",
        value=1,
        apply=lambda item: None,
    )

    qapp.processEvents()
    QTest.qWait(10)
    qapp.processEvents()

    assert applied == [99]
    assert coalescer.pending_count == 0
    coalescer.deleteLater()


def test_page_prefetch_cache_keeps_only_one_exact_context() -> None:
    cache = BoundedPagePrefetchCache[str, tuple[int, ...]]()

    cache.store(context="page-a", page=(1, 2))
    cache.store(context="page-b", page=(3, 4))

    assert cache.count == 1
    assert cache.take(context="page-a") is None
    assert cache.count == 0

    cache.store(context="page-b", page=(3, 4))
    assert cache.take(context="page-b") == (3, 4)
    assert cache.count == 0


def test_background_query_close_discards_late_worker_result(qapp) -> None:
    started = Event()
    release = Event()
    applied: list[str] = []
    controller = BackgroundQueryController(
        client_factory=lambda: object(),
        max_pending=1,
    )

    def blocked_query(client: object) -> object:
        del client
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test query release timed out")
        return "late-result"

    assert controller.submit(
        key="changes-page",
        operation=blocked_query,
        on_result=lambda value: applied.append(str(value)),
    )
    assert started.wait(timeout=1)
    controller.close()
    release.set()
    deadline = monotonic() + 2
    while controller.active and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)

    assert not controller.active
    assert applied == []
    controller.deleteLater()


def test_background_query_replacement_interrupts_cancellation_aware_client(
    qapp,
) -> None:
    class InterruptibleClient:
        def __init__(self) -> None:
            self.cancellation: Event | None = None
            self.started = Event()
            self.interrupted = Event()
            self.calls: list[str] = []

        def bind_background_cancellation(
            self,
            cancellation: Event | None,
        ) -> None:
            self.cancellation = cancellation

        def query(self, name: str) -> str:
            self.calls.append(name)
            if name != "old":
                return name
            self.started.set()
            cancellation = self.cancellation
            if cancellation is None or not cancellation.wait(timeout=2):
                raise TimeoutError("old query was not cancelled")
            self.interrupted.set()
            raise InterruptedError("old query cancelled")

    client = InterruptibleClient()
    applied: list[str] = []
    errors: list[str] = []
    controller = BackgroundQueryController(
        client_factory=lambda: client,
        max_pending=1,
    )

    assert controller.submit(
        key="history-timeline",
        operation=lambda worker: client.query("old"),
        on_result=lambda value: applied.append(str(value)),
        on_error=lambda error: errors.append(str(error)),
    )
    assert client.started.wait(timeout=1)
    assert controller.submit(
        key="history-timeline",
        operation=lambda worker: client.query("new"),
        on_result=lambda value: applied.append(str(value)),
        on_error=lambda error: errors.append(str(error)),
    )

    deadline = monotonic() + 2
    while (controller.active or controller.pending_count) and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)

    assert client.interrupted.is_set()
    assert client.calls == ["old", "new"]
    assert applied == ["new"]
    assert errors == []
    assert not controller.active
    assert controller.pending_count == 0
    assert client.cancellation is None
    controller.close()
    controller.deleteLater()


def test_command_worker_is_dedicated_serial_and_reuses_its_client(qapp) -> None:
    started = Event()
    release = Event()
    factory_calls: list[object] = []
    executed: list[tuple[str, object, int]] = []
    applied: list[str] = []
    worker_client = object()
    ui_thread_id = get_ident()

    def client_factory() -> object:
        factory_calls.append(worker_client)
        return worker_client

    controller = CommandSubmissionController(client_factory=client_factory)

    def first(client: object) -> object:
        executed.append(("first", client, get_ident()))
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test command release timed out")
        return "first"

    def second(client: object) -> object:
        executed.append(("second", client, get_ident()))
        return "second"

    def accept_first(value: object) -> None:
        applied.append(str(value))
        assert controller.submit(
            name="second",
            operation=second,
            on_result=lambda result: applied.append(str(result)),
        )

    assert controller.submit(
        name="first",
        operation=first,
        on_result=accept_first,
    )
    assert started.wait(timeout=1)
    assert not controller.submit(
        name="must-not-queue",
        operation=second,
        on_result=lambda value: applied.append(str(value)),
    )
    release.set()
    deadline = monotonic() + 2
    while (controller.active or len(applied) < 2) and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)

    assert applied == ["first", "second"]
    assert [name for name, _client, _thread_id in executed] == ["first", "second"]
    assert all(client is worker_client for _name, client, _thread_id in executed)
    assert all(thread_id != ui_thread_id for _name, _client, thread_id in executed)
    assert factory_calls == [worker_client]
    assert not controller.active
    controller.close()
    controller.deleteLater()


def test_command_worker_close_discards_late_result_without_cancelling_execution(
    qapp,
) -> None:
    started = Event()
    release = Event()
    completed = Event()
    applied: list[str] = []
    controller = CommandSubmissionController(client_factory=lambda: object())

    def command(client: object) -> object:
        del client
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test command release timed out")
        completed.set()
        return "late"

    assert controller.submit(
        name="durable-command",
        operation=command,
        on_result=lambda value: applied.append(str(value)),
    )
    assert started.wait(timeout=1)
    controller.close()
    assert not controller.submit(
        name="after-close",
        operation=command,
        on_result=lambda value: applied.append(str(value)),
    )
    release.set()
    deadline = monotonic() + 2
    while controller.active and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)

    assert completed.is_set()
    assert not controller.active
    assert applied == []
    controller.deleteLater()


def test_background_query_callback_chain_stays_serial_and_replaces_pending_key(
    qapp,
) -> None:
    first_started = Event()
    release_first = Event()
    worker_lock = Lock()
    active_calls = 0
    max_active = 0
    executed: list[str] = []
    applied: list[str] = []
    controller = BackgroundQueryController(
        client_factory=lambda: object(),
        max_pending=1,
    )

    def query(name: str, *, blocked: bool = False):
        def execute(client: object) -> object:
            nonlocal active_calls, max_active
            del client
            with worker_lock:
                active_calls += 1
                max_active = max(max_active, active_calls)
            executed.append(name)
            try:
                if blocked:
                    first_started.set()
                    if not release_first.wait(timeout=2):
                        raise TimeoutError("test query release timed out")
                return name
            finally:
                with worker_lock:
                    active_calls -= 1

        return execute

    def accept_first(value: object) -> None:
        applied.append(str(value))
        assert controller.submit(
            key="detail",
            operation=query("new-detail"),
            on_result=lambda result: applied.append(str(result)),
        )

    assert controller.submit(
        key="overview",
        operation=query("overview", blocked=True),
        on_result=accept_first,
    )
    assert first_started.wait(timeout=1)
    assert controller.submit(
        key="detail",
        operation=query("stale-detail"),
        on_result=lambda value: applied.append(str(value)),
    )
    release_first.set()

    deadline = monotonic() + 2
    while (controller.active or controller.pending_count) and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)

    assert not controller.active
    assert controller.pending_count == 0
    assert max_active == 1
    assert executed == ["overview", "new-detail"]
    assert applied == ["overview", "new-detail"]
    controller.close()
    controller.deleteLater()


def test_history_workspace_filters_selects_and_localizes_without_clipping(qapp) -> None:
    provider = _FakeHistoryEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        history_list = window.findChild(BoundedVirtualTableView, "historyList")
        history_scroll = window.findChild(QScrollArea, "historyScrollArea")
        detail_title = window.findChild(QLabel, "historyDetailTitle")
        detail_status = window.findChild(QLabel, "historyDetailStatusValue")
        detail_operations = window.findChild(QLabel, "historyDetailOperationsValue")
        detail_transferred = window.findChild(QLabel, "historyDetailTransferredValue")
        detail_speed = window.findChild(QLabel, "historyDetailAverageSpeedValue")
        operation_list = window.findChild(
            BoundedVirtualTableView,
            "historyOperationList",
        )
        operation_title = window.findChild(QLabel, "historyOperationDetailTitle")
        operation_result = window.findChild(
            QLabel,
            "historyOperationDetailResultValue",
        )
        operation_transfer_status = window.findChild(
            QLabel,
            "historyOperationDetailTransferStatusValue",
        )
        operation_verification = window.findChild(
            QLabel,
            "historyOperationDetailVerificationValue",
        )
        operation_durability = window.findChild(
            QLabel,
            "historyOperationDetailDurabilityValue",
        )
        operation_attempts = window.findChild(
            QLabel,
            "historyOperationDetailAttemptsValue",
        )
        operation_last_error = window.findChild(
            QLabel,
            "historyOperationDetailLastErrorValue",
        )
        attempt_list = window.findChild(QListWidget, "historyAttemptList")
        job_filter = window.findChild(QComboBox, "historyJobFilter")
        filter_buttons = {
            button.property("activityFilter"): button
            for button in window.findChildren(QPushButton, "historyFilterButton")
        }

        assert nav is not None
        assert history_list is not None
        assert history_list.bounded_model.max_cached_rows == 25
        assert history_scroll is not None
        assert detail_title is not None
        assert detail_status is not None
        assert detail_operations is not None
        assert detail_transferred is not None
        assert detail_speed is not None
        assert operation_list is not None
        assert operation_list.bounded_model.max_cached_rows == 200
        assert operation_title is not None
        assert operation_result is not None
        assert operation_transfer_status is not None
        assert operation_verification is not None
        assert operation_durability is not None
        assert operation_attempts is not None
        assert operation_last_error is not None
        assert attempt_list is not None
        assert job_filter is not None
        assert set(filter_buttons) == {"ALL", "CONTROLS", "BACKUPS"}

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )
        qapp.processEvents()

        assert _virtual_row_count(history_list) == 2
        assert history_list.horizontalScrollBar().maximum() == 0
        assert operation_list.horizontalScrollBar().maximum() == 0
        assert history_list.isColumnHidden(3)
        assert history_list.isColumnHidden(4)
        assert operation_list.isColumnHidden(3)
        window.resize(1200, 760)
        qapp.processEvents()
        assert not history_list.isColumnHidden(3)
        assert not history_list.isColumnHidden(4)
        assert not operation_list.isColumnHidden(3)
        assert history_list.horizontalScrollBar().maximum() == 0
        assert operation_list.horizontalScrollBar().maximum() == 0
        window.resize(900, 560)
        qapp.processEvents()
        assert detail_title.text() == "Backup · Pictures"
        assert detail_status.text() == "Fullført"
        assert detail_operations.text() == "2 / 2"
        assert detail_transferred.text() == "1.0 KiB / 1.0 KiB"
        assert detail_speed.text() == "11 B/s"
        assert _virtual_row_count(operation_list) == 2
        assert operation_title.text() == "Photos"
        assert operation_result.text() == "Fullført"
        assert operation_transfer_status.text() == "Overført"
        assert operation_verification.text() == "Hash for hovedinnhold verifisert"
        assert operation_durability.text() == "Write-through-forespørsel bekreftet"
        assert operation_attempts.text() == "1"

        _click_virtual_row(operation_list, 1)
        qapp.processEvents()

        assert provider.operation_audit_queries[-1][:2] == ("run-a", "op-b")
        assert operation_title.text().endswith("a-very-long-file-name.jpg")
        assert operation_result.text() == "Fullført"
        assert operation_attempts.text() == "2"
        assert operation_last_error.text() == "LOCAL_IO_TRANSIENT"
        assert attempt_list.count() == 2

        _click_virtual_row(history_list, 1)
        qapp.processEvents()

        assert history_list.selected_row_id() == "CONTROL:analysis-a"
        assert detail_title.text() == "Kontroll · Pictures"
        assert detail_status.text() == "Ingen endringer"
        assert detail_operations.text() == "0 planlagte endringer"
        assert detail_transferred.text() == "Ingen overføring under en kontroll"
        assert detail_speed.text() == "-"
        assert operation_list.isHidden() is True

        window.refresh_engine_status()
        qapp.processEvents()
        assert history_list.selected_row_id() == "CONTROL:analysis-a"

        QTest.mouseClick(
            filter_buttons["CONTROLS"],
            Qt.MouseButton.LeftButton,
        )
        qapp.processEvents()

        assert provider.history_queries[-1][:2] == ("CONTROLS", None)
        assert _virtual_row_count(history_list) == 1
        job_filter.setCurrentIndex(1)
        qapp.processEvents()
        assert provider.history_queries[-1][:2] == ("CONTROLS", "job-a")

        language = window.findChild(QToolButton, "languageSelectorButton")
        assert language is not None
        assert language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert filter_buttons["ALL"].text() == "All activities"
        assert filter_buttons["CONTROLS"].text() == "Controls"
        assert detail_title.text() == "Control · Pictures"
        assert detail_status.text() == "No changes"
        assert (
            window._history_operation_detail_labels["transfer_status"].text()
            == "Transfer status"
        )
        assert (
            window._history_operation_detail_labels["transferred"].text()
            == "Transferred bytes"
        )
        assert history_scroll.horizontalScrollBar().maximum() == 0
        history_page = history_scroll.widget()
        assert history_page is not None
        for label in history_page.findChildren(QLabel):
            if label.property("responsiveText") and not label.isHidden():
                assert label.height() >= label.heightForWidth(label.width())
    finally:
        window.close()
        window.deleteLater()


def test_history_rechecks_then_retries_only_selected_unfinished_file(qapp) -> None:
    provider = _FakeHistoryOperationRetryEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(2)
        qapp.processEvents()
        operation_list = window.findChild(
            BoundedVirtualTableView,
            "historyOperationList",
        )
        retry = window.findChild(QPushButton, "historyRetryOperationButton")
        history_scroll = window.findChild(QScrollArea, "historyScrollArea")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert operation_list is not None and _virtual_row_count(operation_list) == 2
        assert retry is not None and retry.isHidden()
        _click_virtual_row(operation_list, 1)
        qapp.processEvents()

        assert retry.isVisible() and retry.isEnabled()
        assert retry.text() == "Prøv denne filen på nytt"
        assert history_scroll is not None
        assert history_scroll.horizontalScrollBar().maximum() == 0
        assert language is not None and language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert retry.text() == "Retry this file"

        QTest.mouseClick(retry, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.check_start_policies == [False]
        assert retry.isEnabled() is False
        assert retry.text() == "Checking changes..."

        window._poll_backup_analysis()
        qapp.processEvents()

        assert provider.started_plan == ("plan-operation-refreshed", "c" * 64)
        assert provider.started_scope == (("target-a",), "run-a")
        assert provider.started_operation_ids == ("op-b",)
        assert history_scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        window.deleteLater()


def test_history_workspace_pages_with_stable_cursors(qapp) -> None:
    provider = _FakePagedHistoryEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        history_list = window.findChild(BoundedVirtualTableView, "historyList")
        previous = window.findChild(QToolButton, "historyPreviousButton")
        next_button = window.findChild(QToolButton, "historyNextButton")

        assert nav is not None
        assert history_list is not None
        assert previous is not None
        assert next_button is not None
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )
        qapp.processEvents()
        assert next_button.isEnabled() is True

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.history_queries[-1][3] == {
            "cursor_version": 1,
            "started_utc": "2026-07-20T12:00:00.000Z",
            "activity_kind": "BACKUP",
            "activity_id": "run-a",
        }
        assert provider.history_queries[-1][4] is None
        assert _virtual_row_count(history_list) == 1
        assert _virtual_row_id(history_list, 0) == "BACKUP:run-z"
        assert previous.isEnabled() is True
        assert next_button.isEnabled() is False

        QTest.mouseClick(previous, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert provider.history_queries[-1][3] is None
        assert provider.history_queries[-1][4] is None
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize(
    ("paging_mode", "expected_second_request"),
    [
        (
            "keyset",
            (
                "ALL",
                None,
                25,
                {
                    "cursor_version": 1,
                    "started_utc": "2026-07-20T12:00:00.000Z",
                    "activity_kind": "BACKUP",
                    "activity_id": "run-a",
                },
                None,
            ),
        ),
        (
            "legacy",
            ("ALL", None, 25, None, 25),
        ),
    ],
)
def test_history_next_page_uses_keyset_or_legacy_prefetch(
    qapp,
    paging_mode,
    expected_second_request,
) -> None:
    provider = (
        _FakePagedHistoryEngineClient()
        if paging_mode == "keyset"
        else _FakeLegacyPagedHistoryEngineClient()
    )
    worker_provider = (
        _FakePagedHistoryEngineClient()
        if paging_mode == "keyset"
        else _FakeLegacyPagedHistoryEngineClient()
    )
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window._refresh_history_timeline()
        history_list = window.findChild(BoundedVirtualTableView, "historyList")
        next_button = window.findChild(QToolButton, "historyNextButton")
        assert history_list is not None
        assert next_button is not None

        def prefetched() -> bool:
            return (
                len(worker_provider.history_queries) == 2
                and window._history_page_prefetch.count == 1
                and window._page_prefetch_queries is not None
                and not window._page_prefetch_queries.active
                and window._ui_update_coalescer.pending_count == 0
            )

        deadline = monotonic() + 3
        while not prefetched() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert prefetched()
        assert worker_provider.history_queries[1] == expected_second_request
        assert next_button.isEnabled()

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(worker_provider.history_queries) == 2
        assert window._history_page_index == 1
        assert window._history_page_prefetch.count == 0
        assert _virtual_row_id(history_list, 0) == "BACKUP:run-z"
        assert not window._history_query_pending
    finally:
        window.close()
        window.deleteLater()


def test_history_workspace_falls_back_for_legacy_offset_host(qapp) -> None:
    provider = _FakeLegacyPagedHistoryEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(2)
        qapp.processEvents()
        history_list = window.findChild(BoundedVirtualTableView, "historyList")
        previous = window.findChild(QToolButton, "historyPreviousButton")
        next_button = window.findChild(QToolButton, "historyNextButton")

        assert history_list is not None
        assert previous is not None
        assert next_button is not None and next_button.isEnabled()
        assert provider.history_queries[0] == ("ALL", None, 25, None, None)
        assert provider.history_queries[-1] == ("ALL", None, 25, None, 0)

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.history_queries[-1] == ("ALL", None, 25, None, 25)
        assert _virtual_row_id(history_list, 0) == "BACKUP:run-z"
        assert previous.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_stalled_history_prefetch_never_blocks_or_repaints_foreground_filter(
    qapp,
) -> None:
    provider = _FakePagedHistoryEngineClient()
    foreground_provider = _FakePagedHistoryEngineClient()
    prefetch_provider = _BlockingPagedHistoryEngineClient(block_call_no=1)
    factory_calls: list[object] = []

    def worker_factory() -> object:
        client: object = foreground_provider if not factory_calls else prefetch_provider
        factory_calls.append(client)
        return client

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.show()
        window._selected_navigation_index = 2
        assert window._workspace_stack is not None
        window._workspace_stack.setCurrentIndex(2)
        window._refresh_history_timeline()
        backups = window._history_filter_buttons["BACKUPS"]

        deadline = monotonic() + 3
        while not prefetch_provider.started.is_set() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert prefetch_provider.started.is_set()
        assert window._page_prefetch_queries is not None
        assert window._page_prefetch_queries.active
        assert not prefetch_provider.release.is_set()

        QTest.mouseClick(backups, Qt.MouseButton.LeftButton)

        def foreground_settled() -> bool:
            return (
                len(foreground_provider.history_queries) == 2
                and window._background_queries is not None
                and not window._background_queries.active
                and window._ui_update_coalescer.pending_count == 0
                and window._history_timeline_state.activity_filter == "BACKUPS"
            )

        deadline = monotonic() + 3
        while not foreground_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert foreground_settled()
        assert not prefetch_provider.release.is_set()
        assert window._page_prefetch_queries.active
        assert window._page_prefetch_queries.pending_count == 1
        assert window._history_page_prefetch.count == 0
        assert foreground_provider.history_queries == [
            ("ALL", None, 25, None, None),
            ("BACKUPS", None, 25, None, None),
        ]
        assert factory_calls == [foreground_provider, prefetch_provider]

        prefetch_provider.release.set()
        deadline = monotonic() + 3
        while window._page_prefetch_queries.active and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert not window._page_prefetch_queries.active
        assert window._page_prefetch_queries.pending_count == 0
        assert window._history_page_prefetch.count == 1
        assert window._history_timeline_state.activity_filter == "BACKUPS"
        assert prefetch_provider.history_queries[1][0] == "BACKUPS"

        next_button = window.findChild(QToolButton, "historyNextButton")
        history_list = window.findChild(BoundedVirtualTableView, "historyList")
        assert next_button is not None and next_button.isEnabled()
        assert history_list is not None
        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert len(foreground_provider.history_queries) == 2
        assert _virtual_row_id(history_list, 0) == "BACKUP:run-z"
    finally:
        prefetch_provider.release.set()
        window.close()
        window.deleteLater()


def test_history_query_stall_keeps_navigation_and_latest_filter_responsive(
    qapp,
) -> None:
    provider = _FakePagedHistoryEngineClient()
    worker_provider = _BlockingPagedHistoryEngineClient(block_call_no=2)
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingPagedHistoryEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )
    assert window._page_prefetch_queries is not None
    window._page_prefetch_queries.close()
    window._page_prefetch_queries = None

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_history_timeline(background=False)
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        history_list = window.findChild(BoundedVirtualTableView, "historyList")
        history_scroll = window.findChild(QScrollArea, "historyScrollArea")
        previous = window.findChild(QToolButton, "historyPreviousButton")
        next_button = window.findChild(QToolButton, "historyNextButton")
        language = window.findChild(QToolButton, "languageSelectorButton")
        controls = window._history_filter_buttons["CONTROLS"]
        backups = window._history_filter_buttons["BACKUPS"]

        assert nav is not None
        assert history_list is not None
        assert history_scroll is not None
        assert previous is not None
        assert next_button is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )

        assert window._background_queries is not None

        def initial_query_settled() -> bool:
            return (
                len(worker_provider.history_queries) == 1
                and not window._background_queries.active
                and window._ui_update_coalescer.pending_count == 0
            )

        deadline = monotonic() + 3
        while not initial_query_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert initial_query_settled()
        assert next_button.isEnabled()

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()

        assert not worker_provider.release.is_set()
        assert not previous.isEnabled()
        assert not next_button.isEnabled()
        assert not history_list.isEnabled()
        assert controls.isEnabled() and backups.isEnabled()

        QTest.mouseClick(controls, Qt.MouseButton.LeftButton)
        QTest.mouseClick(backups, Qt.MouseButton.LeftButton)
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert backups.text() == "Backup runs"
        assert backups.isChecked()
        assert history_scroll.horizontalScrollBar().maximum() == 0
        assert window._background_queries.pending_count == 1
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        qapp.processEvents()
        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()

        worker_provider.release.set()

        def latest_query_settled() -> bool:
            return (
                len(worker_provider.history_queries) == 3
                and not window._background_queries.active
                and window._ui_update_coalescer.pending_count == 0
                and window._history_timeline_state.activity_filter == "BACKUPS"
                and window._history_timeline_state.offset == 0
            )

        deadline = monotonic() + 3
        while not latest_query_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert latest_query_settled()

        assert worker_provider.max_active == 1
        assert len(factory_calls) == 1
        assert worker_provider.history_queries == [
            ("ALL", None, 25, None, None),
            (
                "ALL",
                None,
                25,
                {
                    "cursor_version": 1,
                    "started_utc": "2026-07-20T12:00:00.000Z",
                    "activity_kind": "BACKUP",
                    "activity_id": "run-a",
                },
                None,
            ),
            ("BACKUPS", None, 25, None, None),
        ]
        assert _virtual_row_count(history_list) == 1
        assert _virtual_row_id(history_list, 0) == "BACKUP:run-a"
        assert history_list.isEnabled()
        assert not previous.isEnabled()
        assert next_button.isEnabled()
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_history_audit_stall_keeps_latest_file_and_navigation_responsive(
    qapp,
) -> None:
    provider = _FakeHistoryEngineClient()
    worker_provider = _BlockingHistoryAuditEngineClient(block_call_no=2)
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingHistoryAuditEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        nav = window.findChild(QListWidget, "navigationRail")
        operation_list = window.findChild(
            BoundedVirtualTableView,
            "historyOperationList",
        )
        retry = window.findChild(QPushButton, "historyRetryOperationButton")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert nav is not None
        assert operation_list is not None
        assert retry is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )
        assert window._background_queries is not None

        def initial_audit_settled() -> bool:
            return (
                not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and _virtual_row_count(operation_list) == 2
                and window._history_operation_audit_state.operation_id == "op-a"
            )

        deadline = monotonic() + 4
        while not initial_audit_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert initial_audit_settled()

        _click_virtual_row(operation_list, 1)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()
        assert worker_provider.attempted_operation_ids[-1] == "op-b"
        assert operation_list.isEnabled()
        assert not retry.isEnabled()

        _click_virtual_row(operation_list, 0)
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()
        worker_provider.release.set()

        def latest_audit_settled() -> bool:
            return (
                not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and window._history_operation_audit_state.operation_id == "op-a"
            )

        deadline = monotonic() + 4
        while not latest_audit_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert latest_audit_settled()

        assert len(factory_calls) == 1
        assert worker_provider.max_active == 1
        assert worker_provider.attempted_operation_ids == ["op-a", "op-b", "op-a"]
        assert operation_list.selected_row_id() == "op-a"
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_history_file_results_page_with_bounded_plan_cursors(qapp) -> None:
    provider = _FakePagedHistoryOperationsEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        operation_list = window.findChild(
            BoundedVirtualTableView,
            "historyOperationList",
        )
        previous = window.findChild(QToolButton, "historyOperationPreviousButton")
        next_button = window.findChild(QToolButton, "historyOperationNextButton")

        assert nav is not None
        assert operation_list is not None
        assert previous is not None
        assert next_button is not None
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )
        qapp.processEvents()

        assert provider.operation_page_queries[-1] == ("plan-a", 200, None)
        assert _virtual_row_count(operation_list) == 1
        assert _virtual_row_id(operation_list, 0) == "op-a"
        assert previous.isEnabled() is False
        assert next_button.isEnabled() is True

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.operation_page_queries[-1][2] == {
            "execution_phase": 20,
            "stable_order_key": "photos/a.jpg",
            "operation_id": "op-a",
        }
        assert _virtual_row_id(operation_list, 0) == "op-z"
        assert previous.isEnabled() is True
        assert next_button.isEnabled() is False

        QTest.mouseClick(previous, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.operation_page_queries[-1] == ("plan-a", 200, None)
        assert _virtual_row_id(operation_list, 0) == "op-a"
    finally:
        window.close()
        window.deleteLater()


def test_history_file_results_next_page_uses_one_page_prefetch(qapp) -> None:
    provider = _FakePagedHistoryOperationsEngineClient()
    worker_provider = _FakePagedHistoryOperationsEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window._selected_navigation_index = 2
        assert window._workspace_stack is not None
        window._workspace_stack.setCurrentIndex(2)
        window._history_operation_activity_key = "run-a:plan-a"
        window._history_operation_run_id = "run-a"
        window._history_operation_plan_id = "plan-a"
        window._refresh_history_operation_page()
        operation_list = window.findChild(
            BoundedVirtualTableView,
            "historyOperationList",
        )
        next_button = window.findChild(QToolButton, "historyOperationNextButton")
        assert operation_list is not None
        assert next_button is not None

        def prefetched() -> bool:
            return (
                len(worker_provider.operation_page_queries) == 2
                and window._history_operation_page_prefetch.count == 1
                and window._page_prefetch_queries is not None
                and not window._page_prefetch_queries.active
                and window._ui_update_coalescer.pending_count == 0
            )

        deadline = monotonic() + 3
        while not prefetched() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert prefetched()
        assert worker_provider.operation_page_queries[1][2] == {
            "execution_phase": 20,
            "stable_order_key": "photos/a.jpg",
            "operation_id": "op-a",
        }
        assert next_button.isEnabled()

        QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(worker_provider.operation_page_queries) == 2
        assert window._history_operation_page_index == 1
        assert window._history_operation_page_prefetch.count == 0
        assert _virtual_row_id(operation_list, 0) == "op-z"
        assert not window._history_operation_query_pending
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


def test_current_job_plan_remains_visible_without_any_run(qapp) -> None:
    provider = _FakePlanOnlyDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.refresh_engine_status()

        plan_summary = window.findChild(QLabel, "planPreviewSummary")
        job_plan = window.findChild(QLabel, "jobDetailPlanValue")
        assert plan_summary is not None
        assert plan_summary.text() == "2 operasjoner fra plan-a."
        assert job_plan is not None
        assert job_plan.text().startswith("2 operasjoner fra plan-a.")
        assert provider.calls.count("get_plan_operations") == 1
        assert provider.calls.count("get_plan_endpoints") == 1
    finally:
        window.close()
        window.deleteLater()


def test_start_backup_button_submits_sealed_plan_once(qapp) -> None:
    provider = _FakeBackupStartDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        button = window.findChild(QPushButton, "startBackupButton")
        assert button is not None
        assert button.isVisible()
        assert button.isEnabled()

        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.started_plan == ("plan-a", "a" * 64)
        assert button.isEnabled() is False
        assert button.text() == "Backup er lagt i kø"
    finally:
        window.close()
        window.deleteLater()


def test_pending_target_registration_stays_responsive_and_bounded(qapp) -> None:
    provider = _FakePendingRegistrationDashboardEngineClient()
    worker_provider = _BlockingTargetRegistrationEngineClient(provider)
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._select_navigation_row(1)
        qapp.processEvents()
        register = window.findChild(QPushButton, "jobsStartBackupButton")
        target_rows = window.findChildren(QLabel, "jobsDetailTargetRow")
        language = window.findChild(QToolButton, "languageSelectorButton")
        nav = window.findChild(QListWidget, "navigationRail")

        assert register is not None and register.isVisible() and register.isEnabled()
        assert register.text() == "Registrer mål"
        assert target_rows and target_rows[0].isVisible()
        assert target_rows[0].toolTip() == target_rows[0].text()
        assert "…" in str(target_rows[0].property("displayText"))
        assert (
            target_rows[0]
            .fontMetrics()
            .horizontalAdvance(str(target_rows[0].property("displayText")))
            <= target_rows[0].contentsRect().width()
        )
        assert language is not None and language.menu() is not None
        assert nav is not None

        QTest.mouseClick(register, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()
        assert register.isEnabled() is False
        assert register.text() == "Registrerer mål..."
        QTest.mouseClick(register, Qt.MouseButton.LeftButton)

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert nav.currentRow() == 3
        assert register.text() == "Registering targets..."
        assert worker_provider.attempted_calls == 1
        assert worker_provider.worker_thread_id != get_ident()
        assert worker_provider.registration_attempts[0][:2] == (
            "job-a",
            "job-rev-a",
        )
        worker_provider.release.set()
        assert window._command_submissions is not None
        deadline = monotonic() + 3
        while (
            window._job_detail_state.job_revision_id != "job-rev-b"
            and monotonic() < deadline
        ):
            qapp.processEvents()
            QTest.qWait(10)

        assert window._job_detail_state.job_revision_id == "job-rev-b"
        assert window._job_detail_state.writable_target_registration_required is False
        assert provider.registered is True
        assert worker_provider.attempted_calls == 1
        assert register.text() == "Start backup"
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_foreign_target_takeover_confirmation_is_localized_and_bounded(qapp) -> None:
    provider = _FakeForeignTakeoverDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        takeover = window.findChild(QPushButton, "jobsStartBackupButton")
        target_rows = window.findChildren(QLabel, "jobsDetailTargetRow")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert takeover is not None and takeover.isVisible() and takeover.isEnabled()
        assert takeover.text() == "Start kontrollert overtakelse"
        assert target_rows and target_rows[0].isVisible()
        assert target_rows[0].toolTip() == target_rows[0].text()
        assert "…" in str(target_rows[0].property("displayText"))
        assert language is not None and language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert takeover.text() == "Start controlled takeover"

        dialog_checks: list[bool] = []
        dialog_errors: list[BaseException] = []

        def confirm_dialog() -> None:
            dialog = window.findChild(QDialog, "controlledTakeoverDialog")
            try:
                assert dialog is not None and dialog.isVisible()
                details = dialog.findChild(QLabel, "controlledTakeoverDetails")
                checkbox = dialog.findChild(QCheckBox, "controlledTakeoverConfirmation")
                confirm = dialog.findChild(QPushButton, "controlledTakeoverConfirm")
                assert details is not None and details.wordWrap()
                assert "Current owner: 22222222...2222" in details.text()
                assert "Latest ownership epoch: 7" in details.text()
                assert "does not start automatically" in details.text()
                assert details.height() >= details.heightForWidth(details.width())
                assert checkbox is not None
                assert checkbox.text() == "I confirm the new owner."
                assert checkbox.sizeHint().width() <= checkbox.width()
                assert confirm is not None and confirm.isEnabled() is False
                QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton)
                assert confirm.isEnabled() is True
                dialog_checks.append(True)
                QTest.mouseClick(confirm, Qt.MouseButton.LeftButton)
            except BaseException as exc:
                dialog_errors.append(exc)
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(0, confirm_dialog)
        QTest.mouseClick(takeover, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert dialog_errors == []
        assert dialog_checks == [True]
        assert provider.takeover_args == (
            "job-a",
            "job-rev-a",
            1,
            "target-a",
            "22222222-2222-4222-8222-222222222222",
            7,
        )
        assert provider.start_calls == 0
        assert window._job_detail_state.job_revision_id == "job-rev-b"
        assert window._job_detail_state.controlled_takeover_required is False
        assert window._job_detail_state.analysis_request_state == "QUEUED"
    finally:
        window.close()
        window.deleteLater()


def test_stalled_create_command_keeps_shell_responsive_and_submits_once(qapp) -> None:
    provider = _FakeBackupCreationEngineClient()
    worker_provider = _BlockingBackupCreationEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingBackupCreationEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=_ready_state(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        choices = ["C:/Users/Ada/Pictures", "E:/MediaSyncBackup"]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        nav = window.findChild(QListWidget, "navigationRail")
        heading = window.findChild(QLabel, "workspaceHeading")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert create is not None
        assert add_target is not None
        assert nav is not None
        assert heading is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(create, Qt.MouseButton.LeftButton)
        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        for _ in range(3):
            QTest.mouseClick(create, Qt.MouseButton.LeftButton)
            qapp.processEvents()
        assert worker_provider.started.wait(timeout=1)
        assert not create.isEnabled()
        QTest.mouseClick(create, Qt.MouseButton.LeftButton)

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert nav.currentRow() == 3
        assert heading.text() == "Settings"
        assert worker_provider.attempted_calls == 1
        assert worker_provider.worker_thread_id != get_ident()
        assert not worker_provider.release.is_set()
        worker_provider.release.set()
        assert window._command_submissions is not None
        deadline = monotonic() + 3
        while window._command_submissions.active and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)

        assert not window._command_submissions.active
        assert worker_provider.calls.count("create_standard_backup_job") == 1
        assert provider.calls == []
        assert factory_calls == [True]
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_stalled_check_command_keeps_navigation_language_and_duplicate_guard(
    qapp,
) -> None:
    provider = _FakeTargetRetryDashboardEngineClient()
    worker_provider = _BlockingCheckCommandEngineClient()

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._select_navigation_row(1)
        qapp.processEvents()
        retry = window.findChild(QPushButton, "jobsRetryTargetButton")
        nav = window.findChild(QListWidget, "navigationRail")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert retry is not None and retry.isVisible() and retry.isEnabled()
        assert nav is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(retry, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()
        assert not retry.isEnabled()
        QTest.mouseClick(retry, Qt.MouseButton.LeftButton)

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert nav.currentRow() == 3
        assert worker_provider.attempted_calls == 1
        assert worker_provider.worker_thread_id != get_ident()
        worker_provider.release.set()
        deadline = monotonic() + 3
        while window._analysis_request_id is None and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)

        window._analysis_timer.stop()
        assert window._analysis_request_id == "analysis-retry"
        assert worker_provider.check_start_policies == [False]
        assert provider.check_start_policies == []
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_stalled_start_result_does_not_replace_newly_selected_job(qapp) -> None:
    provider = _FakeMultiJobDashboardEngineClient()
    worker_provider = _BlockingStartMultiJobEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._select_navigation_row(1)
        qapp.processEvents()
        jobs = window.findChild(QListWidget, "jobsList")
        start = window.findChild(QPushButton, "jobsStartBackupButton")
        nav = window.findChild(QListWidget, "navigationRail")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert jobs is not None and jobs.count() == 2
        assert start is not None and start.isVisible() and start.isEnabled()
        assert nav is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(start, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()
        assert not start.isEnabled()
        QTest.mouseClick(start, Qt.MouseButton.LeftButton)
        jobs.scrollToItem(jobs.item(1))
        qapp.processEvents()
        next_job = jobs.item(1)
        next_job_rect = jobs.visualItemRect(next_job)
        assert jobs.viewport().rect().contains(next_job_rect.center())
        QTest.mouseClick(
            jobs.viewport(),
            Qt.MouseButton.LeftButton,
            pos=next_job_rect.center(),
        )
        deadline = monotonic() + 3
        while window._job_detail_state.job_id != "job-b" and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert window._job_detail_state.job_id == "job-b"

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert nav.currentRow() == 3
        worker_provider.release.set()
        assert window._command_submissions is not None
        deadline = monotonic() + 3
        while window._command_submissions.active and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)

        assert worker_provider.attempted_calls == 1
        assert worker_provider.worker_thread_id != get_ident()
        assert worker_provider.started_plan == ("plan-a", "a" * 64)
        assert window._selected_job_id == "job-b"
        assert window._job_detail_state.job_id == "job-b"
        assert window._active_run_id != "run-a"
        assert "job-a" in window._queued_backup_job_ids
        assert "job-b" not in window._queued_backup_job_ids
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_start_transport_retry_reuses_exact_command_identity(qapp) -> None:
    provider = _FakeBackupStartDashboardEngineClient()
    worker_provider = _FailOnceStartCommandEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.show()
        window._refresh_engine_status_now()
        qapp.processEvents()
        start = window.findChild(QPushButton, "startBackupButton")
        assert start is not None and start.isVisible() and start.isEnabled()
        assert window._command_submissions is not None

        QTest.mouseClick(start, Qt.MouseButton.LeftButton)
        deadline = monotonic() + 3
        while window._command_submissions.active and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        qapp.processEvents()

        assert len(worker_provider.attempts) == 1
        assert start.isEnabled()
        first_identity = worker_provider.attempts[0][2:4]
        QTest.mouseClick(start, Qt.MouseButton.LeftButton)
        deadline = monotonic() + 3
        while "job-a" not in window._queued_backup_job_ids and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)

        assert len(worker_provider.attempts) == 2
        assert worker_provider.attempts[1][2:4] == first_identity
        assert worker_provider.started_plan == ("plan-a", "a" * 64)
        assert provider.started_plan is None
        assert "job-a" in window._queued_backup_job_ids
    finally:
        window.close()
        window.deleteLater()


def test_stalled_run_control_keeps_shell_responsive_and_submits_once(qapp) -> None:
    provider = _FakeRunControlDashboardEngineClient()
    worker_provider = _BlockingRunControlCommandEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=lambda: worker_provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._select_navigation_row(1)
        qapp.processEvents()
        pause = window.findChild(QPushButton, "jobsPauseBackupButton")
        stop = window.findChild(QPushButton, "jobsStopBackupButton")
        nav = window.findChild(QListWidget, "navigationRail")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert pause is not None and pause.isVisible() and pause.isEnabled()
        assert stop is not None and stop.isVisible() and stop.isEnabled()
        assert nav is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(pause, Qt.MouseButton.LeftButton)
        assert worker_provider.started.wait(timeout=1)
        qapp.processEvents()
        assert not pause.isEnabled()
        assert not stop.isEnabled()
        QTest.mouseClick(pause, Qt.MouseButton.LeftButton)

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()
        worker_provider.release.set()
        assert window._command_submissions is not None
        deadline = monotonic() + 3
        while window._command_submissions.active and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)

        assert worker_provider.controls == ["pause"]
        assert worker_provider.attempted_calls == 1
        assert worker_provider.worker_thread_id != get_ident()
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_jobs_page_shows_live_progress_and_run_controls(qapp) -> None:
    provider = _FakeRunControlDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        progress = window.findChild(QProgressBar, "jobsRunProgressBar")
        detail = window.findChild(QLabel, "jobsRunProgressDetail")
        active_file = window.findChild(QLabel, "jobsRunActiveFile")
        pause = window.findChild(QPushButton, "jobsPauseBackupButton")
        resume = window.findChild(QPushButton, "jobsResumeBackupButton")
        stop = window.findChild(QPushButton, "jobsStopBackupButton")
        start = window.findChild(QPushButton, "jobsStartBackupButton")

        assert progress is not None
        assert progress.isVisible()
        assert progress.maximum() == 1000
        assert progress.value() == 500
        assert detail is not None
        assert detail.text().startswith("1 / 3 operasjoner")
        assert "0.0 MB/s" in detail.text()
        assert "< 1 min igjen" in detail.text()
        assert active_file is not None
        assert active_file.isVisible()
        assert "Photos/2026/current.jpg" in active_file.text()
        assert "Kopierer" in active_file.text()
        assert pause is not None
        assert pause.isVisible()
        assert pause.isEnabled()
        assert resume is not None
        assert resume.isHidden()
        assert stop is not None
        assert stop.isVisible()
        assert stop.isEnabled()
        assert start is not None
        assert start.isHidden()

        QTest.mouseClick(pause, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.controls == ["pause"]
        assert pause.isHidden()
        assert resume.isVisible()
        assert resume.isEnabled()

        QTest.mouseClick(resume, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.controls == ["pause", "resume"]
        assert pause.isVisible()
        assert resume.isHidden()

        QTest.mouseClick(stop, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.controls == ["pause", "resume", "stop"]
        assert pause.isHidden()
        assert resume.isHidden()
        assert stop.isVisible()
        assert stop.isEnabled() is False
        assert stop.text() == "Stopper etter aktiv fil"
    finally:
        window.close()
        window.deleteLater()


def test_background_progress_poll_does_not_queue_or_block_navigation(qapp) -> None:
    provider = _FakeRunControlDashboardEngineClient()
    worker_provider = _BlockingRunProgressDashboardEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingRunProgressDashboardEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert nav is not None
        assert language is not None and language.menu() is not None
        window._poll_active_run_progress()
        assert worker_provider.started.wait(timeout=1)
        window._poll_active_run_progress()
        window._poll_active_run_progress()
        qapp.processEvents()

        assert worker_provider.attempted_calls == 1
        assert window._run_progress_query_pending
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()
        worker_provider.release.set()

        assert window._background_queries is not None

        def progress_settled() -> bool:
            return (
                not window._run_progress_query_pending
                and not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and window._run_progress_state.run_id == "run-a"
            )

        deadline = monotonic() + 3
        while not progress_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert progress_settled()

        assert len(factory_calls) == 1
        assert worker_provider.max_active == 1
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_background_analysis_poll_does_not_queue_or_block_navigation(qapp) -> None:
    provider = _FakeTargetRetryDashboardEngineClient()
    worker_provider = _BlockingAnalysisPollEngineClient()
    factory_calls: list[bool] = []

    def worker_factory() -> _BlockingAnalysisPollEngineClient:
        factory_calls.append(True)
        return worker_provider

    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        engine_client_factory=worker_factory,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window._refresh_engine_status_now()
        window._check_selected_backup(start_when_safe=False)
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert nav is not None
        assert language is not None and language.menu() is not None
        assert window._command_submissions is not None
        deadline = monotonic() + 2
        while window._analysis_request_id is None and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert window._analysis_request_id == "analysis-retry"
        window._poll_backup_analysis()
        assert worker_provider.started.wait(timeout=1)
        window._poll_backup_analysis()
        window._poll_backup_analysis()
        qapp.processEvents()

        assert worker_provider.attempted_calls == 1
        assert window._analysis_query_pending
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(3)).center(),
        )
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert nav.currentRow() == 3
        assert not worker_provider.release.is_set()
        worker_provider.release.set()

        assert window._background_queries is not None

        def analysis_settled() -> bool:
            return (
                window._analysis_request_id is None
                and not window._analysis_query_pending
                and not window._background_queries.active
                and window._background_queries.pending_count == 0
                and window._ui_update_coalescer.pending_count == 0
                and window._job_detail_state.plan_id == "plan-refreshed"
            )

        deadline = monotonic() + 4
        while not analysis_settled() and monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert analysis_settled()

        assert len(factory_calls) == 2
        assert worker_provider.max_active == 1
        assert nav.currentRow() == 3
    finally:
        worker_provider.release.set()
        window.close()
        window.deleteLater()


def test_jobs_page_restores_terminal_result_after_gui_restart(qapp) -> None:
    provider = _FakeTerminalRunDashboardEngineClient()

    for _ in range(2):
        window = build_main_window(
            initial_state=EngineStatusViewState.disconnected(),
            engine_client=provider,
            theme_mode=ThemeMode.LIGHT,
        )
        try:
            window.show()
            window.refresh_engine_status()
            window._select_navigation_row(1)
            qapp.processEvents()

            title = window._jobs_run_progress_title
            result_state = window.findChild(QLabel, "jobsRunProgressState")
            detail = window.findChild(QLabel, "jobsRunProgressDetail")
            progress = window.findChild(QProgressBar, "jobsRunProgressBar")
            active_file = window.findChild(QLabel, "jobsRunActiveFile")
            pause = window.findChild(QPushButton, "jobsPauseBackupButton")
            resume = window.findChild(QPushButton, "jobsResumeBackupButton")
            stop = window.findChild(QPushButton, "jobsStopBackupButton")

            assert title is not None
            assert title.isVisible()
            assert title.text() == "Backupresultat"
            assert result_state is not None
            assert result_state.text() == "Fullført"
            assert detail is not None
            assert "3 / 3 operasjoner" in detail.text()
            assert "3.0 KiB / 3.0 KiB overført" in detail.text()
            assert "1 av 1 mål fullført" in detail.text()
            assert "Backupen er fullført og verifisert." in detail.text()
            assert "0 varsler / 0 feil" in detail.text()
            assert "Beregner gjenstående tid" not in detail.text()
            assert progress is not None
            assert progress.value() == progress.maximum()
            assert active_file is not None and active_file.isHidden()
            assert pause is not None and pause.isHidden()
            assert resume is not None and resume.isHidden()
            assert stop is not None and stop.isHidden()
            assert window._run_progress_timer.isActive() is False
        finally:
            window.close()
            window.deleteLater()
            qapp.processEvents()

    assert provider.progress_after_sequences == [None, None]


def test_jobs_page_localizes_terminal_partial_failure_summary(qapp) -> None:
    provider = _FakeTerminalRunDashboardEngineClient(
        run_state="PARTIAL_FAILURE",
        completed_operations=2,
        completed_bytes=2048,
        warning_count=1,
        error_count=1,
    )
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
        user_preferences=UserPreferences(language=UserLanguage.ENGLISH),
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()

        title = window._jobs_run_progress_title
        result_state = window.findChild(QLabel, "jobsRunProgressState")
        detail = window.findChild(QLabel, "jobsRunProgressDetail")
        jobs_scroll = window.findChild(QScrollArea, "jobsScrollArea")

        assert title is not None and title.text() == "Backup result"
        assert result_state is not None
        assert result_state.text() == "Partially completed"
        assert detail is not None
        assert "2 / 3 operations" in detail.text()
        assert "2 of 3 targets completed" in detail.text()
        assert (
            "Some files were not backed up. Review History and run again."
            in detail.text()
        )
        assert "1 warnings / 1 errors" in detail.text()
        assert "Calculating remaining time" not in detail.text()
        assert jobs_scroll is not None
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
        assert detail.height() >= detail.heightForWidth(detail.width())
    finally:
        window.close()
        window.deleteLater()


def test_jobs_page_rechecks_then_retries_only_selected_failed_target(qapp) -> None:
    provider = _FakeTargetRetryDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        combo = window.findChild(QComboBox, "jobsRetryTargetCombo")
        button = window.findChild(QPushButton, "jobsRetryTargetButton")
        jobs_scroll = window.findChild(QScrollArea, "jobsScrollArea")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert combo is not None and combo.isVisible()
        assert combo.count() == 1
        assert combo.currentData(Qt.ItemDataRole.UserRole) == "target-c"
        assert "Feilet" in combo.currentText()
        assert button is not None and button.isVisible() and button.isEnabled()
        assert button.text() == "Prøv målet på nytt"
        assert jobs_scroll is not None
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
        assert language is not None and language.menu() is not None

        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert button.text() == "Retry target"
        assert "Failed" in combo.currentText()
        assert jobs_scroll.horizontalScrollBar().maximum() == 0

        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert provider.check_start_policies == [False]
        assert button.isEnabled() is False
        assert button.text() == "Checking changes..."

        window._poll_backup_analysis()
        qapp.processEvents()

        assert provider.started_plan == ("plan-refreshed", "b" * 64)
        assert provider.started_scope == (("target-c",), "run-a")
        assert jobs_scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        window.deleteLater()


def test_jobs_page_counts_warning_target_as_completed(qapp) -> None:
    provider = _FakeTerminalRunDashboardEngineClient(
        run_state="COMPLETED_WITH_WARNINGS",
        warning_count=1,
    )
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        detail = window.findChild(QLabel, "jobsRunProgressDetail")

        assert detail is not None
        assert "1 av 1 mål fullført" in detail.text()
        assert "Backupen er fullført med varsler." in detail.text()
        assert "1 varsler / 0 feil" in detail.text()
    finally:
        window.close()
        window.deleteLater()


def test_jobs_page_omits_target_count_for_legacy_terminal_snapshot(qapp) -> None:
    provider = _FakeTerminalRunDashboardEngineClient(target_states=())
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.LIGHT,
    )

    try:
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        detail = window.findChild(QLabel, "jobsRunProgressDetail")

        assert detail is not None
        assert "mål fullført" not in detail.text()
        assert "target completed" not in detail.text()
        assert "targets completed" not in detail.text()
    finally:
        window.close()
        window.deleteLater()


def test_activity_bar_wraps_exact_freshness_for_three_targets(qapp) -> None:
    provider = _FakeMultiTargetFreshnessDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        activity_scroll = window.findChild(QScrollArea, "activityScrollArea")
        rows = window.findChildren(QLabel, "activityDimensionLabel")
        freshness = rows[2]
        actions = rows[3]
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert activity_scroll is not None
        assert language is not None
        assert activity_scroll.horizontalScrollBar().maximum() == 0
        assert freshness.text().count("\n") == 2
        assert "target-a: Sist sikkerhetskopiert" in freshness.text()
        assert "target-b: Sist sikkerhetskopiert" in freshness.text()
        assert "target-c: Ukjent · Ingen vellykket backup" in freshness.text()
        assert "19.07.2026" in freshness.text()
        assert "18.07.2026" in freshness.text()
        assert freshness.height() >= freshness.heightForWidth(freshness.width())
        assert "target-a: Kontrollerer måltilgang." in actions.text()
        assert "target-b: Kontroller målet og prøv igjen." in actions.text()
        assert "target-c: Se gjennom målfeilen." in actions.text()
        assert actions.height() >= actions.heightForWidth(actions.width())

        assert language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert activity_scroll.horizontalScrollBar().maximum() == 0
        assert freshness.text().count("\n") == 2
        assert "target-a: Last backed up" in freshness.text()
        assert "target-b: Last backed up" in freshness.text()
        assert "target-c: Unknown · No successful backup" in freshness.text()
        assert "2026-07-19" in freshness.text()
        assert "2026-07-18" in freshness.text()
        assert freshness.height() >= freshness.heightForWidth(freshness.width())
        assert "target-a: Checking target access." in actions.text()
        assert "target-b: Check the target and retry." in actions.text()
        assert "target-c: Review the target error." in actions.text()
        assert actions.height() >= actions.heightForWidth(actions.width())
    finally:
        window.close()
        window.deleteLater()


def test_activity_bar_wraps_exact_filter_decisions_without_clipping(qapp) -> None:
    provider = _FakeFilterDecisionDashboardEngineClient()
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(900, 560)
        window.show()
        window.refresh_engine_status()
        qapp.processEvents()
        activity_scroll = window.findChild(QScrollArea, "activityScrollArea")
        title = window.findChild(QLabel, "filterDecisionTitle")
        summary = window.findChild(QLabel, "filterDecisionSummary")
        rows = window.findChildren(QLabel, "filterDecisionRow")
        row = next(candidate for candidate in rows if candidate.isVisible())
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert activity_scroll is not None
        assert title is not None
        assert summary is not None
        assert language is not None
        assert activity_scroll.horizontalScrollBar().maximum() == 0
        assert title.text() == "Filvalg"
        assert summary.text() == (
            "Nøyaktige, samsvarende filvalg for dette snapshotet."
        )
        assert row.wordWrap()
        assert row.minimumWidth() == 0
        assert row.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Ignored
        assert "Ekskludert:" in row.text()
        assert "default-long-cache-rule" in row.text()
        assert row.width() <= activity_scroll.viewport().width()
        assert row.height() >= row.heightForWidth(row.width())

        assert language.menu() is not None
        language.menu().actions()[1].trigger()
        qapp.processEvents()

        assert activity_scroll.horizontalScrollBar().maximum() == 0
        assert title.text() == "File selection"
        assert summary.text() == (
            "Exact matched file-selection decisions for this snapshot."
        )
        assert "Excluded:" in row.text()
        assert "Matched exclusion rule" in row.text()
        assert row.width() <= activity_scroll.viewport().width()
        assert row.height() >= row.heightForWidth(row.width())
    finally:
        window.close()
        window.deleteLater()


def test_jobs_page_wraps_endpoint_retry_diagnostics_without_clipping(qapp) -> None:
    provider = _FakeRunControlDashboardEngineClient()
    provider.target_waiting = True
    provider.endpoint_wait_reason = "NETWORK_INTERRUPTED"
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(700, 500)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        rows = window.findChildren(QLabel, "jobsRunTargetRow")
        row = next(candidate for candidate in rows if candidate.isVisible())

        assert row.wordWrap()
        assert "Venter på mål" in row.text()
        assert "Forsøk 2" in row.text()
        assert "nytt forsøk etter" in row.text()
        assert "Nettverksforbindelsen ble avbrutt" in row.toolTip()
        assert "NETWORK_INTERRUPTED" in row.toolTip()
        assert "14.2 s" in row.toolTip()
        assert row.height() >= row.fontMetrics().height()
    finally:
        window.close()
        window.deleteLater()


def test_jobs_page_wraps_file_retry_diagnostics_without_clipping(qapp) -> None:
    provider = _FakeRunControlDashboardEngineClient()
    provider.staging_retry_waiting = True
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )

    try:
        window.resize(700, 500)
        window.show()
        window.refresh_engine_status()
        window._select_navigation_row(1)
        qapp.processEvents()
        active_file = window.findChild(QLabel, "jobsRunActiveFile")

        assert active_file is not None
        assert active_file.wordWrap()
        assert "Nytt forsøk 2" in active_file.text()
        assert "LOCAL_STAGING_TRANSFER_FAILED" in active_file.toolTip()
        assert "0.9 s" in active_file.toolTip()
        assert active_file.height() >= active_file.heightForWidth(active_file.width())
    finally:
        window.close()
        window.deleteLater()


def _open_job_editor(qapp, provider: _FakeJobEditingDashboardEngineClient):
    window = build_main_window(
        initial_state=EngineStatusViewState.disconnected(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )
    window.resize(900, 560)
    window.show()
    window.refresh_engine_status()
    qapp.processEvents()
    navigation = window.findChild(QListWidget, "navigationRail")
    edit_button = window.findChild(QPushButton, "jobsEditButton")
    assert navigation is not None
    assert edit_button is not None
    navigation.setCurrentRow(1)
    qapp.processEvents()
    assert edit_button.isVisible()
    assert edit_button.isEnabled()
    QTest.mouseClick(edit_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert navigation.currentRow() == 0
    assert window._is_setup_editing()
    return window


def test_job_editor_saves_name_only_change_without_full_check(qapp) -> None:
    provider = _FakeJobEditingDashboardEngineClient()
    window = _open_job_editor(qapp, provider)

    try:
        name_input = window.findChild(QLineEdit, "setupJobNameInput")
        primary = window.findChild(QPushButton, "createBackupButton")
        save_without_check = window.findChild(
            QPushButton,
            "saveJobWithoutCheckButton",
        )
        assert name_input is not None
        assert primary is not None
        assert save_without_check is not None
        assert name_input.text() == "Pictures"
        assert window._setup_draft.source_path_label == "C:/Users/Ada/Pictures"
        assert tuple(target.path_label for target in window._setup_draft.targets) == (
            "E:/Backup",
        )

        name_input.selectAll()
        QTest.keyClicks(name_input, "Pictures renamed")
        for expected_step in ("targets", "defaults", "review"):
            QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
            qapp.processEvents()
            assert window._setup_state.current_step.value == expected_step

        assert primary.text() == "Lagre endringer"
        assert primary.isEnabled()
        assert save_without_check.isHidden()
        QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(provider.edit_calls) == 1
        call = provider.edit_calls[0]
        draft = call["draft"]
        assert isinstance(draft, StandardBackupJobDraft)
        assert draft.source_name == "Pictures renamed"
        assert call["check_after_save"] is False
        assert call["expected_job_revision_id"] == "job-rev-a"
        assert call["expected_lifecycle_row_version"] == 1
        assert not window._is_setup_editing()
        navigation = window.findChild(QListWidget, "navigationRail")
        assert navigation is not None and navigation.currentRow() == 1
    finally:
        window._setup_edit_original_draft = None
        window.close()
        window.deleteLater()


def test_job_editor_target_change_stays_visible_and_can_save_without_check(
    qapp,
) -> None:
    provider = _FakeJobEditingDashboardEngineClient()
    window = _open_job_editor(qapp, provider)

    try:
        primary = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        save_without_check = window.findChild(
            QPushButton,
            "saveJobWithoutCheckButton",
        )
        controls = window.findChild(QWidget, "setupTargetControls")
        scroll = window.findChild(QScrollArea, "dashboardScrollArea")
        assert primary is not None
        assert add_target is not None
        assert save_without_check is not None
        assert controls is not None
        assert scroll is not None

        QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        long_target = "F:/MediaSync/Second/" + "deep-folder/" * 10 + "backup"
        window._choose_directory = lambda _title: long_target
        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        window._ensure_setup_action_visible()
        qapp.processEvents()

        target_rows = window.findChildren(QWidget, "setupTargetPathRow")
        visible_rows = [row for row in target_rows if row.isVisible()]
        assert len(visible_rows) == 2
        assert visible_rows[1].property("fullText").endswith(long_target)
        for row in window.findChildren(
            QWidget, "setupTargetRow1"
        ) + window.findChildren(
            QWidget,
            "setupTargetRow2",
        ):
            if row.isVisible():
                assert row.geometry().right() <= controls.contentsRect().right()
        primary_bottom = primary.mapTo(
            scroll.viewport(),
            primary.rect().bottomLeft(),
        ).y()
        assert 0 <= primary_bottom < scroll.viewport().height()
        assert primary.isVisible() and primary.isEnabled()

        QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
        QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert window._setup_state.current_step.value == "review"
        assert primary.text() == "Lagre og kontroller endringer"
        assert save_without_check.isVisible()
        assert save_without_check.isEnabled()
        QTest.mouseClick(save_without_check, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(provider.edit_calls) == 1
        call = provider.edit_calls[0]
        draft = call["draft"]
        assert isinstance(draft, StandardBackupJobDraft)
        assert len(draft.targets) == 2
        assert draft.targets[1].path_label == long_target
        assert call["check_after_save"] is False
        assert not window._is_setup_editing()
    finally:
        window._setup_edit_original_draft = None
        window.close()
        window.deleteLater()


def test_job_editor_active_run_lock_and_unsaved_navigation_guard(qapp) -> None:
    provider = _FakeJobEditingDashboardEngineClient()
    window = _open_job_editor(qapp, provider)

    try:
        name_input = window.findChild(QLineEdit, "setupJobNameInput")
        change_source = window.findChild(QToolButton, "changeSetupSourceButton")
        primary = window.findChild(QPushButton, "createBackupButton")
        navigation = window.findChild(QListWidget, "navigationRail")
        add_target = window.findChild(QToolButton, "addTargetButton")
        assert name_input is not None
        assert change_source is not None
        assert primary is not None
        assert navigation is not None
        assert add_target is not None
        active = replace(
            window._run_progress_state,
            run_id="run-editing",
            job_id="job-a",
            state="EXECUTING",
            run_found=True,
            terminal=False,
        )
        window._run_progress_state = active
        window._apply_run_progress_state(active)
        qapp.processEvents()

        assert name_input.isEnabled()
        assert not change_source.isEnabled()
        name_input.selectAll()
        QTest.keyClicks(name_input, "Rename while running")
        QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert not add_target.isEnabled()
        assert all(
            not button.isEnabled()
            for button in window.findChildren(QToolButton, "removeTargetButton")
            if button.isVisible()
        )

        window._confirm_unsaved_job_edit = lambda: "continue"
        navigation.setCurrentRow(1)
        qapp.processEvents()
        assert navigation.currentRow() == 0
        assert window._is_setup_editing()

        window._confirm_unsaved_job_edit = lambda: "discard"
        navigation.setCurrentRow(1)
        qapp.processEvents()
        assert navigation.currentRow() == 1
        assert not window._is_setup_editing()
        assert provider.edit_calls == []
    finally:
        window._setup_edit_original_draft = None
        window.close()
        window.deleteLater()


def _ready_state() -> EngineStatusViewState:
    return engine_status_from_response(
        IpcResponse.accepted(
            {"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()}
        )
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


class _MemoryUserPreferencesStore:
    def __init__(self) -> None:
        self.saved: list[UserPreferences] = []

    def load(self) -> UserPreferences:
        return UserPreferences()

    def save(self, preferences: UserPreferences) -> None:
        self.saved.append(preferences)


class _FakeBackupCreationEngineClient(_FakeEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.draft: StandardBackupJobDraft | None = None

    def create_standard_backup_job(
        self,
        *,
        draft: StandardBackupJobDraft,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        assert request_id
        assert idempotency_key
        self.calls.append("create_standard_backup_job")
        self.draft = draft
        return IpcResponse.accepted(
            {
                "created": True,
                "job": {
                    "job_id": "job-a",
                    "job_revision_id": "job-rev-a",
                    "filter_set_id": "filter-a",
                },
                "writable_endpoint_registration": {
                    "completed": True,
                    "state": "COMMITTED",
                    "registered_target_count": 1,
                },
            }
        )


class _FakeFailedRegistrationEngineClient(_FakeBackupCreationEngineClient):
    def create_standard_backup_job(
        self,
        *,
        draft: StandardBackupJobDraft,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        response = super().create_standard_backup_job(
            draft=draft,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        payload = dict(response.payload)
        payload["writable_endpoint_registration"] = {
            "completed": False,
            "state": "PREPARED",
            "registered_target_count": 0,
            "validation_codes": ["WRITABLE_ENDPOINT_PROBE_FAILED"],
        }
        return IpcResponse.accepted(payload)


class _BlockingBackupCreationEngineClient(_FakeBackupCreationEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.attempted_calls = 0
        self.worker_thread_id: int | None = None

    def create_standard_backup_job(
        self,
        *,
        draft: StandardBackupJobDraft,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.attempted_calls += 1
        self.worker_thread_id = get_ident()
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test create command release timed out")
        return super().create_standard_backup_job(
            draft=draft,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )


class _FakeDashboardEngineClient(_FakeEngineClient):
    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del draft_id, lifecycle_state, limit, offset
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
                                "registration_state": "WRITABLE_READY",
                                "registration_reason_code": (
                                    "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
                                ),
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
                                "registration_state": "WRITABLE_READY",
                                "registration_reason_code": (
                                    "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
                                ),
                            }
                        ],
                        "initial_plan": {
                            "state": "SEALED",
                            "reason_code": "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
                            "analysis_id": "analysis-a",
                            "plan_id": "plan-a",
                            "plan_checksum": "a" * 64,
                            "operation_count": 2,
                            "planned_bytes": 2048,
                            "plan_runnable": False,
                            "next_action": "Review the plan.",
                        },
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
                                    "last_success_utc": "2026-07-19T12:05:00.000Z",
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
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
    ) -> IpcResponse:
        del after
        self.calls.append("get_plan_operations")
        operations = [
            {
                "operation_id": "op-a",
                "operation_type": "CREATE_DIRECTORY",
                "sequence_no": 0,
                "execution_phase": 10,
                "stable_order_key": "photos",
                "target_precondition_kind": "ABSENT",
                "reason_code": "TARGET_DIRECTORY_MISSING",
                "risk_level": "LOW",
                "target_endpoint_id": "target-a",
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
                "target_endpoint_id": "target-a",
                "target_relative_path": "Photos/2026/a.jpg",
                "planned_bytes": 2048,
            },
        ]
        if target_endpoint_id is not None:
            operations = [
                operation
                for operation in operations
                if operation["target_endpoint_id"] == target_endpoint_id
            ]
        if risk_levels:
            operations = [
                operation
                for operation in operations
                if operation["risk_level"] in risk_levels
            ]
        return IpcResponse.accepted(
            {
                "plan_operations": {
                    "plan_id": plan_id,
                    "limit": limit or 25,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "risk_counts": {
                        "LOW": 2,
                        "MEDIUM": 0,
                        "HIGH": 0,
                        "BLOCKED": 0,
                    },
                    "highest_risk": "LOW",
                    "target_endpoint_ids": ["target-a"],
                    "operations": operations,
                }
            }
        )

    def get_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        del limit, after
        self.calls.append("get_plan_endpoints")
        return IpcResponse.accepted(
            {
                "plan_endpoints": {
                    "plan_id": plan_id,
                    "limit": 4,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "endpoints": [
                        {
                            "endpoint_id": "source-a",
                            "endpoint_revision_id": "source-rev-a",
                            "snapshot_id": "source-snapshot-a",
                            "role": "SOURCE",
                            "target_ordinal": None,
                            "capabilities_hash": "capabilities-source",
                            "root_case_context_hash": "case-source",
                            "required_owner_installation_id": None,
                            "required_ownership_epoch": None,
                            "control_schema_version": None,
                            "planned_operations": 0,
                            "planned_bytes": 0,
                        },
                        {
                            "endpoint_id": "target-a",
                            "endpoint_revision_id": "target-rev-a",
                            "snapshot_id": "target-snapshot-a",
                            "role": "TARGET_WRITABLE",
                            "target_ordinal": 0,
                            "capabilities_hash": "capabilities-target",
                            "root_case_context_hash": "case-target",
                            "required_owner_installation_id": "owner-a",
                            "required_ownership_epoch": 1,
                            "control_schema_version": 1,
                            "planned_operations": 2,
                            "planned_bytes": 2048,
                        },
                    ],
                }
            }
        )

    def get_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        del limit, after
        self.calls.append("get_snapshot_issues")
        assert snapshot_id == "source-snapshot-a"
        assert blocking_only is True
        return IpcResponse.accepted(
            {
                "snapshot_issues": {
                    "snapshot_id": snapshot_id,
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": True,
                    "blocking_only": True,
                    "next_cursor": None,
                    "issues": [
                        {
                            "issue_id": 1,
                            "relative_path": "Archive",
                            "issue_type": "UNREADABLE_DIRECTORY",
                            "blocks_destructive_actions": True,
                            "error_code": "ERROR_ACCESS_DENIED",
                            "sanitized_message": "access denied",
                        }
                    ],
                }
            }
        )

    def get_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        del limit, after
        self.calls.append("get_snapshot_coverage")
        assert snapshot_id == "source-snapshot-a"
        assert "COMPLETE" not in coverage_states
        return IpcResponse.accepted(
            {
                "snapshot_coverage": {
                    "snapshot_id": snapshot_id,
                    "limit": 2,
                    "has_more": False,
                    "read_model_available": True,
                    "coverage_states": list(coverage_states),
                    "next_cursor": None,
                    "coverage": [
                        {
                            "relative_path": "Videos",
                            "comparison_key": "videos",
                            "coverage_state": "VOLATILE",
                            "case_mode": "CASE_INSENSITIVE",
                            "case_mode_evidence": "probe-ok",
                            "case_context_hash": "a" * 64,
                            "case_probe_error": None,
                        }
                    ],
                }
            }
        )

    def get_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del run_id, target_endpoint_id, offset
        self.calls.append("get_cataloged_files")
        assert limit == 3
        return IpcResponse.accepted(
            {
                "cataloged_files": {
                    "limit": 3,
                    "offset": 0,
                    "has_more": True,
                    "read_model_available": True,
                    "run_id": None,
                    "target_endpoint_id": None,
                    "files": [
                        {
                            "handoff_id": "final-file:run-a:operation-a",
                            "run_id": "run-a",
                            "run_target_id": "run-a-target-0000",
                            "operation_id": "operation-a",
                            "target_endpoint_id": "target-a",
                            "target_endpoint_revision_id": "target-rev-a",
                            "final_relative_path": "Photos/2026/a.jpg",
                            "content_hash": "abcdef0123456789" * 4,
                            "lease_id": "lease-a",
                            "fencing_token": 1,
                            "effect_kind": "COPY_NEW_FINAL_FILE",
                            "recorded_utc": "2026-07-20T12:00:00.000Z",
                        }
                    ],
                }
            }
        )


class _FakeFilterDecisionDashboardEngineClient(_FakeDashboardEngineClient):
    def get_snapshot_filter_decisions(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        decision_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        del after, decision_states
        self.calls.append("get_snapshot_filter_decisions")
        assert snapshot_id == "source-snapshot-a"
        assert limit == 5
        long_segment = "ApplicationCacheFolderWithoutAnyNaturalBreakPoint" * 3
        return IpcResponse.accepted(
            {
                "snapshot_filter_decisions": {
                    "snapshot_id": snapshot_id,
                    "limit": 5,
                    "has_more": False,
                    "read_model_available": True,
                    "decision_states": [],
                    "next_cursor": None,
                    "decisions": [
                        {
                            "decision_id": 1,
                            "relative_path": f"AppData/{long_segment}/cache.tmp",
                            "decision_state": "EXCLUDED",
                            "reason_code": "FILTER_RULE_EXCLUDED",
                            "matched_rule_id": "default-long-cache-rule",
                            "evaluation_stage": "PRE_METADATA",
                        }
                    ],
                }
            }
        )


class _FakeJobEditingDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.edit_calls: list[dict[str, object]] = []

    def update_standard_backup_job(
        self,
        *,
        job_id: str,
        expected_job_revision_id: str,
        expected_lifecycle_row_version: int,
        draft: StandardBackupJobDraft,
        check_after_save: bool,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.calls.append("update_standard_backup_job")
        self.edit_calls.append(
            {
                "job_id": job_id,
                "expected_job_revision_id": expected_job_revision_id,
                "expected_lifecycle_row_version": expected_lifecycle_row_version,
                "draft": draft,
                "check_after_save": check_after_save,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
            }
        )
        requires_full_check = (
            draft.source_path_label != "C:/Users/Ada/Pictures"
            or tuple(target.path_label for target in draft.targets) != ("E:/Backup",)
        )
        changed_fields = []
        if draft.source_name != "Pictures":
            changed_fields.append("name")
        if draft.source_path_label != "C:/Users/Ada/Pictures":
            changed_fields.append("source")
        if tuple(target.path_label for target in draft.targets) != ("E:/Backup",):
            changed_fields.append("targets")
        return IpcResponse.accepted(
            {
                "job_edit": {
                    "saved": True,
                    "requires_full_check": requires_full_check,
                    "check_queued": requires_full_check and check_after_save,
                    "changed_fields": changed_fields,
                    "validation_code": (
                        "STANDARD_BACKUP_JOB_UPDATED_NEEDS_CHECK"
                        if requires_full_check and not check_after_save
                        else "STANDARD_BACKUP_JOB_UPDATED"
                    ),
                },
                "job": {
                    "job_id": job_id,
                    "job_revision_id": "job-rev-b",
                    "filter_set_id": "filter-a",
                },
            }
        )


class _BlockingStatusDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def get_status(self) -> IpcResponse:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test query release timed out")
        return super().get_status()


class _FakeJobLifecycleDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.lifecycle_filter = "ACTIVE"
        self.lifecycle_queries: list[str] = []
        self.lifecycle_commands: list[str] = []

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        self.lifecycle_filter = lifecycle_state or "ACTIVE"
        self.lifecycle_queries.append(self.lifecycle_filter)
        response = super().get_backup_overview(
            draft_id=draft_id,
            lifecycle_state=lifecycle_state,
            limit=limit,
            offset=offset,
        )
        payload = dict(response.payload)
        overview = dict(payload["backup_overview"])
        overview["lifecycle_state"] = self.lifecycle_filter
        jobs = [dict(job) for job in overview["jobs"]]
        for job in jobs:
            job["lifecycle_state"] = self.lifecycle_filter
            job["lifecycle_row_version"] = (
                2 if self.lifecycle_filter == "ARCHIVED" else 1
            )
        overview["jobs"] = jobs
        payload["backup_overview"] = overview
        return IpcResponse.accepted(payload)

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        response = super().get_backup_job_detail(job_id=job_id)
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        job = dict(detail["job"])
        job["lifecycle_state"] = self.lifecycle_filter
        job["lifecycle_row_version"] = 2 if self.lifecycle_filter == "ARCHIVED" else 1
        job["archived_utc"] = (
            "2026-08-01T10:00:00Z" if self.lifecycle_filter == "ARCHIVED" else None
        )
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)

    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del job_id, limit, offset
        return IpcResponse.accepted(
            {
                "activity_overview": {
                    "read_model_available": True,
                    "has_more": False,
                    "runs": [],
                }
            }
        )

    def archive_standard_backup_job(self, **_kwargs: object) -> IpcResponse:
        self.lifecycle_commands.append("ARCHIVE")
        return IpcResponse.accepted({"applied": True})

    def reactivate_standard_backup_job(self, **_kwargs: object) -> IpcResponse:
        self.lifecycle_commands.append("REACTIVATE")
        return IpcResponse.accepted({"applied": True})


class _FakeChangesDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.changes_queries: list[
            tuple[
                str,
                int,
                dict[str, object] | None,
                str | None,
                tuple[str, ...],
            ]
        ] = []

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
    ) -> IpcResponse:
        normalized_limit = limit or 25
        self.calls.append("get_plan_operations")
        self.changes_queries.append(
            (
                plan_id,
                normalized_limit,
                after,
                target_endpoint_id,
                risk_levels,
            )
        )
        operations = [
            {
                "operation_id": "op-safe",
                "operation_type": "COPY_NEW",
                "sequence_no": 10,
                "execution_phase": 10,
                "stable_order_key": "photos/new.jpg",
                "target_precondition_kind": "ABSENT",
                "reason_code": "SOURCE_ONLY",
                "risk_level": "LOW",
                "target_endpoint_id": "target-a",
                "target_relative_path": "Photos/new.jpg",
                "planned_bytes": 1024,
            },
            {
                "operation_id": "op-review",
                "operation_type": "COPY_NEW",
                "sequence_no": 20,
                "execution_phase": 20,
                "stable_order_key": "photos/review.jpg",
                "target_precondition_kind": "MATCH_FINGERPRINT",
                "reason_code": "TARGET_CONTENT_DIFFERS",
                "risk_level": "MEDIUM",
                "target_endpoint_id": "target-b",
                "target_relative_path": "Photos/review.jpg",
                "planned_bytes": 2048,
            },
            {
                "operation_id": "op-high",
                "operation_type": "DEFER_AUTOMATION_POLICY",
                "sequence_no": 30,
                "execution_phase": 30,
                "stable_order_key": "photos/high.jpg",
                "target_precondition_kind": "NONE",
                "reason_code": "AUTOMATION_REVIEW_REQUIRED",
                "risk_level": "HIGH",
                "target_endpoint_id": "target-a",
                "target_relative_path": "Photos/high.jpg",
                "planned_bytes": 4096,
            },
            {
                "operation_id": "op-blocked",
                "operation_type": "BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN",
                "sequence_no": 40,
                "execution_phase": 40,
                "stable_order_key": "photos/blocked.jpg",
                "target_precondition_kind": "NONE",
                "reason_code": "ENDPOINT_CAPABILITIES_UNKNOWN",
                "risk_level": "BLOCKED",
                "target_endpoint_id": "target-b",
                "target_relative_path": "Photos/blocked.jpg",
                "planned_bytes": 0,
            },
        ]
        if target_endpoint_id is not None:
            operations = [
                operation
                for operation in operations
                if operation["target_endpoint_id"] == target_endpoint_id
            ]
        if risk_levels:
            operations = [
                operation
                for operation in operations
                if operation["risk_level"] in risk_levels
            ]
        start = 0
        if after is not None:
            after_operation_id = after.get("operation_id")
            for index, operation in enumerate(operations):
                if operation["operation_id"] == after_operation_id:
                    start = index + 1
                    break
        page_operations = operations[start : start + 2]
        has_more = start + len(page_operations) < len(operations)
        next_cursor = None
        if has_more and page_operations:
            last = page_operations[-1]
            next_cursor = {
                "execution_phase": last["execution_phase"],
                "stable_order_key": last["stable_order_key"],
                "operation_id": last["operation_id"],
            }
        return IpcResponse.accepted(
            {
                "plan_operations": {
                    "plan_id": plan_id,
                    "limit": normalized_limit,
                    "has_more": has_more,
                    "read_model_available": True,
                    "next_cursor": next_cursor,
                    "risk_counts": {
                        "LOW": 1,
                        "MEDIUM": 1,
                        "HIGH": 1,
                        "BLOCKED": 1,
                    },
                    "highest_risk": "BLOCKED",
                    "target_endpoint_ids": ["target-a", "target-b"],
                    "operations": page_operations,
                }
            }
        )


class _BlockingChangesDashboardEngineClient(_FakeChangesDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self._started_calls = 0
        self._active_calls = 0
        self.max_active = 0

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
    ) -> IpcResponse:
        with self._worker_lock:
            self._started_calls += 1
            call_no = self._started_calls
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
        self.started.set()
        try:
            if call_no == 1 and not self.release.wait(timeout=5):
                raise TimeoutError("test query release timed out")
            return super().get_plan_operations(
                plan_id=plan_id,
                limit=limit,
                after=after,
                target_endpoint_id=target_endpoint_id,
                risk_levels=risk_levels,
            )
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakeBackupStartDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started_plan: tuple[str, str] | None = None
        self.started_scope: tuple[tuple[str, ...], str | None] | None = None
        self.started_operation_ids: tuple[str, ...] = ()

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        response = super().get_backup_job_detail(job_id=job_id)
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        job = dict(detail["job"])
        initial_plan = dict(job["initial_plan"])
        initial_plan["plan_runnable"] = True
        job["initial_plan"] = initial_plan
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)

    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
        target_endpoint_ids: tuple[str, ...] = (),
        resumed_from_run_id: str | None = None,
        source_operation_ids: tuple[str, ...] = (),
    ) -> IpcResponse:
        assert request_id
        assert idempotency_key
        self.calls.append("start_backup")
        self.started_plan = (plan_id, plan_checksum)
        self.started_scope = (target_endpoint_ids, resumed_from_run_id)
        self.started_operation_ids = source_operation_ids
        return IpcResponse.accepted({"created": True, "run": {"run_id": "run-a"}})


class _FakePendingRegistrationDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.registered = False

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        response = super().get_backup_job_detail(job_id=job_id)
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        job = dict(detail["job"])
        job["job_revision_id"] = "job-rev-b" if self.registered else "job-rev-a"
        targets = list(job["targets"])
        target = dict(targets[0])
        target.update(
            {
                "path_label": (
                    "E:/MediaSync Home Backups/Primary External Drive/"
                    "CompleteComputerBackupTargetFolderWithoutBreaks"
                ),
                "registration_state": (
                    "WRITABLE_READY" if self.registered else "REGISTRATION_PENDING"
                ),
                "registration_reason_code": (
                    "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
                    if self.registered
                    else "WRITABLE_ENDPOINT_REGISTRATION_REQUIRED"
                ),
            }
        )
        targets[0] = target
        job["targets"] = targets
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)

    def register_writable_targets(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        assert job_id == "job-a"
        assert job_revision_id == "job-rev-a"
        assert request_id
        assert idempotency_key
        self.registered = True
        return IpcResponse.accepted(
            {
                "job": {
                    "job_id": job_id,
                    "job_revision_id": "job-rev-b",
                },
                "writable_endpoint_registration": {
                    "completed": True,
                    "state": "COMMITTED",
                    "registered_target_count": 1,
                },
            }
        )


class _FakeForeignTakeoverDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.taken_over = False
        self.takeover_args: tuple[str, str, int, str, str, int] | None = None
        self.start_calls = 0

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        response = super().get_backup_job_detail(job_id=job_id)
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        job = dict(detail["job"])
        job["job_revision_id"] = "job-rev-b" if self.taken_over else "job-rev-a"
        targets = list(job["targets"])
        target = dict(targets[0])
        target.update(
            {
                "path_label": (
                    "E:/MediaSync Home Backups/Foreign Installation/"
                    "CompleteComputerBackupTargetFolderWithoutBreaks"
                ),
                "target_ordinal": 1,
                "endpoint_id": "target-a",
                "registration_state": (
                    "WRITABLE_READY" if self.taken_over else "READ_ONLY_READY"
                ),
                "registration_reason_code": (
                    "ENDPOINT_TARGET_WRITABLE_PROBE_VERIFIED"
                    if self.taken_over
                    else "ENDPOINT_TARGET_FOREIGN_READ_ONLY"
                ),
                "foreign_owner_installation_id": (
                    None if self.taken_over else "22222222-2222-4222-8222-222222222222"
                ),
                "foreign_ownership_epoch": None if self.taken_over else 7,
                "foreign_recovery_status": (
                    None if self.taken_over else "CHECK_REQUIRED_UNDER_LOCK"
                ),
            }
        )
        targets[0] = target
        job["targets"] = targets
        if self.taken_over:
            job["initial_plan"] = None
            job["latest_analysis_request"] = {
                "request_id": "analysis-request-a",
                "state": "QUEUED",
                "requested_utc": "2026-08-01T12:00:00Z",
                "reason_code": None,
                "analysis_id": None,
                "plan_id": None,
                "start_when_safe": False,
                "started_run_id": None,
                "row_version": 1,
            }
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)

    def start_controlled_endpoint_takeover(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        target_ordinal: int,
        endpoint_id: str,
        expected_foreign_owner_installation_id: str,
        expected_ownership_epoch: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        assert request_id
        assert idempotency_key
        self.takeover_args = (
            job_id,
            job_revision_id,
            target_ordinal,
            endpoint_id,
            expected_foreign_owner_installation_id,
            expected_ownership_epoch,
        )
        self.taken_over = True
        return IpcResponse.accepted(
            {
                "job": {"job_id": job_id, "job_revision_id": "job-rev-b"},
                "endpoint_takeover": {
                    "completed": True,
                    "state": "COMMITTED",
                    "analysis_request_id": "analysis-request-a",
                    "full_analysis_queued": True,
                    "start_when_safe": False,
                },
            }
        )

    def start_backup(self, **_kwargs: object) -> IpcResponse:
        self.start_calls += 1
        return IpcResponse.accepted({"created": True})


class _BlockingTargetRegistrationEngineClient(
    _FakePendingRegistrationDashboardEngineClient
):
    def __init__(
        self,
        state_owner: _FakePendingRegistrationDashboardEngineClient,
    ) -> None:
        super().__init__()
        self._state_owner = state_owner
        self.started = Event()
        self.release = Event()
        self.attempted_calls = 0
        self.worker_thread_id: int | None = None
        self.registration_attempts: list[tuple[str, str, str, str]] = []

    def register_writable_targets(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.attempted_calls += 1
        self.worker_thread_id = get_ident()
        self.registration_attempts.append(
            (job_id, job_revision_id, request_id, idempotency_key)
        )
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test target registration release timed out")
        self.registered = True
        self._state_owner.registered = True
        return IpcResponse.accepted(
            {
                "job": {
                    "job_id": job_id,
                    "job_revision_id": "job-rev-b",
                },
                "writable_endpoint_registration": {
                    "completed": True,
                    "state": "COMMITTED",
                    "registered_target_count": 1,
                },
            }
        )


class _FailOnceStartCommandEngineClient(_FakeBackupStartDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[tuple[str, str, str, str]] = []

    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
        target_endpoint_ids: tuple[str, ...] = (),
        resumed_from_run_id: str | None = None,
        source_operation_ids: tuple[str, ...] = (),
    ) -> IpcResponse:
        self.attempts.append((plan_id, plan_checksum, request_id, idempotency_key))
        if len(self.attempts) == 1:
            raise TimeoutError("simulated uncertain command transport outcome")
        return super().start_backup(
            plan_id=plan_id,
            plan_checksum=plan_checksum,
            request_id=request_id,
            idempotency_key=idempotency_key,
            target_endpoint_ids=target_endpoint_ids,
            resumed_from_run_id=resumed_from_run_id,
            source_operation_ids=source_operation_ids,
        )


class _FakeRunControlDashboardEngineClient(_FakeBackupStartDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.run_state = "EXECUTING"
        self.sequence_no = 1
        self.controls: list[str] = []
        self.stop_requested = False
        self.target_waiting = False
        self.staging_retry_waiting = False
        self.endpoint_wait_reason = "ENDPOINT_ROOT_UNAVAILABLE"

    def get_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        self.calls.append("get_run_progress")
        assert run_id == "run-a"
        snapshot = {
            "run_id": "run-a",
            "job_id": "job-a",
            "state": self.run_state,
            "terminal": False,
            "sequence_no": self.sequence_no,
            "planned_operations": 3,
            "completed_operations": 1,
            "planned_bytes": 3072,
            "completed_bytes": 1024,
            "transferred_operations": 1,
            "transferred_bytes": 1536,
            "warning_count": 0,
            "error_count": 0,
            "active_relative_path": "Photos/2026/current.jpg",
            "active_phase": "STAGING_ALLOCATED",
            "active_planned_bytes": 2048,
            "active_staging_failure_count": (1 if self.staging_retry_waiting else 0),
            "active_retry_backoff_ms": (900 if self.staging_retry_waiting else None),
            "active_retry_not_before_utc": (
                "2026-07-31T00:00:00.900Z" if self.staging_retry_waiting else None
            ),
            "active_last_error_code": (
                "LOCAL_STAGING_TRANSFER_FAILED" if self.staging_retry_waiting else None
            ),
            "bytes_per_second": 512.0,
            "eta_seconds": 3,
            "stop_requested": self.stop_requested,
            "targets": [
                {
                    "endpoint_id": "target-a",
                    "state": (
                        "WAITING_FOR_ENDPOINT"
                        if self.target_waiting
                        else "PAUSED"
                        if self.run_state == "PAUSED"
                        else "EXECUTING"
                    ),
                    "planned_operations": 3,
                    "completed_operations": 1,
                    "planned_bytes": 3072,
                    "completed_bytes": 1024,
                    "warning_count": 0,
                    "error_count": 0,
                    "endpoint_wait_attempts": 2 if self.target_waiting else 0,
                    "endpoint_wait_total_backoff_ms": (
                        14_250 if self.target_waiting else 0
                    ),
                    "endpoint_retry_backoff_ms": (
                        9_500 if self.target_waiting else None
                    ),
                    "endpoint_retry_not_before_utc": (
                        "2026-07-31T00:00:09.500Z" if self.target_waiting else None
                    ),
                    "endpoint_wait_reason_code": (
                        self.endpoint_wait_reason if self.target_waiting else None
                    ),
                    "endpoint_wait_started_utc": (
                        "2026-07-31T00:00:00.000Z" if self.target_waiting else None
                    ),
                }
            ],
        }
        return IpcResponse.accepted(
            {
                "run_progress": {
                    "run_id": run_id,
                    "read_model_available": True,
                    "run_found": True,
                    "changed": after_sequence_no != self.sequence_no,
                    "snapshot": (
                        None if after_sequence_no == self.sequence_no else snapshot
                    ),
                }
            }
        )

    def pause_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        assert run_id == "run-a"
        assert request_id
        assert idempotency_key
        self.controls.append("pause")
        self.run_state = "PAUSED"
        self.sequence_no += 1
        return IpcResponse.accepted(
            {"applied": True, "run": {"run_id": run_id, "state": "PAUSING"}}
        )

    def resume_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        assert run_id == "run-a"
        assert request_id
        assert idempotency_key
        self.controls.append("resume")
        self.run_state = "EXECUTING"
        self.sequence_no += 1
        return IpcResponse.accepted(
            {"applied": True, "run": {"run_id": run_id, "state": "QUEUED"}}
        )

    def stop_backup_after_active_file(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        assert run_id == "run-a"
        assert request_id
        assert idempotency_key
        self.controls.append("stop")
        self.stop_requested = True
        self.sequence_no += 1
        return IpcResponse.accepted(
            {"applied": True, "run": {"run_id": run_id, "state": "EXECUTING"}}
        )


class _BlockingRunControlCommandEngineClient(_FakeRunControlDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.attempted_calls = 0
        self.worker_thread_id: int | None = None

    def pause_backup(
        self,
        *,
        run_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.attempted_calls += 1
        self.worker_thread_id = get_ident()
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test run-control command release timed out")
        return super().pause_backup(
            run_id=run_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )


class _BlockingRunProgressDashboardEngineClient(_FakeRunControlDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self.attempted_calls = 0
        self._active_calls = 0
        self.max_active = 0

    def get_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        with self._worker_lock:
            self.attempted_calls += 1
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
        self.started.set()
        try:
            if not self.release.wait(timeout=5):
                raise TimeoutError("test query release timed out")
            return super().get_run_progress(
                run_id=run_id,
                after_sequence_no=after_sequence_no,
            )
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakeTerminalRunDashboardEngineClient(_FakeBackupStartDashboardEngineClient):
    def __init__(
        self,
        *,
        run_state: str = "COMPLETED",
        completed_operations: int = 3,
        completed_bytes: int = 3072,
        warning_count: int = 0,
        error_count: int = 0,
        target_states: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self.run_state = run_state
        self.completed_operations = completed_operations
        self.completed_bytes = completed_bytes
        self.warning_count = warning_count
        self.error_count = error_count
        self.target_states = (
            target_states
            if target_states is not None
            else {
                "COMPLETED": ("SUCCEEDED",),
                "COMPLETED_WITH_WARNINGS": ("SUCCEEDED_WITH_WARNINGS",),
                "PARTIAL_FAILURE": (
                    "SUCCEEDED",
                    "SUCCEEDED_WITH_WARNINGS",
                    "FAILED",
                ),
                "FAILED": ("FAILED",),
                "CANCELLED": ("CANCELLED",),
                "BLOCKED_BY_SAFETY": ("BLOCKED",),
                "RECOVERY_REQUIRED": ("RECOVERY_REQUIRED",),
            }.get(run_state, ("FAILED",))
        )
        self.progress_after_sequences: list[int | None] = []

    def _target_payloads(self) -> list[dict[str, object]]:
        target_count = len(self.target_states)
        payloads: list[dict[str, object]] = []
        for index, target_state in enumerate(self.target_states):
            succeeded = target_state in {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"}
            planned_operations = 3 if target_count == 1 else 1
            planned_bytes = 3072 if target_count == 1 else 1024
            completed_operations = (
                self.completed_operations
                if target_count == 1
                else planned_operations
                if succeeded
                else 0
            )
            completed_bytes = (
                self.completed_bytes
                if target_count == 1
                else planned_bytes
                if succeeded
                else 0
            )
            payloads.append(
                {
                    "run_target_id": f"run-a-target-{index:04d}",
                    "endpoint_id": f"target-{chr(ord('a') + index)}",
                    "endpoint_revision_id": f"target-rev-{index}",
                    "state": target_state,
                    "planned_operations": planned_operations,
                    "completed_operations": completed_operations,
                    "planned_bytes": planned_bytes,
                    "completed_bytes": completed_bytes,
                    "warning_count": (
                        1 if target_state == "SUCCEEDED_WITH_WARNINGS" else 0
                    ),
                    "error_count": (
                        1
                        if target_state in {"FAILED", "BLOCKED", "RECOVERY_REQUIRED"}
                        else 0
                    ),
                    "last_success_utc": (
                        "2026-07-20T12:01:00.000Z"
                        if succeeded
                        else "2026-07-19T12:05:00.000Z"
                    ),
                }
            )
        return payloads

    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del limit, offset
        self.calls.append("get_activity_overview")
        assert job_id == "job-a"
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
                            "state": self.run_state,
                            "trigger_type": "MANUAL_LOCAL_PREVIEW",
                            "started_utc": "2026-07-20T12:00:00.000Z",
                            "finished_utc": "2026-07-20T12:01:00.000Z",
                            "planned_operations": 3,
                            "planned_bytes": 3072,
                            "warning_count": self.warning_count,
                            "error_count": self.error_count,
                            "targets": self._target_payloads(),
                        }
                    ],
                }
            }
        )

    def get_run_progress(
        self,
        *,
        run_id: str,
        after_sequence_no: int | None = None,
    ) -> IpcResponse:
        self.calls.append("get_run_progress")
        assert run_id == "run-a"
        self.progress_after_sequences.append(after_sequence_no)
        return IpcResponse.accepted(
            {
                "run_progress": {
                    "run_id": run_id,
                    "read_model_available": True,
                    "run_found": True,
                    "changed": after_sequence_no != 12,
                    "snapshot": (
                        None
                        if after_sequence_no == 12
                        else {
                            "run_id": run_id,
                            "job_id": "job-a",
                            "state": self.run_state,
                            "terminal": True,
                            "sequence_no": 12,
                            "planned_operations": 3,
                            "completed_operations": self.completed_operations,
                            "planned_bytes": 3072,
                            "completed_bytes": self.completed_bytes,
                            "transferred_operations": self.completed_operations,
                            "transferred_bytes": self.completed_bytes,
                            "warning_count": self.warning_count,
                            "error_count": self.error_count,
                            "active_relative_path": None,
                            "active_phase": None,
                            "active_planned_bytes": None,
                            "bytes_per_second": None,
                            "eta_seconds": None,
                            "stop_requested": False,
                            "targets": self._target_payloads(),
                        }
                    ),
                }
            }
        )


class _FakeTargetRetryDashboardEngineClient(_FakeTerminalRunDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__(
            run_state="PARTIAL_FAILURE",
            completed_operations=2,
            completed_bytes=2048,
            warning_count=1,
            error_count=1,
        )
        self.analysis_requested = False
        self.check_start_policies: list[bool] = []

    def check_backup(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
        start_when_safe: bool = True,
    ) -> IpcResponse:
        assert job_id == "job-a"
        assert request_id
        assert idempotency_key
        self.analysis_requested = True
        self.check_start_policies.append(start_when_safe)
        return IpcResponse.accepted(
            {
                "analysis_request": {
                    "request_id": "analysis-retry",
                    "state": "QUEUED",
                }
            }
        )

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        response = super().get_backup_job_detail(job_id=job_id)
        if not self.analysis_requested:
            return response
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        job = dict(detail["job"])
        initial_plan = dict(job["initial_plan"])
        initial_plan.update(
            {
                "analysis_id": "analysis-refreshed",
                "plan_id": "plan-refreshed",
                "plan_checksum": "b" * 64,
                "operation_count": 1,
                "planned_bytes": 1024,
                "plan_runnable": True,
            }
        )
        job["initial_plan"] = initial_plan
        job["latest_analysis_request"] = {
            "request_id": "analysis-retry",
            "job_id": "job-a",
            "job_revision_id": "job-rev-a",
            "state": "SUCCEEDED",
            "requested_utc": "2026-07-31T12:00:00.000Z",
            "started_utc": "2026-07-31T12:00:01.000Z",
            "completed_utc": "2026-07-31T12:00:02.000Z",
            "analysis_id": "analysis-refreshed",
            "plan_id": "plan-refreshed",
            "reason_code": "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
            "operation_count": 1,
            "planned_bytes": 1024,
            "start_when_safe": False,
            "started_run_id": None,
            "row_version": 2,
        }
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)


class _BlockingCheckCommandEngineClient(_FakeTargetRetryDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.attempted_calls = 0
        self.worker_thread_id: int | None = None

    def check_backup(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
        start_when_safe: bool = True,
    ) -> IpcResponse:
        self.attempted_calls += 1
        self.worker_thread_id = get_ident()
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test check command release timed out")
        return super().check_backup(
            job_id=job_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            start_when_safe=start_when_safe,
        )


class _BlockingAnalysisPollEngineClient(_FakeTargetRetryDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.analysis_requested = True
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self.attempted_calls = 0
        self._active_calls = 0
        self.max_active = 0

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        with self._worker_lock:
            self.attempted_calls += 1
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
        self.started.set()
        try:
            if not self.release.wait(timeout=5):
                raise TimeoutError("test query release timed out")
            return super().get_backup_job_detail(job_id=job_id)
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakeMultiTargetFreshnessDashboardEngineClient(_FakeDashboardEngineClient):
    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        response = super().get_activity_overview(
            job_id=job_id,
            limit=limit,
            offset=offset,
        )
        payload = dict(response.payload)
        overview = dict(payload["activity_overview"])
        runs = list(overview["runs"])
        run = dict(runs[0])
        targets = list(run["targets"])
        first = dict(targets[0])
        targets.extend(
            (
                {
                    **first,
                    "run_target_id": "run-a-target-0001",
                    "endpoint_id": "target-b",
                    "endpoint_revision_id": "target-rev-b",
                    "state": "WAITING_FOR_ENDPOINT",
                    "last_success_utc": "2026-07-18T11:05:00.000Z",
                },
                {
                    **first,
                    "run_target_id": "run-a-target-0002",
                    "endpoint_id": "target-c",
                    "endpoint_revision_id": "target-rev-c",
                    "state": "FAILED",
                    "last_success_utc": None,
                },
            )
        )
        run["targets"] = targets
        runs[0] = run
        overview["runs"] = runs
        payload["activity_overview"] = overview
        return IpcResponse.accepted(payload)


class _FakeMultiJobDashboardEngineClient(_FakeBackupStartDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.requested_job_ids: list[str] = []

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        response = super().get_backup_overview(
            draft_id=draft_id,
            lifecycle_state=lifecycle_state,
            limit=limit,
            offset=offset,
        )
        payload = dict(response.payload)
        overview = dict(payload["backup_overview"])
        overview["limit"] = 25
        overview["offset"] = 0
        overview["jobs"] = [
            *overview["jobs"],
            {
                "job_id": "job-b",
                "job_revision_id": "job-rev-b",
                "filter_set_id": "filter-b",
                "title": "Documents",
                "source_name": "Documents",
                "source_path_label": "C:/Users/Ada/Documents",
                "configured_target_count": 1,
                "independent_device_count": 1,
                "targets": [
                    {
                        "name": "Archive Drive",
                        "path_label": "F:/DocumentsBackup",
                        "independent_device_id": "disk-b",
                    }
                ],
            },
        ]
        payload["backup_overview"] = overview
        return IpcResponse.accepted(payload)

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        self.requested_job_ids.append(job_id)
        response = super().get_backup_job_detail(job_id=job_id)
        if job_id != "job-b":
            return response
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        detail["job_id"] = "job-b"
        job = dict(detail["job"])
        job.update(
            {
                "job_id": "job-b",
                "job_revision_id": "job-rev-b",
                "filter_set_id": "filter-b",
                "title": "Documents",
                "source_name": "Documents",
                "source_path_label": "C:/Users/Ada/Documents",
                "targets": [
                    {
                        "name": "Archive Drive",
                        "path_label": "F:/DocumentsBackup",
                        "independent_device_id": "disk-b",
                        "registration_state": "WRITABLE_READY",
                    }
                ],
                "initial_plan": {
                    "state": "SEALED",
                    "reason_code": "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
                    "analysis_id": "analysis-b",
                    "plan_id": "plan-b",
                    "plan_checksum": "b" * 64,
                    "operation_count": 1,
                    "planned_bytes": 1024,
                    "plan_runnable": True,
                    "next_action": "Review the plan.",
                },
            }
        )
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)


class _BlockingStartMultiJobEngineClient(_FakeMultiJobDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.attempted_calls = 0
        self.worker_thread_id: int | None = None

    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
        target_endpoint_ids: tuple[str, ...] = (),
        resumed_from_run_id: str | None = None,
        source_operation_ids: tuple[str, ...] = (),
    ) -> IpcResponse:
        self.attempted_calls += 1
        self.worker_thread_id = get_ident()
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test start command release timed out")
        return super().start_backup(
            plan_id=plan_id,
            plan_checksum=plan_checksum,
            request_id=request_id,
            idempotency_key=idempotency_key,
            target_endpoint_ids=target_endpoint_ids,
            resumed_from_run_id=resumed_from_run_id,
            source_operation_ids=source_operation_ids,
        )


class _BlockingMultiJobDashboardEngineClient(_FakeMultiJobDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self._started_calls = 0
        self._active_calls = 0
        self.max_active = 0
        self.attempted_job_ids: list[str] = []

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        with self._worker_lock:
            self.attempted_job_ids.append(job_id)
            self._started_calls += 1
            call_no = self._started_calls
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
        try:
            if call_no == 1:
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise TimeoutError("test query release timed out")
            return super().get_backup_job_detail(job_id=job_id)
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakePagedJobsEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.requested_offsets: list[int] = []

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del draft_id, lifecycle_state
        assert limit == 25
        page_offset = 0 if offset is None else offset
        self.calls.append("get_backup_overview")
        self.requested_offsets.append(page_offset)
        job_id = "job-a" if page_offset == 0 else "job-z"
        title = "Pictures" if page_offset == 0 else "Archive"
        return IpcResponse.accepted(
            {
                "backup_overview": {
                    "read_model_available": True,
                    "limit": 25,
                    "offset": page_offset,
                    "has_more": page_offset == 0,
                    "draft": None,
                    "jobs": [
                        {
                            "job_id": job_id,
                            "title": title,
                            "source_name": title,
                            "source_path_label": f"C:/Users/Ada/{title}",
                            "configured_target_count": 1,
                            "independent_device_count": 1,
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
        response = super().get_backup_job_detail(job_id=job_id)
        if job_id == "job-a":
            return response
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        detail["job_id"] = job_id
        job = dict(detail["job"])
        job["job_id"] = job_id
        job["title"] = "Archive"
        job["source_name"] = "Archive"
        job["source_path_label"] = "C:/Users/Ada/Archive"
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)


class _BlockingPagedJobsEngineClient(_FakePagedJobsEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self._active_calls = 0
        self.max_active = 0

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        lifecycle_state: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        with self._worker_lock:
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
        self.started.set()
        try:
            if not self.release.wait(timeout=5):
                raise TimeoutError("test query release timed out")
            return super().get_backup_overview(
                draft_id=draft_id,
                lifecycle_state=lifecycle_state,
                limit=limit,
                offset=offset,
            )
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakeHistoryEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.history_queries: list[
            tuple[
                str,
                str | None,
                int,
                dict[str, object] | None,
                int | None,
            ]
        ] = []
        self.operation_audit_queries: list[tuple[str, str, int]] = []

    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        normalized_filter = activity_filter or "ALL"
        normalized_limit = limit or 25
        normalized_offset = 0 if offset is None else offset
        self.history_queries.append(
            (
                normalized_filter,
                job_id,
                normalized_limit,
                after,
                offset,
            )
        )
        activities = [
            _history_activity_payload("run-a", kind="BACKUP"),
            _history_activity_payload("analysis-a", kind="CONTROL"),
        ]
        if normalized_filter == "CONTROLS":
            activities = activities[1:]
        elif normalized_filter == "BACKUPS":
            activities = activities[:1]
        if job_id is not None:
            activities = [
                activity for activity in activities if activity["job_id"] == job_id
            ]
        return IpcResponse.accepted(
            {
                "history_timeline": {
                    "read_model_available": True,
                    "limit": normalized_limit,
                    "offset": normalized_offset,
                    "has_more": False,
                    "activity_filter": normalized_filter,
                    "job_id": job_id,
                    "next_cursor": None,
                    "activities": activities,
                }
            }
        )

    def get_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        normalized_limit = limit or 25
        self.operation_audit_queries.append((run_id, operation_id, normalized_limit))
        retried = operation_id == "op-b"
        path = (
            "Photos/2026/a-directory-name-that-keeps-going/a-very-long-file-name.jpg"
            if retried
            else "Photos"
        )
        attempts: list[dict[str, object]] = []
        if retried:
            attempts.append(
                {
                    "attempt_number": 1,
                    "state": "FAILED",
                    "finished_utc": "2026-07-20T12:00:05.000Z",
                    "bytes_transferred": 0,
                    "transfer_state": "FAILED",
                    "assurance_level": "NONE",
                    "durability_level": "NOT_REQUESTED",
                    "error_code": "LOCAL_IO_TRANSIENT",
                }
            )
        attempts.append(
            {
                "attempt_number": 2 if retried else 1,
                "state": "SUCCEEDED",
                "finished_utc": "2026-07-20T12:00:08.000Z",
                "bytes_transferred": 2048 if retried else 0,
                "transfer_state": "TRANSFERRED",
                "assurance_level": "PRIMARY_STREAM_HASH_VERIFIED",
                "durability_level": "LOCAL_FILE_FLUSH_CONFIRMED",
                "error_code": None,
            }
        )
        return IpcResponse.accepted(
            {
                "operation_audit": {
                    "run_id": run_id,
                    "run_target_id": "run-a-target-0000",
                    "operation_id": operation_id,
                    "target_relative_path": path,
                    "limit": normalized_limit,
                    "read_model_available": True,
                    "found": True,
                    "attempts": attempts,
                    "outcome": {
                        "final_state": "SUCCEEDED",
                        "completed_utc": "2026-07-20T12:00:09.000Z",
                        "bytes_transferred": 2048 if retried else 0,
                        "transfer_state": "TRANSFERRED",
                        "assurance_level": "PRIMARY_STREAM_HASH_VERIFIED",
                        "hash_evidence_kind": "CURRENT_READ_HASH",
                        "durability_level": "WRITE_THROUGH_REQUEST_CONFIRMED",
                        "error_code": None,
                    },
                }
            }
        )


class _FakeRetainedVersionHistoryEngineClient(_FakeHistoryEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.protected = False
        self.restore_state: str | None = None
        self.rollback_state: str | None = None
        self.pending_restore_queries_before_completion: int | None = None
        self.pending_undo_queries_before_completion: int | None = None
        self.version_queries: list[tuple[str, int]] = []
        self.protection_commands: list[tuple[str, int, str, str]] = []
        self.restore_commands: list[tuple[str, int, str, str]] = []
        self.undo_commands: list[tuple[str, str, int, str, str]] = []

    def get_retained_versions(
        self,
        *,
        run_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        assert after is None
        normalized_limit = limit or 25
        self.version_queries.append((run_id, normalized_limit))
        if (
            self.restore_state == "REQUESTED"
            and self.pending_restore_queries_before_completion is not None
        ):
            if self.pending_restore_queries_before_completion == 0:
                self.restore_state = "COMPLETED"
                self.rollback_state = "AVAILABLE"
                self.protected = False
            else:
                self.pending_restore_queries_before_completion -= 1
        if (
            self.rollback_state == "UNDO_REQUESTED"
            and self.pending_undo_queries_before_completion is not None
        ):
            if self.pending_undo_queries_before_completion == 0:
                self.rollback_state = "UNDONE"
            else:
                self.pending_undo_queries_before_completion -= 1
        return IpcResponse.accepted(
            {
                "retained_versions": {
                    "run_id": run_id,
                    "limit": normalized_limit,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "versions": [
                        {
                            "version_object_id": "version-a",
                            "object_role": "EMPTY_DIRECTORY_QUARANTINE",
                            "run_id": run_id,
                            "operation_id": "op-a",
                            "job_id": "job-a",
                            "target_endpoint_id": "target-a",
                            "final_relative_path": (
                                "Photos/Family/2026/"
                                "AReallyLongPreviousVersionFilenameWithoutBreaks.jpg"
                            ),
                            "created_utc": "2026-07-20T12:00:01.000Z",
                            "retention_until_utc": "2026-08-19T12:00:01.000Z",
                            "state": "RETAINED",
                            "row_version": 1,
                            "restorable": True,
                            "protected_for_restore": self.protected,
                            "restore_id": (
                                "restore-a" if self.restore_state is not None else None
                            ),
                            "restore_state": self.restore_state,
                            "restore_pending": self.restore_state == "REQUESTED",
                            "restore_validation_code": None,
                            "rollback_state": self.rollback_state,
                            "rollback_retention_until_utc": (
                                "2026-09-09T12:00:01.000Z"
                                if self.rollback_state is not None
                                else None
                            ),
                            "rollback_validation_code": None,
                            "restore_undo_available": (
                                self.restore_state == "COMPLETED"
                                and self.rollback_state == "AVAILABLE"
                            ),
                            "restore_undo_pending": (
                                self.rollback_state == "UNDO_REQUESTED"
                            ),
                            "hold_id": "restore:key-a" if self.protected else None,
                            "hold_reason": (
                                "RESTORE_REQUESTED" if self.protected else None
                            ),
                            "hold_created_utc": (
                                "2026-08-01T10:00:00.000Z"
                                if self.protected
                                else None
                            ),
                        }
                    ],
                }
            }
        )

    def protect_retained_version_for_restore(
        self,
        *,
        version_object_id: str,
        expected_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.protection_commands.append(
            (
                version_object_id,
                expected_row_version,
                request_id,
                idempotency_key,
            )
        )
        self.protected = True
        return IpcResponse.accepted(
            {
                "version_restore_protection": {
                    "protected": True,
                    "validation_code": "VERSION_RESTORE_PROTECTED",
                    "next_action": "Protected.",
                }
            }
        )

    def undo_retained_version_restore(
        self,
        *,
        restore_id: str,
        version_object_id: str,
        expected_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.undo_commands.append(
            (
                restore_id,
                version_object_id,
                expected_row_version,
                request_id,
                idempotency_key,
            )
        )
        self.rollback_state = "UNDO_REQUESTED"
        return IpcResponse.accepted(
            {
                "version_restore_undo_request": {
                    "scheduled": True,
                    "validation_code": "VERSION_RESTORE_UNDO_SCHEDULED",
                    "next_action": "Scheduled.",
                    "restore_id": restore_id,
                    "state": "UNDO_REQUESTED",
                }
            }
        )

    def restore_retained_version(
        self,
        *,
        version_object_id: str,
        expected_row_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> IpcResponse:
        self.restore_commands.append(
            (
                version_object_id,
                expected_row_version,
                request_id,
                idempotency_key,
            )
        )
        self.restore_state = "REQUESTED"
        return IpcResponse.accepted(
            {
                "version_restore_request": {
                    "scheduled": True,
                    "validation_code": "VERSION_RESTORE_SCHEDULED",
                    "next_action": "Scheduled.",
                    "restore_id": "restore-a",
                    "state": "REQUESTED",
                }
            }
        )


def test_history_previous_version_can_schedule_restore_without_compact_clipping(
    qapp,
    monkeypatch,
) -> None:
    provider = _FakeRetainedVersionHistoryEngineClient()
    window = build_main_window(
        initial_state=_ready_state(),
        engine_client=provider,
        theme_mode=ThemeMode.DARK,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    try:
        window.resize(900, 560)
        window.show()
        qapp.processEvents()
        nav = window.findChild(QListWidget, "navigationRail")
        version_list = window.findChild(
            BoundedVirtualTableView,
            "retainedVersionList",
        )
        protect = window.findChild(QPushButton, "protectRetainedVersionButton")
        heading = window.findChild(QLabel, "retainedVersionHeading")
        history_scroll = window.findChild(QScrollArea, "historyScrollArea")
        language = window.findChild(QToolButton, "languageSelectorButton")

        assert nav is not None
        assert version_list is not None
        assert protect is not None
        assert heading is not None
        assert history_scroll is not None
        assert language is not None and language.menu() is not None
        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )
        qapp.processEvents()

        assert provider.version_queries[-1] == ("run-a", 25)
        assert _virtual_row_count(version_list) == 1
        assert "Tom mappe:" in _virtual_row_text(version_list, 0)
        assert version_list.horizontalScrollBar().maximum() == 0
        assert history_scroll.horizontalScrollBar().maximum() == 0
        _click_virtual_row(version_list, 0)
        qapp.processEvents()
        assert protect.isEnabled()
        assert protect.text() == "Beskytt for gjenoppretting"

        QTest.mouseClick(protect, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(provider.protection_commands) == 1
        assert provider.protection_commands[0][:2] == ("version-a", 1)
        assert protect.text() == "Gjenopprett valgt versjon"
        assert protect.isEnabled()

        provider.pending_restore_queries_before_completion = 1
        QTest.mouseClick(protect, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(provider.restore_commands) == 1
        assert provider.restore_commands[0][:2] == ("version-a", 1)
        assert protect.text() == "Gjenoppretting p\u00e5g\u00e5r"
        assert not protect.isEnabled()
        language.menu().actions()[1].trigger()
        qapp.processEvents()
        assert heading.text() == "Recovery items"
        assert "Empty folder:" in _virtual_row_text(version_list, 0)
        assert protect.text() == "Restore in progress"
        assert history_scroll.horizontalScrollBar().maximum() == 0
        assert (
            protect.fontMetrics().horizontalAdvance(protect.text())
            <= protect.contentsRect().width()
        )
        QTest.qWait(1_200)
        qapp.processEvents()
        assert "Restored" in _virtual_row_text(version_list, 0)
        assert protect.text() == "Undo restore"
        assert protect.isEnabled()
        provider.pending_undo_queries_before_completion = 1
        QTest.mouseClick(protect, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert provider.undo_commands[0][:3] == (
            "restore-a",
            "version-a",
            1,
        )
        assert protect.text() == "Undo in progress"
        assert not protect.isEnabled()
        QTest.qWait(1_200)
        qapp.processEvents()
        assert "Restore undone" in _virtual_row_text(version_list, 0)
        assert protect.text() == "Protect for restore"
        assert protect.isEnabled()
        assert len(provider.version_queries) >= 6
    finally:
        window.close()
        window.deleteLater()


class _BlockingHistoryAuditEngineClient(_FakeHistoryEngineClient):
    def __init__(self, *, block_call_no: int) -> None:
        super().__init__()
        self.block_call_no = block_call_no
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self._started_calls = 0
        self._active_calls = 0
        self.max_active = 0
        self.attempted_operation_ids: list[str] = []

    def get_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        with self._worker_lock:
            self._started_calls += 1
            call_no = self._started_calls
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
            self.attempted_operation_ids.append(operation_id)
        try:
            if call_no == self.block_call_no:
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise TimeoutError("test query release timed out")
            return super().get_operation_audit(
                run_id=run_id,
                operation_id=operation_id,
                limit=limit,
            )
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakeHistoryOperationRetryEngineClient(_FakeHistoryEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.analysis_requested = False
        self.check_start_policies: list[bool] = []
        self.started_plan: tuple[str, str] | None = None
        self.started_scope: tuple[tuple[str, ...], str | None] | None = None
        self.started_operation_ids: tuple[str, ...] = ()

    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        response = super().get_history_timeline(
            activity_filter=activity_filter,
            job_id=job_id,
            limit=limit,
            after=after,
            offset=offset,
        )
        payload = dict(response.payload)
        timeline = dict(payload["history_timeline"])
        activities = [dict(item) for item in timeline["activities"]]
        for activity in activities:
            if activity["activity_kind"] != "BACKUP":
                continue
            activity.update(
                {
                    "state": "COMPLETED_WITH_WARNINGS",
                    "completed_operations": 1,
                    "error_count": 1,
                }
            )
            target = dict(activity["targets"][0])
            target.update(
                {
                    "endpoint_id": "target-a",
                    "state": "SUCCEEDED_WITH_WARNINGS",
                    "completed_operations": 1,
                    "error_count": 1,
                }
            )
            activity["targets"] = [target]
        timeline["activities"] = activities
        payload["history_timeline"] = timeline
        return IpcResponse.accepted(payload)

    def get_operation_audit(
        self,
        *,
        run_id: str,
        operation_id: str,
        limit: int | None = None,
    ) -> IpcResponse:
        response = super().get_operation_audit(
            run_id=run_id,
            operation_id=operation_id,
            limit=limit,
        )
        if operation_id != "op-b":
            return response
        payload = dict(response.payload)
        audit = dict(payload["operation_audit"])
        outcome = dict(audit["outcome"])
        outcome.update(
            {
                "final_state": "SKIPPED",
                "bytes_transferred": 0,
                "transfer_state": "FAILED",
                "assurance_level": "NONE",
                "hash_evidence_kind": None,
                "durability_level": "NOT_REQUESTED",
                "error_code": "LOCAL_IO_TRANSIENT",
            }
        )
        audit["outcome"] = outcome
        payload["operation_audit"] = audit
        return IpcResponse.accepted(payload)

    def check_backup(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
        start_when_safe: bool = True,
    ) -> IpcResponse:
        assert job_id == "job-a"
        assert request_id and idempotency_key
        self.analysis_requested = True
        self.check_start_policies.append(start_when_safe)
        return IpcResponse.accepted(
            {
                "analysis_request": {
                    "request_id": "analysis-operation-retry",
                    "state": "QUEUED",
                }
            }
        )

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        response = super().get_backup_job_detail(job_id=job_id)
        if not self.analysis_requested:
            return response
        payload = dict(response.payload)
        detail = dict(payload["backup_job_detail"])
        job = dict(detail["job"])
        initial_plan = dict(job["initial_plan"])
        initial_plan.update(
            {
                "analysis_id": "analysis-operation-refreshed",
                "plan_id": "plan-operation-refreshed",
                "plan_checksum": "c" * 64,
                "operation_count": 1,
                "planned_bytes": 2048,
                "plan_runnable": True,
            }
        )
        job["initial_plan"] = initial_plan
        job["latest_analysis_request"] = {
            "request_id": "analysis-operation-retry",
            "job_id": "job-a",
            "job_revision_id": "job-rev-a",
            "state": "SUCCEEDED",
            "requested_utc": "2026-07-31T12:00:00.000Z",
            "started_utc": "2026-07-31T12:00:01.000Z",
            "completed_utc": "2026-07-31T12:00:02.000Z",
            "analysis_id": "analysis-operation-refreshed",
            "plan_id": "plan-operation-refreshed",
            "reason_code": "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
            "operation_count": 1,
            "planned_bytes": 2048,
            "start_when_safe": False,
            "started_run_id": None,
            "row_version": 2,
        }
        detail["job"] = job
        payload["backup_job_detail"] = detail
        return IpcResponse.accepted(payload)

    def start_backup(
        self,
        *,
        plan_id: str,
        plan_checksum: str,
        request_id: str,
        idempotency_key: str,
        target_endpoint_ids: tuple[str, ...] = (),
        resumed_from_run_id: str | None = None,
        source_operation_ids: tuple[str, ...] = (),
    ) -> IpcResponse:
        assert request_id and idempotency_key
        self.started_plan = (plan_id, plan_checksum)
        self.started_scope = (target_endpoint_ids, resumed_from_run_id)
        self.started_operation_ids = source_operation_ids
        return IpcResponse.accepted(
            {"created": True, "run": {"run_id": "run-operation-retry"}}
        )


class _FakePagedHistoryEngineClient(_FakeHistoryEngineClient):
    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        normalized_filter = activity_filter or "ALL"
        normalized_limit = limit or 25
        normalized_offset = 0 if offset is None else offset
        self.history_queries.append(
            (
                normalized_filter,
                job_id,
                normalized_limit,
                after,
                offset,
            )
        )
        first_page = after is None and normalized_offset == 0
        activity_id = "run-a" if first_page else "run-z"
        payload = _history_activity_payload(activity_id, kind="BACKUP")
        return IpcResponse.accepted(
            {
                "history_timeline": {
                    "read_model_available": True,
                    "limit": normalized_limit,
                    "offset": normalized_offset,
                    "has_more": first_page,
                    "activity_filter": normalized_filter,
                    "job_id": job_id,
                    "next_cursor": (
                        _history_cursor_payload(payload) if first_page else None
                    ),
                    "activities": [payload],
                }
            }
        )


class _FakeLegacyPagedHistoryEngineClient(_FakeHistoryEngineClient):
    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        normalized_filter = activity_filter or "ALL"
        normalized_limit = limit or 25
        normalized_offset = 0 if offset is None else offset
        self.history_queries.append(
            (normalized_filter, job_id, normalized_limit, after, offset)
        )
        first_page = normalized_offset == 0
        activity_id = "run-a" if first_page else "run-z"
        return IpcResponse.accepted(
            {
                "history_timeline": {
                    "read_model_available": True,
                    "limit": normalized_limit,
                    "offset": normalized_offset,
                    "has_more": first_page,
                    "activity_filter": normalized_filter,
                    "job_id": job_id,
                    "activities": [
                        _history_activity_payload(activity_id, kind="BACKUP")
                    ],
                }
            }
        )


class _BlockingPagedHistoryEngineClient(_FakePagedHistoryEngineClient):
    def __init__(self, *, block_call_no: int) -> None:
        super().__init__()
        self.block_call_no = block_call_no
        self.started = Event()
        self.release = Event()
        self._worker_lock = Lock()
        self._started_calls = 0
        self._active_calls = 0
        self.max_active = 0

    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        with self._worker_lock:
            self._started_calls += 1
            call_no = self._started_calls
            self._active_calls += 1
            self.max_active = max(self.max_active, self._active_calls)
        try:
            if call_no == self.block_call_no:
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise TimeoutError("test query release timed out")
            return super().get_history_timeline(
                activity_filter=activity_filter,
                job_id=job_id,
                limit=limit,
                after=after,
                offset=offset,
            )
        finally:
            with self._worker_lock:
                self._active_calls -= 1


class _FakePagedHistoryOperationsEngineClient(_FakeHistoryEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.operation_page_queries: list[
            tuple[str, int, dict[str, object] | None]
        ] = []

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        target_endpoint_id: str | None = None,
        risk_levels: tuple[str, ...] = (),
    ) -> IpcResponse:
        del target_endpoint_id, risk_levels
        normalized_limit = limit or 100
        self.calls.append("get_plan_operations")
        self.operation_page_queries.append((plan_id, normalized_limit, after))
        first_page = after is None
        operation_id = "op-a" if first_page else "op-z"
        path = "Photos/a.jpg" if first_page else "Photos/z.jpg"
        return IpcResponse.accepted(
            {
                "plan_operations": {
                    "plan_id": plan_id,
                    "limit": normalized_limit,
                    "has_more": first_page,
                    "read_model_available": True,
                    "next_cursor": (
                        {
                            "execution_phase": 20,
                            "stable_order_key": "photos/a.jpg",
                            "operation_id": "op-a",
                        }
                        if first_page
                        else None
                    ),
                    "risk_counts": {
                        "LOW": 2,
                        "MEDIUM": 0,
                        "HIGH": 0,
                        "BLOCKED": 0,
                    },
                    "highest_risk": "LOW",
                    "target_endpoint_ids": ["target-a"],
                    "operations": [
                        {
                            "operation_id": operation_id,
                            "operation_type": "COPY_NEW",
                            "sequence_no": 0 if first_page else 25,
                            "execution_phase": 20,
                            "stable_order_key": path.lower(),
                            "target_precondition_kind": "ABSENT",
                            "reason_code": "SOURCE_ONLY",
                            "risk_level": "LOW",
                            "target_endpoint_id": "target-a",
                            "target_relative_path": path,
                            "planned_bytes": 128,
                        }
                    ],
                }
            }
        )


def _history_activity_payload(
    activity_id: str,
    *,
    kind: str,
) -> dict[str, object]:
    is_backup = kind == "BACKUP"
    return {
        "activity_id": activity_id,
        "activity_kind": kind,
        "job_id": "job-a",
        "job_revision_id": "job-rev-a",
        "job_title": "Pictures",
        "run_id": activity_id if is_backup else None,
        "analysis_id": None if is_backup else activity_id,
        "plan_id": "plan-a" if is_backup else None,
        "state": "COMPLETED" if is_backup else "NO_CHANGES",
        "started_utc": (
            "2026-07-20T12:00:00.000Z" if is_backup else "2026-07-20T11:00:00.000Z"
        ),
        "finished_utc": (
            "2026-07-20T12:01:30.000Z" if is_backup else "2026-07-20T11:01:00.000Z"
        ),
        "planned_operations": 2 if is_backup else 0,
        "completed_operations": 2 if is_backup else 0,
        "planned_bytes": 1024 if is_backup else 0,
        "completed_bytes": 1024 if is_backup else 0,
        "warning_count": 0,
        "error_count": 0,
        "trigger_type": ("MANUAL_LOCAL_PREVIEW" if is_backup else "INITIAL_JOB_SETUP"),
        "targets": [
            {
                "endpoint_id": (
                    "target-with-a-deliberately-long-stable-identifier-for-layout"
                ),
                "endpoint_revision_id": "target-rev-a",
                "state": "SUCCEEDED" if is_backup else "WRITABLE_READY",
                "planned_operations": 2 if is_backup else 0,
                "completed_operations": 2 if is_backup else 0,
                "planned_bytes": 1024 if is_backup else 0,
                "completed_bytes": 1024 if is_backup else 0,
                "warning_count": 0,
                "error_count": 0,
            }
        ],
    }


def _history_cursor_payload(activity: dict[str, object]) -> dict[str, object]:
    return {
        "cursor_version": 1,
        "started_utc": activity["started_utc"],
        "activity_kind": activity["activity_kind"],
        "activity_id": activity["activity_id"],
    }


class _FakePlanOnlyDashboardEngineClient(_FakeDashboardEngineClient):
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
                    "runs": [],
                }
            }
        )
