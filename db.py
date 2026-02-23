"""
db.py
=====
SQLite persistence layer — schema, CRUD, idempotency locks, reconcile.

Every public function accepts a ``sqlite3.Connection`` so the caller
controls transaction boundaries.  The module never commits implicitly.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ======================================================================= #
#  Schema
# ======================================================================= #

_SCHEMA_SQL = """
-- Positions (one row per symbol: SOXL / SOXS)
CREATE TABLE IF NOT EXISTS positions (
    symbol          TEXT PRIMARY KEY,
    qty             INTEGER NOT NULL DEFAULT 0,
    avg_cost        REAL    NOT NULL DEFAULT 0.0,
    entry_date      TEXT,
    allocated_capital REAL  NOT NULL DEFAULT 0.0,
    max_price_since_entry REAL NOT NULL DEFAULT 0.0,
    trailing_stage  INTEGER NOT NULL DEFAULT 0,
    holding_days    INTEGER NOT NULL DEFAULT 0,
    loss_cut_stage  INTEGER NOT NULL DEFAULT 0,
    slices_used     INTEGER NOT NULL DEFAULT 0,
    cooldown_remaining INTEGER NOT NULL DEFAULT 0,
    forced_close    INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL
);

-- Orders
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_order_id TEXT,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    qty             INTEGER NOT NULL,
    notional        REAL    NOT NULL DEFAULT 0.0,
    order_type      TEXT    NOT NULL DEFAULT 'MARKET',
    limit_price     REAL,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    reason          TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- Fills
CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_order_id TEXT    NOT NULL,
    fill_qty        INTEGER NOT NULL,
    fill_price      REAL    NOT NULL,
    filled_at       TEXT    NOT NULL
);

-- Daily action idempotency locks
CREATE TABLE IF NOT EXISTS daily_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    action_key      TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'LOCKED',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE(date, action_key)
);

-- Regime history
CREATE TABLE IF NOT EXISTS regime_history (
    date            TEXT PRIMARY KEY,
    close           REAL,
    sma20           REAL,
    sma50           REAL,
    sma200          REAL,
    indicator_L     INTEGER,
    indicator_M     INTEGER,
    indicator_A     INTEGER,
    score           INTEGER,
    return_3m       REAL,
    return_12m      REAL,
    effective_state TEXT,
    transition_active INTEGER,
    transition_day  INTEGER,
    engine_intent   TEXT,
    created_at      TEXT NOT NULL
);

-- System-wide flags
CREATE TABLE IF NOT EXISTS system_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Alert settings (telegram/runtime thresholds and schedules)
CREATE TABLE IF NOT EXISTS alerts (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- User-defined strategies
CREATE TABLE IF NOT EXISTS strategies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    content_json    TEXT NOT NULL,
    is_active       INTEGER DEFAULT 0,
    updated_at      TEXT NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist."""
    conn.executescript(_SCHEMA_SQL)
    # Seed system_state defaults
    _ensure_system_key(conn, "emergency_stop", "false")
    _ensure_system_key(conn, "last_start_time", "")
    _ensure_system_key(conn, "injection_budget", "0.0")
    conn.commit()


# ======================================================================= #
#  System state helpers
# ======================================================================= #

def _ensure_system_key(conn: sqlite3.Connection, key: str, default: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, default, _now()),
    )


def get_system(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute(
        "SELECT value FROM system_state WHERE key = ?", (key,),
    ).fetchone()
    return row[0] if row else ""


def set_system(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, _now()),
    )


def is_emergency_stop(conn: sqlite3.Connection) -> bool:
    return get_system(conn, "emergency_stop").lower() == "true"


def set_emergency_stop(conn: sqlite3.Connection, active: bool) -> None:
    set_system(conn, "emergency_stop", str(active).lower())
    conn.commit()


# ======================================================================= #
#  Positions
# ======================================================================= #

def upsert_position(conn: sqlite3.Connection, symbol: str, **kwargs: Any) -> None:
    """Insert or update a position row.  Pass only the fields to update."""
    cols = list(kwargs.keys()) + ["updated_at"]
    vals = list(kwargs.values()) + [_now()]
    placeholders = ", ".join(f"{c} = excluded.{c}" for c in cols)
    col_names = ", ".join(["symbol"] + cols)
    q_marks = ", ".join(["?"] * (len(cols) + 1))
    sql = (
        f"INSERT INTO positions ({col_names}) VALUES ({q_marks}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {placeholders}"
    )
    conn.execute(sql, [symbol] + vals)


def get_position(conn: sqlite3.Connection, symbol: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,)).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM positions LIMIT 0").description]
    return dict(zip(cols, row))


def get_all_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM positions").fetchall()
    if not rows:
        return []
    cols = [d[0] for d in conn.execute("SELECT * FROM positions LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


# ======================================================================= #
#  Orders
# ======================================================================= #

def insert_order(conn: sqlite3.Connection, **kwargs: Any) -> int:
    kwargs.setdefault("created_at", _now())
    kwargs.setdefault("updated_at", _now())
    cols = ", ".join(kwargs.keys())
    q_marks = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(f"INSERT INTO orders ({cols}) VALUES ({q_marks})", list(kwargs.values()))
    return cur.lastrowid  # type: ignore[return-value]


def update_order(conn: sqlite3.Connection, order_id: int, **kwargs: Any) -> None:
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    conn.execute(f"UPDATE orders SET {sets} WHERE id = ?", list(kwargs.values()) + [order_id])


def get_open_orders(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE status IN ('PENDING','SUBMITTED','PARTIAL')"
    ).fetchall()
    if not rows:
        return []
    cols = [d[0] for d in conn.execute("SELECT * FROM orders LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


# ======================================================================= #
#  Fills
# ======================================================================= #

def insert_fill(conn: sqlite3.Connection, broker_order_id: str,
                fill_qty: int, fill_price: float) -> int:
    cur = conn.execute(
        "INSERT INTO fills (broker_order_id, fill_qty, fill_price, filled_at) VALUES (?,?,?,?)",
        (broker_order_id, fill_qty, fill_price, _now()),
    )
    return cur.lastrowid  # type: ignore[return-value]


# ======================================================================= #
#  Daily-action idempotency
# ======================================================================= #

def try_lock_action(conn: sqlite3.Connection, date_str: str, action_key: str) -> bool:
    """Attempt to acquire an idempotent lock.  Returns True if acquired.

    If the action already exists (LOCKED or DONE), returns False.
    Uses INSERT OR IGNORE + rowcount to be race-free under WAL mode.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO daily_actions (date, action_key, status, created_at, updated_at) "
        "VALUES (?, ?, 'LOCKED', ?, ?)",
        (date_str, action_key, _now(), _now()),
    )
    return cur.rowcount == 1


