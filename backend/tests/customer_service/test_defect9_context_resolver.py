"""缺陷9（2026-09-23）回归：客服域多轮业务上下文继承。

核心场景「查 MO-1001 → 那它到哪了」此前被路由 UNKNOWN→k_faq→knowledge→
RAG 拒答。修复 = Context Resolver 在 CS Router 判域之前解析回指表达，
继承上一轮唯一订单实体。本文件覆盖任务书 6.7 的 Test 1-10。

隔离原则：Recent Business Context 绑定 (tenant, user, session) 三元组，
存储经 get_cache（测试中替换为内存桩，不依赖 Redis）。
"""
from __future__ import annotations

import pytest

from backend.customer_service import context_resolver as cr
from backend.customer_service.context_resolver import (
    context_key,
    record_recent_order,
    record_recent_orders,
    resolve_turn_reference,
)


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch):
    """get_cache 换成进程内 dict 桩（不依赖 Redis）。"""
    store: dict = {}

    class _FakeCache:
        def get_json(self, key):
            return store.get(key)

        def set_json(self, key, value, ttl=None):
            if value is None:
                store.pop(key, None)
            else:
                store[key] = value

    monkeypatch.setattr(cr, "_cache", lambda: _FakeCache())
    return store


def _seed(order_id="MO-1001", user="u1", session="s1", tenant="default"):
    record_recent_order(tenant, user, session, order_id, source_intent="t_order_status")


def _resolve(query, user="u1", session="s1", tenant="default"):
    return resolve_turn_reference(
        query, tenant_id=tenant, user_id=user, session_id=session,
    )


# ── Test 1：核心 #9 ——「那它到哪了」继承上一轮唯一订单 ─────────


def test_t1_core_reference_inherits_last_order():
    _seed("MO-1001")
    r = _resolve("那它到哪了")
    assert r is not None
    assert r.order_id == "MO-1001"
    assert "MO-1001" in r.resolved_query
    assert "物流" in r.resolved_query
    assert r.resolution_type == "inherited_entity"
    assert r.trace_fields()["entity_source"] == "recent_business_context"
    assert r.trace_fields()["original_query"] == "那它到哪了"


def test_t1_status_variant_resolves_without_logistics_word():
    _seed("MO-1001")
    r = _resolve("它现在什么状态")
    assert r is not None and r.order_id == "MO-1001"
    assert "最新状态" in r.resolved_query


# ── Test 2：当前轮显式实体永远优先 ─────────────────────────────


def test_t2_explicit_order_id_overrides_inheritance():
    _seed("MO-1001")
    # 「那 MO-1002 呢」含显式订单号 → resolver 必须让位（不产生继承注入）
    assert _resolve("那 MO-1002 呢") is None


def test_t2_explicit_wins_at_query_expert_layer():
    """query expert 层：显式抽取的订单号不得被继承值顶掉——注入仅发生在
    resolver 返回 None 的场景，metadata 无残留。"""
    from backend.customer_service.experts.query import _dispatch_service

    captured = {}

    class FakeOrderService:
        def query_orders(self, user_id, order_id=None, query_type="list"):
            captured["order_id"] = order_id
            from backend.customer_service.service.order_service import (
                OrderQueryResult,
            )
            return OrderQueryResult(
                orders=[{"order_no": order_id, "status": "shipped",
                         "total_amount": 1.0}],
                total_count=1, query_type="detail",
            )

    from backend.customer_service.service import order_service as os_mod
    monkey = pytest.MonkeyPatch()
    monkey.setattr(os_mod, "get_order_service", lambda: FakeOrderService())
    try:
        # 模拟「那 MO-1002 呢」被路由后：metadata 不含注入（resolver 让位），
        # 订单号来自文本抽取
        _dispatch_service(
            "u1", "t_order_status", "那 MO-1002 呢", {"metadata": {}},
            tenant_id="default", session_id="s1",
        )
        assert captured["order_id"] == "MO-1002"
    finally:
        monkey.undo()


# ── Test 3：普通知识问题不继承 ─────────────────────────────────


def test_t3_knowledge_question_not_injected():
    _seed("MO-1001")
    assert _resolve("保修多久") is None
    assert _resolve("退货政策是什么") is None


# ── Test 4：新 intent 不被旧订单吞掉 ───────────────────────────


def test_t4_new_intent_not_swallowed():
    _seed("MO-1001")
    assert _resolve("我要投诉") is None
    assert _resolve("帮我写周报") is None


# ── Test 5：pending action 优先（补槽链路不被 resolver 抢走）────


def test_t5_pending_slot_reply_not_intercepted():
    """「我要退货→请提供订单号→MO-1001」：MO-1001 是显式订单号，
    resolver 必须让位（返回 None）——补槽由 CS 图内 pending_handler 链路
    承接，不会被改写成普通订单查询。"""
    _seed("MO-1001")  # 即使上下文里恰好有订单
    assert _resolve("MO-1001") is None  # 显式实体让位 → pending 补槽照常


def test_t5b_stop_b_regression_suite_still_passes():
    """#8 回归（缺槽/补槽/网关一致性）由 test_defect6_action_chain 锁定，
    本用例保证其仍被收集执行（导入即收集，防止意外移除）。"""
    import backend.tests.customer_service.test_defect6_action_chain as m  # noqa: F401
    assert hasattr(m, "test_a_missing_order_id_asks_instead_of_latest")
    assert hasattr(m, "test_b_slot_fill_resumes_return_intent")


