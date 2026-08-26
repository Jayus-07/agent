# -*- coding: utf-8 -*-
"""Faithfulness scorer 回归测试。

fix f18：LLM-Judge 路径 score 必须用 verdict.score（判官整体支撑度），
不得用 supported/len(nli_results) —— 后者在 nli_results 仅由
unsupported_claims 构成时恒为 0，判官给 0.95 也会算成 0.00
触发 hallucination 误拒答（MiniMax 切换后实测暴露）。
"""
from types import SimpleNamespace

import pytest

from backend.rag.guardrails import scorer
from backend.rag.guardrails.nli_llm import LLMVerdict


def _patch_pipeline(monkeypatch, verdict):
    monkeypatch.setattr(scorer, "extract_claims",
                        lambda a: ["claim1", "claim2", "claim3"])
    monkeypatch.setattr(scorer, "filter_claims", lambda c: (c, []))
    monkeypatch.setattr(scorer, "evaluate_with_llm",
                        lambda answer, docs: verdict)


class TestLlmJudgeScore:
    def test_partial_unsupported_keeps_judge_score(self, monkeypatch):
        """判官 score=0.95 且列 1 条 unsupported → 最终 score=0.95（非 0.00）。"""
        _patch_pipeline(monkeypatch, LLMVerdict(
            score=0.95, reason="基本支撑", unsupported_claims=["claim2"],
        ))
        result = scorer.check_faithfulness(
            "回答", [SimpleNamespace(page_content="文档")], enabled=True)
        assert result.score == 0.95
        assert result.unsupported_claims == 1

    def test_full_support_scores_one(self, monkeypatch):
        """无 unsupported → score 取 verdict.score=1.0。"""
        _patch_pipeline(monkeypatch, LLMVerdict(
            score=1.0, reason="完全支撑", unsupported_claims=[],
        ))
        result = scorer.check_faithfulness(
            "回答", [SimpleNamespace(page_content="文档")], enabled=True)
        assert result.score == 1.0
        assert result.unsupported_claims == 0

    def test_low_judge_score_still_rejects(self, monkeypatch):
        """判官真实低分（0.2）不被 f18 修复掩盖 —— 仍按原分拒答。"""
        _patch_pipeline(monkeypatch, LLMVerdict(
            score=0.2, reason="大量编造",
            unsupported_claims=["claim1", "claim2", "claim3"],
        ))
        result = scorer.check_faithfulness(
            "回答", [SimpleNamespace(page_content="文档")], enabled=True)
        assert result.score == 0.2
        assert result.unsupported_claims == 3
