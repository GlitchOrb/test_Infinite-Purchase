"""
app.py
======
Alpha Predator v4.0 — Desktop trading application.

Launch:  ``python app.py``

Architecture
------------
1. LoginPage:  Kiwoom CommConnect → OnEventConnect callback
2. Dashboard:  Chart + Position + Engine panels, QTimer-driven refresh
3. Runtime bridge:  signals/slots connect Kiwoom events → UI updates

Thread safety
-------------
Kiwoom COM events fire on the Qt main thread (QAxWidget), so all
slot connections in this file are inherently thread-safe.  The
Telegram kill-switch runs on a daemon thread and uses
``QMetaObject.invokeMethod`` (via ``_emit_kill``) to cross into
the Qt thread safely.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QSizePolicy, QSpacerItem, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QMetaObject, Q_ARG
from PyQt5.QtGui import QIcon, QPixmap

from config import RuntimeConfig
from db import (
    get_all_positions, get_open_orders, get_position, get_system,
    init_db, insert_fill, insert_order, is_action_done,
    is_emergency_stop, mark_action_done, open_db, rollback_action,
    set_emergency_stop, set_system, try_lock_action, update_order,
    upsert_position, get_latest_regime,
)
from strategy_engine import DailyDecision, EffectiveState, EngineIntent, StrategyEngine
from trade_manager import (
    OrderIntent, OrderSide, PositionInfo, TradeManager, TradeManagerState,
)
from ui_chart import PriceChart, TradeMarker
from ui_panels import ActivityLog, EngineStatusPanel, PositionPanel
from ui_theme import (
    C, F, GLOBAL_STYLE, make_badge, make_emergency_overlay,
    make_kill_button, make_primary_button, make_secondary_button,
    regime_badge,
)
from auth_manager import AuthManager

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


def _time_header_text() -> str:
    """Return KR/US realtime clock text with DST-aware New York timezone."""
    kr_now = datetime.now()
    us_now = datetime.now(ZoneInfo("America/New_York"))
    return (
        f"🇰🇷 현지시간: {kr_now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🇺🇸 미국시간: {us_now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )


def _us_time_tag() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z")


# ======================================================================= #
#  State helpers (same as runtime.py, shared)
# ======================================================================= #

def _load_tm_state(conn: sqlite3.Connection) -> TradeManagerState:
    st = TradeManagerState()
    for sym, attr in [("SOXL", "soxl"), ("SOXS", "soxs")]:
        row = get_position(conn, sym)
        if row and row["qty"] > 0:
            pos = PositionInfo(
                qty=row["qty"],
                avg_cost=row["avg_cost"],
                entry_date=pd.Timestamp(row["entry_date"]) if row.get("entry_date") else None,
                allocated_capital=row.get("allocated_capital", 0.0),
            )
            setattr(st, attr, pos)
    soxl_row = get_position(conn, "SOXL") or {}
    st.soxl_max_price = soxl_row.get("max_price_since_entry", 0.0)
    st.soxl_trailing_stage = soxl_row.get("trailing_stage", 0)
    st.soxl_slices_used = soxl_row.get("slices_used", 0)
    soxs_row = get_position(conn, "SOXS") or {}
    st.soxs_holding_days = soxs_row.get("holding_days", 0)
    st.soxs_loss_cut_stage = soxs_row.get("loss_cut_stage", 0)
    st.soxs_slices_used = soxs_row.get("slices_used", 0)
    budget_str = get_system(conn, "injection_budget")
    st.injection_budget = float(budget_str) if budget_str else 0.0
    return st


# ======================================================================= #
#  Login Page
# ======================================================================= #

class LoginPage(QWidget):
    """Initial login screen — REST App Key/App Secret login."""

    login_success = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setFixedSize(460, 560)
        card.setStyleSheet(f"""
            QWidget {{
                background: {C.BG_CARD};
                border: 1px solid {C.BORDER};
                border-radius: 16px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(34, 26, 34, 24)
        card_layout.setSpacing(10)

        icon_label = QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(pixmap)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("border: none;")
        card_layout.addWidget(icon_label)

        title = QLabel("Alpha Predator")
        title.setFont(F.title())
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {C.NAVY}; border: none;")
        card_layout.addWidget(title)

        subtitle = QLabel("v4.0 — Kiwoom REST Live / Guest Mode")
        subtitle.setFont(F.small())
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(f"color: {C.TEXT_SUB}; border: none;")
        card_layout.addWidget(subtitle)

        self._status = QLabel("Ready for REST login")
        self._status.setFont(F.body())
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(f"color: {C.TEXT_SUB}; border: none;")
        card_layout.addWidget(self._status)

        self._app_key = QLineEdit()
        self._app_key.setPlaceholderText("App Key")
        card_layout.addWidget(self._app_key)

        self._app_secret = QLineEdit()
        self._app_secret.setPlaceholderText("App Secret")
        self._app_secret.setEchoMode(QLineEdit.Password)
        card_layout.addWidget(self._app_secret)

        self._account_no = QLineEdit()
        self._account_no.setPlaceholderText("Account number")
        card_layout.addWidget(self._account_no)

        remember_row = QHBoxLayout()
        self._remember = QCheckBox("Remember login")
        remember_row.addWidget(self._remember)
        self._help_btn = QToolButton()
        self._help_btn.setText("?")
        self._help_btn.clicked.connect(self._show_help)
        remember_row.addStretch()
        remember_row.addWidget(self._help_btn)
        card_layout.addLayout(remember_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        card_layout.addWidget(self._progress)

        self._btn = make_primary_button("Connect (REST)")
        self._btn.setFixedHeight(42)
        self._btn.clicked.connect(self._on_login_clicked)
        card_layout.addWidget(self._btn)

        self._demo_btn = make_secondary_button("Demo Mode (no broker)")
        self._demo_btn.clicked.connect(self._on_demo_clicked)
        card_layout.addWidget(self._demo_btn)

        card_layout.addStretch()
        outer.addWidget(card)

        footer = QLabel('github.com/GlitchOrb/alpha-predator-leveraged-switching')
        footer.setFont(F.small())
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet(f"color: {C.TEXT_MUTED}; margin-top: 8px;")
        outer.addWidget(footer)

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "REST OpenAPI Help",
            "1) Create Telegram Bot token via @BotFather (optional kill alerts).\n"
            "2) Issue Kiwoom OpenAPI REST App Key/App Secret in Kiwoom developer portal.\n"
            "3) Enter App Key, App Secret, and account number for live mode.\n"
            "4) Use Demo Mode to run guest mode with no broker calls.",
        )

    def _on_login_clicked(self) -> None:
        app_key = self._app_key.text().strip()
        app_secret = self._app_secret.text().strip()
        account_no = self._account_no.text().strip()
        if not app_key or not app_secret or not account_no:
            self.set_status("App Key / Secret / Account are required", C.RED)
            return

        self._status.setText("Connecting (REST) …")
        self._status.setStyleSheet(f"color: {C.ORANGE}; border: none;")
        self._progress.show()
        self._btn.setEnabled(False)
        self.parent().parent()._begin_login(app_key, app_secret, account_no, self._remember.isChecked())  # type: ignore

    def _on_demo_clicked(self) -> None:
        self._status.setText("Demo mode — no broker")
        self._status.setStyleSheet(f"color: {C.GREEN}; border: none;")
        self.parent().parent()._begin_guest_mode()  # type: ignore
        self.login_success.emit()

    def set_status(self, text: str, color: str = C.TEXT_SUB) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}; border: none;")
        self._progress.hide()
        self._btn.setEnabled(True)

    def set_remember_supported(self, enabled: bool) -> None:
        self._remember.setEnabled(enabled)
        if not enabled:
            self._remember.setChecked(False)
            self._remember.setToolTip("Remember login is supported on Windows only")

    def prefill_credentials(self, app_key: str, app_secret: str, account_no: str) -> None:
        self._app_key.setText(app_key)
        self._app_secret.setText(app_secret)
        self._account_no.setText(account_no)
        self._remember.setChecked(True)


# ======================================================================= #
#  Dashboard Page
# ======================================================================= #

class DashboardPage(QWidget):
    """Main dashboard: header + chart + side panels + activity log."""

    kill_toggled = pyqtSignal(bool)

    def __init__(self, conn: sqlite3.Connection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conn = conn
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(12)

        # ===== HEADER ===== #
        self._header_layout = QHBoxLayout()
        header = self._header_layout
        header.setSpacing(14)

        title = QLabel("Alpha Predator")
        title.setFont(F.title())
        title.setStyleSheet(f"color: {C.NAVY};")
        header.addWidget(title)

        ver = QLabel("v4.0")
        ver.setFont(F.small())
        ver.setStyleSheet(f"color: {C.TEXT_MUTED};")
        header.addWidget(ver)

        header.addSpacing(16)

        # Regime badge (placeholder — updated later)
        self._regime_badge = regime_badge("NEUTRAL")
        header.addWidget(self._regime_badge)

        # Score badge
        self._score_badge = make_badge("0 / 3", C.NAVY_LIGHT)
        header.addWidget(self._score_badge)

        header.addStretch()

        self._time_label = QLabel(_time_header_text())
        self._time_label.setFont(F.small())
        self._time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._time_label.setStyleSheet(f"color: {C.TEXT_SUB};")
        header.addWidget(self._time_label)

        # Refresh button
        self._refresh_btn = make_secondary_button("↻ Refresh")
        self._refresh_btn.clicked.connect(self.refresh_all)
        header.addWidget(self._refresh_btn)

        # Kill switch
        self._kill_btn = make_kill_button()
        self._kill_btn.toggled.connect(self._on_kill_toggle)
        header.addWidget(self._kill_btn)

        root.addLayout(header)

        # Emergency overlay (hidden by default)
        self._emergency = make_emergency_overlay(self)
        root.addWidget(self._emergency)

        # ===== BODY ===== #
        body = QHBoxLayout()
        body.setSpacing(12)

        # Left: Chart + Activity log
        left = QVBoxLayout()
        left.setSpacing(10)

        self.chart = PriceChart()
        self.chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left.addWidget(self.chart, stretch=4)

        self.activity = ActivityLog()
        self.activity.setMaximumHeight(190)
        left.addWidget(self.activity, stretch=1)

        body.addLayout(left, stretch=3)

        # Right: Position + Engine status
        right = QVBoxLayout()
        right.setSpacing(10)

        self.position_panel = PositionPanel()
        self.position_panel.setMinimumWidth(280)
        self.position_panel.setMaximumWidth(340)
        right.addWidget(self.position_panel, stretch=3)

        self.engine_panel = EngineStatusPanel()
        self.engine_panel.setMinimumWidth(280)
        self.engine_panel.setMaximumWidth(340)
        right.addWidget(self.engine_panel, stretch=2)

        body.addLayout(right, stretch=1)

        root.addLayout(body)

        # Footer
        footer = QLabel('github.com/GlitchOrb/alpha-predator-leveraged-switching')
        footer.setFont(F.small())
        footer.setAlignment(Qt.AlignRight)
        footer.setStyleSheet(f"color: {C.TEXT_MUTED}; padding-right: 4px;")
        footer.setFixedHeight(20)
        root.addWidget(footer)

    # ------------------------------------------------------------------ #
    #  Kill switch handler
    # ------------------------------------------------------------------ #

    def _on_kill_toggle(self, checked: bool) -> None:
        if checked:
            self._emergency.show()
            self.activity.append("🔴 KILL SWITCH ACTIVATED")
        else:
            self._emergency.hide()
            self.activity.append("🟢 Kill switch released")
        self.kill_toggled.emit(checked)

    def set_kill_state(self, active: bool) -> None:
        """Programmatically set kill switch (e.g., from Telegram)."""
        self._kill_btn.setChecked(active)

    # ------------------------------------------------------------------ #
    #  Regime / score header update
    # ------------------------------------------------------------------ #

    def update_regime(self, state: str, score: int) -> None:
        """Replace regime badge in-place and update score badge text."""
        if not self._header_layout or not self._regime_badge:
            return

        old = self._regime_badge
        idx = self._header_layout.indexOf(old)
        if idx < 0:
            return

        new = regime_badge(state)
        self._header_layout.removeWidget(old)
        old.deleteLater()
        self._header_layout.insertWidget(idx, new)
        self._regime_badge = new

        self._score_badge.setText(f"{score} / 3")

    # ------------------------------------------------------------------ #
    #  Full refresh from DB
    # ------------------------------------------------------------------ #

    def refresh_all(self) -> None:
        """Pull latest data from SQLite and update all panels."""
        self.activity.append("Refreshing …")
        try:
            self._refresh_positions()
            self._refresh_engine()
            self._refresh_chart()
            self.activity.append("Refresh complete ✓")
        except Exception as e:
            self.activity.append(f"Refresh error: {e}")
            log.exception("Dashboard refresh failed")

    def update_time_labels(self) -> None:
        """Update top header KR/US clocks."""
        self._time_label.setText(_time_header_text())

    def _refresh_positions(self) -> None:
        tm = _load_tm_state(self.conn)
        # Compute unrealized PnL (would need live price — use 0 for now)
        soxl_pnl = 0.0
        soxs_pnl = 0.0
        self.position_panel.update_positions(
            soxl_qty=tm.soxl.qty,
            soxl_avg=tm.soxl.avg_cost,
            soxl_pnl=soxl_pnl,
            soxl_slices=tm.soxl_slices_used,
            soxs_qty=tm.soxs.qty,
            soxs_avg=tm.soxs.avg_cost,
            soxs_pnl=soxs_pnl,
            soxs_slices=tm.soxs_slices_used,
            injection_budget=tm.injection_budget,
        )

    def _refresh_engine(self) -> None:
        regime = get_latest_regime(self.conn)
        tm = _load_tm_state(self.conn)

        state = regime.get("effective_state", "NEUTRAL") if regime else "NEUTRAL"
        score = regime.get("score", 0) if regime else 0
        trans_day = regime.get("transition_day", 0) if regime else 0

        # Deep drawdown check
        deep_dd = False
        if tm.soxl.qty > 0 and tm.soxl.avg_cost > 0 and tm.soxl_max_price > 0:
            dd = (tm.soxl_max_price - tm.soxl.avg_cost) / tm.soxl.avg_cost
            deep_dd = dd <= -0.40

        self.update_regime(state, score)

        self.engine_panel.update_status(
            fsm_state=state,
            score=score,
            transition_day=trans_day,
            soxs_hold_days=tm.soxs_holding_days,
            deep_drawdown=deep_dd,
            trail_stage=tm.soxl_trailing_stage,
            reconcile_status="OK ✓",
        )

    def _refresh_chart(self) -> None:
        """Load regime_history from DB and plot."""
        rows = self.conn.execute(
            "SELECT date, close, sma20, sma50, sma200 "
            "FROM regime_history ORDER BY date"
        ).fetchall()

        if not rows:
            # Demo data for visual testing
            self._load_demo_chart()
            return

        dates = [r[0] for r in rows]
        close = np.array([r[1] or 0 for r in rows], dtype=float)
        sma20 = np.array([r[2] if r[2] else np.nan for r in rows], dtype=float)
        sma50 = np.array([r[3] if r[3] else np.nan for r in rows], dtype=float)
        sma200 = np.array([r[4] if r[4] else np.nan for r in rows], dtype=float)

        self.chart.set_data(close, sma20, sma50, sma200, dates=dates)

    def _load_demo_chart(self) -> None:
        """Generate synthetic data for demo mode visualization."""
        np.random.seed(42)
        n = 250
        # Random walk price
        returns = np.random.normal(0.0005, 0.015, n)
        price = 200.0 * np.cumprod(1 + returns)

        sma20 = pd.Series(price).rolling(20).mean().values
        sma50 = pd.Series(price).rolling(50).mean().values
        sma200 = pd.Series(price).rolling(200).mean().values

        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n).strftime("%Y-%m-%d").tolist()

        self.chart.set_data(price, sma20, sma50, sma200, dates=dates)

        # Demo trade markers
        markers = [
            TradeMarker(x=60, price=price[60], symbol="SOXL", side="BUY",
                        reason="Bull regime day 1"),
            TradeMarker(x=80, price=price[80], symbol="SOXL", side="BUY",
                        reason="Avg down -8%"),
            TradeMarker(x=120, price=price[120], symbol="SOXL", side="SELL",
                        reason="📉 트레일링 스탑 조건 충족 — 부분 청산 실행"),
            TradeMarker(x=150, price=price[150], symbol="SOXS", side="BUY",
                        reason="Bear regime entry"),
            TradeMarker(x=170, price=price[170], symbol="SOXS", side="SELL",
                        reason="SOXS TP +8%"),
            TradeMarker(x=200, price=price[200], symbol="SOXL", side="BUY",
                        reason="Transition day 2"),
        ]
        self.chart.set_markers(markers)

        self.activity.append("Demo chart loaded (synthetic data)")


# ======================================================================= #
#  Main Window
# ======================================================================= #

class MainWindow(QMainWindow):
    """Top-level window — manages login/dashboard page stack.

    How login works
    ---------------
    1. User clicks "Connect" on LoginPage
    2. ``_begin_login()`` attempts to import KiwoomAdapter and call login()
    3. KiwoomAdapter.login() blocks via QEventLoop until OnEventConnect
    4. On success → switch to DashboardPage
    5. On failure → show error on LoginPage

    How state updates propagate to UI
    ----------------------------------
    1. Kiwoom chejan callback → ``_on_chejan()`` → updates DB → calls
       ``dashboard.refresh_all()``
    2. QTimer ticks every 5s → ``_periodic_refresh()`` → lightweight
       panel updates without full chart redraw
    3. Manual "↻ Refresh" button → ``dashboard.refresh_all()``
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Alpha Predator v4.0")
        self.setMinimumSize(1280, 820)
        self.resize(1440, 900)

        # Window icon
        icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Config + DB
        self.cfg = RuntimeConfig(
            kiwoom_account=os.environ.get("KIWOOM_ACCOUNT", ""),
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )
        self.conn = open_db(self.cfg.db_path)
        init_db(self.conn)

        # Strategy + trade manager (deterministic, no AI)
        self.strategy = StrategyEngine(signal_ticker=self.cfg.signal_ticker)
        self.trade_mgr = TradeManager()

        # Broker client (None in guest mode)
        self.kiwoom = None
        self.auth = AuthManager()
        self._guest_mode = False

        # Page stack: 0=login, 1=dashboard
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._login_page = LoginPage()
        self._login_page.set_remember_supported(self.auth.remember_supported())
        saved = self.auth.try_restore_saved_login()
        if saved:
            self._login_page.prefill_credentials(*saved)
        self._login_page.login_success.connect(self._on_login_success)
        self._stack.addWidget(self._login_page)

        self._dashboard = DashboardPage(self.conn)
        self._dashboard.kill_toggled.connect(self._on_kill_toggle)
        self._stack.addWidget(self._dashboard)

        # Start on login page
        self._stack.setCurrentIndex(0)

        # Periodic refresh timer (runs only after login)
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._periodic_refresh)

    # ------------------------------------------------------------------ #
    #  Login flow
    # ------------------------------------------------------------------ #

    def _begin_login(self, app_key: str, app_secret: str, account_no: str, remember_login: bool) -> None:
        """Called by LoginPage when user clicks REST connect."""
        try:
            self.kiwoom = self.auth.start_live_mode(
                app_key=app_key,
                app_secret=app_secret,
                account_no=account_no,
                remember_login=remember_login,
            )
            self._guest_mode = False
            object.__setattr__(self.cfg, "kiwoom_account", account_no)
            self._login_page.set_status("Connected (REST) ✓", C.GREEN)
            self._login_page.login_success.emit()
        except Exception as e:
            log.exception("REST login error")
            self._login_page.set_status(f"Error: {e}", C.RED)
            self.kiwoom = None
            self._guest_mode = False

    def _begin_guest_mode(self) -> None:
        self.auth.start_guest_mode()
        self.kiwoom = None
        self._guest_mode = True

    @pyqtSlot()
    def _on_login_success(self) -> None:
        """Switch to dashboard and start periodic refresh."""
        self._stack.setCurrentIndex(1)
        self._dashboard.refresh_all()
        self._dashboard.update_time_labels()
        self._refresh_timer.start(5000)  # 5-second refresh cycle
        self._dashboard.activity.append("System online")

        # Check if emergency stop was persisted
        if is_emergency_stop(self.conn):
            self._dashboard.set_kill_state(True)
            self._dashboard.activity.append("⚠ Previous emergency stop detected")

    # ------------------------------------------------------------------ #
    #  Kill switch
    # ------------------------------------------------------------------ #

    @pyqtSlot(bool)
    def _on_kill_toggle(self, active: bool) -> None:
        set_emergency_stop(self.conn, active)
        if active:
            log.critical("KILL SWITCH activated via UI")
            self._cancel_all_open_orders()
        else:
            log.info("Kill switch released via UI")

    def logout_live_mode(self) -> None:
        """Clear secure credentials/session and disable live trading."""
        self.auth.logout()
        self.kiwoom = None
        self._guest_mode = True
        set_emergency_stop(self.conn, True)

    def _cancel_all_open_orders(self) -> None:
        open_orders = get_open_orders(self.conn)
        for order in open_orders:
            bid = order.get("broker_order_id")
            if bid and self.kiwoom and not self._guest_mode:
                self.kiwoom.cancel_order({"order_id": bid, "symbol": order["symbol"], "qty": order["qty"]})
            update_order(self.conn, order["id"], status="CANCELLED")
        self.conn.commit()
        self._dashboard.activity.append(
            f"Cancelled {len(open_orders)} open orders"
        )

    # ------------------------------------------------------------------ #
    #  Chejan callback (fills) — runs on Qt main thread
    # ------------------------------------------------------------------ #

    def _on_chejan(self, data) -> None:
        """Process Kiwoom fill event and update UI.

        How trade markers are plotted
        -----------------------------
        On each fill event, we record the fill in SQLite, update the
        position via TradeManager.apply_fill(), persist state, then
        trigger a dashboard refresh which redraws the chart with all
        recorded trades as markers.
        """
        if data.gubun == "0" and data.status in ("체결", "전량체결"):
            insert_fill(self.conn, data.order_id, data.qty, data.price)
            side = OrderSide.BUY if "매수" in data.side else OrderSide.SELL

            # Pre-fill snapshot for realized PnL
            pre_pos = get_position(self.conn, data.symbol) or {
                "qty": 0, "avg_cost": 0.0
            }
            pre_avg = pre_pos.get("avg_cost", 0.0)
            pre_qty = pre_pos.get("qty", 0)

            tm_state = _load_tm_state(self.conn)
            tm_state = self.trade_mgr.apply_fill(
                data.symbol, side, data.qty, data.price,
                pd.Timestamp.now(), tm_state,
            )

            # Persist
            for sym, pos, extras in [
                ("SOXL", tm_state.soxl, {
                    "max_price_since_entry": tm_state.soxl_max_price,
                    "trailing_stage": tm_state.soxl_trailing_stage,
                    "slices_used": tm_state.soxl_slices_used,
                }),
                ("SOXS", tm_state.soxs, {
                    "holding_days": tm_state.soxs_holding_days,
                    "loss_cut_stage": tm_state.soxs_loss_cut_stage,
                    "slices_used": tm_state.soxs_slices_used,
                }),
            ]:
                upsert_position(
                    self.conn, sym,
                    qty=pos.qty, avg_cost=pos.avg_cost,
                    entry_date=str(pos.entry_date) if pos.entry_date else None,
                    allocated_capital=pos.allocated_capital, **extras,
                )
            set_system(self.conn, "injection_budget", str(tm_state.injection_budget))
            self.conn.commit()

            # Log to UI
            side_str = "BUY" if side == OrderSide.BUY else "SELL"
            self._dashboard.activity.append(
                f"Fill: {data.symbol} {side_str} {data.qty} @ ${data.price:.2f} (US { _us_time_tag() })"
            )
            log.info("체결 이벤트 [%s]: %s %s %d @ %.2f", _us_time_tag(), data.symbol, side_str, data.qty, data.price)

            # Vampire rebalance for SOXS sells
            if data.symbol == "SOXS" and side == OrderSide.SELL:
                realized = (data.price - pre_avg) * data.qty
                if realized > 0:
                    regime = get_latest_regime(self.conn)
                    if regime and regime.get("effective_state") == "BEAR_ACTIVE":
                        tm_state = _load_tm_state(self.conn)
                        tm_state = self.trade_mgr.on_realized_pnl(
                            "SOXS", realized,
                            EffectiveState.BEAR_ACTIVE, 0.0, tm_state,
                        )
                        set_system(self.conn, "injection_budget",
                                   str(tm_state.injection_budget))
                        self.conn.commit()
                        self._dashboard.activity.append(
                            f"🩸 수익 재투입 실행 — SOXL 평단가 하향 조정 (+${realized:.2f}) (US { _us_time_tag() })"
                        )

            # Refresh UI
            self._dashboard.refresh_all()

    # ------------------------------------------------------------------ #
    #  Periodic refresh
    # ------------------------------------------------------------------ #

    def _periodic_refresh(self) -> None:
        """Light refresh every 5s — updates position + engine panels."""
        try:
            self._dashboard.update_time_labels()
            self._dashboard._refresh_positions()
            self._dashboard._refresh_engine()
        except Exception:
            pass  # silent — don't crash the timer


# ======================================================================= #
#  Entry point
# ======================================================================= #

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_STYLE)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
