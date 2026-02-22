"""Advanced Telegram control + notification interface for Alpha Predator v4.1.

Supports both long-polling and webhook-driven update handling.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib import request as urlrequest

from db import get_alert, get_all_positions, get_latest_regime, get_system, set_alert

log = logging.getLogger(__name__)

MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"


def mdv2_escape(text: str) -> str:
    return "".join(f"\\{ch}" if ch in MDV2_SPECIALS else ch for ch in text)


@dataclass
class TelegramBotConfig:
    token: str
    admin_user_ids: set[int]
    allowed_chat_ids: set[str]
    poll_interval_s: int = 2
    log_path: str = "telegram_bot.log"
    webhook_secret: str = ""

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        # Prefer requested env names, keep backward compatibility.
        admins_env = os.environ.get("TG_ADMIN_IDS") or os.environ.get("TG_ADMIN_USER_IDS", "")
        chats_env = os.environ.get("TG_CHAT_ID") or os.environ.get("TG_CHAT_IDS", "")

        admins = {int(x.strip()) for x in admins_env.split(",") if x.strip().isdigit()}
        chats = {x.strip() for x in chats_env.split(",") if x.strip()}
        token = os.environ.get("TG_BOT_TOKEN", "")
        secret = os.environ.get("TG_WEBHOOK_SECRET", "")
        return cls(token=token, admin_user_ids=admins, allowed_chat_ids=chats, webhook_secret=secret)


class TelegramControlBot:
    def __init__(self, runtime: Any, cfg: TelegramBotConfig | None = None) -> None:
        self.runtime = runtime
        self.cfg = cfg or TelegramBotConfig.from_env()
        self._offset = 0
        self._running = False
        self._pending_resume_user: Optional[int] = None

    def _api(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.cfg.token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        for attempt in range(3):
            try:
                req = urlrequest.Request(url, data=body, headers=headers, method="POST")
                with urlrequest.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                log.exception("Telegram API call failed: %s", method)
                time.sleep(0.5 * (attempt + 1))
        return {"ok": False}

    def send_markdown(self, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._api("sendMessage", payload)

    def edit_markdown(self, chat_id: str, message_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "MarkdownV2",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._api("editMessageText", payload)

    def _main_menu(self) -> dict:
        return {
            "inline_keyboard": [
                [{"text": "📊 Status", "callback_data": "status_refresh"}, {"text": "🛑 Kill Switch", "callback_data": "kill_confirm"}],
                [{"text": "▶ Resume", "callback_data": "resume_invoke"}, {"text": "📈 Positions", "callback_data": "positions_show"}],
                [{"text": "⚙ Config", "callback_data": "config_show"}, {"text": "❓ Help", "callback_data": "help_show"}],
            ]
        }

    def _status_menu(self) -> dict:
        return {"inline_keyboard": [[{"text": "Refresh", "callback_data": "status_refresh"}, {"text": "Back", "callback_data": "back_main"}]]}

    def _config_menu(self) -> dict:
        return {
            "inline_keyboard": [
                [{"text": "Toggle Daily Summaries", "callback_data": "toggle_summary"}],
                [{"text": "Set Alert Thresholds", "callback_data": "set_threshold_drawdown"}],
                [{"text": "Back", "callback_data": "back_main"}],
            ]
        }

    def _is_authorized_chat(self, chat_id: str) -> bool:
        return not self.cfg.allowed_chat_ids or chat_id in self.cfg.allowed_chat_ids

    def _is_admin(self, user_id: int) -> bool:
        return user_id in self.cfg.admin_user_ids

    def _reject(self, chat_id: str) -> None:
        self.send_markdown(chat_id, mdv2_escape("❌ Unauthorized — this command is restricted."))

    def _help_text(self) -> str:
        return mdv2_escape(
            "/help /status /positions /balance /userinfo /kill /resume <passcode> /exit "
            "/set_drawdown_alert 20% /set_daily_summary 08:00"
        )

    def _build_status_text(self) -> str:
        conn = self.runtime.conn
        regime = get_latest_regime(conn) or {}
        pnl = get_system(conn, "total_pnl") or "0"
        mdd = get_system(conn, "mdd") or "0"
        carry = get_system(conn, "carry_budget") or "0"
        inject = get_system(conn, "injection_budget") or "0"
        cooldown = get_system(conn, "soxs_cooldown_remaining") or "0"
        session = "OPEN" if datetime.now().hour in range(9, 17) else "CLOSED"

        raw = (
            f"Regime: {regime.get('effective_state', 'NEUTRAL')}\n"
            f"Score L/M/A: {regime.get('indicator_L', 0)}/{regime.get('indicator_M', 0)}/{regime.get('indicator_A', 0)} (score={regime.get('score', 0)})\n"
            f"Engine mode: {regime.get('engine_intent', 'NONE')}\n"
            f"Carry budget: {carry}\nInjection budget: {inject}\n"
            f"SOXS cooldown: {cooldown}\n"
            f"PnL: {pnl} | MDD: {mdd}\n"
            f"NYSE session: {session}"
        )
        return mdv2_escape(raw)

    def _positions_text(self) -> str:
        rows = get_all_positions(self.runtime.conn)
        if not rows:
            return mdv2_escape("No positions")
        return mdv2_escape("\n".join(f"{r['symbol']}: qty={r['qty']}, avg={r['avg_cost']}" for r in rows))

    def _balance_text(self) -> str:
        capital = self.runtime._get_total_capital()
        fx = "N/A"
        if hasattr(self.runtime, "kis") and self.runtime.kis:
            try:
                fx = str(self.runtime.kis.fetch_usdkrw())
            except Exception:
                fx = "ERR"
        return mdv2_escape(f"Balance: {capital}\nUSD/KRW: {fx}")

    def _userinfo_text(self) -> str:
        return mdv2_escape(f"chat_ids={sorted(self.cfg.allowed_chat_ids)}\nadmins={sorted(self.cfg.admin_user_ids)}")

    def _handle_command(self, msg: dict) -> None:
        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = int(msg.get("from", {}).get("id", 0))
        text = (msg.get("text") or "").strip()
        if not self._is_authorized_chat(chat_id):
            return

        if self._pending_resume_user == user_id and not text.startswith("/"):
            ok, response = self.runtime.handle_resume(text)
            log.info("Resume command verified=%s", ok)
            self.send_markdown(chat_id, mdv2_escape(response))
            self._pending_resume_user = None
            return

        if text.startswith("/help"):
            self.send_markdown(chat_id, self._help_text(), self._main_menu())
        elif text.startswith("/status"):
            self.send_markdown(chat_id, self._build_status_text(), self._status_menu())
        elif text.startswith("/positions"):
            self.send_markdown(chat_id, self._positions_text())
        elif text.startswith("/balance"):
            self.send_markdown(chat_id, self._balance_text())
        elif text.startswith("/userinfo"):
            self.send_markdown(chat_id, self._userinfo_text())
        elif text.startswith("/set_drawdown_alert"):
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            pct = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else "20%"
            set_alert(self.runtime.conn, "drawdown_alert", pct)
            self.send_markdown(chat_id, mdv2_escape(f"Drawdown alert set: {pct}"))
        elif text.startswith("/set_daily_summary"):
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            hhmm = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else "08:00"
            if not re.match(r"^\d{2}:\d{2}$", hhmm):
                self.send_markdown(chat_id, mdv2_escape("Invalid time format. Use HH:MM"))
                return
            set_alert(self.runtime.conn, "daily_summary_time", hhmm)
            self.send_markdown(chat_id, mdv2_escape(f"Daily summary time set: {hhmm}"))
        elif text.startswith("/kill"):
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            self.runtime.handle_kill_command()
            self.send_markdown(chat_id, mdv2_escape("🚨 Kill switch activated — trading halted."))
        elif text.startswith("/resume"):
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                self._pending_resume_user = user_id
                self.send_markdown(chat_id, mdv2_escape("Send passcode in next message."))
                return
            ok, response = self.runtime.handle_resume(parts[1].strip())
            log.info("Resume command verified=%s", ok)
            self.send_markdown(chat_id, mdv2_escape(response))
        elif text.startswith("/exit"):
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            self._running = False
            self.send_markdown(chat_id, mdv2_escape("Bot loop stopping."))

    def _handle_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        msg = cb.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = int(cb.get("from", {}).get("id", 0))
        message_id = int(msg.get("message_id", 0))

        if data in {"status_refresh", "positions_show"}:
            txt = self._build_status_text() if data == "status_refresh" else self._positions_text()
            self.edit_markdown(chat_id, message_id, txt, self._status_menu())
        elif data == "kill_confirm":
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            self.runtime.handle_kill_command()
            self.edit_markdown(chat_id, message_id, mdv2_escape("🚨 Kill switch activated — trading halted."), self._main_menu())
        elif data == "resume_invoke":
            if not self._is_admin(user_id):
                self._reject(chat_id)
                return
            self._pending_resume_user = user_id
            self.edit_markdown(chat_id, message_id, mdv2_escape("Enter resume passcode in next message."), self._main_menu())
        elif data == "toggle_summary":
            current = get_alert(self.runtime.conn, "daily_summary_enabled", "true")
            new = "false" if current == "true" else "true"
            set_alert(self.runtime.conn, "daily_summary_enabled", new)
            self.edit_markdown(chat_id, message_id, mdv2_escape(f"Daily summaries: {new}"), self._config_menu())
        elif data.startswith("set_threshold_"):
            name = data.replace("set_threshold_", "")
            set_alert(self.runtime.conn, f"threshold_{name}", "20%")
            self.edit_markdown(chat_id, message_id, mdv2_escape(f"Threshold updated: {name}"), self._config_menu())
        elif data == "config_show":
            self.edit_markdown(chat_id, message_id, mdv2_escape("Config menu"), self._config_menu())
        elif data in {"help_show", "back_main"}:
            self.edit_markdown(chat_id, message_id, self._help_text(), self._main_menu())

    def process_update(self, update: dict) -> None:
        """Handle one Telegram update payload (for webhook or polling)."""
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_command(update["message"])

    def notify_regime_change(self, state: str, score: int) -> None:
        emoji = "🟢" if state == "BULL" else "🔴" if state == "BEAR" else "🟡"
        self.broadcast(f"{emoji} Regime changed to {state} (score={score})")

    def notify_order_execution(self, side: str, symbol: str, price: float, qty_text: str) -> None:
        icon = "📈" if side == "BUY" else "📉"
        self.broadcast(f"{icon} {side} ORDER executed: {symbol} @ {price:.2f} × {qty_text}")

    def notify_stop_resume(self, stopped: bool, reason: str) -> None:
        msg = "🚨 Emergency stop triggered" if stopped else "✅ System resumed after successful reconcile"
        self.broadcast(f"{msg} — {reason}")

    def notify_risk(self, text: str) -> None:
        self.broadcast(f"⚠️ Risk alert: {text}")

    def notify_periodic_summary(self, period: str, text: str) -> None:
        self.broadcast(f"🧾 {period} summary\n{text}")

    def broadcast(self, raw_text: str) -> None:
        text = mdv2_escape(raw_text)
        default_chat = str(getattr(self.runtime.cfg, "telegram_chat_id", ""))
        for chat_id in (self.cfg.allowed_chat_ids or {default_chat}):
            if chat_id:
                self.send_markdown(chat_id, text)

    def set_webhook(self, webhook_url: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"url": webhook_url}
        if self.cfg.webhook_secret:
            payload["secret_token"] = self.cfg.webhook_secret
        return self._api("setWebhook", payload)

    def run_forever(self) -> None:
        if not self.cfg.token:
            raise RuntimeError("TG_BOT_TOKEN is required")
        logging.basicConfig(filename=self.cfg.log_path, level=logging.INFO)
        self._running = True
        while self._running:
            resp = self._api("getUpdates", {"offset": self._offset, "timeout": 20})
            for upd in resp.get("result", []):
                self._offset = max(self._offset, upd["update_id"] + 1)
                self.process_update(upd)
            time.sleep(self.cfg.poll_interval_s)
