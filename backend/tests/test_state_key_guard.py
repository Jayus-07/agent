"""state_key_guard 单测 — 状态键登记守卫（P1-1）。

本文件第一阶段覆盖纯校验函数与派生性；接线层（trace_middleware）
测试在接线提交时追加。
"""
from __future__ import annotations

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
