from __future__ import annotations

import pandas as pd

from indicators.base import IndicatorBase


class OBVIndicator(IndicatorBase):
    @property
    def name(self) -> str:
        return "OBV"

    @property
    def inputs_required(self):
        return ("close", "volume")

    @property
    def render_location(self) -> str:
        return "subpanel"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        self.validate_inputs(dataframe)
        close = dataframe["close"].astype(float)
        volume = dataframe["volume"].astype(float)

        direction = close.diff().fillna(0.0)
        signed_volume = volume.where(direction > 0, -volume.where(direction < 0, 0.0))
        signed_volume = signed_volume.fillna(0.0)
        return signed_volume.cumsum()
