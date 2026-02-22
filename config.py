"""
config.py
=========
Centralised runtime configuration.  All times are US/Eastern-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# ----------------------------------------------------------------------- #
#  Kiwoom TR mapping — centralised placeholder for overseas equity TRs  (E)
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
    # 해외주식 현재가 (current price query)
    tr_current_price: str = "PLACEHOLDER_OVERSEAS_CURRENT_PRICE"
    tr_current_price_input: str = "종목코드"
    tr_current_price_output: str = "현재가"

    # 해외주식 일봉 (daily OHLCV)
    tr_daily_ohlcv: str = "PLACEHOLDER_OVERSEAS_DAILY_OHLCV"
    tr_daily_ohlcv_input: str = "종목코드"
    tr_daily_ohlcv_outputs: str = "일자,시가,고가,저가,종가,거래량"

    # 해외주식 잔고조회 (holdings / balance)
    tr_holdings: str = "PLACEHOLDER_OVERSEAS_HOLDINGS"
    tr_holdings_input: str = "계좌번호"
    tr_holdings_outputs: str = "종목번호,보유수량,매입단가"

    # 해외주식 주문 (order TR — may differ from domestic SendOrder)
    tr_order: str = "PLACEHOLDER_OVERSEAS_ORDER"

    # Order side codes
    order_side_buy: int = 1     # TODO(kiwoom): confirm for overseas
    order_side_sell: int = 2    # TODO(kiwoom): confirm for overseas
    order_side_cancel: int = 3  # TODO(kiwoom): confirm for overseas

    # Order type codes
    order_type_limit: str = "00"   # 지정가
    order_type_market: str = "03"  # 시장가

    def validate(self) -> list[str]:
        """Return a list of fields that still contain placeholder values.

        Called on startup.  If the list is non-empty, the runtime must
        refuse to trade.
        """
        problems: list[str] = []
        for field_name in [
            "tr_current_price",
            "tr_daily_ohlcv",
            "tr_holdings",
            "tr_order",
        ]:
            val = getattr(self, field_name)
            if val.startswith("PLACEHOLDER_"):
                problems.append(f"{field_name}={val}")
        return problems


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
    kiwoom_tr: KiwoomTrConfig = KiwoomTrConfig()  # centralised TR mapping (E)

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
