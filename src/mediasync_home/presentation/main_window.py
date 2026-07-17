from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from mediasync_home.presentation.theme.icon_registry import IconRegistry
from mediasync_home.presentation.view_models.engine_status import (
    EngineStatusProvider,
    EngineStatusViewState,
    engine_status_from_response,
)


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

    def apply_engine_status(self, state: EngineStatusViewState) -> None:
        self._engine_chip.setText(f"{state.connection_label}: {state.state_label}")
        self._engine_chip.setProperty("statusKind", state.status_kind)
        self._engine_state.setText(state.state_label)
        self._engine_detail.setText(state.detail)
        self._engine_scope.setText(state.scope_label)
        self._engine_protocol.setText(state.protocol_label)
        self._engine_mutation.setText(state.mutation_label)
        _refresh_style(self._engine_chip)

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
        layout.addWidget(self._build_engine_panel())
        layout.addStretch(1)
        return workspace

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
        if self._show_component_gallery:
            layout.addWidget(self._build_component_gallery())
        layout.addStretch(1)
        return activity

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


def _add_key_value(layout: QGridLayout, row: int, label_text: str, value: QLabel) -> None:
    label = QLabel(label_text)
    label.setObjectName("mutedLabel")
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label, row, 0)
    layout.addWidget(value, row, 1, 1, 2)


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
