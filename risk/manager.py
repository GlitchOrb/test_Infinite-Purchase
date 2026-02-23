"""manager — the central RiskManager that gates every trade.

The RiskManager is **UI-independent** and **broker-agnostic**.  It evaluates
proposed orders against configurable risk constraints and returns a
:class:`RiskVerdict` indicating ALLOW / REJECT / REDUCE with a human-readable
reason.

Lifecycle
---------
1. Runtime opens a position → ``open_position(...)``
2. Every price tick → ``update_price(symbol, price)``
3. Before sending an order → ``check_order(...)`` → RiskVerdict
4. After a fill → ``record_fill(...)``
5. Position closed → ``close_position(symbol)``
6. Start-of-day → ``reset_daily()`` to clear the daily P&L accumulator.
"""

from __future__ import annotations

import copy
import enum
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from risk.config import RiskConfig, TakeProfitLevel, TakeProfitSchedule
from risk.trailing import TrailingStop

log = logging.getLogger(__name__)


# ── Position tracking ──

@dataclass
class Position:
    """Live state for one open position.

    Attributes
    ----------
    symbol : str
    qty : int
        Current share count.
    avg_entry : float
        Volume-weighted average entry price.
    current_price : float
        Latest known price.
    peak_price : float
        Highest price seen since entry (for trailing stop).
    allocated_capital : float
        Total capital deployed into this position.
    trailing_stop : TrailingStop | None
        Per-position trailing stop tracker (optional).
    tp_levels_fired : set[int]
        Indices of take-profit levels already triggered (prevents re-fire).
    """

    symbol: str
    qty: int = 0
    avg_entry: float = 0.0
    current_price: float = 0.0
    peak_price: float = 0.0
    allocated_capital: float = 0.0
    trailing_stop: TrailingStop | None = None
    tp_levels_fired: set = field(default_factory=set)

    @property
    def is_open(self) -> bool:
        return self.qty > 0

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def unrealised_pnl(self) -> float:
        return self.qty * (self.current_price - self.avg_entry)

    @property
    def unrealised_pnl_pct(self) -> float:
        if self.avg_entry <= 0:
            return 0.0
        return (self.current_price / self.avg_entry) - 1.0


# ── Portfolio snapshot (read-only summary) ──

@dataclass(frozen=True)
class PortfolioSnapshot:
    """Immutable summary of current portfolio risk state."""

    total_equity: float
    open_positions: int
    total_unrealised_pnl: float
    daily_realised_pnl: float
    daily_total_pnl: float
    daily_loss_pct: float
    daily_loss_limit_hit: bool


# ── Verdict ──

