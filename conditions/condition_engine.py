from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable, Dict, List, Optional

from broker.base import BrokerBase, Quote
from conditions.condition_models import (
    ConditionAction,
    ConditionOperator,
    ConditionOrder,
    ConditionOrderType,
    ConditionStatus,
    TriggerResult,
)


class ConditionEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        get_emergency_stop: Callable[[], bool],
        set_emergency_stop: Callable[[bool], None],
        alert: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.conn = conn
        self.get_emergency_stop = get_emergency_stop
        self.set_emergency_stop = set_emergency_stop
        self.alert = alert

    def create_condition(
        self,
        mode: str,
        symbol: str,
        operator: str,
        trigger_price: float,
        action: str,
        order_type: str,
        qty: int,
        limit_price: Optional[float] = None,
    ) -> int:
        if qty <= 0:
            raise RuntimeError("Condition qty must be positive")
        if trigger_price <= 0:
            raise RuntimeError("Trigger price must be positive")
        op = ConditionOperator(operator)
        act = ConditionAction(action)
        typ = ConditionOrderType(order_type)
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "INSERT INTO condition_orders ("
            "mode, symbol, operator, trigger_price, action, order_type, qty, limit_price, status, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mode, symbol, op.value, trigger_price, act.value, typ.value, qty, limit_price, ConditionStatus.PENDING.value, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def cancel_condition(self, condition_id: int) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "UPDATE condition_orders SET status=?, completed_at=? WHERE id=? AND status=?",
            (ConditionStatus.CANCELLED.value, now, condition_id, ConditionStatus.PENDING.value),
        )
        self.conn.commit()

    def list_active(self, mode: str) -> List[ConditionOrder]:
        rows = self.conn.execute(
            "SELECT * FROM condition_orders WHERE mode=? AND status=? ORDER BY id DESC",
            (mode, ConditionStatus.PENDING.value),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def list_history(self, mode: str, limit: int = 200) -> List[ConditionOrder]:
        rows = self.conn.execute(
            "SELECT * FROM condition_orders WHERE mode=? AND status!=? ORDER BY id DESC LIMIT ?",
            (mode, ConditionStatus.PENDING.value, limit),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def evaluate_tick(self, mode: str, quote: Quote, broker: BrokerBase) -> List[TriggerResult]:
        if self.get_emergency_stop():
            return []

        pending = self.conn.execute(
            "SELECT * FROM condition_orders WHERE mode=? AND symbol=? AND status=? ORDER BY id ASC",
            (mode, quote.symbol, ConditionStatus.PENDING.value),
        ).fetchall()

        results: List[TriggerResult] = []
        for row in pending:
            condition = self._row_to_model(row)
            if not self._is_triggered(condition, quote.price):
                continue
            results.append(self._trigger_once(condition, quote, broker))
        return results

    def _trigger_once(self, condition: ConditionOrder, quote: Quote, broker: BrokerBase) -> TriggerResult:
        now = datetime.utcnow().isoformat()

        cur = self.conn.execute(
            "UPDATE condition_orders SET status=?, triggered_at=? "
            "WHERE id=? AND status=?",
            (ConditionStatus.TRIGGERED.value, now, condition.id, ConditionStatus.PENDING.value),
        )
        if cur.rowcount != 1:
            return TriggerResult(
                condition_id=condition.id,
                success=False,
                status=ConditionStatus.FAILED,
                reason="Already processed by concurrent tick",
                triggered_at=now,
            )

        try:
            order_type = condition.order_type.value
            limit_price = condition.limit_price
            if order_type == ConditionOrderType.MARKET.value:
                limit_price = None
            result = broker.place_order(
                symbol=condition.symbol,
                side=condition.action.value,
                qty=condition.qty,
                order_type=order_type,
                limit_price=limit_price,
                metadata={"source": "CONDITION", "condition_id": condition.id},
            )
            broker_order_id = str(result.get("order_id", ""))
            status = str(result.get("status", "")).upper()
            final_status = ConditionStatus.FILLED if status in {"FILLED", "SUBMITTED", "OPEN"} else ConditionStatus.FAILED
            self.conn.execute(
                "UPDATE condition_orders SET status=?, completed_at=?, broker_order_id=?, fail_reason=? WHERE id=?",
                (
                    final_status.value,
                    datetime.utcnow().isoformat(),
                    broker_order_id,
                    None if final_status == ConditionStatus.FILLED else f"Order status={status}",
                    condition.id,
                ),
            )
            self.conn.commit()
            return TriggerResult(
                condition_id=condition.id,
                success=final_status == ConditionStatus.FILLED,
                status=final_status,
                broker_order_id=broker_order_id,
                reason=None if final_status == ConditionStatus.FILLED else f"Order status={status}",
                triggered_at=now,
            )
        except Exception as exc:
            self.conn.execute(
                "UPDATE condition_orders SET status=?, completed_at=?, fail_reason=? WHERE id=?",
                (ConditionStatus.FAILED.value, datetime.utcnow().isoformat(), str(exc), condition.id),
            )
            self.conn.commit()
            self.set_emergency_stop(True)
            if self.alert:
                self.alert(f"🚨 CONDITION_TRIGGER failed id={condition.id}: {exc}")
            return TriggerResult(
                condition_id=condition.id,
                success=False,
                status=ConditionStatus.FAILED,
                reason=str(exc),
                triggered_at=now,
            )

    @staticmethod
    def _is_triggered(condition: ConditionOrder, last_price: float) -> bool:
        if condition.operator == ConditionOperator.GTE:
            return last_price >= condition.trigger_price
        return last_price <= condition.trigger_price

    @staticmethod
    def _row_to_model(row: sqlite3.Row | tuple) -> ConditionOrder:
        cols = [
            "id", "mode", "symbol", "operator", "trigger_price", "action", "order_type", "qty", "limit_price",
            "status", "created_at", "triggered_at", "completed_at", "broker_order_id", "fail_reason",
        ]
        if isinstance(row, sqlite3.Row):
            d: Dict[str, object] = {k: row[k] for k in cols}
        else:
            d = dict(zip(cols, row))
        return ConditionOrder(
            id=int(d["id"]),
            mode=str(d["mode"]),
            symbol=str(d["symbol"]),
            operator=ConditionOperator(str(d["operator"])),
            trigger_price=float(d["trigger_price"]),
            action=ConditionAction(str(d["action"])),
            order_type=ConditionOrderType(str(d["order_type"])),
            qty=int(d["qty"]),
            limit_price=float(d["limit_price"]) if d["limit_price"] is not None else None,
            status=ConditionStatus(str(d["status"])),
            created_at=str(d["created_at"]),
            triggered_at=str(d["triggered_at"]) if d["triggered_at"] else None,
            completed_at=str(d["completed_at"]) if d["completed_at"] else None,
            broker_order_id=str(d["broker_order_id"]) if d["broker_order_id"] else None,
            fail_reason=str(d["fail_reason"]) if d["fail_reason"] else None,
        )
