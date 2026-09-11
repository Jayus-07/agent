"""报告常量 + 控制台摘要 + 帕累托分析 + 发布门控。"""
from collections import Counter
from typing import Any

from backend.evaluation.models import EvalReport

# ============ V1.0 中文化映射 ============
METRIC_LABELS: dict[str, str] = {
    # ── 检索：精确匹配（硬） ──
    "recall@5": "召回率@5",
    "recall@10": "召回率@10",
    "recall@20": "召回率@20",
    "precision@5": "精确率@5 (精确ID)",
    "precision@10": "精确率@10 (精确ID)",
    "context_noise@10": "上下文噪声率@10 (精确ID)",
    "mrr": "平均倒数排名 (MRR)",
    "ndcg@5": "NDCG@5",
    "ndcg@10": "NDCG@10",
    "ndcg@20": "NDCG@20",
    "chunk_recall": "Chunk 级召回率",
    "chunk_recall@5": "Chunk 级召回率@5",
    "chunk_recall@10": "Chunk 级召回率@10",
    "top1_accuracy": "Top-1 准确率 (精确ID)",
    # ── 检索：语义匹配（软） ──
    "sem_context_recall": "语义上下文召回",
    "sem_context_recall_soft": "语义上下文召回(软分)",
    "sem_context_precision": "语义上下文精确率",
    "sem_chunk_recall@1": "语义 Chunk 召回@1",
    "sem_chunk_recall@3": "语义 Chunk 召回@3",
    "sem_chunk_recall@5": "语义 Chunk 召回@5",
    "sem_top1": "语义 Top-1 命中",
    # ── 生成质量 ──
    "sem_faithfulness": "忠实度(语义)",
    "sem_hallucination_rate": "幻觉率",
    "sem_answer_correctness": "答案正确性(语义)",
    "sem_answer_similarity": "答案相似度(语义)",
    "sem_claim_count": "声明数",
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
    # ── 性能 ──
    "p95_latency_ms": "P95 响应时间 (ms)",
    "stability_variance": "稳定性方差",
    "total_prompt_tokens": "Prompt Token 消耗",
    "total_completion_tokens": "Completion Token 消耗",
    # ── 自研业务指标 ──
    "required_fact_coverage": "事实覆盖率 (自研)",
    "false_answer_rate": "误答率 (自研)",
    "citation_accuracy": "引用准确度 (自研)",
    "citation_completeness": "引用完整度 (自研)",
    "multi_hop_success": "多跳成功率 (自研)",
    # ── RAGAS 官方（LLM-as-Judge） ──
    "ragas_context_recall": "[RAGAS] 上下文召回率",
    "ragas_context_precision": "[RAGAS] 上下文精确度",
    "ragas_faithfulness": "[RAGAS] 忠实度",
    "ragas_answer_relevancy": "[RAGAS] 答案相关性",
    "ragas_answer_correctness": "[RAGAS] 答案正确性",
}

STATUS_LABELS: dict[str, str] = {
    "pass": "通过",
    "fail": "失败",
    "error": "错误",
    "skip": "跳过",
}

STATUS_ICONS: dict[str, str] = {
    "pass": "✓",
    "fail": "✗",
    "error": "⚠",
    "skip": "○",
}

MODULE_LABELS: dict[str, str] = {
    "rag": "RAG 检索",
    "planner": "任务规划",
    "sql": "SQL 查询",
    "e2e": "Graph 全链路",
}

