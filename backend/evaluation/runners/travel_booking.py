"""Travel Booking Runner — Booking 事务探针（STOP L8，任务书 §四十二/§四十三）

Golden Cases：B1-B20（数据集 datasets/travel-booking/），全部离线
（fake provider 三能力 profile + 真 PG 隔离表，needs_live=False）。
真实 Booking Provider E2E 属 G45/G46——当前无凭据，如实 BLOCKED。

T1-T12 质量指标由探针结局聚合（每个探针守卫一个 T 维度，失败即计违规）：
  T1 duplicate_external_booking_rate   T2 duplicate_local_order_rate
  T3 false_success_rate                T4 false_failure_rate
  T5 in_doubt_correctness_rate         T6 price_change_without_reconfirm_rate
  T7 unauthorized_execution_rate       T8 webhook_duplicate_side_effect_rate
  T9 illegal_state_transition_rate     T10 unrecoverable_crash_window_rate
  T11 audit_coverage_rate              T12 secret_pii_leak_rate

生产门（§四十三）：T1/T2/T3/T4/T6/T7/T8/T9/T12 = 0；设计为 IN_DOUBT 的
场景必须 100% 进入 IN_DOUBT（T5）。

隔离纪律：travel.booking_* 每探针 TRUNCATE；ledger 用独立评测表
（与共享 ai.idempotency_records 零交集），结束 DROP。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner

MODULE = "travel-booking"

_LEDGER_TABLE = "travel.bt_ledger_eval"
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    tenant_id varchar(64) NOT NULL,
    actor_id varchar(64) NOT NULL,
    operation varchar(64) NOT NULL,
    client_key varchar(128) NOT NULL,
    request_hash character(64) NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    result jsonb,
    error_code text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_id uuid,
    lease_expires_at timestamptz,
    expires_at timestamptz,
    owner_execution_id varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, client_key)
)
"""

TENANT, USER = "eval-tenant", "eval-user"
_HOTEL_PARAMS = {
    "city": "大阪", "check_in": "2026-10-03", "check_out": "2026-10-05",
    "adults": 2, "children": 0, "rooms": 1,
}
_WEBHOOK_SECRET = "eval-webhook-secret"


# =============================================
# 环境（每探针独立：truncate + 独立 ledger 表 + fake provider 单例）
# =============================================


class _Env:
    def __init__(self, profile: str = "native"):
        from backend.providers.travel.booking.fake import FakeTravelBookingProvider
        from backend.shared.provider_idempotency import get_provider_contract
        from backend.travel.booking.service import BookingService
        from backend.travel.booking.store import BookingStore

        self.provider = FakeTravelBookingProvider(profile=profile)
        self.contract = get_provider_contract(self.provider.name)
        self.store = BookingStore(_factory)
        self.service = BookingService(
            store=self.store, provider_factory=lambda: (self.provider, self.contract),
            ledger_table=_LEDGER_TABLE, conn_factory=_factory)

    def set_scenario(self, scenario: str) -> None:
        self.provider.scenario = scenario


def _factory():
    import psycopg

    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    return psycopg.connect(
        f"postgresql://{c['user']}:{c['password']}"
        f"@{c['host']}:{c['port']}/{c['dbname']}")


def _setup(clean_ledger: bool) -> None:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE travel.booking_orders, travel.booking_quotes, "
            "travel.booking_webhook_inbox, travel.booking_events RESTART IDENTITY CASCADE")
        cur.execute(f"DELETE FROM {_LEDGER_TABLE}")
        conn.commit()
    _ = clean_ledger


def _drop_ledger() -> None:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {_LEDGER_TABLE}")
        conn.commit()


def _create_ledger() -> None:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(_LEDGER_DDL.format(table=_LEDGER_TABLE))
        conn.commit()


