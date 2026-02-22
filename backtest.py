"""
backtest.py
===========
Alpha Predator v4.1 — Backtest Framework

Bridges ``StrategyEngine`` + ``TradeManager`` into
``vectorbt``-compatible entry/exit signal arrays for:

1.  **Single-run backtest** with full position simulation
2.  **Parameter sweep** across slices, take-profit, SOXS cooldown, etc.
3.  **Walk-forward validation** (quarterly IS/OOS windows)
4.  **Metrics collection** (Sharpe, MDD, drawdown duration, avg trade P/L)
5.  **Report export** (CSV + JSON)

All logic is offline — no real-time API calls.
Requires: ``vectorbt``, ``pandas``, ``numpy``.

Usage
-----
>>> from backtest import (
...     run_single_backtest,
...     sweep_parameters,
...     walk_forward_quarterly,
...     export_report,
... )
>>> from backtest import generate_sample_data
>>> soxx, soxl, soxs = generate_sample_data(years=5)
>>> result = run_single_backtest(soxx, soxl, soxs)
>>> print(result.metrics)

Author : quant-desk
"""

from __future__ import annotations

import copy
import enum
import itertools
import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import vectorbt as vbt

from strategy_engine import (
    DailyDecision,
    EffectiveState,
    EngineIntent,
    StrategyEngine,
    _FSMState,
)
from trade_manager import (
    OrderIntent,
    OrderSide,
    PositionInfo,
    TradeManager,
    TradeManagerConfig,
    TradeManagerState,
)


# ======================================================================= #
#  Data structures
# ======================================================================= #

