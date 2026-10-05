# -*- coding: utf-8 -*-
"""STOP C：跨域连续会话的 Router 决策契约验证。

测试只调用已经冻结的 Router 适配边界，不启动 LLM、embedding、数据库或域图。
每个场景仍按“上一轮状态 → 下一轮 routing_context”推进，验证新决策快照是旁路
观察字段，旧 route_decision / route_mode / query_understanding 不被覆盖。
"""
from __future__ import annotations

import json

import pytest

from backend.customer_service.context_resolver import resolve_explicit_order_route
from backend.orchestration.context.continuation_resolver import (
    resolve_continuation,
)
from backend.orchestration.graph.router_node import _with_router_decisions


_TRACE_KEYS = (
    "domain_decision",
    "capability_decision",
    "execution_decision",
    "router_fallback_reason",
    "legacy_used",
)


def _meta(domain: str, capability: str, *, reason: str = "stop_c_matrix") -> dict:
    """构造既有 hierarchical 元数据，避免测试依赖外部分类器。"""

    return {
        "domain": domain,
        "domain_source": "rule",
        "domain_confidence": 0.96,
        "candidate_tools": [capability],
        "fine_top1": capability,
        "fine_top1_score": 0.93,
        "reason_code": reason,
    }


def _state(query: str, *, routing_context: dict | None = None) -> dict:
    """提供一份包含旧契约字段的可序列化会话状态。"""

    return {
        "question": query,
        "session_id": "stop-c-session",
        "tenant_id": "default",
        "user_id": "stop-c-user",
        "routing_context": routing_context or {},
        "route_decision": {"execution_mode": "plan", "candidates": []},
        "route_mode": "plan",
        "query_understanding": {
            "intent": "business_query",
            "entities": [],
        },
    }


def _hierarchical_round(
    query: str,
    domain: str,
    capability: str,
    *,
    routing_context: dict | None = None,
) -> dict:
    return _with_router_decisions(
        _state(query, routing_context=routing_context),
        {},
        query,
        hierarchical_meta=_meta(domain, capability),
    )


def _travel_round(query: str, *, routing_context: dict | None = None) -> dict:
    """模拟既有 travel prefilter/continuation 更新，不调用真实域图。"""

    state = _state(query, routing_context=routing_context)
    return _with_router_decisions(
        state,
        {"route_decision": None, "route_mode": "travel"},
        query,
        existing_override={"route_mode": "travel"},
    )


def _assert_trace(result: dict) -> None:
    """验证 STOP B 新增快照存在且普通字典可进入 checkpoint。"""

    for key in _TRACE_KEYS:
        assert key in result, f"缺少 Router trace 字段: {key}"
    assert isinstance(result["domain_decision"], dict)
    assert isinstance(result["capability_decision"], dict)
    assert isinstance(result["execution_decision"], dict)
    assert "source" in result["domain_decision"]
    assert "source" in result["capability_decision"]
    assert "mode" in result["execution_decision"]
    json.dumps(
        {
            key: result[key]
            for key in (
                "domain_decision",
                "capability_decision",
                "execution_decision",
            )
        },
        ensure_ascii=False,
    )


def _assert_legacy_fields_preserved(result: dict) -> None:
    assert result["route_decision"] == {
        "execution_mode": "plan",
        "candidates": [],
    }
    assert result["route_mode"] == "plan"
    assert result["query_understanding"] == {
        "intent": "business_query",
        "entities": [],
    }


# A. 同域连续承接：客服域连续五组，能力与域均不得漂移。
@pytest.mark.parametrize(
    ("first_query", "second_query"),
    [
        ("查询订单 123456", "那退款多久到账？"),
        ("订单物流到哪了", "那它到哪了"),
        ("帮我申请退款", "退款需要多久到账"),
        ("转人工客服", "继续"),
        ("客服投诉怎么处理", "第二个呢？"),
    ],
)
def test_same_domain_customer_service_continuity(first_query, second_query):
    first = _hierarchical_round(
        first_query, "customer_service", "customer_service",
    )
    second_context = {
        "active_domain": first["domain_decision"]["domain"],
        "last_intent": first["capability_decision"]["capability"],
        "last_action": first["execution_decision"]["mode"],
        "brief_summary": {"order_id": "123456"},
    }
    second = _hierarchical_round(
        second_query,
        "customer_service",
        "customer_service",
        routing_context=second_context,
    )

    assert first["domain_decision"]["domain"] == "customer_service"
    assert second["domain_decision"]["domain"] == "customer_service"
    assert second["capability_decision"]["capability"] == "customer_service"
    assert second["execution_decision"]["mode"] == "direct"
    assert second["routing_context"] == second_context
    _assert_trace(second)
    _assert_legacy_fields_preserved(second)


