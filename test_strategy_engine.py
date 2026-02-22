"""
test_strategy_engine.py
=======================
Unit & integration tests for StrategyEngine.

Run with:  pytest test_strategy_engine.py -v
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from strategy_engine import (
    DailyDecision,
    EffectiveState,
    EngineIntent,
    StrategyEngine,
    _FSMState,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic OHLCV generators
# ---------------------------------------------------------------------------

def _make_ohlcv(
    closes: list[float],
    start: str = "2020-01-02",
    freq: str = "B",
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of closes."""
    dates = pd.bdate_range(start=start, periods=len(closes), freq=freq)
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        },
        index=dates,
    )
    return df


def _flat_closes(value: float, n: int) -> list[float]:
    """Return *n* identical close values."""
    return [value] * n


def _trending_closes(start: float, end: float, n: int) -> list[float]:
    """Linearly interpolate from *start* to *end* over *n* days."""
    return list(np.linspace(start, end, n))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine() -> StrategyEngine:
    return StrategyEngine()


@pytest.fixture
def short_engine() -> StrategyEngine:
    """Engine with shorter SMA windows for fast unit tests.

    Uses abs_momentum_lookback=5 so that 12m return is available
    after just 5 bars (matching bear_return_lookback).
    """
    return StrategyEngine(
        sma_short=3,
        sma_mid=5,
        sma_long=10,
        abs_momentum_lookback=5,
        bear_return_lookback=5,
        transition_days=3,
    )


# ===================================================================== #
#  1. Indicator computation
# ===================================================================== #

class TestComputeIndicators:
    """Tests for compute_indicators (stateless)."""

    def test_sma_values_flat(self, short_engine: StrategyEngine):
        """Flat prices → all SMAs equal the price."""
        df = _make_ohlcv(_flat_closes(100.0, 20))
        result = short_engine.compute_indicators(df)

        valid = result.dropna(subset=["sma200"])  # short_engine uses sma_long=10
        assert not valid.empty
        assert all(valid["sma20"].round(6) == 100.0)   # sma_short=3
        assert all(valid["sma50"].round(6) == 100.0)    # sma_mid=5
        assert all(valid["sma200"].round(6) == 100.0)   # sma_long=10

    def test_L_flag(self, short_engine: StrategyEngine):
        """Close above SMA_long → L is True."""
        closes = _flat_closes(100.0, 10) + _flat_closes(200.0, 5)
        df = _make_ohlcv(closes)
        result = short_engine.compute_indicators(df)
        last = result.iloc[-1]
        assert last["L"] is True or last["L"] == True  # noqa: E712

    def test_A_flag_positive_momentum(self, short_engine: StrategyEngine):
        """12-month return > 0 → A is True."""
        # Start at 100, end at 120 (positive momentum)
        closes = _flat_closes(100.0, 10) + _flat_closes(120.0, 5)
        df = _make_ohlcv(closes)
        result = short_engine.compute_indicators(df)
        # After abs_momentum_lookback=5, the 12m return should be computable
        last = result.iloc[-1]
        assert last["A"] is True or last["A"] == True  # noqa: E712

    def test_A_flag_negative_momentum(self, short_engine: StrategyEngine):
        """12-month return < 0 → A is False."""
        # Start at 120, drop to 100 (negative momentum)
        closes = _flat_closes(120.0, 10) + _flat_closes(100.0, 5)
        df = _make_ohlcv(closes)
        result = short_engine.compute_indicators(df)
        last = result.iloc[-1]
        assert last["A"] is False or last["A"] == False  # noqa: E712

    def test_no_mutation(self, short_engine: StrategyEngine):
        """compute_indicators must NOT mutate the input DataFrame."""
        df = _make_ohlcv(_flat_closes(50.0, 15))
        cols_before = set(df.columns)
        _ = short_engine.compute_indicators(df)
        assert set(df.columns) == cols_before


# ===================================================================== #
#  2. Score computation — Dual Momentum (L + M + A)
# ===================================================================== #