# ── Test 6：无上下文不凭空产生订单 ─────────────────────────────


def test_t6_no_context_no_hallucinated_order():
    assert _resolve("那它到哪了") is None
    assert _resolve("它发货了吗") is None


# ── Test 7：多订单无唯一 referent 不猜测 ───────────────────────


def test_t7_list_query_never_records_single_referent(monkeypatch):
    """「查我的订单」列表返回多单：禁止写入 last_order_id（否则下轮回指
    会随机选中一单）。"""
    captured = {}

    class FakeOrderService:
        def query_orders(self, user_id, order_id=None, query_type="list"):
            from backend.customer_service.service.order_service import (
                OrderQueryResult,
            )
            return OrderQueryResult(
                orders=[
                    {"order_no": "MO-1001", "status": "paid"},
                    {"order_no": "MO-1002", "status": "shipped"},
                ],
                total_count=2, query_type="list",
            )

    from backend.customer_service.service import order_service as os_mod
    monkeypatch.setattr(os_mod, "get_order_service", lambda: FakeOrderService())

    import backend.customer_service.context_resolver as cr_mod
    monkeypatch.setattr(
        cr_mod, "record_recent_order",
        lambda *a, **k: captured.update(args=a, kwargs=k),
    )

    import backend.customer_service.experts.query as q_mod
    q_mod._dispatch_service(
        "u1", "t_order_status", "查我的订单", {"metadata": {}},
        tenant_id="default", session_id="s1",
    )
    assert captured == {}, "列表查询不得记录唯一订单上下文"


def test_t7b_explicit_ordinal_selects_from_recent_order_list():
    """列表后明确说“第一个订单”是显式选择，不属于猜测。"""
    record_recent_orders(
        "default", "u1", "s1", ["MO-1001", "MO-1002"],
        source_intent="t_order_status",
    )
    resolved = _resolve("第一个订单什么状态")
    assert resolved is not None
    assert resolved.order_id == "MO-1001"
    assert resolved.resolved_query == "查询订单 MO-1001 的最新状态"


def test_t7c_ordinal_out_of_range_does_not_guess():
    record_recent_orders(
        "default", "u1", "s1", ["MO-1001"],
        source_intent="t_order_status",
    )
    assert _resolve("第二个订单什么状态") is None


# ── Test 8：session 隔离 ───────────────────────────────────────


def test_t8_session_isolation():
    _seed("MO-A", session="sA")
    assert _resolve("那它到哪了", session="sB") is None
    assert _resolve("那它到哪了", session="sA") is not None


# ── Test 9：user 隔离 ──────────────────────────────────────────


def test_t9_user_isolation():
    _seed("MO-A", user="alice", session="shared")
    assert _resolve("那它到哪了", user="bob", session="shared") is None
    assert _resolve("那它到哪了", user="alice", session="shared") is not None


def test_t9_tenant_isolation():
    _seed("MO-A", user="u1", session="s1", tenant="t1")
    assert _resolve("那它到哪了", user="u1", session="s1", tenant="t2") is None
    assert context_key("t1", "u1", "s1") != context_key("t2", "u1", "s1")


# ── 补充：resolver 决策边界 ────────────────────────────────────


def test_resolver_skips_when_explicit_order_present_even_without_context():
    """显式订单号优先是无条件的：即使无上下文也不应有任何继承动作。"""
    assert _resolve("查一下 MO-9999 到哪了") is None


def test_record_then_get_roundtrip():
    record_recent_order("default", "u9", "s9", "MO-77", source_intent="t_order_status")
    ctx = cr.get_recent_business_context("default", "u9", "s9")
    assert ctx["last_order_id"] == "MO-77"
    assert ctx["source_intent"] == "t_order_status"
    assert ctx["updated_at"]


# ── 补充：显式订单号的路由纠偏（第一轮「查 MO-1001」落 knowledge 的修复）──


def test_explicit_order_route_resolution():
    from backend.customer_service.context_resolver import (
        resolve_explicit_order_route,
    )

    r = resolve_explicit_order_route("查 MO-3C052B3A")
    assert r is not None
    assert r.order_id == "MO-3C052B3A"
    assert r.resolved_query == "查询订单 MO-3C052B3A"
    assert r.resolution_type == "explicit_entity"
    assert r.trace_fields()["original_query"] == "查 MO-3C052B3A"
    assert resolve_explicit_order_route("保修多久") is None


def test_explicit_route_lands_on_business_query_via_router():
    """resolved_query「查询订单 X」经 CS Router 落 TRANSACTION/t_order_status
    （query expert），不得落 knowledge——这是「查 MO-1001」本身的修复点。"""
    from backend.customer_service.context_resolver import (
        resolve_explicit_order_route,
    )
    from backend.customer_service.router.cs_router import get_cs_router
    from backend.customer_service.router.domain_detector import detect_cached

    r = resolve_explicit_order_route("查 MO-3C052B3A")
    route = get_cs_router().route(r.resolved_query, detect_cached(r.resolved_query))
    assert route.intent == "t_order_status"
    assert route.route_path.value == "business_query"
