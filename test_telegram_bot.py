from __future__ import annotations

import os
import sqlite3

from db import get_alert, init_db
from telegram_bot import TelegramBotConfig, TelegramControlBot, mdv2_escape


class FakeRuntime:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        self.cfg = type("Cfg", (), {"telegram_chat_id": "100"})
        self.resume_ok = True
        self.killed = False

    def handle_kill_command(self):
        self.killed = True

    def handle_resume(self, passcode: str):
        if passcode != "ok":
            return False, "Resume denied — incorrect passcode."
        return (True, "Resume accepted") if self.resume_ok else (False, "Resume denied — reconcile mismatch.")

    def _get_total_capital(self):
        return 12345.6


def _bot(rt: FakeRuntime) -> TelegramControlBot:
    cfg = TelegramBotConfig(token="x", admin_user_ids={1}, allowed_chat_ids={"100"}, webhook_secret="s")
    b = TelegramControlBot(rt, cfg)
    b.sent = []

    def _capture(method: str, payload: dict):
        b.sent.append((method, payload))
        return {"ok": True, "result": []}

    b._api = _capture  # type: ignore[assignment]
    return b


def test_env_aliases_supported(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_ADMIN_IDS", "1,2")
    monkeypatch.setenv("TG_CHAT_ID", "-1001")
    cfg = TelegramBotConfig.from_env()
    assert cfg.token == "t"
    assert cfg.admin_user_ids == {1, 2}
    assert cfg.allowed_chat_ids == {"-1001"}


def test_unauthorized_user_rejected():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 999}, "text": "/kill"})
    assert any("Unauthorized" in m[1].get("text", "") for m in b.sent)
    assert rt.killed is False


def test_admin_kill_resume_sequence():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/kill"})
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/resume ok"})
    assert rt.killed is True
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "Kill switch activated" in out
    assert "Resume accepted" in out


def test_inline_actions_toggle_summary():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_callback({
        "data": "toggle_summary",
        "from": {"id": 1},
        "message": {"chat": {"id": "100"}, "message_id": 22},
    })
    assert get_alert(rt.conn, "daily_summary_enabled") in {"true", "false"}


def test_process_update_routes_callback():
    rt = FakeRuntime()
    b = _bot(rt)
    b.process_update({
        "callback_query": {
            "data": "kill_confirm",
            "from": {"id": 1},
            "message": {"chat": {"id": "100"}, "message_id": 5},
        }
    })
    assert rt.killed is True


def test_markdown_formatting_escape():
    txt = "*Hello!* This is a test message."
    escaped = mdv2_escape(txt)
    assert escaped == "\\*Hello\\!\\* This is a test message\\."
