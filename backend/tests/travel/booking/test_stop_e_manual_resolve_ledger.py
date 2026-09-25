"""Phase3 STOP E：Booking Model C IN_DOUBT 人工裁决账本收敛回归。

钉死缺口 G-E2：manual_resolve 旧实现走 resolve_stale_side_effect（只匹配
running+租约过期），而 Model C IN_DOUBT 的账本主形态是 executor 落库的
failed+IDEMPOTENCY_UNCERTAIN——裁决静默 no-op，账本永远停在「结果未知」，
not_executed 后同 key 依旧被 _decide_claim 的 UNCERTAIN CONFLICT 阻断。

修复后：manual_resolve 经 resolve_side_effect 双形态分流，账本真实收敛：
  not_executed → RESOLVED_NOT_EXECUTED（同 key claim 冲突码不再是 UNCERTAIN）
  executed     → SUCCEEDED + MANUAL_RESOLVED_EXECUTED（重入走幂等短路）
复用 tests/travel/booking/conftest.py 的 booking_env 隔离（真 PG + 自建
travel.bt_ledger_test，PG 不可达 skip 不假绿）。

订单级「重试」语义注记：FAILED 订单本身不可重入 executor（NOT_CONFIRMABLE，
业务上由用户重新发起生成新 merchant_order_id）；本测试在账本层验证
UNCERTAIN 阻断确实解除——这正是人工裁决要收敛的对象。
"""
from __future__ import annotations

import pytest

from backend.travel.booking.executor import BookingExecutor
from backend.travel.booking.reconciliation import manual_resolve
from backend.travel.booking.service import BookingService
from backend.travel.booking.store import BookingStore

TENANT, USER = "tenant-A", "user-A"
_LEDGER_TABLE = "travel.bt_ledger_test"

_HOTEL_PARAMS = {
    "city": "大阪", "check_in": "2026-10-03", "check_out": "2026-10-05",
    "adults": 2, "children": 0, "rooms": 1,
}


def _factory_of():
    """与 booking conftest 同约定：返回连接工厂（`_factory_of()()` 得连接）。"""
    def _connect():
        import psycopg
        from backend.config.database import MEMORY_DB_CONFIG

        c = MEMORY_DB_CONFIG
        return psycopg.connect(
            f"postgresql://{c['user']}:{c['password']}"
            f"@{c['host']}:{c['port']}/{c['dbname']}")
    return _connect


def _ledger_row(merchant_order_id: str) -> tuple:
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT status, error_code FROM {_LEDGER_TABLE} "
            "WHERE tenant_id=%s AND actor_id=%s AND operation=%s "
            "AND client_key=%s",
            (TENANT, USER, "travel.booking.create", merchant_order_id))
        return cur.fetchone()


def _in_doubt_order(booking_env, bare_factory):
    """Model C timeout_unknown 场景推到 IN_DOUBT（真实 failed+UNCERTAIN 落库）。"""
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=bare_factory,
        ledger_table=_LEDGER_TABLE, conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    provider, contract = bare_factory("timeout_unknown")
    executor = BookingExecutor(
        service.store, provider, contract,
        ledger_table=_LEDGER_TABLE, conn_factory=_factory_of())
    service.store.transition_order(
        order_id=q.order["order_id"], expected_status="awaiting_confirmation",
        target="confirmed", cause="user_confirmed", actor=USER,
        event_type="confirmation_confirmed")
    outcome = executor.execute(order_id=q.order["order_id"],
                               tenant_id=TENANT, user_id=USER)
    assert outcome.result == "in_doubt"
    order = service.store.get_order(q.order["order_id"])
    # 主形态取证：executor 收口的 UNCERTAIN（旧裁决通道对它 no-op 的形态）
    assert _ledger_row(order["merchant_order_id"]) == (
        "failed", "IDEMPOTENCY_UNCERTAIN")
    return service, provider, order


def test_stop_e_model_c_not_executed_releases_uncertain_block(
    booking_env, bare_factory,
):
    """裁决 not_executed：账本 RESOLVED_NOT_EXECUTED，UNCERTAIN 阻断解除。"""
    service, provider, order = _in_doubt_order(booking_env, bare_factory)
    merchant = order["merchant_order_id"]

    final = manual_resolve(
        store=service.store, order=order, decision="not_executed",
        reason="op:tester provider 后台无此单",
        conn_factory=_factory_of(), ledger_table=_LEDGER_TABLE)
    assert final["status"] == "failed"
    # G-E2 回归核心：账本行确实被改写（旧实现是静默 no-op，永远停在
    # failed+UNCERTAIN，not_executed 裁决形同虚设）
    assert _ledger_row(merchant) == ("failed", "RESOLVED_NOT_EXECUTED")


def test_stop_e_model_c_executed_marks_succeeded(booking_env, bare_factory):
    """裁决 executed：账本 SUCCEEDED+审计码；重入走已预订幂等短路零重放。"""
    service, provider, order = _in_doubt_order(booking_env, bare_factory)
    merchant = order["merchant_order_id"]
    calls_before = provider.create_calls

    final = manual_resolve(
        store=service.store, order=order, decision="executed",
        provider_order_id="fbk-manual-stop-e",
        reason="op:tester 供应商后台已查实在单",
        conn_factory=_factory_of(), ledger_table=_LEDGER_TABLE)
    assert final["status"] == "booked"
    assert final["provider_order_id"] == "fbk-manual-stop-e"
    assert _ledger_row(merchant) == ("succeeded", "MANUAL_RESOLVED_EXECUTED")

    # 重入 executor：订单已 BOOKED → already_booked 幂等短路，provider 零新增
    provider2, contract2 = bare_factory("success")
    executor2 = BookingExecutor(
        service.store, provider2, contract2,
        ledger_table=_LEDGER_TABLE, conn_factory=_factory_of())
    outcome = executor2.execute(order_id=order["order_id"],
                                tenant_id=TENANT, user_id=USER,
                                owner_execution_id="stop-e-replay")
    assert outcome.result == "already_booked"
    assert provider2.create_calls == calls_before
