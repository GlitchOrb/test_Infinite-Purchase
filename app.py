from __future__ import annotations

from pathlib import Path

import json
import logging
import os
import sys

from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget

from auth_manager import AuthManager
from config import RuntimeConfig
from db import init_db, open_db
from db_migrations import run_migrations
from pages.login_page import LoginPage
from pages.trading_screen import TradingScreen
from secrets_store_windows import (
    delete_telegram_credentials,
    is_remember_supported,
    load_telegram_credentials,
    save_telegram_credentials,
)
from telegram_manager import TelegramManager

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Trading Platform")
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

        self.login_page = LoginPage()
        self.login_page.login_requested.connect(self.begin_live)
        self.login_page.guest_requested.connect(self.begin_guest)
        self.stack.addWidget(self.login_page)

        self.trading_screen = TradingScreen(
            conn=self.conn,
            auth_manager=self.auth,
            cfg=self.cfg,
            telegram_alert=self._alert,
        )
        self.stack.addWidget(self.trading_screen)

        self._restore_saved_login()
        self._restore_saved_telegram()
        self._set_login_tab_order()

        self.stack.setCurrentWidget(self.login_page)

    def _set_login_tab_order(self) -> None:
        self.setTabOrder(self.login_page.mode_guest, self.login_page.mode_paper)
        self.setTabOrder(self.login_page.mode_paper, self.login_page.mode_live)
        self.setTabOrder(self.login_page.mode_live, self.login_page.app_key)
        self.setTabOrder(self.login_page.app_key, self.login_page.app_secret)
        self.setTabOrder(self.login_page.app_secret, self.login_page.account_no)
        self.setTabOrder(self.login_page.account_no, self.login_page.remember)
        self.setTabOrder(self.login_page.remember, self.login_page.telegram_toggle)
        self.setTabOrder(self.login_page.telegram_toggle, self.login_page.telegram_enabled)
        self.setTabOrder(self.login_page.telegram_enabled, self.login_page.telegram_token)
        self.setTabOrder(self.login_page.telegram_token, self.login_page.telegram_chat)
        self.setTabOrder(self.login_page.telegram_chat, self.login_page.telegram_remember)
        self.setTabOrder(self.login_page.telegram_remember, self.login_page.btn_connect)
        self.setTabOrder(self.login_page.btn_connect, self.login_page.btn_guest)

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
        self.login_page.telegram_token.setText(token)
        self.login_page.telegram_chat.setText(chat_id)
        self.login_page.set_saved_telegram_loaded()

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
            log.warning("Invalid config_endpoints.json; ignoring", exc_info=True)

    def begin_guest(self) -> None:
        self.auth.start_guest_mode()
        self.telegram_mgr = None
        delete_telegram_credentials()
        self.stack.setCurrentWidget(self.trading_screen)
        self.trading_screen.mode_box.setCurrentText("Guest")

    def begin_live(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        remember_login: bool,
        tg_enabled: bool,
        tg_token: str,
        tg_chat: str,
        tg_remember: bool,
    ) -> None:
        try:
            self.auth.start_live_mode(app_key, app_secret, account_no, remember_login)
            object.__setattr__(self.cfg, "kiwoom_account", account_no)

            if tg_enabled:
                tg = TelegramManager(tg_token, tg_chat, enabled=True)
                tg.validate_token()
                tg.send_test_message()
                self.telegram_mgr = tg
                if tg_remember and is_remember_supported():
                    save_telegram_credentials(tg_token, tg_chat)
                else:
                    delete_telegram_credentials()
            else:
                self.telegram_mgr = None
                delete_telegram_credentials()

            self.stack.setCurrentWidget(self.trading_screen)
            self.trading_screen.mode_box.setCurrentText("Paper")
        except Exception as exc:
            self.login_page.status.setText(f"Login failed: {exc}")

    def _alert(self, text: str) -> None:
        if self.telegram_mgr:
            try:
                self.telegram_mgr.send_message(text)
            except Exception:
                pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    qss_path = Path(__file__).resolve().parent / "styles" / "theme.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