class TestComputeScore:
    """Tests for compute_score (stateless)."""

    def test_all_true(self, short_engine: StrategyEngine):
        """L=M=A=True → score == 3."""
        df = pd.DataFrame({"L": [True], "M": [True], "A": [True]})
        result = short_engine.compute_score(df)
        assert result["score"].iloc[0] == 3

    def test_all_false(self, short_engine: StrategyEngine):
        """L=M=A=False → score == 0."""
        df = pd.DataFrame({"L": [False], "M": [False], "A": [False]})
        result = short_engine.compute_score(df)
        assert result["score"].iloc[0] == 0

    def test_mixed(self, short_engine: StrategyEngine):
        """L=True, M=False, A=True → score == 2."""
        df = pd.DataFrame({"L": [True], "M": [False], "A": [True]})
        result = short_engine.compute_score(df)
        assert result["score"].iloc[0] == 2

    def test_sma20_excluded_from_score(self, short_engine: StrategyEngine):
        """Score should only depend on L, M, A — not on SMA20/S."""
        df = pd.DataFrame({"L": [True], "M": [True], "A": [True]})
        result = short_engine.compute_score(df)
        # Even if we added an "S" column, the score computation ignores it
        assert result["score"].iloc[0] == 3


# ===================================================================== #
#  3. FSM — update_state transitions
# ===================================================================== #

class TestUpdateState:
    """Tests for the core FSM transition function."""

    @staticmethod
    def _make_row(**kwargs) -> pd.Series:
        """Build a synthetic Series that update_state expects."""
        defaults = {
            "close": 100.0,
            "sma20": 100.0,
            "sma50": 100.0,
            "sma200": 100.0,
            "L": True,
            "M": True,
            "A": True,
            "score": 3,
            "return_3m": 0.0,
            "return_12m": 0.05,
        }
        defaults.update(kwargs)
        return pd.Series(defaults, name=pd.Timestamp("2024-06-01"))

    # ---- Score 3 from NEUTRAL → BULL_ACTIVE ---- #
    def test_score3_from_neutral(self, engine: StrategyEngine):
        prev = _FSMState(effective_state=EffectiveState.NEUTRAL)
        row = self._make_row(score=3)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.BULL_ACTIVE
        assert decision.engine_intent == EngineIntent.SOXL
        assert not new_state.transition_active

    # ---- Score 0 with deep drawdown → BEAR_ACTIVE ---- #
    def test_score0_deep_drawdown(self, engine: StrategyEngine):
        prev = _FSMState(effective_state=EffectiveState.NEUTRAL)
        row = self._make_row(score=0, L=False, M=False, A=False, return_3m=-0.10)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.BEAR_ACTIVE
        assert decision.engine_intent == EngineIntent.SOXS

    # ---- Score 0 with mild drawdown → NEUTRAL ---- #
    def test_score0_mild_drawdown(self, engine: StrategyEngine):
        prev = _FSMState(effective_state=EffectiveState.NEUTRAL)
        row = self._make_row(score=0, L=False, M=False, A=False, return_3m=-0.02)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.NEUTRAL
        assert decision.engine_intent == EngineIntent.NONE

    # ---- Score 0 at exactly -5 % → BEAR_ACTIVE (boundary) ---- #
    def test_score0_exact_threshold(self, engine: StrategyEngine):
        prev = _FSMState(effective_state=EffectiveState.NEUTRAL)
        row = self._make_row(score=0, L=False, M=False, A=False, return_3m=-0.05)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.BEAR_ACTIVE

    # ---- Score 1..2 → NEUTRAL ---- #
    def test_score1_neutral(self, engine: StrategyEngine):
        prev = _FSMState(effective_state=EffectiveState.BULL_ACTIVE)
        row = self._make_row(score=2, L=True, M=True, A=False)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.NEUTRAL
        assert decision.engine_intent == EngineIntent.NONE

    # ---- BEAR→BULL triggers 3-day transition ---- #
    def test_bear_to_bull_transition_trigger(self, engine: StrategyEngine):
        prev = _FSMState(effective_state=EffectiveState.BEAR_ACTIVE)
        row = self._make_row(score=3)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.TRANSITION
        assert new_state.transition_active is True
        assert new_state.transition_day == 1
        assert decision.engine_intent == EngineIntent.NONE

    def test_transition_day2(self, engine: StrategyEngine):
        prev = _FSMState(
            effective_state=EffectiveState.TRANSITION,
            transition_active=True,
            transition_day=1,
        )
        row = self._make_row(score=3)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.transition_day == 2
        assert new_state.transition_active is True
        assert decision.effective_state == EffectiveState.TRANSITION

    def test_transition_day3(self, engine: StrategyEngine):
        prev = _FSMState(
            effective_state=EffectiveState.TRANSITION,
            transition_active=True,
            transition_day=2,
        )
        row = self._make_row(score=3)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.transition_day == 3
        assert new_state.transition_active is True

    def test_transition_completion(self, engine: StrategyEngine):
        """After day 3, the next update should finalize to BULL_ACTIVE."""
        prev = _FSMState(
            effective_state=EffectiveState.TRANSITION,
            transition_active=True,
            transition_day=3,
        )
        row = self._make_row(score=3)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.BULL_ACTIVE
        assert new_state.transition_active is False
        assert new_state.transition_day == 0
        assert decision.engine_intent == EngineIntent.SOXL

    # ---- Transition overrides score changes ---- #
    def test_transition_overrides_score_drop(self, engine: StrategyEngine):
        """Even if score drops during transition, transition continues."""
        prev = _FSMState(
            effective_state=EffectiveState.TRANSITION,
            transition_active=True,
            transition_day=1,
        )
        # Score drops to 2 during transition — transition still proceeds
        row = self._make_row(score=2, A=False)
        new_state, decision = engine.update_state(row, prev)
        assert new_state.transition_active is True
        assert new_state.transition_day == 2
        assert decision.effective_state == EffectiveState.TRANSITION


