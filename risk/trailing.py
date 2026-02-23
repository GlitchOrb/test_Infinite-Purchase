"""trailing — TrailingStop tracker for per-position high-water-mark stops.

Each :class:`TrailingStop` instance is bound to one position and must be
``update()``-ed on every price tick.  It tracks the peak price and reports
whether the stop has been triggered.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrailingStop:
    """Per-position trailing stop tracker.

    Parameters
    ----------
    trail_pct : float
        Drawdown fraction from the peak that triggers the stop
        (e.g. 0.05 = 5 % below peak).
    activation_pct : float
        Minimum unrealised gain above entry before the trail begins
        tracking.  Default ``0.0`` (track from the start).

    Usage
    -----
    >>> ts = TrailingStop(trail_pct=0.05, activation_pct=0.02)
    >>> ts.reset(entry_price=100.0)
    >>> ts.update(105.0)  # +5% → activated, peak=105
    >>> ts.update(101.0)  # drawdown 3.8% → not triggered yet
    >>> ts.update(99.5)   # drawdown 5.2% → triggered!
    True
    """

    trail_pct: float
    activation_pct: float = 0.0

    # internal state
    _entry_price: float = field(default=0.0, init=False, repr=False)
    _peak_price: float = field(default=0.0, init=False, repr=False)
    _activated: bool = field(default=False, init=False, repr=False)
    _triggered: bool = field(default=False, init=False, repr=False)

    def reset(self, entry_price: float) -> None:
        """(Re)initialise for a new position."""
        self._entry_price = entry_price
        self._peak_price = entry_price
        self._activated = self.activation_pct <= 0.0
        self._triggered = False

    def update(self, current_price: float) -> bool:
        """Feed a new price tick.

        Returns ``True`` **once** when the trailing stop fires.
        Subsequent calls keep returning ``True`` (latched).
        """
        if self._triggered:
            return True
        if self._entry_price <= 0:
            return False

        # Activation gate
        if not self._activated:
            gain = (current_price / self._entry_price) - 1.0
            if gain >= self.activation_pct:
                self._activated = True
                self._peak_price = current_price
            else:
                return False

        # Track peak
        if current_price > self._peak_price:
            self._peak_price = current_price

        # Check trigger
        drawdown = 1.0 - (current_price / self._peak_price)
        if drawdown >= self.trail_pct:
            self._triggered = True
            return True

        return False

    # ── read-only accessors ──

    @property
    def entry_price(self) -> float:
        return self._entry_price

    @property
    def peak_price(self) -> float:
        return self._peak_price

    @property
    def is_activated(self) -> bool:
        return self._activated

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    @property
    def stop_price(self) -> float | None:
        """Current stop price, or ``None`` if not yet activated."""
        if not self._activated or self._peak_price <= 0:
            return None
        return self._peak_price * (1.0 - self.trail_pct)

    def distance_to_stop(self, current_price: float) -> float | None:
        """Fraction of price remaining before the stop fires.

        Returns ``None`` if not activated.
        Positive means still safe; negative means triggered.
        """
        sp = self.stop_price
        if sp is None:
            return None
        return (current_price / sp) - 1.0
