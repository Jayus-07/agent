"""RAGAS bridge 单元测试 — 验证数据格式转换、返回值结构、失败隔离。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_metric_result(value: float) -> MagicMock:
    """构造 mock MetricResult（含 .value 属性）。"""
    result = MagicMock()
    result.value = value
    return result


def _patch_all_metrics(score_value: float = 0.85):
    """Patch 5 个 metric 类 + llm/embeddings，使 score() 返回固定值。"""
    mock_metric = MagicMock()
    mock_metric.score.return_value = _make_metric_result(score_value)

    patches = {
        "ragas.metrics.collections.ContextRecall": MagicMock(return_value=mock_metric),
        "ragas.metrics.collections.ContextPrecision": MagicMock(return_value=mock_metric),
        "ragas.metrics.collections.Faithfulness": MagicMock(return_value=mock_metric),
        "ragas.metrics.collections.AnswerRelevancy": MagicMock(return_value=mock_metric),
        "ragas.metrics.collections.AnswerCorrectness": MagicMock(return_value=mock_metric),
    }
    return patches, mock_metric


class TestIsRagasEnabled:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.delenv("EVAL_RAGAS", raising=False)
        from backend.evaluation.ragas_bridge import is_ragas_enabled
        assert is_ragas_enabled() is False

    def test_env_var_enabled(self, monkeypatch):
        monkeypatch.setenv("EVAL_RAGAS", "1")
        from backend.evaluation.ragas_bridge import is_ragas_enabled
        assert is_ragas_enabled() is True

    def test_env_var_true(self, monkeypatch):
        monkeypatch.setenv("EVAL_RAGAS", "true")
        from backend.evaluation.ragas_bridge import is_ragas_enabled
        assert is_ragas_enabled() is True


class TestComputeRagasMetrics:
    def test_empty_retrieved_returns_empty(self):
        from backend.evaluation.ragas_bridge import compute_ragas_metrics
        result = compute_ragas_metrics(
            question="test",
            retrieved_texts=[],
            ground_truth_texts=["gt"],
            expected_answer="ans",
            generated_answer="gen",
        )
        assert result == {}

    def test_empty_generated_answer_returns_empty(self):
        from backend.evaluation.ragas_bridge import compute_ragas_metrics
        result = compute_ragas_metrics(
            question="test",
            retrieved_texts=["ctx"],
            ground_truth_texts=["gt"],
            expected_answer="ans",
            generated_answer="",
        )
        assert result == {}

    @patch("backend.evaluation.ragas_bridge._get_ragas_embeddings")
    @patch("backend.evaluation.ragas_bridge._get_ragas_llm")
    def test_returns_ragas_prefixed_keys(self, mock_llm, mock_emb):
        """验证返回值结构：全部 ragas_* 前缀。"""
        patches, _ = _patch_all_metrics(0.85)
        import contextlib

        from backend.evaluation.ragas_bridge import compute_ragas_metrics

        patchers = [patch(k, v) for k, v in patches.items()]
        for p in patchers:
            p.start()

        try:
            result = compute_ragas_metrics(
                question="测试问题",
                retrieved_texts=["检索到的上下文"],
                ground_truth_texts=["标准答案上下文"],
                expected_answer="期望答案",
                generated_answer="生成的答案",
            )
        finally:
            for p in patchers:
                p.stop()

        assert "ragas_context_recall" in result
        assert "ragas_context_precision" in result
        assert "ragas_faithfulness" in result
        assert "ragas_answer_relevancy" in result
        assert "ragas_answer_correctness" in result
        assert result["ragas_context_recall"] == 0.85
        assert result["ragas_faithfulness"] == 0.85

    @patch("backend.evaluation.ragas_bridge._get_ragas_embeddings")
    @patch("backend.evaluation.ragas_bridge._get_ragas_llm")
    def test_exception_returns_empty_dict(self, mock_llm, mock_emb):
        """验证失败隔离：单个 metric 异常不传播。"""
        mock_metric = MagicMock()
        mock_metric.score.side_effect = RuntimeError("mock error")

        patches = {
            "ragas.metrics.collections.ContextRecall": MagicMock(return_value=mock_metric),
            "ragas.metrics.collections.ContextPrecision": MagicMock(return_value=mock_metric),
            "ragas.metrics.collections.Faithfulness": MagicMock(return_value=mock_metric),
            "ragas.metrics.collections.AnswerRelevancy": MagicMock(return_value=mock_metric),
            "ragas.metrics.collections.AnswerCorrectness": MagicMock(return_value=mock_metric),
        }

        from backend.evaluation.ragas_bridge import compute_ragas_metrics

        patchers = [patch(k, v) for k, v in patches.items()]
        for p in patchers:
            p.start()

        try:
            result = compute_ragas_metrics(
                question="test",
                retrieved_texts=["ctx"],
                ground_truth_texts=["gt"],
                expected_answer="ans",
                generated_answer="gen",
            )
        finally:
            for p in patchers:
                p.stop()

        assert result == {}

    @patch("backend.evaluation.ragas_bridge._get_ragas_embeddings")
    @patch("backend.evaluation.ragas_bridge._get_ragas_llm")
    def test_nan_values_excluded(self, mock_llm, mock_emb):
        """验证 NaN 值被正确过滤。"""
        call_count = [0]
        values = [0.85, float("nan"), 0.90, float("nan"), 0.82]

        def make_score_fn():
            def score_fn(**kwargs):
                idx = call_count[0]
                call_count[0] += 1
                return _make_metric_result(values[idx % len(values)])
            return score_fn

        def make_metric_cls():
            mock_metric = MagicMock()
            mock_metric.score = make_score_fn()
            return MagicMock(return_value=mock_metric)

        patches = {
            "ragas.metrics.collections.ContextRecall": make_metric_cls(),
            "ragas.metrics.collections.ContextPrecision": make_metric_cls(),
            "ragas.metrics.collections.Faithfulness": make_metric_cls(),
            "ragas.metrics.collections.AnswerRelevancy": make_metric_cls(),
            "ragas.metrics.collections.AnswerCorrectness": make_metric_cls(),
        }

        from backend.evaluation.ragas_bridge import compute_ragas_metrics

        patchers = [patch(k, v) for k, v in patches.items()]
        for p in patchers:
            p.start()

        try:
            result = compute_ragas_metrics(
                question="test",
                retrieved_texts=["ctx"],
                ground_truth_texts=["gt"],
                expected_answer="ans",
                generated_answer="gen",
            )
        finally:
            for p in patchers:
                p.stop()

        assert "ragas_context_recall" in result
        assert "ragas_context_precision" not in result
        assert "ragas_answer_relevancy" not in result
        assert "ragas_faithfulness" in result

    def test_ragas_not_installed_returns_empty(self):
        """验证 ragas 包未安装时返回空 dict。"""
        from backend.evaluation.ragas_bridge import compute_ragas_metrics

        with patch.dict(
            "sys.modules",
            {"ragas": None, "ragas.metrics": None, "ragas.metrics.collections": None},
        ):
            result = compute_ragas_metrics(
                question="test",
                retrieved_texts=["ctx"],
                ground_truth_texts=["gt"],
                expected_answer="ans",
                generated_answer="gen",
            )

        assert result == {}


class TestResetCaches:
    def test_reset_clears_caches(self):
        import backend.evaluation.ragas_bridge as bridge
        bridge._embeddings_cache = "fake_emb"
        bridge._llm_cache = "fake_llm"
        bridge.reset_caches()
        assert bridge._embeddings_cache is None
        assert bridge._llm_cache is None
