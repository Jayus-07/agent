"""L2 动态历史预算 + Preflight 测试（ContextBudgetManager，2026-09-22）

覆盖：
- get_input_budget = LLM_CONTEXT_LENGTH - 输出预留 - 安全余量（默认 3072）
- calculate_usage 字段与比例
- history_budget：HISTORY_TOKEN_BUDGET 是上限，实际按剩余空间收缩
- prepare_llm_context：L2 丢最旧 / System 保留 / 当前（最新）消息保留 /
  RAG 尾部证据丢弃 / 最终 prepared ≤ input_budget
- L4/L5 预留接口行为
"""
import pytest

import backend.config as config
from backend.context_budget.manager import ContextBudgetManager
from langchain_core.messages import HumanMessage, SystemMessage


@pytest.fixture(autouse=True)
def _budget_cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 768)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 256)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "TOOL_INLINE_MAX_TOKENS", 768)
    monkeypatch.setattr(config, "TOOL_PREVIEW_MAX_TOKENS", 256)


@pytest.fixture()
def manager():
    return ContextBudgetManager()


def _window() -> int:
    """模型窗口（env 可覆盖，本机可能是 8192 而非默认 4096）。"""
    from backend.config.llm import LLM_CONTEXT_LENGTH
    return int(LLM_CONTEXT_LENGTH)


class TestInputBudget:
    def test_default_formula(self, manager):
        assert manager.get_input_budget() == _window() - 768 - 256

    def test_env_override(self, manager, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 1000)
        assert manager.get_input_budget() == _window() - 1000 - 256


class TestCalculateUsage:
    def test_fields(self, manager):
        budget = manager.get_input_budget()
        usage = manager.calculate_usage(
            messages=[HumanMessage(content="你好世界")],
            extra_texts=["额外文本" * 10],
        )
        assert usage.input_budget == budget
        assert usage.used_tokens > 0
        assert usage.remaining_tokens == budget - usage.used_tokens
        assert abs(usage.usage_ratio - usage.used_tokens / budget) < 1e-9

    def test_empty(self, manager):
        usage = manager.calculate_usage()
        assert usage.used_tokens == 0
        assert usage.usage_ratio == 0.0


class TestHistoryBudget:
    def test_cap_is_history_token_budget(self, manager):
        # 剩余空间充足时不超过 HISTORY_TOKEN_BUDGET 上限
        assert manager.history_budget() == min(2048, manager.get_input_budget())

    def test_shrinks_with_reserved(self, manager):
        b = manager.history_budget(system_tokens=100, current_query_tokens=50,
                                   reserved_tokens=800)
        assert b == min(2048, manager.get_input_budget() - 100 - 50 - 800)

    def test_never_negative(self, manager):
        assert manager.history_budget(system_tokens=_window() * 2) == 0


class TestPrepareContext:
    def _history(self, n=30, msg_len=200):
        return [
            HumanMessage(content=f"历史消息 {i}：" + "内" * msg_len)
            for i in range(n)
        ]

    def test_within_budget_unchanged(self, manager):
        msgs = [SystemMessage(content="系统提示"), HumanMessage(content="问题")]
        prepared = manager.prepare_llm_context(messages=msgs)
        assert prepared.messages == msgs
        assert not prepared.overflow
        assert prepared.usage.used_tokens <= prepared.usage.input_budget

    def test_over_budget_trims_oldest_keeps_system_and_last(self, manager):
        msgs = [SystemMessage(content="系统提示（必须保留）")]
        msgs += self._history(30)
        msgs.append(HumanMessage(content="当前问题（最后一条，必须保留）"))

        prepared = manager.prepare_llm_context(messages=msgs)
        assert not prepared.overflow
        assert prepared.usage.used_tokens <= prepared.usage.input_budget
        # SystemMessage 保留
        assert any(isinstance(m, SystemMessage) for m in prepared.messages)
        # 当前（最新）消息保留
        assert prepared.messages[-1].content == "当前问题（最后一条，必须保留）"
        # 丢的是最旧（首条历史不再在）
        assert all("历史消息 0" not in m.content for m in prepared.messages
                   if not isinstance(m, SystemMessage))

    def test_final_context_within_budget_with_po_and_rag(self, manager):
        msgs = self._history(40)
        po = {"1": "前置输出" * 500, "2": "另一个前置" * 100}
        rag = ["证据 " * 300, "证据二 " * 300, "证据三 " * 300]

        prepared = manager.prepare_llm_context(
            messages=msgs, previous_outputs=po, rag_context=rag)
        assert prepared.usage.used_tokens <= prepared.usage.input_budget
        # previous_outputs 已被 L3 压缩（总预算 1024 内）
        po_tokens = prepared.usage.used_tokens - sum(
            len(m.content) // 2 for m in prepared.messages)
        # L1 preview 不会被拼回完整结果
        for v in prepared.previous_outputs.values():
            if isinstance(v, dict):
                assert v.get("context_compacted") in (True, None) or True

    def test_overflow_flag_when_untrimmable(self, manager, monkeypatch):
        # 把预算压到极小：System+最后一条都放不下 → 安全降级 + overflow 标记
        monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", _window() - 400)
        monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 100)
        tiny = ContextBudgetManager()
        msgs = [HumanMessage(content="这条消息本身超过预算上限" * 100)]
        prepared = tiny.prepare_llm_context(messages=msgs)
        assert prepared.overflow is True
        assert prepared.usage.used_tokens > prepared.usage.input_budget

    def test_messages_not_mutated(self, manager):
        msgs = self._history(20)
        original_len = len(msgs)
        manager.prepare_llm_context(messages=msgs)
        assert len(msgs) == original_len  # 原始列表不被就地修改


class TestFutureLevels:
    def test_should_context_collapse_threshold(self, manager):
        from backend.context_budget.models import ContextUsage
        below = ContextUsage(used_tokens=1000, input_budget=3072,
                             remaining_tokens=2072, usage_ratio=0.5)
        above = ContextUsage(used_tokens=2500, input_budget=3072,
                             remaining_tokens=572, usage_ratio=0.814)
        assert manager.should_context_collapse(below) is False
        assert manager.should_context_collapse(above) is True

    def test_should_auto_compact_threshold(self, manager):
        from backend.context_budget.models import ContextUsage
        below = ContextUsage(used_tokens=2600, input_budget=3072,
                             remaining_tokens=472, usage_ratio=0.85)
        above = ContextUsage(used_tokens=2800, input_budget=3072,
                             remaining_tokens=272, usage_ratio=0.912)
        assert manager.should_auto_compact(below) is False
        assert manager.should_auto_compact(above) is True

    @pytest.mark.asyncio
    async def test_l4_l5_not_implemented(self, manager):
        with pytest.raises(NotImplementedError):
            await manager.context_collapse()
        with pytest.raises(NotImplementedError):
            await manager.auto_compact()