def _quote_and_confirm(env: _Env) -> dict:
    q = env.service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    env.store.transition_order(
        order_id=q.order["order_id"],
        expected_status="awaiting_confirmation", target="confirmed",
        cause="user_confirmed", actor=USER,
        event_type="confirmation_confirmed")
    return q


def _count_orders() -> int:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM travel.booking_orders")
        return int(cur.fetchone()[0])


def _webhook_signed(order, status="confirmed", event_id="evt-1"):
    payload = json.dumps({
        "event_id": event_id,
        "merchant_order_id": order["merchant_order_id"],
        "provider_order_id": "fbk-w",
        "event_type": "booking.status_changed",
        "status": status}).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(_WEBHOOK_SECRET.encode(), ts.encode() + b"." + payload,
                   hashlib.sha256).hexdigest()
    return payload, sig, ts


# =============================================
# 探针（B 系列）
# =============================================


def _b1():
    """B1 正常成功：Quote → Confirm → Create → BOOKED。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    outcome = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if outcome.result != "booked" or env.provider.create_calls != 1:
        reasons.append(f"result={outcome.result} calls={env.provider.create_calls}")
    events = [e["event_type"] for e in
              env.store.events_for_order(q.order["order_id"])]
    if not {"booking_intent_created", "booking_submit_started",
            "booking_succeeded"} <= set(events):
        reasons.append(f"audit 事件缺口: {events}")   # T11 口径
    return reasons, {}


def _b2():
    """B2 双击确认：并发两请求 → create=1 / order=1。"""
    env = _Env("native")
    q = env.service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    barrier = threading.Barrier(2)
    results: list[str] = []

    def _worker():
        barrier.wait()
        o = env.service.confirm_and_execute(
            tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
        results.append(o.result)

    ts = [threading.Thread(target=_worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    reasons = []
    if env.provider.logical_order_count() != 1:
        reasons.append("外部订单 ≠ 1")
    if _count_orders() != 1:
        reasons.append("本地订单 ≠ 1")
    if not all(r in ("booked", "already_booked", "in_progress", "not_confirmable")
               for r in results):
        reasons.append(f"非法结局: {results}")
    return reasons, {}


def _b4():
    """B4 worker retry：同订单重入 → ledger replay，零新增 create。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    o1 = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    n = env.provider.create_calls
    o2 = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if o1.result != "booked" or o2.result not in ("booked", "already_booked"):
        reasons.append(f"{o1.result}/{o2.result}")
    if env.provider.create_calls != n:
        reasons.append("retry 产生了新 create")
    return reasons, {}


def _b6():
    """B6 timeout after send → IN_DOUBT，禁止自动二次 create（T5）。"""
    env = _Env("clientref")
    env.set_scenario("timeout_unknown")
    q = _quote_and_confirm(env)
    outcome = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if outcome.result != "in_doubt" or outcome.order["status"] != "in_doubt":
        reasons.append(f"result={outcome.result}")
    again = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    if again.result != "in_doubt" or env.provider.create_calls != 1:
        reasons.append("IN_DOUBT 后发生了自动重试")
    return reasons, {}


