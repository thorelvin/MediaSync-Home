from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLabel,
    QListWidget,
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
        assert create_backup.text() == "Continue"
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
        window.resize(window_width, window_height)
        window.show()
        qapp.processEvents()
        choices = [
            "C:/Users/Example/Documents/"
            "ImportantDocumentsAndFamilyPicturesCollectionWithoutBreaks",
            "E:/MediaSync Home Backups/Primary External Drive/"
            "CompleteComputerBackupTargetFolderWithoutBreaks",
        ]
        window._choose_directory = lambda title: choices.pop(0)  # type: ignore[method-assign]
        create_backup = window.findChild(QPushButton, "createBackupButton")
        add_target = window.findChild(QToolButton, "addTargetButton")
        setup_back = window.findChild(QToolButton, "setupBackButton")
        target_paths = window.findChildren(QLabel, "setupTargetPathRow")
        dashboard_scroll = window.findChild(QScrollArea, "dashboardScrollArea")
        activity_scroll = window.findChild(QScrollArea, "activityScrollArea")

        assert create_backup is not None
        assert add_target is not None
        assert setup_back is not None
        assert len(target_paths) == 3
        assert dashboard_scroll is not None
        assert activity_scroll is not None
        QTest.mouseClick(create_backup, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        QTest.mouseClick(add_target, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert target_paths[0].text().endswith(
            "CompleteComputerBackupTargetFolderWithoutBreaks"
        )
        assert target_paths[0].isVisible() is True
        assert window._dashboard_detail_layout is not None
        assert window._dashboard_detail_layout.direction() is QBoxLayout.Direction.TopToBottom
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
        assert dashboard_scroll.verticalScrollBar().maximum() > 0
        assert activity_scroll.verticalScrollBar().maximum() > 0
        dashboard_page = dashboard_scroll.widget()
        assert dashboard_page is not None
        assert dashboard_page.height() >= dashboard_page.minimumSizeHint().height()
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
        assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog) is True
        assert dialog.testOption(QFileDialog.Option.ShowDirsOnly) is True
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
            "MediaSyncBackup: E:/MediaSyncBackup",
            "OffsiteBackup: F:/OffsiteBackup",
            "TemporaryBackup: G:/TemporaryBackup",
        ]

        QTest.mouseClick(remove_targets[1], Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert add_target.isEnabled() is True
        assert [label.text() for label in target_paths[:2]] == [
            "MediaSyncBackup: E:/MediaSyncBackup",
            "TemporaryBackup: G:/TemporaryBackup",
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


def test_failed_target_registration_keeps_review_and_retry_without_clipping(qapp) -> None:
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
        assert jobs_list.currentItem().text() == (
            "Pictures\n1 mål / 1 uavhengig enhet"
        )
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
        assert job_detail_defaults.text() == "Oppdater backup - Alle brukerfiler - Standard kontroll"
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
        assert plan_preview_rows[0].text() == "Lav: Opprett mappe: Photos"
        assert plan_preview_rows[1].text() == "Lav: Kopier ny: Photos/2026/a.jpg - 2.0 KiB"
        assert plan_endpoint_summary is not None
        assert plan_endpoint_summary.text() == "2 endepunkter fra plan-a."
        assert plan_endpoint_rows[0].text() == "Kildeendepunkt: source-a · snapshot source-snapshot-a"
        assert plan_endpoint_rows[1].text() == "Målendepunkt 1: target-a · snapshot target-snapshot-a"
        assert snapshot_health_summary is not None
        assert snapshot_health_summary.text() == "1 blokkerende problem i source-snapshot-a."
        assert snapshot_health_rows[0].text() == "Blokkerende problem: Archive · UNREADABLE_DIRECTORY"
        assert snapshot_health_rows[1].text() == "Dekningsadvarsel: Videos · VOLATILE"
        assert cataloged_files_summary is not None
        assert cataloged_files_summary.text() == (
            "1 katalogf\u00f8rt fil. Flere katalogf\u00f8rte filer finnes."
        )
        assert cataloged_files_rows[0].text() == "Photos/2026/a.jpg · target-a · sha abcdef01"
        assert activity_title is not None
        assert activity_title.text() == "Siste kjøring: run-a"
        assert activity_rows[0].text() == "Aktivitet: Kontrollerer"
        assert activity_rows[1].text() == "Oppmerksomhet: Venter"
        assert language is not None
        assert language.menu() is not None
        language.menu().actions()[1].trigger()

        assert target.text() == "1 target: USB 1"
        assert jobs_list.currentItem().text() == (
            "Pictures\n1 target / 1 independent device"
        )
        assert jobs_detail_targets.text() == "1 target / 1 independent device"
        assert job_detail_targets.text() == "1 target / 1 independent device"
        assert job_detail_defaults.text() == "Update backup - All user files - Standard verification"
        assert job_detail_revision.text() == "Revision: job-rev-a - Filter: filter-a"
        assert job_detail_plan.text().startswith("2 operations from plan-a.")
        assert job_detail_plan.text().endswith("Preview only")
        assert plan_preview_summary.text() == "2 operations from plan-a."
        assert plan_preview_rows[0].text() == "Low: Create folder: Photos"
        assert plan_preview_rows[1].text() == "Low: Copy new: Photos/2026/a.jpg - 2.0 KiB"
        assert plan_endpoint_summary.text() == "2 endpoints from plan-a."
        assert plan_endpoint_rows[0].text() == "Source endpoint: source-a · snapshot source-snapshot-a"
        assert plan_endpoint_rows[1].text() == "Target endpoint 1: target-a · snapshot target-snapshot-a"
        assert snapshot_health_summary.text() == "1 blocking issue in source-snapshot-a."
        assert snapshot_health_rows[0].text() == "Blocking issue: Archive · UNREADABLE_DIRECTORY"
        assert snapshot_health_rows[1].text() == "Coverage warning: Videos · VOLATILE"
        assert cataloged_files_summary.text() == (
            "1 cataloged file. More cataloged files exist."
        )
        assert cataloged_files_rows[0].text() == "Photos/2026/a.jpg · target-a · sha abcdef01"
        assert activity_title.text() == "Latest run: run-a"
        assert activity_rows[0].text() == "Activity: Checking"
        assert activity_rows[1].text() == "Attention: Waiting"

        language.menu().actions()[0].trigger()

        assert target.text() == "1 mål: USB 1"
        assert jobs_list.currentItem().text() == (
            "Pictures\n1 mål / 1 uavhengig enhet"
        )
        assert jobs_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert job_detail_targets.text() == "1 mål / 1 uavhengig enhet"
        assert job_detail_defaults.text() == "Oppdater backup - Alle brukerfiler - Standard kontroll"
        assert job_detail_revision.text() == "Revisjon: job-rev-a - Filter: filter-a"
        assert job_detail_plan.text().startswith("2 operasjoner fra plan-a.")
        assert plan_preview_summary.text() == "2 operasjoner fra plan-a."
        assert plan_preview_rows[0].text() == "Lav: Opprett mappe: Photos"
        assert plan_preview_rows[1].text() == "Lav: Kopier ny: Photos/2026/a.jpg - 2.0 KiB"
        assert plan_endpoint_summary.text() == "2 endepunkter fra plan-a."
        assert plan_endpoint_rows[0].text() == "Kildeendepunkt: source-a · snapshot source-snapshot-a"
        assert plan_endpoint_rows[1].text() == "Målendepunkt 1: target-a · snapshot target-snapshot-a"
        assert snapshot_health_summary.text() == "1 blokkerende problem i source-snapshot-a."
        assert snapshot_health_rows[0].text() == "Blokkerende problem: Archive · UNREADABLE_DIRECTORY"
        assert snapshot_health_rows[1].text() == "Dekningsadvarsel: Videos · VOLATILE"
        assert cataloged_files_summary.text() == (
            "1 katalogf\u00f8rt fil. Flere katalogf\u00f8rte filer finnes."
        )
        assert activity_title.text() == "Siste kjøring: run-a"
        assert activity_rows[0].text() == "Aktivitet: Kontrollerer"
        assert activity_rows[1].text() == "Oppmerksomhet: Venter"
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
        history_list = window.findChild(QListWidget, "historyList")
        history_scroll = window.findChild(QScrollArea, "historyScrollArea")
        detail_title = window.findChild(QLabel, "historyDetailTitle")
        detail_status = window.findChild(QLabel, "historyDetailStatusValue")
        detail_operations = window.findChild(QLabel, "historyDetailOperationsValue")
        detail_transferred = window.findChild(QLabel, "historyDetailTransferredValue")
        detail_speed = window.findChild(QLabel, "historyDetailAverageSpeedValue")
        job_filter = window.findChild(QComboBox, "historyJobFilter")
        filter_buttons = {
            button.property("activityFilter"): button
            for button in window.findChildren(QPushButton, "historyFilterButton")
        }

        assert nav is not None
        assert history_list is not None
        assert history_scroll is not None
        assert detail_title is not None
        assert detail_status is not None
        assert detail_operations is not None
        assert detail_transferred is not None
        assert detail_speed is not None
        assert job_filter is not None
        assert set(filter_buttons) == {"ALL", "CONTROLS", "BACKUPS"}

        QTest.mouseClick(
            nav.viewport(),
            Qt.MouseButton.LeftButton,
            pos=nav.visualItemRect(nav.item(2)).center(),
        )
        qapp.processEvents()

        assert history_list.count() == 2
        assert detail_title.text() == "Backup · Pictures"
        assert detail_status.text() == "Fullført"
        assert detail_operations.text() == "2 / 2"
        assert detail_transferred.text() == "1.0 KiB / 1.0 KiB"
        assert detail_speed.text() == "11 B/s"

        QTest.mouseClick(
            history_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=history_list.visualItemRect(history_list.item(1)).center(),
        )
        qapp.processEvents()

        assert history_list.currentItem() is not None
        assert (
            history_list.currentItem().data(Qt.ItemDataRole.UserRole)
            == "CONTROL:analysis-a"
        )
        assert detail_title.text() == "Kontroll · Pictures"
        assert detail_status.text() == "Ingen endringer"
        assert detail_operations.text() == "0 planlagte endringer"
        assert detail_transferred.text() == "Ingen overføring under en kontroll"
        assert detail_speed.text() == "-"

        window.refresh_engine_status()
        qapp.processEvents()
        assert history_list.currentItem() is not None
        assert (
            history_list.currentItem().data(Qt.ItemDataRole.UserRole)
            == "CONTROL:analysis-a"
        )

        QTest.mouseClick(
            filter_buttons["CONTROLS"],
            Qt.MouseButton.LeftButton,
        )
        qapp.processEvents()

        assert provider.history_queries[-1][:2] == ("CONTROLS", None)
        assert history_list.count() == 1
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
        assert history_scroll.horizontalScrollBar().maximum() == 0
        history_page = history_scroll.widget()
        assert history_page is not None
        for label in history_page.findChildren(QLabel):
            if label.property("responsiveText") and not label.isHidden():
                assert label.height() >= label.heightForWidth(label.width())
    finally:
        window.close()
        window.deleteLater()


def test_history_workspace_pages_with_bounded_offsets(qapp) -> None:
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
        history_list = window.findChild(QListWidget, "historyList")
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

        assert provider.history_queries[-1][3] == 25
        assert history_list.count() == 1
        assert (
            history_list.item(0).data(Qt.ItemDataRole.UserRole)
            == "BACKUP:run-z"
        )
        assert previous.isEnabled() is True
        assert next_button.isEnabled() is False

        QTest.mouseClick(previous, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert provider.history_queries[-1][3] == 0
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


class _FakeDashboardEngineClient(_FakeEngineClient):
    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del draft_id, limit, offset
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
    ) -> IpcResponse:
        del limit, after
        self.calls.append("get_plan_operations")
        return IpcResponse.accepted(
            {
                "plan_operations": {
                    "plan_id": plan_id,
                    "limit": 3,
                    "has_more": False,
                    "read_model_available": True,
                    "next_cursor": None,
                    "operations": [
                        {
                            "operation_id": "op-a",
                            "operation_type": "CREATE_DIRECTORY",
                            "sequence_no": 0,
                            "execution_phase": 10,
                            "stable_order_key": "photos",
                            "target_precondition_kind": "ABSENT",
                            "reason_code": "TARGET_DIRECTORY_MISSING",
                            "risk_level": "LOW",
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
                            "target_relative_path": "Photos/2026/a.jpg",
                            "planned_bytes": 2048,
                        },
                    ],
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


class _FakeBackupStartDashboardEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.started_plan: tuple[str, str] | None = None

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
    ) -> IpcResponse:
        assert request_id
        assert idempotency_key
        self.calls.append("start_backup")
        self.started_plan = (plan_id, plan_checksum)
        return IpcResponse.accepted({"created": True, "run": {"run_id": "run-a"}})


class _FakeMultiJobDashboardEngineClient(_FakeBackupStartDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.requested_job_ids: list[str] = []

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        response = super().get_backup_overview(
            draft_id=draft_id,
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


class _FakePagedJobsEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.requested_offsets: list[int] = []

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        del draft_id
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


class _FakeHistoryEngineClient(_FakeDashboardEngineClient):
    def __init__(self) -> None:
        super().__init__()
        self.history_queries: list[tuple[str, str | None, int, int]] = []

    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        normalized_filter = activity_filter or "ALL"
        normalized_limit = limit or 25
        normalized_offset = offset or 0
        self.history_queries.append(
            (
                normalized_filter,
                job_id,
                normalized_limit,
                normalized_offset,
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
                activity
                for activity in activities
                if activity["job_id"] == job_id
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
                    "activities": activities,
                }
            }
        )


class _FakePagedHistoryEngineClient(_FakeHistoryEngineClient):
    def get_history_timeline(
        self,
        *,
        activity_filter: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        normalized_filter = activity_filter or "ALL"
        normalized_limit = limit or 25
        normalized_offset = offset or 0
        self.history_queries.append(
            (
                normalized_filter,
                job_id,
                normalized_limit,
                normalized_offset,
            )
        )
        activity_id = "run-a" if normalized_offset == 0 else "run-z"
        payload = _history_activity_payload(activity_id, kind="BACKUP")
        return IpcResponse.accepted(
            {
                "history_timeline": {
                    "read_model_available": True,
                    "limit": normalized_limit,
                    "offset": normalized_offset,
                    "has_more": normalized_offset == 0,
                    "activity_filter": normalized_filter,
                    "job_id": job_id,
                    "activities": [payload],
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
            "2026-07-20T12:00:00.000Z"
            if is_backup
            else "2026-07-20T11:00:00.000Z"
        ),
        "finished_utc": (
            "2026-07-20T12:01:30.000Z"
            if is_backup
            else "2026-07-20T11:01:00.000Z"
        ),
        "planned_operations": 2 if is_backup else 0,
        "completed_operations": 2 if is_backup else 0,
        "planned_bytes": 1024 if is_backup else 0,
        "completed_bytes": 1024 if is_backup else 0,
        "warning_count": 0,
        "error_count": 0,
        "trigger_type": (
            "MANUAL_LOCAL_PREVIEW" if is_backup else "INITIAL_JOB_SETUP"
        ),
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
