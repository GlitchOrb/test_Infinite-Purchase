"""
kiwoom_adapter.py
=================
Kiwoom OpenAPI+ COM/ActiveX adapter via PyQt5 QAxWidget.

Wraps login, TR data requests, order submission, and chejan (fill)
callbacks behind a clean Python interface with:
- request/response correlation (screen-number + rqname registry)
- exponential back-off + retry
- token-bucket rate-limiting
- emergency-stop awareness

**NOTE**: TR codes and field names below use 해외주식 (US equity)
conventions.  Adjust if your Kiwoom build uses different TR IDs.
Placeholders are marked with ``# TODO(kiwoom):``.
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


# ======================================================================= #
#  Data containers
# ======================================================================= #

@dataclass
class TrResponse:
    """Parsed TR response payload."""
    rqname: str
    trcode: str
    rows: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ChejanData:
    """Parsed OMS/fill update from OnReceiveChejanData."""
    gubun: str          # "0" = 주문접수/체결, "1" = 잔고변동
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    qty: int = 0
    price: float = 0.0
    status: str = ""
    raw: Dict[str, str] = field(default_factory=dict)


# ======================================================================= #
#  Rate limiter (token bucket)
# ======================================================================= #

class _TokenBucket:
    """Simple token-bucket: 1 token per ``interval_ms`` milliseconds."""

    def __init__(self, interval_ms: int) -> None:
        self._interval_s = interval_ms / 1000.0
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self._interval_s:
            time.sleep(self._interval_s - elapsed)
        self._last = time.monotonic()


# ======================================================================= #
#  KiwoomAdapter
# ======================================================================= #

class KiwoomAdapter:
    """High-level wrapper around Kiwoom OpenAPI+ QAxWidget.

    Lifecycle
    ---------
    1. ``adapter = KiwoomAdapter(cfg)``
    2. ``adapter.login()``  — blocks until connected (or fails)
    3. Use ``request_tr`` / ``send_order`` / ``get_holdings`` etc.
    4. Register callbacks via ``on_chejan`` for real-time fill updates.

    Parameters
    ----------
    cfg : RuntimeConfig
        Global runtime configuration.
    """

    def __init__(self, cfg: RuntimeConfig) -> None:
        self.cfg = cfg
        self._ocx = QAxWidget(cfg.kiwoom_clsid)
        self._bucket = _TokenBucket(cfg.kiwoom_req_interval_ms)
        self._screen_seq = 1000

        # Pending TR correlation: screen_no → QEventLoop
        self._pending: Dict[str, QEventLoop] = {}
        self._last_tr: Optional[TrResponse] = None

        # Chejan callback
        self._chejan_cb: Optional[Callable[[ChejanData], None]] = None

        # Wire COM events
        self._ocx.OnEventConnect.connect(self._on_event_connect)
        self._ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self._ocx.OnReceiveChejanData.connect(self._on_receive_chejan_data)

        self._connected = False
        self._login_loop: Optional[QEventLoop] = None

    # ------------------------------------------------------------------ #
    #  Login
    # ------------------------------------------------------------------ #

    def login(self, timeout_s: int = 60) -> bool:
        """Initiate COM login and block until OnEventConnect fires.

        Returns True on success, False on timeout or error.
        """
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
        status = "OK" if self._connected else f"ERR({err_code})"
        log.info("OnEventConnect: %s", status)
        if self._login_loop and self._login_loop.isRunning():
            self._login_loop.quit()

    # ------------------------------------------------------------------ #
    #  TR request (generic, with correlation + retry + backoff)
    # ------------------------------------------------------------------ #

    def request_tr(
        self,
        trcode: str,
        rqname: str,
        inputs: Dict[str, str],
        output_fields: List[str],
        repeat: int = 0,
        timeout_s: int = 10,
    ) -> TrResponse:
        """Submit a TR request and block until the response arrives.

        Includes exponential back-off on failure up to ``max_retries``.
        """
        screen = self._next_screen()

        for attempt in range(1, self.cfg.kiwoom_max_retries + 1):
            self._bucket.wait()

            for key, val in inputs.items():
                self._ocx.dynamicCall(
                    "SetInputValue(QString, QString)", key, val,
                )

            self._last_tr = None
            loop = QEventLoop()
            self._pending[screen] = loop

            ret = self._ocx.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname, trcode, repeat, screen,
            )

            if ret != 0:
                log.warning("CommRqData returned %d (attempt %d)", ret, attempt)
                self._pending.pop(screen, None)
                self._backoff_sleep(attempt)
                continue

            QTimer.singleShot(timeout_s * 1000, loop.quit)
            loop.exec_()
            self._pending.pop(screen, None)

            if self._last_tr is not None:
                return self._last_tr

            log.warning("TR timeout (attempt %d/%d)", attempt, self.cfg.kiwoom_max_retries)
            self._backoff_sleep(attempt)

        return TrResponse(rqname=rqname, trcode=trcode, error="max retries exceeded")

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
        # TODO(kiwoom): parse multi-row if needed via GetRepeatCnt / GetCommData
        count = self._ocx.dynamicCall(
            "GetRepeatCnt(QString, QString)", trcode, record,
        )
        rows: List[Dict[str, str]] = []
        # Placeholder: extract fields based on caller's output_fields
        # For now, store a single-row dict of raw data
        self._last_tr = TrResponse(rqname=rqname, trcode=trcode, rows=rows)

        loop = self._pending.get(screen)
        if loop and loop.isRunning():
            loop.quit()

    # ------------------------------------------------------------------ #
    #  Order submission
    # ------------------------------------------------------------------ #

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
        """Submit an order via SendOrder.

        Parameters
        ----------
        side : int
            # TODO(kiwoom): 1=매수, 2=매도 for domestic;
            # 해외주식 uses different TR — adjust accordingly.
        order_type : str
            # TODO(kiwoom): "00"=지정가, "03"=시장가, etc.

        Returns
        -------
        int
            0 on success, non-zero on error.
        """
        self._bucket.wait()
        screen = self._next_screen()

        # TODO(kiwoom): For US equities via Kiwoom, the SendOrder signature
        # may differ (해외주식주문 TR).  Replace the dynamicCall below with
        # the correct wrapper for your account type.
        ret = self._ocx.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            rqname, screen, self.cfg.kiwoom_account, side, symbol,
            qty, price, order_type, original_order_id,
        )
        log.info("SendOrder(%s, %s, qty=%d, px=%d) → %d", symbol, rqname, qty, price, ret)
        return ret

    def cancel_order(self, original_order_id: str, symbol: str, qty: int) -> int:
        """Cancel (or reduce) an existing order."""
        # TODO(kiwoom): cancel order type code may vary; typically side=3
        return self.send_order(
            rqname="CANCEL",
            symbol=symbol,
            side=3,         # TODO(kiwoom): confirm cancel side code
            qty=qty,
            price=0,
            order_type="00",
            original_order_id=original_order_id,
        )

    # ------------------------------------------------------------------ #
    #  Chejan (OMS / fill callback)
    # ------------------------------------------------------------------ #

    def on_chejan(self, callback: Callable[[ChejanData], None]) -> None:
        """Register a callback for real-time fill / order-status updates."""
        self._chejan_cb = callback

    def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str) -> None:
        def _get(fid: int) -> str:
            return self._ocx.dynamicCall(
                "GetChejanData(int)", fid,
            ).strip()

        data = ChejanData(
            gubun=gubun,
            order_id=_get(9203),
            symbol=_get(9001),
            side=_get(905),
            status=_get(913),
            raw={},
        )

        try:
            data.qty = int(_get(900) or "0")
            data.price = float(_get(901) or "0")
        except ValueError:
            pass

        log.info("Chejan[%s]: %s %s qty=%d px=%.2f status=%s",
                 gubun, data.symbol, data.side, data.qty, data.price, data.status)

        if self._chejan_cb:
            self._chejan_cb(data)

    # ------------------------------------------------------------------ #
    #  Account / holdings queries
    # ------------------------------------------------------------------ #

    def get_account_list(self) -> List[str]:
        raw = self._ocx.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        return [a.strip() for a in raw.split(";") if a.strip()]

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Query broker for current positions (for reconcile).

        Returns
        -------
        list[dict]
            Each dict has at least ``symbol``, ``qty``, ``avg_cost``.
        """
        # TODO(kiwoom): Use the correct TR code for 해외주식 잔고조회.
        # Example: opw00018 (domestic) or the overseas equivalent.
        resp = self.request_tr(
            trcode="opw00018",          # TODO(kiwoom): replace
            rqname="RECONCILE_HOLDINGS",
            inputs={"계좌번호": self.cfg.kiwoom_account},
            output_fields=["종목번호", "보유수량", "매입단가"],
        )
        holdings: List[Dict[str, Any]] = []
        for row in resp.rows:
            holdings.append({
                "symbol": row.get("종목번호", "").strip(),
                "qty": int(row.get("보유수량", "0")),
                "avg_cost": float(row.get("매입단가", "0")),
            })
        return holdings

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _next_screen(self) -> str:
        self._screen_seq += 1
        if self._screen_seq > 9999:
            self._screen_seq = 1000
        return str(self._screen_seq)

    def _backoff_sleep(self, attempt: int) -> None:
        wait = min(
            self.cfg.kiwoom_backoff_base_s * (2 ** (attempt - 1)),
            self.cfg.kiwoom_backoff_cap_s,
        )
        log.info("Back-off %.1fs", wait)
        time.sleep(wait)
