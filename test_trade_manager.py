"""
test_trade_manager.py
=====================
Unit & integration tests for TradeManager.

Run with:  pytest test_trade_manager.py -v
"""
from __future__ import annotations

import copy
import math

import pandas as pd
import pytest

from strategy_engine import DailyDecision, EffectiveState, EngineIntent
from trade_manager import (
    OrderIntent,
    OrderSide,
    PositionInfo,
    TradeManager,
    TradeManagerConfig,
    TradeManagerState,
)

# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _decision(
    state: EffectiveState = EffectiveState.BULL_ACTIVE,
    score: int = 3,
    transition_active: bool = False,
    transition_day: int = 0,
    date: str = "2024-06-01",
    close: float = 100.0,
) -> DailyDecision:
    """Build a minimal DailyDecision for testing."""
    intent_map = {
        EffectiveState.BULL_ACTIVE: EngineIntent.SOXL,
        EffectiveState.BEAR_ACTIVE: EngineIntent.SOXS,
        EffectiveState.NEUTRAL: EngineIntent.NONE,
        EffectiveState.TRANSITION: EngineIntent.NONE,
    }
    return DailyDecision(
        date=pd.Timestamp(date), close=close,
        sma20=close, sma50=close, sma200=close,
        indicator_L=True, indicator_M=True, indicator_A=True,
        score=score, return_3m=0.0, return_12m=None,
        effective_state=state,
        transition_active=transition_active,
        transition_day=transition_day,
        engine_intent=intent_map[state],
    )


def _state_with_soxl(qty=100, avg_cost=50.0, slices=5, max_price=55.0) -> TradeManagerState:
    return TradeManagerState(
        soxl=PositionInfo(qty=qty, avg_cost=avg_cost,
                          entry_date=pd.Timestamp("2024-01-01"),
                          allocated_capital=avg_cost * qty),
        soxl_max_price=max_price,
        soxl_slices_used=slices,
    )


def _state_with_soxs(qty=50, avg_cost=30.0, slices=3, holding_days=5) -> TradeManagerState:
    return TradeManagerState(
        soxs=PositionInfo(qty=qty, avg_cost=avg_cost,
                          entry_date=pd.Timestamp("2024-03-01"),
                          allocated_capital=avg_cost * qty),
        soxs_slices_used=slices,
        soxs_holding_days=holding_days,
    )


@pytest.fixture
def mgr() -> TradeManager:
    return TradeManager()


# ===================================================================== #
#  A) SOXL buy — slice-based accumulation
# ===================================================================== #

class TestSoxlBuy:

    def test_bull_active_generates_buy(self, mgr):
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, TradeManagerState())
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert len(buys) == 1
        assert buys[0].slices == 1  # default 1 slice

    def test_neutral_no_buy(self, mgr):
        dec = _decision(state=EffectiveState.NEUTRAL, score=2)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, TradeManagerState())
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert buys == []

    def test_bear_no_soxl_buy(self, mgr):
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, TradeManagerState())
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert buys == []

    def test_max_slices_cap(self, mgr):
        st = TradeManagerState(soxl_slices_used=mgr.cfg.soxl_max_slices)
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert buys == []

    def test_avg_down_2_slices(self, mgr):
        st = _state_with_soxl(qty=100, avg_cost=50.0)
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        price = 50.0 * 0.91  # -9 %, past thresh_1 (-8 %)
        intents, _ = mgr.process_day(dec, price, 10.0, 100_000, st)
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert len(buys) == 1
        assert buys[0].slices == 2

    def test_avg_down_3_slices(self, mgr):
        st = _state_with_soxl(qty=100, avg_cost=50.0)
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        price = 50.0 * 0.84  # -16 %, past thresh_2 (-15 %)
        intents, _ = mgr.process_day(dec, price, 10.0, 100_000, st)
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert buys[0].slices == 3

    def test_one_buy_per_day(self, mgr):
        """Only one SOXL BUY intent per process_day call."""
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, TradeManagerState())
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert len(buys) == 1

    def test_notional_includes_injection(self, mgr):
        st = TradeManagerState(injection_budget=500.0)
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        capital = 100_000
        slice_size = capital / mgr.cfg.soxl_max_slices
        intents, new_st = mgr.process_day(dec, 50.0, 10.0, capital, st)
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert buys[0].notional == pytest.approx(slice_size + 500.0, rel=1e-6)
        assert new_st.injection_budget == 0.0
        assert "VAMPIRE_INJECT" in buys[0].reason


