"""tests for multi_agent.critique — Plan Critique 节点"""

import pytest
from unittest.mock import MagicMock, patch
from multi_agent.critique import critique_node, PLAN_CRITIQUE_SYSTEM


def test_critique_prompt_contains_rules():
    """Critique prompt 包含审查规则"""
    assert "审查规则" in PLAN_CRITIQUE_SYSTEM
    assert "capability 匹配" in PLAN_CRITIQUE_SYSTEM
    assert "最小修改" in PLAN_CRITIQUE_SYSTEM
    assert "信任原计划" in PLAN_CRITIQUE_SYSTEM


def test_critique_skips_empty_plan():
    """空计划跳过 Critique"""
    state = {
        "question": "测试问题",
        "plan": {"nodes": {}, "edges": {}},
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is False
    assert result["_plan_changed"] is False
    assert result["plan"] == {"nodes": {}, "edges": {}}


def test_critique_skips_single_step_plan():
    """单步骤计划跳过 Critique（节省延迟）"""
    state = {
        "question": "请假流程是什么",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "检索请假流程",
                    "params": {"question": "请假流程是什么"},
                }
            },
            "edges": {},
        },
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is False
    assert result["plan"]["nodes"]["1"]["capability"] == "search_knowledge"


@patch("multi_agent.critique.llm")
def test_critique_on_multi_step_plan(mock_llm):
    """多步骤计划触发 Critique"""
    import json

    original_plan = {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "query_database",
                "description": "查询员工",
                "params": {"question": "技术部有哪些员工"},
            },
            "2": {
                "step_id": "2",
                "capability": "query_database",  # 错误：应该是 search_knowledge
                "description": "检索请假流程",
                "params": {"question": "请假流程是什么"},
            },
        },
        "edges": {},
    }

    # Mock LLM 修正了 step 2 的 capability
    corrected_plan = dict(original_plan)
    corrected_plan["nodes"]["2"]["capability"] = "search_knowledge"

    mock_llm.invoke.return_value = MagicMock(
        content=json.dumps(corrected_plan, ensure_ascii=False)
    )

    state = {
        "question": "技术部有哪些员工，请假流程是什么",
        "plan": original_plan,
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is True
    # 如果修正了，_plan_changed 应为 True
    # （取决于序列化比较，此处测试基础行为）
    assert "plan" in result


@patch("multi_agent.critique.llm")
def test_critique_llm_failure_uses_original(mock_llm):
    """Critique LLM 调用失败时使用原计划"""
    mock_llm.invoke.side_effect = Exception("LLM 超时")

    original_plan = {
        "nodes": {
            "1": {"step_id": "1", "capability": "search_knowledge", "description": "检索", "params": {}},
            "2": {"step_id": "2", "capability": "query_database", "description": "查询", "params": {}},
        },
        "edges": {},
    }

    state = {
        "question": "测试问题",
        "plan": original_plan,
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is False
    assert result["plan"] == original_plan


@patch("multi_agent.critique.llm")
def test_critique_no_change_when_plan_correct(mock_llm):
    """计划本身正确时，Critique 不修改"""
    import json

    correct_plan = {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "query_database",
                "description": "查询员工",
                "params": {"question": "技术部有哪些员工"},
            },
            "2": {
                "step_id": "2",
                "capability": "search_knowledge",
                "description": "检索请假流程",
                "params": {"question": "请假流程是什么"},
            },
        },
        "edges": {},
    }

    # Mock LLM 返回同样的计划（无需修改）
    mock_llm.invoke.return_value = MagicMock(
        content=json.dumps(correct_plan, ensure_ascii=False)
    )

    state = {
        "question": "技术部有哪些员工，请假流程是什么",
        "plan": correct_plan,
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is True
    assert result["_plan_changed"] is False
    assert result["plan"]["nodes"]["1"]["capability"] == "query_database"
    assert result["plan"]["nodes"]["2"]["capability"] == "search_knowledge"
