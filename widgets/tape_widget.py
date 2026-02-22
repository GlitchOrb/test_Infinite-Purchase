from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Deque, Dict, List

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class TapeWidget(QWidget):
    def __init__(self, max_rows: int = 120, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_rows = max_rows
        self._rows: Deque[Dict[str, str]] = deque(maxlen=max_rows)
        self._last_price: float | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        title = QLabel("Recent Trades")
        title.setObjectName("cardTitle")
        root.addWidget(title)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("tapeTable")
        self.table.setHorizontalHeaderLabels(["Time", "Price", "Size"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        root.addWidget(self.table)

    def add_quote_tick(self, price: float, volume: int) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        size = self._derive_size(price, volume)
        self._rows.appendleft({"time": now, "price": f"{price:.2f}", "size": str(size)})
        self._render()
        self._last_price = price

    def set_rows(self, rows: List[Dict[str, str]]) -> None:
        self._rows.clear()
        for r in rows[: self._max_rows]:
            self._rows.append(r)
        self._render()

    def _derive_size(self, price: float, volume: int) -> int:
        if price <= 0:
            return 0
        return int(max(1, round(volume / 1000)))

    def _render(self) -> None:
        self.table.setRowCount(len(self._rows))
        ref: float | None = self._last_price
        for i, row in enumerate(self._rows):
            t_item = QTableWidgetItem(row.get("time", ""))
            p_item = QTableWidgetItem(row.get("price", ""))
            s_item = QTableWidgetItem(row.get("size", ""))

            t_item.setTextAlignment(Qt.AlignCenter)
            p_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
            s_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)

            try:
                px = float(row.get("price", "0"))
            except ValueError:
                px = 0.0

            if ref is not None:
                if px > ref:
                    p_item.setForeground(QBrush(QColor("#E5484D")))
                elif px < ref:
                    p_item.setForeground(QBrush(QColor("#3178F6")))
                else:
                    p_item.setForeground(QBrush(QColor("#8B95A1")))

            self.table.setItem(i, 0, t_item)
            self.table.setItem(i, 1, p_item)
            self.table.setItem(i, 2, s_item)
