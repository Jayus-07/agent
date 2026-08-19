"""报告生成器 — 控制台摘要 + Markdown 详细报告 + 历史对比 + JSON 全量导出。

V1.0 更新：中文化输出（metric 标签/状态名/表格表头）。
机器字段（metric key、status enum）保持英文，确保 baseline JSON 对比稳定。
"""
import json
from pathlib import Path
from datetime import datetime
from backend.evaluation.models import EvalReport, ModuleSummary

# ============ V1.0 中文化映射 ============
# metric key（英文）→ 中文显示标签
METRIC_LABELS: dict[str, str] = {
    "recall@5": "召回率@5",
    "recall@10": "召回率@10",
    "recall@20": "召回率@20",
    "mrr": "平均倒数排名 (MRR)",
    "ndcg@5": "NDCG@5",
    "ndcg@10": "NDCG@10",
    "ndcg@20": "NDCG@20",
    "chunk_recall": "Chunk 级召回率",
    "chunk_recall@5": "Chunk 级召回率@5",
    "chunk_recall@10": "Chunk 级召回率@10",
    "top1_accuracy": "Top-1 准确率",
    "reject_accuracy": "拒答准确率",
    "routing_accuracy": "路由准确率",
    "syntax_valid": "语法合法率",
    "result_match": "结果集匹配率",
    "security_pass": "安全校验通过率",
    "judge_completeness": "Judge-完整性",
    "judge_faithfulness": "Judge-忠实性",
    "judge_conciseness": "Judge-简洁性",
    "judge_citation": "Judge-引用质量",
    "judge_total": "Judge-综合分",
    "judge_confidence": "Judge-置信度",
    "p95_latency_ms": "P95 响应时间 (ms)",
    "stability_variance": "稳定性方差",
    "reject_accuracy": "拒答准确率",
}

# 状态 enum → 中文显示
STATUS_LABELS: dict[str, str] = {
    "pass": "通过",
    "fail": "失败",
    "error": "错误",
    "skip": "跳过",
}

# 状态图标
STATUS_ICONS: dict[str, str] = {
    "pass": "✓",
    "fail": "✗",
    "error": "⚠",
    "skip": "○",
}

# 模块名 → 中文显示
MODULE_LABELS: dict[str, str] = {
    "rag": "RAG 检索",
    "e2e": "端到端",
    "sql": "SQL 查询",
    "planner": "任务规划",
}


def print_summary(report: EvalReport) -> None:
    """打印控制台摘要表格（中文）。"""
    mode_label = "实时 LLM 调用" if report.mode == "live" else "离线（仅检索）"
    module_zh = MODULE_LABELS.get(report.module, report.module)
    header = f"评估报告 — {module_zh} · {mode_label}"
    if report.smoke:
        header += " · 冒烟测试"
    print(f"\n{'='*64}")
    print(f"  {header}")
    print(f"  时间: {report.timestamp}")
    print(f"{'='*64}")

    for s in report.summaries:
        mod_zh = MODULE_LABELS.get(s.module, s.module)
        print(f"\n  【{mod_zh}】  通过率={s.pass_rate:.1%}  "
              f"({s.passed}/{s.total} 通过, {s.failed} 失败, {s.errors} 错误)")
        if s.metrics:
            for k, v in s.metrics.items():
                label = METRIC_LABELS.get(k, k)
                # 格式化值：浮点保留 4 位；int 直接输出
                if isinstance(v, float) and abs(v) <= 1.0:
                    val_str = f"{v:.4f}"
                else:
                    val_str = f"{v}"
                print(f"    {label}: {val_str}")

    if report.total_score is not None:
        print(f"\n  >>> 综合得分: {report.total_score:.2%} <<<")

    print(f"\n{'='*64}\n")


