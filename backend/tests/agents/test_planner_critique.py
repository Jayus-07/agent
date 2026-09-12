"""test_planner_critique.py — Planner / Critique 节点专属单测

覆盖两个 LLM 决策节点的核心路径与降级路径（全部 mock LLM，不依赖外部服务）:

Planner:
  - 空问题 → 空 plan，不调 LLM
  - LLM 正常返回 → plan 规范化 + rag 步骤注入 kb_id
  - LLM 返回垃圾文本 → 兜底 RAG 单步计划
  - LLM 异常 → 兜底 RAG 单步计划
  - 无效 capability 被过滤

Critique:
  - 单步骤计划 → 跳过审查
  - 合法多步骤计划 → 规则通过，不调 LLM
  - 缺 business.analyze → 规则自动注入，_plan_changed=True
  - edges 引用不存在节点 → 走 LLM 修正
  - LLM 失败 → 回退规则修复结果
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# 先导入 planner 再导入 critique：critique 经 tool_registry 间接依赖 planner，
# 反序导入会触发循环导入
import backend.agents.planner.planner as planner_mod
import backend.agents.planner.critique as critique_mod
from backend.agents.planner.critique import critique_node
from backend.agents.planner.planner import planner_node


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """可编程 LLM 桩：按序返回内容或抛异常。"""

    def __init__(self, contents: list[str] | None = None, exc: Exception | None = None):
        self._contents = contents or []
        self._exc = exc
        self.calls: list[list] = []

    def invoke(self, messages, config=None):
        self.calls.append(messages)
        if self._exc:
            raise self._exc
        return _FakeResp(self._contents.pop(0) if self._contents else "")


_NO_CACHE = SimpleNamespace(get_json=lambda key: None, set_json=lambda key, val: None)


@pytest.fixture(autouse=True)
def _no_planner_cache(monkeypatch):
    """禁用 planner LRU 缓存，避免用例间串扰。"""
    monkeypatch.setattr(planner_mod, "_planner_cache", _NO_CACHE)


@pytest.fixture(autouse=True)
def _critique_enabled(monkeypatch):
    monkeypatch.setattr(critique_mod, "ENABLE_PLAN_CRITIQUE", True)


# =====================================================
# Planner
# =====================================================

class TestPlannerNode:
    def test_empty_question_returns_empty_plan_without_llm(self):
        fake = _FakeLLM()
        with patch.object(planner_mod, "llm", fake):
            result = planner_node({"question": "", "kb_id": "default"})
        assert result["plan"] == {"nodes": {}, "edges": {}}
        assert fake.calls == []

    def test_valid_plan_normalized_and_kb_injected(self):
        plan_json = json.dumps({
            "nodes": {
                "1": {"step_id": "1", "capability": "rag.search",
                      "description": "检索", "params": {"question": "请假政策"}},
            },
            "edges": {},
        }, ensure_ascii=False)
        fake = _FakeLLM([plan_json])
        with patch.object(planner_mod, "llm", fake):
            result = planner_node({"question": "公司的请假政策是什么", "kb_id": "policy"})

        plan = result["plan"]
        assert plan["nodes"]["1"]["capability"] == "rag.search"
        assert plan["nodes"]["1"]["params"]["kb_id"] == "policy"
        assert len(fake.calls) == 1

    def test_garbage_llm_output_falls_back_to_rag(self):
        fake = _FakeLLM(["抱歉，我无法理解这个问题。"])
        with patch.object(planner_mod, "llm", fake):
            result = planner_node({"question": "你好呀", "kb_id": "default"})

        plan = result["plan"]
        assert len(plan["nodes"]) == 1
        assert plan["nodes"]["1"]["capability"] == "rag.search"

    def test_llm_exception_falls_back_to_rag(self):
        fake = _FakeLLM(exc=RuntimeError("LLM 不可用"))
        with patch.object(planner_mod, "llm", fake):
            result = planner_node({"question": "查一下今天的销售额", "kb_id": "default"})

        assert result["plan"]["nodes"]["1"]["capability"] == "rag.search"

    def test_invalid_capability_dropped(self):
        plan_json = json.dumps({
            "nodes": [
                {"step_id": "1", "capability": "not.exist.cap",
                 "description": "x", "params": {}},
            ],
            "edges": {},
        })
        fake = _FakeLLM([plan_json])
        with patch.object(planner_mod, "llm", fake):
            result = planner_node({"question": "你好呀", "kb_id": "default"})

        # 无效 capability 全被过滤 → 空 plan → 兜底 RAG
        assert result["plan"]["nodes"]["1"]["capability"] == "rag.search"


# =====================================================
# Critique
# =====================================================

def _plan(nodes: dict, edges: dict) -> dict:
    return {"plan": {"nodes": nodes, "edges": edges}, "question": "查询今天的订单数据"}


class TestCritiqueNode:
    def test_single_step_plan_skipped(self):
        state = _plan(
            {"1": {"step_id": "1", "capability": "sql.query",
                   "description": "x", "params": {}}},
            {},
        )
        fake = _FakeLLM()
        with patch.object(critique_mod, "llm", fake):
            result = critique_node(state)
        assert result["_plan_critiqued"] is False
        assert result["_plan_changed"] is False
        assert fake.calls == []

    def test_valid_plan_passes_rules_without_llm(self):
        state = _plan(
            {
                "1": {"step_id": "1", "capability": "sql.query",
                      "description": "x", "params": {}},
                "2": {"step_id": "2", "capability": "report.generate",
                      "description": "y", "params": {}},
            },
            {"2": ["1"]},
        )
        fake = _FakeLLM()
        with patch.object(critique_mod, "llm", fake):
            result = critique_node(state)
        assert result["_plan_critiqued"] is True
        assert result["_plan_changed"] is False
        assert fake.calls == []

    def test_missing_analysis_auto_injected(self):
        state = {
            "question": "分析一下上季度销售数据，给出建议",
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "sql.query",
                          "description": "x", "params": {}},
                    "2": {"step_id": "2", "capability": "report.generate",
                          "description": "y", "params": {}},
                },
                "edges": {"2": ["1"]},
            },
        }
        fake = _FakeLLM()
        with patch.object(critique_mod, "llm", fake):
            result = critique_node(state)

        assert result["_plan_critiqued"] is True
        assert result["_plan_changed"] is True
        assert fake.calls == []  # 规则可修复，不需要 LLM
        new_ids = [
            sid for sid, n in result["plan"]["nodes"].items()
            if n["capability"] == "business.analyze"
        ]
        assert len(new_ids) == 1
        assert result["plan"]["edges"][new_ids[0]] == ["1"]

    def test_broken_edges_go_to_llm_and_fixed(self):
        state = _plan(
            {
                "1": {"step_id": "1", "capability": "sql.query",
                      "description": "x", "params": {}},
                "2": {"step_id": "2", "capability": "report.generate",
                      "description": "y", "params": {}},
            },
            {"2": ["99"]},  # 依赖不存在的步骤 99
        )
        corrected = json.dumps({
            "nodes": {
                "1": {"step_id": "1", "capability": "sql.query",
                      "description": "x", "params": {}},
                "2": {"step_id": "2", "capability": "report.generate",
                      "description": "y", "params": {}},
            },
            "edges": {"2": ["1"]},
        })
        fake = _FakeLLM([corrected])
        with patch.object(critique_mod, "llm", fake):
            result = critique_node(state)

        assert len(fake.calls) == 1
        assert result["_plan_changed"] is True
        assert result["plan"]["edges"]["2"] == ["1"]

    def test_llm_failure_returns_rule_fixed_plan(self):
        state = _plan(
            {
                "1": {"step_id": "1", "capability": "sql.query",
                      "description": "x", "params": {}},
                "2": {"step_id": "2", "capability": "report.generate",
                      "description": "y", "params": {}},
            },
            {"2": ["99"]},
        )
        fake = _FakeLLM(exc=RuntimeError("LLM 不可用"))
        with patch.object(critique_mod, "llm", fake):
            result = critique_node(state)

        # LLM 失败 → 保留规则修复结果（此场景规则修不了 edges，原样返回）
        assert result["_plan_critiqued"] is True
        assert result["plan"]["edges"]["2"] == ["99"]
