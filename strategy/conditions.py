"""conditions — atomic comparison primitives for the strategy rule engine.

Each :class:`Condition` encapsulates a single boolean test applied
per-candle to a pre-computed indicator DataFrame.

Supported operators
-------------------
*  ``>``, ``<``, ``>=``, ``<=``, ``==``  — compare an indicator series
   against a scalar threshold **or** another indicator series.
*  ``cross_up``  — series crosses *above* a reference (scalar or series).
*  ``cross_down`` — series crosses *below* a reference (scalar or series).

All conditions are **stateless**.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Union

import numpy as np
import pandas as pd


# ── operator enum ──

class Operator(str, enum.Enum):
    GT = ">"
    LT = "<"
    GE = ">="
    LE = "<="
    EQ = "=="
    CROSS_UP = "cross_up"
    CROSS_DOWN = "cross_down"


_OP_MAP = {
    ">": Operator.GT,
    "<": Operator.LT,
    ">=": Operator.GE,
    "<=": Operator.LE,
    "==": Operator.EQ,
    "cross_up": Operator.CROSS_UP,
    "cross_down": Operator.CROSS_DOWN,
    # aliases
    "gt": Operator.GT,
    "lt": Operator.LT,
    "ge": Operator.GE,
    "le": Operator.LE,
    "eq": Operator.EQ,
    "crossup": Operator.CROSS_UP,
    "crossdown": Operator.CROSS_DOWN,
}


def _resolve_operator(op: str | Operator) -> Operator:
    if isinstance(op, Operator):
        return op
    key = op.strip().lower()
    if key in _OP_MAP:
        return _OP_MAP[key]
    # try the raw string (case-insensitive)
    try:
        return Operator(op.strip())
    except ValueError:
        raise ValueError(
            f"Unknown operator '{op}'. "
            f"Supported: {[o.value for o in Operator]}"
        ) from None


# ── reference value (scalar or column name) ──

ReferenceValue = Union[float, int, str]


def _resolve_reference(ref: ReferenceValue, df: pd.DataFrame) -> pd.Series | float:
    """Return a scalar or a Series from *df* depending on the type of *ref*."""
    if isinstance(ref, str):
        if ref not in df.columns:
            raise KeyError(
                f"Reference column '{ref}' not found in DataFrame. "
                f"Available: {sorted(df.columns)}"
            )
        return df[ref].astype(float)
    return float(ref)


# ── cross helpers (module-level, reusable) ──

def cross_up(series: pd.Series, ref: pd.Series | float) -> pd.Series:
    """Boolean Series that is ``True`` on bars where *series* crosses above *ref*.

    Cross-up is defined as:
        ``series[i-1] <= ref[i-1]`` **and** ``series[i] > ref[i]``
    """
    if isinstance(ref, (int, float)):
        prev_below = series.shift(1) <= ref
        curr_above = series > ref
    else:
        prev_below = series.shift(1) <= ref.shift(1)
        curr_above = series > ref
    return (prev_below & curr_above).fillna(False).astype(bool)


def cross_down(series: pd.Series, ref: pd.Series | float) -> pd.Series:
    """Boolean Series that is ``True`` on bars where *series* crosses below *ref*.

    Cross-down is defined as:
        ``series[i-1] >= ref[i-1]`` **and** ``series[i] < ref[i]``
    """
    if isinstance(ref, (int, float)):
        prev_above = series.shift(1) >= ref
        curr_below = series < ref
    else:
        prev_above = series.shift(1) >= ref.shift(1)
        curr_below = series < ref
    return (prev_above & curr_below).fillna(False).astype(bool)


# ── Condition ──

@dataclass(frozen=True)
class Condition:
    """A single boolean condition evaluated per candle.

    Parameters
    ----------
    indicator : str
        Column name of the indicator series (must be present in the
        enriched DataFrame passed to :meth:`evaluate`).
    operator : str or Operator
        One of ``">"``, ``"<"``, ``">="``, ``"<="``, ``"=="``,
        ``"cross_up"``, ``"cross_down"``.
    reference : float | int | str
        The threshold or another column name to compare against.

    Examples
    --------
    >>> Condition("RSI14", "<", 30)
    >>> Condition("MACD12_26_9", "cross_up", "MACD_signal")
    >>> Condition("close", ">", "SMA20")
    """

    indicator: str
    operator: str | Operator
    reference: ReferenceValue

    # ── public API ──

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Return a boolean Series (one value per row).

        Parameters
        ----------
        df : pd.DataFrame
            Must contain at least ``self.indicator`` and, if
            ``self.reference`` is a string, that column too.

        Returns
        -------
        pd.Series[bool]
        """
        if self.indicator not in df.columns:
            raise KeyError(
                f"Indicator column '{self.indicator}' not found. "
                f"Available: {sorted(df.columns)}"
            )

        series = df[self.indicator].astype(float)
        ref = _resolve_reference(self.reference, df)
        op = _resolve_operator(self.operator)

        if op == Operator.GT:
            return series > ref
        if op == Operator.LT:
            return series < ref
        if op == Operator.GE:
            return series >= ref
        if op == Operator.LE:
            return series <= ref
        if op == Operator.EQ:
            if isinstance(ref, float):
                return (series - ref).abs() < 1e-9
            return (series - ref).abs() < 1e-9
        if op == Operator.CROSS_UP:
            return cross_up(series, ref)
        if op == Operator.CROSS_DOWN:
            return cross_down(series, ref)

        raise ValueError(f"Unhandled operator: {op}")

    def __repr__(self) -> str:
        op = _resolve_operator(self.operator).value
        return f"Condition({self.indicator} {op} {self.reference})"
