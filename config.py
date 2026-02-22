"""
config.py
=========
Runtime configuration for the Kiwoom REST-only PyQt application.

This project is intentionally single-broker:
- Kiwoom REST OpenAPI for authenticated market/account/order flows
- No KIS
- No Kiwoom COM/QAx runtime in product flow
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    # Database
    db_path: str = "runtime.db"

    # Kiwoom REST / account
    kiwoom_account: str = ""

    # Symbol defaults
    signal_ticker: str = "SOXX"
    exec_bull: str = "SOXL"
    exec_bear: str = "SOXS"

    # UI/refresh defaults
    quote_refresh_ms: int = 1500
    ohlcv_refresh_ms: int = 45_000
    account_refresh_ms: int = 4_000
    tape_refresh_ms: int = 1_500

    # Market schedule (US Eastern)
    market_tz: str = "America/New_York"
    market_open_h: int = 9
    market_open_m: int = 30
    market_close_h: int = 16
    market_close_m: int = 0

    # Reconcile tolerances
    reconcile_qty_tolerance: int = 0
    reconcile_cost_tolerance: float = 0.02

    # Telegram (alerts/kill switch only)
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_interval_s: int = 5

    # Misc
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            db_path=os.environ.get("DB_PATH", "runtime.db"),
            kiwoom_account=os.environ.get("KIWOOM_ACCOUNT", ""),
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
