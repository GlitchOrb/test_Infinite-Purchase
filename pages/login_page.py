"""로그인 페이지 — 토스 증권 스타일 싱글 포커스 로그인.

디자인 원칙:
 • 로그인 *만* 담당 — 텔레그램 등 보조 설정은 별도 모달
 • 서버 → 앱키 → 시크릿 → 계좌번호 → 연결하기 순서 흐름
 • 자연어 에러 메시지 (한국어)
 • 비동기 로딩 스피너 + 안내 텍스트
 • Noto Sans KR 기반 타이포그래피
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ko_messages import LOGIN

SERVER_OPTIONS = {
    LOGIN["server_live"]:  "https://api.kiwoom.com",
    LOGIN["server_paper"]: "https://mockapi.kiwoom.com",
}

# ─── 디자인 토큰 ───
_FONT_FAMILY = '"Noto Sans KR", "Pretendard", "Segoe UI", "Malgun Gothic", sans-serif'
_BG = "#F7F8FA"
_CARD_BG = "#FFFFFF"
_PRIMARY = "#3182F6"
_PRIMARY_HOVER = "#1B64DA"
_PRIMARY_PRESSED = "#1957C2"
_PRIMARY_DISABLED = "#B0C8F0"
_NEUTRAL = "#F2F4F6"
_NEUTRAL_HOVER = "#E5E8EB"
_NEUTRAL_PRESSED = "#D1D6DB"
_TEXT = "#191F28"
_TEXT_SECONDARY = "#4E5968"
_TEXT_MUTED = "#8B95A1"
_TEXT_DISABLED = "#B0B8C1"
_BORDER = "#E5E8EB"
_INPUT_BG = "#F9FAFB"
_ERROR = "#E05A5A"
_SUCCESS = "#32A85C"


class LoginPage(QWidget):
    """싱글 포커스 로그인 화면."""

    login_requested = pyqtSignal(str, str, str, bool, str)
    guest_requested = pyqtSignal()
    telegram_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_loading = False
        self._build()

    # ─── UI 빌드 ───
    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addStretch(2)

        # ── 카드 컨테이너 (중앙 정렬) ──
        card_wrap = QHBoxLayout()
        card_wrap.setContentsMargins(24, 0, 24, 0)
        card_wrap.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("loginCard")
        self.card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.card.setMinimumWidth(380)
        self.card.setMaximumWidth(440)
        self.card.setStyleSheet(f"""
            QFrame#loginCard {{
                background: {_CARD_BG};
                border: none;
                border-radius: 24px;
            }}
        """)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(48)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 22))
        self.card.setGraphicsEffect(shadow)

        card = QVBoxLayout(self.card)
        card.setContentsMargins(36, 44, 36, 36)
        card.setSpacing(0)

        # ── 타이틀 ──
        title = QLabel(LOGIN["title"])
        title_font = QFont("Noto Sans KR", 22)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {_TEXT}; background: transparent; margin-bottom: 6px;"
        )
        card.addWidget(title)

        subtitle = QLabel(LOGIN["subtitle"])
        sub_font = QFont("Noto Sans KR", 11)
        subtitle.setFont(sub_font)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(
            f"color: {_TEXT_MUTED}; background: transparent; margin-bottom: 32px;"
        )
        card.addWidget(subtitle)

        # ── 1. 서버 선택 ──
        card.addWidget(self._field_label(LOGIN["server_label"]))
        self.server_combo = QComboBox()
        self.server_combo.setObjectName("loginInput")
        for label in SERVER_OPTIONS:
            self.server_combo.addItem(label)
        self.server_combo.setStyleSheet(self._input_style())
        self.server_combo.setFixedHeight(48)
        card.addWidget(self.server_combo)
        card.addSpacing(16)

        # ── 2. 앱키 ──
        card.addWidget(self._field_label(LOGIN["appkey_label"]))
        self.app_key = QLineEdit()
        self.app_key.setPlaceholderText(LOGIN["appkey_hint"])
        self.app_key.setStyleSheet(self._input_style())
        self.app_key.setFixedHeight(48)
        card.addWidget(self.app_key)
        card.addSpacing(16)

        # ── 3. 시크릿 ──
        card.addWidget(self._field_label(LOGIN["secret_label"]))
        self.app_secret = QLineEdit()
        self.app_secret.setPlaceholderText(LOGIN["secret_hint"])
        self.app_secret.setEchoMode(QLineEdit.Password)
        self.app_secret.setStyleSheet(self._input_style())
        self.app_secret.setFixedHeight(48)
        card.addWidget(self.app_secret)
        card.addSpacing(16)

        # ── 4. 계좌번호 ──
        card.addWidget(self._field_label(LOGIN["account_label"]))
        self.account_no = QLineEdit()
        self.account_no.setPlaceholderText(LOGIN["account_hint"])
        self.account_no.setStyleSheet(self._input_style())
        self.account_no.setFixedHeight(48)
        card.addWidget(self.account_no)

        acc_hint = QLabel(LOGIN["account_desc"])
        acc_hint.setStyleSheet(
            f"color: {_TEXT_DISABLED}; font-size: 11px; background: transparent;"
            " margin-top: 3px; margin-bottom: 10px;"
        )
        card.addWidget(acc_hint)

        # ── 체크박스 ──
        self.remember = QCheckBox(LOGIN["remember"])
        self.remember.setStyleSheet(self._checkbox_style())
        card.addWidget(self.remember)
        card.addSpacing(24)

        # ── 연결하기 (Primary CTA) ──
        self.btn_connect = QPushButton(LOGIN["btn_connect"])
        self.btn_connect.setObjectName("primaryBtn")
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_connect.setFixedHeight(50)
        self.btn_connect.setStyleSheet(self._primary_btn_style())
        card.addWidget(self.btn_connect)
        card.addSpacing(10)

        # ── 보조 버튼 (게스트 + 텔레그램) ──
        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(10)

        self.btn_guest = QPushButton(LOGIN["btn_guest"])
        self.btn_guest.setObjectName("secondaryBtn")
        self.btn_guest.setCursor(Qt.PointingHandCursor)
        self.btn_guest.setFixedHeight(44)
        self.btn_guest.setStyleSheet(self._secondary_btn_style())

        self.btn_telegram = QPushButton(LOGIN["btn_telegram"])
        self.btn_telegram.setObjectName("telegramBtn")
        self.btn_telegram.setCursor(Qt.PointingHandCursor)
        self.btn_telegram.setFixedHeight(44)
        self.btn_telegram.setStyleSheet(self._secondary_btn_style())

        secondary_row.addWidget(self.btn_guest, 1)
        secondary_row.addWidget(self.btn_telegram, 1)
        card.addLayout(secondary_row)
        card.addSpacing(16)

        # ── 상태 메시지 (에러/성공/로딩) ──
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {_ERROR}; font-size: 12px; background: transparent;"
        )
        card.addWidget(self.status)

        # ── 시그널 연결 ──
        self.btn_connect.clicked.connect(self._on_submit)
        self.btn_guest.clicked.connect(self.guest_requested.emit)
        self.btn_telegram.clicked.connect(self.telegram_clicked.emit)

        self.app_key.returnPressed.connect(lambda: self.app_secret.setFocus())
        self.app_secret.returnPressed.connect(lambda: self.account_no.setFocus())
        self.account_no.returnPressed.connect(self._on_submit)

        card_wrap.addWidget(self.card)
        card_wrap.addStretch(1)
        root.addLayout(card_wrap)

        root.addStretch(3)

        # ── 푸터 ──
        footer = QLabel(LOGIN["footer"])
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet(
            f"color: {_TEXT_DISABLED}; font-size: 10px;"
            " background: transparent; margin-bottom: 16px;"
        )
        root.addWidget(footer)

    # ─── 제출 처리 ───
    def _on_submit(self) -> None:
        if self._is_loading:
            return

        app_key = self.app_key.text().strip()
        app_secret = self.app_secret.text().strip()
        account_no = self.account_no.text().strip()
        remember = self.remember.isChecked()

        # 유효성 검사
        if not app_key:
            self._show_error(LOGIN["appkey_required"])
            self.app_key.setFocus()
            return
        if not app_secret:
            self._show_error(LOGIN["secret_required"])
            self.app_secret.setFocus()
            return
        if not account_no:
            self._show_error(LOGIN["account_required"])
            self.account_no.setFocus()
            return

        server_label = self.server_combo.currentText()
        base_url = SERVER_OPTIONS.get(server_label, "")

        # 로딩 상태
        self._set_loading(True)
        self.login_requested.emit(app_key, app_secret, account_no, remember, base_url)

    # ─── 외부 콜백 ───
    def show_error(self, message: str) -> None:
        """MainWindow에서 연결 실패 시 호출."""
        self._set_loading(False)
        self._show_error(message)

    def show_success(self, message: str) -> None:
        """MainWindow에서 연결 성공 시 호출."""
        self._set_loading(False)
        self.status.setText(message)
        self.status.setStyleSheet(
            f"color: {_SUCCESS}; font-size: 12px; background: transparent;"
        )

    # ─── 로딩 상태 관리 ───
    def _set_loading(self, loading: bool) -> None:
        self._is_loading = loading
        self.btn_connect.setEnabled(not loading)
        self.btn_guest.setEnabled(not loading)
        self.btn_telegram.setEnabled(not loading)
        self.app_key.setEnabled(not loading)
        self.app_secret.setEnabled(not loading)
        self.account_no.setEnabled(not loading)
        self.server_combo.setEnabled(not loading)

        if loading:
            self.btn_connect.setText("⏳  " + LOGIN["connecting"])
            self.status.setText(LOGIN["connecting"])
            self.status.setStyleSheet(
                f"color: {_TEXT_MUTED}; font-size: 12px; background: transparent;"
            )
        else:
            self.btn_connect.setText(LOGIN["btn_connect"])

    def _show_error(self, text: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color: {_ERROR}; font-size: 12px; background: transparent;"
        )

    # ─── 헬퍼 ───
    def get_base_url(self) -> str:
        server_label = self.server_combo.currentText()
        return SERVER_OPTIONS.get(server_label, "")

    # ─── 스타일 ───
    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {_TEXT_SECONDARY}; font-size: 13px; font-weight: 600;"
            " background: transparent; margin-bottom: 6px;"
        )
        return lbl

    @staticmethod
    def _input_style() -> str:
        return f"""
            QLineEdit, QComboBox {{
                background: {_INPUT_BG};
                border: 1.5px solid {_BORDER};
                border-radius: 12px;
                padding: 0 16px;
                font-size: 14px;
                color: {_TEXT};
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 1.5px solid {_PRIMARY};
                background: {_CARD_BG};
            }}
            QLineEdit:disabled, QComboBox:disabled {{
                background: {_NEUTRAL};
                color: {_TEXT_DISABLED};
            }}
            QLineEdit::placeholder {{
                color: {_TEXT_DISABLED};
            }}
            QComboBox::drop-down {{
                border: none;
                padding-right: 12px;
            }}
            QComboBox QAbstractItemView {{
                background: {_CARD_BG};
                border: 1px solid {_BORDER};
                border-radius: 8px;
                selection-background-color: #F2F6FC;
                padding: 4px;
            }}
        """

    @staticmethod
    def _checkbox_style() -> str:
        return f"""
            QCheckBox {{
                color: {_TEXT_SECONDARY};
                font-size: 13px;
                background: transparent;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 20px;
                height: 20px;
                border: 1.5px solid #D1D6DB;
                border-radius: 5px;
                background: {_CARD_BG};
            }}
            QCheckBox::indicator:checked {{
                background: {_PRIMARY};
                border-color: {_PRIMARY};
            }}
        """

    @staticmethod
    def _primary_btn_style() -> str:
        return f"""
            QPushButton#primaryBtn {{
                background: {_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 14px;
                font-size: 16px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QPushButton#primaryBtn:hover {{
                background: {_PRIMARY_HOVER};
            }}
            QPushButton#primaryBtn:pressed {{
                background: {_PRIMARY_PRESSED};
            }}
            QPushButton#primaryBtn:disabled {{
                background: {_PRIMARY_DISABLED};
                color: #FFFFFF;
            }}
        """

    @staticmethod
    def _secondary_btn_style() -> str:
        return f"""
            QPushButton {{
                background: {_NEUTRAL};
                color: {_TEXT_SECONDARY};
                border: none;
                border-radius: 12px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: {_NEUTRAL_HOVER};
            }}
            QPushButton:pressed {{
                background: {_NEUTRAL_PRESSED};
            }}
            QPushButton:disabled {{
                color: {_TEXT_DISABLED};
            }}
        """