def write_markdown_report(report: EvalReport, output_dir: Path) -> Path:
    """生成 Markdown 详细报告（中文表头），保存到 output_dir，返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.md"
    module_zh = MODULE_LABELS.get(report.module, report.module)
    mode_zh = "实时" if report.mode == "live" else "离线"
    smoke_zh = "是" if report.smoke else "否"

    lines = [
        f"# 评估报告 — {module_zh}",
        f"",
        f"- **时间**: {report.timestamp}",
        f"- **模式**: {mode_zh}",
        f"- **冒烟测试**: {smoke_zh}",
        f"",
    ]

    if report.total_score is not None:
        lines.append(f"## 综合得分: {report.total_score:.2%}")
        lines.append("")

    for s in report.summaries:
        mod_zh = MODULE_LABELS.get(s.module, s.module)
        lines.append(f"### {mod_zh}")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 总数 | {s.total} |")
        lines.append(f"| 通过 | {s.passed} |")
        lines.append(f"| 失败 | {s.failed} |")
        lines.append(f"| 错误 | {s.errors} |")
        lines.append(f"| 跳过 | {s.skipped} |")
        lines.append(f"| **通过率** | **{s.pass_rate:.1%}** |")
        for k, v in s.metrics.items():
            label = METRIC_LABELS.get(k, k)
            lines.append(f"| {label} | {v} |")
        lines.append("")

    # 失败/错误详情
    lines.append("## 失败与错误详情")
    lines.append("")
    has_detail = False
    for r in report.results:
        if r.status in ("fail", "error"):
            status_zh = STATUS_LABELS.get(r.status, r.status)
            icon = STATUS_ICONS.get(r.status, "?")
            detail = r.error_msg or "指标: " + str(r.metrics)
            lines.append(f"- {icon} **{r.case_id}** [{status_zh}] — {detail}")
            has_detail = True
    if not has_detail:
        lines.append("✅ 全部用例通过，无失败/错误详情。")

    lines.append("")
    # === v2: 3 段式报告 — 过程细节 / 结果 / 是否拒答 ===
    lines.append("## 过程详情（每条用例）")
    lines.append("")
    lines.append(
        "> 每条用例按 **1.过程细节 → 2.结果 → 3.是否拒答** 三段式展开。"
        "拒答判定基于 runner 启发式 confidence（rerank_score 阈值 + gap），"
        "非 EvidenceGate 真值（EvidenceGate 仅在 chain.py 端到端链路生效）。"
    )
    lines.append("")
    for r in report.results:
        status_zh = STATUS_LABELS.get(r.status, r.status)
        icon = STATUS_ICONS.get(r.status, "?")
        lines.append(f"### {icon} {r.case_id} — {status_zh}")
        lines.append("")
        lines.append(f"**问题**: {r.actual.get('question', '')}")
        kb_id = r.actual.get("kb_id") or ""
        if kb_id:
            lines.append(f"**KB**: `{kb_id}`")
        if r.metrics:
            metrics_zh = ", ".join(
                f"{METRIC_LABELS.get(k, k)}={v}" for k, v in r.metrics.items()
            )
            lines.append(f"**指标**: {metrics_zh}")
        if r.duration_ms:
            lines.append(f"**总耗时**: {r.duration_ms} ms")
        if r.error_msg:
            lines.append(f"**错误**: `{r.error_msg}`")
        lines.append("")

        # ─────────────────────────────────────────────
        # 段 1: 过程细节（pipeline 概览 + span 树 + Top-5 chunk）
        # ─────────────────────────────────────────────
        lines.append("#### 1. 过程细节")
        lines.append("")
        pipeline = r.actual.get("pipeline") or {}
        if pipeline:
            lines.append("**Pipeline 概览**:")
            lines.append("")
            lines.append("| 阶段 | 值 |")
            lines.append("|------|----|")
            lines.append(f"| Stage1 召回数 | {pipeline.get('stage1_docs', '?')} |")
            fallback = pipeline.get("stage1_fallback_suspected")
            if fallback:
                lines.append("| Stage1 fallback | ⚠️ 已触发 |")
            lines.append(f"| Stage2 chunk 数 | {pipeline.get('stage2_chunks_recalled', '?')} |")
            lines.append(f"| Adaptive 决策 | {pipeline.get('adaptive', '?')} |")
            lines.append("")

        # 关键 span 树
        trace = r.actual.get("trace") or {}
        spans = trace.get("spans") or []
        if spans:
            lines.append("**关键 Span 树**:")
            lines.append("")
            lines.append(
                f"_trace_id=`{trace.get('trace_id', '?')}` · "
                f"共 {trace.get('total_spans', len(spans))} 个 span · "
                f"合计耗时 {trace.get('total_trace_ms', '?')} ms_"
            )
            lines.append("")
            lines.append("| Span | 类型 | 状态 | 耗时(ms) | Metrics 摘要 |")
            lines.append("|------|------|------|----------|-------------|")
            for sp in spans:
                name = sp.get("name") or sp.get("span_id")
                sp_type = sp.get("type", "")
                sp_status = sp.get("status", "")
                sp_ms = sp.get("duration_ms", 0)
                m = sp.get("metrics") or {}
                m_str = ", ".join(f"{k}={v}" for k, v in m.items()) if m else "—"
                if len(m_str) > 80:
                    m_str = m_str[:80] + "…"
                lines.append(f"| `{name}` | {sp_type} | {sp_status} | {sp_ms} | {m_str} |")
            lines.append("")

        # 召回证据 Top-5
        details = r.actual.get("details") or []
        if details:
            lines.append("**召回证据 Top-5**:")
            lines.append("")
            lines.append("| # | doc_id | chunk_id | rerank_score | snippet |")
            lines.append("|---|--------|----------|--------------|---------|")
            for i, d in enumerate(details[:5], 1):
                doc_id = d.get("doc_id") or "—"
                chunk_id = d.get("chunk_id") or "—"
                rs = d.get("rerank_score")
                rs_str = f"{rs:.4f}" if isinstance(rs, (int, float)) else "—"
                snippet = (d.get("snippet") or "").replace("|", "\\|").replace("\n", " ")
                if len(snippet) > 120:
                    snippet = snippet[:120] + "…"
                lines.append(f"| {i} | `{doc_id}` | `{chunk_id}` | {rs_str} | {snippet} |")
            lines.append("")

        # ─────────────────────────────────────────────
        # 段 2: 结果（期望 vs 实际 + Top-1 命中）
        # ─────────────────────────────────────────────
        lines.append("#### 2. 结果")
        lines.append("")
        expected = r.expected or {}
        expected_docs = expected.get("relevant_docs") or []
        retrieved_docs = r.actual.get("retrieved_docs") or []
        expected_snippets = expected.get("relevant_snippets") or []
        should_reject = expected.get("should_reject", False)

        if should_reject:
            lines.append(f"- **类型**: 负样本（应拒答）")
            lines.append(f"- **期望文档**: `[]`（无答案）")
            lines.append(f"- **实际召回**: `{retrieved_docs[:5] or '[]'}`")
            if not retrieved_docs:
                lines.append(f"- ✅ **结果**: 召回为空，上层直接拒答（理想路径）")
            else:
                # 召回非空时，看 reject_accuracy 判断 runner 启发式 confidence 是否触发拒答
                rej_metric = (r.metrics or {}).get("reject_accuracy")
                if rej_metric == 1.0:
                    lines.append(f"- ✅ **结果**: 召回非空但 confidence=low/none，触发 EvidenceGate 拒答")
                else:
                    lines.append(f"- ❌ **结果**: 召回非空且 confidence=high/medium，未拒答（应拒却没拒）")
        elif expected_docs:
            top1 = retrieved_docs[0] if retrieved_docs else "—"
            top1_hit = top1 in expected_docs if retrieved_docs else False
            hit_all = [d for d in retrieved_docs if d in expected_docs]
            lines.append(f"- **期望文档**: `{expected_docs}`")
            lines.append(f"- **期望 snippets**: `{expected_snippets}`")
            lines.append(f"- **Top-1**: `{top1}`  {'✅ 命中' if top1_hit else '❌ 未命中'}")
            lines.append(f"- **实际召回 Top-5**: `{retrieved_docs[:5]}`")
            if hit_all:
                lines.append(f"- ✅ **整体命中**: `{hit_all}`")
            else:
                lines.append(f"- ❌ **未命中任何期望文档**")
        else:
            lines.append(f"- 期望文档: `{expected_docs}`")
            lines.append(f"- 实际召回: `{retrieved_docs[:5]}`")
        lines.append("")

        # ─────────────────────────────────────────────
        # 段 3: 是否拒答（confidence + reject_gate + Top-1 score）
        # ─────────────────────────────────────────────
        lines.append("#### 3. 是否拒答")
        lines.append("")
        rejection = r.actual.get("rejection") or {}
        confidence = rejection.get("confidence", "?")
        reject_gate = rejection.get("reject_gate")
        reject_reason = rejection.get("reject_reason")
        top1_rs = rejection.get("top1_rerank_score")

        confidence_icon = {
            "high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"
        }.get(confidence, "❔")
        lines.append(f"- **状态**: {confidence_icon} **{confidence}**")
        if top1_rs is not None:
            lines.append(f"- **Top-1 rerank_score**: `{top1_rs:.4f}`")
        if reject_gate:
            lines.append(f"- **拒答 gate**: `{reject_gate}`")
        if reject_reason:
            lines.append(f"- **拒答原因**: `{reject_reason}`")
        if should_reject:
            rej_metric = (r.metrics or {}).get("reject_accuracy")
            if rej_metric is not None:
                judge = "✅ 正确拒答" if rej_metric == 1.0 else "❌ 应该拒答却没拒"
                lines.append(f"- **拒答判定**: {judge} (reject_accuracy={rej_metric})")
        lines.append("")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown 报告已保存到: {path}")
    return path


def write_json_report(report: EvalReport, output_dir: Path) -> Path:
    """生成 JSON 详细报告（含每条用例的完整检索轨迹），保存到 output_dir，返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.json"

    data = {
        "timestamp": report.timestamp,
        "module": report.module,
        "mode": report.mode,
        "smoke": report.smoke,
        "total_score": report.total_score,
        "summaries": [
            {
                "module": s.module,
                "total": s.total,
                "passed": s.passed,
                "failed": s.failed,
                "errors": s.errors,
                "skipped": s.skipped,
                "pass_rate": s.pass_rate,
                "metrics": s.metrics,
            }
            for s in report.summaries
        ],
        "results": [
            {
                "case_id": r.case_id,
                "module": r.module,
                "status": r.status,
                "expected": r.expected,
                "actual": r.actual,
                "metrics": r.metrics,
                "duration_ms": r.duration_ms,
                "error_msg": r.error_msg,
            }
            for r in report.results
        ],
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON report saved to: {path}")
    return path


def compare_reports(report_a: EvalReport, report_b: EvalReport) -> str:
    """比较两次报告，返回差异描述字符串（中文）。"""
    lines = ["## 报告对比", ""]
    lines.append(f"基线: {report_a.timestamp}  |  当前: {report_b.timestamp}")
    lines.append("")

    if report_a.total_score and report_b.total_score:
        delta = report_b.total_score - report_a.total_score
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        lines.append(f"**综合得分:** {report_a.total_score:.2%} → {report_b.total_score:.2%}  {arrow} {delta:+.2%}")

    lines.append("")
    lines.append("| 模块 | 基线 | 当前 | Δ |")
    lines.append("|------|------|------|---|")
    for sb in report_b.summaries:
        sa = next((s for s in report_a.summaries if s.module == sb.module), None)
        if sa:
            mod_zh = MODULE_LABELS.get(sb.module, sb.module)
            delta = sb.pass_rate - sa.pass_rate
            lines.append(f"| {mod_zh} | {sa.pass_rate:.1%} | {sb.pass_rate:.1%} | {delta:+.1%} |")

    result = "\n".join(lines)
    print(result)
    return result


def flag_regressions(
    base: EvalReport, current: EvalReport, threshold: float = 0.05,
) -> list[str]:
    """标记指标下降：当前报告 vs 基线报告，降幅超阈值返回告警列表（中文）。

    Args:
        base: 基线报告（历史）
        current: 当前报告
        threshold: 降幅阈值（默认 0.05，即 5%）

    Returns:
        告警字符串列表（空列表表示无显著下降）
    """
    warnings: list[str] = []
    for cb in current.summaries:
        bb = next((s for s in base.summaries if s.module == cb.module), None)
        if bb is None:
            continue
        mod_zh = MODULE_LABELS.get(cb.module, cb.module)
        # 对比每个指标
        for key, cur_val in cb.metrics.items():
            base_val = bb.metrics.get(key)
            if base_val is None:
                continue
            delta = cur_val - base_val
            if delta < -threshold:
                label = METRIC_LABELS.get(key, key)
                warnings.append(
                    f"⚠️  {mod_zh}.{label}: {base_val:.4f} → {cur_val:.4f} "
                    f"(↓{abs(delta):.4f})"
                )
        # 对比 pass_rate
        delta = cb.pass_rate - bb.pass_rate
        if delta < -threshold:
            warnings.append(
                f"⚠️  {mod_zh}.通过率: {bb.pass_rate:.1%} → "
                f"{cb.pass_rate:.1%} (↓{abs(delta):.1%})"
            )
    return warnings
