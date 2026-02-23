from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Callable, Optional

import pandas as pd
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from broker.base import BrokerBase
from db import (
    get_latest_regime,
    is_emergency_stop,
    mark_action_done,
    rollback_action,
    set_emergency_stop,
    try_lock_action,
)
from strategy_engine import DailyDecision, EffectiveState, EngineIntent
from trade_manager import OrderSide, TradeManager, TradeManagerState
from risk import RiskManager, RiskConfig, VerdictAction


class AutoTradingController(QObject):
    status_changed = pyqtSignal(str)
    event_log = pyqtSignal(str)

    def __init__(
        self,
        conn,
        cfg,
        broker_provider: Callable[[], Optional[BrokerBase]],
        get_symbol_prices: Callable[[], tuple[float, float]],
        alert: Optional[Callable[[str], None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.conn = conn
        self.cfg = cfg
        self._broker_provider = broker_provider
        self._get_symbol_prices = get_symbol_prices
        self._alert = alert
        self.trade_mgr = TradeManager()
        self.risk_mgr = RiskManager(
            RiskConfig(
                max_capital_per_trade_pct=0.10,
                max_daily_loss_pct=0.03,
                max_open_positions=5,
            ),
            initial_equity=0.0
        )
        self.enabled = False
        self.paused = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(60_000)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.paused = False
        self.status_changed.emit("ON" if enabled else "OFF")

    def pause(self) -> None:
        self.paused = True
        self.status_changed.emit("PAUSED")

    def _tick(self) -> None:
        self.event_log.emit("AUTO_TICK")
        if not self.enabled:
            self.status_changed.emit("OFF")
            return
        if self.paused:
            self.status_changed.emit("PAUSED")
            return
        if is_emergency_stop(self.conn):
            self.status_changed.emit("EMERGENCY STOP")
            return

        broker = self._broker_provider()
        if broker is None:
            self.status_changed.emit("OFF")
            return

        regime = get_latest_regime(self.conn)
        if not regime:
            return

        decision = DailyDecision(
            date=pd.Timestamp(regime["date"]),
            close=regime.get("close", 0.0),
            sma20=regime.get("sma20", 0.0),
            sma50=regime.get("sma50", 0.0),
            sma200=regime.get("sma200", 0.0),
            indicator_L=bool(regime.get("indicator_L", 0)),
            indicator_M=bool(regime.get("indicator_M", 0)),
            indicator_A=bool(regime.get("indicator_A", 0)),
            score=regime.get("score", 0),
            return_3m=regime.get("return_3m", 0.0),
            return_12m=regime.get("return_12m", None),
            effective_state=EffectiveState(regime.get("effective_state", "NEUTRAL")),
            transition_active=bool(regime.get("transition_active", 0)),
            transition_day=regime.get("transition_day", 0),
            engine_intent=EngineIntent(regime.get("engine_intent", "NONE")),
        )

        try:
            soxl_px, soxs_px = self._get_symbol_prices()
            # Update RiskManager prices
            self.risk_mgr.update_price("SOXL", soxl_px)
            self.risk_mgr.update_price("SOXS", soxs_px)

            acct = broker.get_account()
            total_capital = acct.cash
            
            # Sync RiskManager state with broker
            self.risk_mgr.reset_daily(acct.equity)
            for p in broker.get_positions():
                self.risk_mgr.open_position(p.symbol, p.qty, p.avg_price)

            tm_state = TradeManagerState()
            intents, _ = self.trade_mgr.process_day(decision, soxl_px, soxs_px, total_capital, tm_state)
            today = datetime.now().strftime("%Y-%m-%d")
            for intent in intents:
                # Idempotency check
                action_key = f"{intent.side.value}_{intent.symbol}_{today}"
                if intent.side == OrderSide.BUY:
                    if not try_lock_action(self.conn, today, action_key):
                        continue
                qty = intent.qty
                if qty <= 0 and intent.notional > 0:
                    px = soxl_px if intent.symbol == self.cfg.exec_bull else soxs_px
                    qty = int(intent.notional // px) if px > 0 else 0
                if qty <= 0:
                    if intent.side == OrderSide.BUY:
                        rollback_action(self.conn, today, action_key)
                    continue

                # Risk Check
                verdict = self.risk_mgr.check_order(
                    side=intent.side.value,
                    symbol=intent.symbol,
                    qty=qty,
                    notional=intent.notional,
                    price=soxl_px if intent.symbol == "SOXL" else soxs_px
                )
                if not verdict.is_allowed:
                    self.event_log.emit(f"RISK_REJECT {verdict.reason}")
                    continue
                if verdict.action == VerdictAction.REDUCE and verdict.allowed_qty is not None:
                    qty = verdict.allowed_qty

                order_type = "MARKET"
                result = broker.place_order(intent.symbol, intent.side.value.upper(), qty, order_type)
                self.event_log.emit(f"ORDER_RESULT {result.get('status')}")
                if intent.side == OrderSide.BUY:
                    mark_action_done(self.conn, today, action_key)
        except Exception as exc:
            set_emergency_stop(self.conn, True)
            self.status_changed.emit("EMERGENCY STOP")
            if self._alert:
                self._alert(f"🚨 AUTO pipeline failure: {exc}")
