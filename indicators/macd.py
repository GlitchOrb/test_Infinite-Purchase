"""MACD — Moving Average Convergence / Divergence."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from indicators.base import IndicatorBase

_OUTPUT_TYPES = ("macd", "signal", "histogram")


class MACDIndicator(IndicatorBase):
    """Moving Average Convergence / Divergence.

    Parameters
    ----------
    fast : int
        Fast EMA span. Default 12.
    slow : int
        Slow EMA span. Default 26.
    signal : int
        Signal EMA span. Default 9.
    output : ``"macd"`` | ``"signal"`` | ``"histogram"``
        Which series to return from :meth:`compute`. Default ``"macd"``.
    """

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        *,
        output: Literal["macd", "signal", "histogram"] = "macd",
    ) -> None:
        if fast <= 0 or slow <= 0 or signal <= 0:
            raise RuntimeError("MACD periods must be positive")
        if fast >= slow:
            raise RuntimeError("MACD fast period must be less than slow period")
        if output not in _OUTPUT_TYPES:
            raise RuntimeError(f"MACD output must be one of {_OUTPUT_TYPES}")
        self.fast = fast
        self.slow = slow
        self._signal_period = signal
        self.output = output

    @property
    def name(self) -> str:
        suffix = {"macd": "", "signal": "_signal", "histogram": "_hist"}[self.output]
        return f"MACD{self.fast}_{self.slow}_{self._signal_period}{suffix}"

    @property
    def inputs_required(self):
        return ("close",)

    @property
    def render_location(self) -> str:
        return "subpanel"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        close = dataframe["close"].astype(float)

        ema_fast = close.ewm(span=self.fast, adjust=False, min_periods=self.fast).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False, min_periods=self.slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(
            span=self._signal_period, adjust=False, min_periods=self._signal_period
        ).mean()

        if self.output == "signal":
            return signal_line
        if self.output == "histogram":
            return macd_line - signal_line
        return macd_line

    # ── convenience: compute all three at once ──

    def compute_all(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame with ``macd``, ``signal``, ``histogram`` columns."""
        self.validate_inputs(dataframe)
        close = dataframe["close"].astype(float)

        ema_fast = close.ewm(span=self.fast, adjust=False, min_periods=self.fast).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False, min_periods=self.slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(
            span=self._signal_period, adjust=False, min_periods=self._signal_period
        ).mean()

        return pd.DataFrame(
            {
                "macd": macd_line,
                "signal": signal_line,
                "histogram": macd_line - signal_line,
            },
            index=dataframe.index,
        )
