"""Async scheduler with NYSE calendar placeholders and daily jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from config import RuntimeConfig


@dataclass
class Scheduler:
    cfg: RuntimeConfig

    def ny_now(self) -> datetime:
        return datetime.now(ZoneInfo(self.cfg.market_tz))

    async def run_forever(self, reconcile_cb, orphan_cleanup_cb, status_cb):
        while True:
            now = self.ny_now()
            if now.hour == self.cfg.market_close_h and now.minute == self.cfg.market_close_m + 5:
                await orphan_cleanup_cb()
            if now.minute % self.cfg.reconcile_interval_min == 0:
                await reconcile_cb()
            if now.minute % self.cfg.order_refresh_interval_min == 0:
                await status_cb()
            await asyncio.sleep(30)
