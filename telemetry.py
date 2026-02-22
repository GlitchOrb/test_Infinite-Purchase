"""Telemetry helpers: logging + Telegram alert routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kill_switch import KillSwitch

log = logging.getLogger(__name__)


@dataclass
class Telemetry:
    kill_sw: KillSwitch | None = None

    def info(self, msg: str) -> None:
        log.info(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def critical(self, msg: str) -> None:
        log.critical(msg)
        if self.kill_sw:
            self.kill_sw.send_alert(f"🚨 {msg}")

    def daily_summary(self, text: str) -> None:
        if self.kill_sw:
            self.kill_sw.send_alert(f"📊 Daily summary\n{text}")
