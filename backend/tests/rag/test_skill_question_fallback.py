"""RAG Skill 对空参数的回归：普通问句必须作为检索问题进入 Tool。"""
import asyncio

import pytest

from backend.orchestration.graph import direct_executor
from backend.observability.tracer import trace_collector
from backend.skills.base import BaseSkill
from backend.skills.rag.skill import rag_skill_node


@pytest.mark.parametrize(
    "mode,params,expected_question",
    [
        ("direct", {"question": ""}, "退款怎么处理"),
        ("plan", {"question": ""}, "退款怎么处理"),
        ("plan", {}, "退款怎么处理"),
        ("plan", {"question": None}, "退款怎么处理"),
        ("plan", {"question": "用户明确指定的问题"}, "用户明确指定的问题"),
    ],
)
def test_rag_skill_recovers_original_question_before_tool_validation(
    monkeypatch, mode, params, expected_question
):
    """Direct 与 Planner 的 RAG 步骤均用原始问句填补空 question。"""
    seen = {}

    async def capture_governed_execute(
        self, state, sr, step_results, tool_fn, invoke_params, params,
        timeout, max_retries, tool_span, contract_md=None,
    ):
        seen["params"] = dict(params)
        seen["invoke_params"] = dict(invoke_params)
        sr.update(status="success", output="合成 RAG 答案", error=None,
                  error_type=None)
        step_results[sr["step_id"]] = dict(sr)

    monkeypatch.setattr(
        "backend.skills.base._tool_runtime_enabled", lambda: True,
    )
    monkeypatch.setattr(BaseSkill, "_execute_governed", capture_governed_execute)
    monkeypatch.setattr(trace_collector, "current", lambda: None)
    monkeypatch.setattr(trace_collector, "start_span", lambda *args, **kwargs: "span")

    if mode == "direct":
        monkeypatch.setattr(
            direct_executor.tool_registry, "get_skill_nodes",
            lambda: {"rag_skill": rag_skill_node},
        )
        state = {
            "question": "退款怎么处理",
            "route_decision": {
                "candidates": [{"name": "rag.search", "score": 0.9}],
            },
            "resolved_params": params,
            "_tool_selection": {
                "source": "fc",
                "candidates": ["rag.search"],
            },
        }
        out = direct_executor.skill_executor_node(state)
        assert out["step_results"]["direct_1"]["status"] == "success"
        step_id = "direct_1"
    else:
        step_id = "plan_1"
        state = {
            "question": "退款怎么处理",
            "current_step_id": step_id,
            "plan": {
                "nodes": {
                    step_id: {
                        "capability": "rag.search",
                        "description": "知识库检索",
                        "params": params,
                    },
                },
                "edges": {},
            },
            "step_results": {},
            "_tool_selection": {
                "source": "passthrough",
                "candidates": ["rag.search"],
            },
        }
        asyncio.run(rag_skill_node(state))

    expected = {"question": expected_question}
    assert seen["params"] == expected
    assert seen["invoke_params"] == expected
    if mode == "plan":
        # 规范化只影响本次 Skill 参数，不改写 LangGraph 输入状态。
        assert state["plan"]["nodes"][step_id]["params"] == params


def _make_direct_state(params, kb_id):
    state = {
        "question": "退款怎么处理",
        "route_decision": {
            "candidates": [{"name": "rag.search", "score": 0.9}],
        },
        "resolved_params": params,
        "_tool_selection": {
            "source": "fc",
            "candidates": ["rag.search"],
        },
    }
    if kb_id is not None:
        state["kb_id"] = kb_id
    return state


