"""test_knowledge_service.py — 客服知识问答服务测试"""
from unittest.mock import MagicMock, patch

from backend.customer_service.knowledge.answer_decision import (
    CAUTIOUS_SUFFIX,
    REFUSAL_MESSAGES,
    CSAnswerDecision,
    Decision,
)
from backend.customer_service.knowledge.service import (
    CSKnowledgeService,
)


class TestAnswerDecision:
    def test_high_confidence_with_evidence_is_answer(self):
        d = CSAnswerDecision.decide(confidence=0.9, has_evidence=True)
        assert d.decision == Decision.ANSWER
        assert d.suffix == ""

    def test_at_threshold_is_answer(self):
        d = CSAnswerDecision.decide(confidence=0.85, has_evidence=True)
        assert d.decision == Decision.ANSWER

    def test_mid_confidence_is_cautious(self):
        d = CSAnswerDecision.decide(confidence=0.7, has_evidence=True)
        assert d.decision == Decision.CAUTIOUS
        assert d.suffix == CAUTIOUS_SUFFIX

    def test_at_cautious_threshold_is_cautious(self):
        d = CSAnswerDecision.decide(confidence=0.60, has_evidence=True)
        assert d.decision == Decision.CAUTIOUS

    def test_low_confidence_is_refuse(self):
        d = CSAnswerDecision.decide(confidence=0.3, has_evidence=True)
        assert d.decision == Decision.REFUSE
        assert d.suffix == REFUSAL_MESSAGES["low_confidence"]

    def test_no_evidence_is_refuse(self):
        d = CSAnswerDecision.decide(confidence=0.95, has_evidence=False)
        assert d.decision == Decision.REFUSE
        assert d.suffix == REFUSAL_MESSAGES["no_evidence"]

    def test_zero_confidence_no_evidence(self):
        d = CSAnswerDecision.decide(confidence=0.0, has_evidence=False)
        assert d.decision == Decision.REFUSE


class TestCSKnowledgeService:
    def _make_service_with_mock_pipeline(self, answer, meta=None):
        """构造 CSKnowledgeService，mock pipeline.ask_result()（D1-6 请求级契约）。"""
        from backend.rag.pipeline import AskOutcome

        svc = CSKnowledgeService()
        mock_pipeline = MagicMock()
        effective_meta = meta or {"confidence": 0.9, "can_answer": True}
        mock_pipeline.ask_result.return_value = AskOutcome(
            answer=answer, sources=[], answer_meta=effective_meta)
        mock_pipeline.last_answer_meta = effective_meta
        return svc, mock_pipeline

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_high_confidence_returns_answer(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "退货政策是7天内可退。",
            {"confidence": 0.92, "can_answer": True},
        )
        mock_get.return_value = mock_pipeline

        result = svc.answer("退货政策是什么", kb_ids=["cs_policy"])

        assert result.decision == Decision.ANSWER
        assert result.answer == "退货政策是7天内可退。"
        assert result.confidence == 0.92
        assert result.kb_ids == ["cs_policy"]
        assert result.suffix == ""

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_mid_confidence_returns_cautious_with_suffix(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "可能是这样的。",
            {"confidence": 0.70, "can_answer": True},
        )
        mock_get.return_value = mock_pipeline

        result = svc.answer("怎么处理", kb_ids=["cs_faq"])

        assert result.decision == Decision.CAUTIOUS
        assert result.answer.endswith(CAUTIOUS_SUFFIX)
        assert result.confidence == 0.70

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_no_evidence_returns_refuse(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "",
            {"confidence": 0.3, "can_answer": False},
        )
        mock_get.return_value = mock_pipeline

        result = svc.answer("未知问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE
        assert result.answer == REFUSAL_MESSAGES["no_evidence"]

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_low_confidence_returns_refuse(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "不确定。",
            {"confidence": 0.3, "can_answer": True},
        )
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE
        assert result.answer == REFUSAL_MESSAGES["low_confidence"]

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_default_kb_ids_when_empty(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "回答",
            {"confidence": 0.9, "can_answer": True},
        )
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=None)

        mock_pipeline.ask_result.assert_called_once()
        call_kwargs = mock_pipeline.ask_result.call_args
        assert call_kwargs.kwargs.get("kb_id") == "cs_faq" or call_kwargs[1].get("kb_id") == "cs_faq"
        assert result.kb_ids == ["cs_faq"]

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_pipeline_exception_returns_refuse_with_error(self, mock_get):
        mock_pipeline = MagicMock()
        mock_pipeline.ask_result.side_effect = RuntimeError("pipeline down")
        mock_get.return_value = mock_pipeline

        svc = CSKnowledgeService()
        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE
        assert result.error is not None
        assert "pipeline down" in result.error
        assert "人工客服" in result.answer

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_multiple_kb_ids_propagated(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "综合回答",
            {"confidence": 0.88, "can_answer": True},
        )
        mock_get.return_value = mock_pipeline

        kb_ids = ["cs_policy", "cs_aftersales", "cs_faq"]
        result = svc.answer("退货流程", kb_ids=kb_ids)

        call_kwargs = mock_pipeline.ask_result.call_args
        assert call_kwargs.kwargs.get("kb_ids") == kb_ids or call_kwargs[1].get("kb_ids") == kb_ids
        assert result.kb_ids == kb_ids
        assert result.decision == Decision.ANSWER


# ── B9（2026-09-29 迁移）：META 缺失兜底收紧 REFUSE ──────────────
# 旧兜底 can_answer 默认 True → 0.65 放行 CAUTIOUS，0.85/0.60 两道门禁
# 对 META 遵循度不稳的流量形同虚设；收紧后兜底 = REFUSE + 兜底指标。


class TestMetaFallbackB9:

    def _make(self, answer, meta):
        from backend.rag.pipeline import AskOutcome

        svc = CSKnowledgeService()
        mock_pipeline = MagicMock()
        mock_pipeline.ask_result.return_value = AskOutcome(
            answer=answer, sources=[], answer_meta=meta)
        return svc, mock_pipeline

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_meta_missing_refuses_even_with_answer(self, mock_get):
        """META 完全缺失 = 置信/证据不可信 → REFUSE（不再 0.65 放行）。"""
        svc, mock_pipeline = self._make("看起来像答案", {})
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE
        assert result.confidence == 0.0
        assert "人工" in result.answer

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_meta_none_entirely_refuses(self, mock_get):
        svc, mock_pipeline = self._make("答案", None)
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_confidence_present_can_answer_missing_uses_real_value(self, mock_get):
        """confidence 存在且 can_answer 缺失（默认 True）→ 按真实分值走三档。"""
        svc, mock_pipeline = self._make("正常回答", {"confidence": 0.9})
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.ANSWER
        assert result.confidence == 0.9

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_high_score_without_evidence_still_refuses(self, mock_get):
        """禁无证据生成：can_answer=False 时高分也必须拒答。"""
        svc, mock_pipeline = self._make("编造的答案", {"confidence": 0.95, "can_answer": False})
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE
        assert result.answer == REFUSAL_MESSAGES["no_evidence"]

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_meta_missing_increments_fallback_metric(self, mock_get):
        """兜底必须可观测（设计方案 §4.3 兜底值监控）。"""
        from backend.observability import metrics as obs_metrics

        svc, mock_pipeline = self._make("答案", {})
        mock_get.return_value = mock_pipeline

        before = obs_metrics.cs_knowledge_meta_fallback_total._value.get()
        svc.answer("问题", kb_ids=["cs_faq"])
        after = obs_metrics.cs_knowledge_meta_fallback_total._value.get()

        assert after == before + 1