# ============ 指标分层分类（双轨架构） ============
METRIC_LAYERS: dict[str, list[str]] = {
    "检索质量": [
        "recall@5", "recall@10", "recall@20",
        "precision@5", "precision@10",
        "mrr", "ndcg@5", "ndcg@10", "ndcg@20",
        "chunk_recall", "chunk_recall@5", "chunk_recall@10",
        "top1_accuracy",
        "sem_top1",
    ],
    "上下文质量": [
        "context_noise@10",
        "sem_context_recall", "sem_context_recall_soft",
        "sem_context_precision",
        "sem_chunk_recall@1", "sem_chunk_recall@3", "sem_chunk_recall@5",
        "ragas_context_recall", "ragas_context_precision",
    ],
    "生成质量": [
        "sem_faithfulness", "sem_hallucination_rate",
        "sem_answer_correctness", "sem_answer_similarity", "sem_claim_count",
        "gen_S6_answer_correctness", "gen_must_contain_hit",
        "gen_must_not_contain_violation",
        "gen_supported_claim_count", "gen_claim_count",
        "S6_answer_correctness", "S7_faithfulness",
        "must_contain_hit", "must_not_contain_violation",
        "claim_count", "supported_claim_count",
        "required_fact_coverage",
        "ragas_faithfulness", "ragas_answer_relevancy", "ragas_answer_correctness",
    ],
    "安全与拒答": [
        "reject_accuracy", "dept_leak", "false_answer_rate",
        "security_pass", "routing_accuracy",
    ],
    "引用质量": [
        "citation_accuracy", "citation_completeness",
    ],
    "多跳/复杂": [
        "multi_hop_success",
    ],
    "性能与稳定性": [
        "p95_latency_ms", "stability_variance",
        "total_prompt_tokens", "total_completion_tokens",
    ],
}

_LAYER_ORDER = ["检索质量", "上下文质量", "生成质量", "安全与拒答", "引用质量", "多跳/复杂", "性能与稳定性"]


def _categorize_metric(key: str) -> str:
    """返回指标所属层级名称，未分类的返回 '其他'。"""
    for layer, keys in METRIC_LAYERS.items():
        if key in keys:
            return layer
    if key.startswith("gen_") or key.startswith("S"):
        return "生成质量"
    if key.startswith("sem_context") or key.startswith("sem_chunk_recall"):
        return "上下文质量"
    if key.startswith("sem_") or key.startswith("doc_"):
        return "检索质量"
    if key.startswith("citation_"):
        return "引用质量"
    if key.startswith("multi_hop"):
        return "多跳/复杂"
    if key.startswith("false_answer") or key.startswith("required_fact"):
        return "生成质量" if key.startswith("required_fact") else "安全与拒答"
    if key.startswith("ragas_"):
        if key in ("ragas_context_recall", "ragas_context_precision"):
            return "上下文质量"
        return "生成质量"
    if key == "context_noise@10":
        return "上下文质量"
    if key in ("precision@5", "precision@10"):
        return "检索质量"
    return "其他"


def print_summary(report: EvalReport) -> None:
    """打印控制台摘要表格（中文）。"""
    mode_label = "实时 LLM 调用" if report.mode == "live" else "离线（不调 LLM）"
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
            grouped: dict[str, list[tuple[str, float]]] = {}
            for k, v in s.metrics.items():
                layer = _categorize_metric(k)
                grouped.setdefault(layer, []).append((k, v))
            for layer_name in _LAYER_ORDER:
                items = grouped.get(layer_name)
                if not items:
                    continue
                print(f"    ── {layer_name} ──")
                for k, v in items:
                    label = METRIC_LABELS.get(k, k)
                    if isinstance(v, float) and abs(v) <= 1.0:
                        val_str = f"{v:.4f}"
                    else:
                        val_str = f"{v}"
                    print(f"      {label}: {val_str}")
            other = grouped.get("其他", [])
            if other:
                print("    ── 其他 ──")
                for k, v in other:
                    label = METRIC_LABELS.get(k, k)
                    if isinstance(v, float) and abs(v) <= 1.0:
                        val_str = f"{v:.4f}"
                    else:
                        val_str = f"{v}"
                    print(f"      {label}: {val_str}")

    if report.total_score is not None:
        print(f"\n  >>> 综合得分: {report.total_score:.2%} <<<")

    if report.tier_summaries:
        print("\n  --- 分层评估 ---")
        for ts in report.tier_summaries:
            status = "✅" if ts.passed_threshold else "❌"
            print(
                f"  {status} [{ts.tier}]  通过率={ts.pass_rate:.1%}  "
                f"({ts.passed}/{ts.total})  阈值={ts.threshold:.1%}"
            )

    qt_stats = compute_query_type_stats(report.results)
    if qt_stats:
        print("\n  --- 按题型分组 ---")
        for qt, stats in sorted(qt_stats.items()):
            print(f"  [{qt}]  通过率={stats['pass_rate']:.1%}  "
                  f"({stats['passed']}/{stats['total']})"
                  + (f"  语义召回={stats['sem_context_recall']:.4f}" if stats.get("sem_context_recall") is not None else ""))

    print(f"\n{'='*64}\n")


