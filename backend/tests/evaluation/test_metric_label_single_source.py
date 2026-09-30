"""评测报告指标中文名 —— 单一事实源守护。

同源病史（同 P1 那批）：同一份「指标键 → 中文显示名」曾被手写 3 份：
  - `evaluation/report/builder.py` 的 `METRIC_LABELS`（权威表）
  - `evaluation/report/markdown.py` 在 5 个表格里把名字内联写死
  - `scripts/pr_comment_metrics.py` 自持一份 `METRIC_DISPLAY`
其中 CI 脚本那份**已经漂移**：`sem_context_recall` 被写成「语义召回」，
丢了「上下文」两个字（权威表是「语义上下文召回」）。手写副本会过期，派生不会。

本文件锁两件事：
1. **派生仍是派生**：渲染层源码里不得再出现指标名**字面量**。
   用 AST 取字符串常量而非扫文本——历史说明注释里出现旧名是允许的，
   不应被误判（上一轮踩过「断言被自己的 docstring 咬住」的坑）。
2. **渲染确实用权威名**：真跑一次 markdown 报告，断言表头出现的是
   `METRIC_LABELS` 的值，且旧写法不出现。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary
from backend.evaluation.report import METRIC_LABELS
from backend.evaluation.report.markdown import write_markdown_report

_REPO_ROOT = Path(__file__).resolve().parents[3]
# 渲染层：这两份是「消费方」，不得自己写名字
_RENDERER_FILES = [
    _REPO_ROOT / "backend" / "evaluation" / "report" / "markdown.py",
    _REPO_ROOT / "backend" / "scripts" / "pr_comment_metrics.py",
]


def _string_constants(path: Path) -> list[str]:
    """模块源码里所有字符串常量（自动忽略注释与文档字符串）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_authoritative_map_is_self_consistent():
    """权威表本身不得有重复中文名（同名不同键 = 又一个待收口的重复表达）。"""
    values = list(METRIC_LABELS.values())
    dupes = {v for v in values if values.count(v) > 1}
    assert not dupes, f"METRIC_LABELS 里中文名重复：{sorted(dupes)}"


@pytest.mark.parametrize("path", _RENDERER_FILES, ids=lambda p: p.name)
def test_renderers_hold_no_inlined_metric_name(path: Path):
    """渲染层不得内联指标中文名——名字的唯一出口是 METRIC_LABELS。

    修复前实测红：markdown.py 有 "Recall@5" / "语义上下文召回" / "事实覆盖率" 等，
    pr_comment_metrics.py 有 "语义召回" / "误答率" 等。
    """
    inlined = sorted(set(_string_constants(path)) & set(METRIC_LABELS.values()))
    assert not inlined, (
        f"{path.name} 内联了指标中文名 {inlined}——"
        "指标显示名的唯一事实源是 evaluation.report.METRIC_LABELS，请改为 .get(key, key)"
    )


def test_renderers_reference_the_authoritative_map():
    """结构守护：渲染层必须真的引用权威表（而不是既不内联也不引用）。"""
    for path in _RENDERER_FILES:
        src = path.read_text(encoding="utf-8")
        assert "METRIC_LABELS" in src or "METRIC_DISPLAY" in src, (
            f"{path.name} 既没内联指标名、也没引用权威表——指标名从哪来？"
        )


def test_markdown_report_renders_authoritative_labels(tmp_path: Path):
    """真跑渲染：表头必须出现权威中文名，旧写法（漂移名）不得出现。"""
    summary = ModuleSummary(
        module="rag",
        total=1,
        passed=1,
        failed=0,
        errors=0,
        skipped=0,
        pass_rate=1.0,
        metrics={
            "recall@5": 0.9,
            "sem_context_recall": 0.8,
            "false_answer_rate": 0.0,
            "ragas_faithfulness": 0.85,
            "citation_accuracy": 0.9,
        },
    )
    result = EvalResult(
        case_id="R001",
        module="rag",
        status="pass",
        expected={},
        actual={},
        metrics={"sem_context_recall": 0.8},
    )
    report = EvalReport(
        module="rag", mode="offline", summaries=[summary], results=[result]
    )

    out = write_markdown_report(report, tmp_path)
    text = out.read_text(encoding="utf-8")

    for key in ("recall@5", "sem_context_recall", "false_answer_rate",
                "ragas_faithfulness", "citation_accuracy"):
        assert METRIC_LABELS[key] in text, (
            f"报告里没出现权威中文名 {METRIC_LABELS[key]!r}（key={key}）"
        )

    # 已漂移的旧写法不得回归
    for stale in ("语义召回", "Recall@5", "Context Recall", "Faithfulness"):
        assert stale not in text, f"报告回退到旧写法：{stale}"