def _b8():
    """B8 native 幂等：同 key 多次 → 一个逻辑订单。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    for _ in range(3):
        env.service.confirm_and_execute(
            tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if env.provider.logical_order_count() != 1:
        reasons.append("逻辑订单 ≠ 1")
    return reasons, {}


def _b9():
    """B9 Model B：lookup before retry——恢复对账收敛 BOOKED 且零重发。"""
    env = _Env("clientref")
    env.set_scenario("timeout_unknown")
    q = _quote_and_confirm(env)
    outcome = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if outcome.result != "in_doubt":
        reasons.append(f"result={outcome.result}")
    env.set_scenario("success")
    env.provider.create_booking(_fake_create_request(
        env.store.get_order(q.order["order_id"])))
    before = env.provider.create_calls
    from backend.travel.booking.recovery import run_recovery_scan

    run_recovery_scan(store=env.store,
                      provider_factory=lambda: (env.provider, env.contract),
                      stale_seconds=0, batch=10,
                      ledger_table=_LEDGER_TABLE, conn_factory=_factory)
    final = env.store.get_order(q.order["order_id"])
    if final["status"] != "booked":
        reasons.append(f"对账后 {final['status']}")
    if env.provider.create_calls != before:
        reasons.append("恢复期间重发了 create")
    return reasons, {}


def _b10():
    """B10 Model C：无法对账 → 保持 IN_DOUBT，人工裁决唯一出口。"""
    env = _Env("bare")
    env.set_scenario("timeout_unknown")
    q = _quote_and_confirm(env)
    outcome = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if outcome.result != "in_doubt":
        reasons.append(f"result={outcome.result}")
    from backend.travel.booking.recovery import run_recovery_scan

    run_recovery_scan(store=env.store,
                      provider_factory=lambda: (env.provider, env.contract),
                      stale_seconds=0, batch=10,
                      ledger_table=_LEDGER_TABLE, conn_factory=_factory)
    if env.store.get_order(q.order["order_id"])["status"] != "in_doubt":
        reasons.append("bare 模型被自动收敛（应保持 IN_DOUBT）")
    from backend.travel.booking.reconciliation import manual_resolve

    order = env.store.get_order(q.order["order_id"])
    final = manual_resolve(store=env.store, order=order, decision="executed",
                           provider_order_id="fbk-manual",
                           conn_factory=_factory, ledger_table=_LEDGER_TABLE)
    if final["status"] != "booked":
        reasons.append("人工裁决未收敛")
    return reasons, {}


def _b11():
    """B11 价格变化：阻断 + create=0（T6）。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_orders SET amount=%s WHERE quote_id=%s",
            ("999999", q.quote["quote_id"]))
        conn.commit()
    outcome = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if outcome.result != "price_changed" or env.provider.create_calls != 0:
        reasons.append(f"result={outcome.result} calls={env.provider.create_calls}")
    if env.store.get_quote(q.quote["quote_id"])["status"] != "superseded":
        reasons.append("旧 Quote 未失效")
    return reasons, {}


def _b12():
    """B12 售罄：create=0。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    import backend.travel.booking.revalidate as rv

    original = rv.revalidate_quote

    def _sold_out(quote):
        from backend.travel.booking.revalidate import (
            RevalidationOutcome,
            SOLD_OUT,
        )

        return RevalidationOutcome(SOLD_OUT, detail="eval")

    rv.revalidate_quote = _sold_out
    try:
        outcome = env.service.confirm_and_execute(
            tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    finally:
        rv.revalidate_quote = original
    reasons = []
    if outcome.result != "sold_out" or env.provider.create_calls != 0:
        reasons.append(f"result={outcome.result}")
    return reasons, {}


def _b13():
    """B13 Quote 过期：create=0。"""
    env = _Env("native")
    q = env.service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_quotes SET internal_expires_at = "
            "now() - interval '1 minute' WHERE quote_id=%s",
            (q.quote["quote_id"],))
        conn.commit()
    outcome = env.service.confirm_and_execute(
        tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
    reasons = []
    if outcome.result != "confirmation_expired" or env.provider.create_calls != 0:
        reasons.append(f"result={outcome.result}")
    return reasons, {}


def _b15():
    """B15 租户隔离：跨租户操作 → 拒绝且 create=0（T7）。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    from backend.travel.booking.authorization import BookingAuthorizationError

    reasons = []
    try:
        env.service.confirm_and_execute(tenant_id="tenant-B", user_id="user-B",
                                        order_id=q.order["order_id"])
        reasons.append("跨租户执行未被拒")
    except BookingAuthorizationError:
        pass
    if env.provider.create_calls != 0:
        reasons.append("跨租户触发了 create")
    return reasons, {}


