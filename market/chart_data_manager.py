"""차트 데이터 매니저 — Primary/Fallback 2-레이어 마켓 데이터 아키텍처.

기능:
 • Layer A: Kiwoom REST → OHLCV daily/intraday
 • Layer B: 캐시 fallback (API 실패 시 자동 전환)
 • 확장시간(프리/애프터) 데이터 존재 여부 자동 감지
 • 타임스탬프 기반 증분 업데이트 (같은 캔들 → 업데이트, 새 캔들 → 추가)
 • 워커 스레드에서 실행 → 메인 스레드로 시그널 전달
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

from market.candle_cache import CandleCacheManager
from market.market_status import MarketSession, MarketStatusManager

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

log = logging.getLogger(__name__)


# ── 확장시간 시간 범위 (ET) ──
_PRE_RANGE = (time(4, 0), time(9, 30))    # 04:00 ~ 09:30
_AFTER_RANGE = (time(16, 0), time(20, 0)) # 16:00 ~ 20:00


class ChartDataSignals(QObject):
    """차트 데이터 시그널 — 메인 스레드로 전달."""
    candles_ready = pyqtSignal(str, list, bool)    # symbol, candles, is_cached
    quote_ready = pyqtSignal(str, object)          # symbol, Quote
    error = pyqtSignal(str, str)                   # symbol, error_message
    extended_hours_status = pyqtSignal(str, bool)   # symbol, supported


class ChartDataWorker(QRunnable):
    """워커 스레드에서 OHLCV 데이터 페치."""

    def __init__(
        self,
        symbol: str,
        fetch_fn: Callable[[], List[Dict[str, Any]]],
        cache: CandleCacheManager,
        market_mgr: MarketStatusManager,
        signals: ChartDataSignals,
    ) -> None:
        super().__init__()
        self._symbol = symbol
        self._fetch_fn = fetch_fn
        self._cache = cache
        self._market_mgr = market_mgr
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            candles = self._fetch_fn()
            if not candles:
                # API가 빈 데이터 반환 → 캐시 폴백
                cached = self._cache.load_candles(self._symbol)
                if cached:
                    self._signals.candles_ready.emit(self._symbol, cached, True)
                else:
                    self._signals.error.emit(self._symbol, "시세 데이터를 불러올 수 없습니다")
                return

            # 확장시간 지원 감지
            extended = self._detect_extended_hours(candles)
            self._signals.extended_hours_status.emit(self._symbol, extended)

            # 캐시에 저장
            self._cache.save_candles(
                self._symbol, candles,
                timeframe="daily",
                extended_hours=extended,
            )

            self._signals.candles_ready.emit(self._symbol, candles, False)
        except Exception as exc:
            log.warning("Chart data fetch failed for %s: %s", self._symbol, exc)
            # 폴백: 캐시에서 로드
            cached = self._cache.load_candles(self._symbol)
            if cached:
                self._signals.candles_ready.emit(self._symbol, cached, True)
                self._signals.error.emit(self._symbol, f"최신 데이터 로드 실패 — 캐시 데이터 표시 중")
            else:
                self._signals.error.emit(self._symbol, f"시세 데이터를 불러올 수 없습니다: {exc}")

    @staticmethod
    def _detect_extended_hours(candles: List[Dict[str, Any]]) -> bool:
        """캔들 타임스탬프에서 프리/애프터 시간대 데이터 존재 여부 확인."""
        for c in candles:
            ts_str = str(c.get("time", c.get("dt", c.get("date", ""))))
            if not ts_str or len(ts_str) < 4:
                continue

            try:
                # 시간 추출 시도 (HH:MM 또는 HHMM 형태)
                if ":" in ts_str:
                    parts = ts_str.split(":")
                    h, m = int(parts[-2][-2:]), int(parts[-1][:2])
                elif len(ts_str) >= 8 and ts_str.isdigit():
                    # YYYYMMDDHHMMSS 형태
                    h, m = int(ts_str[8:10]), int(ts_str[10:12])
                else:
                    continue

                t = time(h, m)
                if _PRE_RANGE[0] <= t < _PRE_RANGE[1]:
                    return True
                if _AFTER_RANGE[0] <= t < _AFTER_RANGE[1]:
                    return True
            except (ValueError, IndexError):
                continue

        return False


class ChartDataManager:
    """차트 데이터의 2-레이어 아키텍처를 관리.

    Layer A: Primary (Kiwoom REST broker)
    Layer B: Cache fallback (SQLite)
    """

    def __init__(
        self,
        cache: CandleCacheManager,
        market_mgr: MarketStatusManager,
    ) -> None:
        self._cache = cache
        self._market_mgr = market_mgr
        self.signals = ChartDataSignals()

        # 현재 데이터 상태
        self._current_candles: Dict[str, List[Dict[str, Any]]] = {}
        self._extended_hours_support: Dict[str, bool] = {}
        self._consecutive_failures: Dict[str, int] = {}

    @property
    def cache(self) -> CandleCacheManager:
        return self._cache

    def get_cached_candles(self, symbol: str) -> List[Dict[str, Any]]:
        """캐시된 캔들 즉시 반환 (비동기 X, 앱 시작 시 사용)."""
        if symbol in self._current_candles and self._current_candles[symbol]:
            return self._current_candles[symbol]
        return self._cache.load_candles(symbol)

    def create_fetch_worker(
        self,
        symbol: str,
        fetch_fn: Callable[[], List[Dict[str, Any]]],
    ) -> ChartDataWorker:
        """비동기 데이터 페치용 워커 생성."""
        return ChartDataWorker(
            symbol=symbol,
            fetch_fn=fetch_fn,
            cache=self._cache,
            market_mgr=self._market_mgr,
            signals=self.signals,
        )

    def on_candles_received(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
        is_cached: bool,
    ) -> List[Dict[str, Any]]:
        """캔들 수신 시 증분 업데이트 처리.

        Returns: 최종 캔들 리스트 (기존 + 새로운 데이터 병합)
        """
        self._consecutive_failures[symbol] = 0

        if is_cached or symbol not in self._current_candles:
            self._current_candles[symbol] = list(candles)
            return self._current_candles[symbol]

        # 증분 처리: 기존 캔들과 비교
        existing = self._current_candles[symbol]
        existing_dates = {str(c.get("date", "")): i for i, c in enumerate(existing)}

        updated = False
        for new_c in candles:
            dt = str(new_c.get("date", ""))
            if dt in existing_dates:
                # 동일 시간 → 업데이트
                idx = existing_dates[dt]
                existing[idx] = new_c
                updated = True
            else:
                # 새 캔들 → 추가
                existing.append(new_c)
                updated = True

        if updated:
            # 날짜순 정렬
            existing.sort(key=lambda c: str(c.get("date", "")))

        self._current_candles[symbol] = existing
        return existing

    def record_failure(self, symbol: str) -> int:
        """실패 횟수 기록. 반환값: 연속 실패 횟수."""
        count = self._consecutive_failures.get(symbol, 0) + 1
        self._consecutive_failures[symbol] = count
        return count

    def is_extended_supported(self, symbol: str) -> bool:
        """해당 심볼의 확장시간 데이터 지원 여부."""
        return self._extended_hours_support.get(
            symbol,
            self._cache.is_extended_hours_supported(symbol),
        )

    def set_extended_support(self, symbol: str, supported: bool) -> None:
        self._extended_hours_support[symbol] = supported