# ===================================================================== #
#  B) SOXL trailing stop — peak definition
# ===================================================================== #

class TestSoxlTrailingStop:

    def test_no_trigger_above_threshold(self, mgr):
        st = _state_with_soxl(max_price=100.0)
        dec = _decision()
        intents, _ = mgr.process_day(dec, 90.0, 10.0, 100_000, st)  # -10 %
        sells = [i for i in intents if i.reason.startswith("TRAILING")]
        assert sells == []

    def test_stage0_to_1_sell_50pct(self, mgr):
        st = _state_with_soxl(qty=100, max_price=100.0)
        dec = _decision()
        intents, new_st = mgr.process_day(dec, 84.0, 10.0, 100_000, st)  # -16 %
        sells = [i for i in intents if i.reason == "TRAILING_STOP_50PCT"]
        assert len(sells) == 1
        assert sells[0].qty == 50
        assert new_st.soxl_trailing_stage == 1

    def test_stage0_to_2_sell_all(self, mgr):
        st = _state_with_soxl(qty=100, max_price=100.0)
        dec = _decision()
        intents, new_st = mgr.process_day(dec, 74.0, 10.0, 100_000, st)  # -26 %
        sells = [i for i in intents if i.reason == "TRAILING_STOP_ALL"]
        assert len(sells) == 1
        assert sells[0].qty == 100
        assert new_st.soxl_trailing_stage == 2

    def test_stage1_to_2(self, mgr):
        st = _state_with_soxl(qty=50, max_price=100.0)
        st.soxl_trailing_stage = 1
        dec = _decision()
        intents, new_st = mgr.process_day(dec, 74.0, 10.0, 100_000, st)
        sells = [i for i in intents if i.reason == "TRAILING_STOP_ALL"]
        assert len(sells) == 1
        assert new_st.soxl_trailing_stage == 2

    def test_stage2_no_more_sells(self, mgr):
        st = _state_with_soxl(qty=100, max_price=100.0)
        st.soxl_trailing_stage = 2
        dec = _decision()
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        sells = [i for i in intents if i.reason.startswith("TRAILING")]
        assert sells == []

    def test_max_price_updated(self, mgr):
        st = _state_with_soxl(max_price=90.0)
        dec = _decision()
        _, new_st = mgr.process_day(dec, 95.0, 10.0, 100_000, st)
        assert new_st.soxl_max_price == 95.0

    # --- B) Peak persists across partial sells --- #
    def test_peak_persists_after_partial_sell(self, mgr):
        """After a partial trailing sell (stage 1), the peak does not reset."""
        st = _state_with_soxl(qty=100, max_price=100.0)
        dec = _decision()
        # Trigger stage 1 sell
        intents, new_st = mgr.process_day(dec, 84.0, 10.0, 100_000, st)
        assert new_st.soxl_max_price == 100.0  # peak unchanged
        assert new_st.soxl_trailing_stage == 1

    # --- B) Adding slices does NOT reset peak --- #
    def test_slice_buy_does_not_reset_peak(self, mgr):
        """Buying additional slices must NOT reset max_price."""
        st = _state_with_soxl(qty=100, avg_cost=50.0, max_price=60.0, slices=5)
        # Simulate a buy fill
        new_st = mgr.apply_fill("SOXL", OrderSide.BUY, 50, 45.0,
                                pd.Timestamp("2024-06-01"), st)
        # Peak should still be 60.0, not reset to 45.0
        assert new_st.soxl_max_price == 60.0
        assert new_st.soxl.qty == 150

    # --- B) Full exit resets peak and stage --- #
    def test_full_exit_resets_peak_and_stage(self, mgr):
        st = _state_with_soxl(qty=100, max_price=120.0, slices=10)
        st.soxl_trailing_stage = 1
        new_st = mgr.apply_fill("SOXL", OrderSide.SELL, 100, 80.0,
                                pd.Timestamp("2024-06-01"), st)
        assert new_st.soxl_max_price == 0.0
        assert new_st.soxl_trailing_stage == 0

    # --- B) Stage transitions are one-shot --- #
    def test_stage_transitions_do_not_refire(self, mgr):
        """Once stage is 1, hitting -15% again does NOT re-fire."""
        st = _state_with_soxl(qty=50, max_price=100.0)
        st.soxl_trailing_stage = 1
        dec = _decision()
        # Price at -16% again — should NOT fire another stage 1 sell
        intents, new_st = mgr.process_day(dec, 84.0, 10.0, 100_000, st)
        sells = [i for i in intents if i.reason == "TRAILING_STOP_50PCT"]
        assert sells == []
        assert new_st.soxl_trailing_stage == 1  # stays at 1, not re-fired


