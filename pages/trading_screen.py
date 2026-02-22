from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto.auto_trading_controller import AutoTradingController
from broker.base import BrokerBase, Quote
from broker.kiwoom_rest_broker import KiwoomRestBroker
from broker.paper_broker import PaperBroker
from conditions.condition_engine import ConditionEngine
from db import is_emergency_stop, set_emergency_stop, set_system
from indicators.obv import OBVIndicator
from indicators.rsi import RSIIndicator
from indicators.sma import SMAIndicator
from widgets.chart_widget import ChartWidget, FillMarker
from widgets.order_panel import OrderPanel
from widgets.tape_widget import TapeWidget


class _WorkerSignals(QObject):
    success = pyqtSignal(str, object)
    error = pyqtSignal(str, str)


class _Worker(QRunnable):
    def __init__(self, key: str, fn: Callable[[], object], signals: _WorkerSignals) -> None:
        super().__init__()
        self.key = key
        self.fn = fn
        self.signals = signals

    def run(self) -> None:
        try:
            self.signals.success.emit(self.key, self.fn())
        except Exception as exc:
            self.signals.error.emit(self.key, str(exc))


class TradingScreen(QWidget):
    MODE_GUEST = "Guest"
    MODE_PAPER = "Paper"
    MODE_LIVE = "Live"

    def __init__(
        self,
        conn: sqlite3.Connection,
        auth_manager,
        cfg,
        telegram_alert: Optional[Callable[[str], None]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.conn = conn
        self.auth = auth_manager
        self.cfg = cfg
        self.alert = telegram_alert
        self.symbol = "SOXL"
        self.mode = self.MODE_GUEST

        self.thread_pool = QThreadPool.globalInstance()
        self.worker_signals = _WorkerSignals()
        self.worker_signals.success.connect(self._on_worker_success)
        self.worker_signals.error.connect(self._on_worker_error)

        self.live_broker: Optional[BrokerBase] = None
        self.paper_broker = PaperBroker(conn)

        self.indicators = {
            "SMA50": SMAIndicator(50),
            "SMA200": SMAIndicator(200),
            "RSI14": RSIIndicator(14),
            "OBV": OBVIndicator(),
        }

        self.condition_engine = ConditionEngine(
            conn=conn,
            get_emergency_stop=lambda: is_emergency_stop(self.conn),
            set_emergency_stop=lambda x: set_emergency_stop(self.conn, x),
            alert=self.alert,
        )
        self.auto_ctl = AutoTradingController(
            conn=conn,
            cfg=cfg,
            broker_provider=self._resolve_exec_broker_or_none,
            get_symbol_prices=self._get_exec_prices,
            alert=self.alert,
        )

        self._build_ui()
        self._init_db()
        self._restore_settings()
        self._setup_timers()

        self.auto_ctl.status_changed.connect(self._on_auto_status)
        self.auto_ctl.event_log.connect(lambda t: self.order_panel.set_status(t))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        self.symbol_box = QComboBox()
        self.symbol_box.addItems(["SOXL", "SOXS", "SOXX"])
        self.symbol_box.currentTextChanged.connect(self._on_symbol_changed)
        self.mode_box = QComboBox()
        self.mode_box.addItems([self.MODE_GUEST, self.MODE_PAPER, self.MODE_LIVE])
        self.mode_box.currentTextChanged.connect(self._on_mode_changed)

        self.et_time_label = QLabel("ET --:--:--")
        self.price_label = QLabel("Price --")

        self.auto_toggle = QCheckBox("Auto Trading")
        self.auto_toggle.toggled.connect(self._on_auto_toggle)
        self.auto_status = QLabel("OFF")

        self.settings_btn = QPushButton("Settings")
        self.reset_paper_btn = QPushButton("Reset Paper Account")
        self.settings_btn.clicked.connect(self._open_settings)
        self.reset_paper_btn.clicked.connect(self._reset_paper_account)

        for w in [QLabel("Symbol"), self.symbol_box, QLabel("Mode"), self.mode_box, self.auto_toggle, self.auto_status]:
            header.addWidget(w)
        header.addStretch()
        header.addWidget(self.et_time_label)
        header.addWidget(self.price_label)
        header.addWidget(self.settings_btn)
        header.addWidget(self.reset_paper_btn)
        root.addLayout(header)

        center = QHBoxLayout()
        left = QVBoxLayout()

        ind_row = QHBoxLayout()
        self.chk_sma50 = QCheckBox("SMA50")
        self.chk_sma200 = QCheckBox("SMA200")
        self.chk_rsi = QCheckBox("RSI(14)")
        self.chk_obv = QCheckBox("OBV")
        for c in [self.chk_sma50, self.chk_sma200, self.chk_rsi, self.chk_obv]:
            c.setChecked(True)
            c.toggled.connect(self._refresh_chart)
            ind_row.addWidget(c)
        ind_row.addStretch()
        left.addLayout(ind_row)

        self.chart = ChartWidget()
        left.addWidget(self.chart)
        center.addLayout(left, 3)

        right = QVBoxLayout()
        mkt = QGroupBox("Market Data")
        mkt_l = QVBoxLayout(mkt)
        self.tape_widget = TapeWidget()
        self.day_summary = QLabel("High: -  Low: -  Volume: -")
        mkt_l.addWidget(self.tape_widget)
        mkt_l.addWidget(self.day_summary)
        right.addWidget(mkt, 3)

        self.order_panel = OrderPanel()
        self.order_panel.order_requested.connect(self._on_manual_order)
        self.order_panel.cancel_all_requested.connect(self._cancel_all_orders)
        right.addWidget(self.order_panel, 2)

        cond_box = QGroupBox("조건주문")
        cond_layout = QVBoxLayout(cond_box)
        form = QFormLayout()
        self.cond_op = QComboBox(); self.cond_op.addItems([">=", "<="])
        self.cond_action = QComboBox(); self.cond_action.addItems(["BUY", "SELL"])
        self.cond_type = QComboBox(); self.cond_type.addItems(["MARKET", "LIMIT"])
        self.cond_trigger = QLineEdit(); self.cond_trigger.setPlaceholderText("Trigger Price")
        self.cond_qty = QLineEdit(); self.cond_qty.setPlaceholderText("Qty")
        self.cond_limit = QLineEdit(); self.cond_limit.setPlaceholderText("Limit Price (optional)")
        form.addRow("Operator", self.cond_op)
        form.addRow("Action", self.cond_action)
        form.addRow("Order Type", self.cond_type)
        form.addRow("Trigger", self.cond_trigger)
        form.addRow("Qty", self.cond_qty)
        form.addRow("Limit", self.cond_limit)
        cond_layout.addLayout(form)

        row = QHBoxLayout()
        self.btn_add_cond = QPushButton("Create Condition")
        self.btn_cancel_cond = QPushButton("Cancel Selected")
        self.btn_add_cond.clicked.connect(self._create_condition)
        self.btn_cancel_cond.clicked.connect(self._cancel_condition)
        row.addWidget(self.btn_add_cond)
        row.addWidget(self.btn_cancel_cond)
        cond_layout.addLayout(row)

        self.cond_active = QTableWidget(0, 7)
        self.cond_active.setHorizontalHeaderLabels(["ID", "Op", "Trig", "Act", "Type", "Qty", "Status"])
        self.cond_hist = QTableWidget(0, 8)
        self.cond_hist.setHorizontalHeaderLabels(["ID", "Symbol", "Op", "Trig", "Act", "Status", "BrokerID", "Reason"])
        cond_layout.addWidget(QLabel("Active Conditions"))
        cond_layout.addWidget(self.cond_active)
        cond_layout.addWidget(QLabel("Triggered/History"))
        cond_layout.addWidget(self.cond_hist)
        right.addWidget(cond_box, 3)

        center.addLayout(right, 2)
        root.addLayout(center, 5)

        bottom = QGridLayout()
        self.account_card = QLabel("Equity: -\nCash: -\nDay PnL: -")
        self.position_card = QLabel("Qty: -\nAvg: -\nCurrent: -\nUPnL: -\nPnL%: -")
        bottom.addWidget(self._boxed("Account", self.account_card), 0, 0)
        bottom.addWidget(self._boxed("Position", self.position_card), 0, 1)

        self.open_orders = QTableWidget(0, 5)
        self.open_orders.setHorizontalHeaderLabels(["ID", "Symbol", "Side", "Qty", "Status"])
        self.fills_table = QTableWidget(0, 6)
        self.fills_table.setHorizontalHeaderLabels(["Time", "Type", "Symbol", "Side", "Qty", "Price"])
        bottom.addWidget(self._boxed("Open Orders", self.open_orders), 1, 0)
        bottom.addWidget(self._boxed("Fills", self.fills_table), 1, 1)
        root.addLayout(bottom, 2)

    def _boxed(self, title: str, widget: QWidget) -> QGroupBox:
        box = QGroupBox(title)
        l = QVBoxLayout(box)
        l.addWidget(widget)
        return box

    def _init_db(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()

    def _restore_settings(self) -> None:
        rows = self.conn.execute("SELECT key, value FROM ui_settings").fetchall()
        data = {k: v for k, v in rows}
        self.symbol_box.setCurrentText(data.get("last_symbol", "SOXL"))
        self.mode_box.setCurrentText(data.get("last_mode", self.MODE_GUEST))
        self._refresh_condition_tables()

    def _setup_timers(self) -> None:
        self.t_quote = QTimer(self); self.t_quote.timeout.connect(lambda: self._run_async("quote", self._fetch_quote)); self.t_quote.start(1500)
        self.t_ohlcv = QTimer(self); self.t_ohlcv.timeout.connect(lambda: self._run_async("ohlcv", self._fetch_ohlcv)); self.t_ohlcv.start(45000)
        self.t_account = QTimer(self); self.t_account.timeout.connect(lambda: self._run_async("account", self._fetch_account_positions)); self.t_account.start(4000)
        self.t_clock = QTimer(self); self.t_clock.timeout.connect(self._refresh_time); self.t_clock.start(1000)

    def _refresh_time(self) -> None:
        from zoneinfo import ZoneInfo

        self.et_time_label.setText(datetime.now(ZoneInfo("America/New_York")).strftime("ET %Y-%m-%d %H:%M:%S %Z"))

    def _run_async(self, key: str, fn: Callable[[], object]) -> None:
        self.thread_pool.start(_Worker(key, fn, self.worker_signals))

    def _on_worker_success(self, key: str, payload: object) -> None:
        if key == "quote":
            q: Quote = payload  # type: ignore[assignment]
            sign = "+" if q.change_pct >= 0 else ""
            self.price_label.setText(f"{q.symbol} {q.price:.2f} ({sign}{q.change_pct:.2f}%)")
            self.day_summary.setText(f"High: {q.high:.2f}  Low: {q.low:.2f}  Volume: {q.volume:,}")
            self.paper_broker.update_quote(q)
            self.tape_widget.add_quote_tick(q.price, q.volume)
            self._evaluate_conditions(q)
        elif key == "ohlcv":
            self._last_candles = payload  # type: ignore[assignment]
            self._refresh_chart()
        elif key == "account":
            account, pos = payload  # type: ignore[misc]
            self._render_account(account, pos)
        self._load_tables()
        self._refresh_condition_tables()

    def _on_worker_error(self, key: str, msg: str) -> None:
        self.order_panel.set_status(f"{key} error: {msg}")
        if self.mode == self.MODE_LIVE:
            set_emergency_stop(self.conn, True)
            self.auto_ctl.set_enabled(False)
            self.order_panel.set_manual_enabled(False)
            if self.alert:
                self.alert(f"🚨 {key} failure: {msg}")

    def _resolve_market_broker(self) -> BrokerBase:
        if self.mode == self.MODE_GUEST:
            raise RuntimeError("Guest mode broker calls are disabled")
        if not self.auth.client:
            raise RuntimeError("Kiwoom REST session unavailable")
        return KiwoomRestBroker(self.auth.client, self.cfg.kiwoom_account)

    def _resolve_exec_broker_or_none(self) -> Optional[BrokerBase]:
        if self.mode == self.MODE_PAPER:
            return self.paper_broker
        if self.mode == self.MODE_LIVE:
            if not self.auth.client:
                return None
            if not self.live_broker:
                self.live_broker = KiwoomRestBroker(self.auth.client, self.cfg.kiwoom_account)
            return self.live_broker
        return None

    def _fetch_quote(self) -> Quote:
        if self.mode == self.MODE_GUEST:
            raise RuntimeError("Guest mode quote polling disabled")
        return self._resolve_market_broker().get_quote(self.symbol)

    def _fetch_ohlcv(self) -> List[Dict[str, Any]]:
        if self.mode == self.MODE_GUEST:
            return []
        return self._resolve_market_broker().get_ohlcv(self.symbol, 300)

    def _fetch_account_positions(self):
        if self.mode == self.MODE_GUEST:
            return None, []
        if self.mode == self.MODE_PAPER:
            return self.paper_broker.get_account(), self.paper_broker.get_positions()
        b = self._resolve_exec_broker_or_none()
        if not b:
            raise RuntimeError("Live broker unavailable")
        return b.get_account(), b.get_positions()

    def _on_symbol_changed(self, symbol: str) -> None:
        self.symbol = symbol
        self.conn.execute("INSERT OR REPLACE INTO ui_settings(key, value) VALUES(?,?)", ("last_symbol", symbol))
        self.conn.commit()
        self._run_async("ohlcv", self._fetch_ohlcv)

    def _on_mode_changed(self, mode: str) -> None:
        self.mode = mode
        self.conn.execute("INSERT OR REPLACE INTO ui_settings(key, value) VALUES(?,?)", ("last_mode", mode))
        self.conn.commit()
        self.reset_paper_btn.setEnabled(mode == self.MODE_PAPER)
        self.order_panel.set_manual_enabled(mode != self.MODE_GUEST and not self.auto_toggle.isChecked())

    def _on_auto_toggle(self, on: bool) -> None:
        if on and self.mode == self.MODE_GUEST:
            QMessageBox.warning(self, "Auto", "Auto trading is unavailable in Guest mode")
            self.auto_toggle.setChecked(False)
            return
        self.auto_ctl.set_enabled(on)
        self.order_panel.set_manual_enabled(not on and self.mode != self.MODE_GUEST)

    def _on_auto_status(self, status: str) -> None:
        self.auto_status.setText(status)
        if status == "EMERGENCY STOP":
            self.order_panel.set_manual_enabled(False)

    def _on_manual_order(self, payload: Dict[str, Any]) -> None:
        if self.auto_toggle.isChecked():
            ok = QMessageBox.question(self, "Auto Trading", "Pause auto-trading then proceed?")
            if ok != QMessageBox.Yes:
                return
            self.auto_toggle.setChecked(False)
            self.auto_ctl.pause()

        if is_emergency_stop(self.conn):
            QMessageBox.warning(self, "Emergency Stop", "Trading is disabled")
            return
        if self.mode == self.MODE_GUEST:
            QMessageBox.warning(self, "Guest", "Trading disabled in Guest mode")
            return

        try:
            qty = int(payload["qty"])
            side = str(payload["side"])
            order_type = "MARKET" if payload["action_type"] == "MARKET" else str(payload["order_type"])
            limit_price = payload.get("limit_price")

            if self.mode == self.MODE_PAPER:
                result = self.paper_broker.place_order(self.symbol, side, qty, order_type, limit_price)
            else:
                broker = self._resolve_exec_broker_or_none()
                if not broker:
                    raise RuntimeError("Live broker unavailable")
                result = broker.place_order(self.symbol, side, qty, order_type, limit_price)
                self.conn.execute(
                    "INSERT INTO live_orders(order_id, symbol, side, qty, status, created_at) VALUES(?,?,?,?,?,?)",
                    (result.get("order_id", ""), self.symbol, side, qty, result.get("status", "SUBMITTED"), datetime.utcnow().isoformat()),
                )
                self.conn.commit()
                self._reconcile_live_gate()
            self.order_panel.set_status(f"ORDER_RESULT {result.get('status')} {result.get('order_id')}")
        except Exception as exc:
            self._fail_safe(f"ORDER_REQUEST failed: {exc}")

    def _create_condition(self) -> None:
        if self.mode == self.MODE_GUEST:
            QMessageBox.warning(self, "Guest", "Condition orders unavailable in Guest mode")
            return
        try:
            cid = self.condition_engine.create_condition(
                mode=self.mode,
                symbol=self.symbol,
                operator=self.cond_op.currentText(),
                trigger_price=float(self.cond_trigger.text().strip()),
                action=self.cond_action.currentText(),
                order_type=self.cond_type.currentText(),
                qty=int(self.cond_qty.text().strip()),
                limit_price=float(self.cond_limit.text().strip()) if self.cond_limit.text().strip() else None,
            )
            self.order_panel.set_status(f"CONDITION_TRIGGER created id={cid}")
            self._refresh_condition_tables()
        except Exception as exc:
            self._fail_safe(f"Condition create failed: {exc}")

    def _cancel_condition(self) -> None:
        row = self.cond_active.currentRow()
        if row < 0:
            return
        cid_item = self.cond_active.item(row, 0)
        if not cid_item:
            return
        self.condition_engine.cancel_condition(int(cid_item.text()))
        self._refresh_condition_tables()

    def _evaluate_conditions(self, quote: Quote) -> None:
        if self.mode == self.MODE_GUEST or is_emergency_stop(self.conn):
            return
        broker = self._resolve_exec_broker_or_none()
        if not broker:
            return
        results = self.condition_engine.evaluate_tick(self.mode, quote, broker)
        for r in results:
            self.order_panel.set_status(f"CONDITION_TRIGGER id={r.condition_id} status={r.status.value}")
            if r.status.value == "FAILED":
                self._fail_safe(f"Condition order failed id={r.condition_id}: {r.reason}")

    def _cancel_all_orders(self) -> None:
        if self.mode == self.MODE_PAPER:
            rows = self.conn.execute("SELECT order_id FROM paper_orders WHERE status='OPEN'").fetchall()
            for (oid,) in rows:
                self.paper_broker.cancel_order(oid)
        elif self.mode == self.MODE_LIVE:
            broker = self._resolve_exec_broker_or_none()
            if not broker:
                return
            rows = self.conn.execute("SELECT order_id, symbol FROM live_orders WHERE status='SUBMITTED'").fetchall()
            for oid, sym in rows:
                broker.cancel_order(oid, sym)

    def _refresh_chart(self) -> None:
        candles = getattr(self, "_last_candles", [])
        if not candles:
            return
        self.chart.set_candles(candles)
        df = pd.DataFrame(candles)
        self._apply_indicator("SMA50", self.chk_sma50.isChecked(), df)
        self._apply_indicator("SMA200", self.chk_sma200.isChecked(), df)
        self._apply_indicator("RSI14", self.chk_rsi.isChecked(), df)
        self._apply_indicator("OBV", self.chk_obv.isChecked(), df)
        self._load_fill_markers()

    def _apply_indicator(self, key: str, enabled: bool, df: pd.DataFrame) -> None:
        if not enabled:
            self.chart.clear_indicator(key)
            return
        vals = [None if pd.isna(v) else float(v) for v in self.indicators[key].compute(df).tolist()]
        if self.indicators[key].render_location == "overlay":
            self.chart.set_overlay(key, vals)
        else:
            self.chart.set_subpanel(key, vals)

    def _render_account(self, account, positions) -> None:
        if account is None:
            self.account_card.setText("Equity: -\nCash: -\nDay PnL: -")
            self.position_card.setText("Qty: -\nAvg: -\nCurrent: -\nUPnL: -\nPnL%: -")
            return
        self.account_card.setText(f"Total Equity: {account.equity:,.2f}\nCash: {account.cash:,.2f}\nBuying Power: {account.buying_power:,.2f}")
        pos = next((p for p in positions if p.symbol == self.symbol), None)
        if not pos:
            self.position_card.setText("Qty: 0\nAvg: -\nCurrent: -\nUPnL: 0\nPnL%: 0")
            return
        upnl = (pos.market_price - pos.avg_price) * pos.qty
        pct = (upnl / max(1e-9, pos.avg_price * max(1, pos.qty))) * 100
        self.position_card.setText(f"Qty: {pos.qty}\nAvg: {pos.avg_price:.2f}\nCurrent: {pos.market_price:.2f}\nUPnL: {upnl:.2f}\nPnL%: {pct:.2f}%")

    def _load_tables(self) -> None:
        if self.mode == self.MODE_PAPER:
            orders = self.conn.execute("SELECT order_id, symbol, side, qty, status FROM paper_orders ORDER BY id DESC LIMIT 100").fetchall()
            fills = self.conn.execute("SELECT filled_at, 'PAPER', symbol, side, qty, fill_price FROM paper_fills ORDER BY id DESC LIMIT 200").fetchall()
        elif self.mode == self.MODE_LIVE:
            orders = self.conn.execute("SELECT order_id, symbol, side, qty, status FROM live_orders ORDER BY id DESC LIMIT 100").fetchall()
            fills = self.conn.execute("SELECT filled_at, 'LIVE', symbol, side, fill_qty, fill_price FROM fills ORDER BY id DESC LIMIT 200").fetchall()
        else:
            orders, fills = [], []

        self.open_orders.setRowCount(len(orders))
        for r, row in enumerate(orders):
            for c, v in enumerate(row):
                self.open_orders.setItem(r, c, QTableWidgetItem(str(v)))

        self.fills_table.setRowCount(len(fills))
        for r, row in enumerate(fills):
            for c, v in enumerate(row):
                self.fills_table.setItem(r, c, QTableWidgetItem(str(v)))

    def _refresh_condition_tables(self) -> None:
        active = self.condition_engine.list_active(self.mode)
        hist = self.condition_engine.list_history(self.mode)

        self.cond_active.setRowCount(len(active))
        for i, c in enumerate(active):
            vals = [c.id, c.operator.value, c.trigger_price, c.action.value, c.order_type.value, c.qty, c.status.value]
            for j, v in enumerate(vals):
                self.cond_active.setItem(i, j, QTableWidgetItem(str(v)))

        self.cond_hist.setRowCount(len(hist))
        for i, c in enumerate(hist):
            vals = [c.id, c.symbol, c.operator.value, c.trigger_price, c.action.value, c.status.value, c.broker_order_id or "", c.fail_reason or ""]
            for j, v in enumerate(vals):
                self.cond_hist.setItem(i, j, QTableWidgetItem(str(v)))

    def _load_fill_markers(self) -> None:
        candles = getattr(self, "_last_candles", [])
        if not candles:
            return
        d2i = {str(c["date"]): i for i, c in enumerate(candles)}
        markers: List[FillMarker] = []
        for ts, side, px in self.conn.execute("SELECT filled_at, side, fill_price FROM paper_fills WHERE symbol=? ORDER BY id DESC LIMIT 200", (self.symbol,)).fetchall():
            markers.append(FillMarker(index=d2i.get(str(ts)[:10], len(candles)-1), price=float(px), side=str(side), source="PAPER"))
        for ts, px in self.conn.execute("SELECT filled_at, fill_price FROM fills ORDER BY id DESC LIMIT 200").fetchall():
            markers.append(FillMarker(index=d2i.get(str(ts)[:10], len(candles)-1), price=float(px), side="BUY", source="LIVE"))
        self.chart.set_fill_markers(markers)

    def _open_settings(self) -> None:
        QMessageBox.information(self, "Settings", "Paper spread/slippage and refresh intervals are controlled by broker/timer config.")

    def _reset_paper_account(self) -> None:
        if self.mode != self.MODE_PAPER:
            return
        if QMessageBox.question(self, "Reset", "Reset paper account and clear paper data?") != QMessageBox.Yes:
            return
        self.conn.execute("DELETE FROM paper_orders")
        self.conn.execute("DELETE FROM paper_fills")
        self.conn.execute("DELETE FROM paper_positions")
        self.conn.execute("UPDATE paper_account SET cash=100000 WHERE id=1")
        self.conn.commit()

    def _fail_safe(self, reason: str) -> None:
        set_emergency_stop(self.conn, True)
        self.auto_ctl.set_enabled(False)
        self.order_panel.set_manual_enabled(False)
        self.order_panel.set_status(reason)
        if self.alert:
            self.alert(f"🚨 {reason}")

    def _reconcile_live_gate(self) -> None:
        if self.mode != self.MODE_LIVE:
            return
        broker = self._resolve_exec_broker_or_none()
        if not broker:
            self._fail_safe("RESUME_DENIED broker unavailable")
            return
        broker_pos = {p.symbol: p.qty for p in broker.get_positions()}
        db_pos = {}
        for sym, qty in self.conn.execute("SELECT symbol, qty FROM positions").fetchall():
            db_pos[str(sym)] = int(qty)
        if broker_pos != db_pos:
            self._fail_safe(f"RESUME_DENIED reconcile mismatch broker={broker_pos} db={db_pos}")

    def _get_exec_prices(self) -> tuple[float, float]:
        b = self._resolve_market_broker()
        return b.get_quote(self.cfg.exec_bull).price, b.get_quote(self.cfg.exec_bear).price