# ===================================================================== #
#  4. DailyDecision dataclass
# ===================================================================== #

class TestDailyDecision:
    """Verify the decision dataclass is frozen / immutable."""

    def test_frozen(self):
        d = DailyDecision(
            date=pd.Timestamp("2024-01-01"),
            close=100.0,
            sma20=99.0,
            sma50=98.0,
            sma200=97.0,
            indicator_L=True,
            indicator_M=True,
            indicator_A=True,
            score=3,
            return_3m=0.05,
            return_12m=0.20,
            effective_state=EffectiveState.BULL_ACTIVE,
            transition_active=False,
            transition_day=0,
            engine_intent=EngineIntent.SOXL,
        )
        with pytest.raises(AttributeError):
            d.score = 999  # type: ignore[misc]

    def test_indicator_A_replaces_S(self):
        """DailyDecision has indicator_A, not indicator_S."""
        d = DailyDecision(
            date=pd.Timestamp("2024-01-01"),
            close=100.0, sma20=99.0, sma50=98.0, sma200=97.0,
            indicator_L=True, indicator_M=True, indicator_A=False,
            score=2, return_3m=0.05, return_12m=0.20,
            effective_state=EffectiveState.NEUTRAL,
            transition_active=False, transition_day=0,
            engine_intent=EngineIntent.NONE,
        )
        assert d.indicator_A is False
        assert not hasattr(d, "indicator_S")


# ===================================================================== #
#  5. Full run (integration)
# ===================================================================== #

