"""Golden Set 快速预检 — 本地 pytest 入口。

用途：
  1. 本地 pre-commit / pre-push 快速验证 RAG 检索质量
  2. GitHub Actions CI quality gate（Deterministic tier）
  3. 前端 /evaluations "运行" 按钮的离线后端

运行方式：
  # golden set（10 条 CI 门禁子集）— 默认被跳过（真实重建索引+检索 ~10min），
  # 必须显式开启：
  pytest backend/tests/evaluation/test_eval_golden.py -v --quality-gate
  #   或 RUN_QUALITY_GATE=1 pytest backend/tests/evaluation/test_eval_golden.py -v
  #   并行跑建议加 --dist loadscope（module 级 fixture 只在每个 worker 各建一次）

  # 完整评测集（145 条 canonical）
  pytest backend/tests/evaluation/test_eval_golden.py -v --full

环境变量：
  RERANKER_BACKEND=local   强制离线（CI 默认）
  EVAL_GATE=legacy         CI 默认（Deterministic tier）
  EVAL_GATE=semantic       离线全量（含 Semantic tier）
  HF_ENDPOINT             HF 模型镜像（CI 用 hf-mirror.com）

三层指标体系（V4）：
  Tier 1 Deterministic（CI 必跑）: recall@k, MRR, NDCG, chunk_recall, dept_leak
  Tier 2 Semantic（离线/夜间）: sem_context_recall, sem_faithfulness
  Tier 3 LLM Judge（夜间）: judge_completeness, judge_faithfulness
"""

import math
import os

import pytest

# 语义评分器默认用 embedding 通道：ENV_MODE=cloud 时 EmbeddingScorer 走
# DashScope 在线 API（embedding_singleton），避免本地 cross_encoder 在
# CPU torch 上加载/推理（曾拖出 ~10min setup，且 RERANKER_DEVICE=cuda
# 与 CPU torch 不匹配必然降级）。原 RERANKER_BACKEND 环境变量无任何
# 代码消费（死变量），已移除。
# 离线 CI 场景可显式 EVAL_SEMANTIC_SCORER=lexical 走零依赖兜底。
os.environ.setdefault("EVAL_SEMANTIC_SCORER", "embedding")

# 质量门禁标记：conftest 的 pytest_collection_modifyitems 会默认跳过本文件
# 全部用例（真实重建索引+本地 embedding/reranker，单跑 ~10min），必须
# --quality-gate 或 RUN_QUALITY_GATE=1 显式运行。跳过原因见 conftest。
pytestmark = pytest.mark.quality_gate


def _is_semantic_mode() -> bool:
    """当前是否运行在 Semantic 模式（shadow / semantic）。"""
    from backend.evaluation.runners._common import gate_mode
    return gate_mode() in ("shadow", "semantic")


def _load_golden_cases():
    """加载 CI golden 子集（10 条）。ID 不可解析时直接 FAIL。"""
    from backend.evaluation.dataset import load_dataset
    return load_dataset("rag", selection="ci_golden")


def _load_golden_thresholds() -> dict:
    """从 ci_golden.jsonl 的 companion 元数据或默认值获取阈值。"""
    return {
        "recall_at_5_min": 0.60,
        "mrr_min": 0.50,
        "sem_context_recall_min": 0.50,
        "sem_context_precision_min": 0.40,
        "reject_accuracy_min": 0.80,
        "dept_isolation_pass": True,
        "sem_faithfulness_min": 0.50,
        "sem_answer_correctness_min": 0.40,
    }


def _run_rag_cases(selection: str = "ci_golden") -> tuple:
    """通过 EvaluationService 运行指定评测集 — 单一核心链路。"""
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.service import EvaluationService

    config = EvalConfig(
        module="rag",
        dataset="rag",
        selection=selection,
        live=False,
        # 质量门禁必须真实重跑：checkpoint 按 case_id 索引、无代码/索引版本
        # 指纹，改检索代码后旧结果会掩蔽新行为（2026-09-13 RC-086/095 修复
        # 曾被旧缓存结果掩蔽）。CLI 长跑仍可用 --resume 断点续跑。
        resume=False,
    )
    report = EvaluationService().evaluate(config)
    return report, report.results


