"""Kiwoom REST OpenAPI client with strict validation and fail-fast endpoint checks."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

REQUIRED_OFFICIAL_VALUES = {
    "auth_token": None,
    "refresh_token": None,
    "quote": None,
    "daily": None,
    "account_balance": None,
    "holdings": None,
    "place_order": None,
    "cancel_order": None,
}


@dataclass
class RestSession:
    access_token: str
    token_type: str
    expires_at: float
    refresh_token: Optional[str] = None


class _RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last_call
            if delta < self._min_interval_s:
                time.sleep(self._min_interval_s - delta)
            self._last_call = time.monotonic()


class KiwoomRestClient:
    def __init__(self, base_url: str, endpoint_mapping: Optional[Dict[str, str]] = None, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session: Optional[RestSession] = None
        self._app_key = ""
        self._app_secret = ""
        self._http = requests.Session()
        self._limiter = _RateLimiter(min_interval_s=0.35)
        self._endpoints = self._resolve_endpoint_mapping(endpoint_mapping)
        self._validate_endpoint_mapping(self._endpoints)

    def create_session(self, app_key: str, app_secret: str) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        payload = {"appkey": app_key, "appsecret": app_secret}
        data = self._request("POST", "auth_token", json_body=payload, auth_required=False)

        access_token = self._expect_str(data, ["access_token", "token"])
        token_type = self._expect_str(data, ["token_type"], default="Bearer")
        expires_in = self._expect_num(data, ["expires_in"], default=3600)
        refresh = self._optional_str(data, ["refresh_token"])

        self._session = RestSession(
            access_token=access_token,
            token_type=token_type,
            expires_at=time.time() + float(expires_in),
            refresh_token=refresh,
        )

    def refresh_token(self) -> None:
        if not self._session or not self._session.refresh_token:
            raise RuntimeError("Refresh token not available")
        payload = {
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "refresh_token": self._session.refresh_token,
        }
        data = self._request("POST", "refresh_token", json_body=payload, auth_required=False)
        access_token = self._expect_str(data, ["access_token", "token"])
        token_type = self._expect_str(data, ["token_type"], default="Bearer")
        expires_in = self._expect_num(data, ["expires_in"], default=3600)
        self._session.access_token = access_token
        self._session.token_type = token_type
        self._session.expires_at = time.time() + float(expires_in)

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        data = self._request("GET", "quote", params={"symbol": symbol})
        self._ensure_json_object(data)
        return data

    def get_daily(self, symbol: str, lookback_days: int) -> Dict[str, Any]:
        data = self._request("GET", "daily", params={"symbol": symbol, "lookback_days": str(lookback_days)})
        self._ensure_json_object(data)
        return data

    def get_account_balance(self, account_no: str) -> Dict[str, Any]:
        data = self._request("GET", "account_balance", params={"account_no": account_no})
        self._ensure_json_object(data)
        return data

    def get_holdings(self, account_no: str) -> Dict[str, Any]:
        data = self._request("GET", "holdings", params={"account_no": account_no})
        self._ensure_json_object(data)
        return data

    def place_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self._request("POST", "place_order", json_body=payload)
        self._ensure_json_object(data)
        return data

    def cancel_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self._request("POST", "cancel_order", json_body=payload)
        self._ensure_json_object(data)
        return data

    def clear_session(self) -> None:
        self._session = None
        self._app_key = ""
        self._app_secret = ""

    def _request(
        self,
        method: str,
        endpoint_key: str,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        auth_required: bool = True,
    ) -> Dict[str, Any]:
        if auth_required:
            self._ensure_token()

        url = f"{self.base_url}{self._endpoints[endpoint_key]}"
        headers = {"Content-Type": "application/json"}
        if self._session and auth_required:
            headers["Authorization"] = f"{self._session.token_type} {self._session.access_token}"

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                self._limiter.wait()
                resp = self._http.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self.timeout_s,
                )
                if resp.status_code >= 500:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                payload = resp.json()
                self._ensure_json_object(payload)
                return payload
            except (requests.Timeout, requests.ConnectionError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.6 * (2 ** attempt))
                else:
                    raise RuntimeError(f"REST request failed: {endpoint_key}") from last_error

        raise RuntimeError(f"REST request failed: {endpoint_key}")

    def _ensure_token(self) -> None:
        if not self._session:
            raise RuntimeError("REST session not created")
        if time.time() >= self._session.expires_at - 10:
            self.refresh_token()

    @staticmethod
    def _ensure_json_object(payload: Any) -> None:
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid JSON payload: expected object")

    @staticmethod
    def _expect_str(payload: Dict[str, Any], keys: list[str], default: Optional[str] = None) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if default is not None:
            return default
        raise RuntimeError(f"Invalid JSON payload: missing {keys}")

    @staticmethod
    def _optional_str(payload: Dict[str, Any], keys: list[str]) -> Optional[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _expect_num(payload: Dict[str, Any], keys: list[str], default: Optional[float] = None) -> float:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value.strip():
                try:
                    return float(value)
                except ValueError:
                    continue
        if default is not None:
            return float(default)
        raise RuntimeError(f"Invalid JSON payload: missing numeric {keys}")

    def _resolve_endpoint_mapping(self, provided: Optional[Dict[str, str]]) -> Dict[str, str]:
        if provided is not None:
            return dict(provided)
        raw = os.environ.get("KIWOOM_REST_ENDPOINTS_JSON", "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return dict(REQUIRED_OFFICIAL_VALUES)

    @staticmethod
    def _validate_endpoint_mapping(mapping: Dict[str, str]) -> None:
        for key in REQUIRED_OFFICIAL_VALUES:
            val = mapping.get(key)
            if not isinstance(val, str) or not val.strip():
                raise RuntimeError("Kiwoom REST endpoint mapping not configured")
