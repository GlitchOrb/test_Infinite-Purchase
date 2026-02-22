from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from broker.base import AccountSnapshot, BrokerBase, PositionSnapshot, Quote
from kiwoom_rest_client import KiwoomRestClient, DEFAULT_ENDPOINT_MAPPING


class LiveBrokerError(RuntimeError):
    """Raised when live broker operations cannot safely proceed."""


@dataclass(frozen=True)
class NormalizedOrderResult:
    order_id: str
    status: str
    raw: Dict[str, Any]


class KiwoomRestBroker(BrokerBase):
    """Live trading broker adapter backed by Kiwoom REST OpenAPI."""

    def __init__(self, rest_client: KiwoomRestClient, account_no: str) -> None:
        self._client = rest_client
        self._account_no = account_no.strip()
        if not self._account_no:
            raise LiveBrokerError("Account number is required for live trading")

    @property
    def name(self) -> str:
        return "KiwoomRestBroker"

    @property
    def is_live(self) -> bool:
        return True

    def get_quote(self, symbol: str) -> Quote:
        try:
            payload = self._client.get_quote(symbol)
            norm = self._normalize_quote(payload)
            return Quote(
                symbol=symbol,
                price=norm["last"],
                change_pct=norm["change_pct"],
                high=norm["high"],
                low=norm["low"],
                volume=norm["volume"],
                timestamp=norm["ts"],
            )
        except Exception as exc:
            raise self._translate_error("quote", exc)

    def get_ohlcv(self, symbol: str, lookback_days: int) -> List[Dict[str, Any]]:
        try:
            payload = self._client.get_daily(symbol, lookback_days)
            return self._normalize_ohlcv(payload)
        except Exception as exc:
            raise self._translate_error("ohlcv", exc)

    def get_account(self) -> AccountSnapshot:
        try:
            payload = self._client.get_account_balance(self._account_no)
            cash = self._extract_float(payload, ["cash", "available_cash", "dnca_tot_amt"]) 
            equity = self._extract_float(payload, ["equity", "total_equity", "tot_evlu_amt"], default=cash)
            buying_power = self._extract_float(payload, ["buying_power", "ord_psbl_cash", "buyable_cash"], default=cash)
            return AccountSnapshot(cash=cash, equity=equity, buying_power=buying_power)
        except Exception as exc:
            raise self._translate_error("account", exc)

    def get_positions(self) -> List[PositionSnapshot]:
        try:
            payload = self._client.get_holdings(self._account_no)
            return self._normalize_positions(payload)
        except Exception as exc:
            raise self._translate_error("positions", exc)

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
            raise LiveBrokerError("Order quantity must be positive")
        if side.upper() not in {"BUY", "SELL"}:
            raise LiveBrokerError("Order side must be BUY or SELL")
        if order_type.upper() not in {"MARKET", "LIMIT"}:
            raise LiveBrokerError("Order type must be MARKET or LIMIT")
        if order_type.upper() == "LIMIT" and (limit_price is None or float(limit_price) <= 0):
            raise LiveBrokerError("Limit price is required for LIMIT orders")

        payload: Dict[str, Any] = {
            "account_no": self._account_no,
            "symbol": symbol,
            "side": side.upper(),
            "qty": int(qty),
            "order_type": order_type.upper(),
        }
        if limit_price is not None:
            payload["limit_price"] = float(limit_price)
        if metadata:
            payload["metadata"] = metadata

        try:
            result = self._client.place_order(payload)
            norm = self._normalize_order_result(result, default_status="SUBMITTED")
            return {"order_id": norm.order_id, "status": norm.status, "raw": norm.raw}
        except Exception as exc:
            raise self._translate_error("place_order", exc)

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        if not order_id.strip():
            raise LiveBrokerError("order_id is required for cancellation")
        payload: Dict[str, Any] = {
            "account_no": self._account_no,
            "order_id": order_id.strip(),
        }
        if symbol:
            payload["symbol"] = symbol

        try:
            result = self._client.cancel_order(payload)
            norm = self._normalize_order_result(result, default_status="CANCEL_REQUESTED", forced_order_id=order_id)
            return {"order_id": norm.order_id, "status": norm.status, "raw": norm.raw}
        except Exception as exc:
            raise self._translate_error("cancel_order", exc)

    @staticmethod
    def _normalize_quote(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "last": KiwoomRestBroker._extract_float(payload, ["price", "last", "close", "current_price"]),
            "change": KiwoomRestBroker._extract_float(payload, ["change", "diff", "change_value"], default=0.0),
            "change_pct": KiwoomRestBroker._extract_float(payload, ["change_pct", "chg_rate", "percent_change"], default=0.0),
            "high": KiwoomRestBroker._extract_float(payload, ["high", "day_high"], default=0.0),
            "low": KiwoomRestBroker._extract_float(payload, ["low", "day_low"], default=0.0),
            "volume": int(KiwoomRestBroker._extract_float(payload, ["volume", "vol"], default=0.0)),
            "ts": KiwoomRestBroker._extract_str(payload, ["timestamp", "time", "trade_time"], default=""),
        }

    @staticmethod
    def _normalize_ohlcv(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = payload.get("data") or payload.get("rows") or payload.get("candles")
        if not isinstance(rows, list):
            raise LiveBrokerError("Invalid OHLCV payload: expected list")

        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "date": KiwoomRestBroker._extract_str(row, ["date", "dt", "trd_date"]),
                    "open": KiwoomRestBroker._extract_float(row, ["open", "o", "stck_oprc"]),
                    "high": KiwoomRestBroker._extract_float(row, ["high", "h", "stck_hgpr"]),
                    "low": KiwoomRestBroker._extract_float(row, ["low", "l", "stck_lwpr"]),
                    "close": KiwoomRestBroker._extract_float(row, ["close", "c", "stck_clpr"]),
                    "volume": int(KiwoomRestBroker._extract_float(row, ["volume", "v", "acml_vol"], default=0.0)),
                }
            )
        if not out:
            raise LiveBrokerError("No OHLCV rows available")
        return out

    @staticmethod
    def _normalize_positions(payload: Dict[str, Any]) -> List[PositionSnapshot]:
        rows = payload.get("positions") or payload.get("holdings") or payload.get("data")
        if not isinstance(rows, list):
            raise LiveBrokerError("Invalid holdings payload: expected list")

        out: List[PositionSnapshot] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = KiwoomRestBroker._extract_str(row, ["symbol", "code", "pdno"], default="")
            if not symbol:
                continue
            qty = int(KiwoomRestBroker._extract_float(row, ["qty", "quantity", "hldg_qty"], default=0.0))
            avg_price = KiwoomRestBroker._extract_float(row, ["avg_price", "average_price", "pchs_avg_pric"], default=0.0)
            market_price = KiwoomRestBroker._extract_float(row, ["market_price", "price", "prpr"], default=0.0)
            out.append(PositionSnapshot(symbol=symbol, qty=qty, avg_price=avg_price, market_price=market_price))
        return out

    @staticmethod
    def _normalize_order_result(
        payload: Dict[str, Any],
        default_status: str,
        forced_order_id: Optional[str] = None,
    ) -> NormalizedOrderResult:
        order_id = (forced_order_id or "").strip() or KiwoomRestBroker._extract_str(payload, ["order_id", "odno", "id"], default="")
        status = KiwoomRestBroker._extract_str(payload, ["status", "rt_cd"], default=default_status)
        return NormalizedOrderResult(order_id=order_id, status=status, raw=payload)

    @staticmethod
    def _extract_str(payload: Dict[str, Any], keys: List[str], default: Optional[str] = None) -> str:
        for key in keys:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        if default is not None:
            return default
        raise LiveBrokerError(f"Missing required string field among {keys}")

    @staticmethod
    def _extract_float(payload: Dict[str, Any], keys: List[str], default: Optional[float] = None) -> float:
        for key in keys:
            val = payload.get(key)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str) and val.strip():
                try:
                    return float(val.replace(",", ""))
                except ValueError:
                    continue
        if default is not None:
            return float(default)
        raise LiveBrokerError(f"Missing required numeric field among {keys}")

    @staticmethod
    def _translate_error(op: str, exc: Exception) -> LiveBrokerError:
        msg = str(exc)
        if "endpoint mapping not configured" in msg.lower():
            keys = ", ".join(DEFAULT_ENDPOINT_MAPPING.keys())
            return LiveBrokerError(
                f"Live disabled: Kiwoom endpoint mapping missing. Supply KIWOOM_REST_ENDPOINTS_JSON "
                f"or config_endpoints.json with keys: {keys}"
            )
        return LiveBrokerError(f"Live disabled: {op} failed ({msg})")
