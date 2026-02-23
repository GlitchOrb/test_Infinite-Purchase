"""VolumeSpike — volume anomaly detector."""

from __future__ import annotations

import pandas as pd

from indicators.base import IndicatorBase


class VolumeSpikeIndicator(IndicatorBase):
    """Volume Spike detector.

    Computes the ratio of current volume to its rolling average.
    A ratio > ``threshold`` indicates abnormally high volume.

    Parameters
    ----------
    period : int
        Lookback for the rolling average volume. Default 20.
    threshold : float
        Spike threshold (informational; stored for downstream use).
        Default 2.0.
    """

    def __init__(self, period: int = 20, *, threshold: float = 2.0) -> None:
        if period <= 0:
            raise RuntimeError("VolumeSpike period must be positive")
        if threshold <= 0:
            raise RuntimeError("VolumeSpike threshold must be positive")
        self.period = period
        self.threshold = threshold

    @property
    def name(self) -> str:
        return f"VolSpike{self.period}"

    @property
    def inputs_required(self):
        return ("volume",)

    @property
    def render_location(self) -> str:
        return "subpanel"

    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        """Return the volume / rolling-avg-volume ratio."""
        self.validate_inputs(dataframe)
        volume = dataframe["volume"].astype(float)
        avg_vol = volume.rolling(window=self.period, min_periods=1).mean()
        return (volume / avg_vol.replace(0, pd.NA)).fillna(1.0)

    def is_spike(self, dataframe: pd.DataFrame) -> pd.Series:
        """Return a boolean Series that is ``True`` when ratio ≥ threshold."""
        return self.compute(dataframe) >= self.threshold
