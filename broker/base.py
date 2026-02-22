from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    change_pct: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0
    timestamp: str = ""


@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    equity: float
    buying_power: float


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: int
    avg_price: float
    market_price: float


class BrokerBase(ABC):
    """Unified broker interface for Guest/Paper/Live trading modes."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_live(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    @abstractmethod
    def get_ohlcv(self, symbol: str, lookback_days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> List[PositionSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str,
        limit_price: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError
