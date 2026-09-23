"""STOP D（P2）加固测试 — 2026-09-23

覆盖：
  I.  RAGBudgeter：score 优先 / source 多样性 / 无分数回退 / 小预算保底
  P2-2 predicted usage：L4/L5 按 predicted ratio 触发
  P2-2 hysteresis：L4 折叠循环收紧 keep 轮数；L5 重建只提交成功档
  P2-3 ProtectedFactRegistry：业务事实 critical 优先 / 去重 / 摘要补丁 /
      run_incremental_summary 的 extra_facts 钩子
  §十九 projection 溯源：fold_id / through_message_id 入 additional_kwargs
  P2-4 用量分解 Gauge
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import backend.config as config
import backend.context_budget.auto_compact as ac_mod
import backend.context_budget.collapse as collapse_mod
from backend.context_budget.auto_compact import fold_rebuild, run_incremental_summary
from backend.context_budget.collapse import fold_messages
from backend.context_budget.fact_registry import (
    ProtectedFactEntry,
    ProtectedFactRegistry,
    SOURCE_BUSINESS,
)
from backend.context_budget.manager import ContextBudgetManager
from backend.context_budget.rag_budgeter import budget_rag_texts
from tests.context_budget.test_auto_compact import FakeStore


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_L4_TRIGGER_RATIO", 0.80)
    monkeypatch.setattr(config, "CONTEXT_L4_TARGET_RATIO", 0.65)
    monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.90)
    monkeypatch.setattr(config, "CONTEXT_L5_TARGET_RATIO", 0.70)
    monkeypatch.setattr(ac_mod, "_acquire_l5_lock", lambda sid: None)


# ---------------------------------------------------------------------------
# I. RAGBudgeter
# ---------------------------------------------------------------------------


class TestRAGBudgeter:
    def test_score_priority_beats_position(self):
        """最高分 chunk 在列表尾部也保留（机械尾删会把它丢掉）。"""
        texts = ["普通证据一" * 30, "普通证据二" * 30, "核心证据" * 30]
        scores = [0.9, 0.8, 0.99]
        kept, dropped = budget_rag_texts(texts, 250, scores=scores)
        assert any("核心证据" in t for t in kept)
        assert dropped >= 1

    def test_no_scores_falls_back_order_preserving(self):
        texts = ["证据A" * 50, "证据B" * 50, "证据C" * 50]
        kept, _ = budget_rag_texts(texts, 400)
        assert kept[0] == texts[0]  # 原序从头保留（旧行为）

    def test_mismatched_scores_falls_back(self):
        texts = ["A" * 100, "B" * 100]
        kept, _ = budget_rag_texts(texts, 100, scores=[0.9])
        assert kept[0] == texts[0]

    def test_tiny_budget_keeps_best_chunk(self):
        texts = ["长证据" * 500, "短但关键"]
        kept, _ = budget_rag_texts(texts, 10, scores=[0.99, 0.9])
        assert kept == ["短但关键"]

    def test_source_diversity_round_robin(self):
        """同 source 高分 chunk 有上限：其他 source 仍能进入保留集。"""
        src_a = ["来源A文档" * 20] * 5        # 全部高分
        src_b = ["来源B文档" * 20]            # 低分
        texts = src_a + src_b
        scores = [0.99] * 5 + [0.5]
        kept, _ = budget_rag_texts(
            texts, 2000, scores=scores,
            sources=["a", "a", "a", "a", "a", "b"])
        assert any("来源B" in t for t in kept)  # B 没被 A 挤光


# ---------------------------------------------------------------------------
# P2-2 predicted usage + hysteresis
# ---------------------------------------------------------------------------


class TestPredictedUsageAndHysteresis:
    def _history(self, turns, msg_len=200):
        msgs = []
        for i in range(turns):
            msgs.append(HumanMessage(content=f"问{i}" + "题" * msg_len))
            msgs.append(AIMessage(content=f"答{i}" + "案" * msg_len))
        return msgs

    def test_predicted_usage_triggers_l4_early(self, monkeypatch):
        """当前 60%，但预测注入 +50% → 越过 0.80 触发线。"""
        monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", 4096)
        from backend.context_budget import token_counter as tc
        monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
        m = ContextBudgetManager()
        # ~50% 用量的历史
        msgs = self._history(5, msg_len=260)
        u = m.calculate_usage(messages=msgs)
        assert 0.3 < u.usage_ratio < 0.7  # 前提：当前未触发
        folds = []

        def _fake_fold(messages, *, keep_recent_turns=None):
            folds.append(keep_recent_turns)
            from backend.context_budget.collapse import ContextFold, build_projection_text
            from backend.context_budget.role_safety import build_historical_context
            fold = ContextFold(
                fold_id="f", from_index=0, to_index=1, message_count=2,
                original_tokens=999, projected_tokens=10)
            return [SystemMessage(content="s"),
                    *build_historical_context(
                        build_projection_text(fold))], fold

        monkeypatch.setattr(collapse_mod, "fold_messages", _fake_fold)
        prepared = m.prepare_llm_context(
            messages=[SystemMessage(content="s")] + msgs,
            predicted_extra_tokens=int(u.input_budget * 0.5))
        assert folds  # predicted 推高 ratio → L4 触发
        assert prepared.folds

    def test_no_predicted_no_trigger(self, monkeypatch):
        monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", 4096)
        from backend.context_budget import token_counter as tc
        monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
        m = ContextBudgetManager()
        msgs = self._history(3, msg_len=260)
        u = m.calculate_usage(messages=msgs)
        assert u.usage_ratio < 0.5
        calls = []
        monkeypatch.setattr(
            collapse_mod, "fold_messages",
            lambda messages, *, keep_recent_turns=None:
            calls.append(1) or (messages, None))
        m.prepare_llm_context(messages=[SystemMessage(content="s")] + msgs)
        assert calls == []

    def test_l4_hysteresis_tightens_keep_turns(self, monkeypatch):
        """折叠后仍高于 target → keep 轮数递减（4→3→…→1），只压到安全区。"""
        monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", 8192)
        monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 10 ** 9)
        from backend.context_budget import token_counter as tc
        monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)

        keeps = []

        def _fake_fold(messages, *, keep_recent_turns=None):
            keeps.append(keep_recent_turns)
            from backend.context_budget.collapse import ContextFold
            fold = ContextFold(
                fold_id=f"f{len(keeps)}", from_index=0,
                to_index=max(1, len(messages) - 3), message_count=2,
                original_tokens=5000, projected_tokens=100)
            # 模拟"每折一次省 200 token"——永远压不到 target，逼循环收紧
            folded = list(messages)
            return folded, fold

        monkeypatch.setattr(collapse_mod, "fold_messages", _fake_fold)
        m = ContextBudgetManager()
        big = self._history(30, msg_len=400)  # 远超预算
        m.prepare_llm_context(messages=[SystemMessage(content="s")] + big)
        # keep 从 4 递减到 1 后停止
        assert keeps == [4, 3, 2, 1]

    def test_l5_rebuild_only_commits_successful_deeper_tier(
            self, monkeypatch):
        """真实链路：重建后仍高于 target → 逐档试探更小 keep（4→3→2→1），
        只提交成功档；水位线/摘要不落库（run_incremental_summary 被 mock）。"""
        monkeypatch.setattr(
            "backend.core.request_context.get_current_session_id",
            lambda: "sess-l5-hyst")

        real_rebuild = ac_mod.fold_rebuild
        calls = []

        def _recording_rebuild(messages, summary, *, keep_recent_turns=None,
                               projection_meta=None):
            calls.append(keep_recent_turns)
            return real_rebuild(messages, summary,
                                keep_recent_turns=keep_recent_turns,
                                projection_meta=projection_meta)

        monkeypatch.setattr(ac_mod, "fold_rebuild", _recording_rebuild)
        monkeypatch.setattr(
            ac_mod, "run_incremental_summary",
            lambda sid, store: ac_mod.SummaryOutcome(
                summary="这是一段足够长的摘要内容，用于占位token",
                through_id=99, token_count=20, delta_message_count=6,
                protected_fact_count=0, patched_fact_count=0))

        # 极低 target 强迫循环逐档下探
        monkeypatch.setattr(config, "CONTEXT_L5_TARGET_RATIO", 0.05)
        m = ContextBudgetManager()
        msgs = [SystemMessage(content="s")] + self._history(12, msg_len=300)
        used = m.calculate_usage(messages=msgs).used_tokens
        budget = used * 10 // 9  # ratio ≈ 0.9 ≥ 0.90 触发线
        msgs2, used2 = m._maybe_auto_compact(msgs, used, budget)
        # target=0.05 几乎达不到 → keep 从 4 一路下探到 1
        assert calls == [4, 3, 2, 1]
        humans = [x for x in msgs2 if type(x).__name__ == "HumanMessage"]
        assert len(humans) == 1  # 只保留最近 1 轮
        assert used2 < used


# ---------------------------------------------------------------------------
# P2-3 ProtectedFactRegistry
# ---------------------------------------------------------------------------


class TestProtectedFactRegistry:
    def test_critical_first_and_dedup(self):
        reg = ProtectedFactRegistry()
        assert reg.add_business_fact("金额", "100,000",
                                     semantic_role="预算",
                                     source=SOURCE_BUSINESS)
        assert not reg.add_business_fact("金额", "100,000")  # 去重
        reg.add_regex_facts([ProtectedFactEntry(
            type="订单号", value="ORD20260923001")])
        text = reg.to_prompt_text()
        lines = text.splitlines()
        assert lines[0].startswith("- 金额（预算）: 100,000")  # critical 优先
        assert any("ORD20260923001" in ln for ln in lines)
        assert len(reg) == 2

    def test_validate_and_patch_appends_missing(self):
        reg = ProtectedFactRegistry()
        reg.add_regex_facts([
            ProtectedFactEntry(type="订单号", value="ORD-1"),
            ProtectedFactEntry(type="金额", value="¥5000"),
        ])
        out = reg.validate_and_patch("摘要里只提到 ¥5000")
        assert out.missing and out.missing[0].value == "ORD-1"
        assert "[关键实体]" in out.patched_summary
        assert "ORD-1" in out.patched_summary

    def test_extra_facts_hook_in_summary(self, monkeypatch):
        """run_incremental_summary(extra_facts=...)：业务态事实受同样保护。"""
        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                            lambda p: type("R", (), {
                                "content": "泛泛而谈的摘要，没有数字",
                                "response_metadata": {}})())
        rows = [(31, "user", "帮我看看推广计划"),
                (32, "assistant", "好的")]
        store = FakeStore(through_id=30, boundary_id=40, rows=rows)
        outcome = run_incremental_summary(
            "s-reg", store,
            extra_facts=[("金额", "100,000")])
        assert outcome is not None
        assert outcome.patched_fact_count >= 1
        assert "100,000" in outcome.summary  # 遗漏被确定性补丁追加


# ---------------------------------------------------------------------------
# §十九 projection 溯源 + P2-4 Gauge
# ---------------------------------------------------------------------------


class TestTraceabilityAndMetrics:
    def test_l4_projection_carries_fold_meta(self):
        msgs = [SystemMessage(content="s")]
        for i in range(8):
            msgs.append(HumanMessage(content=f"问{i}" + "题" * 200))
            msgs.append(AIMessage(content=f"答{i}" + "案" * 200))
        folded, fold = fold_messages(msgs)
        assert fold is not None
        proj = next(m for m in folded
                    if getattr(m, "content", "").startswith("<historical_context>"))
        meta = proj.additional_kwargs.get("context_projection")
        assert meta and meta["fold_id"] == fold.fold_id
        assert meta["source_range"] == [fold.from_index, fold.to_index]
        assert meta["kind"] == "l4_fold"

    def test_l5_projection_carries_watermark_meta(self):
        msgs = [SystemMessage(content="s")]
        for i in range(8):
            msgs.append(HumanMessage(content=f"问{i}"))
            msgs.append(AIMessage(content=f"答{i}"))
        rebuilt, replaced, _ = fold_rebuild(
            msgs, "摘要内容", projection_meta={"through_message_id": 77})
        assert replaced > 0
        proj = next(m for m in rebuilt
                    if getattr(m, "content", "").startswith("<historical_context>"))
        meta = proj.additional_kwargs.get("context_projection")
        assert meta and meta["through_message_id"] == 77
        assert meta["kind"] == "l5_summary"

    def test_usage_components_gauge(self, monkeypatch):
        from backend.observability.metrics import context_tokens_by_component
        monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", 4096)
        from backend.context_budget import token_counter as tc
        monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
        m = ContextBudgetManager()
        m.prepare_llm_context(
            messages=[SystemMessage(content="系统"),
                      HumanMessage(content="问题")],
            rag_context=["证据" * 50])
        assert context_tokens_by_component.labels(
            component="system")._value.get() > 0
        assert context_tokens_by_component.labels(
            component="rag")._value.get() > 0
