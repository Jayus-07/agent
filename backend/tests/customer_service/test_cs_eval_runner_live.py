"""CS live runner 的 expert_history 消费契约回归（2026-11-02）。

背景：experts/base.py 的 expert_history 条目为 {"expert": ...}，而
runners/cs.py 曾按 {"node": ...} 读取——离线 sanity 不触图，契约漂移
只能由 live 暴露（b0 实测 0/20 全部「路由不符: 实际命中 [None]」）。
本测试用假图锁定三种历史形态（现行 dict / 旧版 node dict / 纯字符串）
下路由断言都成立，防再次静默失效。
"""

import pytest

from backend.evaluation.models import TestCase as EvalTestCase
from backend.evaluation.runners import cs as cs_runner


def _case(case_id: str) -> EvalTestCase:
    return EvalTestCase(
        id=case_id,
        question="我的订单怎么申请退款",
        module="cs",
        expected={"target": "cs_business_action", "must_contain": ["退款"]},
    )


class _FakeGraph:
    """返回固定 final_state 的最小图桩；invoke 签名对齐 langgraph。"""

    def __init__(self, final_state: dict):
        self._final_state = final_state

    def invoke(self, cs_input, config):  # noqa: ARG002 — 桩不需要入参
        return self._final_state


@pytest.mark.parametrize(
    ("expert_history", "expected_visited", "desc"),
    [
        (
            [{"expert": "action", "status": "success", "response_draft": ""}],
            ["cs_action_expert"],
            "现行 expert dict（语义短名归一为节点名）",
        ),
        (
            [{"node": "cs_action_expert"}],
            ["cs_action_expert"],
            "旧版 node dict（节点名原样透传）",
        ),
        (
            ["action"],
            ["cs_action_expert"],
            "纯短名字符串",
        ),
        (
            ["cs_action_expert"],
            ["cs_action_expert"],
            "纯节点名字符串",
        ),
        (
            [
                {"expert": "knowledge", "status": "success"},
                {"expert": "action", "status": "success"},
                {},
                None,
            ],
            ["cs_action_expert", "cs_knowledge_expert"],
            "多专家 + 空/None 条目不炸不误判",
        ),
    ],
)
def test_run_live_expert_history_forms(expert_history, expected_visited, desc, monkeypatch):
    monkeypatch.setattr(
        "backend.customer_service.graph_builder.get_cs_graph",
        lambda: _FakeGraph({
            "final_answer": "已为你提交退款申请，请稍候。",
            "expert_history": expert_history,
            "handoff_state": "",
            "conversation_status": "resolved",
        }),
    )

    results = cs_runner._run_cs([_case("CS-T1")], live=True)

    assert len(results) == 1
    result = results[0]
    assert result.metrics["expert_match"] == 1.0, desc
    assert result.status == "pass", (desc, result.error_msg)
    assert result.actual["visited_experts"] == sorted(expected_visited), desc


def test_run_live_route_mismatch_still_fails(monkeypatch):
    """路由到错误专家时断言必须仍然 fail（防修成永远 pass）。"""
    monkeypatch.setattr(
        "backend.customer_service.graph_builder.get_cs_graph",
        lambda: _FakeGraph({
            "final_answer": "已为你提交退款申请，请稍候。",
            "expert_history": [{"expert": "knowledge", "status": "success"}],
            "handoff_state": "",
            "conversation_status": "resolved",
        }),
    )

    result = cs_runner._run_cs([_case("CS-T2")], live=True)[0]

    assert result.status == "fail"
    assert result.metrics["expert_match"] == 0.0
    assert "路由不符" in (result.error_msg or "")
