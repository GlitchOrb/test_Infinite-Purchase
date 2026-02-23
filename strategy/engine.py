"""engine — composable rule-based strategy engine.

Evaluates logical rules (AND / OR groups of :class:`Condition` objects)
per candle and emits :class:`Signal` values: ``BUY``, ``SELL``, or ``HOLD``.

Usage
-----
>>> from strategy import StrategyRuleEngine, Condition, RuleGroup, Signal
>>> engine = StrategyRuleEngine()
>>>
>>> # Buy when RSI < 30 AND MACD crosses up above signal
>>> engine.add_entry_rule(RuleGroup(
...     logic="AND",
...     conditions=[
...         Condition("RSI14", "<", 30),
...         Condition("MACD12_26_9", "cross_up", "MACD_signal"),
...     ],
... ))
>>>
>>> # Sell when RSI > 70 OR close crosses below SMA200
>>> engine.add_exit_rule(RuleGroup(
...     logic="OR",
...     conditions=[
...         Condition("RSI14", ">", 70),
...         Condition("close", "cross_down", "SMA200"),
...     ],
... ))
>>>
>>> signals = engine.evaluate(enriched_df)  # pd.Series of Signal values
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence

import pandas as pd

from strategy.conditions import Condition

log = logging.getLogger(__name__)


# ── Signal enum ──

class Signal(str, enum.Enum):
    """Per-candle trading signal emitted by the engine."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# ── RuleGroup ──

