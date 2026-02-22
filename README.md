# Infinite Purchase

Rule-based semiconductor sector rotation system that runs on Kiwoom OpenAPI+.

Trades SOXL/SOXS based on SOXX regime signals. No ML, no LLM — pure SMA crossover logic with a finite state machine.

---

## What This Does

Watches SOXX daily close. Computes three SMA crossover signals (20/50/200), scores them 0–3, and decides which leveraged ETF to accumulate:

| Score | Regime | Action |
|-------|--------|--------|
| 3 | BULL | Buy SOXL (trend compounding) |
| 0 + deep drawdown | BEAR | Buy SOXS (hit-and-run) |
| 0 (shallow) / 1 / 2 | NEUTRAL | Cash, no new positions |

When flipping from BEAR → BULL, a 3-day transition swap runs: wind down SOXS, ramp up SOXL.

## Architecture

```
SOXX OHLCV
    │
    ▼
StrategyEngine        # regime FSM (L/M/S → score → state)
    │
    ▼
TradeManager          # position logic → OrderIntent[]
    │
    ▼
Runtime               # Kiwoom COM + SQLite + scheduler
    │
    ├── KiwoomAdapter     PyQt5 QAxWidget, rate-limited
    ├── SQLite            positions, orders, fills, idempotency locks
    ├── Scheduler         QTimer-based, DST-aware US market hours
    └── KillSwitch        Telegram /kill + /resume
```

## Key Modules

| File | What |
|------|------|
| `strategy_engine.py` | SMA indicators, regime scoring, FSM transitions |
| `trade_manager.py` | Slice-based buying, trailing stops, take-profit, vampire rebalance |
| `runtime.py` | 24/7 orchestrator — scheduling, reconcile, fill processing |
| `kiwoom_adapter.py` | Kiwoom OpenAPI+ COM wrapper with backoff + token bucket |
| `db.py` | SQLite schema + CRUD + idempotent daily action locks |
| `kill_switch.py` | Telegram polling — emergency stop / resume |
| `config.py` | All tunables in one place |

## Strategy Details

### SOXL Engine (Bull)
- Daily accumulation via configurable slice count (default 35 slices)
- Averaging down: 1 slice normally, 2 at -8%, 3 at -15% from avg cost
- Trailing stop: sell 50% at -15% from peak, sell all at -25%

### SOXS Engine (Bear)
- Allocation capped at 30% of total capital
- Take-profit at +8%, max holding 25 days
- Loss cuts at -15% (half) and -25% (all)

### Vampire Rebalance
When SOXL is down 40%+ and a SOXS position closes at profit during BEAR regime, 70% of that realized gain gets injected into the next SOXL buy. Cross-subsidization.

### Transition Swap (BEAR → BULL)
- Day 1: Stop SOXS buys, start SOXL (1 slice)
- Day 2: Sell 50% SOXS, SOXL buys + profit injection
- Day 3: Sell all SOXS, resume normal bull engine

## Safety

- **Idempotent buys** — SQLite `INSERT OR IGNORE` lock per (date, symbol). No double buys even on crash/restart.
- **Reconcile** — broker holdings vs. DB on every startup + every 15 min. Mismatch → emergency stop + cancel all + Telegram alert.
- **Orphan cleanup** — unfilled orders cancelled at EOD+5min, locks rolled back.
- **Rate limiting** — token bucket (1 req/s) + exponential backoff up to 30s cap.
- **Kill switch** — Telegram `/kill` persists to SQLite, survives restarts.

## Setup

```
pip install pyqt5 pandas requests
```

Requires Kiwoom OpenAPI+ installed (Windows only).

```
set KIWOOM_ACCOUNT=your_account_number
set TELEGRAM_TOKEN=your_bot_token
set TELEGRAM_CHAT_ID=your_chat_id

python runtime.py
```

## Tests

```
pytest test_strategy_engine.py test_trade_manager.py -v
```

65 tests covering:
- Indicator computation, score logic
- FSM transitions (all regime paths including 3-day swap)
- Trailing stops, take-profit, loss cuts
- Averaging down, slice capping
- Vampire rebalance conditions
- Fill application + position resets
- Sell deduplication, determinism, immutability

## Notes

- All Kiwoom-specific TR codes are marked `TODO(kiwoom)` — swap in the correct 해외주식 TR IDs for your account type.
- `runtime.py` is a working skeleton. The strategy + trade logic is fully implemented and tested.
- No margin. Cash only. One buy per symbol per day.

## License

MIT — not financial or legal advice.
