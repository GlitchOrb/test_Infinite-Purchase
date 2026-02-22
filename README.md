# Infinite Purchase — Kiwoom REST Trading UI

PyQt desktop trading application centered on `app.py`.

## Product Direction (Single-Coherent App)
- **Main entry point:** `app.py`
- **Broker stack:** Kiwoom REST OpenAPI only (authenticated market/account/live order paths)
- **Modes:** Guest / Paper / Live (mutually exclusive)
- **No KIS usage**
- **No Kiwoom COM/QAx/CommConnect runtime flow for end users**

Legacy files from older architecture may remain in repository history, but product behavior is UI-first via `app.py` only.

## Run

```bash
python app.py
```

## Required Environment (Live/Paper market data)

- `KIWOOM_REST_BASE_URL`
- `KIWOOM_REST_ENDPOINTS_JSON`
- `KIWOOM_ACCOUNT`

`kiwoom_rest_client.py` fails fast if endpoint mapping is not configured.

## Security

- Do not hardcode credentials
- Do not log tokens/secrets
- Do not store secrets in SQLite
- Windows secure storage preferred for remembered credentials (Credential Manager / DPAPI fallback)

## Notes

- Guest mode performs chart/indicator-only UI behavior without authenticated trading.
- Paper mode uses Kiwoom REST market data and local simulated execution.
- Live mode uses Kiwoom REST for market/account/order paths.
