"""
trade_manager.py
================
Rule-based position logic and order-intent generation.

Consumes ``StrategyEngine.DailyDecision`` objects, maintains position
state via an explicit FSM, and emits ``OrderIntent`` objects for a
downstream execution layer.  **No broker I/O.  No external AI calls.**

Key sub-systems
---------------
A) SOXL trend-compounding engine (slice-based daily accumulation)
B) SOXL partial trailing stop (two-stage drawdown exit, peak = max price since entry)
C) SOXS hit-and-run bear harvesting (take-profit / loss-cut / max-hold + cooldown)
D) 3-day BEAR→BULL transition swap
E) Vampire rebalance (dynamic ratio + slice-cap, profit cross-injection from SOXS into SOXL)
"""

from __future__ import annotations

import copy
import enum
import math
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from strategy_engine import DailyDecision, EffectiveState


# ======================================================================= #
#  Enums
# ======================================================================= #

class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


# ======================================================================= #
#  Configuration
# ======================================================================= #

@dataclass(frozen=True)
class TradeManagerConfig:
    """All tunable parameters — frozen so configs are hashable/immutable."""

    # -- SOXL main engine --
    soxl_max_slices: int = 35
    soxl_avg_down_thresh_1: float = -0.08   # drawdown → 2 slices
    soxl_avg_down_thresh_2: float = -0.15   # drawdown → 3 slices
    soxl_avg_down_slices_1: int = 2
    soxl_avg_down_slices_2: int = 3

    # -- SOXL trailing stop --
    soxl_trail_drawdown_1: float = -0.15    # stage 0→1, sell 50 %
    soxl_trail_drawdown_2: float = -0.25    # stage {0,1}→2, sell all
    soxl_trail_sell_pct_1: float = 0.50

    # -- SOXS sub-engine --
    soxs_max_slices: int = 18
    soxs_alloc_cap_ratio: float = 0.30      # vs total capital
    soxs_take_profit: float = 0.08          # +8 %
    soxs_max_holding_days: int = 25
    soxs_loss_cut_1: float = -0.15          # sell 50 %
    soxs_loss_cut_2: float = -0.25          # sell all
    soxs_cooldown_days: int = 3             # cooldown after forced close (C)

    # -- Vampire rebalance (dynamic ratio) --
    vampire_soxl_dd_thresh: float = -0.40   # minimum drawdown to enable injection
    # Dynamic injection ratio depends on SOXL drawdown depth:
    #   dd > -50% => 0.50
    #   dd > -40% => 0.70
    #   else      => 0.00  (should not reach here due to thresh guard)
    vampire_inject_ratio_deep: float = 0.50    # dd ≤ -50%
    vampire_inject_ratio_normal: float = 0.70  # -50% < dd ≤ -40%

    # -- Transition --
    transition_extra_slices_day2: int = 1


# ======================================================================= #
#  Position / State dataclasses
# ======================================================================= #

@dataclass
class PositionInfo:
    """Tracks a single-symbol position."""
    qty: int = 0
    avg_cost: float = 0.0
    entry_date: Optional[pd.Timestamp] = None
    allocated_capital: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.qty > 0


@dataclass
class TradeManagerState:
    """Mutable state carried between trading days.

    The runtime layer is responsible for persisting this object.
    """
    soxl: PositionInfo = field(default_factory=PositionInfo)
    soxs: PositionInfo = field(default_factory=PositionInfo)

    soxl_max_price: float = 0.0
    soxl_trailing_stage: int = 0        # 0, 1, 2

    soxs_holding_days: int = 0
    soxs_loss_cut_stage: int = 0        # 0, 1, 2

    soxl_slices_used: int = 0
    soxs_slices_used: int = 0

    injection_budget: float = 0.0

    # SOXS cooldown: number of trading days remaining before new
    # SOXS buys are allowed after a max-holding forced close.
    soxs_cooldown_remaining: int = 0

    # Flag set True when SOXS exits due to max holding days.
    # Runtime persists this so cooldown survives restarts.
    soxs_forced_close: bool = False


# ======================================================================= #
#  OrderIntent (output)
# ======================================================================= #

