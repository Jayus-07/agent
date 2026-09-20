"""Markdown Release Gate 报告生成（14 节正式格式）。"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from backend.evaluation.models import EvalReport
from .builder import (
    DEFAULT_THRESHOLDS,
    GATE_METRICS,
    LOWER_IS_BETTER,
    METRIC_LABELS,
    METRIC_SOURCE,
    MODULE_LABELS,
    STATUS_ICONS,
    STATUS_LABELS,
    _LAYER_ORDER,
    _categorize_metric,
    compute_dataset_validation,
    compute_performance_stats,
    compute_query_type_stats,
    compute_release_gate,
    compute_reject_hallucination_pareto,
)


def write_markdown_report(
    report: EvalReport,
    output_dir: Path,
    baseline_metrics: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
) -> Path:
    """生成 14 节 Release Gate 报告，保存到 output_dir，返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.md"
    module_zh = MODULE_LABELS.get(report.module, report.module)
    mode_zh = "实时" if report.mode == "live" else "离线"

    rag_summary = next((s for s in report.summaries if s.module == "rag"), None)
    metrics = rag_summary.metrics if rag_summary else {}
    gate = compute_release_gate(metrics, thresholds)
    dataset_val = compute_dataset_validation(report)
    perf = compute_performance_stats(report.results)
    qt_stats = compute_query_type_stats(report.results)

    lines: list[str] = []

    # ── 1. 总体结论 ──
    lines.append(f"# RAG 评测 Release Gate 报告 — {module_zh}")
    lines.append("")
    verdict = "**PASS** ✅ 可以发布" if gate["overall"] else "**FAIL** ❌ 不建议发布"
    lines.append(f"## 1. 总体结论")
    lines.append("")
    lines.append(f"### {verdict}")
    lines.append("")
    lines.append(f"- **时间**: {report.timestamp}")
    lines.append(f"- **模式**: {mode_zh}")
    lines.append(f"- **用例数**: {len(report.results)}")
    pass_count = sum(1 for r in report.results if r.status == "pass")
    fail_count = sum(1 for r in report.results if r.status == "fail")
    err_count = sum(1 for r in report.results if r.status == "error")
    lines.append(f"- **通过/失败/错误**: {pass_count}/{fail_count}/{err_count}")
    if rag_summary:
        lines.append(f"- **通过率**: {rag_summary.pass_rate:.1%}")
    lines.append("")

    failed_metrics = [it for it in gate["items"] if it["passed"] is False]
    if failed_metrics:
        lines.append("**未达标指标**:")
        lines.append("")
        for it in failed_metrics:
            lines.append(f"- {it['label']} = {it['value']:.4f} (阈值 {it['threshold']}, {it['source']})")
        lines.append("")

    # ── 2. 评测集概况 ──
    lines.append("## 2. 评测集概况")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|------|----|")
    lines.append(f"| 总用例数 | {dataset_val['total_cases']} |")
    lines.append(f"| 唯一 ID 数 | {dataset_val['unique_ids']} |")
    lines.append(f"| Schema 合法 | {'✅' if dataset_val['schema_valid'] else '❌'} |")
    lines.append(f"| 拒答用例 | {dataset_val['reject_cases']} |")
    lines.append(f"| 覆盖题型 | {', '.join(dataset_val['query_types_covered']) or '—'} |")
    if dataset_val['duplicate_ids']:
        lines.append(f"| 重复 ID | {', '.join(dataset_val['duplicate_ids'])} |")
    if dataset_val['missing_expected_answer']:
        lines.append(f"| 缺少 expected_answer | {dataset_val['missing_expected_answer']} |")
    if dataset_val['missing_required_facts']:
        lines.append(f"| 缺少 required_facts | {dataset_val['missing_required_facts']} |")
    lines.append("")

    # ── 3. Retrieval 评测 ──
    lines.append("## 3. Retrieval 评测")
    lines.append("")
    retrieval_metrics = [
        ("recall@5", "Recall@5", "自研"),
        ("mrr", "MRR", "自研"),
        ("top1_accuracy", "Top-1 准确率", "自研"),
        ("sem_context_recall", "语义上下文召回", "自研"),
        ("ragas_context_recall", "Context Recall", "RAGAS"),
        ("ragas_context_precision", "Context Precision", "RAGAS"),
    ]
    lines.append("| 指标 | 数值 | 阈值 | 来源 | 状态 |")
    lines.append("|------|------|------|------|------|")
    for key, label, source in retrieval_metrics:
        val = metrics.get(key)
        th = DEFAULT_THRESHOLDS.get(key)
        if val is None:
            lines.append(f"| {label} | — | {th} | {source} | ⬜ 无数据 |")
        else:
            passed = val >= th
            icon = "✅" if passed else "❌"
            lines.append(f"| {label} | **{val:.4f}** | {th} | {source} | {icon} |")
    lines.append("")

    # ── 4. Generation 评测 ──
    lines.append("## 4. Generation 评测")
    lines.append("")
    gen_metrics = [
        ("ragas_faithfulness", "Faithfulness", "RAGAS"),
        ("ragas_answer_relevancy", "Answer Relevancy", "RAGAS"),
        ("ragas_answer_correctness", "Answer Correctness", "RAGAS"),
        ("required_fact_coverage", "事实覆盖率", "自研"),
    ]
    lines.append("| 指标 | 数值 | 阈值 | 来源 | 状态 |")
    lines.append("|------|------|------|------|------|")
    for key, label, source in gen_metrics:
        val = metrics.get(key)
        th = DEFAULT_THRESHOLDS.get(key)
        if val is None:
            lines.append(f"| {label} | — | {th} | {source} | ⬜ 无数据 |")
        else:
            passed = val >= th
            icon = "✅" if passed else "❌"
            lines.append(f"| {label} | **{val:.4f}** | {th} | {source} | {icon} |")
    lines.append("")

    # ── 5. Negative Reject 评测 ──
    lines.append("## 5. Negative Reject 评测")
    lines.append("")
    reject_metrics = [
        ("reject_accuracy", "拒答准确率", "自研"),
        ("false_answer_rate", "误答率", "自研"),
    ]
    lines.append("| 指标 | 数值 | 阈值 | 来源 | 状态 |")
    lines.append("|------|------|------|------|------|")
    for key, label, source in reject_metrics:
        val = metrics.get(key)
        th = DEFAULT_THRESHOLDS.get(key)
        if val is None:
            lines.append(f"| {label} | — | {th} | {source} | ⬜ 无数据 |")
        else:
            if key in LOWER_IS_BETTER:
                passed = val <= th
            else:
                passed = val >= th
            icon = "✅" if passed else "❌"
            lines.append(f"| {label} | **{val:.4f}** | {th} | {source} | {icon} |")
    lines.append("")

    pareto_rows = compute_reject_hallucination_pareto(report.results)
    if pareto_rows:
        lines.append("**拒答阈值 × 幻觉率帕累托分析**:")
        lines.append("")
        lines.append("| 拒答阈值 | 拒答准确率 | 幻觉泄漏率 | 误拒率 |")
        lines.append("|----------|------------|------------|--------|")
        for row in pareto_rows:
            ra = f"{row['reject_accuracy']:.2%}" if row['reject_accuracy'] is not None else "—"
            hl = f"{row['hallucination_leak']:.4f}" if row['hallucination_leak'] is not None else "—"
            fr = f"{row['false_reject_rate']:.2%}" if row['false_reject_rate'] is not None else "—"
            lines.append(f"| {row['threshold']} | {ra} | {hl} | {fr} |")
        lines.append("")

    # ── 6. Citation 评测 ──
    lines.append("## 6. Citation 评测")
    lines.append("")
    citation_metrics = [
        ("citation_accuracy", "引用准确度", "自研"),
        ("citation_completeness", "引用完整度", "自研"),
    ]
    lines.append("| 指标 | 数值 | 阈值 | 来源 | 状态 |")
    lines.append("|------|------|------|------|------|")
    for key, label, source in citation_metrics:
        val = metrics.get(key)
        th = DEFAULT_THRESHOLDS.get(key)
        if val is None:
            lines.append(f"| {label} | — | {th} | {source} | ⬜ 无数据 |")
        else:
            passed = val >= th
            icon = "✅" if passed else "❌"
            lines.append(f"| {label} | **{val:.4f}** | {th} | {source} | {icon} |")
    lines.append("")

    # ── 7. 按问题类型分析 ──
    lines.append("## 7. 按问题类型分析")
    lines.append("")
    if qt_stats:
        lines.append("| 题型 | 用例数 | 通过 | 通过率 | 语义召回 |")
        lines.append("|------|--------|------|--------|----------|")
        for qt in sorted(qt_stats.keys()):
            s = qt_stats[qt]
            sem = f"{s['sem_context_recall']:.4f}" if s.get("sem_context_recall") is not None else "—"
            lines.append(f"| {qt} | {s['total']} | {s['passed']} | {s['pass_rate']:.1%} | {sem} |")
        lines.append("")
    else:
        lines.append("_无 query_type 数据_")
        lines.append("")

    # ── 8. 失败 Case 分析 ──
    lines.append("## 8. 失败 Case 分析")
    lines.append("")
    fail_results = [r for r in report.results if r.status == "fail"]
    error_results = [r for r in report.results if r.status == "error"]
    if fail_results or error_results:
        def _classify_failure(r):
            if r.status == "error":
                return "执行错误"
            retrieved = r.actual.get("retrieved_docs", []) or []
            if not retrieved:
                return "空召回"
            sem_recall_val = (r.metrics or {}).get("sem_context_recall", 0)
            if sem_recall_val < 0.5:
                return "语义召回不足"
            exp_docs = (r.expected or {}).get("relevant_docs", []) or []
            if exp_docs and retrieved[0] not in exp_docs:
                return "Top-1 错误"
            return "其他"

        fail_groups = Counter(_classify_failure(r) for r in fail_results + error_results)
        lines.append("**失败分类汇总**:")
        lines.append("")
        lines.append("| 类型 | 数量 | 占比 |")
        lines.append("|------|------|------|")
        total_fails = max(len(fail_results) + len(error_results), 1)
        for cat in ["语义召回不足", "Top-1 错误", "空召回", "执行错误", "其他"]:
            n = fail_groups.get(cat, 0)
            if n:
                lines.append(f"| {cat} | {n} | {n / total_fails * 100:.0f}% |")
        lines.append("")

        lines.append("**失败明细**:")
        lines.append("")
        lines.append("| Case | 问题 | 期望来源 | 实际 Top-1 | 分类 | 关键指标 |")
        lines.append("|------|------|----------|------------|------|----------|")
        for r in fail_results + error_results:
            q = (r.actual.get("question", "") or "")[:30]
            gt_ctx = (r.expected or {}).get("ground_truth_context", [])
            gt_label = gt_ctx[0].get("source_doc", "—")[:25] if gt_ctx else "—"
            rd = r.actual.get("retrieved_docs", []) or []
            top1 = rd[0][:25] if rd else ("ERR" if r.status == "error" else "empty")
            cat = _classify_failure(r)
            sem_r = (r.metrics or {}).get("sem_context_recall")
            sem_f = (r.metrics or {}).get("sem_faithfulness")
            parts = []
            if sem_r is not None:
                parts.append(f"recall={sem_r:.2f}")
            if sem_f is not None:
                parts.append(f"faith={sem_f:.2f}")
            metric_str = " ".join(parts) if parts else "—"
            lines.append(f"| {r.case_id} | {q} | {gt_label} | {top1} | {cat} | {metric_str} |")
        lines.append("")
    else:
        lines.append("✅ 全部用例通过，无失败/错误。")
        lines.append("")

    # ── 9. 回归对比 ──
    lines.append("## 9. 回归对比")
    lines.append("")
    if baseline_metrics:
        from .builder import compute_regression_diff
        diff_rows = compute_regression_diff(metrics, baseline_metrics)
        if diff_rows:
            lines.append("| 指标 | 当前 | 基线 | 差值 | 趋势 |")
            lines.append("|------|------|------|------|------|")
            for dr in diff_rows:
                trend = "📈 提升" if dr["improved"] else ("📉 下降" if dr["delta"] != 0 else "➡️ 持平")
                lines.append(f"| {dr['label']} | {dr['current']:.4f} | {dr['baseline']:.4f} | {dr['delta']:+.4f} | {trend} |")
            lines.append("")
        else:
            lines.append("_无共同指标可对比_")
            lines.append("")
    else:
        lines.append("_无基线数据，跳过回归对比。使用 `--promote-baseline` 保存当前结果为基线。_")
        lines.append("")

    # ── 10. RAGAS 指标 ──
    lines.append("## 10. RAGAS 指标")
    lines.append("")
    ragas_metrics_list = [
        ("ragas_context_recall", "Context Recall"),
        ("ragas_context_precision", "Context Precision"),
        ("ragas_faithfulness", "Faithfulness"),
        ("ragas_answer_relevancy", "Answer Relevancy"),
        ("ragas_answer_correctness", "Answer Correctness"),
    ]
    lines.append("| 指标 | 数值 | 阈值 | 状态 |")
    lines.append("|------|------|------|------|")
    for key, label in ragas_metrics_list:
        val = metrics.get(key)
        th = DEFAULT_THRESHOLDS.get(key)
        if val is None:
            lines.append(f"| {label} | — (离线模式) | {th} | ⬜ |")
        else:
            passed = val >= th
            icon = "✅" if passed else "❌"
            lines.append(f"| {label} | **{val:.4f}** | {th} | {icon} |")
    lines.append("")
    lines.append("_RAGAS 的 LLM-as-Judge 使用 `eval_gen` 角色绑定的模型；未配置或离线时指标为 NaN。_")
    lines.append("")

    # ── 11. 双轨对比 ──
    COMPARISON_MAP = {
        "检索准确率": {"self": "recall@10", "ragas": "ragas_context_recall"},
        "排序质量":   {"self": "ndcg@10",  "ragas": "ragas_context_precision"},
        "生成忠实度": {"self": None,        "ragas": "ragas_faithfulness"},
        "答案相关性": {"self": None,        "ragas": "ragas_answer_relevancy"},
    }
    lines.append("## 11. 双轨对比")
    lines.append("")
    lines.append("| 评测维度 | 自研指标（传统） | RAGAS 指标（LLM-as-Judge） |")
    lines.append("|----------|------------------|---------------------------|")
    for dim, keys in COMPARISON_MAP.items():
        self_key = keys["self"]
        ragas_key = keys["ragas"]
        if self_key and self_key in metrics:
            self_label = f"{self_key}: {metrics[self_key]:.4f}"
        elif self_key:
            self_label = "—"
        else:
            self_label = "—"
        if ragas_key and ragas_key in metrics:
            ragas_label = f"{ragas_key.replace('ragas_', '')}: {metrics[ragas_key]:.4f}"
        else:
            ragas_label = "—"
        lines.append(f"| {dim} | {self_label} | {ragas_label} |")
    lines.append("")

    # ── 12. 性能指标 ──
    lines.append("## 12. 性能指标")
    lines.append("")
    if perf:
        lines.append("### 延迟")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|------|----|")
        lines.append(f"| P50 延迟 | {perf['p50_ms']} ms |")
        lines.append(f"| P95 延迟 | {perf['p95_ms']} ms |")
        lines.append(f"| 平均延迟 | {perf['avg_ms']} ms |")
        lines.append(f"| 统计用例数 | {perf['total_cases']} |")
        lines.append("")

        if perf.get("total_tokens"):
            lines.append("### Token / 成本")
            lines.append("")
            lines.append("| 指标 | 值 |")
            lines.append("|------|----|")
            lines.append(f"| 平均输入 Token | {perf['avg_prompt_tokens']:,.0f} |")
            lines.append(f"| 平均输出 Token | {perf['avg_completion_tokens']:,.0f} |")
            lines.append(f"| 平均总 Token | {perf['avg_total_tokens']:,.0f} |")
            lines.append(f"| 最大 Token (单题) | {perf['max_total_tokens']:,} |")
            lines.append(f"| 总输入 Token | {perf['total_prompt_tokens']:,} |")
            lines.append(f"| 总输出 Token | {perf['total_completion_tokens']:,} |")
            lines.append(f"| 总 Token | {perf['total_tokens']:,} |")
            lines.append(f"| 有 Token 数据的用例 | {perf['cases_with_tokens']}/{len(report.results)} |")
            lines.append(f"| 估算成本 (API) | ¥{perf['estimated_cost_cny']:.4f} |")
            lines.append("")
            lines.append("_成本基于 DashScope qwen-plus 参考价：输入 ¥0.004/1K tokens，输出 ¥0.012/1K tokens。_")
            lines.append("")
    else:
        lines.append("_无性能数据_")
        lines.append("")

    # ── 13. 发布门禁清单 ──
    lines.append("## 13. 发布门禁清单")
    lines.append("")
    lines.append("| 检查项 | 状态 |")
    lines.append("|--------|------|")
    lines.append(f"| 数据集 Schema 合法 | {'✅' if dataset_val['schema_valid'] else '❌'} |")
    lines.append(f"| 通过率 ≥ 90% | {'✅' if rag_summary and rag_summary.pass_rate >= 0.9 else '❌'} |")
    for it in gate["items"]:
        if it["passed"] is None:
            lines.append(f"| {it['label']} ≥ {it['threshold']} | ⬜ 无数据 |")
        else:
            lines.append(f"| {it['label']} {'≥' if it['metric'] not in LOWER_IS_BETTER else '≤'} {it['threshold']} | {'✅' if it['passed'] else '❌'} |")
    lines.append(f"| 无执行错误 | {'✅' if err_count == 0 else '❌'} |")
    lines.append("")

    # ── 14. 下一步建议 ──
    lines.append("## 14. 下一步建议")
    lines.append("")
    suggestions: list[str] = []
    if failed_metrics:
        for it in failed_metrics:
            if "recall" in it["metric"]:
                suggestions.append(f"- **{it['label']}** 未达标：优化检索策略（调整 chunk_size、top_k、reranker 权重）")
            elif "faithfulness" in it["metric"]:
                suggestions.append(f"- **{it['label']}** 未达标：优化生成 prompt，减少推理跳跃")
            elif "reject" in it["metric"] or "false_answer" in it["metric"]:
                suggestions.append(f"- **{it['label']}** 未达标：调整拒答阈值（confidence threshold）")
            elif "citation" in it["metric"]:
                suggestions.append(f"- **{it['label']}** 未达标：在生成 prompt 中强化引用标记要求")
            elif "fact_coverage" in it["metric"]:
                suggestions.append(f"- **{it['label']}** 未达标：增加上下文窗口或优化 chunk 切分")
            elif "multi_hop" in it["metric"]:
                suggestions.append(f"- **{it['label']}** 未达标：增强多文档关联检索能力")
            else:
                suggestions.append(f"- **{it['label']}** 未达标：需针对性优化")
    if err_count > 0:
        suggestions.append(f"- 修复 {err_count} 个执行错误用例")
    if not dataset_val["schema_valid"]:
        suggestions.append(f"- 修复数据集重复 ID: {', '.join(dataset_val['duplicate_ids'][:5])}")
    if not suggestions:
        suggestions.append("- ✅ 所有指标达标，可考虑提升基线阈值以持续改进")
    lines.extend(suggestions)
    lines.append("")

    # ── 15. Machine-readable Summary ──
    lines.append("## 15. Machine-readable Summary")
    lines.append("")
    summary_json = {
        "verdict": "PASS" if gate["overall"] else "FAIL",
        "timestamp": report.timestamp,
        "module": report.module,
        "mode": report.mode,
        "total_cases": len(report.results),
        "pass_rate": rag_summary.pass_rate if rag_summary else 0,
        "gate_metrics": {
            it["metric"]: {
                "value": it["value"],
                "threshold": it["threshold"],
                "passed": it["passed"],
                "source": it["source"],
            }
            for it in gate["items"]
        },
        "dataset_validation": {
            "total": dataset_val["total_cases"],
            "schema_valid": dataset_val["schema_valid"],
            "reject_cases": dataset_val["reject_cases"],
        },
        "performance": perf,
    }
    lines.append("```json")
    lines.append(json.dumps(summary_json, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Release Gate 报告已保存到: {path}")
    return path