def compute_query_type_stats(results: list[Any]) -> dict[str, dict[str, Any]]:
    """按 query_type 分组统计通过率和核心指标均值。"""
    from collections import defaultdict
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in results:
        qt = (r.actual or {}).get("pipeline", {}).get("query_type", "")
        if qt:
            groups[qt].append(r)
    if not groups:
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for qt, items in groups.items():
        total = len(items)
        passed = sum(1 for r in items if r.status == "pass")
        sem_vals = [r.metrics.get("sem_context_recall") for r in items if r.metrics.get("sem_context_recall") is not None]
        stats[qt] = {
            "total": total,
            "passed": passed,
            "pass_rate": passed / total if total else 0.0,
            "sem_context_recall": sum(sem_vals) / len(sem_vals) if sem_vals else None,
        }
    return stats


def compute_reject_hallucination_pareto(
    results: list[Any],
) -> list[dict[str, float]]:
    """拒答阈值 × 幻觉率帕累托分析。"""
    confidence_order = {"none": 0, "low": 1, "medium": 2, "high": 3}

    thresholds = [
        ("仅 none", {"none"}),
        ("none + low（当前）", {"none", "low"}),
        ("none + low + medium", {"none", "low", "medium"}),
        ("全部拒答", {"none", "low", "medium", "high"}),
    ]

    rows: list[dict[str, float]] = []
    for label, reject_set in thresholds:
        should_reject_cases = []
        should_answer_cases = []
        for r in results:
            rej_info = (r.actual or {}).get("rejection", {})
            conf = rej_info.get("confidence", "high")
            is_reject = r.expected.get("should_reject", False)
            if is_reject:
                should_reject_cases.append((r, conf))
            else:
                should_answer_cases.append((r, conf))

        correct_rejects = sum(
            1 for _, c in should_reject_cases if c in reject_set
        )
        total_reject = len(should_reject_cases)
        reject_acc = correct_rejects / total_reject if total_reject else float("nan")

        false_rejects = sum(
            1 for _, c in should_answer_cases if c in reject_set
        )
        total_answer = len(should_answer_cases)
        false_reject_rate = false_rejects / total_answer if total_answer else float("nan")

        pass_through = [
            r for r, c in should_answer_cases if c not in reject_set
        ]
        hall_vals = [
            r.metrics.get("sem_hallucination_rate")
            for r in pass_through
            if r.metrics.get("sem_hallucination_rate") is not None
        ]
        hall_leak = (
            sum(hall_vals) / len(hall_vals) if hall_vals else float("nan")
        )

        rows.append({
            "threshold": label,
            "reject_set": sorted(reject_set, key=lambda x: confidence_order.get(x, 9)),
            "reject_accuracy": round(reject_acc, 4) if reject_acc == reject_acc else None,
            "hallucination_leak": round(hall_leak, 4) if hall_leak == hall_leak else None,
            "false_reject_rate": round(false_reject_rate, 4) if false_reject_rate == false_reject_rate else None,
            "pass_through_count": len(pass_through),
            "total_reject_cases": total_reject,
            "total_answer_cases": total_answer,
        })

    return rows