@pytest.fixture(scope="module")
def golden_results(request):
    """运行 golden set 并缓存结果（module 级共享，避免重复检索）。"""
    report, results = _run_rag_cases()
    request.config._golden_eval_report = report
    return results


@pytest.fixture(scope="module")
def thresholds():
    return _load_golden_thresholds()


# ── 聚合指标测试 ──────────────────────────────────────────────

class TestGoldenSetAggregate:
    """Golden set 聚合指标断言 — Deterministic tier（所有模式均运行）。"""

    def test_no_errors(self, golden_results):
        errors = [r for r in golden_results if r.status == "error"]
        if errors:
            msgs = [f"{r.case_id}: {r.error_msg}" for r in errors]
            pytest.fail(f"{len(errors)} 条用例出错:\n" + "\n".join(msgs))

    def test_recall_at_k(self, golden_results, thresholds):
        min_recall = thresholds.get("recall_at_5_min", 0.60)
        recalls = [
            r.metrics.get("recall@5", 0)
            for r in golden_results
            if r.status != "error" and not math.isnan(r.metrics.get("recall@5", 0))
        ]
        if not recalls:
            pytest.fail("无有效评测结果（所有用例 recall@5 均为 NaN）")
        avg = sum(recalls) / len(recalls)
        assert avg >= min_recall, (
            f"平均 recall@5={avg:.2%} < 阈值 {min_recall:.0%}"
        )

    def test_mrr(self, golden_results, thresholds):
        min_mrr = thresholds.get("mrr_min", 0.50)
        mrrs = [
            r.metrics.get("mrr", 0)
            for r in golden_results
            if r.status != "error" and not math.isnan(r.metrics.get("mrr", 0))
        ]
        if not mrrs:
            pytest.fail("无有效评测结果（所有用例 MRR 均为 NaN）")
        avg = sum(mrrs) / len(mrrs)
        assert avg >= min_mrr, (
            f"平均 MRR={avg:.2%} < 阈值 {min_mrr:.0%}"
        )

    def test_context_recall(self, golden_results, thresholds):
        """语义上下文召回率 — 仅 Semantic 模式运行。"""
        if not _is_semantic_mode():
            pytest.skip("Deterministic tier 不检查语义召回")
        min_recall = thresholds.get("sem_context_recall_min", 0.50)
        cases_with_gt = [
            r for r in golden_results
            if r.expected.get("ground_truth_context")
            or r.expected.get("relevant_snippets")
        ]
        if not cases_with_gt:
            pytest.fail("Golden set 无上下文标注用例")
        recalls = [r.metrics.get("sem_context_recall", 0) for r in cases_with_gt]
        avg = sum(recalls) / len(recalls)
        assert avg >= min_recall, (
            f"平均语义上下文召回率 {avg:.2%} < 阈值 {min_recall:.0%}"
        )

    def test_reject_accuracy(self, golden_results, thresholds):
        min_acc = thresholds.get("reject_accuracy_min", 0.80)
        negative = [r for r in golden_results if r.expected.get("should_reject")]
        if not negative:
            pytest.skip("Golden set 无拒答用例")
        hits = sum(1 for r in negative if r.metrics.get("reject_accuracy", 0) >= 1.0)
        acc = hits / len(negative)
        assert acc >= min_acc, (
            f"拒答准确率 {acc:.2%} < 阈值 {min_acc:.0%} "
            f"({hits}/{len(negative)} 正确拒答)"
        )

    def test_dept_isolation(self, golden_results, thresholds):
        if not thresholds.get("dept_isolation_pass", True):
            pytest.skip("部门隔离断言已禁用")
        dept_cases = [
            r for r in golden_results
            if r.expected.get("forbidden_departments")
            or r.expected.get("allowed_departments")
        ]
        if not dept_cases:
            pytest.skip("Golden set 无部门隔离用例")
        leaks = [r for r in dept_cases if r.metrics.get("dept_leak", 0) > 0]
        assert not leaks, (
            f"{len(leaks)} 条部门隔离用例发生泄漏: "
            + ", ".join(r.case_id for r in leaks)
        )


# ── 单条用例明细测试 ──────────────────────────────────────────

def _golden_case_ids() -> list[str]:
    return [c.id for c in _load_golden_cases()]


