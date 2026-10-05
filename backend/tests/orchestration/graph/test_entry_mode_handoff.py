# -*- coding: utf-8 -*-
"""test_entry_mode_handoff.py — 域入口模式判据与 handoff 短路回归（多域隔离收官 M2/M3）

锁定三件事：
  1) **默认零变化**：三开关缺省 execute，prefilter 命中照旧进域图（既有
     prefilter 用例不改断言即可过 = 基线等价的回归门）；
  2) guide 判据：规划/漏斗/客服诉求 → handoff 引导；旅游一次性查询
     （景点/门票/在哪）→ passthrough 落回主路由（travel.poi_search 直答）；
  3) handoff 更新形态：route_mode="handoff" + 契约体过 HandoffPayloadV1 +
     不回写路由上下文（无 mark_domain_turn）。
"""
from unittest.mock import MagicMock

import pytest

from backend.orchestration.contracts.handoff import HandoffPayloadV1
from backend.orchestration.graph.routing.prefilter_chain import (
    entry_mode_verdict,
    handoff_update_for,
    run_domain_prefilters,
)
from backend.orchestration.graph import router_node as rn
from backend.customer_service.router import domain_detector as dd


@pytest.fixture(autouse=True)
def _reset_switch_cache():
    """sys_config 缓存前后各清（与 test_router_prefilter_order 同纪律）。"""
    from backend.services import sys_config

    sys_config.reset_cache_for_tests()
    yield
    sys_config.reset_cache_for_tests()


def _guide(monkeypatch, *keys):
    from backend.services import sys_config

    for key in keys:
        sys_config._values[key] = "guide"


def _domain_update(route_mode: str, **extra) -> dict:
    return {"route_decision": None, "route_mode": route_mode, **extra}


# ── 1) 默认 execute：行为零变化 ──────────────────────


def test_default_verdict_is_execute_for_all_domains():
    for mode in ("travel", "travel_booking", "travel_commerce",
                 "customer_service", "selection_funnel"):
        assert entry_mode_verdict("帮我规划福州3天行程", _domain_update(mode)) == "execute"


def test_unknown_route_mode_is_execute():
    # cs_prefilter 的业务门禁短路（clarify）等非域图归宿恒 execute
    assert entry_mode_verdict("x", _domain_update("clarify")) == "execute"
    assert entry_mode_verdict("x", None) == "execute"


# ── 2) guide 判据 ────────────────────────────────────


def test_guide_mode_travel_planning_guides(monkeypatch):
    _guide(monkeypatch, "TRAVEL_GLOBAL_ENTRY_MODE")
    update = _domain_update("travel")
    assert entry_mode_verdict("帮我规划杭州3天行程", update) == "guide"
    assert entry_mode_verdict("带爸妈福州玩2天", update) == "guide"
    # 预订/商务同属旅游族，共用同一开关
    assert entry_mode_verdict("帮我订一间福州的酒店", _domain_update("travel_booking")) == "guide"


def test_guide_mode_travel_one_shot_passthrough(monkeypatch):
    _guide(monkeypatch, "TRAVEL_GLOBAL_ENTRY_MODE")
    update = _domain_update("travel")
    # 判据左半边：一次性查询不引导，落回主路由 poi_search/rag 直答
    assert entry_mode_verdict("福州有什么景点", update) == "passthrough"
    assert entry_mode_verdict("鼓山门票多少钱", update) == "passthrough"
    assert entry_mode_verdict("三坊七巷在哪", update) == "passthrough"


def test_guide_mode_selection_and_cs_guides(monkeypatch):
    _guide(monkeypatch, "SELECTION_GLOBAL_ENTRY_MODE", "CS_GLOBAL_ENTRY_MODE")
    assert entry_mode_verdict(
        "给宠物零食做一次智能选品", _domain_update("selection_funnel")) == "guide"
    assert entry_mode_verdict(
        "我的订单怎么退款", _domain_update("customer_service")) == "guide"


# ── 3) handoff 更新形态 ──────────────────────────────


