# -*- coding: utf-8 -*-
"""test_main_lock_domain.py — AI 助手页锁域（domain_hint=main）回归（2026-10-08）

产品拍板「隔离+引导卡」：/agent 页每条消息带 domain_hint=main：
- 跳过旅游/预订/选品/商务 prefilter 与延续判定——域请求不在本页执行
- 旅游/选品/预订/商务强信号 → handoff 引导卡（target=travel/selection_funnel）
- general_chat 与主路由（sql/rag/plan）不受影响
- clarify 不给旅游/选品倾向选项（_MAIN_GENERIC_OPTIONS）
"""
from unittest.mock import MagicMock

import pytest

from backend.customer_service.router import domain_detector as dd
from backend.orchestration.graph import router_node as rn
from backend.orchestration.graph.clarify_content import build_refusal_clarify
from backend.orchestration.graph.routing.lock_domain import (
    is_main_forced,
    main_forced_handoff_update,
)


def _fake_detection(is_cs: bool = False):
    d = MagicMock()
    d.is_cs = is_cs
    d.confidence = 0.9
    d.domain = "AFTER_SALES"
    d.rule_hits = []
    d.rule_score = 0.0
    d.vector_score = 0.0
    d.reason = ""
    return d


@pytest.fixture
def fake_detector(monkeypatch):
    det = MagicMock()
    det.rule_hit_count = 0
    det._rule_channel.return_value = ([], 0.0)
    det.detect.return_value = _fake_detection(is_cs=False)
    monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
    monkeypatch.setattr(dd, "_DETECT_CACHE", {})
    return det


@pytest.fixture(autouse=True)
def _reset_switch_cache():
    from backend.services import sys_config

    sys_config.reset_cache_for_tests()
    yield
    sys_config.reset_cache_for_tests()


@pytest.fixture
def travel_on():
    from backend.services import sys_config

    sys_config._values["TRAVEL_ENABLED"] = "true"


class TestMainForcedPredicate:
    def test_main_and_agent_hints(self):
        assert is_main_forced({"domain_hint": "main"}) is True
        assert is_main_forced({"domain_hint": "agent"}) is True
        assert is_main_forced({"domain_hint": "MAIN"}) is True

    def test_other_hints_not_main(self):
        assert is_main_forced({"domain_hint": ""}) is False
        assert is_main_forced({"domain_hint": "customer_service"}) is False
        assert is_main_forced({}) is False


class TestMainHandoffTarget:
    def test_travel_strong_signal(self):
        out = main_forced_handoff_update("帮我规划福州两日游", {})
        assert out is not None
        assert out.get("route_mode") == "handoff"
        assert out["_handoff"]["target_domain"] == "travel"

    def test_selection_strong_signal(self):
        out = main_forced_handoff_update("给宠物零食做一次智能选品", {})
        assert out is not None
        assert out["_handoff"]["target_domain"] == "selection_funnel"

    def test_plain_query_no_signal(self):
        assert main_forced_handoff_update("这个季度的经营状况怎么样", {}) is None

    def test_handoff_carries_no_route_decision(self):
        """handoff 轮不回写路由决策：域并未真正开始。"""
        out = main_forced_handoff_update("帮我规划厦门三日游", {})
        assert out is not None
        assert out.get("route_decision") is None


class TestRouterNodeMainLock:
    def test_travel_query_becomes_handoff_not_domain(self, fake_detector, travel_on):
        """核心拍板回归：AI 助手页旅游强信号 → 引导卡，不进旅游域图。"""
        out = rn.router_node({
            "question": "帮我规划杭州2天旅游行程",
            "session_id": "main-lock-1",
            "domain_hint": "main",
        })
        assert out.get("route_mode") == "handoff"
        assert out["_handoff"]["target_domain"] == "travel"

    def test_general_chat_unaffected(self, fake_detector):
        """寒暄仍走 general_chat 直答（锁域不破坏主图能力）。

        guard_result 由真实链路的 Input Guard 注入，测试预置等价判定。
        """
        out = rn.router_node({
            "question": "你好",
            "session_id": "main-lock-2",
            "domain_hint": "main",
            "guard_result": {"category": "greeting"},
        })
        assert out.get("route_mode") == "general_chat"

    def test_plain_query_passes_to_main_route(self, fake_detector):
        """无强信号普通问题继续主路由（不被锁域吞掉）。"""
        out = rn.router_node({
            "question": "这个季度的经营状况怎么样",
            "session_id": "main-lock-3",
            "domain_hint": "main",
        })
        assert out.get("route_mode") != "handoff"
        assert out.get("route_mode") != "travel"


class TestMainRefusalClarify:
    def test_main_hint_generic_options_exclude_domain(self):
        clarify = build_refusal_clarify("搜索新能源汽车行业新闻", "main")
        assert clarify["source"] == "refusal_main"
        assert "帮我规划一份旅游行程" not in clarify["options"]
        assert "我想做商品智能选品" not in clarify["options"]

    def test_main_hint_keeps_sql_lean(self):
        clarify = build_refusal_clarify("查一下经营数据", "main")
        assert clarify["source"] != "refusal_main"

    def test_global_hint_unchanged(self):
        clarify = build_refusal_clarify("随便聊聊", "")
        assert clarify["source"] == "refusal_generic"
        assert "帮我规划一份旅游行程" in clarify["options"]