@dataclass(frozen=True)
class OrderIntent:
    """A single actionable order request for the execution layer.

    Buys specify *notional* (dollars); sells specify *qty* (shares).
    """
    symbol: str
    side: OrderSide
    qty: int                            # shares (sells)
    notional: float                     # dollars (buys)
    order_type_hint: str                # "MARKET" | "LIMIT"
    limit_price_hint: Optional[float]
    reason: str
    priority: int                       # lower = more urgent
    slices: int = 0                     # slices consumed (buys)


# ======================================================================= #
#  TradeManager
# ======================================================================= #

class TradeManager:
    """Deterministic, side-effect-free order-intent generator.

    Usage
    -----
    >>> mgr = TradeManager()
    >>> state = TradeManagerState()
    >>> intents, state = mgr.process_day(decision, soxl_px, soxs_px, capital, state)
    >>> # runtime executes intents, then calls apply_fill / on_realized_pnl
    """

    def __init__(self, config: TradeManagerConfig | None = None) -> None:
        self.cfg = config or TradeManagerConfig()

    # ------------------------------------------------------------------ #
    #  Main entry point
    # ------------------------------------------------------------------ #

    def process_day(
        self,
        decision: DailyDecision,
        soxl_price: float,
        soxs_price: float,
        total_capital: float,
        state: TradeManagerState,
    ) -> tuple[List[OrderIntent], TradeManagerState]:
        """Generate today's order intents and return updated state.

        Parameters
        ----------
        decision : DailyDecision
            Output of ``StrategyEngine.update_state`` for today.
        soxl_price, soxs_price : float
            Current (close) prices of execution assets.
        total_capital : float
            Total portfolio value available for sizing.
        state : TradeManagerState
            Previous day's persisted state (deep-copied internally).

        Returns
        -------
        (list[OrderIntent], TradeManagerState)
        """
        st = copy.deepcopy(state)
        intents: List[OrderIntent] = []

        # Phase 0 — tracking updates (peak price, holding days)
        # NOTE: cooldown ticks AFTER buy phase to ensure pre-tick
        # value blocks buys correctly.
        self._update_tracking(st, soxl_price, decision.date)

        # Phase 1 — SOXL trailing-stop sells
        soxl_sells = self._check_soxl_trailing(soxl_price, st)

        # Phase 2 — SOXS exits (safety + TP + transition sells)
        soxs_sells = self._check_soxs_exits(decision, soxs_price, st)

        # Cap total sell qty per symbol at position size
        soxl_sells = self._cap_sells(soxl_sells, st.soxl.qty)
        soxs_sells = self._cap_sells(soxs_sells, st.soxs.qty)

        intents.extend(soxl_sells)
        intents.extend(soxs_sells)

        # Phase 3 — SOXL buys
        intents.extend(self._check_soxl_buy(decision, soxl_price, total_capital, st))

        # Phase 4 — SOXS buys (respects cooldown)
        intents.extend(self._check_soxs_buy(decision, soxs_price, total_capital, st))

        # Phase 5 — tick down SOXS cooldown (AFTER buy check)
        if st.soxs_cooldown_remaining > 0 and not st.soxs.is_open:
            st.soxs_cooldown_remaining -= 1

        intents.sort(key=lambda i: i.priority)
        return intents, st

    # ------------------------------------------------------------------ #
    #  Post-execution hooks (called by runtime after fills)
    # ------------------------------------------------------------------ #

    def apply_fill(
        self,
        symbol: str,
        side: OrderSide,
        qty: int,
        fill_price: float,
        date: pd.Timestamp,
        state: TradeManagerState,
    ) -> TradeManagerState:
        """Update position state after a confirmed fill.

        Returns a **new** state; does not mutate the input.
        """
        st = copy.deepcopy(state)
        pos = st.soxl if symbol == "SOXL" else st.soxs

        if side == OrderSide.BUY:
            new_cost = (pos.avg_cost * pos.qty + fill_price * qty)
            pos.qty += qty
            pos.avg_cost = new_cost / pos.qty if pos.qty else 0.0
            pos.allocated_capital += fill_price * qty
            if pos.entry_date is None:
                pos.entry_date = date
            # Reset max-price on new SOXL entry from flat
            if symbol == "SOXL" and pos.qty == qty:
                st.soxl_max_price = fill_price
            # NOTE: adding slices to an existing SOXL position does NOT
            # reset soxl_max_price.  The peak tracks max(price) since the
            # position was first opened. (B)
        else:
            pos.qty -= qty
            if pos.qty <= 0:
                self._reset_position(st, symbol)

        return st

    def on_realized_pnl(
        self,
        symbol: str,
        realized_pnl: float,
        effective_state: EffectiveState,
        soxl_price: float,
        state: TradeManagerState,
    ) -> TradeManagerState:
        """Vampire rebalance: inject SOXS profits into SOXL buying power.

        Dynamic injection ratio (D):
        - dd ≤ -50%  → ratio = 0.50  (deeper crash = more cautious)
        - dd ≤ -40%  → ratio = 0.70
        - dd > -40%  → ratio = 0.00  (no injection)

        Injection is capped by remaining SOXL slice capacity.

        Conditions (all must hold):
        1. ``symbol == "SOXS"`` and ``realized_pnl > 0``
        2. ``effective_state == BEAR_ACTIVE``
        3. SOXL unrealised drawdown ≤ vampire threshold
        """
        st = copy.deepcopy(state)
        if symbol != "SOXS" or realized_pnl <= 0:
            return st
        if effective_state != EffectiveState.BEAR_ACTIVE:
            return st
        if not st.soxl.is_open or st.soxl.avg_cost == 0:
            return st

        soxl_dd = (soxl_price / st.soxl.avg_cost) - 1.0
        if soxl_dd > self.cfg.vampire_soxl_dd_thresh:
            return st

        # Dynamic ratio selection (D)
        if soxl_dd <= -0.50:
            ratio = self.cfg.vampire_inject_ratio_deep       # 0.50
        else:
            ratio = self.cfg.vampire_inject_ratio_normal     # 0.70

        inject_amount = realized_pnl * ratio

        # Cap by remaining SOXL slice capacity (D)
        remaining_slices = self.cfg.soxl_max_slices - st.soxl_slices_used
        if remaining_slices > 0:
            # We need a notional reference.  Use current soxl price as proxy
            # for slice_notional.  In practice, process_day computes this as
            # total_capital / max_slices, but we don't have total_capital here.
            # Instead, cap by remaining_slices * soxl_price * some share count.
            # The simpler approach: just cap inject to avoid exceeding slice
            # capacity when consumed during the next buy cycle. The real cap
            # will be enforced in _check_soxl_buy.
            pass  # cap enforced in _check_soxl_buy via remaining slices
        else:
            inject_amount = 0.0  # no remaining slices

        st.injection_budget += inject_amount
        return st

    # ================================================================== #
    #  Internals — tracking
    # ================================================================== #

    def _update_tracking(
        self,
        st: TradeManagerState,
        soxl_price: float,
        date: pd.Timestamp,
    ) -> None:
        # SOXL peak: max(price) since position opened.
        # Adding slices does NOT reset peak. (B)
        if st.soxl.is_open:
            st.soxl_max_price = max(st.soxl_max_price, soxl_price)
        if st.soxs.is_open:
            st.soxs_holding_days += 1
        # NOTE: cooldown tick-down moved to process_day Phase 5
        # so the buy check in Phase 4 sees the pre-tick value.

    # ================================================================== #
    #  Internals — SOXL trailing stop  (B)
    # ================================================================== #

    def _check_soxl_trailing(
        self,
        soxl_price: float,
        st: TradeManagerState,
    ) -> List[OrderIntent]:
        if not st.soxl.is_open or st.soxl_trailing_stage >= 2:
            return []
        if st.soxl_max_price <= 0:
            return []

        dd = (soxl_price / st.soxl_max_price) - 1.0

        # More severe first
        if dd <= self.cfg.soxl_trail_drawdown_2 and st.soxl_trailing_stage in (0, 1):
            st.soxl_trailing_stage = 2
            return [OrderIntent(
                symbol="SOXL", side=OrderSide.SELL, qty=st.soxl.qty,
                notional=0, order_type_hint="MARKET", limit_price_hint=None,
                reason="TRAILING_STOP_ALL", priority=10,
            )]

        if dd <= self.cfg.soxl_trail_drawdown_1 and st.soxl_trailing_stage == 0:
            sell_qty = max(1, math.floor(st.soxl.qty * self.cfg.soxl_trail_sell_pct_1))
            st.soxl_trailing_stage = 1
            return [OrderIntent(
                symbol="SOXL", side=OrderSide.SELL, qty=sell_qty,
                notional=0, order_type_hint="MARKET", limit_price_hint=None,
                reason="TRAILING_STOP_50PCT", priority=20,
            )]

        return []

    # ================================================================== #
    #  Internals — SOXS exits  (C + D)
    # ================================================================== #

    def _check_soxs_exits(
        self,
        decision: DailyDecision,
        soxs_price: float,
        st: TradeManagerState,
    ) -> List[OrderIntent]:
        if not st.soxs.is_open:
            return []

        sells: List[OrderIntent] = []
        dd = (soxs_price / st.soxs.avg_cost) - 1.0 if st.soxs.avg_cost > 0 else 0.0

        # Max-holding safety — triggers cooldown (C)
        if st.soxs_holding_days >= self.cfg.soxs_max_holding_days:
            sells.append(OrderIntent(
                symbol="SOXS", side=OrderSide.SELL, qty=st.soxs.qty,
                notional=0, order_type_hint="MARKET", limit_price_hint=None,
                reason="MAX_HOLDING_EXIT", priority=30,
            ))
            st.soxs_forced_close = True
            st.soxs_cooldown_remaining = self.cfg.soxs_cooldown_days
            return sells  # no point checking further

        # Loss-cut (more severe first)
        if dd <= self.cfg.soxs_loss_cut_2 and st.soxs_loss_cut_stage < 2:
            st.soxs_loss_cut_stage = 2
            sells.append(OrderIntent(
                symbol="SOXS", side=OrderSide.SELL, qty=st.soxs.qty,
                notional=0, order_type_hint="MARKET", limit_price_hint=None,
                reason="LOSS_CUT_ALL", priority=30,
            ))
            return sells
        if dd <= self.cfg.soxs_loss_cut_1 and st.soxs_loss_cut_stage == 0:
            sell_qty = max(1, st.soxs.qty // 2)
            st.soxs_loss_cut_stage = 1
            sells.append(OrderIntent(
                symbol="SOXS", side=OrderSide.SELL, qty=sell_qty,
                notional=0, order_type_hint="MARKET", limit_price_hint=None,
                reason="LOSS_CUT_50PCT", priority=35,
            ))

        # Take-profit
        if dd >= self.cfg.soxs_take_profit:
            sells.append(OrderIntent(
                symbol="SOXS", side=OrderSide.SELL, qty=st.soxs.qty,
                notional=0, order_type_hint="MARKET", limit_price_hint=None,
                reason="TAKE_PROFIT", priority=40,
            ))

        # Transition-specific sells
        if decision.transition_active:
            if decision.transition_day == 2:
                sell_qty = max(1, st.soxs.qty // 2)
                sells.append(OrderIntent(
                    symbol="SOXS", side=OrderSide.SELL, qty=sell_qty,
                    notional=0, order_type_hint="MARKET", limit_price_hint=None,
                    reason="TRANSITION_SELL_50PCT", priority=50,
                ))
            elif decision.transition_day >= 3:
                sells.append(OrderIntent(
                    symbol="SOXS", side=OrderSide.SELL, qty=st.soxs.qty,
                    notional=0, order_type_hint="MARKET", limit_price_hint=None,
                    reason="TRANSITION_SELL_ALL", priority=50,
                ))

        return sells

    # ================================================================== #
    #  Internals — SOXL buy logic  (A + D)
    # ================================================================== #

    def _check_soxl_buy(
        self,
        decision: DailyDecision,
        soxl_price: float,
        total_capital: float,
        st: TradeManagerState,
    ) -> List[OrderIntent]:
        allowed = decision.effective_state in (
            EffectiveState.BULL_ACTIVE,
            EffectiveState.TRANSITION,
        )
        if not allowed:
            return []
        if st.soxl_slices_used >= self.cfg.soxl_max_slices:
            return []

        # Determine slice count
        if decision.transition_active:
            if decision.transition_day == 1:
                num = 1
            elif decision.transition_day == 2:
                num = 1 + self.cfg.transition_extra_slices_day2
            else:
                num = self._soxl_slice_count(soxl_price, st)
        else:
            num = self._soxl_slice_count(soxl_price, st)

        remaining = self.cfg.soxl_max_slices - st.soxl_slices_used
        num = min(num, remaining)
        if num <= 0:
            return []

        slice_size = total_capital / self.cfg.soxl_max_slices
        notional = slice_size * num

        # Vampire injection (D)
        injection = 0.0
        if st.injection_budget > 0:
            # Cap injection by remaining slice capacity
            max_inject = remaining * slice_size
            injection = min(st.injection_budget, max_inject)
            notional += injection
            st.injection_budget -= injection
            # Round residual to avoid float dust
            if st.injection_budget < 0.01:
                st.injection_budget = 0.0

        st.soxl_slices_used += num

        reason = (
            f"TRANSITION_DAY_{decision.transition_day}"
            if decision.transition_active
            else "BULL_ACCUMULATE"
        )
        if injection > 0:
            reason += "+VAMPIRE_INJECT"

        return [OrderIntent(
            symbol="SOXL", side=OrderSide.BUY, qty=0,
            notional=round(notional, 2),
            order_type_hint="MARKET", limit_price_hint=None,
            reason=reason, priority=60, slices=num,
        )]

    def _soxl_slice_count(self, price: float, st: TradeManagerState) -> int:
        """Averaging-down logic: 1 / 2 / 3 slices based on drawdown."""
        if not st.soxl.is_open or st.soxl.avg_cost <= 0:
            return 1
        dd = (price / st.soxl.avg_cost) - 1.0
        if dd <= self.cfg.soxl_avg_down_thresh_2:
            return self.cfg.soxl_avg_down_slices_2
        if dd <= self.cfg.soxl_avg_down_thresh_1:
            return self.cfg.soxl_avg_down_slices_1
        return 1

    # ================================================================== #
    #  Internals — SOXS buy logic  (C)
    # ================================================================== #

    def _check_soxs_buy(
        self,
        decision: DailyDecision,
        soxs_price: float,
        total_capital: float,
        st: TradeManagerState,
    ) -> List[OrderIntent]:
        # Only during BEAR_ACTIVE (transition stops new buys)
        if decision.effective_state != EffectiveState.BEAR_ACTIVE:
            return []
        if st.soxs_slices_used >= self.cfg.soxs_max_slices:
            return []

        # Cooldown check — after forced max-holding close (C)
        if st.soxs_cooldown_remaining > 0:
            return []

        # Allocation cap
        soxs_cap = total_capital * self.cfg.soxs_alloc_cap_ratio
        if st.soxs.allocated_capital >= soxs_cap:
            return []

        slice_size = soxs_cap / self.cfg.soxs_max_slices
        remaining_cap = soxs_cap - st.soxs.allocated_capital
        notional = min(slice_size, remaining_cap)
        if notional <= 0:
            return []

        st.soxs_slices_used += 1

        return [OrderIntent(
            symbol="SOXS", side=OrderSide.BUY, qty=0,
            notional=round(notional, 2),
            order_type_hint="MARKET", limit_price_hint=None,
            reason="BEAR_ACCUMULATE", priority=70, slices=1,
        )]

    # ================================================================== #
    #  Internals — sell deduplication
    # ================================================================== #

    @staticmethod
    def _cap_sells(sells: List[OrderIntent], position_qty: int) -> List[OrderIntent]:
        """Ensure combined sell qty does not exceed position size.

        Higher-priority (lower number) intents are preserved first.
        """
        if not sells:
            return []
        sells_sorted = sorted(sells, key=lambda s: s.priority)
        result: List[OrderIntent] = []
        remaining = position_qty
        for s in sells_sorted:
            if remaining <= 0:
                break
            capped_qty = min(s.qty, remaining)
            if capped_qty > 0:
                result.append(OrderIntent(
                    symbol=s.symbol, side=s.side, qty=capped_qty,
                    notional=s.notional, order_type_hint=s.order_type_hint,
                    limit_price_hint=s.limit_price_hint,
                    reason=s.reason, priority=s.priority, slices=s.slices,
                ))
                remaining -= capped_qty
        return result

    # ================================================================== #
    #  Internals — position resets
    # ================================================================== #

    @staticmethod
    def _reset_position(st: TradeManagerState, symbol: str) -> None:
        """Zero-out position and associated tracking upon full exit."""
        if symbol == "SOXL":
            st.soxl = PositionInfo()
            st.soxl_max_price = 0.0
            st.soxl_trailing_stage = 0
            st.soxl_slices_used = 0
        else:
            st.soxs = PositionInfo()
            st.soxs_holding_days = 0
            st.soxs_loss_cut_stage = 0
            st.soxs_slices_used = 0
            # NOTE: cooldown is NOT reset here — it persists after close.
            # It will tick down via _update_tracking.

    def __repr__(self) -> str:
        return f"TradeManager(cfg={self.cfg!r})"
