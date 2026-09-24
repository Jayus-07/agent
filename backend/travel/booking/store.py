"""travel/booking/store.py — Booking PG 持久层（STOP L3，G13/G23/G34）

**防并发靠 DB 约束与条件 UPDATE，不靠 Python if-exists**：
  - 订单创建 INSERT ON CONFLICT (booking_intent_id) DO NOTHING——并发双击
    只有一行胜出，败者读回既有行（同一业务意图）；
  - 状态转换 = 条件 UPDATE（WHERE status=期望值）CAS + status_version 单调
    递增，并发抢占败者 rowcount=0（StaleTransition）；
  - booking_events 与状态转换**同一事务**追加（状态+事件原子提交）；
  - webhook inbox (provider,event_id) 唯一——重复投递 INSERT 冲突即去重。

行统一转 JSON-safe dict（Decimal→str，timestamptz→ISO）。
connection_factory 可注入（测试/评测换隔离库；生产=MEMORY_DB_CONFIG）。
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from backend.shared.logger import logger

_SCHEMA = "travel"


class StaleTransition(RuntimeError):
    """条件 UPDATE 未命中：并发已被他人抢先或期望状态已变化（调用方重读）。"""


def default_connection_factory():
    import psycopg

    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    return psycopg.connect(
        f"postgresql://{c['user']}:{c['password']}"
        f"@{c['host']}:{c['port']}/{c['dbname']}")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _num(value: Any) -> str | None:
    if value is None:
        return None
    from backend.travel.booking.identity import canonical_amount

    return canonical_amount(value)


def _row_to_quote(row: tuple, cols: tuple[str, ...]) -> dict:
    data = dict(zip(cols, row))
    for key in ("price_amount", "taxes", "fees"):
        data[key] = _num(data[key])
    for key in ("provider_observed_at", "quote_created_at",
                "provider_expires_at", "internal_expires_at",
                "created_at", "updated_at"):
        data[key] = _iso(data[key])
    return data


_QUOTE_COLS = (
    "quote_id", "tenant_id", "user_id", "commerce_type", "provider",
    "provider_offer_id", "offer_fingerprint", "booking_facts",
    "price_amount", "currency", "taxes", "fees", "tax_inclusion",
    "availability", "provider_observed_at", "quote_created_at",
    "provider_expires_at", "internal_expires_at", "quote_fingerprint",
    "status", "created_at", "updated_at",
)

_ORDER_COLS = (
    "order_id", "tenant_id", "user_id", "booking_intent_id", "quote_id",
    "confirmation_id", "commerce_type", "provider", "merchant_order_id",
    "provider_order_id", "idempotency_key", "amount", "currency",
    "status", "status_version", "failure_code", "failure_class",
    "created_at", "updated_at", "submitted_at", "booked_at", "failed_at",
)

# 提交/终态时间戳列随转换目标自动维护（单一维护点）
_TRANSITION_TS: dict[str, tuple[str, ...]] = {
    "submitting": ("submitted_at",),
    "booked": ("booked_at",),
    "failed": ("failed_at",),
}


class BookingStore:
    """travel.booking_* 四表的持久层（语义务必经状态机，不做裸 UPDATE status）。"""

    def __init__(self, connection_factory=None):
        self._factory = connection_factory or default_connection_factory

    # ── Quote ───────────────────────────────────────────
    def create_quote(self, record: dict) -> dict:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_SCHEMA}.booking_quotes (
                    quote_id, tenant_id, user_id, commerce_type, provider,
                    provider_offer_id, offer_fingerprint, booking_facts,
                    price_amount, currency, taxes, fees, tax_inclusion,
                    availability, provider_observed_at, provider_expires_at,
                    internal_expires_at, quote_fingerprint
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING {", ".join(_QUOTE_COLS)}
                """,
                (
                    record["quote_id"], record["tenant_id"], record["user_id"],
                    record["commerce_type"], record["provider"],
                    record.get("provider_offer_id"),
                    record["offer_fingerprint"],
                    json.dumps(record["booking_facts"], ensure_ascii=False,
                               default=str),
                    str(record["price_amount"]), record["currency"],
                    str(record["taxes"]) if record.get("taxes") is not None else None,
                    str(record["fees"]) if record.get("fees") is not None else None,
                    record.get("tax_inclusion", "unknown"),
                    record["availability"],
                    record["provider_observed_at"],
                    record.get("provider_expires_at"),
                    record["internal_expires_at"],
                    record["quote_fingerprint"],
                ),
            )
            row = cur.fetchone()
            conn.commit()
        return _row_to_quote(row, _QUOTE_COLS)

    def get_quote(self, quote_id: str) -> dict | None:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_QUOTE_COLS)} FROM {_SCHEMA}.booking_quotes "
                "WHERE quote_id = %s", (quote_id,))
            row = cur.fetchone()
        return _row_to_quote(row, _QUOTE_COLS) if row else None

    def mark_quote_status(self, quote_id: str, status: str) -> None:
        """Quote 生命周期状态（expired/superseded/invalidated）——事实列不可变，
        仅状态列可动（trigger 守卫）。"""
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_SCHEMA}.booking_quotes SET status=%s, updated_at=now() "
                "WHERE quote_id=%s", (status, quote_id))
            conn.commit()

    # ── Order ───────────────────────────────────────────
    def create_order(self, record: dict) -> tuple[dict, bool]:
        """创建订单；booking_intent_id 冲突 = 并发双击的败者 → 读回既有行。

        Returns: (order, created)——created=False 表示复用既有意图（G24/G25）。
        """
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_SCHEMA}.booking_orders (
                    order_id, tenant_id, user_id, booking_intent_id, quote_id,
                    confirmation_id, commerce_type, provider, merchant_order_id,
                    idempotency_key, amount, currency
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (booking_intent_id) DO NOTHING
                RETURNING {", ".join(_ORDER_COLS)}
                """,
                (
                    record["order_id"], record["tenant_id"], record["user_id"],
                    record["booking_intent_id"], record["quote_id"],
                    record["confirmation_id"], record["commerce_type"],
                    record["provider"], record["merchant_order_id"],
                    record["idempotency_key"], str(record["amount"]),
                    record["currency"],
                ),
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                cur.execute(
                    f"SELECT {', '.join(_ORDER_COLS)} FROM {_SCHEMA}.booking_orders "
                    "WHERE booking_intent_id = %s", (record["booking_intent_id"],))
                row = cur.fetchone()
                created = False
            else:
                created = True
            conn.commit()
        order = _row_to_order(row, _ORDER_COLS)
        if created:
            self.append_event(
                tenant_id=order["tenant_id"], order_id=order["order_id"],
                quote_id=order["quote_id"], event_type="booking_intent_created",
                actor=order["user_id"], result="created",
                detail={"merchant_order_id": order["merchant_order_id"]},
            )
        return order, created

    def get_order(self, order_id: str) -> dict | None:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_ORDER_COLS)} FROM {_SCHEMA}.booking_orders "
                "WHERE order_id = %s", (order_id,))
            row = cur.fetchone()
        return _row_to_order(row, _ORDER_COLS) if row else None

    def get_order_by_intent(self, intent_id: str) -> dict | None:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_ORDER_COLS)} FROM {_SCHEMA}.booking_orders "
                "WHERE booking_intent_id = %s", (intent_id,))
            row = cur.fetchone()
        return _row_to_order(row, _ORDER_COLS) if row else None

    def get_order_by_merchant_ref(self, merchant_order_id: str) -> dict | None:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_ORDER_COLS)} FROM {_SCHEMA}.booking_orders "
                "WHERE merchant_order_id = %s", (merchant_order_id,))
            row = cur.fetchone()
        return _row_to_order(row, _ORDER_COLS) if row else None

    def latest_order_for_user(self, tenant_id: str, user_id: str,
                              statuses: list[str]) -> dict | None:
        """用户最近一张处于指定状态的订单（确认/状态查询入口）。"""
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_ORDER_COLS)} FROM {_SCHEMA}.booking_orders "
                "WHERE tenant_id=%s AND user_id=%s AND status = ANY(%s) "
                "ORDER BY created_at DESC LIMIT 1",
                (tenant_id, user_id, statuses))
            row = cur.fetchone()
        return _row_to_order(row, _ORDER_COLS) if row else None

    def transition_order(
        self, *, order_id: str, expected_status: str, target: str,
        cause: str = "", actor: str = "", event_type: str,
        failure_code: str | None = None, failure_class: str | None = None,
        provider_order_id: str | None = None, detail: dict | None = None,
    ) -> dict:
        """集中状态转换（G20/G21）：CAS + 版本单调 + 同事务事件追加。

        并发/陈旧（expected_status 不匹配）→ StaleTransition（调用方重读）。
        """
        ts_cols: list[str] = []
        ts_params: list[Any] = []
        for col in _TRANSITION_TS.get(target, ()):
            ts_cols.append(f"{col} = COALESCE({col}, now())")
        set_extra: list[str] = []
        extra_params: list[Any] = []
        if failure_code is not None:
            set_extra.append("failure_code = %s")
            extra_params.append(failure_code)
        if failure_class is not None:
            set_extra.append("failure_class = %s")
            extra_params.append(failure_class)
        if provider_order_id is not None:
            set_extra.append("provider_order_id = %s")
            extra_params.append(provider_order_id)

        event_id = str(uuid.uuid4())
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_SCHEMA}.booking_orders SET
                    status = %s,
                    status_version = status_version + 1,
                    updated_at = now(){"," + ", ".join(ts_cols) if ts_cols else ""}
                    {("," + ", ".join(set_extra)) if set_extra else ""}
                WHERE order_id = %s AND status = %s
                RETURNING {", ".join(_ORDER_COLS)}
                """,
                tuple([target, *ts_params, *extra_params, order_id,
                       expected_status]),
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                raise StaleTransition(
                    f"订单 {order_id} 状态已变化（期望 {expected_status}）")
            cur.execute(
                f"""
                INSERT INTO {_SCHEMA}.booking_events (
                    event_id, tenant_id, order_id, quote_id, event_type,
                    actor, result, detail
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    event_id,
                    row[1],   # tenant_id
                    order_id,
                    row[4],   # quote_id
                    event_type,
                    actor,
                    target,
                    json.dumps(detail or {"cause": cause}, ensure_ascii=False,
                               default=str),
                ),
            )
            conn.commit()
        order = _row_to_order(row, _ORDER_COLS)
        logger.info(
            "[BookingState] order=%s %s -> %s cause=%s version=%s",
            order_id, expected_status, target, cause or "-",
            order["status_version"])
        return order

    # ── 恢复/对账扫描（§二十六）────────────────────────
    def stale_orders(self, *, statuses: list[str], older_than_seconds: int,
                     limit: int = 50) -> list[dict]:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_ORDER_COLS)} FROM {_SCHEMA}.booking_orders "
                "WHERE status = ANY(%s) AND updated_at <= now() - (%s * interval '1 second') "
                "ORDER BY updated_at ASC LIMIT %s",
                (statuses, older_than_seconds, limit))
            rows = cur.fetchall()
        return [_row_to_order(r, _ORDER_COLS) for r in rows]

    def expired_awaiting(self, *, limit: int = 50) -> list[dict]:
        """确认 TTL 已到的待确认订单（响应式过期之外的兜底扫描）。"""
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT o.{", o.".join(_ORDER_COLS)}
                FROM {_SCHEMA}.booking_orders o
                JOIN {_SCHEMA}.booking_quotes q ON q.quote_id = o.quote_id
                WHERE o.status = 'awaiting_confirmation'
                  AND q.internal_expires_at <= now()
                ORDER BY o.created_at ASC LIMIT %s
                """, (limit,))
            rows = cur.fetchall()
        return [_row_to_order(r, _ORDER_COLS) for r in rows]

    # ── Webhook Inbox（§二十七/§二十九）────────────────
    def inbox_insert(self, *, provider: str, event_id: str,
                     external_order_id: str | None, event_type: str,
                     payload: dict, payload_hash: str) -> bool:
        """重复投递（同 provider+event_id）→ False（幂等去重，B16）。"""
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_SCHEMA}.booking_webhook_inbox (
                    provider, event_id, external_order_id, event_type,
                    payload, payload_hash
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (provider, event_id) DO NOTHING
                RETURNING id
                """,
                (provider, event_id, external_order_id, event_type,
                 json.dumps(payload, ensure_ascii=False, default=str),
                 payload_hash))
            inserted = cur.fetchone() is not None
            conn.commit()
        return inserted

    def inbox_mark(self, *, provider: str, event_id: str, status: str,
                   reason: str | None = None) -> None:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_SCHEMA}.booking_webhook_inbox SET status=%s, "
                "processed_at=now(), quarantine_reason=%s "
                "WHERE provider=%s AND event_id=%s",
                (status, reason, provider, event_id))
            conn.commit()

    # ── 事件（供 quote 前置事件等独立追加）──────────────
    def append_event(self, *, tenant_id: str, order_id: str | None,
                     quote_id: str | None, event_type: str, actor: str = "",
                     result: str = "", detail: dict | None = None) -> None:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_SCHEMA}.booking_events (
                    event_id, tenant_id, order_id, quote_id, event_type,
                    actor, result, detail
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (str(uuid.uuid4()), tenant_id, order_id, quote_id, event_type,
                 actor, result,
                 json.dumps(detail or {}, ensure_ascii=False, default=str)))
            conn.commit()

    def events_for_order(self, order_id: str) -> list[dict]:
        with self._factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT event_id, event_type, actor, result, detail, created_at "
                f"FROM {_SCHEMA}.booking_events WHERE order_id=%s ORDER BY id",
                (order_id,))
            rows = cur.fetchall()
        return [
            {"event_id": r[0], "event_type": r[1], "actor": r[2],
             "result": r[3], "detail": r[4], "created_at": _iso(r[5])}
            for r in rows
        ]


def _row_to_order(row: tuple, cols: tuple[str, ...]) -> dict:
    data = dict(zip(cols, row))
    data["amount"] = _num(data["amount"])
    for key in ("created_at", "updated_at", "submitted_at", "booked_at",
                "failed_at"):
        data[key] = _iso(data[key])
    return data
