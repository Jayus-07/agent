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
        assert result.answer.startswith(REFUSAL_MESSAGES["no_evidence"])
        assert "换个说法" in result.answer  # 自救阶梯：无候选给换问法引导
        assert "人工" not in result.answer  # V5：拒答不推人工

    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_low_confidence_returns_refuse(self, mock_get):
        svc, mock_pipeline = self._make_service_with_mock_pipeline(
            "不确定。",
            {"confidence": 0.3, "can_answer": True},
        )
        mock_get.return_value = mock_pipeline

        result = svc.answer("问题", kb_ids=["cs_faq"])

        assert result.decision == Decision.REFUSE
        assert result.answer.startswith(REFUSAL_MESSAGES["low_confidence"])
        assert "换个说法" in result.answer

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

    @patch("backend.customer_service.faq.get_faq_store")
    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_multiple_kb_ids_propagated(self, mock_get, mock_faq):
        # C4 FAQ 双轨后本测试必须 mock FAQ 边界：真实库中「退货流程」会被
        # 「换货流程」(0.5) 近似命中直返，pipeline 断言永远走不到
        class _NoFaq:
            def match(self, q):
                return None
        mock_faq.return_value = _NoFaq()
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
        assert "人工" not in result.answer  # V5：META 缺失拒答走自救阶梯，不推人工

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
        assert result.answer.startswith(REFUSAL_MESSAGES["no_evidence"])
        assert "换个说法" in result.answer  # 自救阶梯：无候选给换问法引导
        assert "人工" not in result.answer  # V5：拒答不推人工

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


class TestRefusalLadder:
    """T2 拒答自救阶梯（V4/V5）：候选/无候选/FAQ 故障三分支。"""

    def _refuse_service(self, mock_get):
        mock_pipeline = MagicMock()
        mock_pipeline.ask_result.return_value = MagicMock(
            answer="", answer_meta={"confidence": 0.3, "can_answer": False})
        mock_get.return_value = mock_pipeline
        return CSKnowledgeService()

    @patch("backend.customer_service.faq.get_faq_store")
    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_refuse_with_candidates_recommends(self, mock_get, mock_faq):
        from backend.customer_service.faq import FAQMatch

        class FakeStore:
            def match_candidates(self, q, k=2, min_score=0.30):
                return [FAQMatch(87, "退款多久能到账", "5-7 工作日", 0.38, "jaccard")]

        mock_faq.return_value = FakeStore()
        result = self._refuse_service(mock_get).answer("退款拖了好久都没到", kb_ids=["cs_faq"])
        assert result.decision == Decision.REFUSE
        assert "您是不是想问" in result.answer
        assert "退款多久能到账" in result.answer
        assert "人工" not in result.answer  # V5：不主动推人工

    @patch("backend.customer_service.faq.get_faq_store")
    @patch("backend.rag.pipeline._get_local_pipeline")
    def test_refuse_faq_failure_falls_back_to_guide(self, mock_get, mock_faq):
        mock_faq.side_effect = RuntimeError("FAQ 层挂了")
        result = self._refuse_service(mock_get).answer("奇怪的问题xyz", kb_ids=["cs_faq"])
        assert result.decision == Decision.REFUSE
        assert "换个说法" in result.answer  # 阶梯旁路降级为引导话术，不炸
        assert "人工" not in result.answer
