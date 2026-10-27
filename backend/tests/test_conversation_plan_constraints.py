"""会话记忆表达和显式知识来源不能被规划后处理错误分流。"""
import pytest

from backend.agents.planner.planner import _filter_plan
from backend.orchestration.graph.routing.prefilter_chain import (
    _is_general_conversation_prompt,
)


@pytest.mark.parametrize("question", [
    "本次对话中，把我正在核对的项目代号记作蓝杉42。请只回复已记下。",
    "我刚才说的项目代号是什么？只回答代号。",
])
def test_session_context_does_not_need_enterprise_tools(question):
    assert _is_general_conversation_prompt(question, {})


@pytest.mark.parametrize("question", [
    "查询数据库中刚才提到的项目名称和销售额。",
    "本次对话帮我查询企业内部项目代号。",
])
def test_session_wording_does_not_bypass_private_facts(question):
    assert not _is_general_conversation_prompt(question, {})


def test_explicit_knowledge_source_remains_in_mixed_plan():
    plan = {"nodes": {"sql": {"capability": "sql.query"},
                      "rag": {"capability": "rag.search"}},
            "edges": {}}
    filtered = _filter_plan(
        plan, "查询九月销售额，同时检索企业知识库中的退款政策并综合分析。",
    )
    assert "rag" in filtered["nodes"]


def test_pure_sales_query_still_removes_unrequested_rag():
    plan = {"nodes": {"sql": {"capability": "sql.query"},
                      "rag": {"capability": "rag.search"}}, "edges": {}}
    assert "rag" not in _filter_plan(plan, "查询九月销售额")["nodes"]


def test_explanation_format_does_not_override_explicit_web_lookup():
    assert not _is_general_conversation_prompt(
        "请联网查找Redis的官方GitHub仓库，用一句话说明用途并附官方仓库链接。", {},
    )
    assert _is_general_conversation_prompt("用一句话解释幂等，不需要联网。", {})
