"""Async REST adapter for KIS API with retry/backoff and rate limiting."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import httpx

from config import KisApiConfig


@dataclass
class KisApiAdapter:
    cfg: KisApiConfig

    def __post_init__(self):
        self._client = httpx.AsyncClient(base_url=self.cfg.base_url, timeout=15)
        self._lock = asyncio.Lock()

    async def _request(self, method: str, path: str, *, params=None, json=None, headers=None):
        retry = 0
        while True:
            async with self._lock:
                await asyncio.sleep(self.cfg.req_interval_s)
                try:
                    resp = await self._client.request(method, path, params=params, json=json, headers=headers)
                    if resp.status_code < 500:
                        resp.raise_for_status()
                        return resp.json()
                except Exception:
                    pass
            retry += 1
            if retry > self.cfg.max_retries:
                raise RuntimeError(f"KIS request failed: {method} {path}")
            await asyncio.sleep(min(self.cfg.backoff_cap_s, self.cfg.backoff_base_s * (2 ** (retry - 1))))

    async def get_quote(self, symbol: str):
        return await self._request('GET', self.cfg.path_quote, params={'SYMB': symbol})

    async def get_balance(self):
        return await self._request('GET', self.cfg.path_balance)

    async def get_positions(self):
        return await self._request('GET', self.cfg.path_balance)

    async def submit_buy(self, symbol: str, qty: int, price: float | None = None):
        return await self._request('POST', self.cfg.path_order, json={'side': 'buy', 'symbol': symbol, 'qty': qty, 'price': price})

    async def submit_sell(self, symbol: str, qty: int, price: float | None = None):
        return await self._request('POST', self.cfg.path_order, json={'side': 'sell', 'symbol': symbol, 'qty': qty, 'price': price})

    async def fetch_usdkrw(self):
        return await self._request('GET', self.cfg.path_forex)
