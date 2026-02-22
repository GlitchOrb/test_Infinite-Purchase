"""
runtime.py
==========
24/7 runtime orchestrator — ties StrategyEngine, TradeManager, Kiwoom,
SQLite persistence, scheduling, reconcile, and kill switch together.

Launch:  ``python runtime.py``

No external AI API at runtime.  All logic is deterministic Python.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

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
    is_action_done,
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
from kiwoom_adapter import ChejanData, KiwoomAdapter
from kill_switch import KillSwitch
from strategy_engine import DailyDecision, EffectiveState, StrategyEngine
from trade_manager import (
    OrderIntent,
    OrderSide,
    PositionInfo,
    TradeManager,
    TradeManagerState,
)

log = logging.getLogger(__name__)

# ======================================================================= #
#  Timezone helper
# ======================================================================= #

try:
    from zoneinfo import ZoneInfo           # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


def _eastern_now(cfg: RuntimeConfig) -> datetime:
    return datetime.now(ZoneInfo(cfg.market_tz))


def _market_close_today(cfg: RuntimeConfig) -> datetime:
    et = _eastern_now(cfg)
    return et.replace(hour=cfg.market_close_h, minute=cfg.market_close_m,
                      second=0, microsecond=0)


# ======================================================================= #
#  State hydration helpers
# ======================================================================= #

def _load_tm_state(conn: sqlite3.Connection) -> TradeManagerState:
    """Rebuild TradeManagerState from SQLite on startup / each cycle."""
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


def _persist_tm_state(conn: sqlite3.Connection, st: TradeManagerState) -> None:
    """Write TradeManagerState back to SQLite."""
    for sym, pos, extras in [
        ("SOXL", st.soxl, {
            "max_price_since_entry": st.soxl_max_price,
            "trailing_stage": st.soxl_trailing_stage,
            "slices_used": st.soxl_slices_used,
        }),
        ("SOXS", st.soxs, {
            "holding_days": st.soxs_holding_days,
            "loss_cut_stage": st.soxs_loss_cut_stage,
            "slices_used": st.soxs_slices_used,
        }),
    ]:
        upsert_position(
            conn, sym,
            qty=pos.qty,
            avg_cost=pos.avg_cost,
            entry_date=str(pos.entry_date) if pos.entry_date else None,
            allocated_capital=pos.allocated_capital,
            **extras,
        )

    set_system(conn, "injection_budget", str(st.injection_budget))
    conn.commit()


# ======================================================================= #
#  Runtime
# ======================================================================= #

class Runtime:
    """Main 24/7 orchestrator.

    Lifecycle
    ---------
    1. ``Runtime(cfg).start()``
    2. Enters Qt event loop (blocking)
    3. QTimer ticks every minute → scheduler evaluates pending jobs
    4. Kill switch polls Telegram in a daemon thread
    """

    def __init__(self, cfg: RuntimeConfig | None = None) -> None:
        self.cfg = cfg or RuntimeConfig()
        self.conn = open_db(self.cfg.db_path)
        init_db(self.conn)

        self.strategy = StrategyEngine(signal_ticker=self.cfg.signal_ticker)
        self.trade_mgr = TradeManager()
        self.kiwoom: Optional[KiwoomAdapter] = None
        self.kill_sw: Optional[KillSwitch] = None

        self._timer: Optional[QTimer] = None
        self._jobs_run_today: set = set()

    # ------------------------------------------------------------------ #
    #  Bootstrap
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Connect to Kiwoom, run startup reconcile, enter event loop."""
        logging.basicConfig(
            level=getattr(logging, self.cfg.log_level),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        set_system(self.conn, "last_start_time", datetime.utcnow().isoformat())
        self.conn.commit()

        app = QApplication.instance() or QApplication(sys.argv)

        # Kiwoom login
        self.kiwoom = KiwoomAdapter(self.cfg)
        if not self.kiwoom.login():
            log.critical("Kiwoom login failed — aborting")
            sys.exit(1)

        accts = self.kiwoom.get_account_list()
        log.info("Accounts: %s", accts)
        if not self.cfg.kiwoom_account and accts:
            # Auto-select first account (override in config for prod)
            object.__setattr__(self.cfg, "kiwoom_account", accts[0])
            log.info("Using account: %s", self.cfg.kiwoom_account)

        # Register chejan callback
        self.kiwoom.on_chejan(self._on_chejan)

        # Startup reconcile (mandatory)
        self._reconcile(is_startup=True)

        # Kill switch
        self.kill_sw = KillSwitch(
            self.cfg,
            on_kill=self._handle_kill,
            on_resume=self._handle_resume,
        )
        self.kill_sw.start()

        # Scheduler tick every 30 seconds
        self._timer = QTimer()
        self._timer.timeout.connect(self._scheduler_tick)
        self._timer.start(30_000)

        log.info("Runtime started — entering Qt event loop")
        app.exec_()

    # ------------------------------------------------------------------ #
    #  Scheduler
    # ------------------------------------------------------------------ #

    def _scheduler_tick(self) -> None:
        """Evaluate which jobs should run, based on current Eastern time."""
        now = _eastern_now(self.cfg)
        close = _market_close_today(self.cfg)
        today = now.strftime("%Y-%m-%d")

        # Reset daily job tracker at midnight ET
        if now.hour == 0 and now.minute < 1:
            self._jobs_run_today.clear()

        # Job 1: Daily buy slice (T-10 min before close)
        buy_time = close - timedelta(minutes=self.cfg.buy_before_close_min)
        self._maybe_run(today, "DAILY_BUY", buy_time, now, self._job_daily_buy)

        # Job 2: Orphan cleanup (T+5 min after close)
        cleanup_time = close + timedelta(minutes=self.cfg.orphan_cleanup_after_close_min)
        self._maybe_run(today, "ORPHAN_CLEANUP", cleanup_time, now, self._job_orphan_cleanup)

        # Job 3: EOD regime computation (T+15 min)
        regime_time = close + timedelta(minutes=self.cfg.regime_compute_after_close_min)
        self._maybe_run(today, "REGIME_COMPUTE", regime_time, now, self._job_regime_compute)

        # Job 4: Periodic reconcile-light
        minutes_since_midnight = now.hour * 60 + now.minute
        if minutes_since_midnight % self.cfg.reconcile_interval_min == 0:
            key = f"RECONCILE_LIGHT_{minutes_since_midnight}"
            if key not in self._jobs_run_today:
                self._jobs_run_today.add(key)
                self._reconcile(is_startup=False)

    def _maybe_run(self, today: str, job_name: str,
                   target: datetime, now: datetime, func) -> None:
        key = f"{job_name}_{today}"
        if key in self._jobs_run_today:
            return
        # Run if we are within (target, target+2min)
        if target <= now < target + timedelta(minutes=2):
            self._jobs_run_today.add(key)
            log.info("⏰ Running job: %s", job_name)
            try:
                func()
            except Exception:
                log.exception("Job %s failed", job_name)

    # ------------------------------------------------------------------ #
    #  Job: Daily buy slice
    # ------------------------------------------------------------------ #

    def _job_daily_buy(self) -> None:
        if is_emergency_stop(self.conn):
            log.warning("Emergency stop active — skipping daily buy")
            return

        today_str = _eastern_now(self.cfg).strftime("%Y-%m-%d")
        tm_state = _load_tm_state(self.conn)

        # Load latest decision (computed in previous EOD cycle)
        from db import get_latest_regime
        regime = get_latest_regime(self.conn)
        if not regime:
            log.warning("No regime data — skipping daily buy")
            return

        decision = self._regime_to_decision(regime)

        # TODO(kiwoom): fetch live SOXL / SOXS prices via TR
        soxl_px = self._fetch_current_price(self.cfg.exec_bull)
        soxs_px = self._fetch_current_price(self.cfg.exec_bear)

        # TODO: fetch actual total capital from broker
        total_capital = 100_000  # placeholder

        intents, new_state = self.trade_mgr.process_day(
            decision, soxl_px, soxs_px, total_capital, tm_state,
        )

        for intent in intents:
            self._execute_intent(intent, today_str)

        _persist_tm_state(self.conn, new_state)

    # ------------------------------------------------------------------ #
    #  Job: Orphan cleanup
    # ------------------------------------------------------------------ #

    def _job_orphan_cleanup(self) -> None:
        open_orders = get_open_orders(self.conn)
        today_str = _eastern_now(self.cfg).strftime("%Y-%m-%d")

        for order in open_orders:
            broker_id = order.get("broker_order_id")
            if not broker_id:
                continue
            log.info("Cancelling orphan order: %s", broker_id)
            if self.kiwoom:
                self.kiwoom.cancel_order(broker_id, order["symbol"], order["qty"])
            update_order(self.conn, order["id"], status="CANCELLED")

            # Rollback daily action so the slice can be re-attempted
            action_key = f"BUY_{order['symbol']}_{today_str}"
            rollback_action(self.conn, today_str, action_key)

        self.conn.commit()
        log.info("Orphan cleanup complete (%d orders)", len(open_orders))

    # ------------------------------------------------------------------ #
    #  Job: EOD regime computation
    # ------------------------------------------------------------------ #

    def _job_regime_compute(self) -> None:
        # TODO(kiwoom): fetch SOXX EOD OHLCV via TR (e.g. 해외주식일봉 TR)
        # Placeholder: fetch_soxx_daily() should return a DataFrame
        log.info("Fetching SOXX daily data for regime computation …")

        # soxx_df = self._fetch_soxx_daily()  # TODO: implement via Kiwoom TR
        # decisions = self.strategy.run(soxx_df)
        # latest = decisions[-1] if decisions else None

        # For now, log a placeholder
        log.info("TODO: regime computation with live SOXX data")

        # if latest:
        #     insert_regime(self.conn, date=str(latest.date), close=latest.close,
        #                   sma20=latest.sma20, sma50=latest.sma50, sma200=latest.sma200,
        #                   indicator_L=int(latest.indicator_L),
        #                   indicator_M=int(latest.indicator_M),
        #                   indicator_S=int(latest.indicator_S),
        #                   score=latest.score, return_3m=latest.return_3m,
        #                   effective_state=latest.effective_state.value,
        #                   transition_active=int(latest.transition_active),
        #                   transition_day=latest.transition_day,
        #                   engine_intent=latest.engine_intent.value)
        #     self.conn.commit()

    # ------------------------------------------------------------------ #
    #  Reconcile
    # ------------------------------------------------------------------ #

    def _reconcile(self, is_startup: bool = False) -> None:
        """Compare broker holdings vs. SQLite positions.

        On mismatch: emergency stop + cancel all + alert.
        """
        label = "STARTUP" if is_startup else "PERIODIC"
        log.info("Reconcile [%s] starting …", label)

        if not self.kiwoom:
            log.warning("Kiwoom not connected — skipping reconcile")
            return

        broker_holdings = self.kiwoom.get_holdings()
        broker_map: Dict[str, dict] = {h["symbol"]: h for h in broker_holdings}
        db_positions = get_all_positions(self.conn)
        db_map: Dict[str, dict] = {p["symbol"]: p for p in db_positions}

        all_symbols = set(broker_map.keys()) | set(db_map.keys())
        mismatches: List[str] = []

        for sym in all_symbols:
            bk = broker_map.get(sym, {"qty": 0, "avg_cost": 0.0})
            db = db_map.get(sym, {"qty": 0, "avg_cost": 0.0})

            qty_ok = abs(bk["qty"] - db["qty"]) <= self.cfg.reconcile_qty_tolerance
            cost_ok = True
            if db["avg_cost"] > 0:
                cost_ok = (
                    abs(bk["avg_cost"] - db["avg_cost"]) / db["avg_cost"]
                    <= self.cfg.reconcile_cost_tolerance
                )

            if not qty_ok or not cost_ok:
                msg = (f"{sym}: broker=({bk['qty']}, {bk['avg_cost']:.2f}) "
                       f"vs db=({db['qty']}, {db['avg_cost']:.2f})")
                mismatches.append(msg)

        if mismatches:
            detail = "\n".join(mismatches)
            log.critical("RECONCILE MISMATCH:\n%s", detail)
            set_emergency_stop(self.conn, True)
            self._cancel_all_open_orders()
            if self.kill_sw:
                self.kill_sw.send_alert(
                    f"🚨 RECONCILE MISMATCH — emergency stop!\n{detail}"
                )
        else:
            log.info("Reconcile [%s] OK ✓", label)

    # ------------------------------------------------------------------ #
    #  Intent execution
    # ------------------------------------------------------------------ #

    def _execute_intent(self, intent: OrderIntent, today_str: str) -> None:
        """Translate an OrderIntent into a Kiwoom order + SQLite records."""
        if is_emergency_stop(self.conn):
            log.warning("Emergency stop — dropping intent: %s", intent.reason)
            return

        action_key = f"{intent.side.value}_{intent.symbol}_{today_str}"

        # Idempotency lock (buys only — sells are always allowed)
        if intent.side == OrderSide.BUY:
            if not try_lock_action(self.conn, today_str, action_key):
                log.info("Daily action already locked/done: %s", action_key)
                self.conn.commit()
                return
            self.conn.commit()

        # Determine qty from notional for buys
        qty = intent.qty
        price = int(intent.limit_price_hint or 0)
        if intent.side == OrderSide.BUY and intent.notional > 0 and qty == 0:
            live_px = self._fetch_current_price(intent.symbol)
            qty = int(intent.notional // live_px) if live_px > 0 else 0
            price = int(live_px)  # limit at last price

        if qty <= 0:
            log.warning("Computed qty=0 for %s — skipping", intent.reason)
            return

        # Record in SQLite
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

        # Submit to Kiwoom
        side_code = 1 if intent.side == OrderSide.BUY else 2
        order_type_code = "00"  # TODO(kiwoom): map to 지정가/시장가
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

            if ret != 0:
                log.error("Order rejected (ret=%d): %s", ret, intent.reason)
                if intent.side == OrderSide.BUY:
                    rollback_action(self.conn, today_str, action_key)
                    self.conn.commit()

    # ------------------------------------------------------------------ #
    #  Chejan callback (fills)
    # ------------------------------------------------------------------ #

    def _on_chejan(self, data: ChejanData) -> None:
        """Process real-time fill / order-status updates from Kiwoom."""
        if data.gubun == "0" and data.status in ("체결", "전량체결"):
            # Fill confirmed
            insert_fill(self.conn, data.order_id, data.qty, data.price)

            # Update position
            pos = get_position(self.conn, data.symbol) or {
                "qty": 0, "avg_cost": 0.0,
            }
            side = OrderSide.BUY if "매수" in data.side else OrderSide.SELL
            tm_state = _load_tm_state(self.conn)
            tm_state = self.trade_mgr.apply_fill(
                data.symbol, side, data.qty, data.price,
                pd.Timestamp.now(), tm_state,
            )
            _persist_tm_state(self.conn, tm_state)

            # Mark daily action DONE for buys
            if side == OrderSide.BUY:
                today_str = _eastern_now(self.cfg).strftime("%Y-%m-%d")
                action_key = f"BUY_{data.symbol}_{today_str}"
                mark_action_done(self.conn, today_str, action_key)

            self.conn.commit()
            log.info("Fill processed: %s %s %d @ %.2f",
                     data.symbol, side.value, data.qty, data.price)

            # Vampire rebalance check for SOXS take-profit
            if data.symbol == "SOXS" and side == OrderSide.SELL:
                pos_soxs = get_position(self.conn, "SOXS")
                if pos_soxs and pos_soxs["qty"] == 0:
                    # Full exit — compute realized PnL
                    realized = (data.price - (pos_soxs.get("avg_cost", 0.0))) * data.qty
                    from db import get_latest_regime
                    regime = get_latest_regime(self.conn)
                    if regime and regime.get("effective_state") == "BEAR_ACTIVE":
                        soxl_px = self._fetch_current_price("SOXL")
                        tm_state = _load_tm_state(self.conn)
                        tm_state = self.trade_mgr.on_realized_pnl(
                            "SOXS", realized,
                            EffectiveState.BEAR_ACTIVE, soxl_px, tm_state,
                        )
                        _persist_tm_state(self.conn, tm_state)

    # ------------------------------------------------------------------ #
    #  Kill / Resume handlers
    # ------------------------------------------------------------------ #

    def _handle_kill(self) -> None:
        set_emergency_stop(self.conn, True)
        self._cancel_all_open_orders()

    def _handle_resume(self) -> None:
        self._reconcile(is_startup=False)
        if not is_emergency_stop(self.conn):
            log.info("Resume after reconcile — already clean")
            return
        # If reconcile passed (no new emergency), clear stop
        # Note: _reconcile sets emergency_stop on mismatch,
        # so if we reach here and it's still True, reconcile was OK.
        set_emergency_stop(self.conn, False)
        if self.kill_sw:
            self.kill_sw.send_alert("✅ System resumed after successful reconcile")

    def _cancel_all_open_orders(self) -> None:
        open_orders = get_open_orders(self.conn)
        for order in open_orders:
            bid = order.get("broker_order_id")
            if bid and self.kiwoom:
                self.kiwoom.cancel_order(bid, order["symbol"], order["qty"])
            update_order(self.conn, order["id"], status="CANCELLED")
        self.conn.commit()
        log.info("Cancelled %d open orders", len(open_orders))

    # ------------------------------------------------------------------ #
    #  Price & data helpers (Kiwoom TR wrappers)
    # ------------------------------------------------------------------ #

    def _fetch_current_price(self, symbol: str) -> float:
        """Fetch last/current price for a symbol via Kiwoom TR.

        Returns 0.0 on failure.
        """
        # TODO(kiwoom): Use the correct TR code for 해외주식 현재가 조회
        # Example: 해외주식현재가 TR with appropriate inputs
        if not self.kiwoom:
            return 0.0
        resp = self.kiwoom.request_tr(
            trcode="hts/overseas",  # TODO(kiwoom): replace with actual TR
            rqname=f"PRICE_{symbol}",
            inputs={"종목코드": symbol},
            output_fields=["현재가"],
        )
        if resp.rows:
            try:
                return abs(float(resp.rows[0].get("현재가", "0")))
            except (ValueError, KeyError):
                pass
        log.warning("Failed to fetch price for %s", symbol)
        return 0.0

    def _regime_to_decision(self, regime: dict) -> DailyDecision:
        """Convert a regime_history DB row into a DailyDecision object."""
        from strategy_engine import EngineIntent
        return DailyDecision(
            date=pd.Timestamp(regime["date"]),
            close=regime.get("close", 0.0),
            sma20=regime.get("sma20", 0.0),
            sma50=regime.get("sma50", 0.0),
            sma200=regime.get("sma200", 0.0),
            indicator_L=bool(regime.get("indicator_L", 0)),
            indicator_M=bool(regime.get("indicator_M", 0)),
            indicator_S=bool(regime.get("indicator_S", 0)),
            score=regime.get("score", 0),
            return_3m=regime.get("return_3m", 0.0),
            return_12m=None,
            effective_state=EffectiveState(regime.get("effective_state", "NEUTRAL")),
            transition_active=bool(regime.get("transition_active", 0)),
            transition_day=regime.get("transition_day", 0),
            engine_intent=EngineIntent(regime.get("engine_intent", "NONE")),
        )


# ======================================================================= #
#  Entry point
# ======================================================================= #

def main() -> None:
    cfg = RuntimeConfig(
        # Override via env vars or a config file for production
        kiwoom_account=os.environ.get("KIWOOM_ACCOUNT", ""),
        telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    runtime = Runtime(cfg)
    runtime.start()


if __name__ == "__main__":
    main()
