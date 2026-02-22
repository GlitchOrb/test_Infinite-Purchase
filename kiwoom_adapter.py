"""
kiwoom_adapter.py
=================
Kiwoom OpenAPI+ COM/ActiveX adapter via PyQt5 QAxWidget.

Production notes:
- Uses Kiwoom overseas TR codes required by runtime
  * quote: HHDFS00000300
  * daily candles: HHDFS76410000
  * holdings/cash: TTTT3012R
- Raises explicit exceptions for session-invalid and critical parse failures.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop, QTimer

from config import RuntimeConfig

log = logging.getLogger(__name__)


class KiwoomSessionInvalidError(RuntimeError):
    """Raised when Kiwoom OpenAPI+ session is no longer valid."""


class KiwoomTrError(RuntimeError):
    """Raised when TR request/response is invalid for trading decisions."""


@dataclass
class TrResponse:
    rqname: str
    trcode: str
    rows: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ChejanData:
    gubun: str
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    qty: int = 0
    price: float = 0.0
    status: str = ""
    raw: Dict[str, str] = field(default_factory=dict)


class _TokenBucket:
    def __init__(self, interval_ms: int) -> None:
        self._interval_s = interval_ms / 1000.0
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self._interval_s:
            time.sleep(self._interval_s - elapsed)
        self._last = time.monotonic()


class KiwoomAdapter:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self.cfg = cfg
        self._ocx = QAxWidget(cfg.kiwoom_clsid)
        self._bucket = _TokenBucket(cfg.kiwoom_req_interval_ms)
        self._screen_seq = 1000

        self._pending: Dict[str, QEventLoop] = {}
        self._pending_fields: Dict[str, List[str]] = {}
        self._last_tr: Optional[TrResponse] = None

        self._chejan_cb: Optional[Callable[[ChejanData], None]] = None

        self._ocx.OnEventConnect.connect(self._on_event_connect)
        self._ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self._ocx.OnReceiveChejanData.connect(self._on_receive_chejan_data)

        self._connected = False
        self._login_loop: Optional[QEventLoop] = None

    def login(self, timeout_s: int = 60) -> bool:
        log.info("Kiwoom login initiated …")
        self._login_loop = QEventLoop()
        self._ocx.dynamicCall("CommConnect()")
        QTimer.singleShot(timeout_s * 1000, self._login_loop.quit)
        self._login_loop.exec_()
        self._login_loop = None
        if not self._connected:
            log.error("Kiwoom login timed out or failed")
        return self._connected

    def _on_event_connect(self, err_code: int) -> None:
        self._connected = err_code == 0
        if self._login_loop and self._login_loop.isRunning():
            self._login_loop.quit()

    def session_is_valid(self) -> bool:
        try:
            state = int(self._ocx.dynamicCall("GetConnectState()") or 0)
            if state != 1:
                return False
            if not self.get_account_list():
                return False
            return True
        except Exception:
            return False

    def request_tr(
        self,
        trcode: str,
        rqname: str,
        inputs: Dict[str, str],
        output_fields: List[str],
        repeat: int = 0,
        timeout_s: int = 10,
    ) -> TrResponse:
        if not self.session_is_valid():
            raise KiwoomSessionInvalidError("Kiwoom session invalid before TR request")

        screen = self._next_screen()
        for attempt in range(1, self.cfg.kiwoom_max_retries + 1):
            self._bucket.wait()

            for key, val in inputs.items():
                self._ocx.dynamicCall("SetInputValue(QString, QString)", key, str(val))

            self._last_tr = None
            loop = QEventLoop()
            self._pending[screen] = loop
            self._pending_fields[screen] = output_fields

            ret = self._ocx.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname, trcode, repeat, screen,
            )
            if ret != 0:
                self._pending.pop(screen, None)
                self._pending_fields.pop(screen, None)
                self._backoff_sleep(attempt)
                continue

            QTimer.singleShot(timeout_s * 1000, loop.quit)
            loop.exec_()
            self._pending.pop(screen, None)
            self._pending_fields.pop(screen, None)

            if self._last_tr is not None:
                if self._last_tr.error:
                    self._backoff_sleep(attempt)
                    continue
                return self._last_tr

            self._backoff_sleep(attempt)

        raise KiwoomTrError(f"TR failed after retries: {trcode}/{rqname}")

    def _on_receive_tr_data(
        self,
        screen: str,
        rqname: str,
        trcode: str,
        record: str,
        prev_next: str,
        _unused1: int = 0,
        _unused2: str = "",
        _unused3: str = "",
        _unused4: str = "",
    ) -> None:
        fields = self._pending_fields.get(screen, [])
        rows: List[Dict[str, str]] = []

        repeat_cnt = int(self._ocx.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname) or 0)
        if repeat_cnt <= 0:
            repeat_cnt = 1

        for i in range(repeat_cnt):
            row: Dict[str, str] = {}
            for field_name in fields:
                val = self._ocx.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    trcode,
                    rqname,
                    i,
                    field_name,
                )
                row[field_name] = (val or "").strip()
            rows.append(row)

        self._last_tr = TrResponse(rqname=rqname, trcode=trcode, rows=rows)
        loop = self._pending.get(screen)
        if loop and loop.isRunning():
            loop.quit()

    def send_order(
        self,
        rqname: str,
        symbol: str,
        side: int,
        qty: int,
        price: int,
        order_type: str,
        original_order_id: str = "",
    ) -> int:
        if not self.session_is_valid():
            raise KiwoomSessionInvalidError("Kiwoom session invalid before order submission")

        self._bucket.wait()
        screen = self._next_screen()
        ret = self._ocx.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            rqname, screen, self.cfg.kiwoom_account, side, symbol,
            qty, price, order_type, original_order_id,
        )
        return int(ret)

    def cancel_order(self, original_order_id: str, symbol: str, qty: int) -> int:
        return self.send_order(
            rqname="CANCEL",
            symbol=symbol,
            side=3,
            qty=qty,
            price=0,
            order_type="00",
            original_order_id=original_order_id,
        )

    def on_chejan(self, callback: Callable[[ChejanData], None]) -> None:
        self._chejan_cb = callback

    def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str) -> None:
        def _get(fid: int) -> str:
            return (self._ocx.dynamicCall("GetChejanData(int)", fid) or "").strip()

        data = ChejanData(
            gubun=gubun,
            order_id=_get(9203),
            symbol=_get(9001),
            side=_get(905),
            status=_get(913),
            raw={},
        )
        data.qty = int(self._to_int(_get(900)))
        data.price = self._to_float(_get(901))

        if self._chejan_cb:
            self._chejan_cb(data)

    def get_account_list(self) -> List[str]:
        raw = self._ocx.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        return [a.strip() for a in str(raw or "").split(";") if a.strip()]

    def get_holdings(self) -> List[Dict[str, Any]]:
        payload = self.get_overseas_holdings_and_cash()
        return payload["holdings"]

    def get_overseas_quote(self, symbol: str) -> float:
        resp = self.request_tr(
            trcode="HHDFS00000300",
            rqname=f"OVERSEAS_QUOTE_{symbol}",
            inputs={"종목코드": symbol},
            output_fields=["현재가", "last", "Last", "체결가"],
        )
        if not resp.rows:
            raise KiwoomTrError(f"No quote rows for {symbol}")

        row = resp.rows[0]
        for key in ["현재가", "last", "Last", "체결가"]:
            if key in row and row[key].strip():
                px = abs(self._to_float(row[key]))
                if px > 0:
                    return px
        raise KiwoomTrError(f"Unable to parse overseas quote for {symbol}: {row}")

    def get_overseas_daily(self, symbol: str, lookback_days: int) -> List[Dict[str, Any]]:
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")

        resp = self.request_tr(
            trcode="HHDFS76410000",
            rqname=f"OVERSEAS_DAILY_{symbol}",
            inputs={"종목코드": symbol, "조회건수": str(lookback_days)},
            output_fields=["일자", "시가", "고가", "저가", "종가", "거래량", "date", "open", "high", "low", "close", "volume"],
        )
        candles: List[Dict[str, Any]] = []
        for row in resp.rows:
            date = row.get("일자", "") or row.get("date", "")
            close = row.get("종가", "") or row.get("close", "")
            open_ = row.get("시가", "") or row.get("open", "")
            high = row.get("고가", "") or row.get("high", "")
            low = row.get("저가", "") or row.get("low", "")
            vol = row.get("거래량", "") or row.get("volume", "")
            if not date or not close:
                continue
            candles.append(
                {
                    "date": date.strip(),
                    "open": self._to_float(open_),
                    "high": self._to_float(high),
                    "low": self._to_float(low),
                    "close": self._to_float(close),
                    "volume": int(self._to_int(vol)),
                }
            )

        if len(candles) < min(lookback_days, 250):
            raise KiwoomTrError(
                f"Insufficient daily candles for {symbol}: {len(candles)} < {min(lookback_days, 250)}"
            )
        return candles

    def get_overseas_holdings_and_cash(self) -> Dict[str, Any]:
        resp = self.request_tr(
            trcode="TTTT3012R",
            rqname="OVERSEAS_HOLDINGS_CASH",
            inputs={"계좌번호": self.cfg.kiwoom_account},
            output_fields=[
                "종목코드", "보유수량", "평균단가", "평균매입가", "매입단가",
                "주문가능현금", "주문가능금액", "예수금", "D+2예수금", "cash_available",
            ],
        )

        holdings: List[Dict[str, Any]] = []
        cash_candidates: List[float] = []

        for row in resp.rows:
            symbol = (row.get("종목코드", "") or "").strip()
            qty = int(self._to_int(row.get("보유수량", "0")))
            avg = 0.0
            for k in ["평균단가", "평균매입가", "매입단가"]:
                if row.get(k):
                    avg = self._to_float(row[k])
                    break
            if symbol and qty > 0:
                holdings.append({"symbol": symbol, "qty": qty, "avg_cost": avg})

            for ck in ["주문가능현금", "주문가능금액", "예수금", "D+2예수금", "cash_available"]:
                if ck in row and row[ck].strip():
                    cash_candidates.append(self._to_float(row[ck]))

        available_cash = max(cash_candidates) if cash_candidates else 0.0
        if available_cash <= 0:
            raise KiwoomTrError("Unable to parse available cash from TTTT3012R response")

        return {"holdings": holdings, "available_cash": available_cash}

    def _next_screen(self) -> str:
        self._screen_seq += 1
        if self._screen_seq > 9999:
            self._screen_seq = 1000
        return str(self._screen_seq)

    def _backoff_sleep(self, attempt: int) -> None:
        wait = min(self.cfg.kiwoom_backoff_base_s * (2 ** (attempt - 1)), self.cfg.kiwoom_backoff_cap_s)
        time.sleep(wait)

    @staticmethod
    def _to_float(raw: Any) -> float:
        txt = str(raw or "").replace(",", "").strip()
        if txt in {"", "-", "+"}:
            return 0.0
        try:
            return float(txt)
        except ValueError:
            return float(txt.replace("+", ""))

    @staticmethod
    def _to_int(raw: Any) -> int:
        txt = str(raw or "").replace(",", "").strip()
        if txt in {"", "-", "+"}:
            return 0
        try:
            return int(float(txt))
        except ValueError:
            return int(float(txt.replace("+", "")))
