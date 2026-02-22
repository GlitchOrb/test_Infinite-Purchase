from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from storage.secure_store_windows import SecureStoreWindows
from telegram.telegram_manager import TelegramManager


@dataclass(frozen=True)
class TelegramConfigResult:
    enabled: bool
    token: str
    chat_id: str
    remember: bool


class TelegramConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Telegram Alerts")
        self._store = SecureStoreWindows("AlphaPredator.Telegram")
        self._result: Optional[TelegramConfigResult] = None
        self._build()
        self._load_saved()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.chk_enabled = QCheckBox("Enable Telegram alerts")
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Bot Token")
        self.chat_id = QLineEdit()
        self.chat_id.setPlaceholderText("Chat ID (optional)")
        self.remember = QCheckBox("Remember settings")

        help_row = QHBoxLayout()
        help_btn = QPushButton("?")
        help_btn.setFixedWidth(28)
        help_btn.clicked.connect(self._show_help)
        help_row.addWidget(help_btn)
        help_row.addWidget(QLabel("Create bot via @BotFather, then send /start to bot."))

        form.addRow(self.chk_enabled)
        form.addRow("Token", self.token)
        form.addRow("Chat ID", self.chat_id)
        form.addRow(self.remember)
        root.addLayout(help_row)
        root.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "Telegram Help",
            "1) @BotFather -> /newbot\n"
            "2) Copy bot token\n"
            "3) Send /start to your bot\n"
            "4) Leave Chat ID empty to auto-discover via getUpdates",
        )

    def _load_saved(self) -> None:
        saved = self._store.load_json()
        if not saved:
            return
        self.chk_enabled.setChecked(bool(saved.get("enabled", False)))
        self.token.setText(str(saved.get("token", "")))
        self.chat_id.setText(str(saved.get("chat_id", "")))
        self.remember.setChecked(True)

    def _on_accept(self) -> None:
        enabled = self.chk_enabled.isChecked()
        token = self.token.text().strip()
        chat_id = self.chat_id.text().strip()
        remember = self.remember.isChecked()

        if enabled:
            if not token:
                QMessageBox.warning(self, "Validation", "Telegram token is required")
                return
            try:
                mgr = TelegramManager(token, chat_id)
                mgr.validate_token()
                if not chat_id:
                    discovered = mgr.discover_chat_id()
                    ok = QMessageBox.question(
                        self,
                        "Confirm Chat ID",
                        f"Discovered chat_id={discovered}. Use this chat?",
                    )
                    if ok != QMessageBox.Yes:
                        return
                    chat_id = discovered
                mgr = TelegramManager(token, chat_id)
                mgr.send_test_message()
            except Exception as exc:
                QMessageBox.critical(self, "Telegram Error", str(exc))
                return

        if remember and enabled:
            if self._store.supported():
                self._store.save_json({"enabled": True, "token": token, "chat_id": chat_id})
            else:
                QMessageBox.information(self, "Notice", "Remember is available on Windows only")
        elif remember and not enabled:
            self._store.delete()

        self._result = TelegramConfigResult(enabled=enabled, token=token, chat_id=chat_id, remember=remember)
        self.accept()

    def result_config(self) -> Optional[TelegramConfigResult]:
        return self._result
