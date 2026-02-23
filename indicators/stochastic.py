"""Stochastic Oscillator — %K / %D momentum indicator."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from indicators.base import IndicatorBase


class StochasticIndicator(IndicatorBase):
    """Stochastic Oscillator.

    Parameters
    ----------
    k_period : int
        Lookback for raw %K. Default 14.
    k_smooth : int
        SMA smoothing applied to raw %K to produce slow %K. Default 3.
    d_period : int
        SMA smoothing of slow %K to produce %D. Default 3.
    output : ``"k"`` | ``"d"``
        Which series to return. Default ``"k"`` (slow %K).
    """

    def __init__(
        self,
        k_period: int = 14,
        k_smooth: int = 3,
        d_period: int = 3,
        *,
        output: Literal["k", "d"] = "k",
    ) -> None:
        if k_period <= 0 or k_smooth <= 0 or d_period <= 0:
            raise RuntimeError("Stochastic periods must be positive")
        if output not in ("k", "d"):
            raise RuntimeError("Stochastic output must be 'k' or 'd'")
        self.k_period = k_period
        self.k_smooth = k_smooth
        self.d_period = d_period
        self.output = output

    @property
    def name(self) -> str:
        suffix = "K" if self.output == "k" else "D"
        return f"STOCH{self.k_period}_{suffix}"

    @property
    def inputs_required(self):
        return ("high", "low", "close")

    @property
    def render_location(self) -> str:
        return "subpanel"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        high = dataframe["high"].astype(float)
        low = dataframe["low"].astype(float)
        close = dataframe["close"].astype(float)

        highest = high.rolling(window=self.k_period, min_periods=self.k_period).max()
        lowest = low.rolling(window=self.k_period, min_periods=self.k_period).min()

        range_ = highest - lowest
        fast_k = ((close - lowest) / range_.replace(0, pd.NA) * 100).fillna(50.0)

        # Slow %K = SMA of fast %K
        slow_k = fast_k.rolling(window=self.k_smooth, min_periods=1).mean()

        if self.output == "d":
            return slow_k.rolling(window=self.d_period, min_periods=1).mean()
        return slow_k

    def compute_all(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Return DataFrame with ``fast_k``, ``slow_k``, ``d`` columns."""
        self.validate_inputs(dataframe)
        high = dataframe["high"].astype(float)
        low = dataframe["low"].astype(float)
        close = dataframe["close"].astype(float)

        highest = high.rolling(window=self.k_period, min_periods=self.k_period).max()
        lowest = low.rolling(window=self.k_period, min_periods=self.k_period).min()

        range_ = highest - lowest
        fast_k = ((close - lowest) / range_.replace(0, pd.NA) * 100).fillna(50.0)
        slow_k = fast_k.rolling(window=self.k_smooth, min_periods=1).mean()
        d_line = slow_k.rolling(window=self.d_period, min_periods=1).mean()

        return pd.DataFrame(
            {"fast_k": fast_k, "slow_k": slow_k, "d": d_line},
            index=dataframe.index,
        )
