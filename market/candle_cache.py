"""캔들 캐시 매니저 — SQLite 기반 로컬 OHLCV 캐시.

기능:
 • 최근 인트라데이 캔들을 SQLite에 저장
 • 앱 재시작 시 캐시된 데이터 즉시 로드 → API 호출 전 빠른 렌더링
 • 주말/휴장에도 마지막 유효 데이터 표시
 • 심볼별 캐시 관리
 • 오래된 캐시 자동 정리 (7일 이상)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS candle_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL DEFAULT 'daily',
    dt          TEXT    NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL DEFAULT 0,
    cached_at   TEXT    NOT NULL,
    UNIQUE(symbol, timeframe, dt)
);

CREATE TABLE IF NOT EXISTS cache_meta (
    symbol      TEXT PRIMARY KEY,
    last_fetch  TEXT NOT NULL,
    candle_count INTEGER NOT NULL DEFAULT 0,
    extended_hours_supported INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_candle_symbol_tf ON candle_cache(symbol, timeframe);
"""


class CandleCacheManager:
    """SQLite 기반 캔들 데이터 캐시."""

    MAX_CANDLES_PER_SYMBOL = 500
    STALE_DAYS = 7

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ─── 저장 ───
    def save_candles(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
        timeframe: str = "daily",
        extended_hours: bool = False,
    ) -> None:
        """캔들 데이터를 캐시에 저장 (upsert)."""
        if not candles:
            return

        now = datetime.now(timezone.utc).isoformat()

        for c in candles:
            dt = str(c.get("date", c.get("dt", c.get("time", ""))))
            if not dt:
                continue
            self._conn.execute(
                """INSERT INTO candle_cache (symbol, timeframe, dt, open, high, low, close, volume, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, timeframe, dt) DO UPDATE SET
                     open=excluded.open, high=excluded.high,
                     low=excluded.low, close=excluded.close,
                     volume=excluded.volume, cached_at=excluded.cached_at""",
                (symbol, timeframe, dt,
                 float(c.get("open", 0)), float(c.get("high", 0)),
                 float(c.get("low", 0)), float(c.get("close", 0)),
                 int(c.get("volume", 0)), now),
            )

        # 메타 업데이트
        count = self._conn.execute(
            "SELECT COUNT(*) FROM candle_cache WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()[0]

        self._conn.execute(
            """INSERT INTO cache_meta (symbol, last_fetch, candle_count, extended_hours_supported)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 last_fetch=excluded.last_fetch,
                 candle_count=excluded.candle_count,
                 extended_hours_supported=excluded.extended_hours_supported""",
            (symbol, now, count, int(extended_hours)),
        )
        self._conn.commit()

        # 오래된 데이터 정리
        self._prune(symbol, timeframe)

    # ─── 로드 ───
    def load_candles(
        self,
        symbol: str,
        timeframe: str = "daily",
        limit: int = 300,
    ) -> List[Dict[str, Any]]:
        """캐시된 캔들 로드. 없으면 빈 리스트."""
        rows = self._conn.execute(
            """SELECT dt, open, high, low, close, volume
               FROM candle_cache
               WHERE symbol=? AND timeframe=?
               ORDER BY dt ASC
               LIMIT ?""",
            (symbol, timeframe, limit),
        ).fetchall()

        return [
            {"date": r[0], "open": r[1], "high": r[2],
             "low": r[3], "close": r[4], "volume": r[5]}
            for r in rows
        ]

    def has_cache(self, symbol: str) -> bool:
        """해당 심볼의 캐시가 존재하는지 확인."""
        row = self._conn.execute(
            "SELECT candle_count FROM cache_meta WHERE symbol=?",
            (symbol,),
        ).fetchone()
        return bool(row and row[0] > 0)

    def is_extended_hours_supported(self, symbol: str) -> bool:
        """캐시된 데이터에서 확장시간 지원 여부 확인."""
        row = self._conn.execute(
            "SELECT extended_hours_supported FROM cache_meta WHERE symbol=?",
            (symbol,),
        ).fetchone()
        return bool(row and row[0])

    def get_last_fetch_time(self, symbol: str) -> Optional[str]:
        """마지막 데이터 업데이트 시각."""
        row = self._conn.execute(
            "SELECT last_fetch FROM cache_meta WHERE symbol=?",
            (symbol,),
        ).fetchone()
        return row[0] if row else None

    # ─── 증분 업데이트 ───
    def append_or_update_candle(
        self,
        symbol: str,
        candle: Dict[str, Any],
        timeframe: str = "daily",
    ) -> None:
        """단일 캔들을 추가하거나 동일 시간 캔들이면 업데이트."""
        dt = str(candle.get("date", candle.get("dt", candle.get("time", ""))))
        if not dt:
            return

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO candle_cache (symbol, timeframe, dt, open, high, low, close, volume, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, timeframe, dt) DO UPDATE SET
                 high=MAX(candle_cache.high, excluded.high),
                 low=MIN(candle_cache.low, excluded.low),
                 close=excluded.close,
                 volume=excluded.volume,
                 cached_at=excluded.cached_at""",
            (symbol, timeframe, dt,
             float(candle.get("open", 0)), float(candle.get("high", 0)),
             float(candle.get("low", 0)), float(candle.get("close", 0)),
             int(candle.get("volume", 0)), now),
        )
        self._conn.commit()

    # ─── 정리 ───
    def _prune(self, symbol: str, timeframe: str) -> None:
        """심볼당 최대 캔들 수 초과 시 오래된 데이터 삭제."""
        count = self._conn.execute(
            "SELECT COUNT(*) FROM candle_cache WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()[0]

        if count > self.MAX_CANDLES_PER_SYMBOL:
            excess = count - self.MAX_CANDLES_PER_SYMBOL
            self._conn.execute(
                """DELETE FROM candle_cache WHERE id IN (
                     SELECT id FROM candle_cache
                     WHERE symbol=? AND timeframe=?
                     ORDER BY dt ASC LIMIT ?
                   )""",
                (symbol, timeframe, excess),
            )
            self._conn.commit()

    def clear_symbol(self, symbol: str) -> None:
        """특정 심볼의 캐시 전체 삭제."""
        self._conn.execute("DELETE FROM candle_cache WHERE symbol=?", (symbol,))
        self._conn.execute("DELETE FROM cache_meta WHERE symbol=?", (symbol,))
        self._conn.commit()