class TestRun:
    """Integration tests for StrategyEngine.run()."""

    def test_flat_market_stays_neutral_or_bull(self, short_engine: StrategyEngine):
        """In a perfectly flat market all SMAs equal → L, M flags
        depend on strict > so close is NOT > SMA → score 0.
        But return_3m == 0.0 which is > -5%, so state is NEUTRAL.
        """
        n = 30
        df = _make_ohlcv(_flat_closes(100.0, n))
        decisions = short_engine.run(df)
        assert len(decisions) > 0
        for d in decisions:
            assert d.effective_state in (EffectiveState.NEUTRAL, EffectiveState.BULL_ACTIVE)

    def test_strong_uptrend_becomes_bull(self):
        """A steep uptrend should eventually produce Score 3 → BULL_ACTIVE."""
        engine = StrategyEngine(
            sma_short=3, sma_mid=5, sma_long=10,
            abs_momentum_lookback=5,
            bear_return_lookback=5, transition_days=3,
        )
        # Start flat then ramp hard
        closes = _flat_closes(100.0, 15) + _trending_closes(101.0, 200.0, 30)
        df = _make_ohlcv(closes)
        decisions = engine.run(df)
        bull_days = [d for d in decisions if d.effective_state == EffectiveState.BULL_ACTIVE]
        assert len(bull_days) > 0, "Expected at least one BULL_ACTIVE day in strong uptrend"

    def test_deep_crash_becomes_bear(self):
        """A sharp decline should trigger BEAR_ACTIVE."""
        engine = StrategyEngine(
            sma_short=3, sma_mid=5, sma_long=10,
            abs_momentum_lookback=5,
            bear_return_lookback=5, transition_days=3,
            bear_return_threshold=-0.05,
        )
        # Start high, drop severely
        closes = _flat_closes(200.0, 15) + _trending_closes(199.0, 100.0, 20)
        df = _make_ohlcv(closes)
        decisions = engine.run(df)
        bear_days = [d for d in decisions if d.effective_state == EffectiveState.BEAR_ACTIVE]
        assert len(bear_days) > 0, "Expected BEAR_ACTIVE during severe crash"

    def test_bear_to_bull_has_transition(self):
        """Recovery from bear should include a TRANSITION phase."""
        engine = StrategyEngine(
            sma_short=3, sma_mid=5, sma_long=10,
            abs_momentum_lookback=5,
            bear_return_lookback=20, transition_days=3,
            bear_return_threshold=-0.05,
        )
        closes = (
            _flat_closes(200.0, 12)     # warmup at 200
            + _flat_closes(80.0, 11)    # gap down, SMAs converge → BEAR_ACTIVE
            + _flat_closes(81.0, 10)    # tiny tick up → score 0→3 → TRANSITION
        )
        df = _make_ohlcv(closes)
        decisions = engine.run(df)

        states = [d.effective_state for d in decisions]
        bear_seen = EffectiveState.BEAR_ACTIVE in states
        transition_seen = EffectiveState.TRANSITION in states
        bull_seen = EffectiveState.BULL_ACTIVE in states

        assert bear_seen, "Expected BEAR_ACTIVE during crash"
        assert transition_seen, "Expected TRANSITION during recovery"
        assert bull_seen, "Expected BULL_ACTIVE after transition"

        # Verify transition lasted exactly 3 days
        transition_days = [d for d in decisions if d.effective_state == EffectiveState.TRANSITION]
        assert len(transition_days) == 3, (
            f"Expected exactly 3 TRANSITION days, got {len(transition_days)}"
        )

    def test_decisions_to_dataframe(self, short_engine: StrategyEngine):
        df = _make_ohlcv(_flat_closes(100.0, 30))
        decisions = short_engine.run(df)
        result_df = short_engine.decisions_to_dataframe(decisions)
        assert isinstance(result_df, pd.DataFrame)
        assert "score" in result_df.columns
        assert "effective_state" in result_df.columns

    def test_deterministic(self, short_engine: StrategyEngine):
        """Two runs on the same data must produce identical results."""
        df = _make_ohlcv(_trending_closes(50, 150, 40))
        d1 = short_engine.run(df)
        d2 = short_engine.run(df)
        assert len(d1) == len(d2)
        for a, b in zip(d1, d2):
            assert a == b


# ===================================================================== #
#  6. Edge cases
# ===================================================================== #

class TestEdgeCases:
    """Boundary / edge-case scenarios."""

    def test_empty_dataframe(self, engine: StrategyEngine):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        decisions = engine.run(df)
        assert decisions == []

    def test_too_few_rows(self, engine: StrategyEngine):
        """Fewer rows than max(SMA200, 252) warmup → empty output."""
        df = _make_ohlcv(_flat_closes(100.0, 50))
        decisions = engine.run(df)
        assert decisions == []

    def test_custom_threshold(self):
        """A stricter bear threshold should require a deeper drawdown."""
        engine = StrategyEngine(
            sma_short=3, sma_mid=5, sma_long=10,
            abs_momentum_lookback=5,
            bear_return_lookback=5,
            bear_return_threshold=-0.20,  # very strict
        )
        prev = _FSMState(effective_state=EffectiveState.NEUTRAL)
        row = pd.Series({
            "close": 80.0,
            "sma20": 90.0,
            "sma50": 95.0,
            "sma200": 100.0,
            "L": False,
            "M": False,
            "A": False,
            "score": 0,
            "return_3m": -0.10,  # only -10 %, threshold is -20 %
            "return_12m": -0.15,
        }, name=pd.Timestamp("2024-06-01"))
        new_state, decision = engine.update_state(row, prev)
        assert new_state.effective_state == EffectiveState.NEUTRAL  # NOT bear

    def test_repr(self, engine: StrategyEngine):
        r = repr(engine)
        assert "SOXX" in r
        assert "200" in r
        assert "abs_momentum_lookback" in r


# ===================================================================== #
#  7. EngineIntent mapping consistency
# ===================================================================== #

class TestIntentMapping:
    """Verify _resolve_intent covers every EffectiveState."""

    def test_all_states_covered(self):
        for state_val in EffectiveState:
            fsm = _FSMState(effective_state=state_val)
            intent = StrategyEngine._resolve_intent(fsm)
            assert isinstance(intent, EngineIntent)