def test_rag_skill_injects_request_kb_id_in_direct_mode(monkeypatch):
    """direct 快路径：FC 只选 question 时，请求级 kb_id 必须从 state 注入。

    2026-10-25 实测：/chat/agent 带 kb_id=rag_100_docs 进入主图（入口日志
    kb=rag_100_docs），但 direct 链路 FC 选参不含 kb_id，工具落回默认
    "default" 空库 → 恒答"知识库暂无相关资料"。
    """
    seen = {}

    async def capture_governed_execute(
        self, state, sr, step_results, tool_fn, invoke_params, params,
        timeout, max_retries, tool_span, contract_md=None,
    ):
        seen["params"] = dict(params)
        sr.update(status="success", output="合成 RAG 答案", error=None,
                  error_type=None)
        step_results[sr["step_id"]] = dict(sr)

    monkeypatch.setattr(
        "backend.skills.base._tool_runtime_enabled", lambda: True,
    )
    monkeypatch.setattr(BaseSkill, "_execute_governed", capture_governed_execute)
    monkeypatch.setattr(trace_collector, "current", lambda: None)
    monkeypatch.setattr(trace_collector, "start_span", lambda *args, **kwargs: "span")
    monkeypatch.setattr(
        direct_executor.tool_registry, "get_skill_nodes",
        lambda: {"rag_skill": rag_skill_node},
    )

    out = direct_executor.skill_executor_node(
        _make_direct_state({"question": "信息安全制度对密码的要求"}, "policy_general"))
    assert out["step_results"]["direct_1"]["status"] == "success"
    assert seen["params"] == {
        "question": "信息安全制度对密码的要求",
        "kb_id": "policy_general",
    }


def test_rag_skill_request_kb_id_overrides_model_picked_value(monkeypatch):
    """kb_id 是请求级授权范围：state 权威，FC 模型选参不得覆盖或绕过。"""
    seen = {}

    async def capture_governed_execute(
        self, state, sr, step_results, tool_fn, invoke_params, params,
        timeout, max_retries, tool_span, contract_md=None,
    ):
        seen["params"] = dict(params)
        sr.update(status="success", output="合成 RAG 答案", error=None,
                  error_type=None)
        step_results[sr["step_id"]] = dict(sr)

    monkeypatch.setattr(
        "backend.skills.base._tool_runtime_enabled", lambda: True,
    )
    monkeypatch.setattr(BaseSkill, "_execute_governed", capture_governed_execute)
    monkeypatch.setattr(trace_collector, "current", lambda: None)
    monkeypatch.setattr(trace_collector, "start_span", lambda *args, **kwargs: "span")
    monkeypatch.setattr(
        direct_executor.tool_registry, "get_skill_nodes",
        lambda: {"rag_skill": rag_skill_node},
    )

    direct_executor.skill_executor_node(_make_direct_state(
        {"question": "q", "kb_id": "other_kb"}, "policy_general"))
    assert seen["params"]["kb_id"] == "policy_general"


def test_rag_skill_without_state_kb_id_keeps_params_unchanged(monkeypatch):
    """state 无 kb_id 时不伪造检索范围（工具按自身默认 default 兜底）。"""
    seen = {}

    async def capture_governed_execute(
        self, state, sr, step_results, tool_fn, invoke_params, params,
        timeout, max_retries, tool_span, contract_md=None,
    ):
        seen["params"] = dict(params)
        sr.update(status="success", output="合成 RAG 答案", error=None,
                  error_type=None)
        step_results[sr["step_id"]] = dict(sr)

    monkeypatch.setattr(
        "backend.skills.base._tool_runtime_enabled", lambda: True,
    )
    monkeypatch.setattr(BaseSkill, "_execute_governed", capture_governed_execute)
    monkeypatch.setattr(trace_collector, "current", lambda: None)
    monkeypatch.setattr(trace_collector, "start_span", lambda *args, **kwargs: "span")
    monkeypatch.setattr(
        direct_executor.tool_registry, "get_skill_nodes",
        lambda: {"rag_skill": rag_skill_node},
    )

    direct_executor.skill_executor_node(_make_direct_state(
        {"question": "q"}, None))
    assert seen["params"] == {"question": "q"}
