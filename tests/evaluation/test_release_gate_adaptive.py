"""Unit tests for adaptive release gate and synonymous doc matching."""
from __future__ import annotations

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary, TestCase
from backend.evaluation.release_gate import compute_module_release_gate
from backend.evaluation.runners.doc_id_resolver import DocIdResolver
from backend.evaluation.runners.rag import _doc_id_match


def _make_report(
    module: str = "rag",
    mode: str = "offline",
    pass_rate: float = 1.0,
    has_reject_cases: bool = False,
    metrics: dict | None = None,
) -> EvalReport:
    metrics = metrics or {
        "recall@5": 1.0,
        "mrr": 1.0,
    }
    cases = [
        TestCase(
            id=f"C-{i}",
            question=f"Q{i}",
            module=module,
            expected={"should_reject": True} if (has_reject_cases and i == 1) else {"should_reject": False},
        )
        for i in range(1, 6)
    ]
    results = [
        EvalResult(
            case_id=c.id,
            module=module,
            status="pass",
            expected=c.expected,
            actual={},
        )
        for c in cases
    ]
    summary = ModuleSummary(
        module=module,
        total=len(cases),
        passed=len(cases),
        failed=0,
        errors=0,
        skipped=0,
        pass_rate=pass_rate,
        metrics=metrics,
    )
    return EvalReport(
        module=module,
        mode=mode,
        summaries=[summary],
        results=results,
        tier_summaries=[],
        metadata={"evaluator_mode": "self_built"},
    )


def test_rag_gate_adaptive_without_reject_cases():
    """纯正向单文档题集（无拒答负样本）时，reject_accuracy 与 false_answer_rate 自适应跳过，门禁通过。"""
    report = _make_report(
        module="rag",
        mode="offline",
        pass_rate=1.0,
        has_reject_cases=False,
        metrics={"recall@5": 1.0, "mrr": 0.8},
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == "PASS"
    assert gate["overall"] is True

    reject_acc_item = next(it for it in gate["items"] if it["metric"] == "reject_accuracy")
    assert reject_acc_item["requirement"] == "optional"
    assert "自适应跳过" in reject_acc_item["reason"]


def test_rag_gate_enforced_when_reject_cases_exist():
    """当评测套件包含拒答用例时，reject_accuracy 依然作为 required 严格校验。"""
    report = _make_report(
        module="rag",
        mode="offline",
        pass_rate=1.0,
        has_reject_cases=True,
        # 故意不给 reject_accuracy，模拟缺失
        metrics={"recall@5": 1.0, "mrr": 0.8},
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == "FAIL"

    reject_acc_item = next(it for it in gate["items"] if it["metric"] == "reject_accuracy")
    assert reject_acc_item["requirement"] == "required"
    assert reject_acc_item["passed"] is False


def test_sql_gate_adaptive_in_offline_sanity():
    """SQL 在离线模式下，router_hit 等 live 指标自适应跳过，不被判负。"""
    report = _make_report(
        module="sql",
        mode="offline",
        pass_rate=1.0,
        has_reject_cases=False,
        metrics={},
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == "PASS"

    router_hit_item = next(it for it in gate["items"] if it["metric"] == "router_hit")
    assert router_hit_item["requirement"] == "optional"
    assert "离线模式" in router_hit_item["reason"]


def test_doc_id_resolver_matches_synonymous_extensions():
    """DocIdResolver 应正确识别同一文档的不同格式载体（如 .docx ↔ .md）。"""
    resolver = DocIdResolver()
    assert resolver.matches("faq_only.docx", "faq_only.md") is True
    assert resolver.matches("employee_handbook.pdf", "employee_handbook.docx") is True
    assert resolver.matches("api_spec.json", "api_spec.md") is True
    # 不同的文件名不能误判
    assert resolver.matches("faq_only.docx", "other_doc.md") is False


def test_rag_doc_id_match_synonymous_extensions():
    """_doc_id_match 回退比较应正确识别同一文档的不同扩展名。"""
    assert _doc_id_match("faq_only.docx", {"faq_only.md"}) is True
    assert _doc_id_match("company_policy.pdf", {"company_policy.docx"}) is True
    assert _doc_id_match("faq_only.docx", {"different_doc.docx"}) is False
