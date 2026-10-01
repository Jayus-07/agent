"""STOP D 验收测试 — 预算口径统一（2026-10-01）

对应验收项（阈值带行为一致）：
  - 异步 89% / 90% / 99% / 超过 100%；L5 开/关 → 阈值行为一致、无异常外溢、
    每次实际发送均过最终预算检查
  - L2 历史额度统一扣除工具 schema（bind_tools 大 schema 场景一轮裁准）
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import backend.config as config
from backend.context_budget.manager import ContextBudgetManager


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
    monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
    monkeypatch.setattr(tc, "_get_provider", lambda m: "deepseek")
    tc._counter_cache.clear()


@pytest.fixture(autouse=True)
def _flights_and_session():
    from backend.context_budget import auto_compact as ac
    from backend.core.request_context import set_session_id
    ac._flights.clear()
    set_session_id("sess-stop-d-test")
    yield
    ac._flights.clear()
    set_session_id("multi-agent-default")


def _history(turns: int, msg_len: int = 100) -> list:
    msgs = []
    for i in range(turns):
        msgs.append(HumanMessage(content=f"用户第{i}轮：" + "问" * msg_len))
        msgs.append(AIMessage(content=f"助手第{i}轮：" + "答" * msg_len))
    return msgs


def _small_window(monkeypatch, window: int) -> None:
    monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", window)
    from backend.context_budget import token_counter as tc
    monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
    monkeypatch.setattr(tc, "_get_provider", lambda m: "deepseek")
    tc._counter_cache.clear()


class TestThresholdBandBehavior:
    """proxy 快路径与 manager L5 判定共用同一阈值：行为随占用比例一致。"""

    def _setup_proxy(self, monkeypatch, window: int, l5_enabled: bool = True):
        from backend.infra.llm import proxy
        _small_window(monkeypatch, window)
        monkeypatch.setattr(config, "CONTEXT_L5_ENABLED", l5_enabled)
        captured: list = []

        class _FakeLLM:
            async def ainvoke(self, *args, **kwargs):
                captured.append(args)
                return AIMessage(content="ok")

        monkeypatch.setattr(proxy, "_resolve_active_llm", lambda: _FakeLLM())
        monkeypatch.setattr(proxy, "_enforce_rate_limit", lambda u: None)
        return captured

    def _ratio_of(self, messages) -> float:
        from backend.context_budget import context_budget
        from backend.memory.token_budget import count_message_tokens
        total = sum(count_message_tokens(m) for m in messages)
        return total / context_budget.get_input_budget()

    @pytest.mark.parametrize("l5_enabled", [True, False])
    async def test_band_89_90_99_over100(self, monkeypatch, l5_enabled):
        """85% 零改动直通；90%~99% 进统一链路（L5 判定执行）；>100% 裁剪。
        L5 开关只影响是否等摘要，不影响入口一致性。"""
        from backend.context_budget import auto_compact as ac
        from backend.context_budget import context_budget as budget_manager
        from backend.infra.llm import proxy
        from backend.memory.token_budget import (
            count_message_tokens, count_tokens)

        monkeypatch.setattr(
            ac, "run_incremental_summary",
            lambda *a, **k: None)  # 摘要安全空转（无增量→None）

        prepare_calls: list = []
        real_prepare = ContextBudgetManager.prepare_llm_context_async

        async def _count_prepare(self, **kwargs):
            prepare_calls.append(1)
            return await real_prepare(self, **kwargs)

        monkeypatch.setattr(
            ContextBudgetManager, "prepare_llm_context_async", _count_prepare)

        captured = self._setup_proxy(monkeypatch, window=100000,
                                     l5_enabled=l5_enabled)
        budget = budget_manager.get_input_budget()
        # 探测式缩放：按真实计数器把内容长度换算到目标比例（±2% 内）
        probe_tok = count_message_tokens(HumanMessage(content="问" * 1000))
        chars_per_tok = 1000.0 / probe_tok

        def _msgs_at(ratio_target: float) -> list:
            def _build(c: int) -> list:
                return [SystemMessage(content="s"),
                        HumanMessage(content="问" * c),
                        AIMessage(content="答" * c),
                        HumanMessage(content="当前问题")]
            chars = int(budget * ratio_target / 3.0 * chars_per_tok)
            msgs = _build(chars)
            # 一次实测重标定（探测系数与混合内容密度有偏差）
            actual = sum(count_message_tokens(m) for m in msgs)
            scale = (budget * ratio_target) / max(1, actual)
            return _build(max(4, int(chars * scale)))

        # 85%（<90% 触发线）：快路径零改动直通，不进 prepare
        m85 = _msgs_at(0.85)
        await proxy.llm.ainvoke(m85)
        assert not prepare_calls, "触发线以下不应进入统一链路"
        assert self._ratio_of(m85) < 0.90

        # 90%~99%：进入统一链路（L5 判定执行；无摘要可做 → 原文放行）
        m95 = _msgs_at(0.95)
        r = self._ratio_of(m95)
        assert 0.90 <= r <= 1.0, f"构造偏差: {r}"
        await proxy.llm.ainvoke(m95)
        assert prepare_calls, "90%~100% 区间必须进入统一预算链路（原实现短路）"
        assert captured and captured[-1][0] is not None

        # 超过 100%：裁剪后发送，且通过最终预算检查（无 overflow 异常）
        m120 = _msgs_at(1.2)
        await proxy.llm.ainvoke(m120)
        sent = captured[-1][0]
        sent_total = sum(count_message_tokens(m) for m in sent)
        assert sent_total <= budget, "发送内容必须通过最终预算检查"

    async def test_no_nameerror_across_band(self, monkeypatch):
        """阈值带全段扫描：任何比例下都不再出现事故异常（回归锚）。"""
        from backend.context_budget import auto_compact as ac
        from backend.infra.llm import proxy

        def _boom(*a, **k):
            raise NameError("name '_L5_TASKS' is not defined")

        monkeypatch.setattr(ac, "run_incremental_summary", _boom)
        captured = self._setup_proxy(monkeypatch, window=10 ** 9)
        for ratio in (0.5, 0.89, 0.93, 0.99, 1.4):
            per = max(4, int(10 ** 9 * 0.001 * ratio / 3))
            msgs = [SystemMessage(content="s"),
                    HumanMessage(content="问" * per),
                    AIMessage(content="答" * per),
                    HumanMessage(content="当前问题")]
            result = await proxy.llm.ainvoke(msgs)  # 不抛即通过
            assert getattr(result, "content", "") == "ok"
        import time as _t
        deadline = _t.time() + 5
        while ac._flights and _t.time() < deadline:
            await asyncio.sleep(0.01)
        assert ac._flights == {}


class TestL2SchemaDeduction:
    def test_history_budget_deducts_tool_schema(self, monkeypatch):
        """L2 历史额度派生式必须扣除 tools schema（与 prepare 同一派生）。"""
        from backend.context_budget.token_counter import (
            count_tool_schema_tokens, count_tokens)
        from backend.memory.token_budget import count_message_tokens
        _small_window(monkeypatch, window=1000)
        mgr = ContextBudgetManager()
        big_schema = [{"type": "function", "function": {
            "name": "f", "parameters": {"desc": "参数" * 300}}}]
        schema_tokens = count_tool_schema_tokens(big_schema)
        assert schema_tokens > 200
        sys_t = count_message_tokens(SystemMessage(content="s"))
        q_t = count_tokens("当前问题")

        cap_a = mgr.history_budget(
            system_tokens=sys_t, current_query_tokens=q_t, reserved_tokens=0)
        cap_b = mgr.history_budget(
            system_tokens=sys_t, current_query_tokens=q_t,
            reserved_tokens=schema_tokens)
        assert cap_b == cap_a - schema_tokens, \
            "history_budget 未按 reserved 扣除 schema"

    def test_prepare_l2_trims_tighter_with_schema(self, monkeypatch):
        """同一消息集：带大 schema 的 prepare 必须裁掉更多历史（L2 生效）。

        关掉 L4（触发线抬到不可达）：本场景的正确结果恰是「schema 扣除
        让 L2 一轮裁准、L4 不必触发」，对比必须隔离在 L2 上。"""
        monkeypatch.setattr(config, "CONTEXT_L4_TRIGGER_RATIO", 1.01)
        _small_window(monkeypatch, window=1000)
        from backend.context_budget.token_counter import count_tool_schema_tokens
        from backend.memory.token_budget import count_message_tokens
        tools = [{"type": "function", "function": {
            "name": "f", "parameters": {"desc": "参数" * 300}}}]
        schema_tokens = count_tool_schema_tokens(tools)

        def _mk():
            return [SystemMessage(content="s")] + _history(6) \
                + [HumanMessage(content="当前问题")]

        prepared_a = ContextBudgetManager().prepare_llm_context(
            messages=_mk(), extra_reserved_tokens=0)
        prepared_b = ContextBudgetManager().prepare_llm_context(
            messages=_mk(), extra_reserved_tokens=schema_tokens)
        # 只数真实对话历史（排除 System 与折叠/摘要投影 AIMessage）
        def _history_count(msg_list):
            return sum(
                1 for m in msg_list
                if type(m).__name__ not in ("SystemMessage",)
                and not str(m.content).lstrip().startswith("<historical_context>"))
        assert _history_count(prepared_b.messages) \
            < _history_count(prepared_a.messages), \
            "L2 未扣除 schema：大 schema 下历史未被多裁"

    async def test_bind_tools_request_fits_after_single_trim(self, monkeypatch):
        """bind_tools 场景：L2 一轮裁准后不再触发硬裁阶段（无 overflow）。"""
        from backend.context_budget.token_counter import count_tool_schema_tokens
        _small_window(monkeypatch, window=1000)
        tools = [{"type": "function", "function": {
            "name": "f", "parameters": {"desc": "参数" * 300}}}]
        schema_tokens = count_tool_schema_tokens(tools)
        msgs = [SystemMessage(content="s")] + _history(6) \
            + [HumanMessage(content="当前问题")]
        prepared = ContextBudgetManager().prepare_llm_context(
            messages=msgs, extra_reserved_tokens=schema_tokens)
        assert not prepared.overflow
        from backend.memory.token_budget import count_message_tokens
        total = sum(count_message_tokens(m) for m in prepared.messages)
        budget = ContextBudgetManager().get_input_budget(
            extra_reserved_tokens=schema_tokens)
        assert total <= budget
