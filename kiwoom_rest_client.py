"""키움 REST OpenAPI 클라이언트 — 안전한 기본값 포함."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

import requests

DEFAULT_ENDPOINT_MAPPING: Dict[str, str] = {
    "auth_token": "/oauth2/token",
    "refresh_token": "/oauth2/token",
    "quote": "/api/overseas-stock/quote",
    "daily": "/api/overseas-stock/daily",
    "account_balance": "/api/account/balance",
    "holdings": "/api/account/holdings",
    "place_order": "/api/overseas-stock/order",
    "cancel_order": "/api/overseas-stock/order/cancel",
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
    def __init__(
        self,
        base_url: str,
        endpoint_mapping: Optional[Dict[str, str]] = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session: Optional[RestSession] = None
        self._app_key = ""
        self._app_secret = ""
        self._http = requests.Session()
        self._limiter = _RateLimiter(min_interval_s=0.35)
        self._endpoints = self._resolve_endpoint_mapping(endpoint_mapping)

    def create_session(self, app_key: str, app_secret: str) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        # au10001 공식 스펙: grant_type + appkey + secretkey
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        }
        data = self._request("POST", "auth_token", json_body=payload, auth_required=False)

        # 공식 응답 필드: token, token_type, expires_dt
        access_token = self._expect_token(data)
        token_type = self._expect_str(data, ["token_type"], default="Bearer")
        expires_at = self._parse_expires_dt(data)
        refresh = self._optional_str(data, ["refresh_token"])

        self._session = RestSession(
            access_token=access_token,
            token_type=token_type,
            expires_at=expires_at,
            refresh_token=refresh,
        )

    def refresh_token(self) -> None:
        if not self._session or not self._session.refresh_token:
            raise RuntimeError("리프레시 토큰을 사용할 수 없습니다")
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._app_secret,
            "refresh_token": self._session.refresh_token,
        }
        data = self._request("POST", "refresh_token", json_body=payload, auth_required=False)
        access_token = self._expect_token(data)
        token_type = self._expect_str(data, ["token_type"], default="Bearer")
        expires_at = self._parse_expires_dt(data)
        self._session.access_token = access_token
        self._session.token_type = token_type
        self._session.expires_at = expires_at

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

        path = self._endpoints.get(endpoint_key)
        if not path:
            raise RuntimeError(f"엔드포인트 매핑이 없습니다: {endpoint_key}")

        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json; charset=UTF-8"}
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

                # 4xx: 서버 에러 메시지를 추출하여 사용자 친화적 에러 표시
                if resp.status_code >= 400:
                    server_msg = self._extract_server_error_from_response(resp)
                    hint = "앱키/시크릿 또는 IP 등록/권한을 확인해주세요."
                    if server_msg:
                        raise RuntimeError(f"토큰 발급 실패: {server_msg} ({hint})")
                    else:
                        raise RuntimeError(f"HTTP {resp.status_code}: {hint}")

                payload = resp.json()
                self._ensure_json_object(payload)
                return payload
            except (requests.Timeout, requests.ConnectionError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.6 * (2 ** attempt))
                else:
                    raise RuntimeError(f"REST 요청 실패: {endpoint_key}") from last_error

        raise RuntimeError(f"REST 요청 실패: {endpoint_key}")

    def _ensure_token(self) -> None:
        if not self._session:
            raise RuntimeError("REST 세션이 생성되지 않았습니다")
        if time.time() >= self._session.expires_at - 10:
            self.refresh_token()

    @staticmethod
    def _ensure_json_object(payload: Any) -> None:
        if not isinstance(payload, dict):
            raise RuntimeError("잘못된 JSON 응답: 객체가 필요합니다")

    @staticmethod
    def _expect_str(payload: Dict[str, Any], keys: list[str], default: Optional[str] = None) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if default is not None:
            return default
        raise RuntimeError(f"응답에서 필수 필드를 찾을 수 없습니다: {keys}")

    @staticmethod
    def _expect_token(payload: Dict[str, Any]) -> str:
        """토큰 응답에서 access token을 추출.

        공식 필드: 'token'. 폴백: 'access_token'.
        토큰이 없으면 서버 에러 메시지를 추출하여 표시.
        """
        for key in ("token", "access_token"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        # 토큰이 없으면 서버 에러 메시지를 확인
        server_msg = KiwoomRestClient._extract_server_error(payload)
        if server_msg:
            raise RuntimeError(f"토큰 발급 실패: {server_msg}")
        raise RuntimeError(
            "토큰 발급 실패: 서버 응답에 토큰이 포함되지 않았습니다. "
            "앱키/시크릿 또는 IP 등록/권한을 확인해주세요."
        )

    @staticmethod
    def _parse_expires_dt(payload: Dict[str, Any]) -> float:
        """expires_dt (문자열 datetime)을 epoch timestamp로 변환.

        공식 응답 필드: expires_dt (예: '2026-02-24 01:05:25')
        파싱 실패 시 현재 시각 + 24시간으로 폴백.
        """
        expires_dt = payload.get("expires_dt")
        if isinstance(expires_dt, str) and expires_dt.strip():
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y%m%d%H%M%S",
            ):
                try:
                    dt = datetime.strptime(expires_dt.strip(), fmt)
                    return dt.timestamp()
                except ValueError:
                    continue

        # expires_in 숫자 폴백 (비공식이지만 안전장치)
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            return time.time() + float(expires_in)

        # 최종 폴백: 24시간
        return time.time() + 86400.0

    @staticmethod
    def _extract_server_error(payload: Dict[str, Any]) -> str:
        """서버 에러 응답에서 사용자에게 보여줄 메시지를 추출."""
        # 키움 REST API 에러 응답 필드들 탐색
        for key in ("return_msg", "msg1", "error_description", "error", "msg_cd", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                # return_code가 있으면 함께 표시
                rc = payload.get("return_code", "")
                prefix = f"[{rc}] " if rc else ""
                return f"{prefix}{value.strip()}"
        return ""

    @staticmethod
    def _extract_server_error_from_response(resp: requests.Response) -> str:
        """HTTP Response 객체에서 서버 에러 메시지를 추출."""
        try:
            data = resp.json()
            if isinstance(data, dict):
                return KiwoomRestClient._extract_server_error(data)
        except (ValueError, AttributeError):
            pass
        return ""

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
        raise RuntimeError(f"응답에서 숫자 필드를 찾을 수 없습니다: {keys}")

    def _resolve_endpoint_mapping(self, provided: Optional[Dict[str, str]]) -> Dict[str, str]:
        if provided is not None:
            return dict(provided)
        raw = os.environ.get("KIWOOM_REST_ENDPOINTS_JSON", "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    merged = dict(DEFAULT_ENDPOINT_MAPPING)
                    merged.update(data)
                    return merged
            except json.JSONDecodeError:
                pass
        return dict(DEFAULT_ENDPOINT_MAPPING)
