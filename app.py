from __future__ import annotations

import logging
import os
import sys

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from auth_manager import AuthManager
from config import RuntimeConfig
from db import init_db, open_db
from db_migrations import run_migrations
from pages.trading_screen import TradingScreen
from telegram_manager import TelegramManager
from ui_theme import GLOBAL_STYLE

log = logging.getLogger(__name__)


class LoginPage(QWidget):
    login_success = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignCenter)

        card = QVBoxLayout()
        root.addLayout(card)

        card.addWidget(QLabel("Kiwoom REST Login"))

        self.app_key = QLineEdit()
        self.app_key.setPlaceholderText("App Key")
        card.addWidget(self.app_key)

        self.app_secret = QLineEdit()
        self.app_secret.setPlaceholderText("App Secret")
        self.app_secret.setEchoMode(QLineEdit.Password)
        card.addWidget(self.app_secret)

        self.account_no = QLineEdit()
        self.account_no.setPlaceholderText("Account Number")
        card.addWidget(self.account_no)

        self.remember = QCheckBox("Remember login")
        card.addWidget(self.remember)

        self.telegram_enabled = QCheckBox("Enable Telegram notifications")
        card.addWidget(self.telegram_enabled)

        self.telegram_token = QLineEdit()
        self.telegram_token.setPlaceholderText("Telegram Bot Token")
        self.telegram_token.setEchoMode(QLineEdit.Password)
        card.addWidget(self.telegram_token)

        self.telegram_chat = QLineEdit()
        self.telegram_chat.setPlaceholderText("Telegram Chat ID")
        card.addWidget(self.telegram_chat)

        self.telegram_remember = QCheckBox("Remember settings")
        card.addWidget(self.telegram_remember)

        help_row = QHBoxLayout()
        help_btn = QPushButton("?")
        help_btn.setFixedWidth(28)
        help_btn.clicked.connect(self._show_help)
        help_row.addWidget(help_btn)
        help_row.addStretch()
        card.addLayout(help_row)

        self.status = QLabel("Ready")
        card.addWidget(self.status)

        btn_live = QPushButton("Connect Live/Paper")
        btn_demo = QPushButton("Demo Mode (Guest)")
        btn_live.clicked.connect(self._do_live)
        btn_demo.clicked.connect(self._do_demo)
        card.addWidget(btn_live)
        card.addWidget(btn_demo)

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "Telegram Help",
            "Create bot at @BotFather (/newbot), copy token, then get chat id from your bot conversation.",
        )

    def _do_live(self) -> None:
        self.parent().parent().begin_live(  # type: ignore[attr-defined]
            self.app_key.text().strip(),
            self.app_secret.text().strip(),
            self.account_no.text().strip(),
            self.remember.isChecked(),
            self.telegram_enabled.isChecked(),
            self.telegram_token.text().strip(),
            self.telegram_chat.text().strip(),
            self.telegram_remember.isChecked(),
        )

    def _do_demo(self) -> None:
        self.parent().parent().begin_guest()  # type: ignore[attr-defined]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Trading Platform")
        self.resize(1600, 950)

        self.cfg = RuntimeConfig(
            kiwoom_account=os.environ.get("KIWOOM_ACCOUNT", ""),
            telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )
        self.conn = open_db(self.cfg.db_path)
        init_db(self.conn)
        run_migrations(self.conn)

        self.auth = AuthManager()
        self.telegram_mgr: TelegramManager | None = None

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login_page = LoginPage()
        self.stack.addWidget(self.login_page)

        self.trading_screen = TradingScreen(
            conn=self.conn,
            auth_manager=self.auth,
            cfg=self.cfg,
            telegram_alert=self._alert,
        )
        self.stack.addWidget(self.trading_screen)

        self.stack.setCurrentWidget(self.login_page)

    def begin_guest(self) -> None:
        self.auth.start_guest_mode()
        self.telegram_mgr = None
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
                object.__setattr__(self.cfg, "telegram_token", tg_token)
                object.__setattr__(self.cfg, "telegram_chat_id", tg_chat)
            else:
                self.telegram_mgr = None
                object.__setattr__(self.cfg, "telegram_token", "")
                object.__setattr__(self.cfg, "telegram_chat_id", "")

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
    app.setStyleSheet(GLOBAL_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
