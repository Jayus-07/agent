"""tests/orchestration/test_request_context.py — RequestContext 上下文契约测试

覆盖:
1. dataclass 形态 bind():user_id 透传到工具层 ContextVar 与日志上下文;
   身份借读注入 RAG 运行态（orchestration → RAG 唯一转换点）
2. checkpoint_safe():剔除 trace/sink,保留纯字段
3. dict 还原:get_context_from_state 兼容 checkpointer 安全形态,
   还原后 bind_sink=False(bind 不覆盖当前线程 sink)
"""
import pytest

from backend.orchestration.request_context import (
    RequestContext,
    get_context_from_state,
)


class TestDataclassContext:
    def test_bind_propagates_user_id(self):
        from backend.tools import session as tool_session
        from backend.shared import logger as log_module

        ctx = RequestContext(session_id="s1", user_id="u9", kb_id="k1")
        token = tool_session._current_user_id.set("")  # 隔离其他测试残留
        try:
            ctx.bind()
            assert tool_session.get_tool_user_id() == "u9"
            assert log_module._user_id_ctx.get() == "u9"
        finally:
            tool_session._current_user_id.reset(token)

    def test_bind_clears_stale_sink(self):
        from backend.infra.llm import proxy

        proxy.set_stream_sink(lambda t: None)
        RequestContext(session_id="s", user_id="u", kb_id="k").bind()
        assert proxy._stream_sink_var.get() is None

    def test_bind_attaches_identity_to_rag_state(self):
        """bind() 把权威实例注入 RAG 运行态（组合借读，非复制）。

        orchestration → RAG 的唯一身份转换点：检索层经
        get_context().identity 读到的必须是 bind 的同一实例。
        """
        from backend.rag.context import get_context

        ctx = RequestContext(session_id="s1", user_id="u1",
                             department="hr", subject_type="employee")
        ctx.bind()
        assert get_context().identity is ctx

    def test_clear_context_resets_borrowed_identity(self):
        """clear_context 重置运行态后，identity 回到全新默认实例，
        不残留上一请求借读的权威实例（防跨请求串味）。"""
        from backend.rag.context import clear_context, get_context

        ctx = RequestContext(department="hr")
        ctx.bind()
        clear_context()
        assert get_context().identity is not ctx
        assert get_context().identity.department == ""


class TestCheckpointSafe:
    def test_checkpoint_safe_strips_objects(self):
        ctx = RequestContext(session_id="s1", user_id="u1", kb_id="k1",
                             trace=object(), stream_sink=lambda t: None)
        safe = ctx.checkpoint_safe()
        assert safe == {"session_id": "s1", "user_id": "u1",
                        "kb_id": "k1", "department": "",
                        "subject_type": "", "model": ""}
        # 可 JSON 序列化（checkpoint 传输前提）
        import json
        json.dumps(safe, ensure_ascii=False)

    def test_dict_state_round_trip(self):
        ctx = RequestContext(session_id="s1", user_id="u2", kb_id="k2",
                             model="deepseek-chat", trace=object())
        state = {"request_context": ctx.checkpoint_safe()}
        restored = get_context_from_state(state)
        assert isinstance(restored, RequestContext)
        assert restored.session_id == "s1"
        assert restored.user_id == "u2"
        assert restored.model == "deepseek-chat"
        assert restored.subject_type == ""
        assert restored.trace is None
        assert restored.stream_sink is None
        assert restored.bind_sink is False

    def test_dict_restore_does_not_clear_sink(self):
        """还原形态 bind 不覆盖当前线程已绑定的 sink（防清掉 worker 主上下文）。"""
        from backend.infra.llm import proxy

        state = {"request_context": RequestContext(
            session_id="s", user_id="u", kb_id="k").checkpoint_safe()}
        restored = get_context_from_state(state)
        sentinel = lambda t: None  # noqa: E731
        proxy.set_stream_sink(sentinel)
        try:
            restored.bind()
            assert proxy._stream_sink_var.get() is sentinel
        finally:
            proxy.reset_stream_sink()

    def test_dataclass_state_still_supported(self):
        ctx = RequestContext(session_id="s", user_id="u", kb_id="k")
        assert get_context_from_state({"request_context": ctx}) is ctx

    def test_missing_context_returns_none(self):
        assert get_context_from_state({}) is None
        assert get_context_from_state(None) is None
        assert get_context_from_state({"request_context": "garbage"}) is None
