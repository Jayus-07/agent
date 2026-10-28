# -*- coding: utf-8 -*-
"""test_tool_selector_discovery.py — Discovery B2：策略接入 + 多轮重新发现 + 费用记账

覆盖：
  - adaptive：首轮越界 → 第二轮候选扩展（email.* 1→3）后成功
  - 扩展不越权：agent 产品禁行根下 reasoning 扩展池剔除 travel.*
    （filter_capabilities_for_state 与执行期门同谓词）
  - fast：失败轮候选固定（行为零变化保障）
  - max_rounds=1：一轮失败直接澄清（轮数上限生效）
  - 费用超限：token 累计超 cost_budget_milliunits 换算值 → 不进下一轮，
    block_reason=discovery_cost_budget
  - 策略读取失败回退代码默认（选择链不阻断）
离线：全部 mock LLM；策略经 monkeypatch 注入，不依赖 DB。
"""
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

import backend.orchestration.graph.tool_selector as ts
from backend.orchestration.discovery import DiscoveryPolicy
from backend.orchestration.discovery import service as discovery_service
from backend.orchestration.graph.tool_selector import tool_selector_node


def _tc(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": "call_1"}


class _FakeBound:
    def __init__(self, fake: "_FakeLLM"):
        self._fake = fake

    def invoke(self, input=None, **kwargs):
        self._fake.bound_calls += 1
        self._fake.last_messages = input
        resp = self._fake.responses.pop(0) if self._fake.responses else AIMessage(content="")
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.bound_calls = 0
        self.bound_tools = None
        self.last_messages = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return _FakeBound(self)

    @property
    def bound_tool_names(self) -> list[str]:
        return [t["function"]["name"] for t in (self.bound_tools or [])]


def _usage(total: int) -> dict:
    return {"input_tokens": total - 1, "output_tokens": 1, "total_tokens": total}


def _state(candidates, question="查一下上季度的发票邮件", **extra):
    domain = extra.pop("domain", "")
    st = {
        "question": question,
        "route_mode": "direct",
        "domain": domain,
        "tool_route_mode": "llm_selection",
        "route_decision": {
            "execution_mode": "direct", "candidates": candidates,
            "confidence": 0.7,
            "routing_meta": {"domain": domain, "selection_mode": "llm_selection",
                             "tool_route_mode": "llm_selection",
                             "score_type": "vector_similarity_heuristic"},
        },
    }
    st.update(extra)
    return st


class DiscoveryB2TestBase:
    """策略注入 + 缓存隔离（TTL 缓存是模块级状态，逐用例清空）。"""

    @pytest.fixture(autouse=True)
    def _inject_policy(self, monkeypatch):
        discovery_service.reset_runtime_policy_cache()
        self._policy = DiscoveryPolicy()
        monkeypatch.setattr(
            discovery_service, "load_policy_for_runtime",
            lambda tenant_id: self._policy)
        yield
        discovery_service.reset_runtime_policy_cache()

    def _set_policy(self, **kwargs) -> None:
        self._policy = DiscoveryPolicy(**kwargs)


class TestAdaptiveMultiRound(DiscoveryB2TestBase):
    def test_first_round_out_of_scope_then_expanded_second_round(self):
        """adaptive：首轮越界 → 第二轮候选扩展（email.* 1→3）后成功。"""
        self._set_policy(mode="adaptive", max_rounds=2, top_k=3)
        fake = _FakeLLM([
            # 第一轮：email.read 已注册但不在候选 [email.send] → 越界
            AIMessage(content="", tool_calls=[_tc("email__read", {})]),
            # 第二轮：扩展池中出现 email.search → 选中 + 填参
            AIMessage(content="", tool_calls=[_tc("email__search", {"action": "search"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 2
        # 第二轮绑定的是扩展后的候选（email.send ∪ email.* 可见 = 3 个）
        assert fake.bound_tool_names == ["email__send", "email__search", "email__read"]
        assert out["resolved_params"] == {"action": "search"}
        sel = out["_tool_selection"]
        assert sel["source"] == "fc"
        assert sel["attempts"] == 2
        assert sel["discovery_rounds"] == 2
        assert sel["policy_mode"] == "adaptive"
        assert set(sel["candidates"]) == {"email.send", "email.search", "email.read"}

    def test_param_failure_round_also_expands(self):
        """参数校验失败同样触发候选重算（同越界分支语义）。"""
        self._set_policy(mode="adaptive", max_rounds=2)
        fake = _FakeLLM([
            # 第一轮：选中候选但参数非法（action 不在 enum）
            AIMessage(content="", tool_calls=[_tc("email__send", {"action": "bad"})]),
            AIMessage(content="", tool_calls=[_tc("email__send", {"action": "send"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 2
        assert out["resolved_params"] == {"action": "send"}
        assert out["_tool_selection"]["discovery_rounds"] == 2

    def test_max_rounds_one_no_expansion_round(self):
        """max_rounds=1：首轮失败后无下一轮，直接澄清。"""
        self._set_policy(mode="adaptive", max_rounds=1)
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__read", {})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 1
        assert out["_tool_selection"]["reason"] == "fc_invalid_after_retry"
        assert out["_tool_selection"]["discovery_rounds"] == 1
        assert out["_tool_selection"]["policy_mode"] == "adaptive"
        assert out["selection_blocked"] is True


class TestExpansionPermission(DiscoveryB2TestBase):
    def test_reasoning_expansion_respects_product_scope(self):
        """扩展集必须过 filter_capabilities_for_state：agent 产品禁行根
        下的 travel.* 不进第二轮绑定面（渲染面不得宽于执行面）。"""
        from backend.infra.bundle_registry import store as bundle_store

        bundle_store.reset_for_tests()  # 快照未加载 → 回退域黑名单语义
        self._set_policy(mode="reasoning", max_rounds=2)
        fake = _FakeLLM([
            # 第一轮：travel.poi_search 已注册但不在候选 [sql.query] → 越界
            AIMessage(content="", tool_calls=[_tc("travel__poi_search", {})]),
            AIMessage(content="", tool_calls=[_tc("sql__query", {"question": "测试"})]),
        ])
        try:
            with patch.object(ts, "llm", fake):
                out = tool_selector_node(_state(
                    [{"name": "sql.query", "score": 0.7}], product_id="agent"))
        finally:
            bundle_store.reset_for_tests()
        assert fake.bound_calls == 2
        # reasoning 扩展池 = planner 可见全集，但 agent 禁行根剔除 travel.*
        assert "travel__poi_search" not in fake.bound_tool_names
        assert "sql__query" in fake.bound_tool_names
        assert out["_tool_selection"]["capability"] == "sql.query"
        assert out["_tool_selection"]["policy_mode"] == "reasoning"

    def test_fast_mode_keeps_fixed_candidates(self):
        """fast 模式：失败轮候选固定（与既有两轮重试行为零变化）。"""
        self._set_policy(mode="fast", max_rounds=2)
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__read", {})]),
            AIMessage(content="", tool_calls=[_tc("email__send", {"action": "send"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 2
        # 第二轮绑定面不变：没有 email.search / email.read
        assert fake.bound_tool_names == ["email__send"]
        assert out["_tool_selection"]["policy_mode"] == "fast"
        assert out["_tool_selection"]["discovery_rounds"] == 2


class TestCostBudget(DiscoveryB2TestBase):
    def test_cost_overrun_stops_next_round(self):
        """首轮 token 累计超 cost_budget_milliunits 换算值 → 不进下一轮，
        block_reason=discovery_cost_budget（1 milliunit = 1000 token 口径）。"""
        self._set_policy(mode="adaptive", max_rounds=2, cost_budget_milliunits=1)
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__read", {})],
                      usage_metadata=_usage(2000)),
            # 不应被消费：费用闸在第二轮前拦截
            AIMessage(content="", tool_calls=[_tc("email__send", {})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 1  # 当前轮完成，下一轮被拦
        assert out["selection_blocked"] is True
        sel = out["_tool_selection"]
        assert sel["reason"] == "discovery_cost_budget"
        assert sel["discovery_rounds"] == 1
        assert sel["policy_mode"] == "adaptive"
        assert out["clarification_request"]["source"] == "discovery_cost_budget"

    def test_within_budget_continues_next_round(self):
        """预算内不拦截：低用量失败轮后仍进第二轮扩展。"""
        self._set_policy(mode="adaptive", max_rounds=2, cost_budget_milliunits=50)
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__read", {})],
                      usage_metadata=_usage(900)),
            AIMessage(content="", tool_calls=[_tc("email__search", {"action": "search"})],
                      usage_metadata=_usage(900)),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 2
        assert out["_tool_selection"]["source"] == "fc"

    def test_missing_usage_does_not_trigger_budget(self):
        """usage 缺失按 0 记账（local 模式禁伪造 token）→ 不误拦。"""
        self._set_policy(mode="adaptive", max_rounds=2, cost_budget_milliunits=0)
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__read", {})]),
            AIMessage(content="", tool_calls=[_tc("email__send", {"action": "send"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        assert fake.bound_calls == 2
        assert out["_tool_selection"]["source"] == "fc"


class TestPolicyFallback(DiscoveryB2TestBase):
    def test_policy_read_failure_falls_back_to_code_default(self, monkeypatch):
        """策略读取异常 → 回退代码默认（adaptive/max_rounds=2），选择链不阻断。"""
        def _boom(_tenant):
            raise RuntimeError("db down")

        monkeypatch.setattr(discovery_service, "load_policy_for_runtime", _boom)
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__read", {})]),
            AIMessage(content="", tool_calls=[_tc("email__send", {"action": "send"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "email.send", "score": 0.7}], domain="communication"))
        # 默认 max_rounds=2 → 越界后仍有一次重试机会
        assert fake.bound_calls == 2
        assert out["_tool_selection"]["source"] == "fc"
        assert out["_tool_selection"]["policy_mode"] == "adaptive"
