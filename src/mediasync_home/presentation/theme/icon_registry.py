from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap


ICON_COLORS = {
    "activity": "#2563eb",
    "dashboard": "#0f766e",
    "history": "#9a6a12",
    "refresh": "#475569",
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
        else:
            painter.drawEllipse(QRectF(5, 5, 14, 14))
            painter.drawLine(12, 7, 12, 13)
            painter.drawLine(12, 13, 16, 16)

        painter.end()
        return QIcon(pixmap)
