"""STOP C 验收测试 — 消息角色与事件归属（2026-10-01）

对应验收项 CONTEXT_EVENT_ISOLATION_PASS：
  - L4 折叠窗口不跨中段 SystemMessage / 显式 pin（工具组不拆对）
  - L2 摘要投影二元组（policy System + 数据 AIMessage）原子保留
  - SSE 事件按会话隔离：晚到事件不冒充他人连接
  - 分项 token：分布（Histogram）+ 总量（Counter）可聚合，Gauge 仅最近值
"""
from dataclasses import replace

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
    @staticmethod
    def _scope(**overrides):
        from backend.context_budget.metrics import ContextEventScope

        fields = {
            "product_id": "agent",
            "tenant_id": "tenant-A",
            "user_id": "user-A",
            "session_id": "same-session",
            "request_id": "request-A",
        }
        fields.update(overrides)
        return ContextEventScope(**fields)

    def _emit(self, scope, **extra):
        from backend.context_budget.metrics import emit_context_event

        emit_context_event(
            level="L2", action="history_trim", before_tokens=100,
            after_tokens=60, session_id=scope.session_id,
            event_scope=scope, **extra,
        )

    @pytest.mark.parametrize(
        "field", ["product_id", "tenant_id", "user_id", "session_id", "request_id"]
    )
    def test_events_require_exact_product_identity_and_request_scope(self, field):
        """任一身份维度不同都不能排空另一个请求的 context 事件。"""
        scope_a = self._scope()
        scope_b = replace(scope_a, **{field: f"other-{field}"})
        self._emit(scope_a)
        from backend.context_budget.metrics import drain_pending_events

        assert drain_pending_events(scope=scope_b) == []
        assert len(drain_pending_events(scope=scope_a)) == 1

    def test_late_event_from_previous_request_is_not_delivered_to_next(self):
        """同一会话的前一请求迟到事件不能进入下一次请求的 SSE。"""
        old_request = self._scope(request_id="timed-out-request")
        next_request = replace(old_request, request_id="next-request")
        self._emit(old_request)
        from backend.context_budget.metrics import drain_pending_events

        assert drain_pending_events(scope=next_request) == []
        assert len(drain_pending_events(scope=old_request)) == 1

    def test_drain_without_complete_scope_refused(self):
        scope = self._scope()
        self._emit(scope)
        from backend.context_budget.metrics import drain_pending_events

        assert drain_pending_events() == [], "无请求作用域调用不得排空事件"
        assert len(drain_pending_events(scope=scope)) == 1

    def test_unattributed_event_not_buffered(self):
        """只有 session_id、缺少完整可信作用域时不缓存晚到事件。"""
        from backend.context_budget import metrics as cbm

        cbm.emit_context_event(
            level="L2", action="history_trim", before_tokens=10,
            after_tokens=5, session_id="same-session",
        )
        with cbm._pending_lock:
            assert cbm._pending_events == {}

    def test_sink_rejects_event_from_another_request_scope(self):
        from backend.context_budget.metrics import (
            emit_context_event,
            reset_context_event_scope,
            reset_context_sink,
            set_context_event_scope,
            set_context_sink,
        )

        owner = self._scope()
        foreign = replace(owner, user_id="user-B")
        received = []
        scope_token = set_context_event_scope(owner)
        sink_token = set_context_sink(received.append, scope=owner)
        try:
            emit_context_event(
                level="L2", action="history_trim", before_tokens=10,
                after_tokens=5, event_scope=foreign,
            )
            assert received == []
            emit_context_event(
                level="L2", action="history_trim", before_tokens=10,
                after_tokens=5, event_scope=owner,
            )
            assert len(received) == 1
        finally:
            reset_context_sink(sink_token)
            reset_context_event_scope(scope_token)

    def test_context_event_does_not_expose_unapproved_extra_content(self):
        from backend.context_budget.metrics import (
            emit_context_event,
            reset_context_event_scope,
            reset_context_sink,
            set_context_event_scope,
            set_context_sink,
        )

        scope = self._scope()
        received = []
        scope_token = set_context_event_scope(scope)
        sink_token = set_context_sink(received.append, scope=scope)
        try:
            emit_context_event(
                level="L2", action="history_trim", before_tokens=10,
                after_tokens=5, event_scope=scope,
                question="用户的敏感问题原文", folded_messages=2,
                fold_id="fold-aaaaaaaaaaaa",
            )
        finally:
            reset_context_sink(sink_token)
            reset_context_event_scope(scope_token)

        assert len(received) == 1
        assert "question" not in received[0]
        assert received[0]["folded_messages"] == 2
        assert received[0]["fold_id"] == "fold-aaaaaaaaaaaa"
        assert "用户的敏感问题原文" not in str(received[0])

    def test_context_event_rejects_untrusted_fold_identifier(self):
        from backend.context_budget.metrics import (
            emit_context_event,
            reset_context_event_scope,
            reset_context_sink,
            set_context_event_scope,
            set_context_sink,
        )

        scope = self._scope()
        received = []
        scope_token = set_context_event_scope(scope)
        sink_token = set_context_sink(received.append, scope=scope)
        try:
            emit_context_event(
                level="L4", action="collapse", before_tokens=10,
                after_tokens=5, event_scope=scope,
                fold_id="用户备注原文可能进入这里",
            )
        finally:
            reset_context_sink(sink_token)
            reset_context_event_scope(scope_token)

        assert len(received) == 1
        assert "fold_id" not in received[0]
        assert "用户备注原文可能进入这里" not in str(received[0])

    def test_context_event_rejects_unknown_action_and_malformed_metadata(self):
        from backend.context_budget.metrics import (
            emit_context_event,
            reset_context_event_scope,
            reset_context_sink,
            set_context_event_scope,
            set_context_sink,
        )

        scope = self._scope()
        received = []
        scope_token = set_context_event_scope(scope)
        sink_token = set_context_sink(received.append, scope=scope)
        try:
            emit_context_event(
                level="L2", action=["history_trim"], before_tokens=10,
                after_tokens=5, event_scope=scope,
            )
            emit_context_event(
                level="L2", action="unknown_action", before_tokens=10,
                after_tokens=5, event_scope=scope,
            )
        finally:
            reset_context_sink(sink_token)
            reset_context_event_scope(scope_token)

        assert received == []

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
