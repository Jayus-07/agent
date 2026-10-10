"""STOP A 硬门禁验收测试 — 2026-10-01

冻结契约：每一次实际发给模型的请求都必须满足该次调用的输入预算；必需
内容放不下时返回明确错误且 provider 调用次数为 0。

覆盖（对应 STOP A 验收标准）：
  A. L5 故障边界：异步事件循环内注入 NameError（本次事故原始形态）/
     普通异常 / 超时 → 模型收到的仍是已裁剪消息，异常不外溢
  B. 硬门禁：12 万字符当前问题、System+当前问题+业务 pin 超预算 →
     ContextBudgetExceededError 且 provider spy 调用数为 0
  C. 预算为 0 = 没有空间：消息裁剪只留保护项、RAG 保留零条、
     previous_outputs 真正清空
  D. provider 超长错误短路：不重试、不 fallback，稳定错误码透传
  E. SSE 契约：稳定错误码 + 用户可操作提示 + retryable=False
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import backend.config as config
from backend.context_budget.errors import ContextBudgetExceededError
from backend.context_budget.manager import ContextBudgetManager

# ---------------------------------------------------------------------------
# 公共 fixture
# ---------------------------------------------------------------------------

_DEFAULT_SESSION = "multi-agent-default"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L5_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.90)
    monkeypatch.setattr(config, "CONTEXT_L4_TRIGGER_RATIO", 0.80)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)
    from backend.context_budget import token_counter as tc
    tc._counter_cache.clear()


@pytest.fixture
def _session():
    """给 L5 一个有效会话上下文（默认值 = 无会话，L5 直接跳过）。"""
    from backend.core.request_context import set_session_id
    set_session_id("sess-stop-a-test")
    yield
    set_session_id(_DEFAULT_SESSION)


@pytest.fixture(autouse=True)
def _clear_flights():
    """摘要单飞注册表逐用例清空（测试间不串槽位）。"""
    from backend.context_budget import auto_compact as ac
    ac._flights.clear()
    yield
    ac._flights.clear()


def _wait_flights_done(timeout: float = 10.0) -> None:
    """等待在途摘要线程真正结束（断言槽位释放前必须等线程，不能只 sleep）。"""
    import time as _t
    from backend.context_budget import auto_compact as ac
    deadline = _t.time() + timeout
    while ac._flights and _t.time() < deadline:
        _t.sleep(0.01)
    assert not ac._flights, "摘要线程结束后单飞槽位必须释放"


def _small_window(monkeypatch, window: int) -> None:
    monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", window)
    from backend.context_budget import token_counter as tc
    monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
    # 深度校准估算策略（不依赖 tiktoken 在线词表）
    monkeypatch.setattr(tc, "_get_provider", lambda m: "deepseek")
    tc._counter_cache.clear()


def _history(turns: int, msg_len: int = 200) -> list:
    msgs = []
    for i in range(turns):
        msgs.append(HumanMessage(content=f"用户第{i}轮：" + "问" * msg_len))
        msgs.append(AIMessage(content=f"助手第{i}轮：" + "答" * msg_len))
    return msgs


# ---------------------------------------------------------------------------
# A. L5 故障边界（本条对应事故本身：async 分支引用未定义 _L5_TASKS）
# ---------------------------------------------------------------------------


class TestL5FaultBoundary:
    def test_flight_registry_exists(self):
        """回归锚：进程内单飞注册表存在（原事故 = async 分支引用未定义
        _L5_TASKS 的 NameError；STOP B 起由 auto_compact._flights 承担）。"""
        from backend.context_budget import auto_compact as ac
        assert isinstance(ac._flights, dict)
        assert hasattr(ac, "start_summary_flight")
        assert hasattr(ac, "wait_flight_async")

    async def test_async_nameerror_contained_proxy_sends_trimmed(
            self, monkeypatch, _session):
        """事故复现回归：async 在线调用 + L5 触发 + 摘要线程 NameError →
        proxy 发送的仍是已裁剪消息，异常不外溢，槽位随线程结束释放。"""
        from backend.context_budget import auto_compact as ac
        from backend.infra.llm import proxy

        def _boom(*a, **k):
            raise NameError("name '_L5_TASKS' is not defined")

        monkeypatch.setattr(ac, "run_incremental_summary", _boom)
        # 阈值降到地板：任何用量都触发 L5（确定性走在线等待路径）
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)

        captured: list = []

        class _FakeLLM:
            async def ainvoke(self, *args, **kwargs):
                captured.append(args)
                return AIMessage(content="ok")

        monkeypatch.setattr(proxy, "_resolve_active_llm", lambda: _FakeLLM())
        monkeypatch.setattr(proxy, "_enforce_rate_limit", lambda u: None)
        _small_window(monkeypatch, window=400)

        big = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        result = await proxy.llm.ainvoke(big)

        assert captured, "provider spy 未被调用"
        sent = captured[0][0]
        assert len(sent) < len(big), "模型收到的是未裁剪原始消息"
        assert sent[-1].content == "当前问题"
        assert getattr(result, "content", "") == "ok"
        _wait_flights_done()

    async def test_async_generic_exception_contained(self, monkeypatch, _session):
        """在线等待路径：摘要线程任意异常 → 本轮确定性结果，不外溢。"""
        from backend.context_budget import auto_compact as ac

        def _boom(*a, **k):
            raise RuntimeError("summary provider down")

        monkeypatch.setattr(ac, "run_incremental_summary", _boom)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
        _small_window(monkeypatch, window=400)

        msgs = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        prepared = await ContextBudgetManager().prepare_llm_context_async(
            messages=msgs)
        assert len(prepared.messages) < len(msgs)
        assert not prepared.overflow
        _wait_flights_done()

    def test_sync_timeout_contained(self, monkeypatch, _session):
        """worker 线程路径：摘要超时（TimeoutError）→ 沿用确定性裁剪结果。"""
        from backend.context_budget import auto_compact as ac

        def _timeout(*a, **k):
            raise TimeoutError("L5 摘要 LLM 调用超时（30s）")

        monkeypatch.setattr(ac, "run_incremental_summary", _timeout)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
        _small_window(monkeypatch, window=400)

        msgs = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        prepared = ContextBudgetManager().prepare_llm_context(messages=msgs)
        assert len(prepared.messages) < len(msgs)
        assert not prepared.overflow
        _wait_flights_done()

    def test_sync_generic_exception_contained(self, monkeypatch, _session):
        """worker 线程路径：摘要线程任意异常 → 不外溢（L2/L4/硬裁保留）。"""
        from backend.context_budget import auto_compact as ac

        def _boom(*a, **k):
            raise ValueError("fold_rebuild exploded")

        monkeypatch.setattr(ac, "run_incremental_summary", _boom)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
        _small_window(monkeypatch, window=400)

        msgs = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        prepared = ContextBudgetManager().prepare_llm_context(messages=msgs)
        assert len(prepared.messages) < len(msgs)
        _wait_flights_done()

    async def test_online_wait_timeout_defers_to_next_turn(
            self, monkeypatch, _session):
        """在线等待超时 → 本轮确定性结果；线程继续跑完落库（下一轮生效）；
        等待期间同键不再起第二个摘要（槽位仍被持有）。"""
        import threading

        from backend.context_budget import auto_compact as ac

        release = threading.Event()
        started_summary = threading.Event()

        def _slow_summary(*a, **k):
            started_summary.set()
            release.wait(10)
            return None  # 摘要最终「失败」（无增量），模拟慢 provider

        monkeypatch.setattr(ac, "run_incremental_summary", _slow_summary)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
        monkeypatch.setattr(config, "CONTEXT_L5_ONLINE_WAIT_SECONDS", 0.2)
        _small_window(monkeypatch, window=400)

        msgs = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        prepared = await ContextBudgetManager().prepare_llm_context_async(
            messages=msgs)
        assert len(prepared.messages) < len(msgs)
        started_summary.wait(5)
        # 摘要线程仍在运行（放弃等待 ≠ 线程停止）：同键再触发必须被抑制
        flight = ac.start_summary_flight("sess-stop-a-test")
        assert flight is None, "等待超时后槽位必须仍被持有，不得重复起摘要"
        release.set()
        _wait_flights_done()

    async def test_concurrent_same_session_single_flight(
            self, monkeypatch, _session):
        """同租户同会话并发 → 摘要在途时只发起一次（第二个触发复用确定性
        结果）；水位线 CAS 保证摘要结束后紧随的再次触发也不会重复调 LLM。"""
        import threading

        from backend.context_budget import auto_compact as ac

        calls: list = []
        started = threading.Event()
        release = threading.Event()

        def _summary(*a, **k):
            calls.append(1)
            started.set()
            release.wait(10)
            return None

        monkeypatch.setattr(ac, "run_incremental_summary", _summary)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
        _small_window(monkeypatch, window=400)

        msgs = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        mgr = ContextBudgetManager()
        first = asyncio.create_task(
            mgr.prepare_llm_context_async(messages=msgs))
        # 等第一个请求的摘要线程真正在途，第二个请求才发起
        await asyncio.to_thread(started.wait, 5)
        second = await mgr.prepare_llm_context_async(messages=msgs)
        assert second.messages[-1].content == "当前问题"
        release.set()
        await first
        assert len(calls) == 1, f"摘要在线途时被触发了 {len(calls)} 次"
        _wait_flights_done()

    async def test_different_tenants_not_suppressed(self, monkeypatch):
        """不同租户互不抑制：两个租户各起一次摘要。"""
        from backend.context_budget import auto_compact as ac
        from backend.core.request_context import set_session_id, set_tool_tenant_id

        calls: list = []

        def _summary(*a, **k):
            calls.append(1)
            return None

        monkeypatch.setattr(ac, "run_incremental_summary", _summary)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
        _small_window(monkeypatch, window=400)

        msgs = [SystemMessage(content="系统提示")] + _history(6) \
            + [HumanMessage(content="当前问题")]

        async def _one(tenant: str):
            set_tool_tenant_id(tenant)
            set_session_id("sess-tenant-test")
            return await ContextBudgetManager().prepare_llm_context_async(
                messages=msgs)

        try:
            await asyncio.gather(_one("tenant-a"), _one("tenant-b"))
        finally:
            set_tool_tenant_id("")
            set_session_id(_DEFAULT_SESSION)
        assert len(calls) == 2, "不同租户的摘要不得互相抑制"
        _wait_flights_done()


# ---------------------------------------------------------------------------
# B. 硬门禁：必需内容放不下 → 明确错误 + provider 调用 0 次
# ---------------------------------------------------------------------------


class TestHardGate:
    def test_oversized_current_question_rejected_zero_provider_calls(
            self, monkeypatch):
        """12 万字符当前问题 + 关闭软降级：明确错误 + provider spy 调用数为 0。

        D-9（commit 8b4b41f）后 overflow 默认走优雅降级截断，硬拒由
        CONTEXT_BUDGET_SOFT_OVERFLOW=false 回退。本用例锁定回退态下的
        硬门禁契约：超限必须报错且**一次都不能**打 provider。
        """
        from backend.infra.llm import proxy

        calls: list = []

        class _SpyLLM:
            def invoke(self, *args, **kwargs):
                calls.append(args)
                return AIMessage(content="ok")

        monkeypatch.setenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "false")
        monkeypatch.setattr(proxy, "_resolve_active_llm", lambda: _SpyLLM())
        monkeypatch.setattr(proxy, "_enforce_rate_limit", lambda u: None)
        _small_window(monkeypatch, window=512)

        big_q = "问" * 120_000
        msgs = [SystemMessage(content="系统提示"),
                HumanMessage(content=big_q)]
        with pytest.raises(ContextBudgetExceededError) as ei:
            proxy.llm.invoke(msgs)
        assert calls == [], "provider 被调用 = 硬门禁失守"
        assert ei.value.code == "context_length_exceeded"
        assert ei.value.used_tokens > 512

    def test_oversized_current_question_soft_truncates_by_default(
            self, monkeypatch):
        """同一超限输入在默认（软降级）下：截断放行，且问题不被整段吞掉。

        D-9 的取舍是"不把大召回打成 500"，但必须保留 system 与最新用户
        消息的可辨识内容——本用例防止实现退化成"清空消息后照常发"。
        """
        from backend.infra.llm import proxy

        calls: list = []

        class _SpyLLM:
            def invoke(self, *args, **kwargs):
                calls.append(args)
                return AIMessage(content="ok")

        monkeypatch.setenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "true")
        monkeypatch.setattr(proxy, "_resolve_active_llm", lambda: _SpyLLM())
        monkeypatch.setattr(proxy, "_enforce_rate_limit", lambda u: None)
        _small_window(monkeypatch, window=512)

        big_q = "问" * 120_000
        msgs = [SystemMessage(content="系统提示"),
                HumanMessage(content=big_q)]
        proxy.llm.invoke(msgs)

        assert len(calls) == 1, "软降级应当放行一次调用"
        sent = list(calls[0][0])
        assert sent, "软降级不得把消息清空"
        assert isinstance(sent[0], SystemMessage), "system 指令被丢弃"
        assert sent[0].content == "系统提示"
        assert any(isinstance(m, HumanMessage) and m.content
                   for m in sent), "最新用户消息被整体丢弃"

    def test_system_plus_pin_over_budget_rejected(self, monkeypatch):
        """System＋当前问题＋业务 pin 总和超预算 → manager 报 overflow，
        且 pin 与保护项保留（proxy 层据此拒发）。"""
        from backend.context_budget.pin import PIN_ENTITY, PinnedContext

        _small_window(monkeypatch, window=300)
        pin_val = "ORD20261001001"
        msgs = [
            SystemMessage(content="系统指令" * 80),
            HumanMessage(content=f"订单 {pin_val} 的退款进度" + "详" * 300),
            AIMessage(content="历史回复" * 100),
            HumanMessage(content="当前问题" * 50),
        ]
        pins = PinnedContext().mark_content_match(pin_val, PIN_ENTITY)
        prepared = ContextBudgetManager().prepare_llm_context(
            messages=msgs, pins=pins)
        assert prepared.overflow is True
        kept_text = "\n".join(str(m.content) for m in prepared.messages)
        assert pin_val in kept_text, "业务 pin 被丢弃"
        assert "当前问题" in kept_text, "当前问题被丢弃"

    def test_overflow_prepared_rejected_at_proxy(self, monkeypatch):
        """overflow 且关闭软降级 → proxy 必须抛错（硬门禁不放行）。

        背景（D-9，commit 8b4b41f）：overflow 的**默认**终局已从硬拒改为
        优雅降级截断（CONTEXT_BUDGET_SOFT_OVERFLOW 默认 true），
        因为多文档大召回整体硬拒会造成 500。硬拒路径仍然保留，
        由该开关回退——本用例锁定"关掉软降级时门禁必须真的拒发"，
        软降级路径由下一条用例覆盖。
        """
        from backend.context_budget import context_budget as budget_manager
        from backend.context_budget.models import ContextUsage, PreparedContext
        from backend.infra.llm import proxy

        def _overflow(**kwargs):
            usage = ContextUsage(used_tokens=9999, input_budget=100,
                                 remaining_tokens=0, usage_ratio=99.99)
            return PreparedContext(messages=kwargs.get("messages") or [],
                                   usage=usage, overflow=True)

        monkeypatch.setenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "false")
        monkeypatch.setattr(budget_manager, "prepare_llm_context", _overflow)
        # 预算归零 → 快路径必不通过 → prepare 的 overflow 结果被门禁消费
        monkeypatch.setattr(
            budget_manager, "get_input_budget", lambda **k: 0)
        msgs = [SystemMessage(content="s"), HumanMessage(content="q")]
        with pytest.raises(ContextBudgetExceededError):
            proxy._preflight_context((msgs,))

    def test_overflow_prepared_soft_truncates_by_default(self, monkeypatch):
        """overflow 且软降级开启（默认）→ 截断后放行，且保留 system + 最新消息。

        D-9 的契约：宁可降级截断也不把整轮请求打成 500，但**不能丢**
        system 指令与当前用户问题——否则模型会在无指令、无问题的状态下作答。
        """
        from backend.context_budget import context_budget as budget_manager
        from backend.context_budget.models import ContextUsage, PreparedContext
        from backend.infra.llm import proxy

        def _overflow(**kwargs):
            usage = ContextUsage(used_tokens=9999, input_budget=100,
                                 remaining_tokens=0, usage_ratio=99.99)
            return PreparedContext(messages=kwargs.get("messages") or [],
                                   usage=usage, overflow=True)

        monkeypatch.setenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "true")
        monkeypatch.setattr(budget_manager, "prepare_llm_context", _overflow)
        monkeypatch.setattr(
            budget_manager, "get_input_budget", lambda **k: 100000)
        msgs = [SystemMessage(content="系统指令"), HumanMessage(content="当前问题")]
        out = proxy._preflight_context((msgs,))
        kept = out[0]
        assert kept, "软降级不得把消息清空"
        assert isinstance(kept[0], SystemMessage), "system 指令被丢弃"
        assert kept[0].content == "系统指令"
        assert any(isinstance(m, HumanMessage) and "当前问题" in str(m.content)
                   for m in kept), "最新用户消息被丢弃"

    def test_preflight_failure_fails_closed(self, monkeypatch):
        """预检自身故障 = 无法证明预算内 → 拒发（不再原样放行）。"""
        from backend.context_budget import context_budget as budget_manager
        from backend.infra.llm import proxy

        def _boom(*a, **k):
            raise RuntimeError("budget down")

        monkeypatch.setattr(budget_manager, "get_input_budget", _boom)
        msgs = [SystemMessage(content="s"), HumanMessage(content="q")]
        with pytest.raises(RuntimeError, match="budget down"):
            proxy._preflight_context((msgs,))

    def test_non_message_shapes_gated(self, monkeypatch):
        """str 输入（如 L5 摘要 prompt）超预算 → 拒绝，不再绕过门禁。"""
        from backend.infra.llm import proxy
        _small_window(monkeypatch, window=100)
        with pytest.raises(ContextBudgetExceededError):
            proxy._preflight_context(("超长摘要输入" * 500,))
        # 预算内原样通过（返回原 args 元组）
        assert proxy._preflight_context(("短输入",)) == ("短输入",)


# ---------------------------------------------------------------------------
# C. 预算为 0 = 没有空间（不是关闭限制）
# ---------------------------------------------------------------------------


class TestBudgetZeroSemantics:
    def test_trim_messages_zero_budget_keeps_protected_only(self):
        from backend.memory.token_budget import trim_messages_to_budget
        msgs = [
            SystemMessage(content="系统指令"),
            HumanMessage(content="旧问题一"),
            AIMessage(content="旧回答一"),
            HumanMessage(content="旧问题二"),
        ]
        kept, dropped = trim_messages_to_budget(msgs, 0)
        assert [type(m).__name__ for m in kept] == ["SystemMessage"]
        assert dropped == 3

    def test_trim_messages_zero_budget_keeps_pins(self):
        from backend.memory.token_budget import trim_messages_to_budget
        msgs = [
            SystemMessage(content="系统指令"),
            HumanMessage(content="旧问题"),
            HumanMessage(content="确认退款 ORD123"),
        ]
        kept, dropped = trim_messages_to_budget(msgs, 0, pin_indices={2})
        assert len(kept) == 2
        assert dropped == 1
        assert kept[-1].content == "确认退款 ORD123"

    def test_trim_texts_zero_budget_keeps_none(self):
        from backend.memory.token_budget import trim_texts_to_budget
        kept, dropped = trim_texts_to_budget(["证据一", "证据二", "证据三"], 0)
        assert kept == []
        assert dropped == 3

    def test_shrink_po_zero_budget_clears_all(self, monkeypatch):
        from backend.context_budget.manager import _shrink_po
        po = {"step_1": {"output": "产出" * 100}}
        assert _shrink_po(po, 0) == {}
        assert _shrink_po(po, -5) == {}

    def test_manager_zero_space_clears_po_and_flags_overflow(
            self, monkeypatch):
        """System+当前问题占满预算 → po 清空、保护项保留、overflow=True
        （上游硬门禁据此拒发）。"""
        _small_window(monkeypatch, window=120)
        po = {"step_1": {"output": "前序产出" * 200}}
        msgs = [
            SystemMessage(content="系统指令" * 60),
            HumanMessage(content="当前问题" * 60),
        ]
        prepared = ContextBudgetManager().prepare_llm_context(
            messages=msgs, previous_outputs=po)
        assert prepared.previous_outputs == {}, "零空间下 po 必须真正清空"
        assert prepared.overflow is True
        kept = prepared.messages
        assert any(type(m).__name__ == "SystemMessage" for m in kept)
        assert kept[-1].content.startswith("当前问题")


# ---------------------------------------------------------------------------
# D. provider 超长错误短路：不重试、不 fallback
# ---------------------------------------------------------------------------


class TestProviderContextLengthShortCircuit:
    _ERR_TEXT = ("Error code: 400 - This model's maximum context length is "
                 "4096 tokens. However, you requested 5000 tokens.")

    def test_is_context_length_error_truth_table(self):
        from backend.infra.llm.error_taxonomy import is_context_length_error

        class _CtxErr(RuntimeError):
            pass

        assert is_context_length_error(RuntimeError(self._ERR_TEXT))
        assert is_context_length_error(_CtxErr("输入超出 context window"))
        assert not is_context_length_error(RuntimeError("invalid api key"))
        assert not is_context_length_error(RuntimeError("rate limit exceeded"))

    def test_sync_no_retry_no_fallback(self, monkeypatch):
        from backend.infra.llm import proxy

        calls: list = []
        fb_calls: list = []

        def _boom(*a, **k):
            calls.append(1)
            raise RuntimeError(self._ERR_TEXT)

        class _FakeFallback:
            def invoke(self, *a, **k):
                fb_calls.append(1)
                return AIMessage(content="fallback")

        monkeypatch.setattr(proxy, "LLM_MAX_RETRIES", 3)
        monkeypatch.setattr(proxy, "_get_fallback_llm", lambda: _FakeFallback())
        monkeypatch.setattr(proxy, "LLM_ALLOW_DEGRADED_ANSWER", True)

        with pytest.raises(ContextBudgetExceededError) as ei:
            proxy._call_with_resilience(_boom)
        assert len(calls) == 1, "超长输入不得重试"
        assert fb_calls == [], "超长输入不得换 fallback 模型（必然再超）"
        assert ei.value.code == "context_length_exceeded"

    async def test_async_no_retry_no_fallback(self, monkeypatch):
        from backend.infra.llm import proxy

        calls: list = []
        fb_calls: list = []

        async def _aboom(*a, **k):
            calls.append(1)
            raise RuntimeError(self._ERR_TEXT)

        class _FakeFallback:
            async def ainvoke(self, *a, **k):
                fb_calls.append(1)
                return AIMessage(content="fallback")

        monkeypatch.setattr(proxy, "LLM_MAX_RETRIES", 3)
        monkeypatch.setattr(proxy, "_get_fallback_llm", lambda: _FakeFallback())
        monkeypatch.setattr(proxy, "LLM_ALLOW_DEGRADED_ANSWER", True)

        with pytest.raises(ContextBudgetExceededError):
            await proxy._acall_with_resilience(_aboom)
        assert len(calls) == 1
        assert fb_calls == []


# ---------------------------------------------------------------------------
# E. SSE 契约：稳定错误码 + 用户可操作提示
# ---------------------------------------------------------------------------


class TestSseContract:
    def test_to_sse_data_shape(self):
        err = ContextBudgetExceededError(
            used_tokens=9000, input_budget=7168, stage="final_gate")
        data = err.to_sse_data()
        assert data["code"] == "context_length_exceeded"
        assert data["retryable"] is False
        assert data["used_tokens"] == 9000
        assert data["input_budget"] == 7168
        assert data["stage"] == "final_gate"
        # 用户可操作提示：不泄露堆栈与内部细节
        assert "Traceback" not in data["message"]
        assert "缩短" in data["message"] or "新会话" in data["message"]

    def test_runner_maps_error_to_stable_code(self):
        """runner 的 worker 异常映射必须能识别本异常（import 契约）。"""
        import backend.orchestration.graph.runner as runner_mod
        assert runner_mod.ContextBudgetExceededError is ContextBudgetExceededError
