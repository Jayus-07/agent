"""生产收口 B2 — RAG hook 接线测试（2026-09-23）

覆盖（任务 §七 验收口径）：
  - score priority + source diversity：同 source 高分簇不得垄断预算
  - kept 下标保序（引用标注序不被打乱），且可为非前缀子集
  - 无 score / 口径不齐 → 原序前缀裁剪（与旧行为逐字节一致）
  - 预算极小 → 至少保住分数最高一条（承接「首条文档保底」旧语义）
  - chain.py 接线契约：rerank_score/source_file 的 metadata 键名与
    Document 映射方式（kept 非前缀必须按下标回映）
"""
import pytest

from backend.context_budget.rag_budgeter import (
    _MAX_CHUNKS_PER_SOURCE,
    budget_rag_indices,
    budget_rag_texts,
)
from backend.memory.token_budget import trim_texts_to_budget


def _mk(text: str, tokens_floor: int = 1) -> str:
    """构造可估算 token 的文本（中文按 calibrated 计数，避免依赖精确值）。"""
    return text if len(text) >= tokens_floor else text * tokens_floor


class TestScorePriorityAndDiversity:
    def test_high_score_cluster_cannot_monopolize_budget(self):
        """规格场景变体：A 簇 4 条高分(0.95/0.93/0.91/0.90) + B(0.89) + C(0.85)。

        预算恰好装 4 条：无 diversity 时 A 的前 4 高分全占；有上限时
        A 被压到 3 条，名额让给 B——「不能因为 A 排名靠前就只留 A」。
        """
        texts = ["文档甲一" * 15, "文档甲二" * 15, "文档甲三" * 15,
                 "文档甲四" * 15, "文档乙一" * 15, "文档丙一" * 15]
        scores = [0.95, 0.93, 0.91, 0.90, 0.89, 0.85]
        sources = ["a.md", "a.md", "a.md", "a.md", "b.md", "c.md"]
        from collections import Counter

        kept_idx, dropped = budget_rag_indices(texts, 198, scores=scores,
                                               sources=sources)
        kept_sources = Counter(sources[i] for i in kept_idx)
        assert len(kept_idx) == 4
        assert max(kept_sources.values()) <= _MAX_CHUNKS_PER_SOURCE
        assert kept_sources.get("a.md", 0) == 3, "A 簇必须被上限压到 3 条"
        assert "b.md" in kept_sources, "让位后 B 必须获得名额"

    def test_diversity_cap_skips_when_others_fit(self):
        """A 用满上限后，同预算下 B 的条目应被装入（让位语义）。"""
        texts = ["甲乙丙丁" * 15 for _ in range(5)]
        scores = [0.99, 0.98, 0.97, 0.96, 0.50]
        sources = ["a.md", "a.md", "a.md", "a.md", "b.md"]
        kept_idx, _ = budget_rag_indices(texts, 198, scores=scores,
                                         sources=sources)
        assert 4 in kept_idx, "让位后 b.md 应被装入"
        assert sum(1 for i in kept_idx if sources[i] == "a.md") == 3

    def test_kept_indices_preserve_relative_order(self):
        """kept 下标严格升序（引用标注序不打乱）。"""
        texts = [f"内容{i}" * 10 for i in range(5)]
        scores = [0.5, 0.99, 0.1, 0.95, 0.2]
        kept_idx, _ = budget_rag_indices(texts, 10_000, scores=scores,
                                         sources=None)
        assert kept_idx == sorted(kept_idx)

    def test_tiny_budget_keeps_best_score(self):
        """全部装不下 → 至少保住分数最高的一条（不为空证据）。"""
        texts = ["长文本" * 100, "长文本" * 100, "更长文本" * 100]
        scores = [0.8, 0.99, 0.7]
        kept_idx, dropped = budget_rag_indices(texts, 5, scores=scores)
        assert kept_idx == [1]
        assert dropped == 2


