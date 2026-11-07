"""Markdown 报告 Machine-readable Summary 必须保留 Release Gate 三态语义。

G2 补齐：此前 :451 用 `PASS if gate["overall"] else "FAIL"` 把 INVALID 折叠成
FAIL，机器可读段丢失「环境无效」语义（人类可读头部 :55-57 一直正确）。
回归口径：summary verdict 必须逐字透传 `compute_module_release_gate` 的
verdict（FAIL 与 INVALID 两个非 PASS 态足以证明不再二值折叠）。
"""

import json

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary
from backend.evaluation.report.markdown import write_markdown_report


def _make_report(results: list[EvalResult]) -> EvalReport:
    passed = sum(1 for r in results if r.status == "pass")
    summary = ModuleSummary(
        module="rag",
        total=len(results),
        passed=passed,
        failed=sum(1 for r in results if r.status == "fail"),
        errors=sum(1 for r in results if r.status == "error"),
        skipped=sum(1 for r in results if r.status == "skip"),
        pass_rate=passed / len(results) if results else 0.0,
    )
    return EvalReport(
        module="rag",
        mode="offline",
        summaries=[summary],
        results=results,
    )


def _machine_summary(markdown: str) -> dict:
    """提取 Machine-readable Summary 的 JSON 块。"""
    block = markdown.split("## 15. Machine-readable Summary", 1)[1]
    block = block.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(block)


def test_machine_summary_keeps_fail_verdict_without_invalid_reason(tmp_path):
    # 单条 pass 用例、无 gate 指标 → required 缺失判 FAIL（确定性构造）
    report = _make_report(
        [EvalResult(case_id="R1", module="rag", status="pass", expected={}, actual={})]
    )
    markdown = write_markdown_report(report, tmp_path / "fail").read_text(
        encoding="utf-8"
    )
    summary = _machine_summary(markdown)
    assert summary["verdict"] == "FAIL"
    assert summary["invalid_reason"] == ""


def test_machine_summary_keeps_invalid_verdict_and_reason(tmp_path):
    # 3 条全部命中 Provider 配置错误签名 → INVALID_PROVIDER
    # （validity.py:89 门槛 = max(3, 80% 非跳过)，3 条全中即满足）
    results = [
        EvalResult(
            case_id=f"R{i}",
            module="rag",
            status="error",
            expected={},
            actual={},
            error_msg="invalid api key",
        )
        for i in range(1, 4)
    ]
    report = _make_report(results)
    markdown = write_markdown_report(report, tmp_path / "invalid").read_text(
        encoding="utf-8"
    )
    summary = _machine_summary(markdown)
    assert summary["verdict"] == "INVALID"
    assert summary["invalid_reason"] == "llm_provider_misconfigured"
    assert "**INVALID**" in markdown  # 人类可读头部同口径
