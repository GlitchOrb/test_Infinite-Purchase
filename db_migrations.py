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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS condition_orders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "mode TEXT NOT NULL,"
        "symbol TEXT NOT NULL,"
        "operator TEXT NOT NULL,"
        "trigger_price REAL NOT NULL,"
        "action TEXT NOT NULL,"
        "order_type TEXT NOT NULL,"
        "qty INTEGER NOT NULL,"
        "limit_price REAL,"
        "status TEXT NOT NULL,"
        "created_at TEXT NOT NULL,"
        "triggered_at TEXT,"
        "completed_at TEXT,"
        "broker_order_id TEXT,"
        "fail_reason TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS telegram_settings_meta ("
        "id INTEGER PRIMARY KEY CHECK (id = 1),"
        "enabled INTEGER NOT NULL DEFAULT 0,"
        "chat_id_present INTEGER NOT NULL DEFAULT 0,"
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "INSERT OR IGNORE INTO telegram_settings_meta (id, enabled, chat_id_present, updated_at) VALUES (1,0,0,datetime('now'))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS strategies ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT UNIQUE NOT NULL,"
        "content_json TEXT NOT NULL,"
        "is_active INTEGER DEFAULT 0,"
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.commit()
