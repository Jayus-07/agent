"""LLM Judge 非阻塞测试 — 需要 Ollama 服务运行。

运行方式：
  # 需要 Ollama 服务 + qwen2.5:3b 模型
  ollama serve &
  ollama pull qwen2.5:3b
  pytest backend/tests/evaluation/test_eval_llm_judge.py -v -m llm_judge

CI 中由 rag_eval.yml 的 llm-judge job 运行（continue-on-error: true）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("RERANKER_BACKEND", "local")

DATASETS_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "datasets"
GOLDEN_SET_PATH = DATASETS_DIR / "golden_set.json"


def _load_golden_ids() -> list[str]:
    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["selected_ids"]


def _get_gen_eval_ids() -> list[str]:
    """获取 generation_eval=true 的 golden 用例 ID。"""
    from backend.evaluation.dataset import load_dataset_file
    all_cases = load_dataset_file("rag_test_kb.json", default_module="rag")
    golden_ids = set(_load_golden_ids())
    return [
        c.id for c in all_cases
        if c.id in golden_ids and c.metadata.get("generation_eval")
    ]


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
    """运行 golden set 中 generation_eval 用例的 Ollama 生成 + 评估。"""
    if not _ollama_available():
        pytest.skip("Ollama 服务不可用")

    from backend.evaluation.dataset import load_dataset_file
    from backend.evaluation.generation import answer_relevancy_llm, generate_answer_ollama
    from backend.evaluation.runners.builtin import _run_rag

    all_cases = load_dataset_file("rag_test_kb.json", default_module="rag")
    golden_ids = set(_load_golden_ids())
    gen_ids = set(_get_gen_eval_ids())
    filtered = [c for c in all_cases if c.id in golden_ids and c.id in gen_ids]

    base_results = _run_rag(filtered)

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