class VerdictAction(str, enum.Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"
    REDUCE = "REDUCE"


@dataclass(frozen=True)
class RiskVerdict:
    """Result of a risk check on a proposed order.

    Attributes
    ----------
    action : VerdictAction
        ``ALLOW``, ``REJECT``, or ``REDUCE``.
    reason : str
        Human-readable explanation.
    allowed_qty : int | None
        For ``REDUCE`` verdicts, the maximum qty that *would* be allowed.
        ``None`` for ``ALLOW`` (full qty ok) and ``REJECT`` (zero qty).
    allowed_notional : float | None
        For ``REDUCE`` verdicts, the maximum notional that *would* be allowed.
    """

    action: VerdictAction
    reason: str
    allowed_qty: int | None = None
    allowed_notional: float | None = None

    @property
    def is_allowed(self) -> bool:
        return self.action in (VerdictAction.ALLOW, VerdictAction.REDUCE)


# ── RiskManager ──

class RiskManager:
    """Central pre-trade risk gatekeeper.

    Parameters
    ----------
    config : RiskConfig
        Immutable risk parameters.
    initial_equity : float
        Starting portfolio equity (used for daily-loss tracking until
        the first ``reset_daily()`` call).

    Usage
    -----
    >>> rm = RiskManager(RiskConfig(max_capital_per_trade_pct=0.10), 100_000)
    >>> verdict = rm.check_order("BUY", "AAPL", qty=50, price=150.0)
    >>> if verdict.is_allowed:
    ...     execute_order(...)
    """

    def __init__(self, config: RiskConfig, initial_equity: float) -> None:
        self._cfg = config
        self._positions: Dict[str, Position] = {}

        # Daily tracking
        self._day_start_equity = initial_equity
        self._daily_realised_pnl = 0.0
        self._daily_loss_blocked = False

    # ================================================================== #
    #  Configuration
    # ================================================================== #

    @property
    def config(self) -> RiskConfig:
        return self._cfg

    def update_config(self, config: RiskConfig) -> None:
        """Hot-swap risk parameters (e.g. from UI settings)."""
        self._cfg = config

    # ================================================================== #
    #  Portfolio state
    # ================================================================== #

    @property
    def open_positions(self) -> Dict[str, Position]:
        """Return a shallow copy of open positions."""
        return {k: v for k, v in self._positions.items() if v.is_open}

    @property
    def open_position_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.is_open)

    def get_position(self, symbol: str) -> Position | None:
        pos = self._positions.get(symbol)
        return pos if pos and pos.is_open else None

    def snapshot(self) -> PortfolioSnapshot:
        """Create an immutable snapshot of current risk state."""
        total_unrealised = sum(
            p.unrealised_pnl for p in self._positions.values() if p.is_open
        )
        daily_total = self._daily_realised_pnl + total_unrealised
        loss_pct = abs(daily_total / self._day_start_equity) if (
            self._day_start_equity > 0 and daily_total < 0
        ) else 0.0

        return PortfolioSnapshot(
            total_equity=self._day_start_equity + daily_total,
            open_positions=self.open_position_count,
            total_unrealised_pnl=total_unrealised,
            daily_realised_pnl=self._daily_realised_pnl,
            daily_total_pnl=daily_total,
            daily_loss_pct=loss_pct,
            daily_loss_limit_hit=self._daily_loss_blocked,
        )

    # ================================================================== #
    #  Daily lifecycle
    # ================================================================== #

    def reset_daily(self, current_equity: float | None = None) -> None:
        """Reset the daily P&L accumulator.

        Call at market open (or start of a new trading day).
        """
        if current_equity is not None:
            self._day_start_equity = current_equity
        else:
            snap = self.snapshot()
            self._day_start_equity = snap.total_equity
        self._daily_realised_pnl = 0.0
        self._daily_loss_blocked = False
        log.debug("RiskManager: daily reset, equity=%.2f", self._day_start_equity)

    # ================================================================== #
    #  Position management
    # ================================================================== #

    def open_position(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
    ) -> Position:
        """Register a new position (or add to existing).

        Returns the updated Position.
        """
        pos = self._positions.get(symbol)

        if pos and pos.is_open:
            # Add to existing position (average-up/down)
            old_cost = pos.avg_entry * pos.qty
            new_cost = entry_price * qty
            pos.qty += qty
            pos.avg_entry = (old_cost + new_cost) / pos.qty
            pos.current_price = entry_price
            pos.allocated_capital += entry_price * qty
            if entry_price > pos.peak_price:
                pos.peak_price = entry_price
        else:
            # New position
            ts = None
            if self._cfg.trailing_stop_pct is not None:
                ts = TrailingStop(
                    trail_pct=self._cfg.trailing_stop_pct,
                    activation_pct=self._cfg.trailing_stop_activation_pct,
                )
                ts.reset(entry_price)
            pos = Position(
                symbol=symbol,
                qty=qty,
                avg_entry=entry_price,
                current_price=entry_price,
                peak_price=entry_price,
                allocated_capital=entry_price * qty,
                trailing_stop=ts,
            )
            self._positions[symbol] = pos

        log.debug(
            "RiskManager: opened %s qty=%d @ %.2f (total qty=%d)",
            symbol, qty, entry_price, pos.qty,
        )
        return pos

    def close_position(self, symbol: str, exit_price: float | None = None) -> float:
        """Fully close a position and return the realised P&L.

        Parameters
        ----------
        exit_price : float or None
            If ``None``, uses the last known ``current_price``.
        """
        pos = self._positions.get(symbol)
        if not pos or not pos.is_open:
            return 0.0

        price = exit_price if exit_price is not None else pos.current_price
        realised = pos.qty * (price - pos.avg_entry)
        self._daily_realised_pnl += realised

        log.debug(
            "RiskManager: closed %s qty=%d, realised=%.2f",
            symbol, pos.qty, realised,
        )

        # Remove from map
        del self._positions[symbol]
        self._check_daily_loss()
        return realised

    def reduce_position(
        self,
        symbol: str,
        qty: int,
        exit_price: float,
    ) -> float:
        """Partially close a position. Returns realised P&L on the sold qty."""
        pos = self._positions.get(symbol)
        if not pos or not pos.is_open:
            return 0.0

        sell_qty = min(qty, pos.qty)
        realised = sell_qty * (exit_price - pos.avg_entry)
        self._daily_realised_pnl += realised
        pos.qty -= sell_qty
        pos.allocated_capital = pos.avg_entry * pos.qty

        if pos.qty <= 0:
            del self._positions[symbol]

        self._check_daily_loss()
        return realised

    # ================================================================== #
    #  Price updates
    # ================================================================== #

    def update_price(self, symbol: str, price: float) -> Dict[str, Any]:
        """Feed a new price for a symbol.

        Returns a dict of triggered events (trailing stop, take-profit levels).
        """
        pos = self._positions.get(symbol)
        if not pos or not pos.is_open:
            return {}

        pos.current_price = price
        if price > pos.peak_price:
            pos.peak_price = price

        events: Dict[str, Any] = {}

        # Trailing stop
        if pos.trailing_stop is not None:
            if pos.trailing_stop.update(price):
                events["trailing_stop"] = {
                    "triggered": True,
                    "stop_price": pos.trailing_stop.stop_price,
                    "peak": pos.peak_price,
                }

        # Hard stop loss
        if self._cfg.hard_stop_loss_pct is not None:
            loss_pct = 1.0 - (price / pos.avg_entry)
            if loss_pct >= self._cfg.hard_stop_loss_pct:
                events["hard_stop_loss"] = {
                    "triggered": True,
                    "loss_pct": loss_pct,
                    "entry": pos.avg_entry,
                }

        # Take-profit schedule
        tp = self._cfg.take_profit
        if tp is not None and pos.avg_entry > 0:
            gain = (price / pos.avg_entry) - 1.0
            for i, level in enumerate(tp.levels):
                if i not in pos.tp_levels_fired and gain >= level.trigger_pct:
                    sell_qty = max(1, math.floor(pos.qty * level.sell_pct))
                    pos.tp_levels_fired.add(i)
                    events.setdefault("take_profit", []).append({
                        "level_index": i,
                        "trigger_pct": level.trigger_pct,
                        "sell_pct": level.sell_pct,
                        "sell_qty": sell_qty,
                    })

        # Check daily loss after unrealised P&L update
        self._check_daily_loss()

        return events

    # ================================================================== #
    #  Pre-trade risk check
    # ================================================================== #

    def check_order(
        self,
        side: str,
        symbol: str,
        *,
        qty: int = 0,
        notional: float = 0.0,
        price: float = 0.0,
    ) -> RiskVerdict:
        """Evaluate a proposed order against all risk constraints.

        Parameters
        ----------
        side : ``"BUY"`` or ``"SELL"``
        symbol : str
        qty : int
            Number of shares (can be 0 if using notional).
        notional : float
            Dollar amount (can be 0 if using qty + price).
        price : float
            Expected execution price.

        Returns
        -------
        RiskVerdict
        """
        side = side.upper()

        # SELL orders are always allowed (closing risk)
        if side == "SELL":
            return RiskVerdict(VerdictAction.ALLOW, "Sell orders always permitted")

        # Resolve notional
        if notional <= 0 and qty > 0 and price > 0:
            notional = qty * price
        if qty <= 0 and notional > 0 and price > 0:
            qty = int(notional / price)

        # ── Gate 1: Daily loss circuit breaker ──
        if self._daily_loss_blocked:
            return RiskVerdict(
                VerdictAction.REJECT,
                f"Daily loss limit hit ({self._cfg.max_daily_loss_pct:.1%}). "
                f"No new BUY orders until next trading day.",
            )

        # ── Gate 2: Max open positions ──
        existing = self.get_position(symbol)
        if not (existing and existing.is_open):
            # This would be a *new* position
            if self.open_position_count >= self._cfg.max_open_positions:
                return RiskVerdict(
                    VerdictAction.REJECT,
                    f"Max open positions reached "
                    f"({self.open_position_count}/{self._cfg.max_open_positions})",
                )

        # ── Gate 3: Max capital per trade ──
        max_notional = self._day_start_equity * self._cfg.max_capital_per_trade_pct
        if notional > max_notional:
            # Allow a reduced size
            allowed_qty = int(max_notional / price) if price > 0 else 0
            if allowed_qty <= 0:
                return RiskVerdict(
                    VerdictAction.REJECT,
                    f"Trade notional ${notional:,.0f} exceeds per-trade limit "
                    f"${max_notional:,.0f} ({self._cfg.max_capital_per_trade_pct:.0%} "
                    f"of ${self._day_start_equity:,.0f})",
                )
            return RiskVerdict(
                VerdictAction.REDUCE,
                f"Trade reduced: ${notional:,.0f} -> ${max_notional:,.0f} "
                f"(per-trade limit {self._cfg.max_capital_per_trade_pct:.0%})",
                allowed_qty=allowed_qty,
                allowed_notional=round(max_notional, 2),
            )

        # ── Gate 4: Would this order blow the daily loss budget? ──
        # Pessimistic: assume worst-case that the entire position could
        # be lost by EOD.  This is a soft check — we don't block aggressively.
        # (The hard block happens once actual losses hit the threshold.)

        return RiskVerdict(VerdictAction.ALLOW, "All risk checks passed")

    # ================================================================== #
    #  Bulk position management helpers
    # ================================================================== #

    def check_trailing_stops(self) -> List[Dict[str, Any]]:
        """Check all positions for trailing stop triggers.

        Returns a list of dicts: ``{symbol, stop_price, peak, qty}``.
        """
        triggered = []
        for pos in self._positions.values():
            if pos.is_open and pos.trailing_stop and pos.trailing_stop.is_triggered:
                triggered.append({
                    "symbol": pos.symbol,
                    "stop_price": pos.trailing_stop.stop_price,
                    "peak": pos.peak_price,
                    "qty": pos.qty,
                })
        return triggered

    def check_take_profits(self) -> List[Dict[str, Any]]:
        """Collect all pending take-profit actions.

        Returns a list of dicts: ``{symbol, level_index, sell_qty, trigger_pct}``.
        Call this after ``update_price()`` to gather all TP events in aggregate.
        """
        actions = []
        tp = self._cfg.take_profit
        if tp is None:
            return actions

        for pos in self._positions.values():
            if not pos.is_open or pos.avg_entry <= 0:
                continue
            gain = (pos.current_price / pos.avg_entry) - 1.0
            for i, level in enumerate(tp.levels):
                if i not in pos.tp_levels_fired and gain >= level.trigger_pct:
                    sell_qty = max(1, math.floor(pos.qty * level.sell_pct))
                    actions.append({
                        "symbol": pos.symbol,
                        "level_index": i,
                        "trigger_pct": level.trigger_pct,
                        "sell_qty": sell_qty,
                    })
        return actions

    def check_hard_stop_losses(self) -> List[Dict[str, Any]]:
        """Check all positions for hard stop-loss triggers.

        Returns a list of dicts: ``{symbol, loss_pct, entry, qty}``.
        """
        if self._cfg.hard_stop_loss_pct is None:
            return []

        triggered = []
        for pos in self._positions.values():
            if not pos.is_open or pos.avg_entry <= 0:
                continue
            loss = 1.0 - (pos.current_price / pos.avg_entry)
            if loss >= self._cfg.hard_stop_loss_pct:
                triggered.append({
                    "symbol": pos.symbol,
                    "loss_pct": loss,
                    "entry": pos.avg_entry,
                    "current_price": pos.current_price,
                    "qty": pos.qty,
                })
        return triggered

    # ================================================================== #
    #  Serialization
    # ================================================================== #

    def describe(self) -> Dict[str, Any]:
        return {
            "config": {
                "max_capital_per_trade_pct": self._cfg.max_capital_per_trade_pct,
                "max_daily_loss_pct": self._cfg.max_daily_loss_pct,
                "max_open_positions": self._cfg.max_open_positions,
                "trailing_stop_pct": self._cfg.trailing_stop_pct,
                "trailing_stop_activation_pct": self._cfg.trailing_stop_activation_pct,
                "hard_stop_loss_pct": self._cfg.hard_stop_loss_pct,
                "take_profit_levels": (
                    len(self._cfg.take_profit.levels)
                    if self._cfg.take_profit else 0
                ),
            },
            "day_start_equity": self._day_start_equity,
            "daily_realised_pnl": self._daily_realised_pnl,
            "daily_loss_blocked": self._daily_loss_blocked,
            "open_positions": {
                sym: {
                    "qty": p.qty,
                    "avg_entry": p.avg_entry,
                    "current_price": p.current_price,
                    "unrealised_pnl": p.unrealised_pnl,
                    "trailing_stop_active": (
                        p.trailing_stop.is_activated
                        if p.trailing_stop else False
                    ),
                }
                for sym, p in self._positions.items()
                if p.is_open
            },
        }

    # ================================================================== #
    #  Internals
    # ================================================================== #

    def _check_daily_loss(self) -> None:
        """Update the daily-loss circuit breaker."""
        if self._daily_loss_blocked:
            return
        if self._day_start_equity <= 0:
            return

        total_unrealised = sum(
            p.unrealised_pnl for p in self._positions.values() if p.is_open
        )
        daily_total = self._daily_realised_pnl + total_unrealised

        if daily_total < 0:
            loss_pct = abs(daily_total) / self._day_start_equity
            if loss_pct >= self._cfg.max_daily_loss_pct:
                self._daily_loss_blocked = True
                log.warning(
                    "RiskManager: DAILY LOSS LIMIT HIT (%.2f%% >= %.2f%%)",
                    loss_pct * 100,
                    self._cfg.max_daily_loss_pct * 100,
                )

    def __repr__(self) -> str:
        return (
            f"RiskManager("
            f"positions={self.open_position_count}, "
            f"equity={self._day_start_equity:,.0f}, "
            f"daily_pnl={self._daily_realised_pnl:+,.0f}, "
            f"blocked={self._daily_loss_blocked})"
        )

    def __len__(self) -> int:
        return self.open_position_count
