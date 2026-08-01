from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSignalBlocker,
    Qt,
    Signal,
)
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget


@dataclass(frozen=True)
class VirtualTableRow:
    row_id: str
    cells: tuple[str, ...]
    tooltip: str | None = None


class BoundedTableModel(QAbstractTableModel):
    """A reset-only table page with an explicit managed-object ceiling."""

    def __init__(
        self,
        *,
        headers: tuple[str, ...],
        max_cached_rows: int,
        parent: QObject | None = None,
    ) -> None:
        if not headers:
            raise ValueError("VIRTUAL_TABLE_HEADERS_REQUIRED")
        if max_cached_rows < 1:
            raise ValueError("VIRTUAL_TABLE_CACHE_LIMIT_INVALID")
        super().__init__(parent)
        self._headers = headers
        self._max_cached_rows = max_cached_rows
        self._rows: tuple[VirtualTableRow, ...] = ()
        self._row_indexes: dict[str, int] = {}

    @property
    def max_cached_rows(self) -> int:
        return self._max_cached_rows

    @property
    def cached_row_count(self) -> int:
        return len(self._rows)

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        if not index.isValid():
            return None
        row_number = index.row()
        column_number = index.column()
        if (
            row_number < 0
            or row_number >= len(self._rows)
            or column_number < 0
            or column_number >= len(self._headers)
        ):
            return None
        row = self._rows[row_number]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return row.cells[column_number]
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return row.tooltip or row.cells[column_number]
        if role == int(Qt.ItemDataRole.UserRole):
            return row.row_id
        if role == int(Qt.ItemDataRole.AccessibleTextRole):
            return ", ".join(row.cells)
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        if (
            role == int(Qt.ItemDataRole.DisplayRole)
            and orientation is Qt.Orientation.Horizontal
            and 0 <= section < len(self._headers)
        ):
            return self._headers[section]
        return cast(object | None, super().headerData(section, orientation, role))

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,
    ) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def replace_rows(self, rows: tuple[VirtualTableRow, ...]) -> None:
        if len(rows) > self._max_cached_rows:
            raise ValueError("VIRTUAL_TABLE_CACHE_LIMIT_EXCEEDED")
        row_indexes: dict[str, int] = {}
        for index, row in enumerate(rows):
            if not row.row_id or row.row_id in row_indexes:
                raise ValueError("VIRTUAL_TABLE_ROW_ID_INVALID")
            if len(row.cells) != len(self._headers):
                raise ValueError("VIRTUAL_TABLE_COLUMN_COUNT_MISMATCH")
            row_indexes[row.row_id] = index
        self.beginResetModel()
        self._rows = rows
        self._row_indexes = row_indexes
        self.endResetModel()

    def replace_headers(self, headers: tuple[str, ...]) -> None:
        if len(headers) != len(self._headers) or not all(headers):
            raise ValueError("VIRTUAL_TABLE_HEADERS_INVALID")
        self._headers = headers
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal,
            0,
            len(headers) - 1,
        )

    def row_id(self, row_number: int) -> str | None:
        if row_number < 0 or row_number >= len(self._rows):
            return None
        return self._rows[row_number].row_id

    def row_index(self, row_id: str) -> int | None:
        return self._row_indexes.get(row_id)


class BoundedVirtualTableView(QTableView):
    rowSelected = Signal(str)  # noqa: N815

    def __init__(
        self,
        *,
        headers: tuple[str, ...],
        max_cached_rows: int,
        column_weights: tuple[int, ...],
        compact_hidden_columns: tuple[int, ...] = (),
        compact_width_threshold: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bounded_model = BoundedTableModel(
            headers=headers,
            max_cached_rows=max_cached_rows,
            parent=self,
        )
        self.setModel(self._bounded_model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setDefaultSectionSize(36)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setMinimumSectionSize(24)
        for section in range(len(headers)):
            header.setSectionResizeMode(
                section,
                QHeaderView.ResizeMode.Fixed,
            )
        if (
            len(column_weights) != len(headers)
            or any(weight < 1 for weight in column_weights)
        ):
            raise ValueError("VIRTUAL_TABLE_COLUMN_WEIGHTS_INVALID")
        if compact_width_threshold < 0:
            raise ValueError("VIRTUAL_TABLE_COMPACT_WIDTH_INVALID")
        if any(
            section < 0 or section >= len(headers)
            for section in compact_hidden_columns
        ):
            raise ValueError("VIRTUAL_TABLE_COMPACT_COLUMN_INVALID")
        self._compact_hidden_columns = compact_hidden_columns
        self._compact_width_threshold = compact_width_threshold
        self._column_weights = column_weights
        self.selectionModel().currentRowChanged.connect(self._emit_row_selected)

    @property
    def bounded_model(self) -> BoundedTableModel:
        return self._bounded_model

    def replace_rows(
        self,
        rows: tuple[VirtualTableRow, ...],
        *,
        selected_row_id: str | None,
    ) -> str | None:
        selection_model = self.selectionModel()
        blocker = QSignalBlocker(selection_model)
        self._bounded_model.replace_rows(rows)
        self._fit_visible_columns()
        selected_index = (
            self._bounded_model.row_index(selected_row_id)
            if selected_row_id is not None
            else None
        )
        if selected_index is None and rows:
            selected_index = 0
        if selected_index is None:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
            resolved_selection = None
        else:
            self.selectRow(selected_index)
            self.setCurrentIndex(self._bounded_model.index(selected_index, 0))
            resolved_selection = self._bounded_model.row_id(selected_index)
        del blocker
        return resolved_selection

    def replace_headers(self, headers: tuple[str, ...]) -> None:
        self._bounded_model.replace_headers(headers)

    def selected_row_id(self) -> str | None:
        return self._bounded_model.row_id(self.currentIndex().row())

    def select_row_id(self, row_id: str) -> bool:
        row_number = self._bounded_model.row_index(row_id)
        if row_number is None:
            return False
        self.selectRow(row_number)
        self.setCurrentIndex(self._bounded_model.index(row_number, 0))
        return True

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        compact = (
            self._compact_width_threshold > 0
            and event.size().width() < self._compact_width_threshold
        )
        for section in self._compact_hidden_columns:
            self.setColumnHidden(section, compact)
        self._fit_visible_columns()

    def _fit_visible_columns(self) -> None:
        visible_sections = tuple(
            section
            for section in range(self._bounded_model.columnCount())
            if not self.isColumnHidden(section)
        )
        if not visible_sections:
            return
        remaining_width = max(1, self.viewport().width())
        remaining_weight = sum(
            self._column_weights[section] for section in visible_sections
        )
        for position, section in enumerate(visible_sections):
            if position == len(visible_sections) - 1:
                width = remaining_width
            else:
                width = max(
                    1,
                    remaining_width * self._column_weights[section]
                    // remaining_weight,
                )
            self.setColumnWidth(section, width)
            remaining_width -= width
            remaining_weight -= self._column_weights[section]

    def _emit_row_selected(self, current: QModelIndex, previous: QModelIndex) -> None:
        del previous
        row_id = self._bounded_model.row_id(current.row())
        if row_id is not None:
            self.rowSelected.emit(row_id)
