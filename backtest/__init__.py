"""backtest — vectorised backtesting engine with structured reporting.

Public API:

    from backtest import (
        Backtester, BacktestConfig, BacktestReport,
        TradeRecord,
    )
"""

from backtest.config import BacktestConfig
from backtest.engine import Backtester
from backtest.report import BacktestReport, TradeRecord

__all__ = [
    "Backtester",
    "BacktestConfig",
    "BacktestReport",
    "TradeRecord",
]
