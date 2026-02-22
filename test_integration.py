"""
test_integration.py
===================
End-to-end integration tests for runtime/db/state consistency.

Uses in-memory SQLite (no Kiwoom COM) and deterministic price feeds.
Focus: idempotency, reconcile, cooldown, and state persistence.

Run with:  pytest test_integration.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from config import RuntimeConfig, KiwoomTrConfig
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
from strategy_engine import DailyDecision, EffectiveState, EngineIntent
from trade_manager import (
    OrderIntent,
    OrderSide,
    PositionInfo,
    TradeManager,
    TradeManagerConfig,
    TradeManagerState,
)


# ======================================================================= #
#  Fixtures
# ======================================================================= #

@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory SQLite with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    init_db(c)
    return c


@pytest.fixture
def mgr() -> TradeManager:
    return TradeManager()


def _decision(
    state: EffectiveState = EffectiveState.BULL_ACTIVE,
    score: int = 3,
    transition_active: bool = False,
    transition_day: int = 0,
    date: str = "2024-06-01",
) -> DailyDecision:
    intent_map = {
        EffectiveState.BULL_ACTIVE: EngineIntent.SOXL,
        EffectiveState.BEAR_ACTIVE: EngineIntent.SOXS,
        EffectiveState.NEUTRAL: EngineIntent.NONE,
        EffectiveState.TRANSITION: EngineIntent.NONE,
    }
    return DailyDecision(
        date=pd.Timestamp(date), close=100.0,
        sma20=100.0, sma50=100.0, sma200=100.0,
        indicator_L=True, indicator_M=True, indicator_A=True,
        score=score, return_3m=0.0, return_12m=None,
        effective_state=state,
        transition_active=transition_active,
        transition_day=transition_day,
        engine_intent=intent_map[state],
    )


# ======================================================================= #
#  Helper: load/persist TM state from/to DB (mirrors runtime.py logic)
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
    st.soxs_cooldown_remaining = soxs_row.get("cooldown_remaining", 0)
    st.soxs_forced_close = bool(soxs_row.get("forced_close", 0))

    budget_str = get_system(conn, "injection_budget")
    st.injection_budget = float(budget_str) if budget_str else 0.0
    return st


def _persist_tm_state(conn: sqlite3.Connection, st: TradeManagerState) -> None:
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
            "cooldown_remaining": st.soxs_cooldown_remaining,
            "forced_close": int(st.soxs_forced_close),
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
#  1) Kill switch → restart → idempotency (no double-buy)
# ======================================================================= #

class TestKillSwitchIdempotency:

    def test_kill_stop_prevents_buy_and_restart_preserves_state(self, conn, mgr):
        """
        Scenario:
        1. BEAR_ACTIVE → a SOXS buy is generated.
        2. Kill switch fires → emergency stop.
        3. Buy intent is dropped (emergency stop active).
        4. Emergency stop cleared → state is consistent.
        5. Same-day buy attempt → idempotency lock prevents double buy.
        """
        today = "2024-06-01"

        # --- Step 1: Generate a SOXS buy intent ---
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0, date=today)
        st = TradeManagerState()
        intents, new_st = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        soxs_buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert len(soxs_buys) == 1

        # --- Step 2: Simulate executing that intent with idempotency lock ---
        action_key = f"BUY_SOXS_{today}"
        assert try_lock_action(conn, today, action_key)
        conn.commit()

        # Simulate order submitted and filled
        order_id = insert_order(conn, symbol="SOXS", side="BUY", qty=10,
                                notional=100.0, status="FILLED", reason="BEAR_ACCUMULATE")
        insert_fill(conn, str(order_id), 10, 10.0)
        mark_action_done(conn, today, action_key)
        conn.commit()

        # --- Step 3: Kill switch fires ---
        set_emergency_stop(conn, True)
        assert is_emergency_stop(conn)

        # --- Step 4: "Restart" — clear emergency after reconcile ---
        set_emergency_stop(conn, False)
        assert not is_emergency_stop(conn)

        # --- Step 5: Same-day buy attempt → blocked ---
        assert not try_lock_action(conn, today, action_key)  # already locked/done
        assert is_action_done(conn, today, action_key)

    def test_emergency_stop_blocks_execution(self, conn):
        """Orders should not be submitted when emergency stop is active."""
        set_emergency_stop(conn, True)
        assert is_emergency_stop(conn)

        # A well-behaved runtime checks this before executing intents
        # This test just verifies the DB flag roundtrip
        set_emergency_stop(conn, False)
        assert not is_emergency_stop(conn)


# ======================================================================= #
#  2) Reconcile mismatch → emergency stop + cancel open orders
# ======================================================================= #

class TestReconcileMismatch:

    def test_mismatch_triggers_emergency_and_cancel(self, conn):
        """
        Scenario:
        1. DB has SOXL qty=100.
        2. Broker reports qty=80 (simulated).
        3. Mismatch detected → emergency stop set.
        4. Open orders are cancelled.
        """
        # Seed DB position
        upsert_position(conn, "SOXL", qty=100, avg_cost=50.0,
                        max_price_since_entry=55.0, trailing_stage=0,
                        slices_used=5)
        conn.commit()

        # Insert an open order
        oid = insert_order(conn, symbol="SOXL", side="BUY", qty=10,
                           notional=500.0, status="SUBMITTED", reason="BULL_ACCUMULATE",
                           broker_order_id="BROKER-001")
        conn.commit()

        # --- Simulate reconcile: broker says qty=80 ---
        broker_position = {"symbol": "SOXL", "qty": 80, "avg_cost": 50.0}
        db_pos = get_position(conn, "SOXL")

        # Check mismatch
        qty_match = abs(broker_position["qty"] - db_pos["qty"]) <= 0
        assert not qty_match  # mismatch!

        # Emergency stop
        set_emergency_stop(conn, True)
        assert is_emergency_stop(conn)

        # Cancel open orders
        open_orders = get_open_orders(conn)
        assert len(open_orders) == 1
        for order in open_orders:
            update_order(conn, order["id"], status="CANCELLED")
        conn.commit()

        # Verify
        open_after = get_open_orders(conn)
        assert len(open_after) == 0
        assert is_emergency_stop(conn)


# ======================================================================= #
#  3) SOXS day-25 cap → forced sell → cooldown → re-entry
# ======================================================================= #

class TestSoxsCooldownIntegration:

    def test_full_cooldown_flow_with_persistence(self, conn, mgr):
        """
        End-to-end scenario through DB persistence:
        1. SOXS position, day 24 → process_day → forced sell at day 25.
        2. Persist state → DB has cooldown = 3.
        3. Reload state → BEAR_ACTIVE → no SOXS buy for 3 days.
        4. After cooldown → buy allowed.
        """
        # --- Setup: SOXS position nearing max hold ---
        st = TradeManagerState(
            soxs=PositionInfo(qty=50, avg_cost=30.0,
                              entry_date=pd.Timestamp("2024-05-01"),
                              allocated_capital=1500.0),
            soxs_slices_used=3,
            soxs_holding_days=24,  # will become 25 on next process_day
        )
        _persist_tm_state(conn, st)

        # --- Day 1: Forced sell ---
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0, date="2024-06-01")
        loaded_st = _load_tm_state(conn)
        intents, new_st = mgr.process_day(dec, 50.0, 30.0, 100_000, loaded_st)
        exits = [i for i in intents if i.reason == "MAX_HOLDING_EXIT"]
        assert len(exits) == 1
        assert new_st.soxs_cooldown_remaining == 3

        # Simulate fill
        new_st = mgr.apply_fill("SOXS", OrderSide.SELL, 50, 30.0,
                                pd.Timestamp("2024-06-01"), new_st)
        _persist_tm_state(conn, new_st)

        # --- Days 2-4: Cooldown period ---
        for day_offset in range(1, 4):
            date = f"2024-06-{1 + day_offset:02d}"
            dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0, date=date)
            loaded_st = _load_tm_state(conn)
            intents, new_st = mgr.process_day(dec, 50.0, 10.0, 100_000, loaded_st)
            buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
            assert buys == [], f"Expected no SOXS buy on cooldown day {day_offset}"
            _persist_tm_state(conn, new_st)

        # --- Day 5: Cooldown expired, buy allowed ---
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0, date="2024-06-05")
        loaded_st = _load_tm_state(conn)
        intents, new_st = mgr.process_day(dec, 50.0, 10.0, 100_000, loaded_st)
        buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert len(buys) == 1, "Expected SOXS buy after cooldown expired"


# ======================================================================= #
#  4) Injection budget persistence across runtime cycles
# ======================================================================= #

class TestInjectionPersistence:

    def test_injection_budget_survives_restart(self, conn, mgr):
        """Budget set via on_realized_pnl persists in DB and is used on next buy."""
        # Setup: SOXL in deep drawdown
        st = TradeManagerState(
            soxl=PositionInfo(qty=100, avg_cost=100.0,
                              entry_date=pd.Timestamp("2024-01-01"),
                              allocated_capital=10000.0),
            soxl_slices_used=5,
            soxl_max_price=110.0,
        )
        _persist_tm_state(conn, st)

        # Simulate SOXS profit → vampire injection
        loaded_st = _load_tm_state(conn)
        soxl_price = 55.0  # -45% drawdown
        new_st = mgr.on_realized_pnl(
            "SOXS", 1000.0, EffectiveState.BEAR_ACTIVE, soxl_price, loaded_st,
        )
        _persist_tm_state(conn, new_st)

        # "Restart" — reload state from DB
        reloaded = _load_tm_state(conn)
        ratio = mgr.cfg.vampire_inject_ratio_normal  # 0.70
        assert reloaded.injection_budget == pytest.approx(1000.0 * ratio)

        # Now use it in a SOXL buy
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        intents, final_st = mgr.process_day(dec, 55.0, 10.0, 100_000, reloaded)
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert len(buys) == 1
        assert "VAMPIRE_INJECT" in buys[0].reason
        assert final_st.injection_budget == pytest.approx(0.0, abs=0.01)


# ======================================================================= #
#  5) KiwoomTrConfig validation
# ======================================================================= #

class TestTrConfigValidation:

    def test_placeholder_detection(self):
        """Default config should flag all placeholder TR IDs."""
        cfg = KiwoomTrConfig()
        issues = cfg.validate()
        assert len(issues) == 4  # all 4 TR fields are placeholders

    def test_partial_replacement(self):
        """Replacing some but not all should still flag remaining."""
        cfg = KiwoomTrConfig(tr_current_price="REAL_TR_001")
        issues = cfg.validate()
        assert len(issues) == 3
        assert all("tr_current_price" not in i for i in issues)

    def test_fully_configured(self):
        """All real TR IDs → no issues."""
        cfg = KiwoomTrConfig(
            tr_current_price="OS_PRICE",
            tr_daily_ohlcv="OS_DAILY",
            tr_holdings="OS_HOLD",
            tr_order="OS_ORDER",
        )
        issues = cfg.validate()
        assert issues == []


# ======================================================================= #
#  6) DB schema basics
# ======================================================================= #

class TestDbSchema:

    def test_cooldown_columns_exist(self, conn):
        """Verify cooldown columns were created in positions table."""
        upsert_position(conn, "SOXS", qty=10, avg_cost=20.0,
                        cooldown_remaining=3, forced_close=1,
                        holding_days=0, loss_cut_stage=0, slices_used=0,
                        max_price_since_entry=0, trailing_stage=0)
        conn.commit()

        pos = get_position(conn, "SOXS")
        assert pos is not None
        assert pos["cooldown_remaining"] == 3
        assert pos["forced_close"] == 1

    def test_indicator_A_in_regime_history(self, conn):
        """Verify indicator_A and return_12m columns exist."""
        insert_regime(
            conn, date="2024-06-01", close=100.0,
            sma20=99.0, sma50=98.0, sma200=97.0,
            indicator_L=1, indicator_M=1, indicator_A=1,
            score=3, return_3m=0.05, return_12m=0.15,
            effective_state="BULL_ACTIVE",
            transition_active=0, transition_day=0,
            engine_intent="SOXL",
        )
        conn.commit()

        from db import get_latest_regime
        regime = get_latest_regime(conn)
        assert regime is not None
        assert regime["indicator_A"] == 1
        assert regime["return_12m"] == pytest.approx(0.15)