class TestGoldenSetPerCase:
    """逐条 pass/fail 断言。"""

    @pytest.mark.parametrize("case_id", _golden_case_ids())
    def test_case_pass(self, golden_results, case_id):
        result_map = {r.case_id: r for r in golden_results}
        r = result_map.get(case_id)
        assert r is not None, f"用例 {case_id} 未运行"
        metrics_info = (
            f"recall@5={r.metrics.get('recall@5')}, "
            f"mrr={r.metrics.get('mrr')}"
        )
        if _is_semantic_mode():
            metrics_info += (
                f", sem_context_recall={r.metrics.get('sem_context_recall')}, "
                f"sem_faithfulness={r.metrics.get('sem_faithfulness')}"
            )
        assert r.status == "pass", (
            f"{case_id} 失败: status={r.status}, {metrics_info}, "
            f"error={r.error_msg or ''}"
        )


# ── Semantic tier 测试（仅 shadow/semantic 模式运行） ──────────

@pytest.mark.skipif(
    not _is_semantic_mode(),
    reason="Deterministic tier 不运行语义指标测试",
)
class TestSemanticTier:
    """Semantic tier 指标验证 — 需要 CrossEncoder 模型。"""

    SEMANTIC_KEYS = {"sem_context_recall", "sem_context_recall_soft", "sem_context_precision"}

    def test_semantic_metrics_present(self, golden_results):
        for r in golden_results:
            if r.status == "error":
                continue
            present = self.SEMANTIC_KEYS & set(r.metrics.keys())
            assert present, f"{r.case_id} 缺少语义指标，仅有: {sorted(r.metrics.keys())}"

    def test_semantic_recall_range(self, golden_results):
        for r in golden_results:
            val = r.metrics.get("sem_context_recall")
            if val is not None and not math.isnan(val):
                assert 0.0 <= val <= 1.0, f"{r.case_id} sem_context_recall={val} 超出 [0,1]"


# ── Semantic tier: 生成质量阻塞门控 ──────────────────────────

@pytest.mark.skipif(
    not _is_semantic_mode(),
    reason="Deterministic tier 不运行语义门控",
)
class TestGenerationQualityBlocking:
    """generation_eval 用例的 CrossEncoder 语义门控（Semantic tier）。"""

    def test_semantic_faithfulness(self, golden_results, thresholds):
        min_faith = thresholds.get("sem_faithfulness_min", 0.50)
        gen_results = [r for r in golden_results if "sem_faithfulness" in r.metrics]
        if not gen_results:
            pytest.skip("Golden set 无 generation_eval 用例")
        for r in gen_results:
            val = r.metrics.get("sem_faithfulness", 0)
            assert val >= min_faith, (
                f"{r.case_id} sem_faithfulness={val:.4f} < 阈值 {min_faith}"
            )

    def test_answer_correctness(self, golden_results, thresholds):
        min_corr = thresholds.get("sem_answer_correctness_min", 0.40)
        gen_results = [r for r in golden_results if "sem_answer_correctness" in r.metrics]
        if not gen_results:
            pytest.skip("Golden set 无 generation_eval 用例")
        for r in gen_results:
            val = r.metrics.get("sem_answer_correctness", 0)
            assert val >= min_corr, (
                f"{r.case_id} sem_answer_correctness={val:.4f} < 阈值 {min_corr}"
            )

    def test_hallucination_rate_bounded(self, golden_results):
        gen_results = [r for r in golden_results if "sem_hallucination_rate" in r.metrics]
        if not gen_results:
            pytest.skip("Golden set 无 generation_eval 用例")
        for r in gen_results:
            hall = r.metrics.get("sem_hallucination_rate")
            if hall is not None:
                assert hall <= 0.50, (
                    f"{r.case_id} sem_hallucination_rate={hall:.4f} > 0.50"
                )


# ── CLI 入口：--full 跑完整评测集 ─────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--full", action="store_true", default=False,
        help="运行完整评测集（145 条 canonical）而非 golden set（10 条）",
    )


@pytest.fixture(scope="module")
def full_results(request):
    """--full 模式下运行完整评测集。"""
    if not request.config.getoption("--full"):
        pytest.skip("未指定 --full，仅运行 golden set")
    return _run_rag_cases(selection="all")
