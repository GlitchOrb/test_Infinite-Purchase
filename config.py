"""
config.py
=========
키움 REST 전용 PyQt 애플리케이션 런타임 설정.

환경변수 없이 동작 가능하도록 설계됨.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: str = "runtime.db"

    kiwoom_account: str = ""

    signal_ticker: str = "SOXX"
    exec_bull: str = "SOXL"
    exec_bear: str = "SOXS"

    quote_refresh_ms: int = 1500
    ohlcv_refresh_ms: int = 45_000
    account_refresh_ms: int = 4_000
    tape_refresh_ms: int = 1_500

    market_tz: str = "America/New_York"
    market_open_h: int = 9
    market_open_m: int = 30
    market_close_h: int = 16
    market_close_m: int = 0

    buy_before_close_min: int = 5
    orphan_cleanup_after_close_min: int = 10
    regime_compute_after_close_min: int = 15
    reconcile_interval_min: int = 30

    reconcile_qty_tolerance: int = 0
    reconcile_cost_tolerance: float = 0.02

    kill_resume_passcode: str = "resume123"

    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_interval_s: int = 5

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
