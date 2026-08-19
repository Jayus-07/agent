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

    # === Dashboard 摘要块（v2 增强：可视化大字号指标，首屏可见） ===
    # 位置：头部信息之后、模块详情之前。一眼能看到核心数字。
    lines.append("## 📊 核心指标 Dashboard")
    lines.append("")
    lines.append("| 指标 | 数值 | 含义 | 状态 |")
    lines.append("|------|------|------|------|")
    rag_summary = next((s for s in report.summaries if s.module == "rag"), None)
    if rag_summary:
        m = rag_summary.metrics or {}
        pr = rag_summary.pass_rate
        pr_icon = "✅" if pr >= 0.9 else "⚠️" if pr >= 0.7 else "❌"
        lines.append(f"| 通过率 | **{pr:.1%}** ({rag_summary.passed}/{rag_summary.total}) | 召回含期望 doc | {pr_icon} |")
        top1 = m.get("top1_accuracy")
        if top1 is not None:
            top1_icon = "✅" if top1 >= 0.85 else "⚠️" if top1 >= 0.65 else "❌"
            lines.append(f"| **Top-1 准确率** | **{top1:.1%}** | 用户看到的第1条是不是对的 | {top1_icon} |")
        rej = m.get("reject_accuracy")
        if rej is not None:
            rej_icon = "✅" if rej >= 0.85 else "⚠️" if rej >= 0.65 else "❌"
            lines.append(f"| **拒答准确率** | **{rej:.1%}** | negative case 拒答正确率 | {rej_icon} |")
        mrr = m.get("mrr")
        if mrr is not None:
            lines.append(f"| MRR | {mrr:.1%} | 第1命中位置倒数平均 | {'✅' if mrr >= 0.8 else '⚠️'} |")
        ndcg = m.get("ndcg@10")
        if ndcg is not None:
            lines.append(f"| NDCG@10 | {ndcg:.1%} | 排序质量 | {'✅' if ndcg >= 0.8 else '⚠️'} |")
        recall5 = m.get("recall@5")
        if recall5 is not None:
            lines.append(f"| recall@5 | {recall5:.1%} | Top-5 召回覆盖率 | {'✅' if recall5 >= 0.9 else '⚠️'} |")
    lines.append("")
    lines.append("> 状态阈值：✅ ≥85%（生产）/ ⚠️ 65~85%（公测）/ ❌ <65%（内测）")
    lines.append("")
    lines.append("---")
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

    # 失败/错误详情（v2 增强：按错误类型分组 + 表格）
    lines.append("## 失败与错误详情")
    lines.append("")
    fail_results = [r for r in report.results if r.status == "fail"]
    error_results = [r for r in report.results if r.status == "error"]
    if fail_results or error_results:
        # 按错误类型分组（chunk_id 错 / empty / snippet 不匹配 / 等）
        def _classify_failure(r):
            """按 expected 字段分类失败原因。"""
            exp = r.expected or {}
            if r.status == "error":
                return "执行错误"
            retrieved = r.actual.get("retrieved_docs", []) or []
            if not retrieved:
                return "空召回"
            exp_docs = exp.get("relevant_docs", []) or []
            if exp_docs and retrieved[0] not in exp_docs:
                return "Top-1 错"
            return "其他"

        from collections import Counter
        fail_groups = Counter(_classify_failure(r) for r in fail_results)
        lines.append("**失败分类汇总**（按根因）：")
        lines.append("")
        lines.append("| 类型 | 数量 | 占比 |")
        lines.append("|------|------|------|")
        total_fails = max(len(fail_results), 1)
        for cat in ["Top-1 错", "空召回", "执行错误", "其他"]:
            n = fail_groups.get(cat, 0)
            if n:
                pct = n / total_fails * 100
                lines.append(f"| {cat} | {n} | {pct:.0f}% |")
        lines.append("")

        # 失败 case 表格（一眼能扫）
        lines.append('**失败 case 明细**（点击下方【过程详情】章节展开）：')
        lines.append("")
        lines.append("| Case | 期望 doc | Top-1 召回 | 分类 |")
        lines.append("|------|----------|------------|------|")
        for r in fail_results + error_results:
            exp_docs = (r.expected or {}).get("relevant_docs", [])
            rd = r.actual.get("retrieved_docs", []) or []
            exp_str = exp_docs[0] if exp_docs else "—"
            top1 = rd[0] if rd else "empty"
            if r.status == "error":
                cat = "执行错误"
                top1 = f"⚠️ {r.error_msg[:30] if r.error_msg else 'error'}"
            else:
                cat = _classify_failure(r)
            lines.append(f"| **{r.case_id}** | `{exp_str}` | `{top1}` | {cat} |")
        lines.append("")
    else:
        lines.append("✅ 全部用例通过，无失败/错误详情。")
        lines.append("")

    # === Dashboard 已在头部渲染，此处跳过（避免重复） ===

    # === v2: 3 段式报告 — 过程细节 / 结果 / 是否拒答 ===
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


