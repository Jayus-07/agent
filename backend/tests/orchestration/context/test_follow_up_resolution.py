"""P2/D1 验收：ConversationContext + FollowUpResolver（方案 §P2.12）。

7 个用例全部基于规则解析（确定性，无 LLM、无随机）：
  Case 1 云南 → 当地美食 → standalone 带云南
  Case 2 云南 → 大理 → 那边 → 优先大理
  Case 3 云南 5 天 → 改成成都 → 那里 → 只用成都、不残留云南
  Case 4 全新会话「那边天气怎么样？」→ need_clarification
  Case 5 连续 5 轮结构化上下文不丢失
  Case 6 同用户两个 conversation 不串上下文
  Case 7 两 tenant 相同用户名不串上下文
"""

from __future__ import annotations

import pytest

from backend.orchestration.context.conversation_context import (
    ConversationContext,
    ConversationContextStore,
    get_conversation_context_store,
    sync_travel_brief_to_context,
)
from backend.orchestration.context.follow_up_resolver import (
    CLARIFICATION_QUESTION,
    apply_resolution_to_context,
    resolve_followup,
)


@pytest.fixture()
def store():
    return ConversationContextStore(ttl_seconds=3600, max_entries=100)


def _ctx(store, tenant, user, conv) -> ConversationContext:
    return store.get(tenant, user, conv)


def test_case1_destination_context_resolves_dangdi(store):
    """Q1 云南 → Q2「那当地有什么特色美食？」→ 第二轮带云南。"""
    ctx = _ctx(store, "t1", "u1", "c1")
    # Q1 经旅游域图后同步的 brief 摘要
    ctx.merge_slots({"destination": "云南"})

    r2 = resolve_followup("那当地有什么特色美食？", ctx)
    assert r2["follow_up_detected"] is True
    assert r2["resolved"] is True
    assert r2["rewrite_method"] == "rule"
    assert "云南" in r2["standalone_query"]
    assert "特色美食" in r2["standalone_query"]
    # 原始输入不被覆盖
    assert r2["raw_query"] == "那当地有什么特色美食？"


def test_case2_recent_city_beats_destination(store):
    """云南 → 大理住两天 → 「那边有什么好吃的？」→ 优先大理。"""
    ctx = _ctx(store, "t1", "u1", "c2")
    ctx.merge_slots({"destination": "云南"})
    # Q2「大理住两天」经 slot filler 后 cities 落地
    ctx.merge_slots({"destination": "云南", "cities": ["大理"]})

    r3 = resolve_followup("那边有什么好吃的？", ctx)
    assert r3["resolved"] is True
    assert "大理" in r3["standalone_query"]
    assert r3["used_context"] == ["cities"]


def test_case3_overwrite_clears_old_destination(store):
    """云南玩 5 天 → 「算了，改成成都」→ 「那里有什么特色美食？」→ 只用成都。"""
    ctx = _ctx(store, "t1", "u1", "c3")
    ctx.merge_slots({"destination": "云南", "days": 5, "cities": ["丽江"]})
    ctx.touch_evidence(["src-yunnan-1", "src-yunnan-2"])

    # Q2 显式切换
    r2 = resolve_followup("算了，改成成都", ctx)
    assert r2["follow_up_detected"] is True
    assert r2["overwrite_destination"] == "成都"
    apply_resolution_to_context(ctx, r2)
    assert ctx.destination == "成都"
    assert ctx.days == 5  # 「5 天游云南，改成成都」→ days 保留
    assert ctx.cities == []
    assert ctx.last_verified_source_ids == []  # 旧 evidence 清掉
    assert ctx.source_context_fingerprint == ""

    # Q3 只能用成都
    r3 = resolve_followup("那里有什么特色美食？", ctx)
    assert r3["resolved"] is True
    assert "成都" in r3["standalone_query"]
    assert "云南" not in r3["standalone_query"]


def test_case4_no_context_requires_clarification(store):
    """全新会话「那边天气怎么样？」→ 澄清，不猜城市。"""
    ctx = _ctx(store, "t1", "u1", "c4-fresh")
    r = resolve_followup("那边天气怎么样？", ctx)
    assert r["follow_up_detected"] is True
    assert r["resolved"] is False
    assert r["need_clarification"] is True
    assert r["clarification_question"] == CLARIFICATION_QUESTION
    # 不产出臆测的 standalone
    assert r["standalone_query"] == "那边天气怎么样？"


