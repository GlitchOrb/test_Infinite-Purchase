# TELEGRAM_BOT.md

## Setup

### Required environment variables

```bash
export TG_BOT_TOKEN="<telegram_bot_token>"
export TG_ADMIN_IDS="123456789,987654321"
export TG_CHAT_ID="-1001234567890"
# optional for webhook verification
export TG_WEBHOOK_SECRET="<random_secret>"
```

> Backward compatibility is kept for `TG_ADMIN_USER_IDS` and `TG_CHAT_IDS`.

## Message format rules (MarkdownV2)

Telegram MarkdownV2 requires escaping special characters.

Example:

```python
text = "*Hello\\!* This is a test message\\."
```

## Engine integration architecture

```text
TelegramBot <--> FastAPI API Server <--> Engine (Strategy + TradeManager + KIS)
```

Webhook mode is supported via FastAPI endpoint `/telegram/webhook` and is recommended for efficiency.

## Commands

- `/help` — command help + inline menu
- `/status` — regime, L/M/A score, engine mode, carry/injection budgets, cooldown, PnL/MDD, NYSE session
- `/positions` — position snapshot
- `/balance` — total capital and forex
- `/userinfo` — bot chat/admin config
- `/kill` *(admin only)* — halt trading
- `/resume <passcode>` *(admin only)* — resume after reconcile
- `/exit` *(admin only)* — stop polling loop
- `/set_drawdown_alert 20%` *(admin only)*
- `/set_daily_summary 08:00` *(admin only)*

## Inline keyboard interactions

Main menu:
- 📊 Status
- 🛑 Kill Switch
- ▶ Resume
- 📈 Positions
- ⚙ Config
- ❓ Help

Callbacks implemented:
- `status_refresh`
- `kill_confirm`
- `resume_invoke`
- `toggle_summary`
- `set_threshold_<name>`

## Security notes

- Passcodes are never logged.
- Full raw command text is not logged.
- Admin-only commands return: `❌ Unauthorized — this command is restricted.`
- Logs can be written to dedicated `telegram_bot.log`.

## Example messages

- `🚨 Kill switch activated — trading halted.`
- `Resume accepted`
- `Resume denied — reconcile mismatch.`
- `🟢 Regime changed to BULL (score=3)`
- `📈 BUY ORDER executed: SOXL @ 85.12 × 1 slice`
- `⚠️ Risk alert: rapid drawdown threshold breached`
