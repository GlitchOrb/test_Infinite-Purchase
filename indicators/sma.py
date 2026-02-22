from __future__ import annotations

import pandas as pd

from indicators.base import IndicatorBase


class SMAIndicator(IndicatorBase):
    def __init__(self, period: int) -> None:
        if period <= 0:
            raise RuntimeError("SMA period must be positive")
        self.period = period

    @property
    def name(self) -> str:
        return f"SMA{self.period}"

    @property
    def inputs_required(self):
        return ("close",)

    @property
    def render_location(self) -> str:
        return "overlay"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        return dataframe["close"].rolling(window=self.period, min_periods=self.period).mean()
