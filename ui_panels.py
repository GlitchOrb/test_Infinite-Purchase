"""
ui_panels.py
============
Side-panel widgets: Position panel, engine status panel, activity log.
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)
from PyQt5.QtCore import Qt

from ui_theme import C, F, make_card, make_header_label, make_kv_row, make_badge


# ======================================================================= #
#  Position Panel
# ======================================================================= #

class PositionPanel(QFrame):
    """Shows SOXL / SOXS positions, unrealized PnL, injection budget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"""
            QFrame {{
                background: {C.BG_CARD};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(make_header_label("Positions"))

        # SOXL block
        layout.addWidget(self._section_label("SOXL", C.GREEN))
        grid_l = QGridLayout()
        grid_l.setSpacing(4)
        self.soxl_qty_k, self.soxl_qty_v = make_kv_row("Shares")
        self.soxl_avg_k, self.soxl_avg_v = make_kv_row("Avg Cost")
        self.soxl_pnl_k, self.soxl_pnl_v = make_kv_row("Unrealized P&L")
        self.soxl_slices_k, self.soxl_slices_v = make_kv_row("Slices Used")
        for i, (k, v) in enumerate([
            (self.soxl_qty_k, self.soxl_qty_v),
            (self.soxl_avg_k, self.soxl_avg_v),
            (self.soxl_pnl_k, self.soxl_pnl_v),
            (self.soxl_slices_k, self.soxl_slices_v),
        ]):
            grid_l.addWidget(k, i, 0)
            grid_l.addWidget(v, i, 1)
        layout.addLayout(grid_l)

        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {C.BORDER}; border: none;")
        layout.addWidget(sep)

        # SOXS block
        layout.addWidget(self._section_label("SOXS", C.RED))
        grid_s = QGridLayout()
        grid_s.setSpacing(4)
        self.soxs_qty_k, self.soxs_qty_v = make_kv_row("Shares")
        self.soxs_avg_k, self.soxs_avg_v = make_kv_row("Avg Cost")
        self.soxs_pnl_k, self.soxs_pnl_v = make_kv_row("Unrealized P&L")
        self.soxs_slices_k, self.soxs_slices_v = make_kv_row("Slices Used")
        for i, (k, v) in enumerate([
            (self.soxs_qty_k, self.soxs_qty_v),
            (self.soxs_avg_k, self.soxs_avg_v),
            (self.soxs_pnl_k, self.soxs_pnl_v),
            (self.soxs_slices_k, self.soxs_slices_v),
        ]):
            grid_s.addWidget(k, i, 0)
            grid_s.addWidget(v, i, 1)
        layout.addLayout(grid_s)

        # Separator
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {C.BORDER}; border: none;")
        layout.addWidget(sep2)

        # Injection budget
        grid_inj = QGridLayout()
        grid_inj.setSpacing(4)
        self.inj_k, self.inj_v = make_kv_row("Injection Budget", "—", C.BLUE)
        grid_inj.addWidget(self.inj_k, 0, 0)
        grid_inj.addWidget(self.inj_v, 0, 1)
        layout.addLayout(grid_inj)

        layout.addStretch()

    def _section_label(self, text: str, color: str) -> QLabel:
        lbl = QLabel(f"  {text}")
        lbl.setFont(F.body_bold())
        lbl.setFixedHeight(24)
        lbl.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background: transparent;
                border: none;
                border-left: 3px solid {color};
                padding-left: 8px;
            }}
        """)
        return lbl

    # ------------------------------------------------------------------ #
    #  Public update
    # ------------------------------------------------------------------ #

    def update_positions(
        self,
        soxl_qty: int, soxl_avg: float, soxl_pnl: float,
        soxl_slices: int,
        soxs_qty: int, soxs_avg: float, soxs_pnl: float,
        soxs_slices: int,
        injection_budget: float,
    ) -> None:
        self.soxl_qty_v.setText(f"{soxl_qty:,}")
        self.soxl_avg_v.setText(f"${soxl_avg:,.2f}" if soxl_avg else "—")
        self._set_pnl(self.soxl_pnl_v, soxl_pnl)
        self.soxl_slices_v.setText(str(soxl_slices))

        self.soxs_qty_v.setText(f"{soxs_qty:,}")
        self.soxs_avg_v.setText(f"${soxs_avg:,.2f}" if soxs_avg else "—")
        self._set_pnl(self.soxs_pnl_v, soxs_pnl)
        self.soxs_slices_v.setText(str(soxs_slices))

        self.inj_v.setText(f"${injection_budget:,.2f}")

    def _set_pnl(self, label: QLabel, pnl: float) -> None:
        color = C.GREEN if pnl >= 0 else C.RED
        sign = "+" if pnl >= 0 else ""
        label.setText(f"{sign}${pnl:,.2f}")
        label.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )


