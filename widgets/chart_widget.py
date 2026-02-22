from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget


@dataclass
class FillMarker:
    index: int
    price: float
    side: str
    source: str


class ChartWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._candles: List[Dict[str, float]] = []
        self._overlays: Dict[str, List[float]] = {}
        self._subpanels: Dict[str, List[float]] = {}
        self._markers: List[FillMarker] = []

    def set_candles(self, candles: List[Dict[str, float]]) -> None:
        self._candles = candles
        self.update()

    def set_overlay(self, name: str, values: List[float]) -> None:
        self._overlays[name] = values
        self.update()

    def set_subpanel(self, name: str, values: List[float]) -> None:
        self._subpanels[name] = values
        self.update()

    def clear_indicator(self, name: str) -> None:
        self._overlays.pop(name, None)
        self._subpanels.pop(name, None)
        self.update()

    def set_fill_markers(self, markers: List[FillMarker]) -> None:
        self._markers = markers
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#F8FAFF"))
        if not self._candles:
            p.setPen(QColor("#90A0B7"))
            p.drawText(self.rect(), Qt.AlignCenter, "No data")
            return

        w = self.width()
        h = self.height()
        main_h = int(h * 0.65)
        sub_h = h - main_h - 10

        highs = [c["high"] for c in self._candles]
        lows = [c["low"] for c in self._candles]
        y_min, y_max = min(lows), max(highs)
        span = max(1e-9, y_max - y_min)

        n = len(self._candles)
        x_step = max(1.0, w / max(1, n))

        for i, c in enumerate(self._candles):
            x = i * x_step + x_step / 2
            o, hi, lo, cl = c["open"], c["high"], c["low"], c["close"]
            yo = main_h - ((o - y_min) / span) * main_h
            yc = main_h - ((cl - y_min) / span) * main_h
            yhi = main_h - ((hi - y_min) / span) * main_h
            ylo = main_h - ((lo - y_min) / span) * main_h
            up = cl >= o
            color = QColor("#E24A4A") if up else QColor("#3C72E8")
            p.setPen(QPen(color, 1))
            p.drawLine(QPointF(x, yhi), QPointF(x, ylo))
            top = min(yo, yc)
            height = max(1, abs(yc - yo))
            p.fillRect(int(x - x_step * 0.3), int(top), int(x_step * 0.6), int(height), color)

        overlay_colors = [QColor("#7A5AF8"), QColor("#2F7CF6"), QColor("#00A3A3")]
        for idx, (_, vals) in enumerate(self._overlays.items()):
            color = overlay_colors[idx % len(overlay_colors)]
            p.setPen(QPen(color, 1.5))
            prev = None
            for i, v in enumerate(vals[:n]):
                if v is None:
                    prev = None
                    continue
                x = i * x_step + x_step / 2
                y = main_h - ((v - y_min) / span) * main_h
                if prev is not None:
                    p.drawLine(prev, QPointF(x, y))
                prev = QPointF(x, y)

        for m in self._markers:
            if m.index < 0 or m.index >= n:
                continue
            x = m.index * x_step + x_step / 2
            y = main_h - ((m.price - y_min) / span) * main_h
            mark_color = QColor("#D13D48") if m.side.upper() == "BUY" else QColor("#2F6BDE")
            p.setPen(QPen(mark_color, 2))
            if m.source == "LIVE":
                p.drawEllipse(QPointF(x, y), 4, 4)
            else:
                p.drawLine(QPointF(x - 4, y + 4), QPointF(x, y - 4))
                p.drawLine(QPointF(x, y - 4), QPointF(x + 4, y + 4))

        if self._subpanels:
            panel_top = main_h + 10
            p.setPen(QPen(QColor("#DDE4F0"), 1))
            p.drawRect(0, panel_top, w - 1, sub_h - 1)

            total = len(self._subpanels)
            pane_h = max(1, sub_h // total)
            for idx, (_, vals) in enumerate(self._subpanels.items()):
                top = panel_top + idx * pane_h
                section = vals[:n]
                finite = [v for v in section if v is not None]
                if not finite:
                    continue
                mn, mx = min(finite), max(finite)
                sp = max(1e-9, mx - mn)
                p.setPen(QPen(QColor("#7A5AF8"), 1.2))
                prev = None
                for i, v in enumerate(section):
                    if v is None:
                        prev = None
                        continue
                    x = i * x_step + x_step / 2
                    y = top + pane_h - ((v - mn) / sp) * pane_h
                    if prev is not None:
                        p.drawLine(prev, QPointF(x, y))
                    prev = QPointF(x, y)
