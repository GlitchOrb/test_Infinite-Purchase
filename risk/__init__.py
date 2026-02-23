"""risk — generic, UI-independent risk management layer.

Public API:

    from risk import (
        RiskManager, RiskConfig, RiskVerdict,
        TrailingStop, TakeProfitSchedule, TakeProfitLevel,
        Position, PortfolioSnapshot,
    )
"""

from risk.config import RiskConfig, TakeProfitLevel, TakeProfitSchedule
from risk.manager import PortfolioSnapshot, Position, RiskManager, RiskVerdict
from risk.trailing import TrailingStop

__all__ = [
    "RiskConfig",
    "RiskManager",
    "RiskVerdict",
    "TrailingStop",
    "TakeProfitSchedule",
    "TakeProfitLevel",
    "Position",
    "PortfolioSnapshot",
]
