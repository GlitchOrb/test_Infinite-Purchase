"""
backtest/config.py
==================
Configuration for the backtesting engine.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestConfig:
    """Immutable configuration for a backtest run."""
    initial_capital: float = 100_000.0
    commission_pct: float = 0.001  # 0.1% per trade value
    slippage_pct: float = 0.001    # 0.1% price impact
    risk_free_rate: float = 0.02   # Annualized risk-free rate for Sharpe
    
    # Note: Strategy/TradeManager configs are passed separately if needed.