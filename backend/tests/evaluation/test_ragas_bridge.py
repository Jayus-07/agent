"""RAGAS bridge 单元测试 — 验证返回值结构、失败隔离、空输入短路、缓存重置。

2026-09-12 重写：原测试针对已废弃的旧 API（is_ragas_enabled / retrieved_texts
参数 / ragas.metrics.collections mock / _embeddings_cache），与现行实现全面漂移
导致 10 个用例长期失败。现行 API：

  - compute_ragas_metrics(question, answer, contexts, ground_truth, level,
    llm_wrapper, embeddings) → {ragas_*: float|None}；单指标失败记 None 不传播
  - compute_ragas_metrics_safe(...) → 超时/异常记 None；空输入直接返回 {}
  - 启用控制由 RagasEvaluator.should_run(ctx) 承担（no_ragas / ragas_disabled）
  - 缓存：_llm_wrapper / _embeddings / _metric_cache，reset_caches() 统一清空
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clean_bridge_caches():
    """每个用例前后清空 bridge 全局缓存，避免 mock 对象 id 复用导致缓存串扰。"""
    import backend.evaluation.ragas_bridge as bridge

    bridge.reset_caches()
    yield
    bridge.reset_caches()


def _patch_ragas_metrics(score_fn):
    """Patch ragas.metrics 的 5 个 metric 类，实例的 single_turn_score 走 score_fn。"""
    mock_metric = MagicMock()
    mock_metric.single_turn_score.side_effect = score_fn
    patches = {
        name: MagicMock(return_value=mock_metric)
        for name in ("ContextRecall", "Faithfulness", "ContextPrecision",
                     "AnswerRelevancy", "AnswerCorrectness")
    }
    return patches


def _compute_with_patched_metrics(score_fn, **kwargs):
    """在 mock 掉 ragas metric 类的状态下调用 compute_ragas_metrics。"""
    from backend.evaluation.ragas_bridge import compute_ragas_metrics

    patches = _patch_ragas_metrics(score_fn)
    kwargs.setdefault("llm_wrapper", MagicMock())
    kwargs.setdefault("embeddings", MagicMock())
    patchers = [patch(f"ragas.metrics.{k}", v) for k, v in patches.items()]
    for p in patchers:
        p.start()
    try:
        return compute_ragas_metrics(**kwargs)
    finally:
        for p in patchers:
            p.stop()


class TestSafeWrapperEmptyInput:
    """compute_ragas_metrics_safe：空输入短路返回 {}。"""

    def test_empty_contexts(self):
        from backend.evaluation.ragas_bridge import compute_ragas_metrics_safe

        result = compute_ragas_metrics_safe(
            question="test", answer="ans", contexts=[],
        )
        assert result == {}

    def test_empty_answer(self):
        from backend.evaluation.ragas_bridge import compute_ragas_metrics_safe

        result = compute_ragas_metrics_safe(
            question="test", answer="", contexts=["ctx"],
        )
        assert result == {}

    def test_whitespace_answer(self):
        from backend.evaluation.ragas_bridge import compute_ragas_metrics_safe

        result = compute_ragas_metrics_safe(
            question="test", answer="   ", contexts=["ctx"],
        )
        assert result == {}


class TestComputeRagasMetrics:
    """compute_ragas_metrics：返回值结构 / 失败隔离 / NaN 过滤 / GT 依赖裁剪。"""

    def test_returns_ragas_prefixed_keys(self):
        """验证返回值结构：全部 ragas_* 前缀，standard 档 4 项指标。"""
        result = _compute_with_patched_metrics(
            lambda sample: 0.85,
            question="测试问题",
            answer="生成的答案",
            contexts=["检索到的上下文"],
            ground_truth="标准答案上下文",
            level="standard",
        )
        assert set(result.keys()) == {
            "ragas_context_recall", "ragas_faithfulness",
            "ragas_context_precision", "ragas_answer_relevancy",
        }
        assert result["ragas_context_recall"] == 0.85
        assert result["ragas_faithfulness"] == 0.85

    def test_single_metric_exception_isolated(self):
        """验证失败隔离：单个 metric 异常记 None，不传播、不影响其他指标。"""
        calls = {"n": 0}

        def score_fn(sample):
            calls["n"] += 1
            if calls["n"] == 1:  # 第一个指标（ContextRecall）抛异常
                raise RuntimeError("mock error")
            return 0.9

        result = _compute_with_patched_metrics(
            score_fn,
            question="test", answer="gen", contexts=["ctx"], ground_truth="gt",
        )
        assert result["ragas_context_recall"] is None
        assert result["ragas_faithfulness"] == 0.9

    def test_nan_values_recorded_as_none(self):
        """验证 NaN 被过滤为 None（缺失语义），不冒充真实得分。"""
        values = iter([0.85, float("nan"), 0.90, float("nan")])

        def score_fn(sample):
            return next(values)

        result = _compute_with_patched_metrics(
            score_fn,
            question="test", answer="gen", contexts=["ctx"], ground_truth="gt",
            level="standard",
        )
        assert result["ragas_context_recall"] == 0.85
        assert result["ragas_faithfulness"] is None
        assert result["ragas_context_precision"] == 0.90
        assert result["ragas_answer_relevancy"] is None

    def test_ground_truth_none_skips_dep_metrics(self):
        """ground_truth 为空时自动移除依赖指标（ContextRecall/Precision/Correctness）。"""
        result = _compute_with_patched_metrics(
            lambda sample: 0.85,
            question="test", answer="gen", contexts=["ctx"], ground_truth=None,
        )
        assert "ragas_context_recall" not in result
        assert "ragas_context_precision" not in result
        assert result["ragas_faithfulness"] == 0.85

    def test_ragas_not_installed_all_none(self):
        """ragas 包不可用时 safe 包装器返回全 None（缺失语义，非 0 分）。"""
        from backend.evaluation.ragas_bridge import compute_ragas_metrics_safe

        with patch.dict(
            "sys.modules",
            {"ragas": None, "ragas.dataset_schema": None, "ragas.metrics": None},
        ):
            result = compute_ragas_metrics_safe(
                question="test", answer="gen", contexts=["ctx"],
            )
        # safe 包装器按 level 展开全部 key，值全为 None
        assert result["ragas_context_recall"] is None
        assert result["ragas_faithfulness"] is None


class TestResetCaches:
    """reset_caches：清空 LLM/Embeddings 单例与 metric 对象缓存。"""

    def test_reset_clears_caches(self):
        import backend.evaluation.ragas_bridge as bridge

        bridge._llm_wrapper = "fake_llm"
        bridge._embeddings = "fake_emb"
        bridge._metric_cache[("Faithfulness", 1)] = "fake_metric"
        bridge.reset_caches()
        assert bridge._llm_wrapper is None
        assert bridge._embeddings is None
        assert bridge._metric_cache == {}


class TestRagasEvaluatorGating:
    """启用控制：RagasEvaluator.should_run（原 is_ragas_enabled 的现行等价物）。"""

    def _evaluator(self):
        from backend.evaluation.evaluators.ragas_provider import RagasEvaluator
        return RagasEvaluator()

    def test_enabled_by_default(self):
        assert self._evaluator().should_run(MagicMock(), {}) is True

    def test_no_ragas_flag_disables(self):
        assert self._evaluator().should_run(MagicMock(), {"no_ragas": True}) is False

    def test_ragas_disabled_flag_disables(self):
        assert self._evaluator().should_run(MagicMock(), {"ragas_disabled": True}) is False

    def test_missing_input_returns_none_not_zero(self):
        """无法计算（无 ragas_input）→ None + reason，不冒充 0 分。"""
        result = self._evaluator().evaluate(MagicMock(), {})
        assert result["ragas_context_recall"] is None
        assert result["ragas_reason"] == "no_ragas_input"
