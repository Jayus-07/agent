"""L4 Context Collapse 测试（Phase 2，2026-09-22）

覆盖规格 §二十六 的 9 项 + 投影内容 + 恢复语义。
"""
import pytest

import backend.config as config
from backend.context_budget.collapse import (
    ContextFold,
    FoldRegistry,
    build_projection_text,
    fold_messages,
)
from backend.context_budget.manager import ContextBudgetManager
from langchain_core.messages import HumanMessage, SystemMessage


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L4_TRIGGER_RATIO", 0.80)
    monkeypatch.setattr(config, "CONTEXT_L4_KEEP_RECENT_TURNS", 4)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)


def _history(turns: int, msg_len: int = 300):
    """构造 turns 轮历史（user+assistant 交替）。"""
    msgs = []
    for i in range(turns):
        msgs.append(HumanMessage(content=f"用户第{i}轮提问：" + "问" * msg_len))
        msgs.append(_assistant(f"助手第{i}轮回答：" + "答" * msg_len))
    return msgs


def _assistant(text: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content=text)


class TestFoldMessages:
    def test_below_keep_turns_no_fold(self):
        msgs = _history(3)  # 轮数 < keep_recent_turns(4)
        folded, fold = fold_messages(msgs)
        assert fold is None
        assert folded == msgs

    def test_reaches_threshold_folds_old_turns(self):
        msgs = [SystemMessage(content="系统提示")]
        msgs += _history(10)
        folded, fold = fold_messages(msgs)
        assert fold is not None
        assert fold.message_count == 2 * (10 - 4)  # 6 轮被折叠
        # after_tokens < before_tokens
        assert fold.projected_tokens < fold.original_tokens
        # System 保留在最前
        assert type(folded[0]).__name__ == "SystemMessage"
        # 最近 4 轮（8 条）+ 1 条 projection + 1 条 system = 10
        assert len(folded) == len(msgs) - fold.message_count + 1

    def test_recent_turns_kept_verbatim(self):
        msgs = _history(8)
        folded, fold = fold_messages(msgs)
        assert fold is not None
        tail = folded[-8:]
        assert [m.content for m in tail] == [m.content for m in msgs[-8:]]

    def test_current_last_message_never_folded(self):
        msgs = _history(6)
        msgs.append(HumanMessage(content="当前问题（必须保留）"))
        folded, _ = fold_messages(msgs)
        assert folded[-1].content == "当前问题（必须保留）"

    def test_projection_is_deterministic_no_summary(self):
        fold = ContextFold(
            fold_id="fold-x", from_index=0, to_index=7, message_count=8,
            original_tokens=1000, projected_tokens=60)
        text = build_projection_text(fold)
        assert "8 earlier messages were omitted" in text
        assert "1 - 8" in text
        assert "remain available in session history" in text
        # 重复生成结果一致（确定性）
        assert build_projection_text(fold) == text

    def test_no_fold_when_no_gain(self):
        # 候选极少太小：折叠无收益 → 不折
        msgs = _history(6, msg_len=2)
        folded, fold = fold_messages(msgs)
        assert fold is None
        assert folded == msgs


class TestFoldRegistry:
    def test_register_and_restore(self):
        reg = FoldRegistry()
        fold = ContextFold(fold_id="f1", from_index=0, to_index=3,
                           message_count=4, original_tokens=100,
                           projected_tokens=40)
        reg.register(fold)
        assert reg.get("f1") is fold
        assert len(reg.active_folds) == 1
        assert reg.restore_fold("f1") is True
        assert reg.restore_fold("missing") is False
        assert len(reg.active_folds) == 0  # 恢复后不再算活跃折叠
        # 台账仍在（审计），且可序列化
        assert reg.to_state()[0]["fold_id"] == "f1"
        assert reg.to_state()[0]["reversible"] is True


class TestL4InPreflight:
    def _long_context(self):
        msgs = [SystemMessage(content="系统提示必须保留")]
        msgs += _history(12)
        msgs.append(HumanMessage(content="当前问题"))
        po = {"1": "前置输出" * 400}
        # L2 会先把历史压到 ≤2048；要让总量越过 80%×7168≈5734，rag 需 ~3000 token
        rag = ["证据。" * 300] * 10
        return msgs, po, rag

    def test_no_collapse_below_threshold(self):
        m = ContextBudgetManager()
        msgs = [SystemMessage(content="s"), HumanMessage(content="短问题")]
        prepared = m.prepare_llm_context(messages=msgs)
        assert prepared.folds == []

    def test_collapse_triggers_above_threshold(self):
        m = ContextBudgetManager()
        msgs, po, rag = self._long_context()
        prepared = m.prepare_llm_context(messages=msgs, previous_outputs=po,
                                         rag_context=rag)
        # 12 轮 + 大 po/rag → 远超 80% → 触发 L4
        assert len(prepared.folds) == 1
        fold = prepared.folds[0]
        assert fold["reversible"] is True
        assert fold["message_count"] >= 2 * (12 - 4)
        assert fold["projected_tokens"] < fold["original_tokens"]
        assert prepared.usage.used_tokens <= prepared.usage.input_budget

    def test_original_message_list_not_mutated(self):
        m = ContextBudgetManager()
        msgs, po, rag = self._long_context()
        n = len(msgs)
        m.prepare_llm_context(messages=msgs, previous_outputs=po,
                              rag_context=rag)
        assert len(msgs) == n

    def test_context_collapse_api_functional(self):
        m = ContextBudgetManager()

        async def _run():
            return await m.context_collapse(_history(8))

        import asyncio
        folded, fold = asyncio.run(_run())
        assert fold is not None
        assert len(folded) < len(_history(8))

    def test_auto_compact_still_not_implemented(self):
        m = ContextBudgetManager()
        import asyncio
        try:
            asyncio.run(m.auto_compact())
            raised = False
        except NotImplementedError:
            raised = True
        assert raised is True
