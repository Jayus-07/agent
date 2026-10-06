"""state_key_guard 单测 — 状态键登记守卫（P1-1）。

覆盖：纯校验函数（已知键零告警 / 未知键点名 / 非 dict 输入防御）、
派生性（KNOWN_STATE_KEYS 恒等于两个 TypedDict 注解并集，G2）、
历史踩坑键全数登记（selection_blocked/_clarify/funnel_context/
travel_context/prompt_versions/cs_pending_action/tenant_id）、
接线层（log 模式告警 + 指标；enforce 模式抛错；守卫自身异常软失败；
wrapper 内嵌守卫；router 专用纯守卫包装）。

真实图零误报检查不在此文件：以 STATE_KEY_GUARD_MODE=enforce 跑
tests/orchestration/graph/ 既有集成测试完成（任何未登记键直接抛错）。
"""
from __future__ import annotations

import pytest

import backend.observability.trace_middleware as tm
from backend.orchestration.state import (
    AgentState,
    KNOWN_STATE_KEYS,
    OrchestratorState,
    validate_state_update,
)


def test_known_keys_derived_from_annotations():
    """G2 派生性：KNOWN_STATE_KEYS 恒等于注解并集（防手抄漂移）。"""
    expected = (
        frozenset(AgentState.__annotations__)
        | frozenset(OrchestratorState.__annotations__)
    )
    assert KNOWN_STATE_KEYS == expected
    # 继承合并行为免疫抽查：AgentState 的键必须在册
    assert {"question", "route_mode", "messages"} <= KNOWN_STATE_KEYS
    # OrchestratorState 扩展键必须在册
    assert {"cs_context", "travel_context"} <= KNOWN_STATE_KEYS


def test_historical_stripped_keys_registered():
    """四次剥离事故 + 后续补登记的键一个都不能少——缺一即守卫自身失真。"""
    historical = [
        "selection_blocked",   # 2026-09-22 灰区阻断失效
        "_clarify",            # 2026-09-19 追问事件发不出
        "funnel_context",      # 2026-09-23 漏斗产出被丢
        "travel_context",      # 2026-09-23 旅游域回退
        "cs_pending_action",   # 活证据：runner 侧读原始输出、state 侧为 None
        "prompt_versions",     # 治理 M4/#5
        "tenant_id",           # 跨轮上下文 miss 根因
        "route_decision_v2",   # STOP B canonical decision
        "runtime_result",      # STOP D domain result contract
        "clarification_request",  # STOP E canonical clarification
    ]
    for key in historical:
        assert key in KNOWN_STATE_KEYS, f"历史踩坑键未登记: {key}"


def test_validate_all_known_keys_clean():
    update = {key: None for key in KNOWN_STATE_KEYS}
    assert validate_state_update("any_node", update) == []


def test_validate_reports_unknown_keys():
    update = {"question": "q", "ghost_key": 1, "another_ghost": 2}
    unknown = validate_state_update("router", update)
    assert sorted(unknown) == ["another_ghost", "ghost_key"]


def test_validate_empty_and_non_dict():
    assert validate_state_update("node", {}) == []
    assert validate_state_update("node", None) == []
    assert validate_state_update("node", "not-a-dict") == []


# ── 接线层（trace_middleware.check_state_update）──────────────


def test_check_log_mode_warns_without_raising(monkeypatch, caplog):
    """默认 log 模式：告警 + 指标，不阻断节点返回。"""
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "log")
    before = tm.state_unknown_key_total.labels(node="router")._value.get()
    with caplog.at_level("WARNING"):
        tm.check_state_update("router", {"question": "q", "ghost": 1})
    assert tm.state_unknown_key_total.labels(node="router")._value.get() == before + 1
    assert any("ghost" in r.message for r in caplog.records)


def test_check_log_mode_counts_per_key(monkeypatch):
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "log")
    before = tm.state_unknown_key_total.labels(node="planner")._value.get()
    tm.check_state_update("planner", {"a_ghost": 1, "b_ghost": 2, "plan": {}})
    assert tm.state_unknown_key_total.labels(node="planner")._value.get() == before + 2


def test_check_enforce_mode_raises(monkeypatch):
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "enforce")
    with pytest.raises(RuntimeError, match="ghost"):
        tm.check_state_update("planner", {"ghost": 1})


def test_check_enforce_mode_clean_update_passes(monkeypatch):
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "enforce")
    tm.check_state_update("planner", {"plan": {}, "_plan_critiqued": True})


def test_check_soft_fail_on_internal_error(monkeypatch):
    """守卫自身异常必须软失败：不能因守卫弄挂节点。"""
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "enforce")
    monkeypatch.setattr(tm, "validate_state_update", lambda *a: 1 / 0)
    tm.check_state_update("planner", {"plan": {}})  # 不抛即通过


def test_check_skips_non_dict():
    # 节点返回 None / 非 dict（LangGraph 允许，supervisor 返回 Send 列表）时静默跳过
    tm.check_state_update("supervisor", None)
    tm.check_state_update("supervisor", "not-a-dict")


# ── wrapper 集成（wrap_sync_node 内嵌守卫）──────────────────


def test_wrap_sync_node_invokes_guard(monkeypatch):
    """wrap_sync_node 包裹的节点返回未知键时触发守卫（无 trace 也检查）。"""
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "enforce")
    calls = []

    def node(state):
        calls.append(state)
        return {"question": "q", "ghost": 1}

    wrapped = tm.trace_middleware.wrap_sync_node("planner", node)
    with pytest.raises(RuntimeError, match="ghost"):
        wrapped({"question": "q"})
    assert calls, "节点本体必须已被执行"


def test_wrap_sync_node_clean_update(monkeypatch):
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "enforce")

    def node(state):
        return {"final_answer": "ok"}

    wrapped = tm.trace_middleware.wrap_sync_node("reporter", node)
    assert wrapped({}) == {"final_answer": "ok"}


def test_guard_node_update_wrapper_pure_guard(monkeypatch):
    """router 专用纯守卫包装：只校验，不改返回值。"""
    monkeypatch.setattr(tm, "STATE_KEY_GUARD_MODE", "enforce")

    def good(state):
        return {"route_mode": "direct"}

    def bad(state):
        return {"route_mode": "direct", "ghost": 1}

    assert tm.guard_node_update("router", good)({}) == {"route_mode": "direct"}
    with pytest.raises(RuntimeError, match="ghost"):
        tm.guard_node_update("router", bad)({})
