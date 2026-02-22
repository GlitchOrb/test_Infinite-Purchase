"""트레이딩 화면 — 프로덕션급 마켓 데이터 시스템 통합.

변경사항:
 • MarketStatusManager 통합 → 시장 상태 표시
 • ChartDataManager 통합 → 2-레이어 데이터 아키텍처
 • PollingController 통합 → 스마트 폴링
 • CandleCacheManager 통합 → 캐시 기반 빠른 렌더링
 • 차트 헤더: 종목명, 현재가, 등락률, ET 시간, 시장 상태, 폴링 인디케이터
 • 확장시간 미지원 감지 및 표시
 • 3회 연속 API 실패 시 자동 일시정지 + 에러 라벨 표시
 • 증분 차트 업데이트 (전체 리렌더 방지)
 • 기존 매매 로직 유지
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto.auto_trading_controller import AutoTradingController
from broker.base import BrokerBase, Quote
from broker.kiwoom_rest_broker import KiwoomRestBroker, LiveBrokerError
from broker.paper_broker import PaperBroker
from conditions.condition_engine import ConditionEngine
from db import is_emergency_stop, set_emergency_stop, set_system
from indicators.obv import OBVIndicator
from indicators.rsi import RSIIndicator
from indicators.sma import SMAIndicator
from market.candle_cache import CandleCacheManager
from market.chart_data_manager import ChartDataManager
from market.market_status import MarketSession, MarketStatus, MarketStatusManager
from market.polling_controller import PollingController, PollingState
from widgets.chart_widget import ChartWidget, FillMarker
from widgets.order_panel import OrderPanel
from widgets.tape_widget import TapeWidget
from widgets.toast import Toast


# ─── 디자인 토큰 ───
_PRIMARY = "#3182F6"
_SUCCESS = "#32A85C"
_ERROR = "#E05A5A"
_TEXT = "#191F28"
_TEXT_SECONDARY = "#4E5968"
_TEXT_MUTED = "#8B95A1"
_TEXT_DISABLED = "#B0B8C1"
_CARD_BG = "#FFFFFF"


class _WorkerSignals(QObject):
    success = pyqtSignal(str, object)
    error = pyqtSignal(str, str)


class _Worker(QRunnable):
    def __init__(self, key: str, fn: Callable[[], object], signals: _WorkerSignals) -> None:
        super().__init__()
        self.key = key
        self.fn = fn
        self.signals = signals

    def run(self) -> None:
        try:
            self.signals.success.emit(self.key, self.fn())
        except Exception as exc:
            self.signals.error.emit(self.key, str(exc))


class TradingScreen(QWidget):
    MODE_GUEST = "Guest"
    MODE_PAPER = "Paper"
    MODE_LIVE = "Live"

    def __init__(
        self,
        conn: sqlite3.Connection,
        auth_manager,
        cfg,
        telegram_alert: Optional[Callable[[str], None]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.conn = conn
        self.auth = auth_manager
        self.cfg = cfg
        self.alert = telegram_alert
        self.symbol = "SOXL"
        self.mode = self.MODE_GUEST

        self.thread_pool = QThreadPool.globalInstance()
        self.worker_signals = _WorkerSignals()
        self.worker_signals.success.connect(self._on_worker_success)
        self.worker_signals.error.connect(self._on_worker_error)

        self.live_broker: Optional[BrokerBase] = None
        self.paper_broker = PaperBroker(conn)
        self._manual_order_payload: Optional[Dict[str, Any]] = None
        self._live_disabled_reason: str = ""

        self.indicators = {
            "SMA50": SMAIndicator(50),
            "SMA200": SMAIndicator(200),
            "RSI14": RSIIndicator(14),
            "OBV": OBVIndicator(),
        }

        self.condition_engine = ConditionEngine(
            conn=conn,
            get_emergency_stop=lambda: is_emergency_stop(self.conn),
            set_emergency_stop=lambda x: set_emergency_stop(self.conn, x),
            alert=self.alert,
        )
        self.auto_ctl = AutoTradingController(
            conn=conn,
            cfg=cfg,
            broker_provider=self._resolve_exec_broker_or_none,
            get_symbol_prices=self._get_exec_prices,
            alert=self.alert,
        )

        # ── 마켓 데이터 시스템 초기화 ──
        self._market_mgr = MarketStatusManager()
        self._candle_cache = CandleCacheManager(conn)
        self._chart_data = ChartDataManager(
            cache=self._candle_cache,
            market_mgr=self._market_mgr,
        )
        self._polling = PollingController(
            chart_mgr=self._chart_data,
            market_mgr=self._market_mgr,
            thread_pool=self.thread_pool,
        )

        self._build_ui()
        self._init_db()
        self._restore_settings()

        # ── 시그널 연결 ──
        self._chart_data.signals.candles_ready.connect(self._on_chart_candles)
        self._chart_data.signals.error.connect(self._on_chart_error)
        self._chart_data.signals.extended_hours_status.connect(self._on_extended_status)
        self._polling.state_changed.connect(self._on_polling_state)
        self._polling.market_status_changed.connect(self._on_market_status)
        self.auto_ctl.status_changed.connect(self._on_auto_status)
        self.auto_ctl.event_log.connect(lambda t: self.order_panel.set_status(t))

        # ── 기존 타이머 + 마켓 데이터 폴링 시작 ──
        self._setup_timers()
        self._start_chart_polling()

        # ── 캐시에서 즉시 로드 (빠른 초기 렌더링) ──
        self._load_cached_chart()

    # ═══════════════════════════════════════════
    #  UI 빌드
    # ═══════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)

        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        header_card = self._card()
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(12)

        self.symbol_box = QComboBox()
        self.symbol_box.addItems(["SOXL", "SOXS", "SOXX"])
        self.symbol_box.currentTextChanged.connect(self._on_symbol_changed)
        self.mode_box = QComboBox()
        self.mode_box.addItems([self.MODE_GUEST, self.MODE_PAPER, self.MODE_LIVE])
        self.mode_box.currentTextChanged.connect(self._on_mode_changed)

        # ── 시장 상태 헤더 (새로 추가) ──
        self.market_status_label = QLabel("--")
        self.market_status_label.setObjectName("marketStatusLabel")
        self.market_status_label.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 12px; font-weight: 600;"
            f" background: transparent; padding: 4px 10px;"
            f" border: 1px solid #E5E8EB; border-radius: 8px;"
        )

        self.et_time_label = QLabel("ET --:--:--")
        self.et_time_label.setObjectName("secondaryLabel")

        self.price_label = QLabel("Price --")
        self.price_label.setObjectName("priceLabel")

        # ── 폴링 인디케이터 ──
        self.polling_dot = QLabel("●")
        self.polling_dot.setObjectName("pollingDot")
        self.polling_dot.setStyleSheet(
            f"color: {_TEXT_DISABLED}; font-size: 10px; background: transparent;"
        )
        self.polling_dot.setToolTip("데이터 갱신 상태")

        self.auto_toggle = QCheckBox("자동매매")
        self.auto_toggle.toggled.connect(self._on_auto_toggle)
        self.auto_status = QLabel("OFF")
        self.auto_status.setObjectName("secondaryLabel")

        self.settings_btn = QPushButton("설정")
        self.reset_paper_btn = QPushButton("모의 초기화")
        self.settings_btn.clicked.connect(self._open_settings)
        self.reset_paper_btn.clicked.connect(self._reset_paper_account)

        # 상단 1행: 종목/모드/자동매매
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        for w in [QLabel("종목"), self.symbol_box, QLabel("모드"), self.mode_box,
                  self.auto_toggle, self.auto_status]:
            controls_row.addWidget(w)
        controls_row.addStretch()

        # 상단 2행: 시장 상태 / 가격 / 시간 / 폴링
        quote_row = QHBoxLayout()
        quote_row.setSpacing(10)
        quote_row.addWidget(self.market_status_label)
        quote_row.addWidget(self.polling_dot)
        quote_row.addStretch()
        quote_row.addWidget(self.et_time_label)
        quote_row.addWidget(self.price_label)
        quote_row.addWidget(self.settings_btn)
        quote_row.addWidget(self.reset_paper_btn)

        header_wrap = QVBoxLayout()
        header_wrap.setContentsMargins(0, 0, 0, 0)
        header_wrap.setSpacing(8)
        header_wrap.addLayout(controls_row)
        header_wrap.addLayout(quote_row)
        header_layout.addLayout(header_wrap, 1)
        root.addWidget(header_card)

        # ── 차트 영역 ──
        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        left_col = QVBoxLayout()
        left_col.setSpacing(16)

        chart_card = self._card()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(16, 16, 16, 16)
        chart_layout.setSpacing(12)

        # 차트 헤더 (확장시간 안내 포함)
        chart_header = QHBoxLayout()
        chart_header.setSpacing(8)
        chart_header.addWidget(self._section_label("차트"))

        self.extended_label = QLabel("")
        self.extended_label.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 11px;"
            " background: transparent; padding: 2px 8px;"
        )
        chart_header.addWidget(self.extended_label)

        self.chart_error_label = QLabel("")
        self.chart_error_label.setStyleSheet(
            f"color: {_ERROR}; font-size: 11px;"
            " background: transparent; padding: 2px 8px;"
        )
        self.chart_error_label.hide()
        chart_header.addWidget(self.chart_error_label)

        chart_header.addStretch()
        chart_layout.addLayout(chart_header)

        ind_row = QHBoxLayout()
        ind_row.setSpacing(8)
        self.chk_sma50 = QCheckBox("SMA50")
        self.chk_sma200 = QCheckBox("SMA200")
        self.chk_rsi = QCheckBox("RSI(14)")
        self.chk_obv = QCheckBox("OBV")
        for c in [self.chk_sma50, self.chk_sma200, self.chk_rsi, self.chk_obv]:
            c.setChecked(True)
            c.toggled.connect(self._refresh_chart)
            ind_row.addWidget(c)
        ind_row.addStretch()
        chart_layout.addLayout(ind_row)

        self.chart = ChartWidget()
        chart_layout.addWidget(self.chart)
        left_col.addWidget(chart_card, 7)

        # ── 하단 그리드 (Account/Position/Orders/Fills) ──
        bottom = QGridLayout()
        bottom.setHorizontalSpacing(16)
        bottom.setVerticalSpacing(16)
        self.account_card = QLabel("Equity: -\nCash: -\nDay PnL: -")
        self.account_card.setObjectName("secondaryLabel")
        self.position_card = QLabel("Qty: -\nAvg: -\nCurrent: -\nUPnL: -\nPnL%: -")
        self.position_card.setObjectName("secondaryLabel")
        bottom.addWidget(self._boxed("계좌", self.account_card), 0, 0)
        bottom.addWidget(self._boxed("포지션", self.position_card), 0, 1)

        self.open_orders = QTableWidget(0, 5)
        self.open_orders.setHorizontalHeaderLabels(["ID", "종목", "구분", "수량", "상태"])
        self.fills_table = QTableWidget(0, 6)
        self.fills_table.setHorizontalHeaderLabels(["시각", "유형", "종목", "구분", "수량", "가격"])
        bottom.addWidget(self._boxed("미체결", self.open_orders), 1, 0)
        bottom.addWidget(self._boxed("체결내역", self.fills_table), 1, 1)
        left_col.addLayout(bottom, 3)

        content_row.addLayout(left_col, 7)

        # ── 우측 패널 (Tape / Order / Position) ──
        self.right_col = QWidget()
        self.right_col_layout = QVBoxLayout(self.right_col)
        self.right_col_layout.setContentsMargins(0, 0, 0, 0)
        self.right_col_layout.setSpacing(16)

        self.tape_card = self._card()
        tape_layout = QVBoxLayout(self.tape_card)
        tape_layout.setContentsMargins(16, 16, 16, 16)
        tape_layout.setSpacing(8)
        tape_layout.addWidget(self._section_label("실시간 체결"))
        self.tape_widget = TapeWidget()
        self.day_summary = QLabel("High: -  Low: -  Volume: -")
        self.day_summary.setObjectName("secondaryLabel")
        tape_layout.addWidget(self.tape_widget)
        tape_layout.addWidget(self.day_summary)

        self.order_card = self._card()
        order_layout = QVBoxLayout(self.order_card)
        order_layout.setContentsMargins(16, 16, 16, 16)
        order_layout.setSpacing(8)
        order_layout.addWidget(self._section_label("주문"))
        self.order_panel = OrderPanel()
        self.order_panel.order_requested.connect(self._on_manual_order)
        self.order_panel.cancel_all_requested.connect(self._cancel_all_orders)
        order_layout.addWidget(self.order_panel)

        self.position_summary_compact = QLabel("Qty: -\nAvg: -\nCurrent: -\nUPnL: -\nPnL%: -")
        self.position_summary_compact.setObjectName("secondaryLabel")
        self.position_summary_card = self._boxed("포지션 요약", self.position_summary_compact)

        self.right_col_layout.addWidget(self.tape_card, 5)
        self.right_col_layout.addWidget(self.order_card, 4)
        self.right_col_layout.addWidget(self.position_summary_card, 3)

        # ── 반응형 탭 (compact) ──
        self.compact_tabs = QTabBar()
        self.compact_tabs.setObjectName("compactPanelTabs")
        self.compact_tabs.addTab("체결")
        self.compact_tabs.addTab("주문")
        self.compact_tabs.addTab("포지션")
        self.compact_tabs.currentChanged.connect(self._sync_compact_stack)
        self.compact_tabs.hide()

        self.compact_stack = QStackedWidget()
        self.compact_pages = [QWidget(), QWidget(), QWidget()]
        self.compact_page_layouts = []
        for page in self.compact_pages:
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(0)
            self.compact_page_layouts.append(page_layout)
            self.compact_stack.addWidget(page)
        self.compact_stack.hide()

        content_row.addWidget(self.right_col, 3)
        root.addLayout(content_row, 7)
        root.addWidget(self.compact_tabs)
        root.addWidget(self.compact_stack, 4)

        # ── 조건주문 ──
        cond_card = self._card()
        cond_layout = QVBoxLayout(cond_card)
        cond_layout.setContentsMargins(16, 16, 16, 16)
        cond_layout.setSpacing(12)
        cond_layout.addWidget(QLabel("조건주문"))

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.cond_op = QComboBox(); self.cond_op.addItems([">=", "<="])
        self.cond_action = QComboBox(); self.cond_action.addItems(["BUY", "SELL"])
        self.cond_type = QComboBox(); self.cond_type.addItems(["MARKET", "LIMIT"])
        self.cond_trigger = QLineEdit(); self.cond_trigger.setPlaceholderText("트리거 가격")
        self.cond_qty = QLineEdit(); self.cond_qty.setPlaceholderText("수량")
        self.cond_limit = QLineEdit(); self.cond_limit.setPlaceholderText("지정가 (선택)")
        form.addRow("조건", self.cond_op)
        form.addRow("구분", self.cond_action)
        form.addRow("주문유형", self.cond_type)
        form.addRow("트리거", self.cond_trigger)
        form.addRow("수량", self.cond_qty)
        form.addRow("지정가", self.cond_limit)
        cond_layout.addLayout(form)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_add_cond = QPushButton("조건 생성")
        self.btn_cancel_cond = QPushButton("선택 취소")
        self.btn_add_cond.clicked.connect(self._create_condition)
        self.btn_cancel_cond.clicked.connect(self._cancel_condition)
        row.addWidget(self.btn_add_cond)
        row.addWidget(self.btn_cancel_cond)
        cond_layout.addLayout(row)

        self.cond_active = QTableWidget(0, 7)
        self.cond_active.setHorizontalHeaderLabels(["ID", "조건", "트리거", "구분", "유형", "수량", "상태"])
        self.cond_hist = QTableWidget(0, 8)
        self.cond_hist.setHorizontalHeaderLabels(["ID", "종목", "조건", "트리거", "구분", "상태", "주문ID", "사유"])
        cond_layout.addWidget(QLabel("활성 조건"))
        cond_layout.addWidget(self.cond_active)
        cond_layout.addWidget(QLabel("이력"))
        cond_layout.addWidget(self.cond_hist)
        root.addWidget(cond_card, 3)

        self.toast = Toast(self)
        self._apply_responsive_mode(self.width())
        self._load_local_theme()

    # ─── 헬퍼 위젯 ───
    def _card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setFrameShape(QFrame.StyledPanel)
        return card

    def _boxed(self, title: str, widget: QWidget) -> QFrame:
        box = self._card()
        l = QVBoxLayout(box)
        l.setContentsMargins(12, 12, 12, 12)
        l.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("cardTitle")
        l.addWidget(title_lbl)
        l.addWidget(widget)
        return box

    def _section_label(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("sectionCaption")
        return label

    def _sync_compact_stack(self, index: int) -> None:
        self.compact_stack.setCurrentIndex(index)

    def _apply_responsive_mode(self, width: int) -> None:
        compact = width < 1100
        if getattr(self, "_compact_mode", None) == compact:
            return
        self._compact_mode = compact

        if compact:
            self._move_card(self.tape_card, self.compact_page_layouts[0])
            self._move_card(self.order_card, self.compact_page_layouts[1])
            self._move_card(self.position_summary_card, self.compact_page_layouts[2])
            self.compact_tabs.setCurrentIndex(0)
            self.compact_stack.setCurrentIndex(0)
        else:
            self._move_card(self.tape_card, self.right_col_layout, 5)
            self._move_card(self.order_card, self.right_col_layout, 4)
            self._move_card(self.position_summary_card, self.right_col_layout, 3)

        self.right_col.setVisible(not compact)
        self.compact_tabs.setVisible(compact)
        self.compact_stack.setVisible(compact)

    def _move_card(self, widget: QWidget, layout: QVBoxLayout, stretch: int = 0) -> None:
        if widget.parentWidget() is layout.parentWidget():
            return
        widget.setParent(layout.parentWidget())
        if stretch:
            layout.addWidget(widget, stretch)
        else:
            layout.addWidget(widget)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._apply_responsive_mode(event.size().width())
        if hasattr(self, "toast"):
            self.toast.move(
                (self.width() - self.toast.width()) // 2,
                self.height() - self.toast.height() - 24,
            )
        super().resizeEvent(event)

    def _load_local_theme(self) -> None:
        from pathlib import Path
        qss_path = Path(__file__).resolve().parent.parent / "styles" / "theme.qss"
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # ═══════════════════════════════════════════
    #  마켓 데이터 시스템
    # ═══════════════════════════════════════════

    def _start_chart_polling(self) -> None:
        """차트 폴링 시작."""
        self._polling.start(
            symbol=self.symbol,
            fetch_fn=self._fetch_ohlcv,
        )

    def _load_cached_chart(self) -> None:
        """앱 시작 시 캐시에서 즉시 차트 로드."""
        cached = self._chart_data.get_cached_candles(self.symbol)
        if cached:
            self._last_candles = cached
            self._refresh_chart()

    def _on_chart_candles(
        self, symbol: str, candles: list, is_cached: bool
    ) -> None:
        """ChartDataManager → 캔들 수신 콜백."""
        if symbol != self.symbol:
            return

        # 증분 업데이트
        final = self._chart_data.on_candles_received(symbol, candles, is_cached)
        self._last_candles = final

        # 에러 라벨 숨기기
        self.chart_error_label.hide()

        self._refresh_chart()

    def _on_chart_error(self, symbol: str, msg: str) -> None:
        """ChartDataManager → 에러 콜백."""
        if symbol != self.symbol:
            return

        count = self._chart_data.record_failure(symbol)

        if count >= PollingController.MAX_CONSECUTIVE_FAILURES:
            # 영구적 에러 라벨
            self.chart_error_label.setText(f"⚠ {msg}")
            self.chart_error_label.show()
        else:
            # 토스트로 알림
            self.toast.show_message(msg, "error")

    def _on_extended_status(self, symbol: str, supported: bool) -> None:
        """확장시간 데이터 지원 여부 업데이트."""
        if symbol != self.symbol:
            return
        self._chart_data.set_extended_support(symbol, supported)

        if supported:
            self.extended_label.setText("📊 확장시간 데이터 포함")
            self.extended_label.setStyleSheet(
                f"color: {_SUCCESS}; font-size: 11px;"
                " background: transparent; padding: 2px 8px;"
            )
        else:
            self.extended_label.setText("확장시간 차트 미지원 (정규장만 표시)")
            self.extended_label.setStyleSheet(
                f"color: {_TEXT_MUTED}; font-size: 11px;"
                " background: transparent; padding: 2px 8px;"
            )

    def _on_polling_state(self, state: str) -> None:
        """폴링 상태 변경 → UI 인디케이터 업데이트."""
        if state == PollingState.ACTIVE:
            self.polling_dot.setStyleSheet(
                f"color: {_SUCCESS}; font-size: 10px; background: transparent;"
            )
            self.polling_dot.setToolTip("데이터 실시간 갱신 중")
        elif state == PollingState.RETRY:
            self.polling_dot.setStyleSheet(
                "color: #F5A623; font-size: 10px; background: transparent;"
            )
            self.polling_dot.setToolTip("데이터 갱신 재시도 중")
        elif state == PollingState.PAUSED:
            self.polling_dot.setStyleSheet(
                f"color: {_ERROR}; font-size: 10px; background: transparent;"
            )
            self.polling_dot.setToolTip("데이터 갱신 일시정지 (3회 연속 실패)")
        else:  # STOPPED
            self.polling_dot.setStyleSheet(
                f"color: {_TEXT_DISABLED}; font-size: 10px; background: transparent;"
            )
            self.polling_dot.setToolTip("시장 폐장 — 데이터 갱신 중지")

    def _on_market_status(self, status: MarketStatus) -> None:
        """시장 상태 변경 → 헤더 업데이트."""
        session = status.session

        # 시장 상태 라벨
        label_text = f"미국 {status.display_time} ({status.display_label})"

        if session == MarketSession.REGULAR:
            color = _SUCCESS
        elif session in (MarketSession.PRE_MARKET, MarketSession.AFTER_HOURS):
            color = "#F5A623"  # 橙
        else:
            color = _TEXT_DISABLED

        self.market_status_label.setText(label_text)
        self.market_status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 600;"
            f" background: transparent; padding: 4px 10px;"
            f" border: 1px solid {color}40; border-radius: 8px;"
        )

        # ET 시간 업데이트
        self.et_time_label.setText(
            status.et_now.strftime("ET %Y-%m-%d %H:%M:%S %Z")
        )

    # ═══════════════════════════════════════════
    #  기존 로직 (매매/주문/조건 — 변경 없음)
    # ═══════════════════════════════════════════

    def _init_db(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()

    def _restore_settings(self) -> None:
        rows = self.conn.execute("SELECT key, value FROM ui_settings").fetchall()
        data = {k: v for k, v in rows}
        self.symbol_box.setCurrentText(data.get("last_symbol", "SOXL"))
        self.mode_box.setCurrentText(data.get("last_mode", self.MODE_GUEST))
        self._refresh_condition_tables()

    def _setup_timers(self) -> None:
        # 시세 타이머 (기존 — quote/account 용)
        self.t_quote = QTimer(self)
        self.t_quote.timeout.connect(lambda: self._run_async("quote", self._fetch_quote))
        self.t_quote.start(1500)

        self.t_account = QTimer(self)
        self.t_account.timeout.connect(
            lambda: self._run_async("account", self._fetch_account_positions)
        )
        self.t_account.start(4000)

        # 시계 (기존)
        self.t_clock = QTimer(self)
        self.t_clock.timeout.connect(self._refresh_time)
        self.t_clock.start(1000)

        # NOTE: OHLCV 타이머는 PollingController로 대체됨

    def _refresh_time(self) -> None:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

        self.et_time_label.setText(
            datetime.now(ZoneInfo("America/New_York")).strftime("ET %Y-%m-%d %H:%M:%S %Z")
        )

    def _run_async(self, key: str, fn: Callable[[], object]) -> None:
        self.thread_pool.start(_Worker(key, fn, self.worker_signals))

    def _on_worker_success(self, key: str, payload: object) -> None:
        if key == "quote":
            q: Quote = payload  # type: ignore[assignment]
            sign = "+" if q.change_pct >= 0 else ""
            self.price_label.setText(
                f"{q.symbol} ${q.price:.2f} ({sign}{q.change_pct:.2f}%)"
            )
            if q.change_pct > 0:
                self.price_label.setStyleSheet("color:#D43B3B;")
            elif q.change_pct < 0:
                self.price_label.setStyleSheet("color:#2F6BDE;")
            else:
                self.price_label.setStyleSheet(f"color:{_TEXT_MUTED};")
            self.day_summary.setText(
                f"High: {q.high:.2f}  Low: {q.low:.2f}  Volume: {q.volume:,}"
            )
            self.paper_broker.update_quote(q)
            self.tape_widget.add_quote_tick(q.price, q.volume)
            self._evaluate_conditions(q)
        elif key == "account":
            account, pos = payload  # type: ignore[misc]
            self._render_account(account, pos)
        elif key == "manual_order":
            result = payload  # type: ignore[assignment]
            self.order_panel.set_status(
                f"ORDER_RESULT {result.get('status')} {result.get('order_id')}"
            )
            self.toast.show_message("주문이 접수되었습니다", "success")
        self._load_tables()
        self._refresh_condition_tables()

    def _on_worker_error(self, key: str, msg: str) -> None:
        self.order_panel.set_status(f"{key} error: {msg}")
        self.toast.show_message(f"{key}: {msg}", "error")
        if key == "manual_order":
            return
        if self.mode == self.MODE_LIVE:
            set_emergency_stop(self.conn, True)
            self.auto_ctl.set_enabled(False)
            self.order_panel.set_manual_enabled(False)
            if self.alert:
                self.alert(f"🚨 {key} failure: {msg}")

    # ── Broker resolution (unchanged) ──

    def _resolve_market_broker(self) -> BrokerBase:
        if self.mode in {self.MODE_GUEST, self.MODE_PAPER}:
            return self.paper_broker
        if self._live_disabled_reason:
            raise LiveBrokerError(self._live_disabled_reason)
        if not self.auth.client:
            raise LiveBrokerError("Live disabled: authentication session unavailable")
        try:
            return KiwoomRestBroker(self.auth.client, self.cfg.kiwoom_account)
        except Exception as exc:
            self._live_disabled_reason = str(exc)
            raise LiveBrokerError(self._live_disabled_reason)

    def _resolve_exec_broker_or_none(self) -> Optional[BrokerBase]:
        if self.mode == self.MODE_GUEST:
            return None
        if self.mode == self.MODE_PAPER:
            return self.paper_broker
        try:
            if not self.live_broker:
                self.live_broker = self._resolve_market_broker()
            return self.live_broker
        except Exception as exc:
            self._live_disabled_reason = str(exc)
            self.order_panel.set_status(self._live_disabled_reason)
            return None

    def _fetch_quote(self) -> Quote:
        b = self._resolve_market_broker()
        return b.get_quote(self.symbol)

    def _fetch_ohlcv(self) -> List[Dict[str, Any]]:
        b = self._resolve_market_broker()
        return b.get_ohlcv(self.symbol, 300)

    def _fetch_account_positions(self):
        if self.mode == self.MODE_GUEST:
            return None, []
        if self.mode == self.MODE_PAPER:
            return self.paper_broker.get_account(), self.paper_broker.get_positions()
        b = self._resolve_exec_broker_or_none()
        if not b:
            raise RuntimeError("Live broker unavailable")
        return b.get_account(), b.get_positions()

    # ── Symbol/Mode changes ──

    def _on_symbol_changed(self, symbol: str) -> None:
        self.symbol = symbol
        self.conn.execute(
            "INSERT OR REPLACE INTO ui_settings(key, value) VALUES(?,?)",
            ("last_symbol", symbol),
        )
        self.conn.commit()

        # 차트 폴링 재시작 (새 심볼)
        self._polling.change_symbol(symbol, self._fetch_ohlcv)

        # 캐시에서 즉시 로드
        self._load_cached_chart()

    def _on_mode_changed(self, mode: str) -> None:
        self.mode = mode
        if mode != self.MODE_LIVE:
            self._live_disabled_reason = ""
            self.live_broker = None
        self.conn.execute(
            "INSERT OR REPLACE INTO ui_settings(key, value) VALUES(?,?)",
            ("last_mode", mode),
        )
        self.conn.commit()
        self.reset_paper_btn.setEnabled(mode == self.MODE_PAPER)
        self.order_panel.set_manual_enabled(
            mode != self.MODE_GUEST and not self.auto_toggle.isChecked()
        )

    # ── Auto trading ──

    def _on_auto_toggle(self, on: bool) -> None:
        if on and self.mode == self.MODE_GUEST:
            QMessageBox.warning(self, "자동매매", "게스트 모드에서는 자동매매를 사용할 수 없습니다")
            self.auto_toggle.setChecked(False)
            return
        self.auto_ctl.set_enabled(on)
        self.order_panel.set_manual_enabled(not on and self.mode != self.MODE_GUEST)

    def _on_auto_status(self, status: str) -> None:
        self.auto_status.setText(status)
        if status == "EMERGENCY STOP":
            self.order_panel.set_manual_enabled(False)

    # ── Manual orders ──

    def _on_manual_order(self, payload: Dict[str, Any]) -> None:
        if self.auto_toggle.isChecked():
            ok = QMessageBox.question(
                self, "자동매매", "자동매매를 일시정지하고 수동 주문을 진행할까요?"
            )
            if ok != QMessageBox.Yes:
                return
            self.auto_toggle.setChecked(False)
            self.auto_ctl.pause()

        if is_emergency_stop(self.conn):
            QMessageBox.warning(self, "긴급정지", "매매가 중단되었습니다")
            return
        if self.mode == self.MODE_GUEST:
            QMessageBox.warning(self, "게스트", "게스트 모드에서는 주문할 수 없습니다")
            return

        self._manual_order_payload = dict(payload)
        self._run_async("manual_order", self._execute_manual_order)

    def _execute_manual_order(self) -> Dict[str, Any]:
        payload = dict(self._manual_order_payload or {})
        qty = int(payload["qty"])
        side = str(payload["side"])
        order_type = (
            "MARKET" if payload["action_type"] == "MARKET"
            else str(payload["order_type"])
        )
        limit_price = payload.get("limit_price")

        if self.mode == self.MODE_PAPER:
            return self.paper_broker.place_order(
                self.symbol, side, qty, order_type, limit_price
            )

        broker = self._resolve_exec_broker_or_none()
        if not broker:
            raise RuntimeError(self._live_disabled_reason or "Live broker unavailable")

        result = broker.place_order(self.symbol, side, qty, order_type, limit_price)
        self.conn.execute(
            "INSERT INTO live_orders(order_id, symbol, side, qty, status, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (result.get("order_id", ""), self.symbol, side, qty,
             result.get("status", "SUBMITTED"), datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        self._reconcile_live_gate()
        return result

    # ── Conditions ──

    def _create_condition(self) -> None:
        if self.mode == self.MODE_GUEST:
            QMessageBox.warning(
                self, "게스트", "게스트 모드에서는 조건주문을 사용할 수 없습니다"
            )
            return
        try:
            cid = self.condition_engine.create_condition(
                mode=self.mode,
                symbol=self.symbol,
                operator=self.cond_op.currentText(),
                trigger_price=float(self.cond_trigger.text().strip()),
                action=self.cond_action.currentText(),
                order_type=self.cond_type.currentText(),
                qty=int(self.cond_qty.text().strip()),
                limit_price=(
                    float(self.cond_limit.text().strip())
                    if self.cond_limit.text().strip() else None
                ),
            )
            self.order_panel.set_status(f"CONDITION_TRIGGER created id={cid}")
            self._refresh_condition_tables()
        except Exception as exc:
            self._fail_safe(f"Condition create failed: {exc}")

    def _cancel_condition(self) -> None:
        row = self.cond_active.currentRow()
        if row < 0:
            return
        cid_item = self.cond_active.item(row, 0)
        if not cid_item:
            return
        self.condition_engine.cancel_condition(int(cid_item.text()))
        self._refresh_condition_tables()

    def _evaluate_conditions(self, quote: Quote) -> None:
        if self.mode == self.MODE_GUEST or is_emergency_stop(self.conn):
            return
        broker = self._resolve_exec_broker_or_none()
        if not broker:
            return
        results = self.condition_engine.evaluate_tick(self.mode, quote, broker)
        for r in results:
            self.order_panel.set_status(
                f"CONDITION_TRIGGER id={r.condition_id} status={r.status.value}"
            )
            if r.status.value == "FAILED":
                self._fail_safe(
                    f"Condition order failed id={r.condition_id}: {r.reason}"
                )

    def _cancel_all_orders(self) -> None:
        if self.mode == self.MODE_PAPER:
            rows = self.conn.execute(
                "SELECT order_id FROM paper_orders WHERE status='OPEN'"
            ).fetchall()
            for (oid,) in rows:
                self.paper_broker.cancel_order(oid)
        elif self.mode == self.MODE_LIVE:
            broker = self._resolve_exec_broker_or_none()
            if not broker:
                return
            rows = self.conn.execute(
                "SELECT order_id, symbol FROM live_orders WHERE status='SUBMITTED'"
            ).fetchall()
            for oid, sym in rows:
                broker.cancel_order(oid, sym)

    # ── Chart rendering ──

    def _refresh_chart(self) -> None:
        candles = getattr(self, "_last_candles", [])
        if not candles:
            return
        self.chart.set_candles(candles)
        df = pd.DataFrame(candles)
        self._apply_indicator("SMA50", self.chk_sma50.isChecked(), df)
        self._apply_indicator("SMA200", self.chk_sma200.isChecked(), df)
        self._apply_indicator("RSI14", self.chk_rsi.isChecked(), df)
        self._apply_indicator("OBV", self.chk_obv.isChecked(), df)
        self._load_fill_markers()

    def _apply_indicator(
        self, key: str, enabled: bool, df: pd.DataFrame
    ) -> None:
        if not enabled:
            self.chart.clear_indicator(key)
            return
        vals = [
            None if pd.isna(v) else float(v)
            for v in self.indicators[key].compute(df).tolist()
        ]
        if self.indicators[key].render_location == "overlay":
            self.chart.set_overlay(key, vals)
        else:
            self.chart.set_subpanel(key, vals)

    def _render_account(self, account, positions) -> None:
        if account is None:
            self.account_card.setText("Equity: -\nCash: -\nDay PnL: -")
            self.position_card.setText("Qty: -\nAvg: -\nCurrent: -\nUPnL: -\nPnL%: -")
            self.position_summary_compact.setText(self.position_card.text())
            return
        self.account_card.setText(
            f"Total Equity: {account.equity:,.2f}\n"
            f"Cash: {account.cash:,.2f}\n"
            f"Buying Power: {account.buying_power:,.2f}"
        )
        pos = next((p for p in positions if p.symbol == self.symbol), None)
        if not pos:
            self.position_card.setText(
                "Qty: 0\nAvg: -\nCurrent: -\nUPnL: 0\nPnL%: 0"
            )
            self.position_summary_compact.setText(self.position_card.text())
            return
        upnl = (pos.market_price - pos.avg_price) * pos.qty
        pct = (upnl / max(1e-9, pos.avg_price * max(1, pos.qty))) * 100
        self.position_card.setText(
            f"Qty: {pos.qty}\nAvg: {pos.avg_price:.2f}\n"
            f"Current: {pos.market_price:.2f}\nUPnL: {upnl:.2f}\nPnL%: {pct:.2f}%"
        )
        self.position_summary_compact.setText(self.position_card.text())

    def _load_tables(self) -> None:
        if self.mode == self.MODE_PAPER:
            orders = self.conn.execute(
                "SELECT order_id, symbol, side, qty, status "
                "FROM paper_orders ORDER BY id DESC LIMIT 100"
            ).fetchall()
            fills = self.conn.execute(
                "SELECT filled_at, 'PAPER', symbol, side, qty, fill_price "
                "FROM paper_fills ORDER BY id DESC LIMIT 200"
            ).fetchall()
        elif self.mode == self.MODE_LIVE:
            orders = self.conn.execute(
                "SELECT order_id, symbol, side, qty, status "
                "FROM live_orders ORDER BY id DESC LIMIT 100"
            ).fetchall()
            fills = self.conn.execute(
                "SELECT filled_at, 'LIVE', symbol, side, fill_qty, fill_price "
                "FROM fills ORDER BY id DESC LIMIT 200"
            ).fetchall()
        else:
            orders, fills = [], []

        self.open_orders.setRowCount(len(orders))
        for r, row_data in enumerate(orders):
            for c, v in enumerate(row_data):
                self.open_orders.setItem(r, c, QTableWidgetItem(str(v)))

        self.fills_table.setRowCount(len(fills))
        for r, row_data in enumerate(fills):
            for c, v in enumerate(row_data):
                self.fills_table.setItem(r, c, QTableWidgetItem(str(v)))

    def _refresh_condition_tables(self) -> None:
        active = self.condition_engine.list_active(self.mode)
        hist = self.condition_engine.list_history(self.mode)

        self.cond_active.setRowCount(len(active))
        for i, c in enumerate(active):
            vals = [
                c.id, c.operator.value, c.trigger_price,
                c.action.value, c.order_type.value, c.qty, c.status.value,
            ]
            for j, v in enumerate(vals):
                self.cond_active.setItem(i, j, QTableWidgetItem(str(v)))

        self.cond_hist.setRowCount(len(hist))
        for i, c in enumerate(hist):
            vals = [
                c.id, c.symbol, c.operator.value, c.trigger_price,
                c.action.value, c.status.value,
                c.broker_order_id or "", c.fail_reason or "",
            ]
            for j, v in enumerate(vals):
                self.cond_hist.setItem(i, j, QTableWidgetItem(str(v)))

    def _load_fill_markers(self) -> None:
        candles = getattr(self, "_last_candles", [])
        if not candles:
            return
        d2i = {str(c["date"]): i for i, c in enumerate(candles)}
        markers: List[FillMarker] = []
        for ts, side, px in self.conn.execute(
            "SELECT filled_at, side, fill_price FROM paper_fills "
            "WHERE symbol=? ORDER BY id DESC LIMIT 200",
            (self.symbol,),
        ).fetchall():
            markers.append(
                FillMarker(
                    index=d2i.get(str(ts)[:10], len(candles) - 1),
                    price=float(px), side=str(side), source="PAPER",
                )
            )
        for ts, px in self.conn.execute(
            "SELECT filled_at, fill_price FROM fills ORDER BY id DESC LIMIT 200"
        ).fetchall():
            markers.append(
                FillMarker(
                    index=d2i.get(str(ts)[:10], len(candles) - 1),
                    price=float(px), side="BUY", source="LIVE",
                )
            )
        self.chart.set_fill_markers(markers)

    # ── Settings / Reset ──

    def _open_settings(self) -> None:
        QMessageBox.information(
            self, "설정",
            "페이퍼 스프레드/슬리피지 및 갱신 간격은 브로커/타이머 설정에서 관리됩니다.",
        )

    def _reset_paper_account(self) -> None:
        if self.mode != self.MODE_PAPER:
            return
        if (
            QMessageBox.question(
                self, "초기화", "모의 계좌를 초기화하고 모든 데이터를 삭제할까요?"
            ) != QMessageBox.Yes
        ):
            return
        self.conn.execute("DELETE FROM paper_orders")
        self.conn.execute("DELETE FROM paper_fills")
        self.conn.execute("DELETE FROM paper_positions")
        self.conn.execute("UPDATE paper_account SET cash=100000 WHERE id=1")
        self.conn.commit()
        self.toast.show_message("모의 계좌가 초기화되었습니다", "success")

    def _fail_safe(self, reason: str) -> None:
        set_emergency_stop(self.conn, True)
        self.auto_ctl.set_enabled(False)
        self.order_panel.set_manual_enabled(False)
        self.order_panel.set_status(reason)
        if self.alert:
            self.alert(f"🚨 {reason}")

    def _reconcile_live_gate(self) -> None:
        if self.mode != self.MODE_LIVE:
            return
        broker = self._resolve_exec_broker_or_none()
        if not broker:
            self._fail_safe("RESUME_DENIED broker unavailable")
            return
        broker_pos = {p.symbol: p.qty for p in broker.get_positions()}
        db_pos = {}
        for sym, qty in self.conn.execute(
            "SELECT symbol, qty FROM positions"
        ).fetchall():
            db_pos[str(sym)] = int(qty)
        if broker_pos != db_pos:
            self._fail_safe(
                f"RESUME_DENIED reconcile mismatch broker={broker_pos} db={db_pos}"
            )

    def _get_exec_prices(self) -> tuple[float, float]:
        b = self._resolve_market_broker()
        return (
            b.get_quote(self.cfg.exec_bull).price,
            b.get_quote(self.cfg.exec_bear).price,
        )
