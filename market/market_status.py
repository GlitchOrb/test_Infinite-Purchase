"""시장 상태 관리자 — US ETF 마켓 시간대 감지.

기능:
 • 미국 동부 시간(ET) 기준 시장 상태 판별
 • 정규장 / 프리마켓 / 애프터마켓 / 휴장 / 주말 감지
 • 폴링 간격 결정 (시장 열림 → 3초, 그 외 → 5초, 폐장 → 0)
 • US 공휴일 기본 목록 포함
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, date
from enum import Enum
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


class MarketSession(Enum):
    """미국 ETF 시장 세션."""
    PRE_MARKET = "프리마켓"
    REGULAR = "정규장"
    AFTER_HOURS = "애프터마켓"
    CLOSED = "휴장"
    WEEKEND = "주말"


# ── 미국 공휴일 (2024-2027, 날짜만 관리) ──
_US_HOLIDAYS = frozenset([
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25),
    date(2027, 12, 24),
])

# ── 시간 상수 (미국 동부 시간) ──
_PRE_OPEN = time(4, 0)      # 프리마켓 시작
_REGULAR_OPEN = time(9, 30)  # 정규장 시작
_REGULAR_CLOSE = time(16, 0) # 정규장 종료
_AFTER_CLOSE = time(20, 0)   # 애프터마켓 종료

_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketStatus:
    """현재 시장 상태 스냅샷."""
    session: MarketSession
    et_now: datetime
    display_time: str       # "09:42"
    display_label: str      # "정규장"
    is_tradable: bool       # 정규장만 True
    poll_interval_ms: int   # 폴링 간격 (0 = 폴링 중지)


class MarketStatusManager:
    """US ETF 시장 상태를 실시간 판별."""

    def __init__(self, tz_name: str = "America/New_York") -> None:
        self._tz = ZoneInfo(tz_name)

    def get_status(self, now: Optional[datetime] = None) -> MarketStatus:
        """현재 시각 기준 시장 상태 반환."""
        et_now = (now or datetime.now(self._tz)).astimezone(self._tz)
        t = et_now.time()
        d = et_now.date()

        # 주말 감지
        if d.weekday() >= 5:
            return self._make(MarketSession.WEEKEND, et_now, poll_ms=0)

        # 공휴일 감지
        if d in _US_HOLIDAYS:
            return self._make(MarketSession.CLOSED, et_now, poll_ms=0)

        # 시간대별 세션 판별
        if _PRE_OPEN <= t < _REGULAR_OPEN:
            return self._make(MarketSession.PRE_MARKET, et_now, poll_ms=5000)

        if _REGULAR_OPEN <= t < _REGULAR_CLOSE:
            return self._make(MarketSession.REGULAR, et_now, poll_ms=3000)

        if _REGULAR_CLOSE <= t < _AFTER_CLOSE:
            return self._make(MarketSession.AFTER_HOURS, et_now, poll_ms=5000)

        # 그 외 (0:00~4:00, 20:00~24:00)
        return self._make(MarketSession.CLOSED, et_now, poll_ms=0)

    def is_extended_hours(self, now: Optional[datetime] = None) -> bool:
        """프리/애프터 시간대인지 확인."""
        status = self.get_status(now)
        return status.session in (MarketSession.PRE_MARKET, MarketSession.AFTER_HOURS)

    def next_open_in_seconds(self, now: Optional[datetime] = None) -> int:
        """다음 프리마켓 오픈까지 남은 초 (최대 72시간)."""
        et_now = (now or datetime.now(self._tz)).astimezone(self._tz)
        t = et_now.time()

        if t < _PRE_OPEN:
            target = datetime.combine(et_now.date(), _PRE_OPEN, self._tz)
        else:
            # 내일 프리마켓
            from datetime import timedelta
            next_day = et_now.date() + timedelta(days=1)
            # 주말 skip
            while next_day.weekday() >= 5 or next_day in _US_HOLIDAYS:
                next_day += timedelta(days=1)
            target = datetime.combine(next_day, _PRE_OPEN, self._tz)

        delta = (target - et_now).total_seconds()
        return max(0, int(delta))

    @staticmethod
    def _make(session: MarketSession, et_now: datetime, poll_ms: int) -> MarketStatus:
        return MarketStatus(
            session=session,
            et_now=et_now,
            display_time=et_now.strftime("%H:%M"),
            display_label=session.value,
            is_tradable=(session == MarketSession.REGULAR),
            poll_interval_ms=poll_ms,
        )