def test_handoff_update_shape_and_contract():
    update = handoff_update_for("带爸妈福州玩2天", {"session_id": "s1"}, "travel")
    assert update["route_mode"] == "handoff"
    assert update["route_decision"] is None
    payload = HandoffPayloadV1.model_validate(update["_handoff"])
    assert payload.target_domain == "travel"
    assert payload.params["destination"] == "福州"
    assert payload.text.strip()
    # 不回写路由上下文：handoff 轮没有 mark_domain_turn 的任何痕迹
    assert "travel_context" not in update
    assert "_clarify" not in update


# ── 4) run_domain_prefilters 集成 ────────────────────


@pytest.fixture
def travel_on():
    from backend.services import sys_config

    sys_config._values["TRAVEL_ENABLED"] = "true"


def test_prefilter_chain_guide_mode_emits_handoff(travel_on, monkeypatch):
    _guide(monkeypatch, "TRAVEL_GLOBAL_ENTRY_MODE")
    out = run_domain_prefilters("帮我规划杭州3天行程", {"session_id": "s1"})
    assert out is not None
    assert out["route_mode"] == "handoff"
    payload = HandoffPayloadV1.model_validate(out["_handoff"])
    assert payload.target_domain == "travel"
    assert payload.params["destination"] == "杭州"


def test_prefilter_chain_one_shot_falls_through(travel_on, monkeypatch):
    _guide(monkeypatch, "TRAVEL_GLOBAL_ENTRY_MODE")
    # 一次性查询：旅游 prefilter 放行 → 后续 prefilter 未命中 → None（回主路由）
    assert run_domain_prefilters(
        "福州有什么景点", {"session_id": "s1"}) is None


def test_prefilter_chain_execute_mode_unchanged(travel_on, monkeypatch):
    # 开关切回 execute = 完整回滚：与基线一致进域图
    from backend.services import sys_config

    sys_config._values["TRAVEL_GLOBAL_ENTRY_MODE"] = "execute"
    out = run_domain_prefilters("帮我规划杭州3天行程", {"session_id": "s1"})
    assert out is not None
    assert out["route_mode"] == "travel"
    assert "_handoff" not in out


# ── 5) router_node 级：guide 短路 + 域锁不受影响 ─────


@pytest.fixture
def fake_detector(monkeypatch):
    det = MagicMock()
    det.rule_hit_count = 0
    det._rule_channel.return_value = ([], 0.0)
    det.detect.return_value = MagicMock(is_cs=False, confidence=0.9)
    monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
    monkeypatch.setattr(dd, "_DETECT_CACHE", {})
    return det


def test_router_node_handoff_short_circuit(travel_on, fake_detector, monkeypatch):
    _guide(monkeypatch, "TRAVEL_GLOBAL_ENTRY_MODE")
    out = rn.router_node(
        {"question": "帮我规划杭州3天行程", "session_id": "s1"})
    assert out.get("route_mode") == "handoff"
    assert HandoffPayloadV1.model_validate(out["_handoff"]).target_domain == "travel"


def test_router_node_cs_global_guide(travel_on, fake_detector, monkeypatch):
    """全局入口 CS 命中 + guide 模式 → handoff；不依赖真实 CS 检测链。"""
    _guide(monkeypatch, "CS_GLOBAL_ENTRY_MODE")
    monkeypatch.setattr(rn, "cs_rule_hits_of", lambda query: 1)
    monkeypatch.setattr(
        rn, "_try_cs_prefilter",
        lambda query, state, forced=False: _domain_update("customer_service"),
    )
    out = rn.router_node({"question": "我的订单怎么退款", "session_id": "s2"})
    assert out.get("route_mode") == "handoff"
    payload = HandoffPayloadV1.model_validate(out["_handoff"])
    assert payload.target_domain == "customer_service"
    assert payload.params["prefill_question"] == "我的订单怎么退款"


def test_router_node_cs_forced_lock_bypasses_guide(travel_on, fake_detector, monkeypatch):
    """域锁入口（CSDrawer）不经过模式开关：guide 开着仍进 CS 域图。"""
    _guide(monkeypatch, "CS_GLOBAL_ENTRY_MODE")
    monkeypatch.setattr(
        rn, "_try_cs_prefilter",
        lambda query, state, forced=False: _domain_update("customer_service"),
    )
    out = rn.router_node({
        "question": "我的订单怎么退款", "session_id": "s3",
        "domain_hint": "customer_service",
    })
    assert out.get("route_mode") == "customer_service"
    assert "_handoff" not in out