@dataclass
class BacktestMetrics:
    """Core performance metrics from a single backtest run.

    All figures computed from the combined SOXL + SOXS equity curve.
    """
    # -- Return --
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    # -- Risk --
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0
    calmar_ratio: float = 0.0
    # -- Trade --
    total_trades: int = 0
    win_rate_pct: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    # -- Regime --
    bull_days: int = 0
    bear_days: int = 0
    neutral_days: int = 0
    transition_days: int = 0
    # -- Params --
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Full output from a single backtest run."""
    metrics: BacktestMetrics
    equity_curve: pd.Series           # daily NAV
    decisions: pd.DataFrame           # daily regime decisions
    trades: pd.DataFrame              # individual trade log
    drawdown_series: pd.Series        # daily drawdown
    soxl_entries: pd.Series           # boolean entry signals
    soxl_exits: pd.Series             # boolean exit signals
    soxs_entries: pd.Series
    soxs_exits: pd.Series


@dataclass
class SweepResult:
    """Output from a parameter sweep."""
    results: List[BacktestResult]
    summary: pd.DataFrame             # one row per parameter combo
    best_sharpe: BacktestMetrics
    best_calmar: BacktestMetrics


@dataclass
class WalkForwardResult:
    """Output from a walk-forward validation."""
    windows: List[Dict[str, Any]]     # per-window detail
    oos_equity: pd.Series             # concatenated OOS equity
    oos_metrics: BacktestMetrics      # aggregated OOS metrics
    summary: pd.DataFrame


# ======================================================================= #
#  Synthetic data generator (for demo / testing)
# ======================================================================= #

def generate_sample_data(
    years: int = 5,
    start: str = "2018-01-02",
    soxx_start: float = 200.0,
    annual_drift: float = 0.08,
    annual_vol: float = 0.25,
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate synthetic daily OHLCV for SOXX, SOXL, SOXS.

    SOXL ≈ 3× daily return of SOXX (leveraged bull).
    SOXS ≈ –3× daily return of SOXX (leveraged inverse).

    Parameters
    ----------
    years : int
        Number of years of data.
    start : str
        Start date string.
    soxx_start : float
        Initial SOXX close.
    annual_drift, annual_vol : float
        GBM parameters for SOXX.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    (soxx_df, soxl_df, soxs_df)
        Each DataFrame has columns: open, high, low, close, volume
        with a DatetimeIndex.
    """
    rng = np.random.default_rng(seed)
    n_days = int(years * 252)
    dates = pd.bdate_range(start=start, periods=n_days, freq="B")

    # GBM daily returns for SOXX
    dt = 1 / 252
    daily_ret = np.exp(
        (annual_drift - 0.5 * annual_vol ** 2) * dt
        + annual_vol * np.sqrt(dt) * rng.standard_normal(n_days)
    )

    soxx_close = np.empty(n_days)
    soxx_close[0] = soxx_start
    for i in range(1, n_days):
        soxx_close[i] = soxx_close[i - 1] * daily_ret[i]

    def _build_ohlcv(close: np.ndarray) -> pd.DataFrame:
        intraday_vol = 0.005
        high = close * (1 + rng.uniform(0, intraday_vol, len(close)))
        low = close * (1 - rng.uniform(0, intraday_vol, len(close)))
        open_ = close * (1 + rng.uniform(-intraday_vol / 2, intraday_vol / 2, len(close)))
        volume = rng.integers(500_000, 5_000_000, len(close))
        return pd.DataFrame({
            "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        }, index=dates)

    soxx_df = _build_ohlcv(soxx_close)

    # SOXL: 3× daily return
    soxx_daily = pd.Series(soxx_close).pct_change().fillna(0).values
    soxl_close = np.empty(n_days)
    soxl_close[0] = 30.0
    for i in range(1, n_days):
        soxl_close[i] = soxl_close[i - 1] * (1 + 3 * soxx_daily[i])
    soxl_close = np.maximum(soxl_close, 0.01)
    soxl_df = _build_ohlcv(soxl_close)

    # SOXS: –3× daily return
    soxs_close = np.empty(n_days)
    soxs_close[0] = 40.0
    for i in range(1, n_days):
        soxs_close[i] = soxs_close[i - 1] * (1 - 3 * soxx_daily[i])
    soxs_close = np.maximum(soxs_close, 0.01)
    soxs_df = _build_ohlcv(soxs_close)

    return soxx_df, soxl_df, soxs_df


# ======================================================================= #
#  Core backtest engine
# ======================================================================= #

def _run_fsm(
    soxx_df: pd.DataFrame,
    engine: StrategyEngine,
) -> pd.DataFrame:
    """Run the regime FSM on SOXX data and return a decisions DataFrame.

    Parameters
    ----------
    soxx_df : pd.DataFrame
        SOXX OHLCV with DatetimeIndex and ``close`` column.
    engine : StrategyEngine

    Returns
    -------
    pd.DataFrame
        Indexed by date with columns from ``DailyDecision``.
    """
    decisions = engine.run(soxx_df)
    if not decisions:
        return pd.DataFrame()
    return engine.decisions_to_dataframe(decisions)


def _simulate_positions(
    decisions_df: pd.DataFrame,
    soxl_prices: pd.Series,
    soxs_prices: pd.Series,
    total_capital: float,
    trade_mgr: TradeManager,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series, List[Dict]]:
    """Day-by-day position simulation using TradeManager.

    Returns
    -------
    (equity_df, soxl_entries, soxl_exits, soxs_entries, soxs_exits, trade_log)
    """
    common_idx = decisions_df.index.intersection(soxl_prices.index).intersection(soxs_prices.index)
    decisions_df = decisions_df.loc[common_idx]
    soxl_prices = soxl_prices.loc[common_idx]
    soxs_prices = soxs_prices.loc[common_idx]

    n = len(common_idx)
    nav = np.full(n, total_capital, dtype=float)
    soxl_entry_arr = np.zeros(n, dtype=bool)
    soxl_exit_arr = np.zeros(n, dtype=bool)
    soxs_entry_arr = np.zeros(n, dtype=bool)
    soxs_exit_arr = np.zeros(n, dtype=bool)

    tm_state = TradeManagerState()
    cash = total_capital
    trade_log: List[Dict] = []

    # Track open trade start info
    soxl_open_trade_date: Optional[pd.Timestamp] = None
    soxl_open_trade_cost: float = 0.0
    soxs_open_trade_date: Optional[pd.Timestamp] = None
    soxs_open_trade_cost: float = 0.0

    for i, date in enumerate(common_idx):
        row = decisions_df.loc[date]
        soxl_px = float(soxl_prices.loc[date])
        soxs_px = float(soxs_prices.loc[date])

        # Rebuild decision
        decision = DailyDecision(
            date=date,
            close=float(row.get("close", 0)),
            sma20=float(row.get("sma20", 0)),
            sma50=float(row.get("sma50", 0)),
            sma200=float(row.get("sma200", 0)),
            indicator_L=bool(row.get("indicator_L", False)),
            indicator_M=bool(row.get("indicator_M", False)),
            indicator_A=bool(row.get("indicator_A", False)),
            score=int(row.get("score", 0)),
            return_3m=float(row.get("return_3m", 0)),
            return_12m=float(row["return_12m"]) if pd.notna(row.get("return_12m")) else None,
            effective_state=EffectiveState(row["effective_state"]),
            transition_active=bool(row.get("transition_active", False)),
            transition_day=int(row.get("transition_day", 0)),
            engine_intent=EngineIntent(row.get("engine_intent", "NONE")),
        )

        # Process day
        intents, new_state = trade_mgr.process_day(
            decision, soxl_px, soxs_px, cash + _position_value(tm_state, soxl_px, soxs_px),
            tm_state,
        )

        # Execute intents (simplified: immediate fill at current price)
        for intent in intents:
            px = soxl_px if intent.symbol == "SOXL" else soxs_px
            if intent.side == OrderSide.BUY:
                qty = int(intent.notional // px) if px > 0 else 0
                if qty <= 0:
                    continue
                cost = qty * px
                if cost > cash:
                    qty = int(cash // px)
                    cost = qty * px
                if qty <= 0:
                    continue
                cash -= cost
                new_state = trade_mgr.apply_fill(
                    intent.symbol, OrderSide.BUY, qty, px, date, new_state,
                )
                if intent.symbol == "SOXL":
                    soxl_entry_arr[i] = True
                    if soxl_open_trade_date is None:
                        soxl_open_trade_date = date
                        soxl_open_trade_cost = px
                else:
                    soxs_entry_arr[i] = True
                    if soxs_open_trade_date is None:
                        soxs_open_trade_date = date
                        soxs_open_trade_cost = px
            else:
                qty = min(intent.qty, new_state.soxl.qty if intent.symbol == "SOXL" else new_state.soxs.qty)
                if qty <= 0:
                    continue
                cash += qty * px
                pos_before = (new_state.soxl if intent.symbol == "SOXL" else new_state.soxs)
                avg_cost_before = pos_before.avg_cost
                new_state = trade_mgr.apply_fill(
                    intent.symbol, OrderSide.SELL, qty, px, date, new_state,
                )
                pos_after = (new_state.soxl if intent.symbol == "SOXL" else new_state.soxs)

                if intent.symbol == "SOXL":
                    soxl_exit_arr[i] = True
                    if pos_after.qty <= 0 and soxl_open_trade_date is not None:
                        trade_log.append({
                            "symbol": "SOXL", "entry_date": soxl_open_trade_date,
                            "exit_date": date, "entry_price": soxl_open_trade_cost,
                            "exit_price": px, "qty": qty,
                            "pnl": (px - avg_cost_before) * qty,
                            "return_pct": (px / avg_cost_before - 1) * 100 if avg_cost_before > 0 else 0,
                            "reason": intent.reason,
                        })
                        soxl_open_trade_date = None
                else:
                    soxs_exit_arr[i] = True
                    if pos_after.qty <= 0 and soxs_open_trade_date is not None:
                        realized = (px - avg_cost_before) * qty
                        trade_log.append({
                            "symbol": "SOXS", "entry_date": soxs_open_trade_date,
                            "exit_date": date, "entry_price": soxs_open_trade_cost,
                            "exit_price": px, "qty": qty,
                            "pnl": realized,
                            "return_pct": (px / avg_cost_before - 1) * 100 if avg_cost_before > 0 else 0,
                            "reason": intent.reason,
                        })
                        soxs_open_trade_date = None

                        # Vampire injection
                        if realized > 0:
                            new_state = trade_mgr.on_realized_pnl(
                                "SOXS", realized, decision.effective_state,
                                soxl_px, new_state,
                            )

        tm_state = new_state
        nav[i] = cash + _position_value(tm_state, soxl_px, soxs_px)

    equity = pd.Series(nav, index=common_idx, name="equity")

    return (
        decisions_df,
        pd.Series(soxl_entry_arr, index=common_idx, name="soxl_entry"),
        pd.Series(soxl_exit_arr, index=common_idx, name="soxl_exit"),
        pd.Series(soxs_entry_arr, index=common_idx, name="soxs_entry"),
        pd.Series(soxs_exit_arr, index=common_idx, name="soxs_exit"),
        trade_log,
    )


def _position_value(st: TradeManagerState, soxl_px: float, soxs_px: float) -> float:
    """Mark-to-market value of current positions."""
    return st.soxl.qty * soxl_px + st.soxs.qty * soxs_px


# ======================================================================= #
#  Metrics computation
# ======================================================================= #

def _compute_metrics(
    equity: pd.Series,
    trades: pd.DataFrame,
    decisions: pd.DataFrame,
    initial_capital: float,
    params: Optional[Dict[str, Any]] = None,
) -> BacktestMetrics:
    """Compute comprehensive performance metrics from equity curve.

    Parameters
    ----------
    equity : pd.Series
        Daily NAV series.
    trades : pd.DataFrame
        Trade log.
    decisions : pd.DataFrame
        Daily regime decisions.
    initial_capital : float
    params : dict, optional
        Parameter dict for reference.

    Returns
    -------
    BacktestMetrics
    """
    m = BacktestMetrics(params=params or {})

    if equity.empty or len(equity) < 2:
        return m

    # -- Returns --
    daily_ret = equity.pct_change().dropna()
    total_ret = (equity.iloc[-1] / equity.iloc[0]) - 1.0
    m.total_return_pct = round(total_ret * 100, 4)

    n_years = len(equity) / 252
    if n_years > 0 and equity.iloc[-1] > 0 and equity.iloc[0] > 0:
        m.cagr_pct = round(((equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1) * 100, 4)

    # -- Risk --
    if daily_ret.std() > 0:
        m.sharpe_ratio = round(float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)), 4)

    downside = daily_ret[daily_ret < 0]
    if len(downside) > 0 and downside.std() > 0:
        m.sortino_ratio = round(float(daily_ret.mean() / downside.std() * np.sqrt(252)), 4)

    # Drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    m.max_drawdown_pct = round(float(drawdown.min()) * 100, 4)

    # Drawdown duration
    is_dd = drawdown < 0
    if is_dd.any():
        dd_groups = (~is_dd).cumsum()
        dd_durations = is_dd.groupby(dd_groups).sum()
        m.max_drawdown_duration_days = int(dd_durations.max())

    # Calmar
    if m.max_drawdown_pct != 0:
        m.calmar_ratio = round(m.cagr_pct / abs(m.max_drawdown_pct), 4)

    # -- Trades --
    if not trades.empty and "pnl" in trades.columns:
        m.total_trades = len(trades)
        wins = trades[trades["pnl"] > 0]
        losses = trades[trades["pnl"] <= 0]
        m.win_rate_pct = round(len(wins) / m.total_trades * 100, 2) if m.total_trades > 0 else 0
        m.avg_trade_pnl = round(float(trades["pnl"].mean()), 2)
        m.avg_win = round(float(wins["pnl"].mean()), 2) if len(wins) > 0 else 0
        m.avg_loss = round(float(losses["pnl"].mean()), 2) if len(losses) > 0 else 0
        gross_profit = float(wins["pnl"].sum()) if len(wins) > 0 else 0
        gross_loss = abs(float(losses["pnl"].sum())) if len(losses) > 0 else 0
        m.profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")

    # -- Regime days --
    if "effective_state" in decisions.columns:
        state_counts = decisions["effective_state"].value_counts()
        m.bull_days = int(state_counts.get("BULL_ACTIVE", 0))
        m.bear_days = int(state_counts.get("BEAR_ACTIVE", 0))
        m.neutral_days = int(state_counts.get("NEUTRAL", 0))
        m.transition_days = int(state_counts.get("TRANSITION", 0))

    return m


# ======================================================================= #
#  Public API: Single backtest
# ======================================================================= #

def run_single_backtest(
    soxx_df: pd.DataFrame,
    soxl_df: pd.DataFrame,
    soxs_df: pd.DataFrame,
    initial_capital: float = 100_000.0,
    engine_params: Optional[Dict[str, Any]] = None,
    trade_config: Optional[TradeManagerConfig] = None,
) -> BacktestResult:
    """Run a single full backtest.

    Parameters
    ----------
    soxx_df : pd.DataFrame
        SOXX OHLCV with DatetimeIndex.
    soxl_df, soxs_df : pd.DataFrame
        SOXL/SOXS OHLCV used for position simulation.
    initial_capital : float
        Starting portfolio NAV.
    engine_params : dict, optional
        Overrides for ``StrategyEngine.__init__`` kwargs.
    trade_config : TradeManagerConfig, optional
        Override for trade config.

    Returns
    -------
    BacktestResult
    """
    ep = engine_params or {}
    engine = StrategyEngine(**ep)
    mgr = TradeManager(config=trade_config)

    decisions_df = _run_fsm(soxx_df, engine)
    if decisions_df.empty:
        empty_series = pd.Series(dtype=float)
        return BacktestResult(
            metrics=BacktestMetrics(params=ep),
            equity_curve=empty_series,
            decisions=pd.DataFrame(),
            trades=pd.DataFrame(),
            drawdown_series=empty_series,
            soxl_entries=empty_series,
            soxl_exits=empty_series,
            soxs_entries=empty_series,
            soxs_exits=empty_series,
        )

    soxl_prices = soxl_df["close"]
    soxs_prices = soxs_df["close"]

    # Build vectorbt-style entry/exit arrays (for the result struct)
    common_idx = decisions_df.index.intersection(soxl_prices.index).intersection(soxs_prices.index)
    decisions_df = decisions_df.loc[common_idx]

    # Run authoritative day-by-day simulation
    equity, trades_df = _backtest_with_equity(
        decisions_df, soxl_prices, soxs_prices, initial_capital, mgr,
    )

    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax

    # Derive entry/exit booleans from decisions for the result struct
    soxl_entries = (
        (decisions_df["effective_state"] == "BULL_ACTIVE") |
        (decisions_df["effective_state"] == "TRANSITION")
    )
    soxl_exits = (
        (decisions_df["effective_state"].shift(1) == "BULL_ACTIVE") &
        (decisions_df["effective_state"] != "BULL_ACTIVE") &
        (decisions_df["effective_state"] != "TRANSITION")
    ).fillna(False)
    soxs_entries = decisions_df["effective_state"] == "BEAR_ACTIVE"
    soxs_exits = (
        (decisions_df["effective_state"].shift(1) == "BEAR_ACTIVE") &
        (decisions_df["effective_state"] != "BEAR_ACTIVE")
    ).fillna(False)

    all_params = {**ep}
    if trade_config:
        all_params.update(asdict(trade_config))

    metrics = _compute_metrics(equity, trades_df, decisions_df, initial_capital, all_params)

    return BacktestResult(
        metrics=metrics,
        equity_curve=equity,
        decisions=decisions_df,
        trades=trades_df,
        drawdown_series=drawdown,
        soxl_entries=soxl_entries,
        soxl_exits=soxl_exits,
        soxs_entries=soxs_entries,
        soxs_exits=soxs_exits,
    )


def _backtest_with_equity(
    decisions_df: pd.DataFrame,
    soxl_prices: pd.Series,
    soxs_prices: pd.Series,
    initial_capital: float,
    trade_mgr: TradeManager,
) -> Tuple[pd.Series, pd.DataFrame]:
    """Internal: run day-by-day sim and return (equity_series, trades_df).

    This is the authoritative simulation loop.  All position sizing,
    fill logic, and state management happens here.
    """
    common_idx = (
        decisions_df.index
        .intersection(soxl_prices.index)
        .intersection(soxs_prices.index)
    )
    decisions_df = decisions_df.loc[common_idx]
    soxl_prices = soxl_prices.loc[common_idx]
    soxs_prices = soxs_prices.loc[common_idx]

    n = len(common_idx)
    nav = np.full(n, initial_capital, dtype=float)
    tm_state = TradeManagerState()
    cash = initial_capital
    trade_log: List[Dict] = []

    # Track open trades for PnL logging
    open_trades: Dict[str, Dict] = {}  # symbol -> {date, cost, qty}

    for i, date in enumerate(common_idx):
        row = decisions_df.loc[date]
        soxl_px = float(soxl_prices.loc[date])
        soxs_px = float(soxs_prices.loc[date])

        decision = DailyDecision(
            date=date,
            close=float(row.get("close", 0)),
            sma20=float(row.get("sma20", 0)),
            sma50=float(row.get("sma50", 0)),
            sma200=float(row.get("sma200", 0)),
            indicator_L=bool(row.get("indicator_L", False)),
            indicator_M=bool(row.get("indicator_M", False)),
            indicator_A=bool(row.get("indicator_A", False)),
            score=int(row.get("score", 0)),
            return_3m=float(row.get("return_3m", 0)),
            return_12m=float(row["return_12m"]) if pd.notna(row.get("return_12m")) else None,
            effective_state=EffectiveState(row["effective_state"]),
            transition_active=bool(row.get("transition_active", False)),
            transition_day=int(row.get("transition_day", 0)),
            engine_intent=EngineIntent(row.get("engine_intent", "NONE")),
        )

        portfolio_value = cash + _position_value(tm_state, soxl_px, soxs_px)
        intents, new_state = trade_mgr.process_day(
            decision, soxl_px, soxs_px, portfolio_value, tm_state,
        )

        for intent in intents:
            px = soxl_px if intent.symbol == "SOXL" else soxs_px

            if intent.side == OrderSide.BUY:
                qty = int(intent.notional // px) if px > 0 else 0
                if qty <= 0:
                    continue
                cost = qty * px
                if cost > cash:
                    qty = int(cash // px)
                    cost = qty * px
                if qty <= 0:
                    continue

                cash -= cost
                new_state = trade_mgr.apply_fill(
                    intent.symbol, OrderSide.BUY, qty, px, date, new_state,
                )

                sym = intent.symbol
                if sym not in open_trades:
                    open_trades[sym] = {"date": date, "avg_cost": px, "total_qty": qty}
                else:
                    old = open_trades[sym]
                    total = old["total_qty"] + qty
                    old["avg_cost"] = (old["avg_cost"] * old["total_qty"] + px * qty) / total
                    old["total_qty"] = total

            else:  # SELL
                pos = new_state.soxl if intent.symbol == "SOXL" else new_state.soxs
                qty = min(intent.qty, pos.qty)
                if qty <= 0:
                    continue

                avg_cost_before = pos.avg_cost
                cash += qty * px
                new_state = trade_mgr.apply_fill(
                    intent.symbol, OrderSide.SELL, qty, px, date, new_state,
                )
                pos_after = new_state.soxl if intent.symbol == "SOXL" else new_state.soxs
                pnl = (px - avg_cost_before) * qty

                if pos_after.qty <= 0 and intent.symbol in open_trades:
                    ot = open_trades.pop(intent.symbol)
                    trade_log.append({
                        "symbol": intent.symbol,
                        "entry_date": str(ot["date"]),
                        "exit_date": str(date),
                        "entry_price": round(ot["avg_cost"], 4),
                        "exit_price": round(px, 4),
                        "qty": qty,
                        "pnl": round(pnl, 2),
                        "return_pct": round((px / ot["avg_cost"] - 1) * 100, 4) if ot["avg_cost"] > 0 else 0,
                        "reason": intent.reason,
                    })

                    # Vampire injection for realized SOXS profit
                    if intent.symbol == "SOXS" and pnl > 0:
                        new_state = trade_mgr.on_realized_pnl(
                            "SOXS", pnl, decision.effective_state, soxl_px, new_state,
                        )

        tm_state = new_state
        nav[i] = cash + _position_value(tm_state, soxl_px, soxs_px)

    equity = pd.Series(nav, index=common_idx, name="equity")
    trades_df = pd.DataFrame(trade_log)

    return equity, trades_df


# ======================================================================= #
#  VectorBT integration: entry/exit array generation
# ======================================================================= #

def build_vbt_signals(
    soxx_df: pd.DataFrame,
    soxl_df: pd.DataFrame,
    soxs_df: pd.DataFrame,
    engine_params: Optional[Dict[str, Any]] = None,
    trade_config: Optional[TradeManagerConfig] = None,
) -> Dict[str, Any]:
    """Generate vectorbt-ready entry/exit arrays from the strategy.

    This bridges the FSM-based regime signals into boolean arrays
    that vectorbt's ``Portfolio.from_signals()`` can consume directly.

    Parameters
    ----------
    soxx_df : pd.DataFrame
        SOXX OHLCV.
    soxl_df, soxs_df : pd.DataFrame
        Execution-asset OHLCV.
    engine_params : dict, optional
    trade_config : TradeManagerConfig, optional

    Returns
    -------
    dict
        Keys: ``soxl_entries``, ``soxl_exits``, ``soxs_entries``,
        ``soxs_exits``, ``soxl_close``, ``soxs_close``, ``decisions_df``

    Example
    -------
    >>> signals = build_vbt_signals(soxx, soxl, soxs)
    >>> pf = vbt.Portfolio.from_signals(
    ...     signals["soxl_close"],
    ...     entries=signals["soxl_entries"],
    ...     exits=signals["soxl_exits"],
    ...     init_cash=100_000,
    ... )
    >>> print(pf.stats())
    """
    ep = engine_params or {}
    engine = StrategyEngine(**ep)
    mgr = TradeManager(config=trade_config)

    decisions_df = _run_fsm(soxx_df, engine)
    if decisions_df.empty:
        idx = soxx_df.index
        empty = pd.Series(False, index=idx)
        return {
            "soxl_entries": empty, "soxl_exits": empty,
            "soxs_entries": empty, "soxs_exits": empty,
            "soxl_close": soxl_df["close"], "soxs_close": soxs_df["close"],
            "decisions_df": pd.DataFrame(),
        }

    # Regime-based signals (simplified for vectorbt)
    common_idx = (
        decisions_df.index
        .intersection(soxl_df.index)
        .intersection(soxs_df.index)
    )
    dec = decisions_df.loc[common_idx]

    soxl_entries = (dec["effective_state"] == "BULL_ACTIVE") | (dec["effective_state"] == "TRANSITION")
    soxl_exits = (
        (dec["effective_state"].shift(1) == "BULL_ACTIVE") &
        (dec["effective_state"] != "BULL_ACTIVE") &
        (dec["effective_state"] != "TRANSITION")
    ).fillna(False)

    soxs_entries = dec["effective_state"] == "BEAR_ACTIVE"
    soxs_exits = (
        (dec["effective_state"].shift(1) == "BEAR_ACTIVE") &
        (dec["effective_state"] != "BEAR_ACTIVE")
    ).fillna(False)

    return {
        "soxl_entries": soxl_entries.reindex(soxl_df.loc[common_idx].index, fill_value=False),
        "soxl_exits": soxl_exits.reindex(soxl_df.loc[common_idx].index, fill_value=False),
        "soxs_entries": soxs_entries.reindex(soxs_df.loc[common_idx].index, fill_value=False),
        "soxs_exits": soxs_exits.reindex(soxs_df.loc[common_idx].index, fill_value=False),
        "soxl_close": soxl_df.loc[common_idx, "close"],
        "soxs_close": soxs_df.loc[common_idx, "close"],
        "decisions_df": dec,
    }


def run_vbt_portfolio(
    soxx_df: pd.DataFrame,
    soxl_df: pd.DataFrame,
    soxs_df: pd.DataFrame,
    initial_capital: float = 100_000.0,
    engine_params: Optional[Dict[str, Any]] = None,
    trade_config: Optional[TradeManagerConfig] = None,
) -> Dict[str, Any]:
    """Build vectorbt Portfolio objects for SOXL and SOXS legs.

    Returns
    -------
    dict
        ``soxl_portfolio``, ``soxs_portfolio``, ``signals``
    """
    signals = build_vbt_signals(soxx_df, soxl_df, soxs_df, engine_params, trade_config)

    soxl_pf = vbt.Portfolio.from_signals(
        signals["soxl_close"],
        entries=signals["soxl_entries"],
        exits=signals["soxl_exits"],
        init_cash=initial_capital * 0.7,  # 70% to SOXL leg
        freq="1D",
    )

    soxs_pf = vbt.Portfolio.from_signals(
        signals["soxs_close"],
        entries=signals["soxs_entries"],
        exits=signals["soxs_exits"],
        init_cash=initial_capital * 0.3,  # 30% to SOXS leg
        freq="1D",
    )

    return {
        "soxl_portfolio": soxl_pf,
        "soxs_portfolio": soxs_pf,
        "signals": signals,
    }


# ======================================================================= #
#  Public API: Parameter sweep
# ======================================================================= #

def sweep_parameters(
    soxx_df: pd.DataFrame,
    soxl_df: pd.DataFrame,
    soxs_df: pd.DataFrame,
    param_grid: Optional[Dict[str, List[Any]]] = None,
    initial_capital: float = 100_000.0,
) -> SweepResult:
    """Sweep over parameter combinations and collect metrics.

    Parameters
    ----------
    soxx_df, soxl_df, soxs_df : pd.DataFrame
        Price data.
    param_grid : dict, optional
        Mapping of TradeManagerConfig field names to lists of values.
        If None, uses a sensible default grid.
    initial_capital : float

    Returns
    -------
    SweepResult

    Example
    -------
    >>> grid = {
    ...     "soxl_max_slices": [25, 35, 45],
    ...     "soxs_take_profit": [0.06, 0.08, 0.10],
    ...     "soxs_cooldown_days": [2, 3, 5],
    ... }
    >>> result = sweep_parameters(soxx, soxl, soxs, param_grid=grid)
    >>> print(result.summary.sort_values("sharpe_ratio", ascending=False).head())
    """
    if param_grid is None:
        param_grid = {
            "soxl_max_slices": [25, 35, 45],
            "soxs_take_profit": [0.06, 0.08, 0.10, 0.12],
            "soxs_cooldown_days": [2, 3, 5],
        }

    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))

    results: List[BacktestResult] = []
    summary_rows: List[Dict[str, Any]] = []

    total = len(combos)
    for i, vals in enumerate(combos, 1):
        cfg_overrides = dict(zip(keys, vals))
        cfg = TradeManagerConfig(**cfg_overrides)

        bt = run_single_backtest(
            soxx_df, soxl_df, soxs_df,
            initial_capital=initial_capital,
            trade_config=cfg,
        )
        results.append(bt)

        row = {**cfg_overrides}
        row["sharpe_ratio"] = bt.metrics.sharpe_ratio
        row["total_return_pct"] = bt.metrics.total_return_pct
        row["cagr_pct"] = bt.metrics.cagr_pct
        row["max_drawdown_pct"] = bt.metrics.max_drawdown_pct
        row["max_dd_duration"] = bt.metrics.max_drawdown_duration_days
        row["calmar_ratio"] = bt.metrics.calmar_ratio
        row["total_trades"] = bt.metrics.total_trades
        row["win_rate_pct"] = bt.metrics.win_rate_pct
        row["avg_trade_pnl"] = bt.metrics.avg_trade_pnl
        row["profit_factor"] = bt.metrics.profit_factor
        summary_rows.append(row)

    summary = pd.DataFrame(summary_rows)

    # Find best by Sharpe and Calmar
    best_sharpe_idx = summary["sharpe_ratio"].idxmax() if not summary.empty else 0
    best_calmar_idx = summary["calmar_ratio"].idxmax() if not summary.empty else 0

    return SweepResult(
        results=results,
        summary=summary,
        best_sharpe=results[best_sharpe_idx].metrics if results else BacktestMetrics(),
        best_calmar=results[best_calmar_idx].metrics if results else BacktestMetrics(),
    )


# ======================================================================= #
#  Public API: Walk-forward validation
# ======================================================================= #

def walk_forward_quarterly(
    soxx_df: pd.DataFrame,
    soxl_df: pd.DataFrame,
    soxs_df: pd.DataFrame,
    is_quarters: int = 4,
    oos_quarters: int = 1,
    param_grid: Optional[Dict[str, List[Any]]] = None,
    initial_capital: float = 100_000.0,
) -> WalkForwardResult:
    """Walk-forward validation with quarterly IS/OOS windows.

    For each window:
    1. In-Sample (IS): sweep parameters, pick best Sharpe config.
    2. Out-of-Sample (OOS): run best config forward.

    Parameters
    ----------
    soxx_df, soxl_df, soxs_df : pd.DataFrame
        Full price data.
    is_quarters : int
        Number of quarters for in-sample training (default 4 = 1 year).
    oos_quarters : int
        Number of quarters for out-of-sample testing (default 1 = quarter).
    param_grid : dict, optional
        Parameter grid for IS sweep.
    initial_capital : float

    Returns
    -------
    WalkForwardResult
    """
    if param_grid is None:
        param_grid = {
            "soxl_max_slices": [25, 35, 45],
            "soxs_take_profit": [0.06, 0.08, 0.10],
            "soxs_cooldown_days": [2, 3, 5],
        }

    # Split data into quarterly chunks
    all_dates = soxx_df.index.sort_values()
    start = all_dates[0]
    end = all_dates[-1]

    quarters = pd.date_range(start=start, end=end, freq="QS")
    if len(quarters) < is_quarters + oos_quarters + 1:
        warnings.warn("Not enough data for walk-forward. Need more history.")
        empty_eq = pd.Series(dtype=float)
        return WalkForwardResult(
            windows=[], oos_equity=empty_eq,
            oos_metrics=BacktestMetrics(), summary=pd.DataFrame(),
        )

    windows: List[Dict[str, Any]] = []
    oos_equities: List[pd.Series] = []
    window_summary: List[Dict] = []

    total_is_oos = is_quarters + oos_quarters
    n_windows = len(quarters) - total_is_oos

    for w in range(n_windows):
        is_start = quarters[w]
        is_end = quarters[w + is_quarters]
        oos_start = is_end
        oos_end_idx = min(w + total_is_oos, len(quarters) - 1)
        oos_end = quarters[oos_end_idx]

        # Feed engine full history up to window end so the 252-day
        # warmup is always satisfied.  Then evaluate only IS / OOS dates.
        soxx_through_is = soxx_df.loc[:is_end]
        soxl_through_is = soxl_df.loc[:is_end]
        soxs_through_is = soxs_df.loc[:is_end]

        soxx_through_oos = soxx_df.loc[:oos_end]
        soxl_through_oos = soxl_df.loc[:oos_end]
        soxs_through_oos = soxs_df.loc[:oos_end]

        if soxx_through_is.empty or soxx_through_oos.empty:
            continue

        # IS: parameter sweep (full trailing history for warmup)
        sw = sweep_parameters(soxx_through_is, soxl_through_is, soxs_through_is,
                              param_grid=param_grid,
                              initial_capital=initial_capital)

        best_params = sw.best_sharpe.params
        trade_cfg_args = {k: v for k, v in best_params.items()
                         if k in TradeManagerConfig.__dataclass_fields__}
        best_cfg = TradeManagerConfig(**trade_cfg_args) if trade_cfg_args else TradeManagerConfig()

        # OOS: run with full trailing data, then slice equity to OOS dates
        oos_bt = run_single_backtest(
            soxx_through_oos, soxl_through_oos, soxs_through_oos,
            initial_capital=initial_capital,
            trade_config=best_cfg,
        )

        # Slice to OOS evaluation window only
        oos_equity_slice = oos_bt.equity_curve.loc[
            (oos_bt.equity_curve.index >= oos_start) &
            (oos_bt.equity_curve.index <= oos_end)
        ]
        oos_trades_slice = pd.DataFrame()
        if not oos_bt.trades.empty and "exit_date" in oos_bt.trades.columns:
            oos_trades_slice = oos_bt.trades[
                oos_bt.trades["exit_date"].astype(str) >= str(oos_start.date())
            ]
        oos_dec_slice = oos_bt.decisions.loc[
            (oos_bt.decisions.index >= oos_start) &
            (oos_bt.decisions.index <= oos_end)
        ] if not oos_bt.decisions.empty else pd.DataFrame()

        oos_metrics = _compute_metrics(
            oos_equity_slice, oos_trades_slice, oos_dec_slice, initial_capital,
        )

        window_info = {
            "window": w,
            "is_start": str(is_start.date()),
            "is_end": str(is_end.date()),
            "oos_start": str(oos_start.date()),
            "oos_end": str(oos_end.date()),
            "is_best_sharpe": sw.best_sharpe.sharpe_ratio,
            "is_best_params": best_params,
            "oos_sharpe": oos_metrics.sharpe_ratio,
            "oos_return_pct": oos_metrics.total_return_pct,
            "oos_mdd_pct": oos_metrics.max_drawdown_pct,
            "oos_trades": oos_metrics.total_trades,
        }
        windows.append(window_info)
        window_summary.append(window_info)

        if not oos_equity_slice.empty:
            oos_equities.append(oos_equity_slice)

    # Concatenate OOS equity curves (rescale to continuous sequence)
    if oos_equities:
        chained = oos_equities[0].copy()
        for eq in oos_equities[1:]:
            if chained.empty or eq.empty:
                continue
            scale = chained.iloc[-1] / eq.iloc[0]
            scaled = eq * scale
            # Remove overlap
            scaled = scaled.loc[scaled.index > chained.index[-1]]
            chained = pd.concat([chained, scaled])
        oos_equity = chained
    else:
        oos_equity = pd.Series(dtype=float)

    oos_metrics = BacktestMetrics()
    if not oos_equity.empty:
        oos_metrics = _compute_metrics(oos_equity, pd.DataFrame(), pd.DataFrame(), initial_capital)

    return WalkForwardResult(
        windows=windows,
        oos_equity=oos_equity,
        oos_metrics=oos_metrics,
        summary=pd.DataFrame(window_summary),
    )


# ======================================================================= #
#  Public API: Report export
# ======================================================================= #

def export_report(
    result: BacktestResult,
    output_dir: str = "backtest_output",
    prefix: str = "bt",
) -> Dict[str, str]:
    """Export backtest results to CSV and JSON files.

    Parameters
    ----------
    result : BacktestResult
    output_dir : str
        Directory to write files into (created if needed).
    prefix : str
        Filename prefix.

    Returns
    -------
    dict
        Mapping of label → file path for each exported file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, str] = {}

    # Metrics JSON
    metrics_path = out / f"{prefix}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(asdict(result.metrics), f, indent=2, default=str)
    paths["metrics"] = str(metrics_path)

    # Equity curve CSV
    if not result.equity_curve.empty:
        eq_path = out / f"{prefix}_equity.csv"
        eq_df = result.equity_curve.to_frame("equity")
        eq_df.index.name = "date"
        eq_df.to_csv(eq_path)
        paths["equity"] = str(eq_path)

    # Trades CSV
    if not result.trades.empty:
        trades_path = out / f"{prefix}_trades.csv"
        result.trades.to_csv(trades_path, index=False)
        paths["trades"] = str(trades_path)

    # Decisions CSV
    if not result.decisions.empty:
        dec_path = out / f"{prefix}_decisions.csv"
        result.decisions.to_csv(dec_path)
        paths["decisions"] = str(dec_path)

    # Drawdown CSV
    if not result.drawdown_series.empty:
        dd_path = out / f"{prefix}_drawdown.csv"
        result.drawdown_series.to_frame("drawdown").to_csv(dd_path)
        paths["drawdown"] = str(dd_path)

    return paths


def export_sweep_report(
    result: SweepResult,
    output_dir: str = "backtest_output",
) -> Dict[str, str]:
    """Export sweep summary to CSV + JSON.

    Parameters
    ----------
    result : SweepResult
    output_dir : str

    Returns
    -------
    dict
        Mapping of label → file path.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}

    summary_csv = out / "sweep_summary.csv"
    result.summary.to_csv(summary_csv, index=False)
    paths["summary_csv"] = str(summary_csv)

    best_json = out / "sweep_best.json"
    with open(best_json, "w") as f:
        json.dump({
            "best_sharpe": asdict(result.best_sharpe),
            "best_calmar": asdict(result.best_calmar),
        }, f, indent=2, default=str)
    paths["best_json"] = str(best_json)

    return paths


def export_walkforward_report(
    result: WalkForwardResult,
    output_dir: str = "backtest_output",
) -> Dict[str, str]:
    """Export walk-forward results.

    Parameters
    ----------
    result : WalkForwardResult
    output_dir : str

    Returns
    -------
    dict
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}

    if not result.summary.empty:
        wf_csv = out / "walkforward_summary.csv"
        result.summary.to_csv(wf_csv, index=False)
        paths["summary"] = str(wf_csv)

    if not result.oos_equity.empty:
        eq_csv = out / "walkforward_oos_equity.csv"
        result.oos_equity.to_frame("equity").to_csv(eq_csv)
        paths["oos_equity"] = str(eq_csv)

    wf_json = out / "walkforward_metrics.json"
    with open(wf_json, "w") as f:
        json.dump({
            "oos_metrics": asdict(result.oos_metrics),
            "n_windows": len(result.windows),
            "windows": result.windows,
        }, f, indent=2, default=str)
    paths["metrics"] = str(wf_json)

    return paths


# ======================================================================= #
#  Convenience: quick summary printer
# ======================================================================= #

def print_metrics(m: BacktestMetrics) -> None:
    """Pretty-print a BacktestMetrics object."""
    print("=" * 56)
    print("  Alpha Predator v4.1 - Backtest Report")
    print("=" * 56)
    print(f"  Total Return     : {m.total_return_pct:>10.2f} %")
    print(f"  CAGR             : {m.cagr_pct:>10.2f} %")
    print(f"  Sharpe Ratio     : {m.sharpe_ratio:>10.4f}")
    print(f"  Sortino Ratio    : {m.sortino_ratio:>10.4f}")
    print(f"  Max Drawdown     : {m.max_drawdown_pct:>10.2f} %")
    print(f"  Max DD Duration  : {m.max_drawdown_duration_days:>10d} days")
    print(f"  Calmar Ratio     : {m.calmar_ratio:>10.4f}")
    print("-" * 56)
    print(f"  Total Trades     : {m.total_trades:>10d}")
    print(f"  Win Rate         : {m.win_rate_pct:>10.2f} %")
    print(f"  Avg Trade P/L    : ${m.avg_trade_pnl:>9.2f}")
    print(f"  Avg Win          : ${m.avg_win:>9.2f}")
    print(f"  Avg Loss         : ${m.avg_loss:>9.2f}")
    print(f"  Profit Factor    : {m.profit_factor:>10.4f}")
    print("-" * 56)
    print(f"  Bull Days        : {m.bull_days:>10d}")
    print(f"  Bear Days        : {m.bear_days:>10d}")
    print(f"  Neutral Days     : {m.neutral_days:>10d}")
    print(f"  Transition Days  : {m.transition_days:>10d}")
    print("=" * 56)


# ======================================================================= #
#  CLI / quick demo
# ======================================================================= #

def run_demo() -> None:
    """Run a self-contained demo with synthetic data.

    Demonstrates:
    1. Single backtest with default params
    2. Parameter sweep over slices/TP/cooldown
    3. Walk-forward quarterly validation
    4. Report export
    """
    print("\n[1/4] Generating 5 years of synthetic data...")
    soxx, soxl, soxs = generate_sample_data(years=5, seed=42)
    print(f"      SOXX: {len(soxx)} days, range {soxx.index[0].date()} → {soxx.index[-1].date()}")

    # ------------------------------------------------------------------
    print("\n[2/4] Running single backtest (default params)...")
    result = run_single_backtest(soxx, soxl, soxs, initial_capital=100_000)
    print_metrics(result.metrics)

    # ------------------------------------------------------------------
    print("\n[3/4] Parameter sweep (3 × 3 × 2 = 18 combos)...")
    grid = {
        "soxl_max_slices": [25, 35, 45],
        "soxs_take_profit": [0.06, 0.08, 0.10],
        "soxs_cooldown_days": [2, 3],
    }
    sw = sweep_parameters(soxx, soxl, soxs, param_grid=grid)
    print(f"      Best Sharpe: {sw.best_sharpe.sharpe_ratio:.4f}")
    print(f"      Best Calmar: {sw.best_calmar.calmar_ratio:.4f}")
    print(f"\n      Top 5 combos by Sharpe:")
    top5 = sw.summary.sort_values("sharpe_ratio", ascending=False).head()
    print(top5.to_string(index=False))

    # ------------------------------------------------------------------
    print("\n[4/4] Walk-forward quarterly (IS=4Q, OOS=1Q)...")
    wf = walk_forward_quarterly(
        soxx, soxl, soxs,
        is_quarters=4, oos_quarters=1,
        param_grid={
            "soxl_max_slices": [25, 35],
            "soxs_take_profit": [0.06, 0.08],
            "soxs_cooldown_days": [3],
        },
    )
    if wf.windows:
        print(f"      {len(wf.windows)} walk-forward windows completed")
        print(f"      OOS aggregate Sharpe: {wf.oos_metrics.sharpe_ratio:.4f}")
        print(f"      OOS aggregate Return: {wf.oos_metrics.total_return_pct:.2f}%")
    else:
        print("      (insufficient data for walk-forward)")

    # ------------------------------------------------------------------
    print("\n[+] Exporting reports...")
    paths = export_report(result, output_dir="backtest_output", prefix="demo")
    for label, path in paths.items():
        print(f"      {label}: {path}")

    if not sw.summary.empty:
        sp = export_sweep_report(sw, output_dir="backtest_output")
        for label, path in sp.items():
            print(f"      {label}: {path}")

    if wf.windows:
        wp = export_walkforward_report(wf, output_dir="backtest_output")
        for label, path in wp.items():
            print(f"      {label}: {path}")

    print("\nDone.")


if __name__ == "__main__":
    run_demo()
