"""ADX — Average Directional Index (trend-strength)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from indicators.base import IndicatorBase


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Division that returns 0.0 where denominator is 0 (no object dtype)."""
    denom = denominator.to_numpy(dtype=float, na_value=0.0)
    num = numerator.to_numpy(dtype=float, na_value=0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = num / denom
    result = np.where(np.isfinite(raw), raw, 0.0)
    return pd.Series(result, index=numerator.index, dtype=float)


class ADXIndicator(IndicatorBase):
    """Average Directional Index.

    Parameters
    ----------
    period : int
        Smoothing period (Wilder's method). Default 14.
    output : ``"adx"`` | ``"plus_di"`` | ``"minus_di"``
        Which series to return. Default ``"adx"``.
    """

    def __init__(
        self,
        period: int = 14,
        *,
        output: Literal["adx", "plus_di", "minus_di"] = "adx",
    ) -> None:
        if period <= 0:
            raise RuntimeError("ADX period must be positive")
        if output not in ("adx", "plus_di", "minus_di"):
            raise RuntimeError("ADX output must be 'adx', 'plus_di', or 'minus_di'")
        self.period = period
        self.output = output

    @property
    def name(self) -> str:
        suffix = {"adx": "", "plus_di": "_plusDI", "minus_di": "_minusDI"}[self.output]
        return f"ADX{self.period}{suffix}"

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

        # True Range
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        # Directional Movement
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        # Wilder's smoothing (alpha = 1 / period)
        alpha = 1.0 / self.period
        atr = tr.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        smooth_plus = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        smooth_minus = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()

        plus_di = _safe_div(smooth_plus, atr) * 100
        minus_di = _safe_div(smooth_minus, atr) * 100

        if self.output == "plus_di":
            return plus_di
        if self.output == "minus_di":
            return minus_di

        # DX → ADX
        di_sum = plus_di + minus_di
        dx = _safe_div((plus_di - minus_di).abs(), di_sum) * 100
        adx = dx.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        return adx

    def compute_all(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Return DataFrame with ``adx``, ``plus_di``, ``minus_di`` columns."""
        self.validate_inputs(dataframe)
        high = dataframe["high"].astype(float)
        low = dataframe["low"].astype(float)
        close = dataframe["close"].astype(float)
        prev_close = close.shift(1)

        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)

        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        alpha = 1.0 / self.period
        atr = tr.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        smooth_plus = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        smooth_minus = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()

        plus_di = _safe_div(smooth_plus, atr) * 100
        minus_di = _safe_div(smooth_minus, atr) * 100

        di_sum = plus_di + minus_di
        dx = _safe_div((plus_di - minus_di).abs(), di_sum) * 100
        adx = dx.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()

        return pd.DataFrame(
            {"adx": adx, "plus_di": plus_di, "minus_di": minus_di},
            index=dataframe.index,
        )
