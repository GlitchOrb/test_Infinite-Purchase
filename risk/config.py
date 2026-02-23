"""config — immutable configuration for the RiskManager.

All tunable risk parameters live here in frozen dataclasses so they are
hashable, serializable, and safe to share across threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


# ── Partial Take-Profit ──

@dataclass(frozen=True)
class TakeProfitLevel:
    """One step in a multi-stage take-profit schedule.

    Parameters
    ----------
    trigger_pct : float
        Unrealised gain (as a fraction, e.g. 0.10 = +10 %) at which
        this level activates.
    sell_pct : float
        Fraction of the **remaining** position to sell (0..1].
    """

    trigger_pct: float
    sell_pct: float

    def __post_init__(self) -> None:
        if self.trigger_pct <= 0:
            raise ValueError("trigger_pct must be positive")
        if not (0 < self.sell_pct <= 1.0):
            raise ValueError("sell_pct must be in (0, 1]")


@dataclass(frozen=True)
class TakeProfitSchedule:
    """Ordered sequence of take-profit levels.

    Levels are evaluated lowest-trigger-first.  Each level fires
    **at most once** per position lifecycle.

    Example
    -------
    >>> TakeProfitSchedule([
    ...     TakeProfitLevel(0.05, 0.25),   # +5% → sell 25%
    ...     TakeProfitLevel(0.10, 0.50),   # +10% → sell 50% of remainder
    ...     TakeProfitLevel(0.20, 1.00),   # +20% → sell all remaining
    ... ])
    """

    levels: List[TakeProfitLevel] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Sort by trigger ascending (purely defensive — caller should
        # already supply them in order).
        if self.levels:
            sorted_levels = sorted(self.levels, key=lambda l: l.trigger_pct)
            # frozen dataclass → use object.__setattr__
            object.__setattr__(self, "levels", sorted_levels)


# ── Main risk config ──

@dataclass(frozen=True)
class RiskConfig:
    """All tunable risk parameters.

    Parameters
    ----------
    max_capital_per_trade_pct : float
        Maximum fraction of total portfolio equity that a single trade
        may consume.  Default ``0.10`` (10 %).
    max_daily_loss_pct : float
        Daily loss circuit-breaker.  Once *realised + unrealised*
        day-loss exceeds this fraction of the day's opening equity,
        all new orders are blocked.  Default ``0.03`` (3 %).
    max_open_positions : int
        Hard cap on the number of simultaneously open positions.
        Default ``5``.
    trailing_stop_pct : float | None
        If set, every position gets an automatic trailing stop at this
        drawdown from its high-water mark.  Default ``None`` (disabled).
    trailing_stop_activation_pct : float
        Minimum unrealised % gain before the trailing stop becomes
        active.  Default ``0.0`` (active immediately).
    take_profit : TakeProfitSchedule | None
        Optional staged take-profit schedule.  Default ``None``.
    hard_stop_loss_pct : float | None
        If set, a **hard** (non-trailing) stop loss that triggers if
        the position drops below entry price by this fraction.
        Default ``None`` (disabled).
    """

    max_capital_per_trade_pct: float = 0.10
    max_daily_loss_pct: float = 0.03
    max_open_positions: int = 5
    trailing_stop_pct: float | None = None
    trailing_stop_activation_pct: float = 0.0
    take_profit: TakeProfitSchedule | None = None
    hard_stop_loss_pct: float | None = None

    def __post_init__(self) -> None:
        if not (0 < self.max_capital_per_trade_pct <= 1.0):
            raise ValueError("max_capital_per_trade_pct must be in (0, 1]")
        if not (0 < self.max_daily_loss_pct <= 1.0):
            raise ValueError("max_daily_loss_pct must be in (0, 1]")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be >= 1")
        if self.trailing_stop_pct is not None and self.trailing_stop_pct <= 0:
            raise ValueError("trailing_stop_pct must be positive")
        if self.hard_stop_loss_pct is not None and self.hard_stop_loss_pct <= 0:
            raise ValueError("hard_stop_loss_pct must be positive")