def write_html_report(report: EvalReport, output_dir: Path) -> Path:
    """生成 HTML Dashboard 报告（自包含 CSS/JS，浏览器直接打开）。

    V2.0 设计：
    - 顶部 4 个大数字卡片（通过率 / Top-1 / 拒答 / MRR）
    - 失败 case 按根因分类（一眼看出问题在哪）
    - per-case 详情用 <details> 折叠（避免长报告滚动）
    - 颜色编码（pass=绿 / fail=红 / reject=黄）
    """
    import html as _html

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.html"
    module_zh = MODULE_LABELS.get(report.module, report.module)

    # === 核心指标计算 ===
    rag_summary = next((s for s in report.summaries if s.module == "rag"), None)
    metrics_dict = rag_summary.metrics if rag_summary else {}

    def _card(icon, label, value, status, hint=""):
        """单张大数字卡片。"""
        return f"""
        <div class="card card-{status}">
          <div class="card-icon">{icon}</div>
          <div class="card-label">{_html.escape(label)}</div>
          <div class="card-value">{value}</div>
          <div class="card-hint">{_html.escape(hint)}</div>
        </div>"""

    # 计算各指标 + 状态
    pr = rag_summary.pass_rate if rag_summary else 0
    top1 = metrics_dict.get("top1_accuracy")
    rej = metrics_dict.get("reject_accuracy")
    mrr = metrics_dict.get("mrr")

    def _status_icon(v, th_h=0.85, th_m=0.65):
        if v is None:
            return "none", "—"
        if v >= th_h:
            return "ok", "✅"
        if v >= th_m:
            return "warn", "⚠️"
        return "fail", "❌"

    pr_st, pr_icon = _status_icon(pr, 0.9, 0.7)
    top1_st, top1_icon = _status_icon(top1, 0.85, 0.65) if top1 is not None else ("none", "—")
    rej_st, rej_icon = _status_icon(rej, 0.85, 0.65) if rej is not None else ("none", "—")
    mrr_st, mrr_icon = _status_icon(mrr, 0.8, 0.5) if mrr is not None else ("none", "—")

    cards_html = (
        _card("📋", "通过率", f"{pr:.1%} ({rag_summary.passed}/{rag_summary.total})" if rag_summary else "—", pr_st, "recall@5") +
        _card("🎯", "Top-1 准确率", f"{top1:.1%}" if top1 is not None else "—", top1_st, "用户看到的第1条") +
        _card("🚫", "拒答准确率", f"{rej:.1%}" if rej is not None else "—", rej_st, "negative case") +
        _card("📈", "MRR", f"{mrr:.1%}" if mrr is not None else "—", mrr_st, "排序质量")
    )

    # === 失败 case 分类 ===
    fail_results = [r for r in report.results if r.status == "fail"]
    error_results = [r for r in report.results if r.status == "error"]

    def _classify_failure(r):
        exp = r.expected or {}
        if r.status == "error":
            return "执行错误"
        retrieved = r.actual.get("retrieved_docs", []) or []
        if not retrieved:
            return "空召回"
        exp_docs = exp.get("relevant_docs", []) or []
        if exp_docs and retrieved[0] not in exp_docs:
            return "Top-1 错"
        return "其他"

    from collections import Counter
    fail_groups = Counter(_classify_failure(r) for r in fail_results)
    error_count = len(error_results)

    if fail_results or error_results:
        group_rows = ""
        for cat in ["Top-1 错", "空召回", "执行错误", "其他"]:
            n = fail_groups.get(cat, 0)
            if n:
                group_rows += f'<tr><td>{cat}</td><td>{n}</td></tr>'

        fail_rows = ""
        for r in fail_results + error_results:
            exp_docs = (r.expected or {}).get("relevant_docs", [])
            rd = r.actual.get("retrieved_docs", []) or []
            exp_str = exp_docs[0] if exp_docs else "—"
            top1 = rd[0] if rd else ("⚠️ " + (r.error_msg[:30] if r.error_msg else "error") if r.status == "error" else "empty")
            cat = "执行错误" if r.status == "error" else _classify_failure(r)
            cls = "fail" if r.status == "fail" else "error"
            fail_rows += f'<tr class="{cls}"><td><b>{_html.escape(r.case_id)}</b></td><td><code>{_html.escape(exp_str)}</code></td><td><code>{_html.escape(top1)}</code></td><td>{_html.escape(cat)}</td></tr>'

        failure_section = f"""
        <h2>⚠️ 失败 case ({len(fail_results)} 失败 + {error_count} 错误)</h2>
        <h3>失败分类汇总</h3>
        <table class="stats">
          <tr><th>类型</th><th>数量</th></tr>
          {group_rows}
        </table>
        <h3>失败 case 明细</h3>
        <table class="cases">
          <tr><th>Case</th><th>期望 doc</th><th>Top-1 召回</th><th>分类</th></tr>
          {fail_rows}
        </table>"""
    else:
        failure_section = '<h2>✅ 全部用例通过，无失败</h2>'

    # === per-case 详情折叠面板 ===
    details_html = ""
    for r in report.results:
        status_class = r.status
        status_zh = STATUS_LABELS.get(r.status, r.status)
        status_icon = STATUS_ICONS.get(r.status, "?")
        question = r.actual.get("question", "")
        kb_id = r.actual.get("kb_id", "")
        metrics_str = ", ".join(
            f"{METRIC_LABELS.get(k, k)}={v}" for k, v in (r.metrics or {}).items()
        )

        # 1. 过程细节
        pipeline = r.actual.get("pipeline") or {}
        rejection = r.actual.get("rejection") or {}
        confidence = rejection.get("confidence", "?")
        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"}.get(confidence, "❔")

        # Top-5 召回证据
        details = r.actual.get("details", []) or []
        details_rows = ""
        for i, d in enumerate(details[:5], 1):
            doc_id = _html.escape(str(d.get("doc_id", "—")))
            chunk_id = _html.escape(str(d.get("chunk_id", "")))[:25]
            rs = d.get("rerank_score")
            rs_str = f"{rs:.4f}" if isinstance(rs, (int, float)) else "—"
            snippet = _html.escape(str(d.get("snippet", "")).replace("|", "\\|")[:120])
            details_rows += f'<tr><td>{i}</td><td><code>{doc_id}</code></td><td>{rs_str}</td><td>{snippet}</td></tr>'

        # 2. 结果
        exp = r.expected or {}
        expected_docs = exp.get("relevant_docs", []) or []
        retrieved_docs = r.actual.get("retrieved_docs", []) or []
        should_reject = exp.get("should_reject", False)

        if should_reject:
            result_block = f"""
            <p><b>类型</b>: 负样本（应拒答）</p>
            <p><b>期望文档</b>: <code>[]</code></p>
            <p><b>实际召回</b>: <code>{_html.escape(str(retrieved_docs[:5]))}</code></p>"""
        elif expected_docs:
            top1 = retrieved_docs[0] if retrieved_docs else "—"
            top1_hit = top1 in expected_docs if retrieved_docs else False
            top1_icon = "✅" if top1_hit else "❌"
            result_block = f"""
            <p><b>期望文档</b>: <code>{_html.escape(str(expected_docs))}</code></p>
            <p><b>Top-1</b>: <code>{_html.escape(top1)}</code> {top1_icon}</p>
            <p><b>实际召回 Top-5</b>: <code>{_html.escape(str(retrieved_docs[:5]))}</code></p>"""
        else:
            result_block = f'<p><b>期望</b>: <code>{_html.escape(str(expected_docs))}</code> · <b>召回</b>: <code>{_html.escape(str(retrieved_docs[:5]))}</code></p>'

        # 3. 拒答
        top1_rs = rejection.get("top1_rerank_score")
        rej_gate = rejection.get("reject_gate")
        rej_reason = rejection.get("reject_reason")
        rej_metric = (r.metrics or {}).get("reject_accuracy")
        reject_block = f"""
            <p><b>状态</b>: {conf_icon} <b>{confidence}</b></p>"""
        if top1_rs is not None:
            reject_block += f'<p><b>Top-1 rerank_score</b>: <code>{top1_rs:.4f}</code></p>'
        if rej_gate:
            reject_block += f'<p><b>拒答 gate</b>: <code>{_html.escape(rej_gate)}</code></p>'
        if rej_reason:
            reject_block += f'<p><b>拒答原因</b>: <code>{_html.escape(rej_reason)}</code></p>'
        if should_reject and rej_metric is not None:
            judge = "✅ 正确拒答" if rej_metric == 1.0 else "❌ 应该拒答却没拒"
            reject_block += f'<p><b>拒答判定</b>: {judge} (reject_accuracy={rej_metric})</p>'

        details_html += f"""
<details class="case case-{status_class}">
  <summary>{status_icon} <b>{_html.escape(r.case_id)}</b> — {status_zh} ({_html.escape(question[:40])})</summary>
  <div class="case-body">
    <p><b>KB</b>: <code>{_html.escape(kb_id)}</code> · <b>耗时</b>: {r.duration_ms}ms</p>
    <p><b>指标</b>: {_html.escape(metrics_str)}</p>
    {f'<p><b>错误</b>: <code>{_html.escape(r.error_msg)}</code></p>' if r.error_msg else ''}
    <h4>1. 过程细节</h4>
    {f'<p>Stage1={pipeline.get("stage1_docs","?")} / Stage2={pipeline.get("stage2_chunks_recalled","?")} / Adaptive={pipeline.get("adaptive","?")}</p>' if pipeline else ''}
    <table class="retrieve">
      <tr><th>#</th><th>doc_id</th><th>rerank</th><th>snippet</th></tr>
      {details_rows}
    </table>
    <h4>2. 结果</h4>
    {result_block}
    <h4>3. 是否拒答</h4>
    {reject_block}
  </div>
</details>"""

    # === HTML 主框架 ===
    mode_zh = "实时" if report.mode == "live" else "离线"
    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           margin: 0; background: #f5f6fa; color: #2d3142; }
    .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
    .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
              color: white; padding: 32px; border-radius: 12px; margin-bottom: 24px;
              box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    .header h1 { margin: 0 0 8px; font-size: 28px; }
    .header .meta { opacity: 0.9; font-size: 14px; }
    .dashboard { display: grid; grid-template-columns: repeat(4, 1fr);
                 gap: 16px; margin-bottom: 24px; }
    .card { background: white; padding: 20px; border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06); text-align: center; }
    .card-icon { font-size: 32px; margin-bottom: 8px; }
    .card-label { font-size: 13px; color: #6c757d; margin-bottom: 8px; }
    .card-value { font-size: 28px; font-weight: bold; margin-bottom: 4px; }
    .card-hint { font-size: 11px; color: #adb5bd; }
    .card-ok { border-top: 4px solid #10b9810; }
    .card-warn { border-top: 4px solid #f59e0b; }
    .card-fail { border-top: 4px solid #ef4444; }
    .card-none { border-top: 4px solid #cbd5e1; }
    h2 { color: #2d3142; border-bottom: 2px solid #667eea; padding-bottom: 8px; margin-top: 32px; }
    h3 { color: #4a5568; margin-top: 20px; }
    table { border-collapse: collapse; width: 100%; background: white;
           box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-radius: 6px; overflow: hidden; }
    th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #e9ecef; }
    th { background: #f8f9fa; font-weight: 600; color: #495057; font-size: 13px; }
    tr.fail { background: #fef2f2; }
    tr.fail:hover { background: #fee2e2; }
    tr.error { background: #fff3cd; }
    tr.error:hover { background: #ffe69c; }
    code { background: #f1f3f5; padding: 2px 6px; border-radius: 3px;
           font-family: 'Menlo', monospace; font-size: 12px; }
    details.case { background: white; border-radius: 8px; margin-bottom: 8px;
                   box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
    details.case[open] { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
    details.case summary { padding: 14px 18px; cursor: pointer;
                           font-size: 14px; user-select: none; }
    details.case summary:hover { background: #f8f9fa; }
    details.case summary b { color: #2d3142; }
    details.case-pass summary { border-left: 4px solid #10b9810; }
    details.case-fail summary { border-left: 4px solid #ef4444; background: #fef2f2; }
    details.case-error summary { border-left: 4px solid #f59e0b; background: #fff3cd; }
    .case-body { padding: 0 18px 18px; font-size: 13px; line-height: 1.7; }
    .case-body h4 { color: #667eea; margin-top: 16px; }
    table.retrieve { font-size: 12px; }
    table.retrieve th { background: #e0e7ff; }
    """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>评测报告 — {_html.escape(module_zh)}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 RAG 评测报告</h1>
    <div class="meta">{_html.escape(module_zh)} · {mode_zh} · {_html.escape(report.timestamp)}</div>
  </div>
  <div class="dashboard">
    {cards_html}
  </div>
  {failure_section}
  <h2>📂 per-case 详情（{len(report.results)} 条）</h2>
  {details_html}
</div>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
    print(f"HTML 报告已保存到: {path}")
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
