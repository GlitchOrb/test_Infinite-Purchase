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
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from broker.base import BrokerBase, Quote
from broker.kiwoom_rest_broker import KiwoomRestBroker
from broker.paper_broker import PaperBroker
from db import set_emergency_stop, set_system
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
        self.auto_trading_on = False

        self.thread_pool = QThreadPool.globalInstance()
        self.worker_signals = _WorkerSignals()
        self.worker_signals.success.connect(self._on_worker_success)
        self.worker_signals.error.connect(self._on_worker_error)

        self.live_broker: Optional[BrokerBase] = None
        self.paper_broker = PaperBroker(conn)
        self.current_broker: Optional[BrokerBase] = None

        self.indicators = {
            "SMA50": SMAIndicator(50),
            "SMA200": SMAIndicator(200),
            "RSI14": RSIIndicator(14),
            "OBV": OBVIndicator(),
        }

        self._build_ui()
        self._init_db()
        self._restore_settings()
        self._setup_timers()
        self._refresh_time()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        self.symbol_box = QComboBox()
        self.symbol_box.addItems(["SOXL", "SOXS", "SOXX"])
        self.symbol_box.currentTextChanged.connect(self._on_symbol_changed)
        header.addWidget(QLabel("Symbol"))
        header.addWidget(self.symbol_box)

        self.mode_box = QComboBox()
        self.mode_box.addItems([self.MODE_GUEST, self.MODE_PAPER, self.MODE_LIVE])
        self.mode_box.currentTextChanged.connect(self._on_mode_changed)
        header.addWidget(QLabel("Mode"))
        header.addWidget(self.mode_box)

        self.et_time_label = QLabel("ET --:--:--")
        self.price_label = QLabel("Price --")
        header.addStretch()
        header.addWidget(self.et_time_label)
        header.addWidget(self.price_label)

        self.settings_btn = QPushButton("Settings")
        self.reset_paper_btn = QPushButton("Reset Paper Account")
        self.settings_btn.clicked.connect(self._open_settings)
        self.reset_paper_btn.clicked.connect(self._reset_paper_account)
        header.addWidget(self.settings_btn)
        header.addWidget(self.reset_paper_btn)

        root.addLayout(header)

        center = QHBoxLayout()
        center_left = QVBoxLayout()

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
        center_left.addLayout(ind_row)

        self.chart = ChartWidget()
        center_left.addWidget(self.chart)

        center.addLayout(center_left, 3)

        right = QVBoxLayout()

        mkt_box = QGroupBox("Market Data")
        mkt_layout = QVBoxLayout(mkt_box)
        self.tape_widget = TapeWidget()
        mkt_layout.addWidget(self.tape_widget)
        self.day_summary = QLabel("High: -  Low: -  Volume: -")
        mkt_layout.addWidget(self.day_summary)
        right.addWidget(mkt_box, 3)

        self.order_panel = OrderPanel()
        self.order_panel.order_requested.connect(self._on_manual_order)
        self.order_panel.cancel_all_requested.connect(self._cancel_all_orders)
        right.addWidget(self.order_panel, 2)

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
        lay = QVBoxLayout(box)
        lay.addWidget(widget)
        return box

    def _init_db(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS ui_settings ("
            "key TEXT PRIMARY KEY,"
            "value TEXT NOT NULL"
            ")"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS live_orders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "order_id TEXT, symbol TEXT, side TEXT, qty INTEGER, status TEXT, created_at TEXT"
            ")"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS indicator_settings ("
            "name TEXT PRIMARY KEY,"
            "enabled INTEGER NOT NULL"
            ")"
        )
        self.conn.commit()

    def _restore_settings(self) -> None:
        rows = self.conn.execute("SELECT key, value FROM ui_settings").fetchall()
        saved = {k: v for k, v in rows}
        self.symbol = saved.get("last_symbol", "SOXL")
        self.mode = saved.get("last_mode", self.MODE_GUEST)

        self.symbol_box.setCurrentText(self.symbol)
        self.mode_box.setCurrentText(self.mode)

        for chk, key in [
            (self.chk_sma50, "SMA50"),
            (self.chk_sma200, "SMA200"),
            (self.chk_rsi, "RSI14"),
            (self.chk_obv, "OBV"),
        ]:
            row = self.conn.execute("SELECT enabled FROM indicator_settings WHERE name=?", (key,)).fetchone()
            if row is not None:
                chk.setChecked(bool(row[0]))

        self._on_mode_changed(self.mode)

    def _setup_timers(self) -> None:
        self.t_quote = QTimer(self)
        self.t_quote.timeout.connect(lambda: self._run_async("quote", self._fetch_quote))
        self.t_quote.start(1500)

        self.t_tape = QTimer(self)
        self.t_tape.timeout.connect(self._refresh_tape)
        self.t_tape.start(1500)

        self.t_ohlcv = QTimer(self)
        self.t_ohlcv.timeout.connect(lambda: self._run_async("ohlcv", self._fetch_ohlcv))
        self.t_ohlcv.start(45000)

        self.t_account = QTimer(self)
        self.t_account.timeout.connect(lambda: self._run_async("account", self._fetch_account_positions))
        self.t_account.start(4000)

        self.t_clock = QTimer(self)
        self.t_clock.timeout.connect(self._refresh_time)
        self.t_clock.start(1000)

    def _refresh_time(self) -> None:
        from zoneinfo import ZoneInfo

        et = datetime.now(ZoneInfo("America/New_York"))
        self.et_time_label.setText(et.strftime("ET %Y-%m-%d %H:%M:%S %Z"))

    def _run_async(self, key: str, fn: Callable[[], object]) -> None:
        worker = _Worker(key, fn, self.worker_signals)
        self.thread_pool.start(worker)

    def _on_worker_success(self, key: str, payload: object) -> None:
        if key == "quote":
            q: Quote = payload  # type: ignore[assignment]
            sign = "+" if q.change_pct >= 0 else ""
            self.price_label.setText(f"{q.symbol} {q.price:.2f} ({sign}{q.change_pct:.2f}%)")
            self.day_summary.setText(f"High: {q.high:.2f}  Low: {q.low:.2f}  Volume: {q.volume:,}")
            self.paper_broker.update_quote(q)
            self.tape_widget.add_quote_tick(q.price, q.volume)
        elif key == "ohlcv":
            candles: List[Dict[str, Any]] = payload  # type: ignore[assignment]
            self._last_candles = candles
            self._refresh_chart()
        elif key == "account":
            acct, pos = payload  # type: ignore[misc]
            self._render_account(acct, pos)

    def _on_worker_error(self, key: str, message: str) -> None:
        if self.mode == self.MODE_GUEST:
            return
        self.order_panel.set_status(f"{key} error: {message}")
        if self.mode == self.MODE_LIVE:
            set_emergency_stop(self.conn, True)
            self.order_panel.set_manual_enabled(False)
            if self.alert:
                self.alert(f"🚨 Live API failure ({key}): {message}")

    def _resolve_market_broker(self) -> BrokerBase:
        if self.mode == self.MODE_GUEST:
            raise RuntimeError("Guest mode has no broker calls")
        if self.mode == self.MODE_PAPER:
            if not self.auth.client:
                raise RuntimeError("Paper mode requires Kiwoom REST session for market data")
            return KiwoomRestBroker(self.auth.client, self.cfg.kiwoom_account)
        if self.mode == self.MODE_LIVE:
            if not self.auth.client:
                raise RuntimeError("Live mode requires Kiwoom REST session")
            if not self.live_broker:
                self.live_broker = KiwoomRestBroker(self.auth.client, self.cfg.kiwoom_account)
            return self.live_broker
        raise RuntimeError("Unknown mode")

    def _fetch_quote(self) -> Quote:
        b = self._resolve_market_broker()
        return b.get_quote(self.symbol)

    def _fetch_ohlcv(self) -> List[Dict[str, Any]]:
        b = self._resolve_market_broker()
        return b.get_ohlcv(self.symbol, 300)

    def _fetch_account_positions(self):
        if self.mode == self.MODE_GUEST:
            return None, []
        if self.mode == self.MODE_PAPER:
            return self.paper_broker.get_account(), self.paper_broker.get_positions()
        b = self._resolve_market_broker()
        return b.get_account(), b.get_positions()

    def _on_symbol_changed(self, symbol: str) -> None:
        self.symbol = symbol
        set_system(self.conn, "last_symbol", symbol)
        self.conn.execute("INSERT OR REPLACE INTO ui_settings(key, value) VALUES(?,?)", ("last_symbol", symbol))
        self.conn.commit()
        self._run_async("ohlcv", self._fetch_ohlcv)

    def _on_mode_changed(self, mode: str) -> None:
        self.mode = mode
        self.current_broker = None
        if mode == self.MODE_GUEST:
            self.order_panel.set_manual_enabled(False)
            self.reset_paper_btn.setEnabled(False)
        elif mode == self.MODE_PAPER:
            self.order_panel.set_manual_enabled(not self.auto_trading_on)
            self.reset_paper_btn.setEnabled(True)
        else:
            self.order_panel.set_manual_enabled(not self.auto_trading_on)
            self.reset_paper_btn.setEnabled(False)

        self.conn.execute("INSERT OR REPLACE INTO ui_settings(key, value) VALUES(?,?)", ("last_mode", mode))
        self.conn.commit()

    def _refresh_tape(self) -> None:
        self._load_tables()

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
        self._save_indicator_flags()
        self._load_fill_markers()

    def _apply_indicator(self, key: str, enabled: bool, df: pd.DataFrame) -> None:
        if not enabled:
            self.chart.clear_indicator(key)
            return
        series = self.indicators[key].compute(df)
        vals = [None if pd.isna(v) else float(v) for v in series.tolist()]
        if self.indicators[key].render_location == "overlay":
            self.chart.set_overlay(key, vals)
        else:
            self.chart.set_subpanel(key, vals)

    def _save_indicator_flags(self) -> None:
        flags = {
            "SMA50": self.chk_sma50.isChecked(),
            "SMA200": self.chk_sma200.isChecked(),
            "RSI14": self.chk_rsi.isChecked(),
            "OBV": self.chk_obv.isChecked(),
        }
        for k, v in flags.items():
            self.conn.execute("INSERT OR REPLACE INTO indicator_settings(name, enabled) VALUES(?,?)", (k, int(v)))
        self.conn.commit()

    def _on_manual_order(self, payload: Dict[str, Any]) -> None:
        if self.auto_trading_on:
            ok = QMessageBox.question(self, "Auto Trading", "Pause auto and proceed?")
            if ok != QMessageBox.Yes:
                return
            self.auto_trading_on = False
            self.order_panel.set_manual_enabled(True)

        if self.mode == self.MODE_GUEST:
            QMessageBox.warning(self, "Guest Mode", "Trading is disabled in Guest mode")
            return

        try:
            qty = int(payload["qty"])
            side = str(payload["side"])
            order_type = "MARKET" if payload["action_type"] == "MARKET" else str(payload["order_type"])
            limit_price = payload.get("limit_price")

            if self.mode == self.MODE_PAPER:
                result = self.paper_broker.place_order(self.symbol, side, qty, order_type, limit_price)
            else:
                b = self._resolve_market_broker()
                result = b.place_order(self.symbol, side, qty, order_type, limit_price)
                self.conn.execute(
                    "INSERT INTO live_orders(order_id, symbol, side, qty, status, created_at) VALUES(?,?,?,?,?,?)",
                    (result.get("order_id", ""), self.symbol, side, qty, result.get("status", "SUBMITTED"), datetime.utcnow().isoformat()),
                )
                self.conn.commit()
                self._run_async("account", self._fetch_account_positions)

            self.order_panel.set_status(f"Order {result.get('status')}: {result.get('order_id')}")
            self._load_tables()
        except Exception as exc:
            self.order_panel.set_status(f"Order failed: {exc}")
            if self.mode == self.MODE_LIVE:
                set_emergency_stop(self.conn, True)
                self.order_panel.set_manual_enabled(False)
                if self.alert:
                    self.alert(f"🚨 Live order failure: {exc}")

    def _cancel_all_orders(self) -> None:
        if self.mode == self.MODE_PAPER:
            rows = self.conn.execute("SELECT order_id FROM paper_orders WHERE status='OPEN'").fetchall()
            for (oid,) in rows:
                self.paper_broker.cancel_order(oid)
        elif self.mode == self.MODE_LIVE:
            rows = self.conn.execute("SELECT order_id, symbol FROM live_orders WHERE status='SUBMITTED'").fetchall()
            b = self._resolve_market_broker()
            for oid, sym in rows:
                b.cancel_order(oid, sym)
        self._load_tables()

    def _render_account(self, account, positions) -> None:
        if account is None:
            self.account_card.setText("Equity: -\nCash: -\nDay PnL: -")
            self.position_card.setText("Qty: -\nAvg: -\nCurrent: -\nUPnL: -\nPnL%: -")
            return

        self.account_card.setText(
            f"Total Equity: {account.equity:,.2f}\n"
            f"Cash: {account.cash:,.2f}\n"
            f"Buying Power: {account.buying_power:,.2f}"
        )

        target = next((p for p in positions if p.symbol == self.symbol), None)
        if not target:
            self.position_card.setText("Qty: 0\nAvg: -\nCurrent: -\nUPnL: 0\nPnL%: 0")
            return
        upnl = (target.market_price - target.avg_price) * target.qty
        pct = (upnl / max(1e-9, target.avg_price * max(1, target.qty))) * 100
        self.position_card.setText(
            f"Qty: {target.qty}\nAvg: {target.avg_price:.2f}\nCurrent: {target.market_price:.2f}\nUPnL: {upnl:.2f}\nPnL%: {pct:.2f}%"
        )

    def _load_tables(self) -> None:
        if self.mode == self.MODE_PAPER:
            orders = self.conn.execute(
                "SELECT order_id, symbol, side, qty, status FROM paper_orders WHERE status='OPEN' ORDER BY id DESC LIMIT 100"
            ).fetchall()
            fills = self.conn.execute(
                "SELECT filled_at, 'PAPER', symbol, side, qty, fill_price FROM paper_fills ORDER BY id DESC LIMIT 200"
            ).fetchall()
        elif self.mode == self.MODE_LIVE:
            orders = self.conn.execute(
                "SELECT order_id, symbol, side, qty, status FROM live_orders ORDER BY id DESC LIMIT 100"
            ).fetchall()
            fills = self.conn.execute(
                "SELECT filled_at, 'LIVE', symbol, side, fill_qty, fill_price FROM fills ORDER BY id DESC LIMIT 200"
            ).fetchall()
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

    def _load_fill_markers(self) -> None:
        candles = getattr(self, "_last_candles", [])
        if not candles:
            return
        date_to_idx = {str(c["date"]): i for i, c in enumerate(candles)}
        markers: List[FillMarker] = []

        rows = self.conn.execute(
            "SELECT filled_at, side, fill_price FROM paper_fills WHERE symbol=? ORDER BY id DESC LIMIT 200",
            (self.symbol,),
        ).fetchall()
        for ts, side, px in rows:
            d = str(ts)[:10]
            idx = date_to_idx.get(d, len(candles) - 1)
            markers.append(FillMarker(index=idx, price=float(px), side=str(side), source="PAPER"))

        rows2 = self.conn.execute(
            "SELECT filled_at, fill_price FROM fills ORDER BY id DESC LIMIT 200"
        ).fetchall()
        for ts, px in rows2:
            d = str(ts)[:10]
            idx = date_to_idx.get(d, len(candles) - 1)
            markers.append(FillMarker(index=idx, price=float(px), side="BUY", source="LIVE"))

        self.chart.set_fill_markers(markers)

    def _open_settings(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle("Settings")
        msg.setText("Paper spread/slippage and refresh timers are active from broker/timers configuration.")
        msg.exec_()

    def _reset_paper_account(self) -> None:
        if self.mode != self.MODE_PAPER:
            return
        if QMessageBox.question(self, "Reset Paper", "Reset paper account and clear paper orders/fills?") != QMessageBox.Yes:
            return
        self.conn.execute("DELETE FROM paper_orders")
        self.conn.execute("DELETE FROM paper_fills")
        self.conn.execute("DELETE FROM paper_positions")
        self.conn.execute("UPDATE paper_account SET cash=100000 WHERE id=1")
        self.conn.commit()
        self._load_tables()
