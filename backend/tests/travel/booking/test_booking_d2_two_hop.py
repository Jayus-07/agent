"""tests/travel/booking/test_booking_d2_two_hop.py — Phase 5 / D2 两跳断片修复

适配器级两跳回归（无 PG、无 Provider）：验证「反问 → 用户答槽位值」这一
跨轮链路在**真实子图 + 真实适配器 + 真实会话上下文**上闭环。

链路（审计报告 §六 Commit A ②③④）：
  hop1  用户「帮我预订大阪的酒店」→ 子图缺槽 → 适配器写挂起
        （booking_intent）+ 回写活跃域（G1/G2）
  hop2  用户「2027年10月3日」→ 子图从挂起续填（G3）→ 已收 check_in、仍缺 check_out
        → 挂起按同一 question_id 更新（续填不是新挂起）

澄清路径不触达 BookingService（executor 在 action=unknown 时直接返回），
故本文件不依赖 PG / 交易 Provider。
"""
from __future__ import annotations

from backend.orchestration.context.routing_context import (
    assemble_routing_context,
)
from backend.travel.booking.graph_node import travel_booking_graph_node

_TENANT, _USER, _SESSION = "t-d2", "u-d2", "sess-d2-two-hop"


def _state(question: str) -> dict:
    return {
        "question": question,
        "query": question,
        "user_id": _USER,
        "tenant_id": _TENANT,
        "session_id": _SESSION,
    }


def _intent(repo):
    snap = repo.peek(_TENANT, _USER, _SESSION)
    return (snap.booking_intent if snap else None) or {}


def test_two_hop_clarify_then_slot_answer_resumes(_memory_context_repo):
    repo = _memory_context_repo
    repo.delete(_TENANT, _USER, _SESSION)

    # ── hop1：新预订缺日期 → 追问 + 挂起 ─────────────────────────
    out1 = travel_booking_graph_node(_state("帮我预订大阪的酒店"))
    assert out1["final_answer"], "首轮必须给出追问文案"

    it1 = _intent(repo)
    assert it1.get("route_mode") == "travel_booking"
    assert it1.get("kind") == "hotel"
    assert it1.get("collected", {}).get("city") == "大阪"
    assert set(it1.get("missing_slots") or []) == {"check_in", "check_out"}
    qid1 = it1.get("question_id")
    assert qid1, "挂起必须带 question_id（续填 CAS 依据）"

    # 活跃域已登记（G1）：否则下一轮纯槽位值回答没人接住
    ctx = assemble_routing_context(_TENANT, _USER, _SESSION)
    assert ctx["active_domain"] == "travel_booking"
    assert (ctx["brief_summary"].get("booking_intent") or {}).get("kind") == "hotel"

    # ── hop2：纯槽位值回答 → 路由层判定命中 → 子图续填 ───────────
    from backend.orchestration.context.booking_pending_resolver import (
        resolve_booking_pending,
    )

    route = resolve_booking_pending("2027年10月3日", ctx)
    assert route is not None and route["route_mode"] == "travel_booking"

    out2 = travel_booking_graph_node(_state("2027年10月3日"))
    assert out2["final_answer"]

    it2 = _intent(repo)
    assert it2.get("collected", {}).get("check_in") == "2027-10-03"
    assert it2.get("collected", {}).get("city") == "大阪"     # 上轮槽位保留
    assert it2.get("missing_slots") == ["check_out"]          # 只差退房日期
    # 续填是同一件事的推进：question_id 保留，不作废成新挂起
    assert it2.get("question_id") == qid1

    repo.delete(_TENANT, _USER, _SESSION)


def test_hop2_city_answer_fills_city_slot(_memory_context_repo):
    """变体：首句只给意图（缺城市），答城市名 → 补上 city。"""
    repo = _memory_context_repo
    repo.delete(_TENANT, _USER, _SESSION)

    travel_booking_graph_node(_state("帮我订一间酒店"))
    it1 = _intent(repo)
    assert "city" in (it1.get("missing_slots") or [])

    travel_booking_graph_node(_state("大阪"))
    it2 = _intent(repo)
    assert it2.get("collected", {}).get("city") == "大阪"
    assert "city" not in (it2.get("missing_slots") or [])

    repo.delete(_TENANT, _USER, _SESSION)


def test_router_dispatches_booking_pending_back(_memory_context_repo):
    """路由层接线守护：router_node 见交易挂起 + 槽位值回答 → 回子图。

    覆盖 G1（_ROUTE_MODE_DOMAIN 登记）+ 新解析器在 router_node 的接入点。
    """
    repo = _memory_context_repo
    repo.delete(_TENANT, _USER, _SESSION)
    travel_booking_graph_node(_state("帮我预订大阪的酒店"))

    import backend.orchestration.graph.router_node as rn

    state = {
        "question": "2027年10月3日",
        "session_id": _SESSION,
        "user_id": _USER,
        "tenant_id": _TENANT,
        "domain_hint": "",
        "guard_result": {"category": "business_query"},
        "routing_context": assemble_routing_context(_TENANT, _USER, _SESSION),
    }
    update = rn.router_node(state)
    assert update.get("route_mode") == "travel_booking"

    repo.delete(_TENANT, _USER, _SESSION)
