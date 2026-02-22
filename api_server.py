"""FastAPI orchestration server for runtime state, control, and Telegram webhook."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

from config import RuntimeConfig
from db import get_all_positions, get_latest_regime, get_open_orders, get_system, is_emergency_stop
from runtime import Runtime
from telegram_bot import TelegramBotConfig, TelegramControlBot

app = FastAPI(title="Alpha Predator API", version="4.1")
_cfg = RuntimeConfig.from_env()
_runtime = Runtime(_cfg)
_bot = TelegramControlBot(_runtime, TelegramBotConfig.from_env())


@app.get("/status")
def status():
    regime = get_latest_regime(_runtime.conn)
    return {
        "emergency_stop": is_emergency_stop(_runtime.conn),
        "regime": regime,
        "positions": get_all_positions(_runtime.conn),
        "signals": {
            "effective_state": regime.get("effective_state") if regime else None,
            "score": regime.get("score") if regime else None,
        },
    }


@app.get("/orders")
def orders():
    return {"orders": get_open_orders(_runtime.conn)}


@app.get("/balance")
def balance():
    return {
        "total_capital": _runtime._get_total_capital(),
        "injection_budget": float(get_system(_runtime.conn, "injection_budget") or 0.0),
    }


@app.get("/metrics")
def metrics():
    p = Path("backtest_output/demo_metrics.json")
    if not p.exists():
        raise HTTPException(status_code=404, detail="metrics file not found")
    return json.loads(p.read_text())


@app.post("/reconcile")
def reconcile():
    _runtime._reconcile(is_startup=False)
    return {"ok": True, "emergency_stop": is_emergency_stop(_runtime.conn)}


@app.post("/resume")
def resume():
    _runtime._handle_resume()
    return {"ok": True, "emergency_stop": is_emergency_stop(_runtime.conn)}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    expected = _bot.cfg.webhook_secret
    if expected and x_telegram_bot_api_secret_token != expected:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    payload = await request.json()
    _bot.process_update(payload)
    return {"ok": True}


@app.post("/telegram/set_webhook")
def set_telegram_webhook(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    return _bot.set_webhook(url)