class TestNoScoreFallback:
    def test_no_scores_falls_back_to_prefix_trim(self):
        """无 score → 原序前缀裁剪，与 trim_texts_to_budget 结果一致。"""
        texts = ["短" * 30, "短" * 30, "短" * 30]
        budget = 40
        kept_idx, dropped = budget_rag_indices(texts, budget)
        expected, expected_dropped = trim_texts_to_budget(texts, budget)
        assert [texts[i] for i in kept_idx] == expected
        assert dropped == expected_dropped
        # 前缀性质
        assert kept_idx == list(range(len(expected)))

    def test_score_length_mismatch_falls_back(self):
        """口径不一致 → 保守回退原序裁剪（不做错误假设）。"""
        texts = ["甲" * 30, "乙" * 30]
        kept_idx, _ = budget_rag_indices(texts, 35, scores=[0.9])
        assert kept_idx == [0]

    def test_budget_rag_texts_delegates_to_indices(self):
        """budget_rag_texts 与 indices 版本结果一致（向后兼容）。"""
        texts = ["甲" * 30, "乙" * 30, "丙" * 30]
        scores = [0.5, 0.99, 0.95]
        kept_texts, dropped = budget_rag_texts(texts, 70, scores=scores)
        kept_idx, dropped_idx = budget_rag_indices(texts, 70, scores=scores)
        assert kept_texts == [texts[i] for i in kept_idx]
        assert dropped == dropped_idx


class TestChainWiringContract:
    """chain.py 接线契约：metadata 键名 + 非前缀 kept 的 Document 回映。"""

    def test_document_mapping_by_indices(self):
        """rerank_score/source_file 存于 doc.metadata（reranker.py:334 写入）；
        kept 非前缀时必须按下标映射，docs[:len(kept)] 会选错对象。"""
        class _Doc:
            def __init__(self, content, score, source):
                self.page_content = content
                self.metadata = {"rerank_score": score,
                                 "source_file": source}

        docs = [
            _Doc("文档A第一条" * 20, 0.95, "a.md"),
            _Doc("文档A第二条" * 20, 0.93, "a.md"),
            _Doc("文档B唯一条" * 20, 0.89, "b.md"),
        ]
        texts = [d.page_content for d in docs]
        scores = [float(d.metadata["rerank_score"]) for d in docs]
        sources = [str(d.metadata["source_file"]) for d in docs]
        kept_idx, _ = budget_rag_indices(texts, 45, scores=scores,
                                         sources=sources)
        kept_docs = [docs[i] for i in kept_idx]
        # 回映后的对象与 kept 文本一一对应（score/chunk 不错位）
        assert [d.page_content for d in kept_docs] == [
            texts[i] for i in kept_idx]
        assert all(d.metadata["rerank_score"] is not None for d in kept_docs)

    def test_metadata_score_type_guard(self):
        """非数值 score（如 rerank_unreliable 场景缺键）→ 整体回退原序。"""
        class _Doc:
            def __init__(self, content, meta):
                self.page_content = content
                self.metadata = meta

        docs = [_Doc("甲" * 30, {"rerank_score": 0.9}),
                _Doc("乙" * 30, {"rerank_unreliable": True})]
        rag_scores = [(d.metadata or {}).get("rerank_score") for d in docs]
        assert not all(isinstance(s, (int, float)) for s in rag_scores)
        # chain.py 的守卫分支：类型不齐 → 不传 scores → 前缀裁剪
        kept_idx, _ = budget_rag_indices([d.page_content for d in docs], 35)
        assert kept_idx == [0]


class TestPredictedExtraTokensNoWiring:
    """B3 结论的回归锚：生产调用面（proxy preflight）不传 predicted 值，
    保持 0；此断言锁定「manager 侧支持、调用方不虚构」的口径不漂移。"""

    def test_prepare_llm_context_default_predicted_is_zero(self, monkeypatch):
        import backend.config as config
        monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
        monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
        monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
        monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
        monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)

        from langchain_core.messages import HumanMessage

        from backend.context_budget.manager import context_budget

        msgs = [HumanMessage(content="你好" * 50)]
        prepared = context_budget.prepare_llm_context(messages=msgs)
        # 不传 predicted → L4/L5 触发判定只用真实用量；预算内零改动
        assert prepared.messages == msgs
        assert not prepared.overflow