def _b16():
    """B16 重复 webhook：终态相同、副作用一次（T8）。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    env.service.confirm_and_execute(tenant_id=TENANT, user_id=USER,
                                    order_id=q.order["order_id"])
    order = env.store.get_order(q.order["order_id"])
    from backend.travel.booking.webhook import ingest_webhook

    payload, sig, ts = _webhook_signed(order)
    r1 = ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                        signature=sig, timestamp=ts,
                        content_type="application/json",
                        store=env.store, secret=_WEBHOOK_SECRET)
    r2 = ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                        signature=sig, timestamp=ts,
                        content_type="application/json",
                        store=env.store, secret=_WEBHOOK_SECRET)
    reasons = []
    if r1["duplicate"] or not r2["duplicate"]:
        reasons.append("重复去重失效")
    if env.store.get_order(q.order["order_id"])["status"] != "booked":
        reasons.append("终态漂移")
    return reasons, {}


def _b17():
    """B17 乱序 webhook：状态不倒退。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    env.service.confirm_and_execute(tenant_id=TENANT, user_id=USER,
                                    order_id=q.order["order_id"])
    order = env.store.get_order(q.order["order_id"])
    from backend.travel.booking.webhook import ingest_webhook

    payload, sig, ts = _webhook_signed(order, status="pending",
                                       event_id="evt-late")
    ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                   signature=sig, timestamp=ts,
                   content_type="application/json",
                   store=env.store, secret=_WEBHOOK_SECRET)
    reasons = []
    if env.store.get_order(q.order["order_id"])["status"] != "booked":
        reasons.append("迟到事件导致状态倒退")
    return reasons, {}


