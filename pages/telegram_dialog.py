"""텔레그램 설정 다이얼로그 — 로그인과 분리된 모달 팝업.

기능:
 • 봇 토큰 / 채팅 ID 입력
 • 텔레그램 알림 사용 체크박스
 • 설정 저장 체크박스
 • 테스트 전송 (비동기 UX — 로딩 표시)
 • 저장 / 닫기 버튼
 • 토큰 유효성 검사
 • 상태 표시 (성공/실패 한국어 메시지)
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ko_messages import TELEGRAM
from secrets_store_windows import (
    delete_telegram_credentials,
    is_remember_supported,
    load_telegram_credentials,
    save_telegram_credentials,
)
from telegram_manager import TelegramManager

# ─── 디자인 토큰 ───
_BG = "#F7F8FA"
_CARD_BG = "#FFFFFF"
_PRIMARY = "#3182F6"
_PRIMARY_HOVER = "#1B64DA"
_PRIMARY_PRESSED = "#1957C2"
_PRIMARY_DISABLED = "#B0C8F0"
_NEUTRAL = "#F2F4F6"
_NEUTRAL_HOVER = "#E5E8EB"
_TEXT = "#191F28"
_TEXT_SECONDARY = "#4E5968"
_TEXT_MUTED = "#8B95A1"
_TEXT_DISABLED = "#B0B8C1"
_BORDER = "#E5E8EB"
_INPUT_BG = "#F9FAFB"
_ERROR = "#E05A5A"
_SUCCESS = "#32A85C"


# ─── 비동기 작업용 워커 ───
class _TelegramWorker(QThread):
    """별도 스레드에서 텔레그램 API 호출."""
    finished = pyqtSignal(bool, str)

    def __init__(self, token: str, chat_id: str, send_test: bool = False) -> None:
        super().__init__()
        self._token = token
        self._chat_id = chat_id
        self._send_test = send_test

    def run(self) -> None:
        try:
            mgr = TelegramManager(self._token, self._chat_id, enabled=True)
            mgr.validate_token()
            if self._send_test and self._chat_id:
                mgr.send_message(TELEGRAM["test_msg_body"])
            self.finished.emit(True, "")
        except Exception as exc:
            self.finished.emit(False, str(exc))


class TelegramDialog(QDialog):
    """텔레그램 알림 설정 다이얼로그."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TELEGRAM["dialog_title"])
        self.setFixedSize(460, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._tg_enabled = False
        self._tg_token = ""
        self._tg_chat_id = ""
        self._worker: _TelegramWorker | None = None

        self._build()
        self._load_saved()

    # ─── UI 빌드 ───
    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(0)

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {_CARD_BG};
                border: none;
                border-radius: 20px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(36)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 18))
        card.setGraphicsEffect(shadow)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(28, 36, 28, 28)
        inner.setSpacing(0)

        # 타이틀
        title = QLabel(TELEGRAM["dialog_title"])
        title_font = QFont("Noto Sans KR", 18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(
            f"color: {_TEXT}; background: transparent; margin-bottom: 6px;"
        )
        inner.addWidget(title)

        desc = QLabel(TELEGRAM["desc"])
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 12px;"
            " background: transparent; margin-bottom: 28px;"
        )
        inner.addWidget(desc)

        # 봇 토큰
        inner.addWidget(self._field_label(TELEGRAM["token_label"]))
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText(TELEGRAM["token_hint"])
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setStyleSheet(self._input_style())
        self.token_input.setFixedHeight(48)
        inner.addWidget(self.token_input)
        inner.addSpacing(16)

        # 채팅 ID
        inner.addWidget(self._field_label(TELEGRAM["chat_label"]))
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText(TELEGRAM["chat_hint"])
        self.chat_input.setStyleSheet(self._input_style())
        self.chat_input.setFixedHeight(48)
        inner.addWidget(self.chat_input)
        inner.addSpacing(18)

        # 체크박스
        self.chk_enabled = QCheckBox(TELEGRAM["chk_enabled"])
        self.chk_enabled.setStyleSheet(self._checkbox_style())
        inner.addWidget(self.chk_enabled)
        inner.addSpacing(8)

        self.chk_remember = QCheckBox(TELEGRAM["chk_remember"])
        self.chk_remember.setStyleSheet(self._checkbox_style())
        inner.addWidget(self.chk_remember)
        inner.addSpacing(22)

        # 테스트 전송 버튼
        self.btn_test = QPushButton(TELEGRAM["btn_test"])
        self.btn_test.setObjectName("outlineBtn")
        self.btn_test.setCursor(Qt.PointingHandCursor)
        self.btn_test.setFixedHeight(44)
        self.btn_test.setStyleSheet(self._outline_btn_style())
        self.btn_test.clicked.connect(self._on_test)
        inner.addWidget(self.btn_test)
        inner.addSpacing(12)

        # 저장 / 닫기
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_save = QPushButton(TELEGRAM["btn_save"])
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setFixedHeight(48)
        self.btn_save.setStyleSheet(self._primary_btn_style())
        self.btn_save.clicked.connect(self._on_save)

        self.btn_close = QPushButton(TELEGRAM["btn_close"])
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setFixedHeight(48)
        self.btn_close.setStyleSheet(self._secondary_btn_style())
        self.btn_close.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_save, 2)
        btn_row.addWidget(self.btn_close, 1)
        inner.addLayout(btn_row)

        # 상태 라벨
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 11px;"
            " background: transparent; margin-top: 12px;"
        )
        inner.addWidget(self.status)

        root.addWidget(card)

    # ─── 저장된 설정 복원 ───
    def _load_saved(self) -> None:
        saved = load_telegram_credentials()
        if not saved:
            return
        token, chat_id = saved
        self.token_input.setText(token)
        self.chat_input.setText(chat_id)
        self.chk_enabled.setChecked(True)
        self.chk_remember.setChecked(True)
        self._set_status(TELEGRAM["loaded"], "info")

    # ─── 테스트 전송 ───
    def _on_test(self) -> None:
        token = self.token_input.text().strip()
        chat_id = self.chat_input.text().strip()

        if not token:
            self._set_status(TELEGRAM["token_required"], "error")
            self.token_input.setFocus()
            return

        self._set_loading(True, testing=True)
        self._set_status(TELEGRAM["testing"], "info")

        self._worker = _TelegramWorker(token, chat_id, send_test=True)
        self._worker.finished.connect(self._on_test_finished)
        self._worker.start()

    def _on_test_finished(self, ok: bool, error: str) -> None:
        self._set_loading(False)
        if ok:
            self._set_status(TELEGRAM["test_success"], "success")
        else:
            self._set_status(TELEGRAM["test_fail"].format(error=error), "error")

    # ─── 저장 ───
    def _on_save(self) -> None:
        token = self.token_input.text().strip()
        chat_id = self.chat_input.text().strip()
        enabled = self.chk_enabled.isChecked()
        remember = self.chk_remember.isChecked()

        if enabled and not token:
            self._set_status(TELEGRAM["token_required_for_enable"], "error")
            self.token_input.setFocus()
            return

        if enabled:
            # 저장 전 토큰 검증
            self._set_loading(True)
            self._set_status(TELEGRAM["saving"], "info")
            self._worker = _TelegramWorker(token, chat_id, send_test=False)
            self._worker.finished.connect(
                lambda ok, err: self._on_save_validated(ok, err, token, chat_id, remember)
            )
            self._worker.start()
        else:
            delete_telegram_credentials()
            self._tg_enabled = False
            self._tg_token = ""
            self._tg_chat_id = ""
            self.accept()

    def _on_save_validated(
        self, ok: bool, error: str, token: str, chat_id: str, remember: bool
    ) -> None:
        self._set_loading(False)
        if not ok:
            self._set_status(TELEGRAM["validate_fail"].format(error=error), "error")
            return

        if remember and is_remember_supported():
            save_telegram_credentials(token, chat_id)

        self._tg_enabled = True
        self._tg_token = token
        self._tg_chat_id = chat_id

        self._set_status(TELEGRAM["save_success"], "success")
        # 짧은 딜레이 후 자동 닫기
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(600, self.accept)

    # ─── 상태 표시 ───
    def _set_status(self, text: str, kind: str = "info") -> None:
        color_map = {
            "error":   _ERROR,
            "success": _SUCCESS,
            "info":    _TEXT_MUTED,
        }
        color = color_map.get(kind, _TEXT_MUTED)
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color: {color}; font-size: 11px;"
            " background: transparent; margin-top: 12px;"
        )

    def _set_loading(self, loading: bool, testing: bool = False) -> None:
        self.btn_test.setEnabled(not loading)
        self.btn_save.setEnabled(not loading)
        self.btn_close.setEnabled(not loading)
        self.token_input.setEnabled(not loading)
        self.chat_input.setEnabled(not loading)

        if loading and testing:
            self.btn_test.setText("⏳  " + TELEGRAM["testing"])
        else:
            self.btn_test.setText(TELEGRAM["btn_test"])

    # ─── 결과 프로퍼티 ───
    @property
    def result_enabled(self) -> bool:
        return self._tg_enabled

    @property
    def result_token(self) -> str:
        return self._tg_token

    @property
    def result_chat_id(self) -> str:
        return self._tg_chat_id

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
            QLineEdit {{
                background: {_INPUT_BG};
                border: 1.5px solid {_BORDER};
                border-radius: 12px;
                padding: 0 16px;
                font-size: 14px;
                color: {_TEXT};
            }}
            QLineEdit:focus {{
                border: 1.5px solid {_PRIMARY};
                background: {_CARD_BG};
            }}
            QLineEdit:disabled {{
                background: {_NEUTRAL};
                color: {_TEXT_DISABLED};
            }}
            QLineEdit::placeholder {{
                color: {_TEXT_DISABLED};
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
            QPushButton {{
                background: {_PRIMARY};
                color: #FFFFFF;
                border: none;
                border-radius: 14px;
                font-size: 15px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {_PRIMARY_HOVER};
            }}
            QPushButton:pressed {{
                background: {_PRIMARY_PRESSED};
            }}
            QPushButton:disabled {{
                background: {_PRIMARY_DISABLED};
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
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {_NEUTRAL_HOVER};
            }}
            QPushButton:disabled {{
                color: {_TEXT_DISABLED};
            }}
        """

    @staticmethod
    def _outline_btn_style() -> str:
        return f"""
            QPushButton {{
                background: {_CARD_BG};
                color: {_PRIMARY};
                border: 1.5px solid {_PRIMARY};
                border-radius: 12px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #F2F6FC;
            }}
            QPushButton:disabled {{
                color: {_PRIMARY_DISABLED};
                border-color: {_PRIMARY_DISABLED};
            }}
        """
