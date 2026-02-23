"""
backtest/report.py
==================
Structured output containers for backtest results.
"""

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class TradeRecord:
    """Record of a completed round-trip trade (FIFO matched)."""
    symbol: str
    side: str  # "LONG" (since we only buy long positions in this strategy)
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    return_pct: float
    holding_days: int


@dataclass
class BacktestReport:
    """Aggregate performance metrics and time-series data."""
    initial_capital: float
    final_capital: float
    total_return: float
    cagr: float
    mdd: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    
    equity_curve: pd.Series  # Index: Date, Value: Equity
    trades: List[TradeRecord]