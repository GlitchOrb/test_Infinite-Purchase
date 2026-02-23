"""EMA — Exponential Moving Average."""

from __future__ import annotations

import pandas as pd

from indicators.base import IndicatorBase


class EMAIndicator(IndicatorBase):
    """Exponential Moving Average.

    Parameters
    ----------
    period : int
        Lookback window (span) for the EMA calculation.
    source : str
        Column name to compute EMA on. Default ``"close"``.
    """

    def __init__(self, period: int = 20, *, source: str = "close") -> None:
        if period <= 0:
            raise RuntimeError("EMA period must be positive")
        self.period = period
        self.source = source

    @property
    def name(self) -> str:
        return f"EMA{self.period}"

    @property
    def inputs_required(self):
        return (self.source,)

    @property
    def render_location(self) -> str:
        return "overlay"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        return (
            dataframe[self.source]
            .astype(float)
            .ewm(span=self.period, adjust=False, min_periods=self.period)
            .mean()
        )