# ===================================================================== #
#  C) SOXS sub-engine + cooldown
# ===================================================================== #

class TestSoxsBuy:

    def test_bear_generates_soxs_buy(self, mgr):
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, TradeManagerState())
        buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert len(buys) == 1

    def test_bull_no_soxs_buy(self, mgr):
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, TradeManagerState())
        buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []

    def test_alloc_cap(self, mgr):
        cap = 100_000 * mgr.cfg.soxs_alloc_cap_ratio  # 30 000
        st = TradeManagerState(
            soxs=PositionInfo(qty=100, avg_cost=300.0,
                              allocated_capital=cap,
                              entry_date=pd.Timestamp("2024-03-01")),
        )
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []

    def test_cooldown_blocks_soxs_buy(self, mgr):
        """During cooldown after forced close, SOXS buys are blocked."""
        st = TradeManagerState(soxs_cooldown_remaining=2)
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, new_st = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []
        # Cooldown ticks down by 1
        assert new_st.soxs_cooldown_remaining == 1

    def test_cooldown_expires_allows_buy(self, mgr):
        """After cooldown expires, SOXS buys are allowed again."""
        st = TradeManagerState(soxs_cooldown_remaining=1)
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        # Day 1: cooldown ticks 1→0, but still blocks (checked before tick)
        intents, new_st = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []
        assert new_st.soxs_cooldown_remaining == 0

        # Day 2: cooldown at 0, buy should be allowed
        intents2, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, new_st)
        buys2 = [i for i in intents2 if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert len(buys2) == 1


class TestSoxsExits:

    def test_take_profit(self, mgr):
        st = _state_with_soxs(qty=50, avg_cost=30.0)
        tp_price = 30.0 * (1 + mgr.cfg.soxs_take_profit + 0.01)
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, _ = mgr.process_day(dec, 50.0, tp_price, 100_000, st)
        tp = [i for i in intents if i.reason == "TAKE_PROFIT"]
        assert len(tp) == 1
        assert tp[0].qty == 50

    def test_max_holding_exit(self, mgr):
        st = _state_with_soxs(holding_days=24)  # will increment to 25
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, new_st = mgr.process_day(dec, 50.0, 30.0, 100_000, st)
        exits = [i for i in intents if i.reason == "MAX_HOLDING_EXIT"]
        assert len(exits) == 1
        # Cooldown should be set
        assert new_st.soxs_cooldown_remaining == mgr.cfg.soxs_cooldown_days
        assert new_st.soxs_forced_close is True

    def test_max_holding_then_cooldown_blocks_rebuy(self, mgr):
        """Full scenario: day 25 forced sell → next days blocked → then allowed."""
        # Day 25: forced sell
        st = _state_with_soxs(qty=50, holding_days=24)
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, new_st = mgr.process_day(dec, 50.0, 30.0, 100_000, st)
        assert any(i.reason == "MAX_HOLDING_EXIT" for i in intents)
        assert new_st.soxs_cooldown_remaining == 3

        # Simulate full exit via apply_fill
        new_st = mgr.apply_fill("SOXS", OrderSide.SELL, 50, 30.0,
                                pd.Timestamp("2024-06-01"), new_st)
        assert new_st.soxs.qty == 0

        # Day 26: cooldown 3→2, blocked
        intents2, st2 = mgr.process_day(dec, 50.0, 10.0, 100_000, new_st)
        buys = [i for i in intents2 if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []
        assert st2.soxs_cooldown_remaining == 2

        # Day 27: cooldown 2→1, blocked
        intents3, st3 = mgr.process_day(dec, 50.0, 10.0, 100_000, st2)
        buys = [i for i in intents3 if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []
        assert st3.soxs_cooldown_remaining == 1

        # Day 28: cooldown 1→0, blocked (checked before tick)
        intents4, st4 = mgr.process_day(dec, 50.0, 10.0, 100_000, st3)
        buys = [i for i in intents4 if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert buys == []
        assert st4.soxs_cooldown_remaining == 0

        # Day 29: cooldown = 0, buy allowed!
        intents5, _ = mgr.process_day(dec, 50.0, 10.0, 100_000, st4)
        buys = [i for i in intents5 if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert len(buys) == 1

    def test_loss_cut_50pct(self, mgr):
        st = _state_with_soxs(qty=50, avg_cost=30.0)
        bad_price = 30.0 * (1 + mgr.cfg.soxs_loss_cut_1 - 0.01)
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, new_st = mgr.process_day(dec, 50.0, bad_price, 100_000, st)
        lc = [i for i in intents if i.reason == "LOSS_CUT_50PCT"]
        assert len(lc) == 1
        assert lc[0].qty == 25
        assert new_st.soxs_loss_cut_stage == 1

    def test_loss_cut_all(self, mgr):
        st = _state_with_soxs(qty=50, avg_cost=30.0)
        bad_price = 30.0 * (1 + mgr.cfg.soxs_loss_cut_2 - 0.01)
        dec = _decision(state=EffectiveState.BEAR_ACTIVE, score=0)
        intents, new_st = mgr.process_day(dec, 50.0, bad_price, 100_000, st)
        lc = [i for i in intents if i.reason == "LOSS_CUT_ALL"]
        assert len(lc) == 1
        assert lc[0].qty == 50
        assert new_st.soxs_loss_cut_stage == 2


# ===================================================================== #
#  D) Transition swap (3-day)
# ===================================================================== #

class TestTransition:

    def test_day1_soxl_buy_1_slice_no_soxs_buy(self, mgr):
        dec = _decision(state=EffectiveState.TRANSITION,
                        transition_active=True, transition_day=1)
        st = _state_with_soxs(qty=50)
        intents, _ = mgr.process_day(dec, 50.0, 30.0, 100_000, st)
        soxl_buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        soxs_buys = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.BUY]
        assert len(soxl_buys) == 1
        assert soxl_buys[0].slices == 1
        assert soxs_buys == []

    def test_day2_soxs_sell_50pct_and_extra_soxl(self, mgr):
        dec = _decision(state=EffectiveState.TRANSITION,
                        transition_active=True, transition_day=2)
        st = _state_with_soxs(qty=50)
        intents, _ = mgr.process_day(dec, 50.0, 30.0, 100_000, st)
        soxs_sells = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.SELL]
        soxl_buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        assert any(i.reason == "TRANSITION_SELL_50PCT" for i in soxs_sells)
        assert len(soxl_buys) == 1
        assert soxl_buys[0].slices == 1 + mgr.cfg.transition_extra_slices_day2

    def test_day3_soxs_sell_all(self, mgr):
        dec = _decision(state=EffectiveState.TRANSITION,
                        transition_active=True, transition_day=3)
        st = _state_with_soxs(qty=50)
        intents, _ = mgr.process_day(dec, 50.0, 30.0, 100_000, st)
        soxs_sells = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.SELL]
        assert any(i.reason == "TRANSITION_SELL_ALL" for i in soxs_sells)
    def test_take_profit_prevents_duplicate_transition_sell(self, mgr):
        dec = _decision(state=EffectiveState.TRANSITION,
                        transition_active=True, transition_day=3)
        st = _state_with_soxs(qty=50, avg_cost=30.0)
        tp_price = 30.0 * (1 + mgr.cfg.soxs_take_profit + 0.01)
        intents, _ = mgr.process_day(dec, 50.0, tp_price, 100_000, st)
        soxs_sells = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.SELL]
        assert len(soxs_sells) == 1
        assert soxs_sells[0].reason == "TAKE_PROFIT"




# ===================================================================== #
#  E) Vampire rebalance — dynamic ratio + cap
# ===================================================================== #

class TestVampireRebalance:

    def test_injection_at_40pct_drawdown(self, mgr):
        """dd = -45% (between -40% and -50%) → ratio = 0.70."""
        st = _state_with_soxl(qty=100, avg_cost=100.0)
        soxl_price = 55.0  # -45 % drawdown
        new_st = mgr.on_realized_pnl(
            "SOXS", 1000.0, EffectiveState.BEAR_ACTIVE, soxl_price, st,
        )
        expected = 1000.0 * mgr.cfg.vampire_inject_ratio_normal  # 0.70
        assert new_st.injection_budget == pytest.approx(expected)

    def test_injection_at_50pct_drawdown(self, mgr):
        """dd = -55% (past -50%) → ratio = 0.50."""
        st = _state_with_soxl(qty=100, avg_cost=100.0)
        soxl_price = 45.0  # -55 % drawdown
        new_st = mgr.on_realized_pnl(
            "SOXS", 1000.0, EffectiveState.BEAR_ACTIVE, soxl_price, st,
        )
        expected = 1000.0 * mgr.cfg.vampire_inject_ratio_deep  # 0.50
        assert new_st.injection_budget == pytest.approx(expected)

    def test_no_injection_if_bull(self, mgr):
        st = _state_with_soxl(avg_cost=100.0)
        new_st = mgr.on_realized_pnl(
            "SOXS", 1000.0, EffectiveState.BULL_ACTIVE, 55.0, st,
        )
        assert new_st.injection_budget == 0.0

    def test_no_injection_if_soxl_not_deep(self, mgr):
        st = _state_with_soxl(avg_cost=100.0)
        new_st = mgr.on_realized_pnl(
            "SOXS", 1000.0, EffectiveState.BEAR_ACTIVE, 80.0, st,
        )
        assert new_st.injection_budget == 0.0  # only -20 %, thresh is -40 %

    def test_no_injection_on_loss(self, mgr):
        st = _state_with_soxl(avg_cost=100.0)
        new_st = mgr.on_realized_pnl(
            "SOXS", -500.0, EffectiveState.BEAR_ACTIVE, 55.0, st,
        )
        assert new_st.injection_budget == 0.0

    def test_injection_capped_by_remaining_slices(self, mgr):
        """If all slices used, injection should be zero."""
        st = _state_with_soxl(qty=100, avg_cost=100.0, slices=35)  # max
        soxl_price = 55.0  # -45%
        new_st = mgr.on_realized_pnl(
            "SOXS", 10000.0, EffectiveState.BEAR_ACTIVE, soxl_price, st,
        )
        assert new_st.injection_budget == 0.0  # no remaining slices

    def test_injection_budget_persists_with_cap(self, mgr):
        """Sequential injections never exceed remaining-slice cap."""
        st = _state_with_soxl(qty=100, avg_cost=100.0)
        soxl_price = 55.0
        cap = (mgr.cfg.soxl_max_slices - st.soxl_slices_used) * mgr.cfg.soxl_slice_notional
        st1 = mgr.on_realized_pnl(
            "SOXS", 50_000.0, EffectiveState.BEAR_ACTIVE, soxl_price, st,
        )
        st2 = mgr.on_realized_pnl(
            "SOXS", 50_000.0, EffectiveState.BEAR_ACTIVE, soxl_price, st1,
        )
        assert st1.injection_budget <= cap
        assert st2.injection_budget == pytest.approx(cap)

    def test_injection_drain_during_buy(self, mgr):
        """Injection budget properly consumed during SOXL buy."""
        capital = 100_000
        slice_size = capital / mgr.cfg.soxl_max_slices
        st = TradeManagerState(injection_budget=2000.0)
        dec = _decision(state=EffectiveState.BULL_ACTIVE)
        intents, new_st = mgr.process_day(dec, 50.0, 10.0, capital, st)
        buys = [i for i in intents if i.symbol == "SOXL" and i.side == OrderSide.BUY]
        # Notional should include injection
        assert buys[0].notional == pytest.approx(slice_size + 2000.0, rel=1e-6)
        assert new_st.injection_budget == pytest.approx(0.0, abs=0.01)


# ===================================================================== #
#  apply_fill
# ===================================================================== #

class TestApplyFill:

    def test_buy_fill_updates_position(self, mgr):
        st = TradeManagerState()
        new = mgr.apply_fill("SOXL", OrderSide.BUY, 10, 50.0,
                             pd.Timestamp("2024-06-01"), st)
        assert new.soxl.qty == 10
        assert new.soxl.avg_cost == 50.0
        assert new.soxl.allocated_capital == 500.0
        assert new.soxl_max_price == 50.0

    def test_buy_avg_cost(self, mgr):
        st = TradeManagerState(
            soxl=PositionInfo(qty=10, avg_cost=50.0,
                              entry_date=pd.Timestamp("2024-01-01"),
                              allocated_capital=500.0),
        )
        new = mgr.apply_fill("SOXL", OrderSide.BUY, 10, 60.0,
                             pd.Timestamp("2024-06-02"), st)
        assert new.soxl.qty == 20
        assert new.soxl.avg_cost == pytest.approx(55.0)

    def test_sell_fill_reduces_position(self, mgr):
        st = _state_with_soxl(qty=100)
        new = mgr.apply_fill("SOXL", OrderSide.SELL, 50, 55.0,
                             pd.Timestamp("2024-06-01"), st)
        assert new.soxl.qty == 50

    def test_sell_all_resets(self, mgr):
        st = _state_with_soxl(qty=100, slices=10, max_price=60.0)
        st.soxl_trailing_stage = 1
        new = mgr.apply_fill("SOXL", OrderSide.SELL, 100, 55.0,
                             pd.Timestamp("2024-06-01"), st)
        assert new.soxl.qty == 0
        assert new.soxl_max_price == 0.0
        assert new.soxl_trailing_stage == 0
        assert new.soxl_slices_used == 0

    def test_soxs_sell_all_resets(self, mgr):
        st = _state_with_soxs(qty=50, slices=5, holding_days=10)
        st.soxs_loss_cut_stage = 1
        new = mgr.apply_fill("SOXS", OrderSide.SELL, 50, 35.0,
                             pd.Timestamp("2024-06-01"), st)
        assert new.soxs.qty == 0
        assert new.soxs_holding_days == 0
        assert new.soxs_loss_cut_stage == 0
        assert new.soxs_slices_used == 0

    def test_soxs_sell_all_preserves_cooldown(self, mgr):
        """Cooldown should NOT be reset when position is closed."""
        st = _state_with_soxs(qty=50)
        st.soxs_cooldown_remaining = 3
        new = mgr.apply_fill("SOXS", OrderSide.SELL, 50, 35.0,
                             pd.Timestamp("2024-06-01"), st)
        assert new.soxs.qty == 0
        # Cooldown persists — it ticks down in _update_tracking
        assert new.soxs_cooldown_remaining == 3


# ===================================================================== #
#  Sell capping / deduplication
# ===================================================================== #

class TestSellCapping:

    def test_cap_prevents_oversell(self, mgr):
        st = _state_with_soxs(qty=50, avg_cost=30.0)
        tp_price = 30.0 * 1.10  # above TP
        dec = _decision(state=EffectiveState.TRANSITION,
                        transition_active=True, transition_day=3)
        intents, _ = mgr.process_day(dec, 50.0, tp_price, 100_000, st)
        soxs_sells = [i for i in intents if i.symbol == "SOXS" and i.side == OrderSide.SELL]
        total_sell_qty = sum(i.qty for i in soxs_sells)
        assert total_sell_qty <= 50


# ===================================================================== #
#  Determinism & immutability
# ===================================================================== #

class TestDeterminism:

    def test_no_input_state_mutation(self, mgr):
        st = _state_with_soxl()
        original = copy.deepcopy(st)
        dec = _decision()
        mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        assert st.soxl.qty == original.soxl.qty
        assert st.soxl_slices_used == original.soxl_slices_used

    def test_same_input_same_output(self, mgr):
        st = _state_with_soxl()
        dec = _decision()
        i1, s1 = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        i2, s2 = mgr.process_day(dec, 50.0, 10.0, 100_000, st)
        assert len(i1) == len(i2)
        for a, b in zip(i1, i2):
            assert a == b

    def test_order_intent_frozen(self):
        oi = OrderIntent("SOXL", OrderSide.BUY, 0, 1000.0,
                         "MARKET", None, "TEST", 60, 1)
        with pytest.raises(AttributeError):
            oi.qty = 99  # type: ignore[misc]
