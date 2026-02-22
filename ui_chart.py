"""
ui_chart.py
===========
Interactive price + SMA chart with trade markers, built on pyqtgraph.

Features
--------
- SOXX price line (thick, navy)
- SMA 20 / 50 / 200 lines (green, orange, red)
- BUY markers  (green upward triangle for SOXL, green circle for SOXS)
- SELL markers (red downward triangle for SOXL, red circle for SOXS)
- Tooltips via ScatterPlotItem with hover events
- Trailing max price line (dashed)
- Crosshair with date/value labels
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt5.QtCore import Qt, pyqtSignal, QPointF
from PyQt5.QtGui import QColor, QPen, QFont

try:
    import pyqtgraph as pg
except ImportError:
    pg = None  # type: ignore

from ui_theme import C, F, make_card, make_header_label


# ======================================================================= #
#  Trade marker data
# ======================================================================= #

@dataclass
class TradeMarker:
    """Single trade event to plot on chart."""
    x: float              # index position (epoch or integer)
    price: float
    symbol: str           # "SOXL" or "SOXS"
    side: str             # "BUY" or "SELL"
    reason: str = ""
    qty: int = 0


# ======================================================================= #
#  Chart widget
# ======================================================================= #

class PriceChart(QFrame):
    """Interactive pyqtgraph chart showing price, SMAs, and trade markers.

    Signals
    -------
    marker_hovered(str)
        Emitted when user hovers a trade marker; payload is tooltip text.
    """

    marker_hovered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._markers: List[TradeMarker] = []

    # ------------------------------------------------------------------ #
    #  Build
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.setStyleSheet(f"""
            QFrame {{
                background: {C.BG_CARD};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        # Header row
        header_row = QHBoxLayout()
        header_row.addWidget(make_header_label("Price & Regime Chart"))
        header_row.addStretch()

        self._tooltip_label = QLabel("")
        self._tooltip_label.setFont(F.small())
        self._tooltip_label.setStyleSheet(
            f"color: {C.TEXT_SUB}; background: transparent; border: none;"
        )
        header_row.addWidget(self._tooltip_label)
        layout.addLayout(header_row)

        if pg is None:
            fallback = QLabel("pyqtgraph not installed — chart unavailable")
            fallback.setAlignment(Qt.AlignCenter)
            fallback.setStyleSheet(f"color: {C.TEXT_MUTED}; border: none;")
            layout.addWidget(fallback)
            self._pw = None
            return

        # Configure pyqtgraph defaults
        pg.setConfigOptions(
            antialias=True,
            background=QColor(C.BG_CARD),
            foreground=QColor(C.NAVY_TEXT),
        )

        self._pw = pg.PlotWidget()
        self._pw.setStyleSheet("border: none; background: transparent;")
        self._pw.showGrid(x=True, y=True, alpha=0.08)
        self._pw.setLabel("left", "Price", color=C.NAVY_TEXT)
        self._pw.setLabel("bottom", "Trading Day", color=C.NAVY_TEXT)
        self._pw.getAxis("left").setWidth(60)

        # Legend
        self._pw.addLegend(offset=(10, 10))
        self._pw.getPlotItem().legend.setLabelTextColor(C.NAVY_TEXT)

        layout.addWidget(self._pw, stretch=1)

        # Crosshair
        self._vline = pg.InfiniteLine(angle=90, pen=pg.mkPen(C.TEXT_MUTED, width=1, style=Qt.DashLine))
        self._hline = pg.InfiniteLine(angle=0, pen=pg.mkPen(C.TEXT_MUTED, width=1, style=Qt.DashLine))
        self._pw.addItem(self._vline, ignoreBounds=True)
        self._pw.addItem(self._hline, ignoreBounds=True)

        proxy = pg.SignalProxy(self._pw.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse_move)
        self._mouse_proxy = proxy  # prevent garbage collection

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def set_data(
        self,
        close: np.ndarray,
        sma20: np.ndarray | None = None,
        sma50: np.ndarray | None = None,
        sma200: np.ndarray | None = None,
        trailing_max: np.ndarray | None = None,
        dates: list | None = None,
    ) -> None:
        """Plot price and SMA lines.  ``close`` is the primary series."""
        if self._pw is None:
            return
        self._pw.clear()
        # Re-add crosshair
        self._pw.addItem(self._vline, ignoreBounds=True)
        self._pw.addItem(self._hline, ignoreBounds=True)

        x = np.arange(len(close))

        # Price line
        self._pw.plot(x, close, pen=pg.mkPen(C.NAVY, width=2.2),
                      name="Price")

        # SMAs
        if sma20 is not None:
            valid = ~np.isnan(sma20)
            self._pw.plot(x[valid], sma20[valid],
                          pen=pg.mkPen(C.GREEN, width=1.4, style=Qt.DashLine),
                          name="SMA 20")
        if sma50 is not None:
            valid = ~np.isnan(sma50)
            self._pw.plot(x[valid], sma50[valid],
                          pen=pg.mkPen(C.ORANGE, width=1.4, style=Qt.DashLine),
                          name="SMA 50")
        if sma200 is not None:
            valid = ~np.isnan(sma200)
            self._pw.plot(x[valid], sma200[valid],
                          pen=pg.mkPen(C.RED, width=1.4, style=Qt.DashLine),
                          name="SMA 200")

        # Trailing max
        if trailing_max is not None:
            valid = ~np.isnan(trailing_max)
            self._pw.plot(x[valid], trailing_max[valid],
                          pen=pg.mkPen(C.BLUE, width=1, style=Qt.DotLine),
                          name="Trail Max")

        self._close = close
        self._dates = dates

    def set_markers(self, markers: List[TradeMarker]) -> None:
        """Plot trade markers (buy/sell arrows)."""
        if self._pw is None:
            return
        self._markers = markers

        # Group into 4 categories: SOXL BUY, SOXL SELL, SOXS BUY, SOXS SELL
        groups = {
            ("SOXL", "BUY"):  {"symbol": "t1", "brush": C.GREEN, "size": 14},
            ("SOXL", "SELL"): {"symbol": "t",  "brush": C.RED,   "size": 14},
            ("SOXS", "BUY"):  {"symbol": "o",  "brush": C.GREEN_DARK, "size": 10},
            ("SOXS", "SELL"): {"symbol": "o",  "brush": C.RED_DARK,   "size": 10},
        }

        for (sym, side), style in groups.items():
            pts = [m for m in markers if m.symbol == sym and m.side == side]
            if not pts:
                continue
            xs = [m.x for m in pts]
            ys = [m.price for m in pts]
            tips = [f"{m.symbol} {m.side}\n${m.price:.2f}\n{m.reason}" for m in pts]

            scatter = pg.ScatterPlotItem(
                x=xs, y=ys,
                symbol=style["symbol"],
                size=style["size"],
                brush=pg.mkBrush(style["brush"]),
                pen=pg.mkPen("w", width=1),
                data=tips,
                hoverable=True,
                tip=None,
            )
            scatter.sigHovered.connect(self._on_marker_hover)
            self._pw.addItem(scatter)

    # ------------------------------------------------------------------ #
    #  Interaction
    # ------------------------------------------------------------------ #

    def _on_mouse_move(self, evt) -> None:
        pos = evt[0]
        if self._pw is None:
            return
        vb = self._pw.plotItem.vb
        mouse_point = vb.mapSceneToView(pos)
        self._vline.setPos(mouse_point.x())
        self._hline.setPos(mouse_point.y())

        idx = int(round(mouse_point.x()))
        if hasattr(self, "_close") and 0 <= idx < len(self._close):
            date_str = ""
            if self._dates and idx < len(self._dates):
                date_str = f"{self._dates[idx]}  |  "
            self._tooltip_label.setText(
                f"{date_str}Price: ${self._close[idx]:.2f}"
            )

    def _on_marker_hover(self, _plot, points, _ev) -> None:
        if len(points) > 0:
            tip = points[0].data()
            if tip:
                self.marker_hovered.emit(str(tip))
                self._tooltip_label.setText(str(tip).replace("\n", "  |  "))
