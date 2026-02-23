"""Bollinger Bands — volatility-envelope indicator."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from indicators.base import IndicatorBase

_OUTPUT_TYPES = ("upper", "middle", "lower", "bandwidth", "percent_b")


class BollingerBandsIndicator(IndicatorBase):
    """Bollinger Bands.

    Parameters
    ----------
    period : int
        SMA lookback window. Default 20.
    num_std : float
        Number of standard-deviation widths. Default 2.0.
    output : ``"upper"`` | ``"middle"`` | ``"lower"`` | ``"bandwidth"`` | ``"percent_b"``
        Which series to return from :meth:`compute`. Default ``"middle"``.
    """

    def __init__(
        self,
        period: int = 20,
        num_std: float = 2.0,
        *,
        output: Literal["upper", "middle", "lower", "bandwidth", "percent_b"] = "middle",
    ) -> None:
        if period <= 0:
            raise RuntimeError("Bollinger period must be positive")
        if num_std <= 0:
            raise RuntimeError("Bollinger num_std must be positive")
        if output not in _OUTPUT_TYPES:
            raise RuntimeError(f"Bollinger output must be one of {_OUTPUT_TYPES}")
        self.period = period
        self.num_std = num_std
        self.output = output

    @property
    def name(self) -> str:
        suffix = {"upper": "_upper", "middle": "_mid", "lower": "_lower",
                  "bandwidth": "_bw", "percent_b": "_pctb"}[self.output]
        return f"BB{self.period}{suffix}"

    @property
    def inputs_required(self):
        return ("close",)

    @property
    def render_location(self) -> str:
        if self.output in ("bandwidth", "percent_b"):
            return "subpanel"
        return "overlay"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        close = dataframe["close"].astype(float)
        rolling = close.rolling(window=self.period, min_periods=self.period)
        middle = rolling.mean()
        std = rolling.std(ddof=0)

        upper = middle + self.num_std * std
        lower = middle - self.num_std * std

        if self.output == "upper":
            return upper
        if self.output == "lower":
            return lower
        if self.output == "bandwidth":
            return ((upper - lower) / middle).fillna(0.0)
        if self.output == "percent_b":
            band_width = upper - lower
            return ((close - lower) / band_width.replace(0, pd.NA)).fillna(0.5)
        return middle

    def compute_all(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Return DataFrame with ``upper``, ``middle``, ``lower``,
        ``bandwidth``, ``percent_b`` columns."""
        self.validate_inputs(dataframe)
        close = dataframe["close"].astype(float)
        rolling = close.rolling(window=self.period, min_periods=self.period)
        middle = rolling.mean()
        std = rolling.std(ddof=0)

        upper = middle + self.num_std * std
        lower = middle - self.num_std * std
        band_width = upper - lower

        return pd.DataFrame(
            {
                "upper": upper,
                "middle": middle,
                "lower": lower,
                "bandwidth": (band_width / middle).fillna(0.0),
                "percent_b": ((close - lower) / band_width.replace(0, pd.NA)).fillna(0.5),
            },
            index=dataframe.index,
        )