# B. 旅游连续规划：域图连续五组，不能回退 general_chat 或普通 plan。
@pytest.mark.parametrize(
    ("first_query", "second_query"),
    [
        ("规划东京三日游", "酒店预算控制在5000以内"),
        ("规划东京三日游", "第二天轻松一点"),
        ("东京三日游", "加一天"),
        ("规划大阪行程", "换一个酒店"),
        ("安排京都旅行", "继续"),
    ],
)
def test_travel_domain_graph_continuity(first_query, second_query):
    first = _travel_round(first_query)
    second_context = {
        "active_domain": first["domain_decision"]["domain"],
        "last_intent": "travel",
        "last_action": first["execution_decision"]["target"],
        "brief_summary": {"destination": "东京"},
    }
    second = _travel_round(second_query, routing_context=second_context)

    assert first["domain_decision"]["domain"] == "travel"
    assert second["domain_decision"]["domain"] == "travel"
    assert second["execution_decision"]["mode"] == "domain_graph"
    assert second["execution_decision"]["target"] == "travel"
    assert second["routing_context"]["brief_summary"]["destination"] == "东京"
    _assert_trace(second)
    # 域图入口按既有契约明确把 route_decision 置空、route_mode 切到 travel；
    # 这里验证其余旧字段仍保留，而不是把合法的入口更新误判成覆盖。
    assert second["route_decision"] is None
    assert second["route_mode"] == "travel"
    assert second["query_understanding"] == {
        "intent": "business_query",
        "entities": [],
    }


# C. 跨域切换：新域拍板不能被上一轮活跃域锁死，结构化摘要允许共存。
@pytest.mark.parametrize(
    ("first_domain", "first_capability", "second_domain", "second_capability"),
    [
        ("customer_service", "customer_service", "travel", "travel.plan"),
        ("travel", "travel.plan", "customer_service", "customer_service"),
        ("customer_service", "customer_service", "data", "sql.query"),
        ("travel", "travel.plan", "selection_funnel", "selection_funnel"),
        ("data", "sql.query", "travel", "travel.plan"),
    ],
)
def test_cross_domain_switch_preserves_both_contexts(
    first_domain,
    first_capability,
    second_domain,
    second_capability,
):
    first = _hierarchical_round("上一轮请求", first_domain, first_capability)
    context = {
        "active_domain": first_domain,
        "last_intent": first_capability,
        "last_action": first["execution_decision"]["mode"],
        "brief_summary": {
            "order_id": "123456",
            "destination": "东京",
        },
    }
    second = _hierarchical_round(
        "切换到新的业务请求",
        second_domain,
        second_capability,
        routing_context=context,
    )

    assert second["domain_decision"]["domain"] == second_domain
    assert second["domain_decision"]["domain"] != first_domain
    assert second["capability_decision"]["capability"] == second_capability
    assert second["routing_context"]["brief_summary"] == {
        "order_id": "123456",
        "destination": "东京",
    }
    _assert_trace(second)
    _assert_legacy_fields_preserved(second)


# D. 模糊指代与 fallback：优先延续，强新域信号允许切域，异常保持旧链路。
@pytest.mark.parametrize(
    ("query", "active_domain", "expected"),
    [
        ("第二个呢？", "travel", {
            "is_continuation": True,
            "domain": "travel",
            "reason": "continuation_hit",
        }),
        ("价格是多少？", "travel", {
            "is_continuation": False,
            "domain": "",
            "reason": "no_signal",
        }),
        ("太赶了，先统计本月订单金额", "travel", {
            "is_continuation": False,
            "domain": "",
            "reason": "domain_switch_allowed",
        }),
    ],
)
def test_ambiguous_continuation_resolution(query, active_domain, expected):
    result = resolve_continuation(query, {"active_domain": active_domain})
    assert {key: result[key] for key in expected} == expected


def test_ambiguous_explicit_order_reference_is_normalized():
    result = resolve_explicit_order_route("请查一下订单 MO-3C052B3A")
    assert result is not None
    assert result.order_id == "MO-3C052B3A"
    assert result.resolved_query == "查询订单 MO-3C052B3A"
    assert result.resolution_type == "explicit_entity"


def test_fallback_keeps_legacy_state_and_records_reason(monkeypatch):
    import backend.orchestration.router.domain_router as domain_router

    def _raise(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("stop-c classifier unavailable")

    monkeypatch.setattr(domain_router.DomainRouter, "route", _raise)
    state = _state("模糊请求")
    result = _with_router_decisions(state, {}, "模糊请求")

    _assert_legacy_fields_preserved(result)
    assert result["legacy_used"] is False
    assert result["router_fallback_reason"].startswith("decision_adapter:")


def test_engine_route_decision_trace_is_serializable_and_non_destructive():
    from backend.orchestration.graph.router_node import _with_router_decisions

    legacy = {
        "execution_mode": "direct",
        "candidates": [{"name": "sql.query", "score": 0.9}],
        "confidence": 0.9,
    }
    state = _state("查本月订单金额")
    result = _with_router_decisions(
        state,
        {"route_decision": legacy, "route_mode": "direct"},
        "查本月订单金额",
        existing_override=legacy,
    )

    _assert_trace(result)
    assert result["capability_decision"]["capability"] == "sql.query"
    assert result["execution_decision"]["mode"] == "direct"
    assert result["legacy_used"] is False
    assert result["route_decision"] == legacy
    assert result["route_mode"] == "direct"
    assert result["query_understanding"] == state["query_understanding"]
