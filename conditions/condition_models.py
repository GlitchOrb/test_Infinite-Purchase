from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ConditionStatus(str, Enum):
    PENDING = "PENDING"
    TRIGGERED = "TRIGGERED"
    FILLED = "FILLED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ConditionOperator(str, Enum):
    GTE = ">="
    LTE = "<="


class ConditionAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ConditionOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class ConditionOrder:
    id: int
    mode: str
    symbol: str
    operator: ConditionOperator
    trigger_price: float
    action: ConditionAction
    order_type: ConditionOrderType
    qty: int
    limit_price: Optional[float]
    status: ConditionStatus
    created_at: str
    triggered_at: Optional[str]
    completed_at: Optional[str]
    broker_order_id: Optional[str]
    fail_reason: Optional[str]


@dataclass(frozen=True)
class TriggerResult:
    condition_id: int
    success: bool
    status: ConditionStatus
    broker_order_id: Optional[str] = None
    reason: Optional[str] = None
    triggered_at: str = datetime.utcnow().isoformat()
