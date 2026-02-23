"""ATR — Average True Range."""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.base import IndicatorBase


class ATRIndicator(IndicatorBase):
    """Average True Range — volatility measure.

    Parameters
    ----------
    period : int
        Smoothing window (Wilder's method). Default 14.
    """

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise RuntimeError("ATR period must be positive")
        self.period = period

    @property
    def name(self) -> str:
        return f"ATR{self.period}"

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
        prev_close = close.shift(1)

        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        # Wilder's smoothing (equivalent to EMA with alpha = 1/period)
        return tr.ewm(alpha=1.0 / self.period, adjust=False, min_periods=self.period).mean()
