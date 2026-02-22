from __future__ import annotations

import pandas as pd

from indicators.base import IndicatorBase


class RSIIndicator(IndicatorBase):
    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise RuntimeError("RSI period must be positive")
        self.period = period

    @property
    def name(self) -> str:
        return f"RSI{self.period}"

    @property
    def inputs_required(self):
        return ("close",)

    @property
    def render_location(self) -> str:
        return "subpanel"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        close = dataframe["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        avg_gain = gain.ewm(alpha=1 / self.period, adjust=False, min_periods=self.period).mean()
        avg_loss = loss.ewm(alpha=1 / self.period, adjust=False, min_periods=self.period).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)
