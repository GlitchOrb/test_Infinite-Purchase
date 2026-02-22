"""
config.py
=========
Centralised runtime configuration.  All times are US/Eastern-aware.

Supports both legacy Kiwoom TR mapping AND the new KIS REST API.
The engine uses KIS exclusively; the Kiwoom config is retained for
backward compatibility only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict


# ----------------------------------------------------------------------- #
#  KIS REST API configuration                                              #
# ----------------------------------------------------------------------- #

@dataclass(frozen=True)
class KisApiConfig:
    """Korea Investment & Securities Open-API (REST) credentials.

    All values can be loaded from environment variables.  The ``validate``
    method ensures nothing is left blank before the engine starts.
    """
    # Auth
    app_key: str = ""
    app_secret: str = ""
    access_token: str = ""        # populated at runtime via token refresh
    token_type: str = "Bearer"

    # Account
    account_no: str = ""          # e.g. "50123456-01"
    account_product_code: str = "01"

    # Environment
    base_url: str = "https://openapi.koreainvestment.com:9443"
    is_paper: bool = False        # True = virtual trading (모의투자)

    # REST endpoints (overseas equity)
    path_token: str = "/oauth2/tokenP"
    path_quote: str = "/uapi/overseas-price/v1/quotations/price"
    path_daily: str = "/uapi/overseas-price/v1/quotations/dailyprice"
    path_balance: str = "/uapi/overseas-stock/v1/trading/inquire-balance"
    path_order: str = "/uapi/overseas-stock/v1/trading/order"
    path_order_status: str = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
    path_forex: str = "/uapi/overseas-price/v1/quotations/exchange-rate"

    # Exchange code
    exchange_code: str = "NASD"   # NASDAQ

    # Rate limiting
    req_interval_s: float = 0.25  # 250 ms between calls (KIS guideline)
    max_retries: int = 5
    backoff_base_s: float = 1.0
    backoff_cap_s: float = 30.0

    def validate(self) -> list[str]:
        """Return a list of empty/unconfigured fields."""
        problems: list[str] = []
        for f in ["app_key", "app_secret", "account_no"]:
            val = getattr(self, f)
            if not val:
                problems.append(f"{f} is empty")
        return problems

    @classmethod
    def from_env(cls) -> "KisApiConfig":
        """Build config from environment variables."""
        return cls(
            app_key=os.environ.get("KIS_APP_KEY", ""),
            app_secret=os.environ.get("KIS_APP_SECRET", ""),
            account_no=os.environ.get("KIS_ACCOUNT_NO", ""),
            is_paper=os.environ.get("KIS_PAPER_TRADE", "false").lower() == "true",
        )


# ----------------------------------------------------------------------- #
#  Legacy Kiwoom TR mapping (retained for backward compat)                  #
# ----------------------------------------------------------------------- #

@dataclass(frozen=True)
class KiwoomTrConfig:
    """Centralised TR code / field mapping for Kiwoom overseas equities.

    These are **placeholders**.  Replace each ``PLACEHOLDER_*`` value
    with the actual TR ID from your Kiwoom OpenAPI+ documentation
    before going live.

    On startup the runtime will validate that no placeholder values
    remain.  If any do, the system will refuse to trade and enter
    emergency stop with an explanatory log.
    """
    tr_current_price: str = "PLACEHOLDER_OVERSEAS_CURRENT_PRICE"
    tr_current_price_input: str = "종목코드"
    tr_current_price_output: str = "현재가"
    tr_daily_ohlcv: str = "PLACEHOLDER_OVERSEAS_DAILY_OHLCV"
    tr_daily_ohlcv_input: str = "종목코드"
    tr_daily_ohlcv_outputs: str = "일자,시가,고가,저가,종가,거래량"
    tr_holdings: str = "PLACEHOLDER_OVERSEAS_HOLDINGS"
    tr_holdings_input: str = "계좌번호"
    tr_holdings_outputs: str = "종목번호,보유수량,매입단가"
    tr_order: str = "PLACEHOLDER_OVERSEAS_ORDER"
    order_side_buy: int = 1
    order_side_sell: int = 2
    order_side_cancel: int = 3
    order_type_limit: str = "00"
    order_type_market: str = "03"

    def validate(self) -> list[str]:
        problems: list[str] = []
        for field_name in [
            "tr_current_price", "tr_daily_ohlcv",
            "tr_holdings", "tr_order",
        ]:
            val = getattr(self, field_name)
            if val.startswith("PLACEHOLDER_"):
                problems.append(f"{field_name}={val}")
        return problems


# ----------------------------------------------------------------------- #
#  Master runtime config                                                    #
# ----------------------------------------------------------------------- #

@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable runtime configuration -- tweak defaults here or override."""

    # -- Database --
    db_path: str = "runtime.db"

    # -- KIS REST API (primary) --
    kis: KisApiConfig = field(default_factory=KisApiConfig)

    # -- Legacy Kiwoom (retained for backward compat) --
    kiwoom_clsid: str = "KHOPENAPI.KHOpenAPICtrl.1"
    kiwoom_account: str = ""
    kiwoom_req_interval_ms: int = 1000
    kiwoom_max_retries: int = 5
    kiwoom_backoff_base_s: float = 1.0
    kiwoom_backoff_cap_s: float = 30.0
    kiwoom_tr: KiwoomTrConfig = field(default_factory=KiwoomTrConfig)

    # -- Market schedule (US/Eastern, 24h format) --
    market_tz: str = "US/Eastern"
    market_open_h: int = 9
    market_open_m: int = 30
    market_close_h: int = 16
    market_close_m: int = 0

    # -- Scheduled jobs (minutes relative to market close) --
    buy_before_close_min: int = 10
    orphan_cleanup_after_close_min: int = 5
    regime_compute_after_close_min: int = 15
    reconcile_interval_min: int = 15
    order_refresh_interval_min: int = 5

    # -- Reconcile --
    reconcile_qty_tolerance: int = 0
    reconcile_cost_tolerance: float = 0.02

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

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        """Build config from environment variables."""
        return cls(
            kis=KisApiConfig.from_env(),
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            db_path=os.environ.get("DB_PATH", "runtime.db"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
