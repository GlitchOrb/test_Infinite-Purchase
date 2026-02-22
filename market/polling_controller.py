"""폴링 컨트롤러 — QTimer 기반 마켓 데이터 업데이트 루프.

동작 규칙:
 • 정규장   → 3초마다 폴링
 • 프리/애프터 → 5초마다 폴링
 • 휴장/주말 → 폴링 중지
 • API 실패 → 10초 후 재시도
 • 3회 연속 실패 → 폴링 일시정지 + 에러 라벨 표시
 • 폴링 상태 변경 시 시그널 발행

설계:
 • 단일 QTimer — 중복 타이머 방지
 • UI 스레드 블로킹 없음
 • QThreadPool 활용
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt5.QtCore import QObject, QThreadPool, QTimer, pyqtSignal

from market.chart_data_manager import ChartDataManager
from market.market_status import MarketSession, MarketStatusManager

log = logging.getLogger(__name__)


class PollingState:
    """폴링 상태 상수."""
    ACTIVE = "active"       # 정상 폴링 중
    PAUSED = "paused"       # 일시 정지 (연속 실패)
    STOPPED = "stopped"     # 시장 폐장으로 중지
    RETRY = "retry"         # 에러 재시도 대기 중


class PollingController(QObject):
    """마켓 데이터 폴링 루프 관리."""

    # ── 시그널 ──
    state_changed = pyqtSignal(str)          # PollingState
    market_status_changed = pyqtSignal(object)  # MarketStatus
    poll_tick = pyqtSignal()                 # 매 폴링마다 발행

    MAX_CONSECUTIVE_FAILURES = 3
    RETRY_INTERVAL_MS = 10_000
    MARKET_CHECK_INTERVAL_MS = 30_000  # 30초마다 시장 상태 재확인

    def __init__(
        self,
        chart_mgr: ChartDataManager,
        market_mgr: MarketStatusManager,
        thread_pool: QThreadPool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._chart_mgr = chart_mgr
        self._market_mgr = market_mgr
        self._pool = thread_pool

        self._state = PollingState.STOPPED
        self._current_interval_ms = 0
        self._fetch_fn: Optional[Callable] = None
        self._symbol: str = ""
        self._consecutive_failures = 0

        # 단일 폴링 타이머
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._on_tick)

        # 시장 상태 확인 타이머
        self._market_timer = QTimer(self)
        self._market_timer.setSingleShot(False)
        self._market_timer.timeout.connect(self._check_market_status)

        # ChartDataManager 시그널 연결
        self._chart_mgr.signals.candles_ready.connect(self._on_data_success)
        self._chart_mgr.signals.error.connect(self._on_data_error)

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == PollingState.ACTIVE

    # ─── 시작 / 중지 ───
    def start(self, symbol: str, fetch_fn: Callable) -> None:
        """폴링 시작. 시장 상태에 따라 자동으로 간격 결정."""
        self._symbol = symbol
        self._fetch_fn = fetch_fn
        self._consecutive_failures = 0

        # 즉시 시장 상태 확인 → 적절한 간격 설정
        self._check_market_status()

        # 시장 상태 주기적 확인 시작
        if not self._market_timer.isActive():
            self._market_timer.start(self.MARKET_CHECK_INTERVAL_MS)

    def stop(self) -> None:
        """폴링 완전 중지."""
        self._timer.stop()
        self._market_timer.stop()
        self._set_state(PollingState.STOPPED)

    def pause(self) -> None:
        """수동 일시 정지."""
        self._timer.stop()
        self._set_state(PollingState.PAUSED)

    def resume(self) -> None:
        """일시 정지에서 재개."""
        self._consecutive_failures = 0
        self._check_market_status()

    def change_symbol(self, symbol: str, fetch_fn: Callable) -> None:
        """심볼 변경 시 폴링 재시작."""
        self._timer.stop()
        self._symbol = symbol
        self._fetch_fn = fetch_fn
        self._consecutive_failures = 0
        self._check_market_status()

    # ─── 내부 로직 ───
    def _on_tick(self) -> None:
        """폴링 틱 — 워커 스레드에서 데이터 페치."""
        if not self._fetch_fn or not self._symbol:
            return

        self.poll_tick.emit()

        worker = self._chart_mgr.create_fetch_worker(
            symbol=self._symbol,
            fetch_fn=self._fetch_fn,
        )
        self._pool.start(worker)

    def _on_data_success(self, symbol: str, candles: list, is_cached: bool) -> None:
        """데이터 성공 수신."""
        if symbol != self._symbol:
            return
        self._consecutive_failures = 0
        if self._state == PollingState.RETRY:
            self._check_market_status()  # 정상 간격으로 복귀

    def _on_data_error(self, symbol: str, msg: str) -> None:
        """데이터 에러 수신."""
        if symbol != self._symbol:
            return

        self._consecutive_failures += 1

        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            # 3회 연속 실패 → 폴링 일시 정지
            self._timer.stop()
            self._set_state(PollingState.PAUSED)
            log.warning(
                "Polling paused for %s after %d failures",
                symbol, self._consecutive_failures,
            )
        else:
            # 10초 후 재시도
            self._timer.stop()
            self._timer.start(self.RETRY_INTERVAL_MS)
            self._set_state(PollingState.RETRY)

    def _check_market_status(self) -> None:
        """시장 상태 확인 → 폴링 간격 조정."""
        status = self._market_mgr.get_status()
        self.market_status_changed.emit(status)

        target_ms = status.poll_interval_ms

        if target_ms == 0:
            # 시장 폐장 → 폴링 중지
            if self._timer.isActive():
                self._timer.stop()
            self._set_state(PollingState.STOPPED)
            return

        # 폴링 간격이 변경되었거나 시작해야 할 때
        if (not self._timer.isActive() or
            self._current_interval_ms != target_ms):
            self._timer.stop()
            self._current_interval_ms = target_ms
            self._timer.start(target_ms)
            self._set_state(PollingState.ACTIVE)

            # 즉시 첫 틱 실행
            self._on_tick()

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)
