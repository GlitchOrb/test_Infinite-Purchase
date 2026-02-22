"""
strategy_engine.py
==================
Rule-based regime FSM for semiconductor-sector rotation.

Signal asset : SOXX (configurable)
Trade targets : SOXL (bull), SOXS (bear), NONE (neutral / cash)

All signals are derived deterministically from daily OHLCV of the signal
asset.  No external AI / LLM calls at runtime.

Author : quant-desk
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EffectiveState(str, enum.Enum):
    """Regime the portfolio is currently operating under."""
    BULL_ACTIVE = "BULL_ACTIVE"
    BEAR_ACTIVE = "BEAR_ACTIVE"
    NEUTRAL = "NEUTRAL"
    TRANSITION = "TRANSITION"


class EngineIntent(str, enum.Enum):
    """Which leveraged ETF engine should be active today."""
    SOXL = "SOXL"
    SOXS = "SOXS"
    NONE = "NONE"


# ---------------------------------------------------------------------------
# Decision dataclass — one per trading day
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DailyDecision:
    """Immutable snapshot of the strategy output for a single trading day.

    Attributes
    ----------
    date : pd.Timestamp
        Trading date.
    close : float
        Signal-asset closing price.
    sma20 : float
        20-day simple moving average.
    sma50 : float
        50-day simple moving average.
    sma200 : float
        200-day simple moving average.
    indicator_L : bool
        close > SMA200.
    indicator_M : bool
        SMA50 > SMA200.
    indicator_S : bool
        SMA20 > SMA50.
    score : int
        Sum of L, M, S  (0..3).
    return_3m : float
        3-month (~63 trading-day) return of the signal asset.
    return_12m : float | None
        12-month (~252 trading-day) return, optional.
    effective_state : EffectiveState
        Current regime after FSM update.
    transition_active : bool
        True while the 3-day bear→bull swap is in progress.
    transition_day : int
        Day counter (1..3) during transition, 0 otherwise.
    engine_intent : EngineIntent
        Which execution engine should be live.
    """

    date: pd.Timestamp
    close: float
    sma20: float
    sma50: float
    sma200: float
    indicator_L: bool
    indicator_M: bool
    indicator_S: bool
    score: int
    return_3m: float
    return_12m: Optional[float]
    effective_state: EffectiveState
    transition_active: bool
    transition_day: int
    engine_intent: EngineIntent


# ---------------------------------------------------------------------------
# Internal FSM state (mutable, carried between days)
# ---------------------------------------------------------------------------

@dataclass
class _FSMState:
    """Mutable internal state that persists across trading days."""
    effective_state: EffectiveState = EffectiveState.NEUTRAL
    transition_active: bool = False
    transition_day: int = 0


# ---------------------------------------------------------------------------
# StrategyEngine
# ---------------------------------------------------------------------------

class StrategyEngine:
    """Deterministic regime finite-state machine for SOXX-based rotation.

    Parameters
    ----------
    signal_ticker : str
        Ticker whose OHLCV drives the signals (default ``"SOXX"``).
    sma_short : int
        Short SMA window (default 20).
    sma_mid : int
        Mid SMA window (default 50).
    sma_long : int
        Long SMA window (default 200).
    bear_return_threshold : float
        3-month return threshold to *activate* BEAR posture.
        Default ``-0.05`` (i.e. ≤ –5 %).
    bear_return_lookback : int
        Number of trading days for the 3-month return computation
        (default 63).
    transition_days : int
        Duration in trading days for the bear→bull swap (default 3).

    Usage
    -----
    >>> engine = StrategyEngine()
    >>> decisions = engine.run(ohlcv_df)   # returns list[DailyDecision]
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        signal_ticker: str = "SOXX",
        sma_short: int = 20,
        sma_mid: int = 50,
        sma_long: int = 200,
        bear_return_threshold: float = -0.05,
        bear_return_lookback: int = 63,
        transition_days: int = 3,
    ) -> None:
        self.signal_ticker = signal_ticker
        self.sma_short = sma_short
        self.sma_mid = sma_mid
        self.sma_long = sma_long
        self.bear_return_threshold = bear_return_threshold
        self.bear_return_lookback = bear_return_lookback
        self.transition_days = transition_days

    # ------------------------------------------------------------------ #
    # Public — indicator computation (stateless, unit-test friendly)
    # ------------------------------------------------------------------ #

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add SMA columns and boolean regime indicators to *df*.

        Expects at least a ``"close"`` column.  Returns a **copy** with the
        following columns appended:

        * ``sma20``, ``sma50``, ``sma200``
        * ``L``  — close > SMA200
        * ``M``  — SMA50 > SMA200
        * ``S``  — SMA20 > SMA50

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data indexed by date (or with a ``date`` column).

        Returns
        -------
        pd.DataFrame
            Copy of *df* with indicator columns appended.
        """
        out = df.copy()

        out["sma20"] = out["close"].rolling(window=self.sma_short, min_periods=self.sma_short).mean()
        out["sma50"] = out["close"].rolling(window=self.sma_mid, min_periods=self.sma_mid).mean()
        out["sma200"] = out["close"].rolling(window=self.sma_long, min_periods=self.sma_long).mean()

        out["L"] = out["close"] > out["sma200"]
        out["M"] = out["sma50"] > out["sma200"]
        out["S"] = out["sma20"] > out["sma50"]

        return out

    # ------------------------------------------------------------------ #
    # Public — score computation (stateless, unit-test friendly)
    # ------------------------------------------------------------------ #

    def compute_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute the regime score (0..3) from boolean indicators.

        Expects columns ``L``, ``M``, ``S`` to be present (call
        :meth:`compute_indicators` first).

        Parameters
        ----------
        df : pd.DataFrame

        Returns
        -------
        pd.DataFrame
            Copy with ``score`` column added.
        """
        out = df.copy()
        out["score"] = out["L"].astype(int) + out["M"].astype(int) + out["S"].astype(int)
        return out

    # ------------------------------------------------------------------ #
    # Public — 3-month return (stateless)
    # ------------------------------------------------------------------ #

    def compute_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute rolling 3-month (and 12-month) returns.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain ``close``.

        Returns
        -------
        pd.DataFrame
            Copy with ``return_3m`` and ``return_12m`` columns.
        """
        out = df.copy()
        out["return_3m"] = out["close"].pct_change(periods=self.bear_return_lookback)

        lookback_12m = self.bear_return_lookback * 4  # ~252
        if len(out) >= lookback_12m:
            out["return_12m"] = out["close"].pct_change(periods=lookback_12m)
        else:
            out["return_12m"] = None

        return out

    # ------------------------------------------------------------------ #
    # Public — single-row FSM update (unit-test friendly)
    # ------------------------------------------------------------------ #

    def update_state(
        self,
        row: pd.Series,
        previous_state: _FSMState,
    ) -> tuple[_FSMState, DailyDecision]:
        """Advance the FSM by one trading day.

        This is the **core deterministic transition function**.  It is
        intentionally side-effect-free: it reads *previous_state*, computes
        the next state, and returns *both* the new state and the decision.

        Parameters
        ----------
        row : pd.Series
            A single row containing at minimum: ``date | close | sma20 |
            sma50 | sma200 | L | M | S | score | return_3m``
            (optionally ``return_12m``).
        previous_state : _FSMState
            Mutable state carried from the prior trading day.

        Returns
        -------
        tuple[_FSMState, DailyDecision]
            ``(new_state, decision)``
        """
        score: int = int(row["score"])
        return_3m: float = float(row["return_3m"]) if pd.notna(row["return_3m"]) else 0.0
        return_12m = float(row["return_12m"]) if pd.notna(row.get("return_12m")) else None

        prev = previous_state
        new = _FSMState(
            effective_state=prev.effective_state,
            transition_active=prev.transition_active,
            transition_day=prev.transition_day,
        )

        # ----- Transition in progress ----- #
        if prev.transition_active:
            new.transition_day = prev.transition_day + 1

            if new.transition_day > self.transition_days:
                # Transition complete → enter BULL
                new.transition_active = False
                new.transition_day = 0
                new.effective_state = EffectiveState.BULL_ACTIVE
            else:
                new.effective_state = EffectiveState.TRANSITION
                # Stay in transition; engine_intent resolved below

        # ----- No transition in progress — normal regime logic ----- #
        else:
            if score == 3:
                # FULL_BULL
                if prev.effective_state == EffectiveState.BEAR_ACTIVE:
                    # Trigger bear→bull transition
                    new.transition_active = True
                    new.transition_day = 1
                    new.effective_state = EffectiveState.TRANSITION
                else:
                    new.effective_state = EffectiveState.BULL_ACTIVE

            elif score == 0:
                # FULL_BEAR candidate — apply activation filter
                if return_3m <= self.bear_return_threshold:
                    new.effective_state = EffectiveState.BEAR_ACTIVE
                else:
                    new.effective_state = EffectiveState.NEUTRAL

            else:
                # Score 1 or 2 → NEUTRAL
                new.effective_state = EffectiveState.NEUTRAL

        # ----- Derive engine intent from effective state ----- #
        engine_intent = self._resolve_intent(new)

        decision = DailyDecision(
            date=row.name if isinstance(row.name, pd.Timestamp) else pd.Timestamp(row.get("date", row.name)),
            close=float(row["close"]),
            sma20=float(row["sma20"]) if pd.notna(row["sma20"]) else float("nan"),
            sma50=float(row["sma50"]) if pd.notna(row["sma50"]) else float("nan"),
            sma200=float(row["sma200"]) if pd.notna(row["sma200"]) else float("nan"),
            indicator_L=bool(row["L"]),
            indicator_M=bool(row["M"]),
            indicator_S=bool(row["S"]),
            score=score,
            return_3m=return_3m,
            return_12m=return_12m,
            effective_state=new.effective_state,
            transition_active=new.transition_active,
            transition_day=new.transition_day,
            engine_intent=engine_intent,
        )

        return new, decision

    # ------------------------------------------------------------------ #
    # Public — full backtest / batch run
    # ------------------------------------------------------------------ #

    def run(self, df: pd.DataFrame) -> list[DailyDecision]:
        """Process an entire OHLCV DataFrame and return daily decisions.

        Parameters
        ----------
        df : pd.DataFrame
            Raw OHLCV for the signal asset.  Must have a ``close`` column
            and a DatetimeIndex (or a ``date`` column).

        Returns
        -------
        list[DailyDecision]
        """
        enriched = self.compute_indicators(df)
        enriched = self.compute_score(enriched)
        enriched = self.compute_returns(enriched)

        # Drop rows where indicators are not yet available
        min_warmup = self.sma_long  # SMA200 requires 200 bars
        enriched = enriched.iloc[min_warmup - 1:]  # keep from first valid SMA200

        # Further restrict to rows where return_3m is available
        enriched = enriched.dropna(subset=["sma200", "return_3m"])

        state = _FSMState()  # defaults to NEUTRAL, no transition
        decisions: list[DailyDecision] = []

        for idx, row in enriched.iterrows():
            state, decision = self.update_state(row, state)
            decisions.append(decision)

        return decisions

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_intent(state: _FSMState) -> EngineIntent:
        """Map effective state to engine intent.

        During TRANSITION the engine is winding down SOXS and preparing
        SOXL, but the *intent* stays NONE (no new positions until the swap
        completes, existing SOXS is being liquidated).

        Returns
        -------
        EngineIntent
        """
        mapping = {
            EffectiveState.BULL_ACTIVE: EngineIntent.SOXL,
            EffectiveState.BEAR_ACTIVE: EngineIntent.SOXS,
            EffectiveState.NEUTRAL: EngineIntent.NONE,
            EffectiveState.TRANSITION: EngineIntent.NONE,
        }
        return mapping[state.effective_state]

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    def decisions_to_dataframe(self, decisions: list[DailyDecision]) -> pd.DataFrame:
        """Convert a list of decisions to a DataFrame for analysis.

        Parameters
        ----------
        decisions : list[DailyDecision]

        Returns
        -------
        pd.DataFrame
        """
        from dataclasses import asdict

        records = [asdict(d) for d in decisions]
        result = pd.DataFrame(records)
        if "date" in result.columns:
            result = result.set_index("date")
        return result

    def __repr__(self) -> str:
        return (
            f"StrategyEngine("
            f"signal={self.signal_ticker}, "
            f"sma=[{self.sma_short}/{self.sma_mid}/{self.sma_long}], "
            f"bear_thresh={self.bear_return_threshold:.2%}, "
            f"transition_days={self.transition_days})"
        )