# ======================================================================= #
#  Engine Status Panel
# ======================================================================= #

class EngineStatusPanel(QFrame):
    """FSM state, transition day, SOXS holding days, drawdown flag, reconcile status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"""
            QFrame {{
                background: {C.BG_CARD};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        layout.addWidget(make_header_label("엔진 상태"))

        grid = QGridLayout()
        grid.setSpacing(4)

        self.state_k, self.state_v = make_kv_row("레짐 상태")
        self.score_k, self.score_v = make_kv_row("추세 점수")
        self.trans_k, self.trans_v = make_kv_row("전환 일차")
        self.hold_k, self.hold_v   = make_kv_row("SOXS 보유일")
        self.dd_k, self.dd_v       = make_kv_row("심화 낙폭")
        self.trail_k, self.trail_v = make_kv_row("트레일 단계")
        self.recon_k, self.recon_v = make_kv_row("최근 리컨실")

        for i, (k, v) in enumerate([
            (self.state_k, self.state_v),
            (self.score_k, self.score_v),
            (self.trans_k, self.trans_v),
            (self.hold_k, self.hold_v),
            (self.dd_k, self.dd_v),
            (self.trail_k, self.trail_v),
            (self.recon_k, self.recon_v),
        ]):
            grid.addWidget(k, i, 0)
            grid.addWidget(v, i, 1)

        layout.addLayout(grid)
        layout.addStretch()

    # ------------------------------------------------------------------ #
    #  Public update
    # ------------------------------------------------------------------ #

    def update_status(
        self,
        fsm_state: str = "—",
        score: int = 0,
        transition_day: int = 0,
        soxs_hold_days: int = 0,
        deep_drawdown: bool = False,
        trail_stage: int = 0,
        reconcile_status: str = "—",
    ) -> None:
        # State color
        state_colors = {
            "BULL_ACTIVE": C.GREEN,
            "BEAR_ACTIVE": C.RED,
            "TRANSITION": C.ORANGE,
            "NEUTRAL": C.NAVY_LIGHT,
        }
        color = state_colors.get(fsm_state, C.TEXT)
        display_map = {"BULL_ACTIVE": "상승 모드", "BEAR_ACTIVE": "하락 모드", "TRANSITION": "전환 구간", "NEUTRAL": "관망 모드"}
        display = display_map.get(fsm_state, fsm_state)
        self.state_v.setText(display)
        self.state_v.setStyleSheet(
            f"color: {color}; font-weight: bold; background: transparent; border: none;"
        )

        self.score_v.setText(f"{score} / 3")

        if transition_day > 0:
            self.trans_v.setText(f"{transition_day}일차")
            self.trans_v.setStyleSheet(
                f"color: {C.ORANGE}; font-weight: bold; background: transparent; border: none;"
            )
        else:
            self.trans_v.setText("—")
            self.trans_v.setStyleSheet(
                f"color: {C.TEXT}; background: transparent; border: none;"
            )

        self.hold_v.setText(str(soxs_hold_days) if soxs_hold_days else "—")

        dd_text = "예" if deep_drawdown else "아니오"
        dd_color = C.RED if deep_drawdown else C.GREEN
        self.dd_v.setText(dd_text)
        self.dd_v.setStyleSheet(
            f"color: {dd_color}; font-weight: bold; background: transparent; border: none;"
        )

        self.trail_v.setText(f"{trail_stage}단계" if trail_stage else "—")

        ok = "OK" in reconcile_status.upper() or reconcile_status == "✓"
        rc_color = C.GREEN if ok else C.TEXT
        self.recon_v.setText(reconcile_status)
        self.recon_v.setStyleSheet(
            f"color: {rc_color}; background: transparent; border: none;"
        )


# ======================================================================= #
#  Activity Log
# ======================================================================= #

class ActivityLog(QFrame):
    """Scrollable activity / event log with timestamp."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"""
            QFrame {{
                background: {C.BG_CARD};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        layout.addWidget(make_header_label("Activity Log"))

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(F.mono())
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: #F4F5F9;
                color: {C.NAVY_TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        self._log.setMaximumHeight(180)
        layout.addWidget(self._log)

    def append(self, message: str) -> None:
        """Append a timestamped message to the log."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{ts}]  {message}")
        # Auto-scroll to bottom
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_log(self) -> None:
        self._log.clear()