@dataclass(frozen=True)
class RuleGroup:
    """A logical group of :class:`Condition` objects.

    Parameters
    ----------
    logic : ``"AND"`` | ``"OR"``
        How to combine the conditions.
    conditions : list[Condition]
        One or more atomic conditions.
    nested : list[RuleGroup] | None
        Optional nested sub-groups for complex expressions like
        ``(A AND B) OR (C AND D)``.

    Examples
    --------
    Simple AND group::

        RuleGroup("AND", [
            Condition("RSI14", "<", 30),
            Condition("MACD12_26_9", "cross_up", "MACD_signal"),
        ])

    Nested OR-of-ANDs::

        RuleGroup("OR", nested=[
            RuleGroup("AND", [Condition("RSI14", "<", 30), Condition("ADX14", ">", 25)]),
            RuleGroup("AND", [Condition("close", "cross_up", "SMA20")]),
        ])
    """

    logic: Literal["AND", "OR"]
    conditions: List[Condition] = field(default_factory=list)
    nested: List[RuleGroup] = field(default_factory=list)

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Evaluate this group against the DataFrame.

        Returns
        -------
        pd.Series[bool]
            Boolean mask (one per row).
        """
        parts: list[pd.Series] = []

        for cond in self.conditions:
            parts.append(cond.evaluate(df))

        for sub in self.nested:
            parts.append(sub.evaluate(df))

        if not parts:
            # Empty group → vacuously True for AND, False for OR
            idx = df.index
            if self.logic == "AND":
                return pd.Series(True, index=idx, dtype=bool)
            return pd.Series(False, index=idx, dtype=bool)

        if self.logic == "AND":
            result = parts[0]
            for p in parts[1:]:
                result = result & p
            return result.astype(bool)
        else:  # OR
            result = parts[0]
            for p in parts[1:]:
                result = result | p
            return result.astype(bool)

    def __repr__(self) -> str:
        items = [repr(c) for c in self.conditions] + [repr(n) for n in self.nested]
        joiner = f" {self.logic} "
        return f"({joiner.join(items)})"


# ── StrategyRuleEngine ──

class StrategyRuleEngine:
    """Composable, UI-independent signal engine.

    Entry rules trigger ``BUY``, exit rules trigger ``SELL``.
    When neither fires the signal is ``HOLD``.

    If both entry and exit fire on the same candle, ``exit_priority``
    determines the winner (default: exit wins → ``SELL``).

    Parameters
    ----------
    exit_priority : bool
        If ``True`` (default), SELL takes precedence over BUY when
        both fire simultaneously.

    Usage
    -----
    1. Add entry / exit rule groups via :meth:`add_entry_rule` /
       :meth:`add_exit_rule`.
    2. Call :meth:`evaluate` with an enriched OHLCV+indicator DataFrame
       to receive a per-candle ``Signal`` Series.
    """

    def __init__(self, *, exit_priority: bool = True) -> None:
        self._entry_rules: list[RuleGroup] = []
        self._exit_rules: list[RuleGroup] = []
        self._exit_priority = exit_priority

    # ── rule management ──

    def add_entry_rule(self, group: RuleGroup) -> None:
        """Register a rule group that, when True, emits ``BUY``."""
        self._entry_rules.append(group)

    def add_exit_rule(self, group: RuleGroup) -> None:
        """Register a rule group that, when True, emits ``SELL``."""
        self._exit_rules.append(group)

    def clear_entry_rules(self) -> None:
        self._entry_rules.clear()

    def clear_exit_rules(self) -> None:
        self._exit_rules.clear()

    def clear_all(self) -> None:
        self._entry_rules.clear()
        self._exit_rules.clear()

    @property
    def entry_rules(self) -> List[RuleGroup]:
        return list(self._entry_rules)

    @property
    def exit_rules(self) -> List[RuleGroup]:
        return list(self._exit_rules)

    # ── evaluation ──

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Evaluate all rules per candle and return a ``Signal`` Series.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain all indicator columns referenced by the
            registered conditions.

        Returns
        -------
        pd.Series
            Series of :class:`Signal` values (``BUY`` / ``SELL`` / ``HOLD``)
            aligned to ``df.index``.
        """
        entry_mask = self._evaluate_rule_set(self._entry_rules, df, default=False)
        exit_mask = self._evaluate_rule_set(self._exit_rules, df, default=False)

        signals = pd.Series(Signal.HOLD, index=df.index, dtype=object)
        signals[entry_mask] = Signal.BUY
        signals[exit_mask] = Signal.SELL

        # Conflict resolution: when both fire on the same candle
        conflict = entry_mask & exit_mask
        if conflict.any():
            winner = Signal.SELL if self._exit_priority else Signal.BUY
            signals[conflict] = winner
            log.debug(
                "StrategyRuleEngine: %d conflict(s) resolved → %s",
                conflict.sum(),
                winner.value,
            )

        return signals

    def evaluate_detail(self, df: pd.DataFrame) -> pd.DataFrame:
        """Like :meth:`evaluate`, but return a DataFrame with additional
        columns showing each rule group's boolean mask.

        Columns: ``signal``, ``entry_<i>``, ``exit_<i>``, ``entry_any``,
        ``exit_any``.
        """
        result = pd.DataFrame(index=df.index)

        for i, grp in enumerate(self._entry_rules):
            result[f"entry_{i}"] = grp.evaluate(df)
        for i, grp in enumerate(self._exit_rules):
            result[f"exit_{i}"] = grp.evaluate(df)

        entry_cols = [c for c in result.columns if c.startswith("entry_")]
        exit_cols = [c for c in result.columns if c.startswith("exit_")]

        result["entry_any"] = (
            result[entry_cols].any(axis=1) if entry_cols
            else pd.Series(False, index=df.index)
        )
        result["exit_any"] = (
            result[exit_cols].any(axis=1) if exit_cols
            else pd.Series(False, index=df.index)
        )

        result["signal"] = self.evaluate(df)
        return result

    # ── single-bar evaluation ──

    def evaluate_bar(self, df: pd.DataFrame, idx: int) -> Signal:
        """Evaluate rules for a single bar by integer position.

        This is useful for streaming / real-time evaluation where you
        append a new row and want the signal for just the latest candle.

        Parameters
        ----------
        df : pd.DataFrame
            Full enriched DataFrame (needed for cross_up/cross_down which
            reference the previous bar).
        idx : int
            Integer position (iloc index) of the bar to evaluate.

        Returns
        -------
        Signal
        """
        if idx < 0:
            idx = len(df) + idx

        # Evaluate on a 2-row window (current + previous) to support cross ops
        start = max(0, idx - 1)
        window = df.iloc[start: idx + 1]

        signals = self.evaluate(window)
        return signals.iloc[-1]

    # ── serialization helpers ──

    def describe(self) -> Dict[str, Any]:
        """Return a JSON-serializable summary of all rules."""
        return {
            "exit_priority": self._exit_priority,
            "entry_rules": [repr(r) for r in self._entry_rules],
            "exit_rules": [repr(r) for r in self._exit_rules],
        }

    # ── internals ──

    @staticmethod
    def _evaluate_rule_set(
        rules: list[RuleGroup],
        df: pd.DataFrame,
        *,
        default: bool = False,
    ) -> pd.Series:
        """Evaluate a list of rule groups, OR-ing their results.

        Multiple entry (or exit) rule groups are combined with OR logic:
        *any* group firing is sufficient to trigger the signal.
        """
        if not rules:
            return pd.Series(default, index=df.index, dtype=bool)

        masks = [g.evaluate(df) for g in rules]
        result = masks[0]
        for m in masks[1:]:
            result = result | m
        return result.astype(bool)

    def __repr__(self) -> str:
        return (
            f"StrategyRuleEngine("
            f"entry_rules={len(self._entry_rules)}, "
            f"exit_rules={len(self._exit_rules)}, "
            f"exit_priority={self._exit_priority})"
        )

    def __len__(self) -> int:
        return len(self._entry_rules) + len(self._exit_rules)
