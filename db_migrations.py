from __future__ import annotations

import sqlite3


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(
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
    conn.execute(
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS live_orders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "order_id TEXT, symbol TEXT, side TEXT, qty INTEGER, status TEXT, created_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS indicator_settings ("
        "name TEXT PRIMARY KEY,"
        "enabled INTEGER NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ui_settings ("
        "key TEXT PRIMARY KEY,"
        "value TEXT NOT NULL"
        ")"
    )
    conn.commit()
