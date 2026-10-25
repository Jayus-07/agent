from __future__ import annotations

import importlib

import pytest


prefilters = importlib.import_module(
    "backend.orchestration.graph.routing.prefilter_chain",
)
router_node_module = importlib.import_module(
    "backend.orchestration.graph.router_node",
)


@pytest.mark.parametrize(
    "query",
    [
        "什么是退款？",
        "杭州在哪里？",
        "今天天气怎么样？",
        "继续",
        "上面第二点",
    ],
)
def test_registered_agent_general_prompts_use_existing_general_chat(query):
    result = prefilters._try_general_chat({
        "question": query,
        "product_id": "agent",
        "product_source": "registered_route",
        "request_context": {
            "product_id": "agent",
            "product_source": "registered_route",
        },
        "guard_result": {"category": "business_query"},
        "routing_context": {},
    })

    assert result is not None
    assert result["route_mode"] == "general_chat"
    assert result["query_understanding"]["complexity"] == "chat"


@pytest.mark.parametrize(
    ("query", "routing_context", "product_source"),
    [
        ("公司的退款政策是什么？", {}, "registered_route"),
        ("继续", {"active_domain": "sql"}, "registered_route"),
        ("什么是退款？", {}, "shared_route_default"),
    ],
)
def test_private_context_and_untrusted_entries_keep_existing_router(
    query, routing_context, product_source,
):
    result = prefilters._try_general_chat({
        "question": query,
        "product_id": "agent",
        "product_source": product_source,
        "request_context": {
            "product_id": "agent",
            "product_source": product_source,
        },
        "guard_result": {"category": "business_query"},
        "routing_context": routing_context,
    })

    assert result is None


@pytest.mark.parametrize(
    "query",
    [
        "348 乘以 27 再减去 1200 等于多少？",
        "15% 的 240 是多少",
        "(128+72)×3 等于多少？",
        "帮我写一份本周工作周报的框架",
        "帮我起草一封给客户的道歉信",
        "怎么写自我介绍",
    ],
)
def test_no_tool_prompts_use_general_chat_directly(query):
    """conversation-first 直答扩展（2026-10-25）：纯计算与通用写作不走
    工具路由（实测算术题曾被 cands=[] 澄清、周报框架被误路由 report.generate）。"""
    result = prefilters._try_general_chat({
        "question": query,
        "product_id": "agent",
        "product_source": "registered_route",
        "request_context": {
            "product_id": "agent",
            "product_source": "registered_route",
        },
        "guard_result": {"category": "business_query"},
        "routing_context": {},
    })

    assert result is not None
    assert result["route_mode"] == "general_chat"


@pytest.mark.parametrize(
    "query",
    [
        "查询近 30 天销售额",           # 业务数据（私有事实）
        "生成一份本周销售日报",         # 业务报告（无写作触发词）
        "公司的考勤制度怎么写",         # 制度类（私有事实，应查库）
        "统计一下库存",                 # 业务统计
    ],
)
def test_business_prompts_still_route_to_tools(query):
    """业务数据/报告/制度类不得被直答快路径拦截（守卫不放松）。"""
    result = prefilters._try_general_chat({
        "question": query,
        "product_id": "agent",
        "product_source": "registered_route",
        "request_context": {
            "product_id": "agent",
            "product_source": "registered_route",
        },
        "guard_result": {"category": "business_query"},
        "routing_context": {},
    })

    assert result is None


def test_agent_concept_prompt_skips_classifier_and_embedding(monkeypatch):
    def unexpected_route_engine():
        raise AssertionError("一般概念问答不应进入分类器或能力路由")

    monkeypatch.setattr(router_node_module, "get_routing_engine", unexpected_route_engine)
    monkeypatch.setattr(router_node_module, "cs_rule_hits_of", lambda _query: 0)

    result = router_node_module.router_node({
        "question": "退款是什么意思？",
        "product_id": "agent",
        "product_source": "registered_route",
        "request_context": {
            "product_id": "agent",
            "product_source": "registered_route",
        },
        "product_session_id": "session-1",
        "session_id": "session-1",
        "guard_result": {"category": "business_query"},
        "routing_context": {},
    })

    assert result["route_mode"] == "general_chat"
    assert result["query_understanding"]["intent"] == "general_chat"
