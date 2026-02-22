from __future__ import annotations

import pytest

from config import RuntimeConfig
from db import is_emergency_stop, set_emergency_stop
from kill_switch import KillSwitch
from runtime import Runtime


class _FakeKillSwitch:
    def __init__(self):
        self.alerts: list[str] = []

    def send_alert(self, text: str) -> None:
        self.alerts.append(text)


def _runtime_with_memdb() -> Runtime:
    cfg = RuntimeConfig(db_path=":memory:")
    rt = Runtime(cfg)
    return rt


def test_handle_resume_denied_on_reconcile_mismatch(monkeypatch: pytest.MonkeyPatch):
    rt = _runtime_with_memdb()
    rt.kill_sw = _FakeKillSwitch()
    set_emergency_stop(rt.conn, False)

    def _reconcile_fail(is_startup: bool = False) -> None:
        set_emergency_stop(rt.conn, True)

    monkeypatch.setattr(rt, "_reconcile", _reconcile_fail)

    rt._handle_resume()

    assert is_emergency_stop(rt.conn) is True
    assert rt.kill_sw.alerts[-1] == "RESUME DENIED — mismatch present"


def test_handle_resume_clears_emergency_on_clean_reconcile(monkeypatch: pytest.MonkeyPatch):
    rt = _runtime_with_memdb()
    rt.kill_sw = _FakeKillSwitch()
    set_emergency_stop(rt.conn, True)

    def _reconcile_ok(is_startup: bool = False) -> None:
        set_emergency_stop(rt.conn, False)

    monkeypatch.setattr(rt, "_reconcile", _reconcile_ok)

    rt._handle_resume()

    assert is_emergency_stop(rt.conn) is False
    assert rt.kill_sw.alerts[-1] == "RESUME successful after reconcile"


def test_get_total_capital_success(monkeypatch: pytest.MonkeyPatch):
    rt = _runtime_with_memdb()
    monkeypatch.setattr(rt, "_fetch_account_balance", lambda: 123456.78)

    assert rt._get_total_capital() == 123456.78
    assert is_emergency_stop(rt.conn) is False


def test_get_total_capital_failure_sets_emergency(monkeypatch: pytest.MonkeyPatch):
    rt = _runtime_with_memdb()

    def _boom():
        raise RuntimeError("api down")

    monkeypatch.setattr(rt, "_fetch_account_balance", _boom)
    cap = rt._get_total_capital()

    assert cap == 0.0
    assert is_emergency_stop(rt.conn) is True


def test_kill_switch_resume_logging_hides_passcode(caplog: pytest.LogCaptureFixture):
    cfg = RuntimeConfig(telegram_chat_id="42", kill_resume_passcode="SECRET")
    ks = KillSwitch(cfg)

    with caplog.at_level("INFO"):
        ks._handle_update(
            {
                "message": {
                    "text": "/resume SECRET",
                    "chat": {"id": "42"},
                }
            }
        )

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET" not in log_text
    assert "passcode verified" in log_text
