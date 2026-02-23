"""VWAP — Volume Weighted Average Price."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from indicators.base import IndicatorBase


class VWAPIndicator(IndicatorBase):
    """Volume Weighted Average Price.

    Computed as cumulative (typical_price × volume) / cumulative volume.

    Parameters
    ----------
    session_col : str or None
        If provided, VWAP resets at each unique value in this column
        (e.g. a date column for daily resets).  When ``None`` the VWAP
        is computed cumulatively across the entire dataframe.
    """

    def __init__(self, *, session_col: Optional[str] = None) -> None:
        self.session_col = session_col

    @property
    def name(self) -> str:
        return "VWAP"

    @property
    def inputs_required(self):
        cols = ["high", "low", "close", "volume"]
        if self.session_col:
            cols.append(self.session_col)
        return tuple(cols)

    @property
    def render_location(self) -> str:
        return "overlay"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        typical = (
            dataframe["high"].astype(float)
            + dataframe["low"].astype(float)
            + dataframe["close"].astype(float)
        ) / 3.0
        volume = dataframe["volume"].astype(float)
        tp_vol = typical * volume

        if self.session_col and self.session_col in dataframe.columns:
            cum_tp_vol = tp_vol.groupby(dataframe[self.session_col]).cumsum()
            cum_vol = volume.groupby(dataframe[self.session_col]).cumsum()
        else:
            cum_tp_vol = tp_vol.cumsum()
            cum_vol = volume.cumsum()

        return (cum_tp_vol / cum_vol.replace(0, pd.NA)).fillna(typical)
