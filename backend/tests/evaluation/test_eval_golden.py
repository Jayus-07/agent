"""Golden Set 快速预检 — 本地 pytest 入口。

用途：
  1. 本地 pre-commit / pre-push 快速验证 RAG 检索质量
  2. GitHub Actions CI quality gate
  3. 前端 /evaluations "运行" 按钮的离线后端

运行方式：
  # 仅 golden set（13 条，~30s）
  pytest backend/tests/evaluation/test_eval_golden.py -v

  # 完整评测集（59 条，~2min）
  pytest backend/tests/evaluation/test_eval_golden.py -v --full

环境变量：
  RERANKER_BACKEND=local   强制离线（CI 默认）
  HF_ENDPOINT             HF 模型镜像（CI 用 hf-mirror.com）
"""

import json
import os
from pathlib import Path

import pytest

# 强制离线：CI 和本地预检都不应调用付费 API
os.environ.setdefault("RERANKER_BACKEND", "local")

DATASETS_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "datasets"
GOLDEN_SET_PATH = DATASETS_DIR / "golden_set.json"
FULL_DATASET_PATH = DATASETS_DIR / "rag_test_kb.json"


def _load_golden_ids() -> list[str]:
    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["selected_ids"]


def _load_golden_thresholds() -> dict:
    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("thresholds", {})


def _run_rag_cases(case_ids: list[str]) -> list:
    """加载数据集 → 过滤指定 case → 运行 RAG runner → 返回 EvalResult 列表。"""
    from backend.evaluation.dataset import load_dataset_file
    from backend.evaluation.runners.builtin import _run_rag

    all_cases = load_dataset_file("rag_test_kb.json", default_module="rag")
    id_set = set(case_ids)
    filtered = [c for c in all_cases if c.id in id_set]

    missing = id_set - {c.id for c in filtered}
    if missing:
        pytest.skip(f"Golden set 中有 ID 在数据集中不存在: {sorted(missing)}")

    return _run_rag(filtered)


# ── Session-scoped fixture：避免重复加载模型 ──────────────────

@pytest.fixture(scope="module")
def golden_results():
    """运行 golden set 并缓存结果（module 级共享，避免重复检索）。"""
    ids = _load_golden_ids()
    return _run_rag_cases(ids)


@pytest.fixture(scope="module")
def thresholds():
    return _load_golden_thresholds()


# ── 聚合指标测试 ──────────────────────────────────────────────

class TestGoldenSetAggregate:
    """Golden set 聚合指标断言。"""

    def test_no_errors(self, golden_results):
        """所有用例不应出现 error 状态。"""
        errors = [r for r in golden_results if r.status == "error"]
        if errors:
            msgs = [f"{r.case_id}: {r.error_msg}" for r in errors]
            pytest.fail(f"{len(errors)} 条用例出错:\n" + "\n".join(msgs))

    def test_top1_accuracy(self, golden_results, thresholds):
        """正样本 Top-1 准确率 ≥ 阈值。"""
        min_acc = thresholds.get("top1_accuracy_min", 0.85)
        positive = [
            r for r in golden_results
            if not r.expected.get("should_reject")
            and r.expected.get("relevant_docs")
        ]
        if not positive:
            pytest.skip("Golden set 无正样本")
        hits = sum(1 for r in positive if r.metrics.get("top1_accuracy", 0) >= 1.0)
        acc = hits / len(positive)
        assert acc >= min_acc, (
            f"Top-1 准确率 {acc:.2%} < 阈值 {min_acc:.0%} "
            f"({hits}/{len(positive)} 命中)"
        )

    def test_snippet_recall(self, golden_results, thresholds):
        """Snippet 召回率 ≥ 阈值。"""
        min_recall = thresholds.get("snippet_recall_min", 0.80)
        cases_with_snippets = [
            r for r in golden_results
            if r.expected.get("relevant_snippets")
        ]
        if not cases_with_snippets:
            pytest.skip("Golden set 无 snippet 标注用例")
        recalls = [r.metrics.get("chunk_recall", 0) for r in cases_with_snippets]
        avg = sum(recalls) / len(recalls)
        assert avg >= min_recall, (
            f"平均 snippet 召回率 {avg:.2%} < 阈值 {min_recall:.0%}"
        )

    def test_reject_accuracy(self, golden_results, thresholds):
        """拒答用例准确率 ≥ 阈值。"""
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
        """部门隔离用例无泄漏。"""
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


# ── 单条用例明细测试（方便定位失败点）─────────────────────────

class TestGoldenSetPerCase:
    """逐条用例 pass/fail 断言 — 失败时直接显示哪个 case 出了问题。"""

    @pytest.mark.parametrize("case_id", _load_golden_ids())
    def test_case_pass(self, golden_results, case_id):
        result_map = {r.case_id: r for r in golden_results}
        r = result_map.get(case_id)
        assert r is not None, f"用例 {case_id} 未运行"
        assert r.status == "pass", (
            f"{case_id} 失败: status={r.status}, "
            f"top1={r.metrics.get('top1_accuracy')}, "
            f"chunk_recall={r.metrics.get('chunk_recall')}, "
            f"error={r.error_msg or ''}"
        )


# ── CLI 入口：--full 跑完整评测集 ─────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--full", action="store_true", default=False,
        help="运行完整评测集（59 条）而非 golden set（13 条）",
    )


@pytest.fixture(scope="module")
def full_results(request):
    """--full 模式下运行完整评测集。"""
    if not request.config.getoption("--full"):
        pytest.skip("未指定 --full，仅运行 golden set")
    from backend.evaluation.dataset import load_dataset_file
    all_cases = load_dataset_file("rag_test_kb.json", default_module="rag")
    ids = [c.id for c in all_cases]
    return _run_rag_cases(ids)
