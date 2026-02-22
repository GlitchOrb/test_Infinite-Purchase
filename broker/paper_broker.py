from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from broker.base import AccountSnapshot, BrokerBase, PositionSnapshot, Quote


@dataclass
class _PaperOrder:
    order_id: str
    symbol: str
    side: str
    qty: int
    order_type: str
    limit_price: Optional[float]
    created_at: str
    status: str = "OPEN"


class PaperBroker(BrokerBase):
    """Paper trading broker with local fill simulation and SQLite persistence."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        initial_cash: float = 100_000.0,
        spread_bps: float = 5.0,
        slippage_bps: float = 3.0,
        min_delay_ms: int = 150,
        max_delay_ms: int = 300,
    ) -> None:
        self._conn = conn
        self._spread_bps = max(0.0, spread_bps)
        self._slippage_bps = max(0.0, slippage_bps)
        self._min_delay_ms = max(0, min_delay_ms)
        self._max_delay_ms = max(self._min_delay_ms, max_delay_ms)
        self._quotes: Dict[str, Quote] = {}
        self._init_tables(initial_cash)

    @property
    def name(self) -> str:
        return "PaperBroker"

    @property
    def is_live(self) -> bool:
        return False

    def update_quote(self, quote: Quote) -> None:
        self._quotes[quote.symbol] = quote

    def get_quote(self, symbol: str) -> Quote:
        q = self._quotes.get(symbol)
        if not q:
            raise RuntimeError(f"No quote available for {symbol}")
        self._process_pending_orders(symbol, q.price)
        return q

    def get_ohlcv(self, symbol: str, lookback_days: int) -> List[Dict[str, Any]]:
        raise RuntimeError("PaperBroker requires external market data feed for OHLCV")

    def get_account(self) -> AccountSnapshot:
        cash = self._get_cash()
        positions = self.get_positions()
        equity = cash + sum(p.qty * p.market_price for p in positions)
        return AccountSnapshot(cash=cash, equity=equity, buying_power=cash)

    def get_positions(self) -> List[PositionSnapshot]:
        rows = self._conn.execute(
            "SELECT symbol, qty, avg_price FROM paper_positions WHERE qty > 0"
        ).fetchall()
        out: List[PositionSnapshot] = []
        for symbol, qty, avg_price in rows:
            market_price = self._quotes.get(symbol).price if symbol in self._quotes else float(avg_price)
            out.append(PositionSnapshot(symbol=symbol, qty=int(qty), avg_price=float(avg_price), market_price=float(market_price)))
        return out

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str,
        limit_price: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if qty <= 0:
            raise RuntimeError("Order quantity must be positive")
        side_u = side.upper()
        typ_u = order_type.upper()
        if side_u not in {"BUY", "SELL"}:
            raise RuntimeError("Order side must be BUY or SELL")
        if typ_u not in {"MARKET", "LIMIT"}:
            raise RuntimeError("Order type must be MARKET or LIMIT")
        if typ_u == "LIMIT" and (limit_price is None or limit_price <= 0):
            raise RuntimeError("Limit price is required for LIMIT order")

        self._simulate_delay()
        order_id = f"P{int(time.time() * 1000)}{random.randint(100, 999)}"
        now = datetime.utcnow().isoformat()
        self._conn.execute(
            "INSERT INTO paper_orders (order_id, symbol, side, qty, order_type, limit_price, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (order_id, symbol, side_u, qty, typ_u, limit_price, "OPEN", now, now),
        )
        self._conn.commit()

        q = self._quotes.get(symbol)
        if q:
            self._process_pending_orders(symbol, q.price)

        row = self._conn.execute("SELECT status FROM paper_orders WHERE order_id = ?", (order_id,)).fetchone()
        status = row[0] if row else "OPEN"
        return {"order_id": order_id, "status": status}

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        self._conn.execute(
            "UPDATE paper_orders SET status='CANCELLED', updated_at=? WHERE order_id=? AND status='OPEN'",
            (datetime.utcnow().isoformat(), order_id),
        )
        self._conn.commit()
        return {"order_id": order_id, "status": "CANCELLED"}

    def _process_pending_orders(self, symbol: str, last_price: float) -> None:
        rows = self._conn.execute(
            "SELECT order_id, side, qty, order_type, limit_price FROM paper_orders WHERE symbol=? AND status='OPEN' ORDER BY created_at",
            (symbol,),
        ).fetchall()
        for order_id, side, qty, order_type, limit_price in rows:
            fillable = False
            fill_price = self._fill_price(last_price, side)
            if order_type == "MARKET":
                fillable = True
            elif order_type == "LIMIT":
                if side == "BUY" and float(last_price) <= float(limit_price):
                    fillable = True
                    fill_price = min(fill_price, float(limit_price))
                if side == "SELL" and float(last_price) >= float(limit_price):
                    fillable = True
                    fill_price = max(fill_price, float(limit_price))
            if fillable:
                self._execute_fill(order_id, symbol, side, int(qty), float(fill_price))

    def _execute_fill(self, order_id: str, symbol: str, side: str, qty: int, fill_price: float) -> None:
        now = datetime.utcnow().isoformat()
        if side == "BUY":
            cost = qty * fill_price
            cash = self._get_cash()
            if cash < cost:
                self._conn.execute(
                    "UPDATE paper_orders SET status='REJECTED', updated_at=? WHERE order_id=?",
                    (now, order_id),
                )
                self._conn.commit()
                return
            self._set_cash(cash - cost)
            self._upsert_position_buy(symbol, qty, fill_price)
        else:
            pos = self._conn.execute(
                "SELECT qty, avg_price FROM paper_positions WHERE symbol=?",
                (symbol,),
            ).fetchone()
            held = int(pos[0]) if pos else 0
            if held < qty:
                self._conn.execute(
                    "UPDATE paper_orders SET status='REJECTED', updated_at=? WHERE order_id=?",
                    (now, order_id),
                )
                self._conn.commit()
                return
            self._set_cash(self._get_cash() + qty * fill_price)
            self._upsert_position_sell(symbol, qty)

        self._conn.execute(
            "INSERT INTO paper_fills (order_id, symbol, side, qty, fill_price, filled_at) VALUES (?,?,?,?,?,?)",
            (order_id, symbol, side, qty, fill_price, now),
        )
        self._conn.execute(
            "UPDATE paper_orders SET status='FILLED', updated_at=? WHERE order_id=?",
            (now, order_id),
        )
        self._conn.commit()

    def _fill_price(self, last_price: float, side: str) -> float:
        spread = last_price * (self._spread_bps / 10_000.0)
        slip = last_price * (self._slippage_bps / 10_000.0)
        if side == "BUY":
            return last_price + spread + slip
        return max(0.0, last_price - spread - slip)

    def _simulate_delay(self) -> None:
        delay = random.randint(self._min_delay_ms, self._max_delay_ms)
        time.sleep(delay / 1000.0)

    def _init_tables(self, initial_cash: float) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_orders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "order_id TEXT UNIQUE NOT NULL,"
            "symbol TEXT NOT NULL,"
            "side TEXT NOT NULL,"
            "qty INTEGER NOT NULL,"
            "order_type TEXT NOT NULL,"
            "limit_price REAL,"
            "status TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_fills ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "order_id TEXT NOT NULL,"
            "symbol TEXT NOT NULL,"
            "side TEXT NOT NULL,"
            "qty INTEGER NOT NULL,"
            "fill_price REAL NOT NULL,"
            "filled_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_positions ("
            "symbol TEXT PRIMARY KEY,"
            "qty INTEGER NOT NULL,"
            "avg_price REAL NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_account ("
            "id INTEGER PRIMARY KEY CHECK (id = 1),"
            "cash REAL NOT NULL"
            ")"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO paper_account (id, cash) VALUES (1, ?)",
            (float(initial_cash),),
        )
        self._conn.commit()

    def _get_cash(self) -> float:
        row = self._conn.execute("SELECT cash FROM paper_account WHERE id = 1").fetchone()
        return float(row[0]) if row else 0.0

    def _set_cash(self, cash: float) -> None:
        self._conn.execute("UPDATE paper_account SET cash = ? WHERE id = 1", (float(cash),))

    def _upsert_position_buy(self, symbol: str, qty: int, fill_price: float) -> None:
        row = self._conn.execute("SELECT qty, avg_price FROM paper_positions WHERE symbol=?", (symbol,)).fetchone()
        if row:
            old_qty, old_avg = int(row[0]), float(row[1])
            new_qty = old_qty + qty
            new_avg = ((old_qty * old_avg) + (qty * fill_price)) / new_qty
            self._conn.execute(
                "UPDATE paper_positions SET qty=?, avg_price=? WHERE symbol=?",
                (new_qty, new_avg, symbol),
            )
        else:
            self._conn.execute(
                "INSERT INTO paper_positions (symbol, qty, avg_price) VALUES (?,?,?)",
                (symbol, qty, fill_price),
            )

    def _upsert_position_sell(self, symbol: str, qty: int) -> None:
        row = self._conn.execute("SELECT qty FROM paper_positions WHERE symbol=?", (symbol,)).fetchone()
        if not row:
            return
        old_qty = int(row[0])
        new_qty = old_qty - qty
        if new_qty <= 0:
            self._conn.execute("DELETE FROM paper_positions WHERE symbol=?", (symbol,))
        else:
            self._conn.execute("UPDATE paper_positions SET qty=? WHERE symbol=?", (new_qty, symbol))
