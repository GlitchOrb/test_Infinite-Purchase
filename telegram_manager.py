"""Telegram notification manager with startup validation and test message."""

from __future__ import annotations

import requests


class TelegramManager:
    def __init__(self, token: str, chat_id: str, enabled: bool) -> None:
        self._token = token.strip()
        self._chat_id = chat_id.strip()
        self.enabled = enabled and bool(self._token and self._chat_id)

    def validate_token(self, timeout_s: float = 8.0) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self._token}/getMe"
        resp = requests.get(url, timeout=timeout_s)
        if resp.status_code != 200:
            raise RuntimeError("Telegram validation failed")
        data = resp.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError("Telegram validation failed")

    def send_test_message(self, timeout_s: float = 8.0) -> None:
        if not self.enabled:
            return
        self.send_message("✅ Telegram notifications connected")

    def send_message(self, text: str, timeout_s: float = 8.0) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        resp = requests.post(url, json={"chat_id": self._chat_id, "text": text}, timeout=timeout_s)
        if resp.status_code != 200:
            raise RuntimeError("Telegram message send failed")
        data = resp.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError("Telegram message send failed")

    @property
    def chat_id(self) -> str:
        return self._chat_id
