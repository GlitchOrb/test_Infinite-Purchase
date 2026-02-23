"""strategy — composable rule-based signal engine.

Public API:

    from strategy import (
        Signal, Condition, RuleGroup, StrategyRuleEngine,
        cross_up, cross_down,
    )
"""

from strategy.conditions import Condition, cross_down, cross_up
from strategy.engine import RuleGroup, Signal, StrategyRuleEngine

__all__ = [
    "Signal",
    "Condition",
    "RuleGroup",
    "StrategyRuleEngine",
    "cross_up",
    "cross_down",
]