# ============ 发布门控阈值（V1.0） ============
DEFAULT_THRESHOLDS: dict[str, float] = {
    "recall@5": 0.80,
    "mrr": 0.75,
    "top1_accuracy": 0.80,
    "sem_context_recall": 0.50,
    "reject_accuracy": 0.85,
    "false_answer_rate": 0.10,
    "required_fact_coverage": 0.60,
    "citation_accuracy": 0.70,
    "citation_completeness": 0.60,
    "multi_hop_success": 0.50,
    "ragas_context_recall": 0.60,
    "ragas_context_precision": 0.60,
    "ragas_faithfulness": 0.70,
    "ragas_answer_relevancy": 0.70,
    "ragas_answer_correctness": 0.60,
}

LOWER_IS_BETTER: set[str] = {"false_answer_rate", "sem_hallucination_rate", "dept_leak", "context_noise@10"}

METRIC_SOURCE: dict[str, str] = {
    "recall@5": "自研", "recall@10": "自研", "mrr": "自研",
    "top1_accuracy": "自研", "ndcg@10": "自研",
    "sem_context_recall": "自研", "sem_context_precision": "自研",
    "sem_faithfulness": "自研", "sem_answer_correctness": "自研",
    "reject_accuracy": "自研", "false_answer_rate": "自研",
    "required_fact_coverage": "自研", "citation_accuracy": "自研",
    "citation_completeness": "自研", "multi_hop_success": "自研",
    "ragas_context_recall": "RAGAS", "ragas_context_precision": "RAGAS",
    "ragas_faithfulness": "RAGAS", "ragas_answer_relevancy": "RAGAS",
    "ragas_answer_correctness": "RAGAS",
}

GATE_METRICS = [
    ("recall@5", "检索"),
    ("mrr", "检索"),
    ("sem_context_recall", "检索"),
    ("ragas_context_recall", "检索"),
    ("ragas_context_precision", "检索"),
    ("ragas_faithfulness", "生成"),
    ("ragas_answer_correctness", "生成"),
    ("required_fact_coverage", "生成"),
    ("false_answer_rate", "安全"),
    ("reject_accuracy", "安全"),
    ("citation_accuracy", "引用"),
    ("multi_hop_success", "复杂"),
]


