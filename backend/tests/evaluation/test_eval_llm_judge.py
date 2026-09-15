"""LLM Judge 非阻塞测试 — 需要 Ollama 服务运行。

运行方式：
  # 需要 Ollama 服务 + qwen2.5:3b 模型
  ollama serve &
  ollama pull qwen2.5:3b
  pytest backend/tests/evaluation/test_eval_llm_judge.py -v -m llm_judge

CI 中由 rag_eval.yml 的 llm-judge job 运行（continue-on-error: true）。
"""
from __future__ import annotations

import os

import pytest
# 注：不要在这里设 RERANKER_BACKEND —— 该环境变量无任何代码消费（真开关是
# ENV_MODE，见 backend/rag/reranker.py get_reranker_backend），设了只是死变量。


def _get_gen_eval_ids() -> list[str]:
    """获取 generation_eval=true 的 CI golden 用例 ID。"""
    from backend.evaluation.dataset import load_dataset
    golden_cases = load_dataset("rag", selection="ci_golden")
    return [c.id for c in golden_cases if c.metadata.get("generation_eval")]


def _ollama_available() -> bool:
    """检测 Ollama 服务是否可用。"""
    import urllib.error
    import urllib.request
    try:
        url = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def llm_results():
    """运行 CI golden 中 generation_eval 用例的 Ollama 生成 + 评估。"""
    if not _ollama_available():
        pytest.skip("Ollama 服务不可用")

    from backend.evaluation.config import EvalConfig
    from backend.evaluation.dataset import load_dataset
    from backend.evaluation.generation import answer_relevancy_llm, generate_answer_ollama
    from backend.evaluation.service import EvaluationService

    golden_cases = load_dataset("rag", selection="ci_golden")
    gen_ids = set(_get_gen_eval_ids())
    filtered = [c for c in golden_cases if c.id in gen_ids]

    config = EvalConfig(
        module="rag", dataset="rag", selection="ci_golden", live=False,
    )
    report = EvaluationService().evaluate(config)
    base_results = [r for r in report.results if r.case_id in gen_ids]

    for r in base_results:
        details = r.actual.get("details", [])
        context_list = [d.get("page_content", "") for d in details if d.get("page_content")]
        question = r.actual.get("question", "")

        generated = generate_answer_ollama(question, context_list)
        if generated:
            relevancy = answer_relevancy_llm(question, generated)
            r.metrics["llm_answer_relevancy"] = relevancy
            r.metrics["llm_generated_length"] = float(len(generated))

    return base_results


@pytest.mark.llm_judge
class TestLLMJudge:
    """LLM Judge 非阻塞测试 — 仅在 Ollama 可用时运行。"""

    def test_ollama_generation_produces_output(self, llm_results):
        """Ollama 应为所有 generation_eval 用例生成非空答案。"""
        for r in llm_results:
            length = r.metrics.get("llm_generated_length", 0)
            assert length > 0, f"{r.case_id} Ollama 未生成答案"

    def test_answer_relevancy_reasonable(self, llm_results):
        """LLM 生成答案的语义相关性应 > 0.3（宽松阈值）。"""
        for r in llm_results:
            relevancy = r.metrics.get("llm_answer_relevancy", 0)
            assert relevancy > 0.3, (
                f"{r.case_id} llm_answer_relevancy={relevancy:.4f} < 0.3"
            )

    def test_metrics_report(self, llm_results, capsys):
        """输出 LLM Judge 指标摘要（信息性）。"""
        for r in llm_results:
            relevancy = r.metrics.get("llm_answer_relevancy", 0)
            length = r.metrics.get("llm_generated_length", 0)
            print(f"  {r.case_id}: relevancy={relevancy:.4f}, length={length:.0f}")
