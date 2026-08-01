from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mediasync_home.presentation.virtual_tables import (  # noqa: E402
    BoundedTableModel,
    VirtualTableRow,
)


class _MillionRowSource:
    row_count = 1_000_000

    def page(self, *, start: int, limit: int) -> tuple[VirtualTableRow, ...]:
        stop = min(start + limit, self.row_count)
        return tuple(
            VirtualTableRow(
                row_id=f"operation-{index}",
                cells=("Copy", f"Photos/{index:07d}.jpg"),
                tooltip=f"Operation {index}",
            )
            for index in range(start, stop)
        )


def test_virtual_table_keeps_one_bounded_page_for_million_row_source() -> None:
    source = _MillionRowSource()
    model = BoundedTableModel(
        headers=("Change", "Path"),
        max_cached_rows=200,
    )

    model.replace_rows(source.page(start=0, limit=200))

    assert source.row_count == 1_000_000
    assert model.cached_row_count == 200
    assert model.rowCount() == 200
    assert model.row_id(0) == "operation-0"
    assert model.children() == []

    model.replace_rows(source.page(start=999_800, limit=200))

    assert model.cached_row_count == 200
    assert model.row_id(0) == "operation-999800"
    assert model.row_id(199) == "operation-999999"
    assert model.row_index("operation-0") is None
    assert model.children() == []


def test_virtual_table_enforces_cache_identity_and_column_bounds() -> None:
    model = BoundedTableModel(headers=("A", "B"), max_cached_rows=2)

    with pytest.raises(ValueError, match="VIRTUAL_TABLE_CACHE_LIMIT_EXCEEDED"):
        model.replace_rows(
            tuple(
                VirtualTableRow(row_id=str(index), cells=("a", "b"))
                for index in range(3)
            )
        )
    with pytest.raises(ValueError, match="VIRTUAL_TABLE_ROW_ID_INVALID"):
        model.replace_rows(
            (
                VirtualTableRow(row_id="same", cells=("a", "b")),
                VirtualTableRow(row_id="same", cells=("c", "d")),
            )
        )
    with pytest.raises(ValueError, match="VIRTUAL_TABLE_COLUMN_COUNT_MISMATCH"):
        model.replace_rows((VirtualTableRow(row_id="one", cells=("a",)),))


def test_virtual_table_exposes_headers_ids_tooltips_and_accessible_text() -> None:
    model = BoundedTableModel(headers=("Change", "Path"), max_cached_rows=2)
    model.replace_rows(
        (
            VirtualTableRow(
                row_id="operation-a",
                cells=("Copy", "Photos/a.jpg"),
                tooltip="SOURCE_ONLY",
            ),
        )
    )
    change = model.index(0, 0)
    path = model.index(0, 1)

    assert change.data() == "Copy"
    assert path.data() == "Photos/a.jpg"
    assert path.data(Qt.ItemDataRole.UserRole) == "operation-a"
    assert path.data(Qt.ItemDataRole.ToolTipRole) == "SOURCE_ONLY"
    assert path.data(Qt.ItemDataRole.AccessibleTextRole) == "Copy, Photos/a.jpg"
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Change"

    model.replace_headers(("Endring", "Sti"))

    assert model.headerData(0, Qt.Orientation.Horizontal) == "Endring"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Sti"
