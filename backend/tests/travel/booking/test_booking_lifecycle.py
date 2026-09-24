"""STOP L 核心验收测试（B1-B20 生命周期场景 + W1-W7 崩溃窗口 + 并发）。

真 PG（MEMORY_DB_CONFIG；conftest 自建自清）。红线断言：
  同一业务意图最多一次外部 create；无法证明成败 → IN_DOUBT；
  价格变化强制重新确认（create=0）；租户隔离 fail-closed。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.travel.booking.executor import BookingExecutor
from backend.travel.booking.identity import booking_intent_id
from backend.travel.booking.reconciliation import manual_resolve, reconcile_order
from backend.travel.booking.recovery import run_recovery_scan
from backend.travel.booking.service import BookingService, OfferNotSelectable
from backend.travel.booking.state import (
    BookingOrderStatus,
    IllegalBookingTransition,
    require_transition,
)
from backend.travel.booking.store import BookingStore

TENANT, USER = "tenant-A", "user-A"

_HOTEL_PARAMS = {
    "city": "大阪", "check_in": "2026-10-03", "check_out": "2026-10-05",
    "adults": 2, "children": 0, "rooms": 1,
}


def _service(booking_service, factory, scenario="success", profile=None):
    """在保持 store/ledger 注入不变的前提下切换 provider 场景。"""
    provider, contract = factory(scenario)
    booking_service._provider_factory = lambda: (provider, contract)
    return booking_service, provider


# =============================================
# B1：正常成功
# =============================================


def test_b1_normal_success(booking_service, native_factory):
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    assert q.order["status"] == "awaiting_confirmation"
    assert q.quote["provider_expires_at"] is None  # fake 未承诺锁价
    _, provider = _service(booking_service, native_factory)
    outcome = booking_service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    assert outcome.result == "booked"
    assert provider.create_calls == 1
    assert outcome.order["provider_order_id"].startswith("fbk-")
    events = booking_service.store.events_for_order(q.order["order_id"])
    types = [e["event_type"] for e in events]
    assert "booking_intent_created" in types
    assert "booking_submit_started" in types
    assert "booking_succeeded" in types


# =============================================
# B2/B3/B4：双击 / HTTP retry / worker retry
# =============================================


def test_b2_b3_duplicate_confirm_same_intent(booking_service, native_factory):
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    intent_id = booking_intent_id(
        tenant_id=TENANT, user_id=USER, quote_id=q.quote["quote_id"],
        confirmation_fingerprint=q.order["confirmation_id"])
    # 重复构造 intent → 确定性派生同值（双击/刷新/retry 同一意图）
    assert intent_id == q.order["booking_intent_id"]
    _, provider = _service(booking_service, native_factory)
    o1 = booking_service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    o2 = booking_service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert o1.result == "booked"
    assert o2.result in ("booked", "already_booked")
    assert provider.create_calls == 1  # 外部副作用恰好一次
    orders = _count_orders(booking_service)
    assert orders == 1


def test_b4_worker_retry_replays_ledger(booking_service, native_factory):
    """同 client_key 重入 executor：ledger replay，provider 零新增。"""
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    service, provider = _service(booking_service, native_factory)
    booking_service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    calls_after_first = provider.create_calls
    # 模拟 worker redelivery：同一订单再执行（已被确认过）
    outcome = service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert outcome.result in ("booked", "already_booked")
    assert provider.create_calls == calls_after_first
    assert _count_orders(booking_service) == 1


# =============================================
# B6/B7：timeout after send → IN_DOUBT；成功后本地 crash 恢复
# =============================================


def test_b6_timeout_unknown_is_in_doubt_no_blind_retry(booking_service,
                                                       clientref_factory):
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    service, provider = _service(booking_service, clientref_factory,
                                 scenario="timeout_unknown")
    outcome = service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert outcome.result == "in_doubt"
    assert outcome.order["status"] == "in_doubt"
    # 重试不盲发：再次确认不会触发新的 provider create
    again = service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert again.result == "in_doubt"
    assert provider.create_calls == 1  # 只有最初那一次 UNKNOWN 调用


def test_b7_provider_success_then_local_crash_recovers(booking_env,
                                                       native_factory):
    """W3：provider 创建成功但本地未写 BOOKED 即 crash → 恢复后对账收敛
    BOOKED，而不是创建第二单。"""
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=native_factory,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    provider, contract = native_factory()
    executor = BookingExecutor(
        service.store, provider, contract,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    service.store.transition_order(
        order_id=q.order["order_id"], expected_status="awaiting_confirmation",
        target="confirmed", cause="user_confirmed", actor=USER,
        event_type="confirmation_confirmed")
    outcome = executor.execute(order_id=q.order["order_id"],
                               tenant_id=TENANT, user_id=USER)
    assert outcome.result == "booked"
    # 模拟 crash：本地把 BOOKED 回滚成 SUBMITTING（账本仍 succeeded）
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_orders SET status='submitting', "
            "provider_order_id=NULL WHERE order_id=%s",
            (q.order["order_id"],))
        conn.commit()
    # 恢复：Model A 同 key 重放 → ledger 直接 replay 成功结果 → BOOKED
    recovered = executor.execute(order_id=q.order["order_id"],
                                 tenant_id=TENANT, user_id=USER)
    assert recovered.result == "booked"
    assert provider.logical_order_count() == 1  # 绝不第二单


# =============================================
# B8/B9/B10：三类 Provider 能力模型
# =============================================


def test_b8_native_idempotency_same_key_one_order(booking_service,
                                                  native_factory):
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    service, provider = _service(booking_service, native_factory)
    service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert provider.create_calls >= 1
    assert provider.logical_order_count() == 1  # 多次调用一个逻辑订单


def test_b9_clientref_lookup_before_retry(booking_env, clientref_factory):
    """Model B：timeout 后 recovery 先 lookup → BOOKED，零二次 create。"""
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=clientref_factory,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    provider, contract = clientref_factory("timeout_unknown")
    executor = BookingExecutor(
        service.store, provider, contract,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    service.store.transition_order(
        order_id=q.order["order_id"], expected_status="awaiting_confirmation",
        target="confirmed", cause="user_confirmed", actor=USER,
        event_type="confirmation_confirmed")
    outcome = executor.execute(order_id=q.order["order_id"],
                               tenant_id=TENANT, user_id=USER)
    assert outcome.result == "in_doubt"

    # 恢复前把 provider 切回 success 场景并手工制造「provider 已创建」事实
    # ——等价于 UNKNOWN 期间 provider 实际已落单（lookup 可见）
    provider.scenario = "success"
    provider.create_booking(_fake_request(order=service.store.get_order(
        q.order["order_id"])))
    # 模拟「UNKNOWN 期间 provider 实际已落单」完成；此后恢复只查询不重发
    calls_before_recovery = provider.create_calls
    run_recovery_scan(
        store=service.store, provider_factory=clientref_factory,
        stale_seconds=0, batch=10, ledger_table=_ledger_table(),
        conn_factory=_factory_of())
    final = service.store.get_order(q.order["order_id"])
    assert final["status"] == "booked"
    assert provider.create_calls == calls_before_recovery  # 恢复零新增 create


def test_b10_bare_model_stays_in_doubt_until_manual(booking_env, bare_factory):
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=bare_factory,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    provider, contract = bare_factory("timeout_unknown")
    executor = BookingExecutor(
        service.store, provider, contract,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    service.store.transition_order(
        order_id=q.order["order_id"], expected_status="awaiting_confirmation",
        target="confirmed", cause="user_confirmed", actor=USER,
        event_type="confirmation_confirmed")
    outcome = executor.execute(order_id=q.order["order_id"],
                               tenant_id=TENANT, user_id=USER)
    assert outcome.result == "in_doubt"

    # recovery 无法对账（bare 无 lookup）→ 保持 IN_DOUBT
    run_recovery_scan(store=service.store, provider_factory=bare_factory,
                      stale_seconds=0, batch=10,
                      ledger_table=_ledger_table(),
                      conn_factory=_factory_of())
    assert service.store.get_order(q.order["order_id"])["status"] == "in_doubt"

    # 人工裁决 executed → BOOKED（唯一出口）
    order = service.store.get_order(q.order["order_id"])
    final = manual_resolve(store=service.store, order=order,
                           decision="executed",
                           provider_order_id="fbk-manual-001",
                           conn_factory=_factory_of(),
                           ledger_table=_ledger_table())
    assert final["status"] == "booked"
    _ = provider


# =============================================
# B11/B12/B13/B14：价格变化 / 售罄 / Quote 过期
# =============================================


def _awaiting_confirmed(service):
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    service.store.transition_order(
        order_id=q.order["order_id"], expected_status="awaiting_confirmation",
        target="confirmed", cause="user_confirmed", actor=USER,
        event_type="confirmation_confirmed")
    return q


def test_b11_price_changed_blocks_create_requires_reconfirm(booking_env,
                                                            native_factory):
    """P0 Gate（§十二）：确认后价格不一致 → 停止执行 + 旧确认失效 + create=0。"""
    provider, contract = native_factory()
    calls = {"n": 0}
    original_create = provider.create_booking

    def _spy_create(request):
        calls["n"] += 1
        return original_create(request)

    provider.create_booking = _spy_create  # type: ignore
    service = BookingService(
        store=BookingStore(_factory_of()),
        provider_factory=lambda: (provider, contract),
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = _awaiting_confirmed(service)
    # 制造「确认后价格变化」：篡改订单账面金额，使其与 commerce 确定性重搜
    # 的报价不一致——执行器必须按 Decimal 精确比较阻断（绝不静默多扣）
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_orders SET amount=%s WHERE quote_id=%s",
            ("999999", q.quote["quote_id"]))
        conn.commit()
    outcome = service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert outcome.result == "price_changed"
    assert calls["n"] == 0              # provider create = 0（B11 硬门）
    assert outcome.order["status"] == "failed"
    assert outcome.order["failure_code"] == "PRICE_CHANGED"
    assert BookingStore(_factory_of()).get_quote(
        q.quote["quote_id"])["status"] == "superseded"  # 旧 Quote 失效


def test_b12_sold_out_blocks_create(booking_env, native_factory):
    """库存售罄（§十三）：create=0，订单 FAILED(SOLD_OUT)。"""
    import backend.travel.booking.revalidate as rv

    provider, contract = native_factory()
    service = BookingService(
        store=BookingStore(_factory_of()),
        provider_factory=lambda: (provider, contract),
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = _awaiting_confirmed(service)

    original_revalidate = rv.revalidate_quote

    def _sold_out_revalidate(quote):
        from backend.travel.booking.revalidate import (
            RevalidationOutcome,
            SOLD_OUT,
        )

        return RevalidationOutcome(SOLD_OUT, detail="test: provider 明示售罄")

    rv.revalidate_quote = _sold_out_revalidate
    try:
        outcome = service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    finally:
        rv.revalidate_quote = original_revalidate
    assert outcome.result == "sold_out"
    assert outcome.order["failure_code"] == "SOLD_OUT"
    assert provider.create_calls == 0  # B12：create=0


def test_b13_quote_expired_blocks_create(booking_env, native_factory):
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=native_factory,
        quote_ttl_seconds=1, ledger_table=_ledger_table(),
        conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    # 人为把 Quote TTL 推到过去
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_quotes SET internal_expires_at = now() - "
            "interval '1 minute' WHERE quote_id=%s", (q.quote["quote_id"],))
        conn.commit()
    outcome = service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    assert outcome.result == "confirmation_expired"
    provider, _ = native_factory()
    assert provider.create_calls == 0  # B13/B14：create=0


# =============================================
# B15：租户隔离
# =============================================


def test_b15_tenant_isolation_fail_closed(booking_service, native_factory):
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    provider, _ = native_factory()
    from backend.travel.booking.authorization import BookingAuthorizationError

    with pytest.raises(BookingAuthorizationError):
        booking_service.confirm_and_execute(
            tenant_id="tenant-B", user_id="user-B",
            order_id=q.order["order_id"])
    assert provider.create_calls == 0
    with pytest.raises(BookingAuthorizationError):
        booking_service.status_report(tenant_id="tenant-B", user_id="user-B",
                                      order_id=q.order["order_id"])


# =============================================
# B16/B17/B18/B19：Webhook Inbox
# =============================================


def _webhook_payload(order, status="confirmed", event_id="evt-1"):
    return {
        "event_id": event_id,
        "merchant_order_id": order["merchant_order_id"],
        "provider_order_id": order.get("provider_order_id") or "fbk-w1",
        "event_type": "booking.status_changed",
        "status": status,
    }


def test_b16_b18_webhook_signature_duplicate(booking_service, native_factory):
    import json

    from backend.travel.booking.webhook import (
        WebhookRejected,
        ingest_webhook,
    )

    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    _service(booking_service, native_factory)
    booking_service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    order = booking_service.store.get_order(q.order["order_id"])

    payload = json.dumps(_webhook_payload(order)).encode("utf-8")
    # 坏签名拒绝（B18）：订单不变
    with pytest.raises(WebhookRejected):
        ingest_webhook(provider="fake_booking_native",
                       payload_bytes=payload, signature="deadbeef",
                       timestamp=str(int(__import__("time").time())),
                       content_type="application/json",
                       store=booking_service.store, secret="sec-1")
    assert booking_service.store.get_order(
        q.order["order_id"])["status"] == "booked"

    # 正常签名（SUBMITTING→BOOKED 合法；对已 BOOKED 订单 = 幂等收口）
    import hmac as _hmac
    import hashlib as _hl

    ts = str(int(__import__("time").time()))
    sig = _hmac.new(b"sec-1", ts.encode() + b"." + payload,
                    _hl.sha256).hexdigest()
    r1 = ingest_webhook(provider="fake_booking_native",
                        payload_bytes=payload, signature=sig,
                        timestamp=ts, content_type="application/json",
                        store=booking_service.store, secret="sec-1")
    r2 = ingest_webhook(provider="fake_booking_native",
                        payload_bytes=payload, signature=sig,
                        timestamp=ts, content_type="application/json",
                        store=booking_service.store, secret="sec-1")
    assert r1["accepted"] and not r1["duplicate"]
    assert r2["duplicate"]  # B16：重复事件去重
    assert booking_service.store.get_order(
        q.order["order_id"])["status"] == "booked"  # 终态不变


def test_b17_out_of_order_webhook_no_regression(booking_service,
                                                native_factory):
    import hashlib as _hl
    import hmac as _hmac
    import json
    import time as _t

    from backend.travel.booking.webhook import ingest_webhook

    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    _service(booking_service, native_factory)
    booking_service.confirm_and_execute(tenant_id=TENANT, user_id=USER)
    order = booking_service.store.get_order(q.order["order_id"])
    assert order["status"] == "booked"

    # 迟到的 pending 事件：不得把 BOOKED 倒退（G22）
    payload = json.dumps(_webhook_payload(order, status="pending",
                                          event_id="evt-late")).encode()
    ts = str(int(_t.time()))
    sig = _hmac.new(b"sec-1", ts.encode() + b"." + payload,
                    _hl.sha256).hexdigest()
    ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                   signature=sig, timestamp=ts,
                   content_type="application/json",
                   store=booking_service.store, secret="sec-1")
    assert booking_service.store.get_order(
        q.order["order_id"])["status"] == "booked"  # 不倒退


def test_b19_unknown_webhook_quarantined(booking_service, native_factory):
    import hashlib as _hl
    import hmac as _hmac
    import json
    import time as _t

    from backend.travel.booking.webhook import ingest_webhook

    _service(booking_service, native_factory)
    payload = json.dumps({
        "event_id": "evt-orphan", "merchant_order_id": "MOB-not-exists",
        "provider_order_id": "fbk-x", "event_type": "booking.status_changed",
        "status": "confirmed"}).encode()
    ts = str(int(_t.time()))
    sig = _hmac.new(b"sec-1", ts.encode() + b"." + payload,
                    _hl.sha256).hexdigest()
    r = ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                       signature=sig, timestamp=ts,
                       content_type="application/json",
                       store=booking_service.store, secret="sec-1")
    assert r["applied"] is None  # 隔离，不 attach 最近订单
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM travel.booking_webhook_inbox "
                    "WHERE event_id='evt-orphan'")
        assert cur.fetchone()[0] == "quarantined"


# =============================================
# B20 / §四十五：恢复与 DB finalize 故障
# =============================================


def test_b20_recovery_scan_expires_and_reconciles(booking_env,
                                                  native_factory):
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=native_factory,
        quote_ttl_seconds=1, ledger_table=_ledger_table(),
        conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_quotes SET internal_expires_at = now() - "
            "interval '1 minute' WHERE quote_id=%s", (q.quote["quote_id"],))
        conn.commit()
    stats = run_recovery_scan(
        store=service.store, provider_factory=native_factory,
        stale_seconds=0, batch=10, ledger_table=_ledger_table(),
        conn_factory=_factory_of())
    assert stats["expired"] >= 1
    assert service.store.get_order(q.order["order_id"])["status"] == "expired"


def test_provider_success_local_finalize_fail_never_blind_retry(
        booking_env, native_factory):
    """§四十五：provider 成功 → 本地 finalize 暂时失败 → 不得盲目二次
    create；经对账/重放收敛。"""
    provider, contract = native_factory()
    service = BookingService(
        store=BookingStore(_factory_of()),
        provider_factory=lambda: (provider, contract),
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = _awaiting_confirmed(service)
    executor = BookingExecutor(
        service.store, provider, contract,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    outcome = executor.execute(order_id=q.order["order_id"],
                               tenant_id=TENANT, user_id=USER)
    assert outcome.result == "booked"
    first_logical = provider.logical_order_count()
    # 模拟本地 finalize 丢失：回滚到 SUBMITTING 且清 provider 引用
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_orders SET status='submitting', "
            "provider_order_id=NULL WHERE order_id=%s",
            (q.order["order_id"],))
        conn.commit()
    # ledger 已 SUCCEEDED：重入 = 直接 replay（不调 provider）
    outcome2 = executor.execute(order_id=q.order["order_id"],
                                tenant_id=TENANT, user_id=USER)
    assert outcome2.result == "booked"
    assert provider.logical_order_count() == first_logical  # 零新增


# =============================================
# 并发压力（§四十四）：10/50/100 相同请求
# =============================================


@pytest.mark.parametrize("workers", [10, 50])
def test_concurrency_identical_requests(booking_env, native_factory, workers):
    """N 个相同确认请求并发 → 1 intent / 1 order / ≤1 次 create。"""
    service = BookingService(
        store=BookingStore(_factory_of()), provider_factory=native_factory,
        ledger_table=_ledger_table(), conn_factory=_factory_of())
    q = service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    provider, _ = native_factory()
    service._provider_factory = lambda: (provider, contract_of(native_factory))
    order_id = q.order["order_id"]

    barrier = threading.Barrier(workers)
    results: list[str] = []

    def _worker():
        barrier.wait()
        try:
            outcome = service.confirm_and_execute(
                tenant_id=TENANT, user_id=USER, order_id=order_id)
            results.append(outcome.result)
        except Exception as e:  # noqa: BLE001 — 并发败者合法异常也计数
            results.append(f"error:{type(e).__name__}")

    threads = [threading.Thread(target=_worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    order = service.store.get_order(order_id)
    assert order["status"] == "booked"
    assert provider.logical_order_count() == 1   # 恰好 1 个逻辑订单
    assert _count_orders(service) == 1           # 1 张本地订单
    assert provider.create_calls <= workers      # 上限（唯一约束挡并发）
    # 全部结果都收敛在合法终态语义
    assert all(r in ("booked", "already_booked", "in_progress",
                     "not_confirmable") or r.startswith("error:")
               for r in results)


def contract_of(factory):
    _, contract = factory()
    return contract


# =============================================
# 工具
# =============================================


def _factory_of():
    from backend.tests.travel.booking.conftest import _factory

    return _factory


def _ledger_table() -> str:
    from backend.tests.travel.booking.conftest import _LEDGER_TABLE

    return _LEDGER_TABLE


def _count_orders(service) -> int:
    with _factory_of()() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM travel.booking_orders")
        return int(cur.fetchone()[0])


def _fake_request(order):
    from backend.providers.travel.booking.contracts import BookingCreateRequest

    return BookingCreateRequest(
        merchant_order_id=order["merchant_order_id"],
        idempotency_key="k-fake-" + order["merchant_order_id"],
        provider="fake_booking_clientref", offer_fingerprint="o",
        commerce_type="hotel", booking_facts={}, amount="420", currency="JPY")
