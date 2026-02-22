from __future__ import annotations

import logging
import sqlite3

from db import get_alert, init_db
from telegram_bot import TelegramBotConfig, TelegramControlBot, mdv2_escape


class FakeKis:
    def __init__(self):
        self.fail = False

    def fetch_usdkrw(self):
        if self.fail:
            raise RuntimeError("fx fail")
        return 1350.0


class FakeRuntime:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        self.cfg = type("Cfg", (), {"telegram_chat_id": "100"})
        self.resume_ok = True
        self.killed = False
        self.kis = FakeKis()

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


def test_unauthorized_user_rejected_korean():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 999}, "text": "/kill"})
    assert any("권한 없음" in m[1].get("text", "") for m in b.sent)
    assert rt.killed is False


def test_admin_kill_resume_sequence_korean_messages():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/kill"})
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/resume ok"})
    assert rt.killed is True
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "긴급 정지 모드" in out
    assert "시스템이 정상적으로 재개" in out


def test_resume_fail_message_korean():
    rt = FakeRuntime()
    rt.resume_ok = False
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/resume ok"})
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "재개 실패" in out


def test_status_message_contains_korean():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/status"})
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "시스템 상태 보고서" in out
    assert "레짐 상태" in out
    assert "예수금" in out


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


def test_passcode_not_logged(caplog):
    rt = FakeRuntime()
    b = _bot(rt)
    with caplog.at_level(logging.INFO):
        b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/resume my-secret-pass"})
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "my-secret-pass" not in logs


def test_callback_validation_rejects_unknown():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_callback({
        "data": "__bad_payload__",
        "from": {"id": 1},
        "message": {"chat": {"id": "100"}, "message_id": 33},
    })
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "잘못된 요청" in out


def test_message_flood_rate_limit():
    rt = FakeRuntime()
    b = _bot(rt)
    for _ in range(20):
        b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/help"})
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "요청이 너무 많습니다" in out


def test_fx_fallback_uses_last_known_value():
    rt = FakeRuntime()
    b = _bot(rt)
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/balance"})
    rt.kis.fail = True
    b._handle_command({"chat": {"id": "100"}, "from": {"id": 1}, "text": "/balance"})
    out = "\n".join(x[1].get("text", "") for x in b.sent)
    assert "환율 조회 실패" in out