def mark_action_done(conn: sqlite3.Connection, date_str: str, action_key: str) -> None:
    conn.execute(
        "UPDATE daily_actions SET status = 'DONE', updated_at = ? "
        "WHERE date = ? AND action_key = ?",
        (_now(), date_str, action_key),
    )


def rollback_action(conn: sqlite3.Connection, date_str: str, action_key: str) -> None:
    """Remove the lock so the action can be retried (e.g. order never filled)."""
    conn.execute(
        "DELETE FROM daily_actions WHERE date = ? AND action_key = ?",
        (date_str, action_key),
    )


def is_action_done(conn: sqlite3.Connection, date_str: str, action_key: str) -> bool:
    row = conn.execute(
        "SELECT status FROM daily_actions WHERE date = ? AND action_key = ?",
        (date_str, action_key),
    ).fetchone()
    return row is not None and row[0] == "DONE"


# ======================================================================= #
#  Regime history
# ======================================================================= #

def insert_regime(conn: sqlite3.Connection, **kwargs: Any) -> None:
    kwargs["created_at"] = _now()
    cols = ", ".join(kwargs.keys())
    q_marks = ", ".join(["?"] * len(kwargs))
    conn.execute(
        f"INSERT OR REPLACE INTO regime_history ({cols}) VALUES ({q_marks})",
        list(kwargs.values()),
    )


def get_latest_regime(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM regime_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM regime_history LIMIT 0").description]
    return dict(zip(cols, row))


# ======================================================================= #
#  Alerts config
# ======================================================================= #

def set_alert(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO alerts (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, _now()),
    )
    conn.commit()


def get_alert(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM alerts WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def get_all_alerts(conn: sqlite3.Connection) -> Dict[str, str]:
    rows = conn.execute("SELECT key, value FROM alerts").fetchall()
    return {k: v for k, v in rows}


# ======================================================================= #
#  Strategies
# ======================================================================= #

def save_strategy(conn: sqlite3.Connection, name: str, content_json: str) -> None:
    conn.execute(
        "INSERT INTO strategies (name, content_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET content_json=excluded.content_json, updated_at=excluded.updated_at",
        (name, content_json, _now()),
    )
    conn.commit()


def get_strategies(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM strategies ORDER BY name").fetchall()
    if not rows:
        return []
    cols = [d[0] for d in conn.execute("SELECT * FROM strategies LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def delete_strategy(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM strategies WHERE name = ?", (name,))
    conn.commit()


def set_active_strategy(conn: sqlite3.Connection, name: str) -> None:
    # Deactivate all
    conn.execute("UPDATE strategies SET is_active = 0")
    # Activate target
    conn.execute("UPDATE strategies SET is_active = 1 WHERE name = ?", (name,))
    conn.commit()


def get_active_strategy(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM strategies WHERE is_active = 1 LIMIT 1").fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM strategies LIMIT 0").description]
    return dict(zip(cols, row))

# ======================================================================= #
#  Connection factory
# ======================================================================= #

def open_db(path: str) -> sqlite3.Connection:
    """Open (or create) the database with recommended pragmas."""
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ======================================================================= #
#  Utility
# ======================================================================= #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
