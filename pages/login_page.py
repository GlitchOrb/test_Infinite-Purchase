from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class LoginPage(QWidget):
    login_requested = pyqtSignal(str, str, str, bool, bool, str, str, bool)
    guest_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll)

        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(16)
        holder_layout.addStretch(1)

        card_wrap = QHBoxLayout()
        card_wrap.setContentsMargins(0, 0, 0, 0)
        card_wrap.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("loginCard")
        self.card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.card.setMaximumWidth(460)

        card = QVBoxLayout(self.card)
        card.setContentsMargins(20, 20, 20, 20)
        card.setSpacing(12)

        title = QLabel("Kiwoom REST Login")
        title.setObjectName("cardTitle")
        card.addWidget(title)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_guest = QToolButton(text="Guest")
        self.mode_paper = QToolButton(text="Paper")
        self.mode_live = QToolButton(text="Live")
        for b in [self.mode_guest, self.mode_paper, self.mode_live]:
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setObjectName("modeButton")
            self.mode_group.addButton(b)
            mode_row.addWidget(b)
        self.mode_paper.setChecked(True)
        card.addLayout(mode_row)

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

        self.telegram_toggle = QToolButton(text="Telegram settings (advanced)")
        self.telegram_toggle.setObjectName("neutralButton")
        self.telegram_toggle.setCheckable(True)
        self.telegram_toggle.setChecked(False)
        self.telegram_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        card.addWidget(self.telegram_toggle)

        self.telegram_section = QWidget()
        tg_l = QVBoxLayout(self.telegram_section)
        tg_l.setContentsMargins(0, 0, 0, 0)
        tg_l.setSpacing(8)

        self.telegram_enabled = QCheckBox("Enable Telegram notifications")
        tg_l.addWidget(self.telegram_enabled)

        self.telegram_token = QLineEdit()
        self.telegram_token.setPlaceholderText("Telegram Bot Token")
        self.telegram_token.setEchoMode(QLineEdit.Password)
        tg_l.addWidget(self.telegram_token)

        self.telegram_chat = QLineEdit()
        self.telegram_chat.setPlaceholderText("Telegram Chat ID")
        tg_l.addWidget(self.telegram_chat)

        self.telegram_remember = QCheckBox("Remember Telegram settings")
        tg_l.addWidget(self.telegram_remember)

        help_row = QHBoxLayout()
        help_btn = QPushButton("Telegram Help")
        help_btn.clicked.connect(self._show_help)
        help_row.addWidget(help_btn)
        help_row.addStretch(1)
        tg_l.addLayout(help_row)

        card.addWidget(self.telegram_section)
        self.telegram_section.setVisible(False)
        self.telegram_toggle.toggled.connect(self.telegram_section.setVisible)

        self.status = QLabel("Ready")
        self.status.setObjectName("secondaryLabel")
        card.addWidget(self.status)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("buyButton")
        self.btn_guest = QPushButton("Enter Guest")
        self.btn_guest.setObjectName("neutralButton")
        action_row.addWidget(self.btn_connect)
        action_row.addWidget(self.btn_guest)
        card.addLayout(action_row)

        self.btn_connect.clicked.connect(self._on_submit)
        self.btn_guest.clicked.connect(self.guest_requested.emit)

        self.app_key.returnPressed.connect(self._on_submit)
        self.app_secret.returnPressed.connect(self._on_submit)
        self.account_no.returnPressed.connect(self._on_submit)
        self.telegram_token.returnPressed.connect(self._on_submit)
        self.telegram_chat.returnPressed.connect(self._on_submit)

        card_wrap.addWidget(self.card)
        card_wrap.addStretch(1)
        holder_layout.addLayout(card_wrap)
        holder_layout.addStretch(2)

        scroll.setWidget(holder)

    def set_saved_telegram_loaded(self) -> None:
        self.telegram_enabled.setChecked(True)
        self.telegram_remember.setChecked(True)
        self.telegram_toggle.setChecked(True)
        self.status.setText("Loaded saved Telegram config")

    def _on_submit(self) -> None:
        if self.mode_guest.isChecked():
            self.guest_requested.emit()
            return

        tg_enabled = self.telegram_enabled.isChecked()
        self.login_requested.emit(
            self.app_key.text().strip(),
            self.app_secret.text().strip(),
            self.account_no.text().strip(),
            self.remember.isChecked(),
            tg_enabled,
            self.telegram_token.text().strip(),
            self.telegram_chat.text().strip(),
            self.telegram_remember.isChecked(),
        )

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "Telegram Help",
            "Create bot at @BotFather (/newbot), copy token, then get chat id from your bot conversation.",
        )