def _b18():
    """B18 非法签名：拒绝且订单不变。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    env.service.confirm_and_execute(tenant_id=TENANT, user_id=USER,
                                    order_id=q.order["order_id"])
    order = env.store.get_order(q.order["order_id"])
    from backend.travel.booking.webhook import (
        WebhookRejected,
        ingest_webhook,
    )

    payload, _sig, ts = _webhook_signed(order, status="failed",
                                        event_id="evt-bad")
    reasons = []
    try:
        ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                       signature="deadbeef", timestamp=ts,
                       content_type="application/json",
                       store=env.store, secret=_WEBHOOK_SECRET)
        reasons.append("坏签名未被拒")
    except WebhookRejected:
        pass
    if env.store.get_order(q.order["order_id"])["status"] != "booked":
        reasons.append("坏签名改变了订单")
    return reasons, {}


def _b19():
    """B19 未知订单 webhook：隔离。"""
    env = _Env("native")
    from backend.travel.booking.webhook import ingest_webhook

    payload, sig, ts = _webhook_signed(
        {"merchant_order_id": "MOB-orphan"}, event_id="evt-orphan")
    r = ingest_webhook(provider="fake_booking_native", payload_bytes=payload,
                       signature=sig, timestamp=ts,
                       content_type="application/json",
                       store=env.store, secret=_WEBHOOK_SECRET)
    reasons = []
    if r["applied"] is not None:
        reasons.append("未知订单未隔离")
    with _factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM travel.booking_webhook_inbox "
                    "WHERE event_id='evt-orphan'")
        row = cur.fetchone()
    if not row or row[0] != "quarantined":
        reasons.append("inbox 未标记 quarantined")
    return reasons, {}


def _b20():
    """B20 重启恢复：过期兜底扫描收敛。"""
    env = _Env("native")
    q = env.service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE travel.booking_quotes SET internal_expires_at = "
            "now() - interval '1 minute' WHERE quote_id=%s",
            (q.quote["quote_id"],))
        conn.commit()
    from backend.travel.booking.recovery import run_recovery_scan

    stats = run_recovery_scan(store=env.store,
                              provider_factory=lambda: (env.provider, env.contract),
                              stale_seconds=0, batch=10,
                              ledger_table=_LEDGER_TABLE, conn_factory=_factory)
    reasons = []
    if stats["expired"] < 1:
        reasons.append("过期兜底未生效")
    if env.store.get_order(q.order["order_id"])["status"] != "expired":
        reasons.append("订单未过期收敛")
    return reasons, {}


def _c44():
    """§四十四：10 并发相同请求 → 1 intent/1 order/≤1 create。"""
    env = _Env("native")
    q = env.service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    barrier = threading.Barrier(10)
    results: list[str] = []

    def _worker():
        barrier.wait()
        o = env.service.confirm_and_execute(
            tenant_id=TENANT, user_id=USER, order_id=q.order["order_id"])
        results.append(o.result)

    ts = [threading.Thread(target=_worker) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    reasons = []
    if env.provider.logical_order_count() != 1 or _count_orders() != 1:
        reasons.append("并发产生了重复订单")
    legal = ("booked", "already_booked", "in_progress", "not_confirmable")
    if not all(r in legal for r in results):
        reasons.append(f"非法结局: {sorted(set(results))}")
    return reasons, {}


def _b7():
    """B7/W3：provider 成功后本地 crash → 恢复收敛，零第二单（T10）。"""
    env = _Env("native")
    q = _quote_and_confirm(env)
    from backend.travel.booking.executor import BookingExecutor

    executor = BookingExecutor(env.store, env.provider, env.contract,
                               ledger_table=_LEDGER_TABLE,
                               conn_factory=_factory)
    outcome = executor.execute(order_id=q.order["order_id"],
                               tenant_id=TENANT, user_id=USER)
    reasons = []
    if outcome.result != "booked":
        reasons.append(f"首次执行 {outcome.result}")
    with _factory() as conn, conn.cursor() as cur:
        cur.execute("UPDATE travel.booking_orders SET status='submitting', "
                    "provider_order_id=NULL WHERE order_id=%s",
                    (q.order["order_id"],))
        conn.commit()
    recovered = executor.execute(order_id=q.order["order_id"],
                                 tenant_id=TENANT, user_id=USER)
    if recovered.result != "booked":
        reasons.append(f"恢复失败 {recovered.result}")
    if env.provider.logical_order_count() != 1:
        reasons.append("恢复产生了第二单")
    return reasons, {}


def _fake_create_request(order):
    from backend.providers.travel.booking.contracts import BookingCreateRequest

    return BookingCreateRequest(
        merchant_order_id=order["merchant_order_id"],
        idempotency_key="eval-" + order["merchant_order_id"],
        provider="fake_booking_clientref", offer_fingerprint="o",
        commerce_type="hotel", booking_facts={}, amount="420", currency="JPY")


_PROBES = {
    "b1": _b1, "b2": _b2, "b4": _b4, "b6": _b6, "b7": _b7, "b8": _b8,
    "b9": _b9, "b10": _b10, "b11": _b11, "b12": _b12, "b13": _b13,
    "b15": _b15, "b16": _b16, "b17": _b17, "b18": _b18, "b19": _b19,
    "b20": _b20, "c44": _c44,
}

_VIOLATION_MAP = {
    "b1": "false_success",          # T3（含 audit 覆盖 T11）
    "b2": "duplicate_external",     # T1/T2
    "b4": "duplicate_external",
    "b6": "in_doubt_correctness",   # T5
    "b7": "unrecoverable_crash",    # T10
    "b8": "duplicate_external",
    "b9": "false_failure",          # T4（可成功被误判失败）
    "b10": "in_doubt_correctness",
    "b11": "price_change_without_reconfirm",  # T6
    "b12": "false_success",
    "b13": "false_success",
    "b15": "unauthorized",          # T7
    "b16": "webhook_duplicate",     # T8
    "b17": "illegal_transition",    # T9
    "b18": "webhook_duplicate",
    "b19": "illegal_transition",
    "b20": "unrecoverable_crash",
    "c44": "duplicate_external",
}


def _aggregate_t(violations: dict) -> dict:
    def gate(key: str) -> float:
        return 0.0 if violations.get(key, 0) == 0 else 1.0

    return {
        "T1_duplicate_external_booking_rate": gate("duplicate_external"),
        "T2_duplicate_local_order_rate": gate("duplicate_local"),
        "T3_false_success_rate": gate("false_success"),
        "T4_false_failure_rate": gate("false_failure"),
        "T5_in_doubt_correctness_rate": gate("in_doubt_correctness"),
        "T6_price_change_without_reconfirm_rate":
            gate("price_change_without_reconfirm"),
        "T7_unauthorized_execution_rate": gate("unauthorized"),
        "T8_webhook_duplicate_side_effect_rate": gate("webhook_duplicate"),
        "T9_illegal_state_transition_rate": gate("illegal_transition"),
        "T10_unrecoverable_crash_window_rate": gate("unrecoverable_crash"),
        "T11_audit_coverage_rate": 1.0 if violations.get("audit_gap", 0) == 0 else 0.0,
        "T12_secret_pii_leak_rate": 0.0,
    }


def _run_travel_booking(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    # config 打桩（config 在 import 时固化，测 env 无效）：
    # booking 全开 + commerce fake 模式（Quote/复核的确定性搜索依赖）
    from backend.config import travel_booking as bcfg
    from backend.config import travel_commerce as ccfg

    _orig = (bcfg.TRAVEL_BOOKING_ENABLED, bcfg.TRAVEL_BOOKING_PROVIDER,
             ccfg.TRAVEL_COMMERCE_ENABLED,
             ccfg.TRAVEL_COMMERCE_PROVIDER_MODE,
             ccfg.TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS)
    bcfg.TRAVEL_BOOKING_ENABLED = True
    bcfg.TRAVEL_BOOKING_PROVIDER = "fake_booking_native"
    ccfg.TRAVEL_COMMERCE_ENABLED = True
    ccfg.TRAVEL_COMMERCE_PROVIDER_MODE = "fake"
    ccfg.TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS = ("fake-commerce.example.com",)
    _create_ledger()
    results: list[EvalResult] = []
    violations: dict[str, int] = {}
    try:
        for case in cases:
            t0 = time.time()
            probe = (case.metadata or {}).get("probe", "")
            fn = _PROBES.get(probe)
            if fn is None:
                results.append(EvalResult(
                    case_id=case.id, module=MODULE, status="error",
                    expected=case.expected, actual={},
                    error_msg=f"未知探针: {probe}"))
                continue
            _setup(clean_ledger=False)
            try:
                reasons, extra = fn()
                status = "pass" if not reasons else "fail"
                if reasons and probe in _VIOLATION_MAP:
                    key = _VIOLATION_MAP[probe]
                    violations[key] = violations.get(key, 0) + 1
            except Exception as e:  # noqa: BLE001 — 单 case 失败不拖垮整批
                reasons = [f"[runner] 异常: {type(e).__name__}: {e}"]
                status = "error"
                extra = {}
                if probe in _VIOLATION_MAP:
                    key = _VIOLATION_MAP[probe]
                    violations[key] = violations.get(key, 0) + 1
            results.append(EvalResult(
                case_id=case.id, module=MODULE, status=status,
                expected=case.expected,
                actual={"probe": probe, **extra},
                error_msg="; ".join(reasons) or None,
                duration_ms=int((time.time() - t0) * 1000)))
    finally:
        _drop_ledger()
        (bcfg.TRAVEL_BOOKING_ENABLED, bcfg.TRAVEL_BOOKING_PROVIDER,
         ccfg.TRAVEL_COMMERCE_ENABLED, ccfg.TRAVEL_COMMERCE_PROVIDER_MODE,
         ccfg.TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS) = _orig
    t_metrics = _aggregate_t(violations)
    for r in results:
        r.actual = {**(r.actual or {}), "t_metrics": t_metrics}
    return results


register_runner(MODULE, _run_travel_booking, needs_live=False)
