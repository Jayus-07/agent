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
    # === 过程证据：每条用例的 trace 树 + 召回证据 ===
    # RAG 模块额外展开 span 树，便于定位"召回对了但排序差" / "rerank 分数异常"等
    lines.append("## 过程详情（每条用例）")
    lines.append("")
    lines.append(
        "> 每条用例展开 RAG 链路中实际产生的 span（含耗时、metrics、输入/输出摘要），"
        "以及召回的文档 chunk 与 rerank 分数。"
    )
    lines.append("")
    for r in report.results:
        status_zh = STATUS_LABELS.get(r.status, r.status)
        icon = STATUS_ICONS.get(r.status, "?")
        lines.append(f"### {icon} {r.case_id} — {status_zh}")
        lines.append("")
        lines.append(f"- **问题**: {r.actual.get('question', '')}")
        kb_id = r.actual.get("kb_id") or ""
        if kb_id:
            lines.append(f"- **KB**: `{kb_id}`")
        if r.metrics:
            metrics_zh = ", ".join(
                f"{METRIC_LABELS.get(k, k)}={v}" for k, v in r.metrics.items()
            )
            lines.append(f"- **指标**: {metrics_zh}")
        if r.duration_ms:
            lines.append(f"- **总耗时**: {r.duration_ms} ms")
        if r.error_msg:
            lines.append(f"- **错误**: `{r.error_msg}`")
        lines.append("")

        # Pipeline 概览
        pipeline = r.actual.get("pipeline") or {}
        if pipeline:
            lines.append("**Pipeline 概览**")
            lines.append("")
            lines.append("| 阶段 | 值 |")
            lines.append("|------|----|")
            lines.append(f"| Stage1 召回数 | {pipeline.get('stage1_docs', '?')} |")
            fallback = pipeline.get("stage1_fallback_suspected")
            if fallback:
                lines.append("| Stage1 fallback | ⚠️ 已触发（KB filter 无结果） |")
            lines.append(f"| Stage2 chunk 数 | {pipeline.get('stage2_chunks_recalled', '?')} |")
            lines.append(f"| Adaptive 决策 | {pipeline.get('adaptive', '?')} |")
            lines.append("")

        # Trace span 树
        trace = r.actual.get("trace") or {}
        spans = trace.get("spans") or []
        if spans:
            lines.append("**Trace Span 树**")
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
                # 把 dict metrics 压成一行 key=val, key=val
                m = sp.get("metrics") or {}
                m_str = ", ".join(f"{k}={v}" for k, v in m.items()) if m else "—"
                if len(m_str) > 80:
                    m_str = m_str[:80] + "…"
                lines.append(f"| `{name}` | {sp_type} | {sp_status} | {sp_ms} | {m_str} |")
            lines.append("")

            # 选取关键 span（retrieval / rerank / evidence_gate）的 input/output 摘要
            key_types = {"retrieval", "rerank", "retrieval_gate", "rerank_gate"}
            key_spans = [s for s in spans if s.get("type") in key_types]
            for sp in key_spans[:6]:  # 限 6 个，避免报告过长
                lines.append(f"**Span `{sp.get('name')}` ({sp.get('type')})**")
                lines.append("")
                has_any = False
                if sp.get("input"):
                    has_any = True
                    inp = sp["input"]
                    if isinstance(inp, dict):
                        inp_brief = {
                            k: (v if not isinstance(v, str) or len(v) < 200 else v[:200] + "…")
                            for k, v in inp.items()
                        }
                        lines.append("- 输入:")
                        lines.append("")
                        lines.append("```json")
                        lines.append(json.dumps(inp_brief, ensure_ascii=False, indent=2))
                        lines.append("```")
                if sp.get("output"):
                    has_any = True
                    out = sp["output"]
                    if isinstance(out, dict):
                        out_brief = {
                            k: (v if not isinstance(v, str) or len(v) < 300 else v[:300] + "…")
                            for k, v in out.items()
                        }
                        lines.append("- 输出:")
                        lines.append("")
                        lines.append("```json")
                        lines.append(json.dumps(out_brief, ensure_ascii=False, indent=2))
                        lines.append("```")
                events = sp.get("events") or []
                if events:
                    has_any = True
                    lines.append(f"- 事件 ({len(events)} 条):")
                    lines.append("")
                    for ev in events[:5]:
                        ev_name = ev.get("name", "?")
                        ev_level = ev.get("level", "")
                        ev_msg = ev.get("message", "")
                        if len(ev_msg) > 300:
                            ev_msg = ev_msg[:300] + "…"
                        lines.append(f"  - `{ev_name}` ({ev_level}): {ev_msg}")
                    if len(events) > 5:
                        lines.append(f"  - …另有 {len(events) - 5} 条事件")
                errors = sp.get("errors") or []
                if errors:
                    has_any = True
                    lines.append(f"- ❗ 错误:")
                    lines.append("")
                    lines.append("```json")
                    lines.append(json.dumps(errors, ensure_ascii=False, indent=2)[:1000])
                    lines.append("```")
                if not has_any:
                    lines.append("_（该 span 未记录 input/output/events，仅有 metrics）_")
                lines.append("")
            if len(key_spans) > 6:
                lines.append(f"_…另有 {len(key_spans) - 6} 个关键 span，已在 JSON 报告中保留全量数据_")
                lines.append("")

        # 召回证据：top-5 chunk + rerank 分数
        details = r.actual.get("details") or []
        if details:
            lines.append("**召回证据（Top 5）**")
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

        # 期望 vs 实际
        expected = r.expected or {}
        expected_docs = expected.get("relevant_docs") or []
        retrieved_docs = r.actual.get("retrieved_docs") or []
        if expected_docs or retrieved_docs:
            lines.append("**期望 vs 实际**")
            lines.append("")
            lines.append(f"- 期望文档: `{expected_docs}`")
            lines.append(f"- 实际召回: `{retrieved_docs[:10]}`")
            hit = [d for d in retrieved_docs if d in expected_docs]
            if hit:
                lines.append(f"- ✅ 命中: `{hit}`")
            elif expected_docs:
                lines.append(f"- ❌ 未命中")
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
