from __future__ import annotations

import requests
from typing import Callable, Optional


class TelegramManager:
    def __init__(self, token: str, chat_id: str = "") -> None:
        self._token = token.strip()
        self.chat_id = chat_id.strip()
        self._offset = 0

    def validate_token(self, timeout_s: float = 8.0) -> dict:
        url = f"https://api.telegram.org/bot{self._token}/getMe"
        resp = requests.get(url, timeout=timeout_s)
        data = resp.json()
        if resp.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError("Invalid Telegram token")
        return data

    def discover_chat_id(self, timeout_s: float = 8.0) -> str:
        updates = self.get_updates(timeout_s=timeout_s)
        for upd in reversed(updates):
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is not None:
                self.chat_id = str(cid)
                return self.chat_id
        raise RuntimeError("No chat_id found. Send /start to the bot and retry")

    def send_test_message(self) -> None:
        self.send_message("✅ Telegram test message: alerts enabled")

    def send_message(self, text: str, timeout_s: float = 8.0) -> None:
        if not self.chat_id:
            raise RuntimeError("chat_id is empty")
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        resp = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=timeout_s)
        data = resp.json()
        if resp.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError("Telegram sendMessage failed")

    def get_updates(self, timeout_s: float = 8.0) -> list[dict]:
        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = {"offset": self._offset, "timeout": 0}
        resp = requests.get(url, params=params, timeout=timeout_s)
        data = resp.json()
        if resp.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError("Telegram getUpdates failed")
        result = data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Telegram getUpdates returned invalid payload")
        if result:
            self._offset = int(result[-1]["update_id"]) + 1
        return result

    def poll_commands(self, on_kill: Callable[[], None], on_resume: Callable[[], None]) -> None:
        updates = self.get_updates()
        for upd in updates:
            msg = upd.get("message") or {}
            text = str(msg.get("text", "")).strip().lower()
            if not text:
                continue
            if self.chat_id:
                cid = str((msg.get("chat") or {}).get("id", ""))
                if cid != self.chat_id:
                    continue
            if text == "/kill":
                on_kill()
                self.send_message("🔴 /kill accepted. emergency_stop=ON")
            elif text.startswith("/resume"):
                on_resume()
