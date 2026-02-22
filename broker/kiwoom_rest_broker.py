from __future__ import annotations

from typing import Any, Dict, List, Optional

from broker.base import AccountSnapshot, BrokerBase, PositionSnapshot, Quote
from kiwoom_rest_client import KiwoomRestClient


class KiwoomRestBroker(BrokerBase):
    """Live trading broker implementation backed by Kiwoom REST OpenAPI."""

    def __init__(self, rest_client: KiwoomRestClient, account_no: str) -> None:
        self._client = rest_client
        self._account_no = account_no.strip()
        if not self._account_no:
            raise RuntimeError("Account number is required for live trading")

    @property
    def name(self) -> str:
        return "KiwoomRestBroker"

    @property
    def is_live(self) -> bool:
        return True

    def get_quote(self, symbol: str) -> Quote:
        payload = self._client.get_quote(symbol)
        price = self._extract_float(payload, ["price", "last", "close", "current_price"])
        change_pct = self._extract_float(payload, ["change_pct", "chg_rate", "percent_change"], default=0.0)
        high = self._extract_float(payload, ["high", "day_high"], default=0.0)
        low = self._extract_float(payload, ["low", "day_low"], default=0.0)
        volume = int(self._extract_float(payload, ["volume", "vol"], default=0.0))
        timestamp = self._extract_str(payload, ["timestamp", "time", "trade_time"], default="")
        return Quote(symbol=symbol, price=price, change_pct=change_pct, high=high, low=low, volume=volume, timestamp=timestamp)

    def get_ohlcv(self, symbol: str, lookback_days: int) -> List[Dict[str, Any]]:
        payload = self._client.get_daily(symbol, lookback_days)
        rows = payload.get("data") or payload.get("rows") or payload.get("candles")
        if not isinstance(rows, list):
            raise RuntimeError("Invalid OHLCV payload")

        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            date = self._extract_str(row, ["date", "dt", "trd_date"])
            open_px = self._extract_float(row, ["open", "o", "stck_oprc"])
            high_px = self._extract_float(row, ["high", "h", "stck_hgpr"])
            low_px = self._extract_float(row, ["low", "l", "stck_lwpr"])
            close_px = self._extract_float(row, ["close", "c", "stck_clpr"])
            volume = int(self._extract_float(row, ["volume", "v", "acml_vol"], default=0.0))
            out.append({
                "date": date,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": volume,
            })
        if not out:
            raise RuntimeError("No OHLCV rows available")
        return out

    def get_account(self) -> AccountSnapshot:
        payload = self._client.get_account_balance(self._account_no)
        cash = self._extract_float(payload, ["cash", "available_cash", "dnca_tot_amt"])
        equity = self._extract_float(payload, ["equity", "total_equity", "tot_evlu_amt"], default=cash)
        buying_power = self._extract_float(payload, ["buying_power", "ord_psbl_cash", "buyable_cash"], default=cash)
        return AccountSnapshot(cash=cash, equity=equity, buying_power=buying_power)

    def get_positions(self) -> List[PositionSnapshot]:
        payload = self._client.get_holdings(self._account_no)
        rows = payload.get("positions") or payload.get("holdings") or payload.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Invalid holdings payload")

        out: List[PositionSnapshot] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = self._extract_str(row, ["symbol", "code", "pdno"])
            qty = int(self._extract_float(row, ["qty", "quantity", "hldg_qty"], default=0.0))
            avg_price = self._extract_float(row, ["avg_price", "average_price", "pchs_avg_pric"], default=0.0)
            market_price = self._extract_float(row, ["market_price", "price", "prpr"], default=0.0)
            if symbol:
                out.append(PositionSnapshot(symbol=symbol, qty=qty, avg_price=avg_price, market_price=market_price))
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
        if side.upper() not in {"BUY", "SELL"}:
            raise RuntimeError("Order side must be BUY or SELL")
        if order_type.upper() not in {"MARKET", "LIMIT"}:
            raise RuntimeError("Order type must be MARKET or LIMIT")
        if order_type.upper() == "LIMIT" and (limit_price is None or limit_price <= 0):
            raise RuntimeError("Limit price is required for LIMIT orders")

        payload: Dict[str, Any] = {
            "account_no": self._account_no,
            "symbol": symbol,
            "side": side.upper(),
            "qty": qty,
            "order_type": order_type.upper(),
        }
        if limit_price is not None:
            payload["limit_price"] = float(limit_price)
        if metadata:
            payload["metadata"] = metadata

        result = self._client.place_order(payload)
        order_id = self._extract_str(result, ["order_id", "odno", "id"], default="")
        status = self._extract_str(result, ["status", "rt_cd"], default="SUBMITTED")
        return {"order_id": order_id, "status": status, "raw": result}

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        if not order_id:
            raise RuntimeError("order_id is required for cancellation")
        payload: Dict[str, Any] = {"account_no": self._account_no, "order_id": order_id}
        if symbol:
            payload["symbol"] = symbol
        result = self._client.cancel_order(payload)
        status = self._extract_str(result, ["status", "rt_cd"], default="CANCEL_REQUESTED")
        return {"order_id": order_id, "status": status, "raw": result}

    @staticmethod
    def _extract_str(payload: Dict[str, Any], keys: List[str], default: Optional[str] = None) -> str:
        for key in keys:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        if default is not None:
            return default
        raise RuntimeError(f"Missing required string field among {keys}")

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
        raise RuntimeError(f"Missing required numeric field among {keys}")