def compute_release_gate(
    metrics: dict[str, float],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """计算每个指标的 pass/fail 和总体发布决策。"""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    items: list[dict[str, Any]] = []
    all_pass = True
    for key, dimension in GATE_METRICS:
        val = metrics.get(key)
        threshold = th.get(key)
        if val is None or threshold is None:
            items.append({
                "metric": key, "dimension": dimension,
                "value": val, "threshold": threshold,
                "source": METRIC_SOURCE.get(key, "—"),
                "passed": None, "label": METRIC_LABELS.get(key, key),
            })
            continue
        if key in LOWER_IS_BETTER:
            passed = val <= threshold
        else:
            passed = val >= threshold
        if not passed:
            all_pass = False
        items.append({
            "metric": key, "dimension": dimension,
            "value": round(val, 4), "threshold": threshold,
            "source": METRIC_SOURCE.get(key, "—"),
            "passed": passed, "label": METRIC_LABELS.get(key, key),
        })
    return {"overall": all_pass, "items": items}


def compute_dataset_validation(report: EvalReport) -> dict[str, Any]:
    """验证数据集完整性：schema 检查、重复 ID、缺失字段。"""
    results = report.results
    case_ids = [r.case_id for r in results]
    dup_ids = [cid for cid, cnt in Counter(case_ids).items() if cnt > 1]

    missing_answer = 0
    missing_facts = 0
    missing_docs = 0
    reject_cases = 0
    for r in results:
        exp = r.expected or {}
        if not exp.get("expected_answer"):
            missing_answer += 1
        if not exp.get("should_reject") and not exp.get("required_facts"):
            missing_facts += 1
        if not exp.get("should_reject") and not exp.get("required_docs"):
            missing_docs += 1
        if exp.get("should_reject"):
            reject_cases += 1

    query_types = set()
    for r in results:
        qt = (r.actual or {}).get("pipeline", {}).get("query_type", "")
        if qt:
            query_types.add(qt)

    return {
        "total_cases": len(results),
        "unique_ids": len(set(case_ids)),
        "duplicate_ids": dup_ids,
        "missing_expected_answer": missing_answer,
        "missing_required_facts": missing_facts,
        "missing_required_docs": missing_docs,
        "reject_cases": reject_cases,
        "query_types_covered": sorted(query_types),
        "schema_valid": len(dup_ids) == 0,
    }


def compute_regression_diff(
    current_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
) -> list[dict[str, Any]]:
    """对比当前与基线的指标差异。"""
    rows: list[dict[str, Any]] = []
    all_keys = sorted(set(current_metrics) | set(baseline_metrics))
    for key in all_keys:
        cur = current_metrics.get(key)
        base = baseline_metrics.get(key)
        if cur is None or base is None:
            continue
        delta = cur - base
        if key in LOWER_IS_BETTER:
            improved = delta < 0
        else:
            improved = delta > 0
        rows.append({
            "metric": key,
            "label": METRIC_LABELS.get(key, key),
            "current": round(cur, 4),
            "baseline": round(base, 4),
            "delta": round(delta, 4),
            "improved": improved,
        })
    return rows


def compute_performance_stats(results: list[Any]) -> dict[str, Any]:
    """计算性能统计：P50/P95 延迟、token 消耗（总量/均值/最大值）、估算成本。"""
    durations = sorted(r.duration_ms for r in results if r.duration_ms)
    if not durations:
        return {}
    n = len(durations)
    p50 = durations[n // 2]
    p95 = durations[int(n * 0.95)] if n >= 20 else durations[-1]
    avg = sum(durations) / n

    prompt_tokens_list: list[int] = []
    completion_tokens_list: list[int] = []
    for r in results:
        trace = (r.actual or {}).get("trace", {})
        pt = trace.get("prompt_tokens", 0) or 0
        ct = trace.get("completion_tokens", 0) or 0
        prompt_tokens_list.append(pt)
        completion_tokens_list.append(ct)

    total_prompt = sum(prompt_tokens_list)
    total_completion = sum(completion_tokens_list)
    total_tokens = total_prompt + total_completion
    cases_with_tokens = sum(1 for p, c in zip(prompt_tokens_list, completion_tokens_list) if p or c)

    avg_prompt = round(total_prompt / cases_with_tokens, 1) if cases_with_tokens else 0
    avg_completion = round(total_completion / cases_with_tokens, 1) if cases_with_tokens else 0
    avg_total = round(total_tokens / cases_with_tokens, 1) if cases_with_tokens else 0
    max_total = max(
        (p + c for p, c in zip(prompt_tokens_list, completion_tokens_list)),
        default=0,
    )

    # 成本估算：per-case trace 的 token 全部来自本地 Ollama 推理（免费，不计费）。
    # 原按 DashScope qwen-plus 价格估算本地 token，方向性错误（本地高估、
    # 云端 RAGAS 调用反而没统计到）。云端成本看 token_summary（JSONL 按
    # run 时间窗过滤后的统计），此处仅保留本地推理量供参考。
    estimated_cost = 0.0

    return {
        "p50_ms": p50, "p95_ms": p95, "avg_ms": round(avg, 1),
        "total_cases": n,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "avg_prompt_tokens": avg_prompt,
        "avg_completion_tokens": avg_completion,
        "avg_total_tokens": avg_total,
        "max_total_tokens": max_total,
        "cases_with_tokens": cases_with_tokens,
        "local_inference_tokens": total_tokens,
        "estimated_cost_cny": estimated_cost,
    }
