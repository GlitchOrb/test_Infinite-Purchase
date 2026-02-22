"""
config.py
=========
Centralised runtime configuration.  All times are US/Eastern-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable runtime configuration — tweak defaults here or override."""

    # -- Database --
    db_path: str = "runtime.db"

    # -- Kiwoom --
    kiwoom_clsid: str = "KHOPENAPI.KHOpenAPICtrl.1"
    kiwoom_account: str = ""                     # set at startup
    kiwoom_req_interval_ms: int = 1000           # min ms between TR calls
    kiwoom_max_retries: int = 5
    kiwoom_backoff_base_s: float = 1.0
    kiwoom_backoff_cap_s: float = 30.0

    # -- Market schedule (US/Eastern, 24h format) --
    market_tz: str = "US/Eastern"
    market_open_h: int = 9
    market_open_m: int = 30
    market_close_h: int = 16
    market_close_m: int = 0

    # -- Scheduled jobs (minutes relative to market close) --
    buy_before_close_min: int = 10               # T-10 min
    orphan_cleanup_after_close_min: int = 5      # T+5 min
    regime_compute_after_close_min: int = 15     # T+15 min
    reconcile_interval_min: int = 15             # periodic light reconcile
    order_refresh_interval_min: int = 5          # order status poll

    # -- Reconcile --
    reconcile_qty_tolerance: int = 0             # exact match
    reconcile_cost_tolerance: float = 0.02       # 2 % relative

    # -- Signal / execution assets --
    signal_ticker: str = "SOXX"
    exec_bull: str = "SOXL"
    exec_bear: str = "SOXS"

    # -- Telegram kill switch --
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_interval_s: int = 5
    kill_resume_passcode: str = "CONFIRM_RESUME"

    # -- Misc --
    log_level: str = "INFO"
