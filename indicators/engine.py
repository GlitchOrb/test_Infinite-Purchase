"""IndicatorEngine — modular orchestrator for technical indicators.

Usage
-----
>>> from indicators.engine import IndicatorEngine
>>> engine = IndicatorEngine()
>>> engine.add("SMA", period=20)
>>> engine.add("RSI", period=14)
>>> engine.add("MACD", fast=12, slow=26, signal=9)
>>> result = engine.compute(ohlcv_df)  # returns Dict[str, pd.Series]

All indicators are **stateless** — the same input always produces the same
output with no hidden side-effects.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Type

import pandas as pd

from indicators.adx import ADXIndicator
from indicators.atr import ATRIndicator
from indicators.base import IndicatorBase
from indicators.bollinger import BollingerBandsIndicator
from indicators.ema import EMAIndicator
from indicators.macd import MACDIndicator
from indicators.obv import OBVIndicator
from indicators.rsi import RSIIndicator
from indicators.sma import SMAIndicator
from indicators.stochastic import StochasticIndicator
from indicators.volume_spike import VolumeSpikeIndicator
from indicators.vwap import VWAPIndicator

log = logging.getLogger(__name__)

# ── canonical registry: short name → class ──
_INDICATOR_REGISTRY: Dict[str, Type[IndicatorBase]] = {
    "SMA": SMAIndicator,
    "EMA": EMAIndicator,
    "RSI": RSIIndicator,
    "MACD": MACDIndicator,
    "BollingerBands": BollingerBandsIndicator,
    "BB": BollingerBandsIndicator,          # alias
    "VWAP": VWAPIndicator,
    "ATR": ATRIndicator,
    "Stochastic": StochasticIndicator,
    "STOCH": StochasticIndicator,           # alias
    "ADX": ADXIndicator,
    "VolumeSpike": VolumeSpikeIndicator,
    "OBV": OBVIndicator,
}

# Required OHLCV columns for input validation
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


class IndicatorEngine:
    """Stateless orchestrator that configures, manages, and batch-computes
    a pipeline of technical indicators.

    Parameters
    ----------
    strict : bool
        If ``True`` (default), raise on any indicator compute error.
        If ``False``, log a warning and skip the failed indicator.
    """

    def __init__(self, *, strict: bool = True) -> None:
        self._indicators: OrderedDict[str, IndicatorBase] = OrderedDict()
        self._strict = strict

    # ── indicator management ──

    def add(self, indicator_type: str, *, name: str | None = None, **params: Any) -> str:
        """Create an indicator from the registry and add it to the engine.

        Parameters
        ----------
        indicator_type : str
            One of the registered short names (e.g. ``"SMA"``, ``"MACD"``).
        name : str or None
            Override the auto-generated key used in the result dict.
            If ``None``, the indicator's ``.name`` property is used.
        **params
            Forwarded to the indicator constructor.

        Returns
        -------
        str
            The key under which this indicator will appear in ``compute()`` output.
        """
        cls = _INDICATOR_REGISTRY.get(indicator_type)
        if cls is None:
            raise ValueError(
                f"Unknown indicator type '{indicator_type}'. "
                f"Available: {sorted(_INDICATOR_REGISTRY)}"
            )
        indicator = cls(**params)
        key = name or indicator.name
        self._indicators[key] = indicator
        log.debug("IndicatorEngine: added %s (key=%s, params=%s)", indicator_type, key, params)
        return key

    def add_instance(self, indicator: IndicatorBase, *, name: str | None = None) -> str:
        """Add a pre-constructed indicator instance directly.

        Returns
        -------
        str
            The key under which this indicator will appear in ``compute()`` output.
        """
        key = name or indicator.name
        self._indicators[key] = indicator
        return key

    def remove(self, key: str) -> None:
        """Remove an indicator by its key."""
        self._indicators.pop(key, None)

    def clear(self) -> None:
        """Remove all indicators."""
        self._indicators.clear()

    @property
    def indicator_keys(self) -> List[str]:
        """Return the ordered list of indicator keys."""
        return list(self._indicators.keys())

    @property
    def indicator_count(self) -> int:
        return len(self._indicators)

    def get(self, key: str) -> Optional[IndicatorBase]:
        """Retrieve an indicator instance by key."""
        return self._indicators.get(key)

    # ── batch computation ──

    def compute(self, dataframe: pd.DataFrame) -> Dict[str, pd.Series]:
        """Run every registered indicator against *dataframe*.

        Parameters
        ----------
        dataframe : pd.DataFrame
            Must contain at least the columns required by each indicator
            (typically OHLCV: open, high, low, close, volume).

        Returns
        -------
        Dict[str, pd.Series]
            Mapping from indicator key → computed Series, in registration order.
        """
        if dataframe.empty:
            return {key: pd.Series(dtype=float) for key in self._indicators}

        results: Dict[str, pd.Series] = {}
        for key, indicator in self._indicators.items():
            try:
                results[key] = indicator.compute(dataframe)
            except Exception as exc:
                if self._strict:
                    raise RuntimeError(
                        f"Indicator '{key}' failed: {exc}"
                    ) from exc
                log.warning("Indicator '%s' skipped: %s", key, exc)
        return results

    def compute_to_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Run all indicators and return a new DataFrame with indicator columns
        appended to the original OHLCV data.

        The original ``dataframe`` is **not** mutated.
        """
        results = self.compute(dataframe)
        out = dataframe.copy()
        for key, series in results.items():
            out[key] = series
        return out

    # ── introspection ──

    @staticmethod
    def available_indicators() -> List[str]:
        """Return the list of indicator type names that can be passed to :meth:`add`."""
        return sorted(_INDICATOR_REGISTRY.keys())

    def describe(self) -> Dict[str, Dict[str, Any]]:
        """Return a summary dict ``{key: {type, name, render_location, inputs}}``."""
        out: Dict[str, Dict[str, Any]] = {}
        for key, ind in self._indicators.items():
            out[key] = {
                "type": type(ind).__name__,
                "name": ind.name,
                "render_location": ind.render_location,
                "inputs_required": list(ind.inputs_required),
            }
        return out

    def validate_dataframe(self, dataframe: pd.DataFrame) -> List[str]:
        """Check what columns are missing for the current indicator set.

        Returns
        -------
        List[str]
            Missing column names (empty list = all OK).
        """
        needed: set[str] = set()
        for ind in self._indicators.values():
            needed.update(ind.inputs_required)
        return sorted(needed - set(dataframe.columns))

    def __repr__(self) -> str:
        names = ", ".join(self._indicators.keys())
        return f"IndicatorEngine([{names}])"

    def __len__(self) -> int:
        return len(self._indicators)