def test_case5_five_rounds_context_survives(store):
    """云南 → 大理 → 第二天呢 → 便宜一点呢 → 再推荐几个：上下文不丢。"""
    ctx = _ctx(store, "t1", "u1", "c5")
    ctx.merge_slots({"destination": "云南"})

    r2 = resolve_followup("大理有哪些景点？", ctx)  # 无标记，原样通过
    assert r2["follow_up_detected"] is False
    ctx.merge_slots({"destination": "云南", "cities": ["大理"]})

    r3 = resolve_followup("第二天呢", ctx)
    assert r3["resolved"] is True
    assert "大理" in r3["standalone_query"]
    assert "第二天" in r3["standalone_query"]
    apply_resolution_to_context(ctx, r3)

    r4 = resolve_followup("便宜一点呢", ctx)
    assert r4["resolved"] is True
    assert "大理" in r4["standalone_query"]
    apply_resolution_to_context(ctx, r4)

    r5 = resolve_followup("再推荐几个", ctx)
    assert r5["resolved"] is True
    assert "大理" in r5["standalone_query"]
    apply_resolution_to_context(ctx, r5)

    # 结构化槽位仍在
    assert ctx.destination == "云南"
    assert ctx.cities == ["大理"]


def test_case6_two_conversations_no_cross_leak(store):
    """同用户两个 conversation：云南 vs 成都，互不串。"""
    ctx_a = _ctx(store, "t1", "u1", "conv-a")
    ctx_b = _ctx(store, "t1", "u1", "conv-b")
    ctx_a.merge_slots({"destination": "云南"})
    ctx_b.merge_slots({"destination": "成都"})

    ra = resolve_followup("那边有什么好吃的？", ctx_a)
    rb = resolve_followup("那边有什么好吃的？", ctx_b)
    assert "云南" in ra["standalone_query"]
    assert "成都" in rb["standalone_query"]
    assert "云南" not in rb["standalone_query"]
    assert "成都" not in ra["standalone_query"]


def test_case7_two_tenants_same_username_no_cross_leak(store):
    """两 tenant 相同用户名/会话名：不串上下文。"""
    ctx_a = _ctx(store, "tenant-a", "张三", "conv-1")
    ctx_b = _ctx(store, "tenant-b", "张三", "conv-1")
    ctx_a.merge_slots({"destination": "云南"})
    ctx_b.merge_slots({"destination": "成都"})

    ra = resolve_followup("那边住宿推荐呢？", ctx_a)
    rb = resolve_followup("那边住宿推荐呢？", ctx_b)
    assert "云南" in ra["standalone_query"]
    assert "成都" in rb["standalone_query"]


def test_deterministic_resolution(store):
    """同一输入重复解析结果完全一致（确定性，无随机）。"""
    ctx = _ctx(store, "t1", "u1", "c-det")
    ctx.merge_slots({"destination": "云南"})
    a = resolve_followup("那当地有什么特色美食？", ctx)
    b = resolve_followup("那当地有什么特色美食？", ctx)
    assert a["standalone_query"] == b["standalone_query"]
    assert a["used_context"] == b["used_context"]


def test_travel_brief_sync_updates_context_and_clears_evidence_on_destination_change():
    """sync_travel_brief_to_context：槽位单向同步；目的地变化清旧证据。

    sync 写入的是进程级单例 store，用唯一 conv id 隔离并在结束后清理。
    """
    conv_id = "conv-sync-test-only"
    store = get_conversation_context_store()
    try:
        store.reset("t1", "u1", conv_id)
        sync_travel_brief_to_context("t1", "u1", conv_id, {"destination": "云南"})
        ctx = store.get("t1", "u1", conv_id)
        assert ctx.destination == "云南"
        ctx.touch_evidence(["s1"])

        sync_travel_brief_to_context(
            "t1", "u1", conv_id,
            {"destination": "成都", "cities": ["宽窄巷子"], "days": 4},
        )
        ctx = store.get("t1", "u1", conv_id)
        assert ctx.destination == "成都"
        assert ctx.cities == ["宽窄巷子"]
        assert ctx.days == 4
        # 目的地变化 → 旧 evidence 不可复用
        assert ctx.last_verified_source_ids == []
        assert ctx.evidence_compatible() is False
    finally:
        store.reset("t1", "u1", conv_id)


def test_topic_change_invalidates_evidence():
    """topic 变化 → 旧 evidence 指纹不兼容（P2.10）。"""
    ctx = ConversationContext("t", "u", "c")
    ctx.set_topic("大理住宿")
    ctx.touch_evidence(["s1", "s2"])
    assert ctx.evidence_compatible() is True
    ctx.set_topic("成都美食")
    assert ctx.evidence_compatible() is False
