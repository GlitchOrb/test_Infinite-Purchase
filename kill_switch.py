"""
kill_switch.py
==============
Telegram-based kill switch — runs in a background thread, polls for
/kill and /resume commands, persists emergency_stop to SQLite.

No external AI API.  Uses only the Telegram Bot HTTP API via ``requests``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

from config import RuntimeConfig

log = logging.getLogger(__name__)


class KillSwitch:
    """Telegram-polling kill switch.

    Commands recognised
    -------------------
    ``/kill``
        Sets ``emergency_stop=True``, cancels all open orders, blocks
        new order submission.
    ``/resume <passcode>``
        Clears ``emergency_stop`` if the passcode matches and the daily
        reconcile is clean.

    The switch persists ``emergency_stop`` in SQLite via callbacks so
    it survives process restarts.

    Parameters
    ----------
    cfg : RuntimeConfig
    on_kill : callable
        Invoked (from the polling thread) when /kill is received.
    on_resume : callable
        Invoked when /resume succeeds.
    """

    def __init__(
        self,
        cfg: RuntimeConfig,
        on_kill: Optional[callable] = None,
        on_resume: Optional[callable] = None,
    ) -> None:
        self.cfg = cfg
        self._on_kill = on_kill
        self._on_resume = on_resume
        self._offset: int = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    #  Public
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if not self.cfg.telegram_token:
            log.warning("Telegram token not set — kill switch disabled")
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info("Kill switch polling started (chat=%s)", self.cfg.telegram_chat_id)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def send_alert(self, text: str) -> None:
        """Send an outbound Telegram message (best-effort)."""
        if not self.cfg.telegram_token:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                json={"chat_id": self.cfg.telegram_chat_id, "text": text},
                timeout=10,
            )
        except Exception:
            log.exception("Failed to send Telegram alert")

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = self._get_updates()
                for upd in updates:
                    self._handle_update(upd)
            except Exception:
                log.exception("Kill switch poll error")
            time.sleep(self.cfg.telegram_poll_interval_s)

    def _get_updates(self) -> list:
        resp = requests.get(
            f"https://api.telegram.org/bot{self.cfg.telegram_token}/getUpdates",
            params={"offset": self._offset, "timeout": 5},
            timeout=15,
        )
        data = resp.json()
        results = data.get("result", [])
        if results:
            self._offset = results[-1]["update_id"] + 1
        return results

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if chat_id != self.cfg.telegram_chat_id:
            return

        if text.lower() == "/kill":
            log.critical("KILL SWITCH activated via Telegram")
            self.send_alert("🔴 KILL SWITCH ACTIVATED — all orders being cancelled")
            if self._on_kill:
                self._on_kill()

        elif text.lower().startswith("/resume"):
            parts = text.split(maxsplit=1)
            passcode = parts[1].strip() if len(parts) > 1 else ""
            if passcode == self.cfg.kill_resume_passcode:
                log.info("RESUME command accepted via Telegram")
                self.send_alert("🟢 RESUME accepted — system re-enabled")
                if self._on_resume:
                    self._on_resume()
            else:
                self.send_alert("❌ RESUME rejected — incorrect passcode")
