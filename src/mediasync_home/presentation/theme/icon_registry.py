from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap


ICON_COLORS = {
    "activity": "#2563eb",
    "add-target": "#14b8a6",
    "archive": "#64748b",
    "back": "#94a3b8",
    "dashboard": "#0f766e",
    "edit": "#2563eb",
    "exit": "#b42318",
    "folder": "#14b8a6",
    "history": "#9a6a12",
    "next": "#94a3b8",
    "refresh": "#475569",
    "remove-target": "#ef4444",
    "save": "#0f766e",
    "settings": "#64748b",
    "status-blocked": "#b42318",
    "status-ready": "#1f7a4d",
    "status-waiting": "#9a6a12",
}


@dataclass
class IconRegistry:
    size: int = 24
    _cache: dict[str, QIcon] = field(default_factory=dict)

    def icon(self, name: str) -> QIcon:
        if name not in ICON_COLORS:
            raise KeyError(f"unknown semantic icon: {name}")
        if name not in self._cache:
            self._cache[name] = self._draw_icon(name)
        return self._cache[name]

    def _draw_icon(self, name: str) -> QIcon:
        color = QColor(ICON_COLORS[name])
        pixmap = QPixmap(self.size, self.size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if name.startswith("status-"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(6, 6, 12, 12))
        elif name == "refresh":
            painter.drawArc(QRectF(5, 5, 14, 14), 35 * 16, 285 * 16)
            painter.drawLine(16, 4, 20, 4)
            painter.drawLine(20, 4, 20, 8)
        elif name == "dashboard":
            painter.setBrush(QBrush(color))
            painter.drawRect(5, 5, 5, 5)
            painter.drawRect(14, 5, 5, 5)
            painter.drawRect(5, 14, 5, 5)
            painter.drawRect(14, 14, 5, 5)
        elif name == "activity":
            painter.drawLine(4, 14, 8, 14)
            painter.drawLine(8, 14, 11, 8)
            painter.drawLine(11, 8, 15, 18)
            painter.drawLine(15, 18, 18, 11)
            painter.drawLine(18, 11, 21, 11)
        elif name == "add-target":
            painter.drawLine(4, 8, 9, 8)
            painter.drawLine(9, 8, 11, 10)
            painter.drawLine(11, 10, 20, 10)
            painter.drawRect(4, 8, 16, 11)
            painter.drawLine(12, 12, 12, 17)
            painter.drawLine(9, 15, 15, 15)
        elif name == "archive":
            painter.drawRect(5, 8, 14, 11)
            painter.drawRect(4, 5, 16, 4)
            painter.drawLine(9, 12, 15, 12)
        elif name == "edit":
            painter.drawLine(6, 18, 9, 17)
            painter.drawLine(9, 17, 19, 7)
            painter.drawLine(17, 5, 19, 7)
            painter.drawLine(6, 18, 7, 14)
            painter.drawLine(7, 14, 17, 5)
        elif name == "exit":
            painter.drawArc(QRectF(5, 5, 14, 14), 130 * 16, 280 * 16)
            painter.drawLine(12, 3, 12, 12)
        elif name == "folder":
            painter.drawLine(4, 8, 9, 8)
            painter.drawLine(9, 8, 11, 10)
            painter.drawLine(11, 10, 20, 10)
            painter.drawRect(4, 8, 16, 11)
        elif name == "remove-target":
            painter.drawLine(7, 8, 17, 8)
            painter.drawLine(10, 5, 14, 5)
            painter.drawRect(8, 8, 8, 11)
            painter.drawLine(11, 11, 11, 17)
            painter.drawLine(14, 11, 14, 17)
        elif name == "save":
            painter.drawRect(5, 4, 14, 16)
            painter.drawRect(8, 5, 7, 5)
            painter.drawRect(8, 13, 8, 5)
        elif name == "back":
            painter.drawLine(19, 12, 6, 12)
            painter.drawLine(6, 12, 11, 7)
            painter.drawLine(6, 12, 11, 17)
        elif name == "next":
            painter.drawLine(5, 12, 18, 12)
            painter.drawLine(18, 12, 13, 7)
            painter.drawLine(18, 12, 13, 17)
        else:
            painter.drawEllipse(QRectF(5, 5, 14, 14))
            painter.drawLine(12, 7, 12, 13)
            painter.drawLine(12, 13, 16, 16)

        painter.end()
        return QIcon(pixmap)
