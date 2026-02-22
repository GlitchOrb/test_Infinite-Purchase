"""메인 애플리케이션 — 키움 REST 트레이딩 플랫폼.

스토리보드 흐름:
  1) LoginPage — 로그인 전용 (싱글 포커스)
  2) TelegramDialog — 텔레그램 설정 (별도 모달)
  3) TradingScreen — 매매 대시보드
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget

from auth_manager import AuthManager
from config import RuntimeConfig
from db import init_db, open_db
from db_migrations import run_migrations
from ko_messages import LOGIN
from pages.login_page import LoginPage
from pages.telegram_dialog import TelegramDialog
from pages.trading_screen import TradingScreen
from secrets_store_windows import (
    delete_telegram_credentials,
    load_telegram_credentials,
)
from telegram_manager import TelegramManager

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("키움 트레이딩 플랫폼")
        self.resize(1600, 950)

        self._load_endpoint_mapping_from_file_if_needed()

        self.cfg = RuntimeConfig(
            kiwoom_account=os.environ.get("KIWOOM_ACCOUNT", ""),
            telegram_token="",
            telegram_chat_id="",
        )
        self.conn = open_db(self.cfg.db_path)
        init_db(self.conn)
        run_migrations(self.conn)

        self.auth = AuthManager()
        self.telegram_mgr: TelegramManager | None = None

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # ── 로그인 페이지 ──
        self.login_page = LoginPage()
        self.login_page.login_requested.connect(self.begin_live)
        self.login_page.guest_requested.connect(self.begin_guest)
        self.login_page.telegram_clicked.connect(self._open_telegram_dialog)
        self.stack.addWidget(self.login_page)

        # ── 트레이딩 화면 ──
        self.trading_screen = TradingScreen(
            conn=self.conn,
            auth_manager=self.auth,
            cfg=self.cfg,
            telegram_alert=self._alert,
        )
        self.stack.addWidget(self.trading_screen)

        # ── 저장된 로그인/텔레그램 복원 ──
        self._restore_saved_login()
        self._restore_saved_telegram()

        self.stack.setCurrentWidget(self.login_page)

    def _restore_saved_login(self) -> None:
        saved = self.auth.try_restore_saved_login()
        if not saved:
            return
        app_key, app_secret, account_no = saved
        self.login_page.app_key.setText(app_key)
        self.login_page.app_secret.setText(app_secret)
        self.login_page.account_no.setText(account_no)
        self.login_page.remember.setChecked(True)

    def _restore_saved_telegram(self) -> None:
        saved = load_telegram_credentials()
        if not saved:
            return
        token, chat_id = saved
        try:
            tg = TelegramManager(token, chat_id, enabled=True)
            self.telegram_mgr = tg
        except Exception:
            pass

    def _load_endpoint_mapping_from_file_if_needed(self) -> None:
        if os.environ.get("KIWOOM_REST_ENDPOINTS_JSON", "").strip():
            return
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_endpoints.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                os.environ["KIWOOM_REST_ENDPOINTS_JSON"] = json.dumps(data)
        except Exception:
            log.warning("config_endpoints.json 파일을 읽을 수 없습니다", exc_info=True)

    # ─── 로그인 흐름 ───
    def begin_guest(self) -> None:
        self.auth.start_guest_mode()
        self.stack.setCurrentWidget(self.trading_screen)
        self.trading_screen.mode_box.setCurrentText("Guest")

    def begin_live(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        remember_login: bool,
        base_url: str,
    ) -> None:
        try:
            self.auth.start_live_mode(app_key, app_secret, account_no, remember_login, base_url)
            normalized_account = "".join(ch for ch in account_no if ch.isdigit())
            object.__setattr__(self.cfg, "kiwoom_account", normalized_account)

            self.login_page.show_success(LOGIN["connect_success"])
            self.stack.setCurrentWidget(self.trading_screen)
            self.trading_screen.mode_box.setCurrentText("Paper")
        except Exception as exc:
            self.login_page.show_error(
                LOGIN["connect_fail"].format(error=str(exc))
            )

    # ─── 텔레그램 다이얼로그 ───
    def _open_telegram_dialog(self) -> None:
        dialog = TelegramDialog(self)
        if dialog.exec_() == TelegramDialog.Accepted:
            if dialog.result_enabled:
                tg = TelegramManager(
                    dialog.result_token,
                    dialog.result_chat_id,
                    enabled=True,
                )
                self.telegram_mgr = tg
            else:
                self.telegram_mgr = None
                delete_telegram_credentials()

    def _alert(self, text: str) -> None:
        if self.telegram_mgr:
            try:
                self.telegram_mgr.send_message(text)
            except Exception:
                pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── Noto Sans KR 폰트 로드 (시스템에 설치되어 있으면 자동 적용) ──
    QFontDatabase.addApplicationFont("NotoSansKR-Regular.otf")
    QFontDatabase.addApplicationFont("NotoSansKR-Bold.otf")

    # ── 전역 스타일시트 ──
    qss_path = Path(__file__).resolve().parent / "styles" / "theme.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
