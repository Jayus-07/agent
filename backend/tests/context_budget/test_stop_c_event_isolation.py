"""STOP C 验收测试 — 消息角色与事件归属（2026-10-01）

对应验收项 CONTEXT_EVENT_ISOLATION_PASS：
  - L4 折叠窗口不跨中段 SystemMessage / 显式 pin（工具组不拆对）
  - L2 摘要投影二元组（policy System + 数据 AIMessage）原子保留
  - SSE 事件按会话隔离：晚到事件不冒充他人连接
  - 分项 token：分布（Histogram）+ 总量（Counter）可聚合，Gauge 仅最近值
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import backend.config as config


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L4_KEEP_RECENT_TURNS", 4)
    from backend.context_budget import token_counter as tc
    monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
    monkeypatch.setattr(tc, "_get_provider", lambda m: "deepseek")
    tc._counter_cache.clear()


@pytest.fixture(autouse=True)
def _clear_event_buckets():
    from backend.context_budget import metrics as cbm
    with cbm._pending_lock:
        cbm._pending_events.clear()
    yield
    with cbm._pending_lock:
        cbm._pending_events.clear()


def _history(turns: int, msg_len: int = 150) -> list:
    msgs = []
    for i in range(turns):
        msgs.append(HumanMessage(content=f"用户第{i}轮：" + "问" * msg_len))
        msgs.append(AIMessage(content=f"助手第{i}轮：" + "答" * msg_len))
    return msgs


class TestL4FoldSegments:
    def test_fold_does_not_cross_mid_system(self):
        """折叠窗口不得替换中段业务 SystemMessage（原实现整窗切片会吃掉它）。"""
        from backend.context_budget.collapse import fold_messages
        mid_sys = SystemMessage(content="中段业务指令：必须原样保留")
        msgs = _history(3) + [mid_sys] + _history(3, 200) \
            + [HumanMessage(content="当前问题")]
        folded, fold = fold_messages(msgs, keep_recent_turns=4)
        assert fold is not None, "此场景必有可折叠段"
        kept_sys = [m for m in folded
                    if type(m).__name__ == "SystemMessage"
                    and "中段业务指令" in str(m.content)]
        assert kept_sys, "中段 System 被折叠吞掉"
        # 折叠范围只在中段 System 的一侧
        assert fold.to_index < 6 or fold.from_index >= 6

    def test_fold_stops_at_explicit_pin(self):
        """显式 pin 终止折叠片段：被 pin 的消息原样保留。"""
        from backend.context_budget.collapse import fold_messages
        msgs = _history(8)
        pin_idx = 4
        pinned = msgs[pin_idx]
        folded, fold = fold_messages(
            msgs, keep_recent_turns=4, pin_indices={pin_idx})
        assert fold is not None
        assert pinned in folded, "被 pin 消息被折叠"
        # 被折叠段不得包含 pin 下标
        assert not (pin_idx <= fold.to_index and pin_idx >= fold.from_index) \
            if fold.from_index <= pin_idx <= fold.to_index else True

    def test_fold_never_orphans_tool_pair(self):
        """工具调用组（assistant(tool_calls)+ToolMessage）整组折叠，不拆对。"""
        from langchain_core.messages import ToolMessage
        from backend.context_budget.collapse import fold_messages
        assistant = AIMessage(
            content="", tool_calls=[{
                "name": "sql_query", "args": {"q": "x"},
                "id": "call_1", "type": "tool_call"}])
        tool = ToolMessage(content="查询结果" * 50, tool_call_id="call_1")
        msgs = _history(2) + [assistant, tool] + _history(4, 200) \
            + [HumanMessage(content="当前问题")]
        folded, fold = fold_messages(msgs, keep_recent_turns=4)
        assert fold is not None
        # 折叠段内若含 assistant(tool_calls)，其 ToolMessage 必须同段
        # （折叠后不留孤立 ToolMessage / 孤立调用）
        for m in folded:
            if type(m).__name__ == "ToolMessage":
                assert getattr(m, "tool_call_id", "") in str(
                    [getattr(x, "tool_calls", None) for x in folded]), \
                    "孤立 ToolMessage 残留"

    def test_multi_segment_folds_oldest_beneficial(self):
        """多个片段时折最旧的收益段，其余段留待后续调用。"""
        from backend.context_budget.collapse import fold_messages
        msgs = _history(2) + [SystemMessage(content="分隔指令")] \
            + _history(4, 200) + [HumanMessage(content="当前问题")]
        folded, fold = fold_messages(msgs, keep_recent_turns=4)
        assert fold is not None
        assert fold.from_index == 0, "第一个收益段从最旧片段开始"
        assert "分隔指令" in str(msgs[4].content)


class TestProjectionPairAtomic:
    def test_l2_keeps_policy_and_data_together(self):
        """L2 裁剪不得拆散投影二元组（policy System + 数据 AIMessage）。"""
        from backend.context_budget.role_safety import (
            build_historical_context,
            is_policy_system_message,
        )
        from backend.memory.token_budget import trim_messages_to_budget
        pair = build_historical_context("旧摘要内容" * 30)
        msgs = pair + _history(10)
        kept, dropped = trim_messages_to_budget(msgs, 400)
        # 二元组要么都在、要么都不在（policy 用模块谓词判定）
        has_policy = any(is_policy_system_message(m) for m in kept)
        has_data = any(
            type(m).__name__ == "AIMessage"
            and str(m.content).lstrip().startswith("<historical_context>")
            for m in kept)
        assert has_policy == has_data, \
            f"投影二元组被拆散：policy={has_policy} data={has_data}"

    def test_build_atomic_groups_pairs_projection(self):
        from backend.context_budget.pin import build_atomic_groups
        from backend.context_budget.role_safety import build_historical_context
        pair = build_historical_context("摘要")
        msgs = [HumanMessage(content="q1"), *pair, HumanMessage(content="q2")]
        groups = build_atomic_groups(msgs)
        assert [1, 2] in groups, "投影二元组必须成组"


class TestSseEventIsolation:
    def _emit(self, session_id):
        from backend.context_budget.metrics import emit_context_event
        emit_context_event(level="L2", action="history_trim",
                           before_tokens=100, after_tokens=60,
                           session_id=session_id)

    def test_events_isolated_per_session(self):
        self._emit("sess-A")
        self._emit("sess-B")
        got_a = []
        from backend.context_budget.metrics import drain_pending_events
        got_a = drain_pending_events(session_id="sess-A")
        got_b = drain_pending_events(session_id="sess-B")
        assert len(got_a) == 1 and len(got_b) == 1
        assert drain_pending_events(session_id="sess-B") == []
        assert drain_pending_events(session_id="sess-A") == []

    def test_no_cross_session_delivery(self):
        """A 会话的晚到事件不得被 B 会话取走（旧单队列缺陷）。"""
        self._emit("sess-A")
        from backend.context_budget.metrics import drain_pending_events
        assert drain_pending_events(session_id="sess-B") == [], \
            "B 会话取走了 A 的事件（归属缺陷复现）"
        assert len(drain_pending_events(session_id="sess-A")) == 1

    def test_drain_without_session_refused(self):
        self._emit("sess-A")
        from backend.context_budget.metrics import drain_pending_events
        assert drain_pending_events() == [], "无 id 调用不得整队取走"
        # A 的事件仍在，可被 A 自己取走
        assert len(drain_pending_events(session_id="sess-A")) == 1

    def test_unattributed_event_not_buffered(self):
        """无会话归属的晚到事件只降级日志，不入任何桶。"""
        from backend.context_budget import metrics as cbm
        cbm.emit_context_event(level="L2", action="history_trim",
                               before_tokens=10, after_tokens=5)
        with cbm._pending_lock:
            assert cbm._pending_events == {}


class TestUsageComponentMetrics:
    def test_distribution_and_total_recorded(self):
        """分项分布进 Histogram、总量进 Counter（看板可聚合口径）。"""
        from prometheus_client import REGISTRY
        from backend.context_budget.metrics import record_usage_components

        h_before = REGISTRY.get_sample_value(
            "context_tokens_by_component_tokens_sum",
            {"component": "history"}) or 0.0
        c_before = REGISTRY.get_sample_value(
            "context_component_tokens_total",
            {"component": "history"}) or 0.0

        record_usage_components(system=10, history=500, rag=30,
                                previous_outputs=0, tool_schema=64)

        h_after = REGISTRY.get_sample_value(
            "context_tokens_by_component_tokens_sum",
            {"component": "history"}) or 0.0
        c_after = REGISTRY.get_sample_value(
            "context_component_tokens_total",
            {"component": "history"}) or 0.0
        assert h_after - h_before >= 500
        assert c_after - c_before == 500
