from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Deque, Dict, List

from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QLabel


class TapeWidget(QWidget):
    def __init__(self, max_rows: int = 120, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_rows = max_rows
        self._rows: Deque[Dict[str, str]] = deque(maxlen=max_rows)
        self._last_price: float | None = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Recent Trades"))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time", "Price", "Size"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
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
        if self._last_price is None:
            return max(1, volume // 1000 if volume > 0 else 1)
        delta = abs(price - self._last_price)
        base = max(1, volume // 3000 if volume > 0 else 1)
        return max(1, int(base + delta * 10))

    def _render(self) -> None:
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self.table.setItem(i, 0, QTableWidgetItem(row["time"]))
            self.table.setItem(i, 1, QTableWidgetItem(row["price"]))
            self.table.setItem(i, 2, QTableWidgetItem(row["size"]))
