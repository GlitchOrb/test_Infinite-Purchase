"""
runtime.py
==========
24/7 runtime orchestrator — StrategyEngine + TradeManager + Kiwoom + SQLite.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import dataclasses
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from config import RuntimeConfig
from db import (
    get_all_positions,
    get_open_orders,
    get_position,
    get_system,
    init_db,
    insert_fill,
    insert_order,
    insert_regime,
    is_emergency_stop,
    mark_action_done,
    open_db,
    rollback_action,
    set_emergency_stop,
    set_system,
    try_lock_action,
    update_order,
    upsert_position,
)
try:
    from kiwoom_adapter import (
        ChejanData,
        KiwoomAdapter,
        KiwoomSessionInvalidError,
    )
except ImportError:
    ChejanData = None  # type: ignore[assignment,misc]
    KiwoomAdapter = None  # type: ignore[assignment,misc]
    KiwoomSessionInvalidError = RuntimeError  # type: ignore[assignment,misc]

from kill_switch import KillSwitch
from telegram_manager import TelegramManager
from strategy_engine import DailyDecision, EffectiveState, StrategyEngine
from trade_manager import OrderIntent, OrderSide, PositionInfo, TradeManager, TradeManagerState
from risk import RiskManager, RiskConfig, RiskVerdict, VerdictAction

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


def _eastern_now(cfg: RuntimeConfig) -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def _market_close_today(cfg: RuntimeConfig) -> datetime:
    et = _eastern_now(cfg)
    return et.replace(hour=cfg.market_close_h, minute=cfg.market_close_m, second=0, microsecond=0)


def _is_trading_session(cfg: RuntimeConfig) -> bool:
    et = _eastern_now(cfg)
    open_t = et.replace(hour=cfg.market_open_h, minute=cfg.market_open_m, second=0, microsecond=0)
    close_t = et.replace(hour=cfg.market_close_h, minute=cfg.market_close_m, second=0, microsecond=0)
    return open_t <= et <= close_t


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
    st.soxs_cooldown_remaining = soxs_row.get("cooldown_remaining", 0)
    st.soxs_forced_close = bool(soxs_row.get("forced_close", 0))

    budget_str = get_system(conn, "injection_budget")
    st.injection_budget = float(budget_str) if budget_str else 0.0
    return st


def _persist_tm_state(conn: sqlite3.Connection, st: TradeManagerState) -> None:
    for sym, pos, extras in [
        ("SOXL", st.soxl, {"max_price_since_entry": st.soxl_max_price, "trailing_stage": st.soxl_trailing_stage, "slices_used": st.soxl_slices_used}),
        ("SOXS", st.soxs, {"holding_days": st.soxs_holding_days, "loss_cut_stage": st.soxs_loss_cut_stage, "slices_used": st.soxs_slices_used, "cooldown_remaining": st.soxs_cooldown_remaining, "forced_close": int(st.soxs_forced_close)}),
    ]:
        upsert_position(
            conn,
            sym,
            qty=pos.qty,
            avg_cost=pos.avg_cost,
            entry_date=str(pos.entry_date) if pos.entry_date else None,
            allocated_capital=pos.allocated_capital,
            **extras,
        )
    set_system(conn, "injection_budget", str(st.injection_budget))
    conn.commit()


class Runtime:
    def __init__(self, cfg: RuntimeConfig | None = None) -> None:
        self.cfg = cfg or RuntimeConfig()
        self.conn = open_db(self.cfg.db_path)
        init_db(self.conn)

        self.strategy = StrategyEngine(signal_ticker=self.cfg.signal_ticker)
        self.trade_mgr = TradeManager()
        self.risk_mgr = RiskManager(
            RiskConfig(
                max_capital_per_trade_pct=0.10,
                max_daily_loss_pct=0.03,
                max_open_positions=5,
            ),
            initial_equity=0.0
        )
        self.kiwoom: Optional[KiwoomAdapter] = None
        self.kill_sw: Optional[KillSwitch] = None
        self.telegram_mgr: Optional[TelegramManager] = None
        self._timer: Optional[QTimer] = None
        self._jobs_run_today: set = set()

    def start(self) -> None:
        logging.basicConfig(level=getattr(logging, self.cfg.log_level), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        set_system(self.conn, "last_start_time", datetime.utcnow().isoformat())
        self.conn.commit()

        app = QApplication.instance() or QApplication(sys.argv)
        self.kiwoom = KiwoomAdapter(self.cfg)
        if not self.kiwoom.login():
            log.critical("Kiwoom login failed — aborting")
            sys.exit(1)

        accts = self.kiwoom.get_account_list()
        if not self.cfg.kiwoom_account and accts:
            object.__setattr__(self.cfg, "kiwoom_account", accts[0])

        self.kiwoom.on_chejan(self._on_chejan)
        self._init_risk_manager()
        self._reconcile(is_startup=True)
        self._init_telegram_notifications()

        self.kill_sw = KillSwitch(self.cfg, on_kill=self._handle_kill, on_resume=self._handle_resume)
        self.kill_sw.start()

        self._timer = QTimer()
        self._timer.timeout.connect(self._scheduler_tick)
        self._timer.start(30_000)
        app.exec_()

    def _init_risk_manager(self) -> None:
        if not self.kiwoom:
            return
        try:
            payload = self.kiwoom.get_overseas_holdings_and_cash()
            cash = float(payload.get("available_cash", 0.0))
            # Estimate equity if total_equity not explicitly provided
            equity = float(payload.get("total_equity", 0.0))
            holdings = payload.get("holdings", [])
            
            if equity <= 0:
                holdings_val = sum(float(h.get("qty", 0)) * float(h.get("current_price", h.get("avg_cost", 0))) for h in holdings)
                equity = cash + holdings_val
            
            self.risk_mgr.reset_daily(equity)
            for h in holdings:
                qty = int(h.get("qty", 0))
                if qty > 0:
                    self.risk_mgr.open_position(h.get("symbol", ""), qty, float(h.get("avg_cost", 0.0)))
            log.info("RiskManager initialized. Equity: %.2f, Positions: %d", equity, self.risk_mgr.open_position_count)
        except Exception as e:
            log.error("Failed to initialize RiskManager: %s", e)

    def _scheduler_tick(self) -> None:
        if not self.kiwoom:
            return
        if not self.kiwoom.session_is_valid():
            self._emergency_stop_for_invalid_session("Kiwoom session invalid detected during scheduler tick")
            return

        now = _eastern_now(self.cfg)
        close = _market_close_today(self.cfg)
        today = now.strftime("%Y-%m-%d")

        if now.hour == 0 and now.minute < 1:
            self._jobs_run_today.clear()

        self._maybe_run(today, "DAILY_BUY", close - timedelta(minutes=self.cfg.buy_before_close_min), now, self._job_daily_buy)
        self._maybe_run(today, "ORPHAN_CLEANUP", close + timedelta(minutes=self.cfg.orphan_cleanup_after_close_min), now, self._job_orphan_cleanup)
        self._maybe_run(today, "REGIME_COMPUTE", close + timedelta(minutes=self.cfg.regime_compute_after_close_min), now, self._job_regime_compute)

        minutes_since_midnight = now.hour * 60 + now.minute
        if minutes_since_midnight % self.cfg.reconcile_interval_min == 0:
            key = f"RECONCILE_LIGHT_{minutes_since_midnight}"
            if key not in self._jobs_run_today:
                self._jobs_run_today.add(key)
                self._reconcile(is_startup=False)

    def _maybe_run(self, today: str, job_name: str, target: datetime, now: datetime, func) -> None:
        key = f"{job_name}_{today}"
        if key in self._jobs_run_today:
            return
        if target <= now < target + timedelta(minutes=2):
            self._jobs_run_today.add(key)
            try:
                func()
            except KiwoomSessionInvalidError as exc:
                self._emergency_stop_for_invalid_session(str(exc))
            except Exception:
                log.exception("Job %s failed", job_name)

    def _job_daily_buy(self) -> None:
        if is_emergency_stop(self.conn) or not _is_trading_session(self.cfg):
            return
        if not self.kiwoom:
            return

        today_str = _eastern_now(self.cfg).strftime("%Y-%m-%d")
        tm_state = _load_tm_state(self.conn)

        from db import get_latest_regime
        regime = get_latest_regime(self.conn)
        if not regime:
            log.warning("No regime data — skipping daily buy")
            return
        decision = self._regime_to_decision(regime)

        try:
            soxl_px = self.kiwoom.get_overseas_quote(self.cfg.exec_bull)
            soxs_px = self.kiwoom.get_overseas_quote(self.cfg.exec_bear)
            
            # Update RiskManager with latest prices for P&L tracking
            self.risk_mgr.update_price(self.cfg.exec_bull, soxl_px)
            self.risk_mgr.update_price(self.cfg.exec_bear, soxs_px)
        except Exception as exc:
            log.error("Price fetch failed, trading skipped: %s", exc)
            if self.kill_sw:
                self.kill_sw.send_alert(f"🚨 PRICE FETCH FAILED — no trade: {exc}")
            return

        total_capital = self._get_total_capital()
        if total_capital <= 0:
            return

        intents, new_state = self.trade_mgr.process_day(decision, soxl_px, soxs_px, total_capital, tm_state)
        for intent in intents:
            self._execute_intent(intent, today_str)
        _persist_tm_state(self.conn, new_state)

    def _job_orphan_cleanup(self) -> None:
        open_orders = get_open_orders(self.conn)
        today_str = _eastern_now(self.cfg).strftime("%Y-%m-%d")
        for order in open_orders:
            broker_id = order.get("broker_order_id")
            if not broker_id:
                continue
            if self.kiwoom:
                self.kiwoom.cancel_order(broker_id, order["symbol"], order["qty"])
            update_order(self.conn, order["id"], status="CANCELLED")
            rollback_action(self.conn, today_str, f"BUY_{order['symbol']}_{today_str}")
        self.conn.commit()

    def _job_regime_compute(self) -> None:
        if not self.kiwoom:
            return
        now = _eastern_now(self.cfg)
        close = _market_close_today(self.cfg)
        if now < close:
            return

        candles = self.kiwoom.get_overseas_daily(self.cfg.signal_ticker, lookback_days=320)
        df = pd.DataFrame(candles)
        if df.empty or len(df) < 250:
            raise RuntimeError("Insufficient SOXX daily candles for regime computation")

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").set_index("date")
        df = self.strategy.compute_indicators(df)
        df = self.strategy.compute_score(df)
        df = self.strategy.compute_returns(df)

        decisions = self.strategy.run(df.reset_index())
        if not decisions:
            raise RuntimeError("Strategy produced no decisions from SOXX daily candles")
        latest = decisions[-1]

        insert_regime(
            self.conn,
            date=str(pd.Timestamp(latest.date).date()),
            close=float(latest.close),
            sma20=float(latest.sma20),
            sma50=float(latest.sma50),
            sma200=float(latest.sma200),
            indicator_L=int(latest.indicator_L),
            indicator_M=int(latest.indicator_M),
            indicator_A=int(latest.indicator_A),
            score=int(latest.score),
            return_3m=float(latest.return_3m),
            return_12m=float(latest.return_12m) if latest.return_12m is not None else None,
            effective_state=latest.effective_state.value,
            transition_active=int(latest.transition_active),
            transition_day=int(latest.transition_day),
            engine_intent=latest.engine_intent.value,
        )
        self.conn.commit()

    def _reconcile(self, is_startup: bool = False) -> bool:
        if not self.kiwoom:
            return False
        payload = self.kiwoom.get_overseas_holdings_and_cash()
        broker_holdings = payload["holdings"]
        broker_map: Dict[str, dict] = {h["symbol"]: h for h in broker_holdings}
        db_positions = get_all_positions(self.conn)
        db_map: Dict[str, dict] = {p["symbol"]: p for p in db_positions}

        mismatches: List[str] = []
        for sym in set(broker_map.keys()) | set(db_map.keys()):
            bk = broker_map.get(sym, {"qty": 0, "avg_cost": 0.0})
            db = db_map.get(sym, {"qty": 0, "avg_cost": 0.0})
            qty_ok = abs(bk["qty"] - db["qty"]) <= self.cfg.reconcile_qty_tolerance
            cost_ok = True
            if db["avg_cost"] > 0:
                cost_ok = abs(bk["avg_cost"] - db["avg_cost"]) / db["avg_cost"] <= self.cfg.reconcile_cost_tolerance
            if not qty_ok or not cost_ok:
                mismatches.append(f"{sym}: broker=({bk['qty']}, {bk['avg_cost']:.4f}) vs db=({db['qty']}, {db['avg_cost']:.4f})")

        has_mismatch = len(mismatches) > 0
        if has_mismatch:
            detail = "\n".join(mismatches)
            set_emergency_stop(self.conn, True)
            self._cancel_all_open_orders()
            if self.kill_sw:
                self.kill_sw.send_alert(f"🚨 RECONCILE MISMATCH — emergency stop!\n{detail}")
        return has_mismatch

    def _execute_intent(self, intent: OrderIntent, today_str: str) -> None:
        if is_emergency_stop(self.conn):
            return
        action_key = f"{intent.side.value}_{intent.symbol}_{today_str}"

        # --- Risk Check ---
        price_for_risk = intent.limit_price_hint or 0.0
        if price_for_risk <= 0 and intent.symbol:
            try:
                price_for_risk = self._fetch_current_price(intent.symbol)
            except Exception:
                pass

        verdict = self.risk_mgr.check_order(
            side=intent.side.value,
            symbol=intent.symbol,
            qty=intent.qty,
            notional=intent.notional,
            price=price_for_risk
        )

        if not verdict.is_allowed:
            log.warning("RiskManager REJECT %s %s: %s", intent.symbol, intent.side.value, verdict.reason)
            return
        if verdict.action == VerdictAction.REDUCE and verdict.allowed_qty is not None:
            log.info("RiskManager REDUCE %s: %s", intent.symbol, verdict.reason)
            intent = dataclasses.replace(intent, qty=verdict.allowed_qty)
        # ------------------

        if intent.side == OrderSide.BUY:
            if not try_lock_action(self.conn, today_str, action_key):
                self.conn.commit()
                return
            self.conn.commit()

        qty = intent.qty
        price = int(intent.limit_price_hint or 0)
        if intent.side == OrderSide.BUY and intent.notional > 0 and qty == 0:
            live_px = self._fetch_current_price(intent.symbol)
            qty = int(intent.notional // live_px) if live_px > 0 else 0
            price = int(live_px)

        if qty <= 0:
            return

        order_id = insert_order(
            self.conn,
            symbol=intent.symbol,
            side=intent.side.value,
            qty=qty,
            notional=intent.notional,
            order_type=intent.order_type_hint,
            limit_price=price,
            status="PENDING",
            reason=intent.reason,
        )
        self.conn.commit()

        side_code = 1 if intent.side == OrderSide.BUY else 2
        order_type_code = "03"
        if self.kiwoom:
            ret = self.kiwoom.send_order(
                rqname=f"ORDER_{order_id}",
                symbol=intent.symbol,
                side=side_code,
                qty=qty,
                price=price,
                order_type=order_type_code,
            )
            status = "SUBMITTED" if ret == 0 else "REJECTED"
            update_order(self.conn, order_id, status=status)
            self.conn.commit()
            if ret != 0 and intent.side == OrderSide.BUY:
                rollback_action(self.conn, today_str, action_key)
                self.conn.commit()

    def _on_chejan(self, data: ChejanData) -> None:
        if data.gubun == "0" and data.status in ("체결", "전량체결", "부분체결"):
            insert_fill(self.conn, data.order_id, data.qty, data.price)
            side = OrderSide.BUY if "매수" in data.side else OrderSide.SELL

            pre_pos = get_position(self.conn, data.symbol) or {"qty": 0, "avg_cost": 0.0}
            pre_avg_cost: float = pre_pos.get("avg_cost", 0.0)
            pre_qty: int = pre_pos.get("qty", 0)

            tm_state = _load_tm_state(self.conn)
            tm_state = self.trade_mgr.apply_fill(data.symbol, side, data.qty, data.price, pd.Timestamp.now(), tm_state)
            _persist_tm_state(self.conn, tm_state)

            if side == OrderSide.BUY:
                self.risk_mgr.open_position(data.symbol, data.qty, data.price)
            else:
                self.risk_mgr.reduce_position(data.symbol, data.qty, data.price)

            if side == OrderSide.BUY:
                today_str = _eastern_now(self.cfg).strftime("%Y-%m-%d")
                mark_action_done(self.conn, today_str, f"BUY_{data.symbol}_{today_str}")

            self.conn.commit()
            if data.symbol == "SOXS" and side == OrderSide.SELL:
                post_qty = pre_qty - data.qty
                realized = (data.price - pre_avg_cost) * data.qty
                if realized > 0:
                    from db import get_latest_regime
                    regime = get_latest_regime(self.conn)
                    if regime and regime.get("effective_state") == "BEAR_ACTIVE":
                        soxl_px = self._fetch_current_price("SOXL")
                        tm_state = _load_tm_state(self.conn)
                        tm_state = self.trade_mgr.on_realized_pnl("SOXS", realized, EffectiveState.BEAR_ACTIVE, soxl_px, tm_state)
                        _persist_tm_state(self.conn, tm_state)

    def _handle_kill(self) -> None:
        set_emergency_stop(self.conn, True)
        self._cancel_all_open_orders()

    def _handle_resume(self) -> None:
        has_mismatch = self._reconcile(is_startup=False)
        if has_mismatch:
            if self.kill_sw:
                self.kill_sw.send_alert("RESUME DENIED — reconcile mismatch present")
            return
        set_emergency_stop(self.conn, False)
        if self.kill_sw:
            self.kill_sw.send_alert("RESUME successful after reconcile")

    def _get_total_capital(self) -> float:
        try:
            if not self.kiwoom:
                raise RuntimeError("Kiwoom not connected")
            payload = self.kiwoom.get_overseas_holdings_and_cash()
            available_cash = float(payload["available_cash"])
            if available_cash <= 0:
                raise ValueError(f"Non-positive available cash: {available_cash}")
            return available_cash
        except Exception as exc:
            log.critical("Failed to fetch account balance via TTTT3012R: %s", exc)
            set_emergency_stop(self.conn, True)
            if self.kill_sw:
                self.kill_sw.send_alert(f"🚨 BALANCE FETCH FAILED — trading aborted: {exc}")
            return 0.0

    def handle_kill_command(self) -> None:
        self._handle_kill()

    def handle_resume(self, passcode: str) -> tuple[bool, str]:
        if passcode != self.cfg.kill_resume_passcode:
            return False, "❌ 재개 실패 — 비밀번호가 올바르지 않습니다."
        self._handle_resume()
        if is_emergency_stop(self.conn):
            return False, "❌ 재개 실패 — 포지션 불일치가 존재합니다."
        return True, "✅ 시스템이 정상적으로 재개되었습니다."

    def _cancel_all_open_orders(self) -> None:
        open_orders = get_open_orders(self.conn)
        for order in open_orders:
            bid = order.get("broker_order_id")
            if bid and self.kiwoom:
                self.kiwoom.cancel_order(bid, order["symbol"], order["qty"])
            update_order(self.conn, order["id"], status="CANCELLED")
        self.conn.commit()

    def _fetch_current_price(self, symbol: str) -> float:
        if not self.kiwoom:
            raise RuntimeError("Kiwoom not connected")
        px = self.kiwoom.get_overseas_quote(symbol)
        if px <= 0:
            raise RuntimeError(f"Invalid price for {symbol}: {px}")
        return px

    def _regime_to_decision(self, regime: dict) -> DailyDecision:
        from strategy_engine import EngineIntent
        return DailyDecision(
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

    def _emergency_stop_for_invalid_session(self, reason: str) -> None:
        log.critical("%s", reason)
        set_emergency_stop(self.conn, True)
        if self.kill_sw:
            self.kill_sw.send_alert(f"🚨 KIWOOM SESSION INVALID — emergency stop + shutdown\n{reason}")
        app = QApplication.instance()
        if app:
            app.quit()


def main() -> None:
    cfg = RuntimeConfig(
        kiwoom_account=os.environ.get("KIWOOM_ACCOUNT", ""),
        telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    Runtime(cfg).start()


if __name__ == "__main__":
    main()
