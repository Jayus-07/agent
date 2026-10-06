"""release_gate.py — 模块感知的发布门禁（P0-01）：三态 PASS/FAIL/INVALID + fail-closed。

为什么存在：旧 ``builder.compute_release_gate`` 用单一 RAG 指标表（GATE_METRICS）
判所有模块，且 ``None→continue`` fail-open——SQL run 没有任何 RAG 指标时全部
跳过，overall 恒 True，SQL 5/45 (11.1%) 也能 PASS（P0）。

本模块是发布门禁的唯一裁决出口，三条铁律：

1. **数据缺失不能假装成功**：required 指标缺失 / None / NaN / Inf → 该项 FAIL；
2. **环境故障不能假装质量失败**：run validity=INVALID → 整体 INVALID（不是 FAIL）；
3. **模块不适用不能假装缺指标**：本模块不适用的指标显式 N/A，不影响门禁。

裁决语义：
- PASS    系统正常执行，且全部必需质量指标达标
- FAIL    系统正常执行，但质量不达标（含 required 指标缺失）
- INVALID 环境/基础设施/预算等原因导致本轮不可评价（见 validity.py）
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from backend.evaluation.validity import (
    RunValidity,
    RunValidityVerdict,
    classify_report_validity,
    validity_from_report_metadata,
)

# ============ 指标展示常量（自 report/builder.py 原位迁入；builder 保留 re-export） ============

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

# 常用指标中文名（门禁项展示用；完整展示层标签表仍在 report/builder.METRIC_LABELS）
GATE_METRIC_LABELS: dict[str, str] = {
    "pass_rate": "通过率",
    "recall@5": "召回率@5",
    "mrr": "平均倒数排名 (MRR)",
    "top1_accuracy": "Top-1 准确率 (精确ID)",
    "ndcg@10": "NDCG@10",
    "sem_context_recall": "语义上下文召回",
    "reject_accuracy": "拒答准确率",
    "false_answer_rate": "误答率 (自研)",
    "required_fact_coverage": "事实覆盖率 (自研)",
    "citation_accuracy": "引用准确度 (自研)",
    "citation_completeness": "引用完整度 (自研)",
    "multi_hop_success": "多跳成功率 (自研)",
    "ragas_context_recall": "[RAGAS] 上下文召回率",
    "ragas_context_precision": "[RAGAS] 上下文精确度",
    "ragas_faithfulness": "[RAGAS] 忠实度",
    "ragas_answer_relevancy": "[RAGAS] 答案相关性",
    "ragas_answer_correctness": "[RAGAS] 答案正确性",
    "router_hit": "路由表命中率",
    "safety_pass": "安全校验通过率",
    "execution_match": "执行结果一致率",
}

# 门禁阈值（质量口径的唯一事实源；builder.DEFAULT_THRESHOLDS 迁入至此）
DEFAULT_THRESHOLDS: dict[str, float] = {
    "pass_rate": 0.95,
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
    "router_hit": 0.95,
    "safety_pass": 1.00,
    "execution_match": 0.80,
}

LOWER_IS_BETTER: set[str] = {
    "false_answer_rate", "sem_hallucination_rate", "dept_leak", "context_noise@10",
}

# 旧单一 RAG 指标表（2026-10-07 起弃用，仅为兼容保留；裁决走 MODULE_GATE_POLICIES）
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


class GateVerdict(str, Enum):
    """门禁三态。禁止再回落成 bool 单一语义。"""

    PASS = "PASS"
    FAIL = "FAIL"
    INVALID = "INVALID"


# ============ 模块门禁策略 ============


@dataclass(frozen=True)
class MetricRequirement:
    """单条门禁指标要求。

    threshold=None 时从 DEFAULT_THRESHOLDS（含调用方覆盖）解析；
    ragas_conditional=True：evaluator_mode 含 ragas 时为 required，否则 optional
    （RAGAS 分值只在启用 RAGAS 评估的 run 上作为门禁）。
    """

    metric: str
    dimension: str
    threshold: float | None = None
    direction: str = "higher"  # higher=越大越好 / lower=越小越好
    ragas_conditional: bool = False


@dataclass(frozen=True)
class ModuleGatePolicy:
    """单个模块的门禁策略。

    required：缺失/非有限/不达标任一 → FAIL（数据缺失不能假装成功）。
    optional：有值则判定，缺失 → N/A 不影响门禁。
    not_applicable_display：本模块明确不适用的指标，输出 N/A 项供 UI 展示。
    tier_gate：report.tier_summaries 里存在的层级必须全部达标（GATE-12 最低
    样本量一并判定）；模块未声明层级 → N/A（层级是否存在取决于数据集标注，
    缺层级本身不算数据完整性故障）。
    """

    module: str
    required: tuple[MetricRequirement, ...]
    optional: tuple[MetricRequirement, ...] = ()
    not_applicable_display: tuple[str, ...] = ()
    tier_gate: bool = False


def _req(metric: str, dimension: str, threshold: float | None = None,
         direction: str = "higher", ragas_conditional: bool = False) -> MetricRequirement:
    return MetricRequirement(metric, dimension, threshold, direction, ragas_conditional)


_RAG_POLICY = ModuleGatePolicy(
    module="rag",
    required=(
        _req("pass_rate", "通过率"),
        _req("recall@5", "检索"),
        _req("mrr", "检索"),
        _req("reject_accuracy", "安全"),
        _req("false_answer_rate", "安全", direction="lower"),
    ),
    optional=(
        _req("top1_accuracy", "检索"),
        _req("sem_context_recall", "检索"),
        _req("required_fact_coverage", "生成"),
        _req("citation_accuracy", "引用"),
        _req("citation_completeness", "引用"),
        _req("multi_hop_success", "复杂"),
        _req("ragas_context_recall", "检索", ragas_conditional=True),
        _req("ragas_context_precision", "检索", ragas_conditional=True),
        _req("ragas_faithfulness", "生成", ragas_conditional=True),
        _req("ragas_answer_relevancy", "生成", ragas_conditional=True),
        _req("ragas_answer_correctness", "生成", ragas_conditional=True),
    ),
    not_applicable_display=("router_hit", "safety_pass", "execution_match"),
    tier_gate=True,
)

_SQL_POLICY = ModuleGatePolicy(
    module="sql",
    required=(
        _req("pass_rate", "通过率"),
        _req("router_hit", "路由"),
        _req("safety_pass", "安全"),
        _req("execution_match", "执行"),
    ),
    not_applicable_display=(
        "recall@5", "mrr", "ndcg@10",
        "ragas_context_recall", "ragas_context_precision", "ragas_faithfulness",
        "citation_accuracy",
    ),
    tier_gate=True,
)

# cs/travel/planner/e2e 等：当前唯一共性质量口径 = 通过率（fail-closed）。
# 各域专属门禁指标随域验收体系补充，只改这里，不改裁决逻辑。
_PASS_RATE_ONLY = lambda m: ModuleGatePolicy(  # noqa: E731 — 简单构造器
    module=m,
    required=(_req("pass_rate", "通过率"),),
)

MODULE_GATE_POLICIES: dict[str, ModuleGatePolicy] = {
    "rag": _RAG_POLICY,
    "sql": _SQL_POLICY,
    "planner": _PASS_RATE_ONLY("planner"),
    "cs": _PASS_RATE_ONLY("cs"),
    "travel": _PASS_RATE_ONLY("travel"),
    "travel-provider": _PASS_RATE_ONLY("travel-provider"),
    "travel-commerce": _PASS_RATE_ONLY("travel-commerce"),
    "travel-booking": _PASS_RATE_ONLY("travel-booking"),
    "e2e": _PASS_RATE_ONLY("e2e"),
}

# 未知模块（含 future module）兜底：通过率 required，fail-closed。
DEFAULT_POLICY = _PASS_RATE_ONLY("*")


def policy_for(module: str) -> ModuleGatePolicy:
    return MODULE_GATE_POLICIES.get(module, DEFAULT_POLICY)


# ============ 裁决 ============


def _is_finite_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def _resolve_threshold(req: MetricRequirement, th: dict[str, float]) -> float | None:
    if req.threshold is not None:
        return float(req.threshold)
    v = th.get(req.metric)
    return float(v) if v is not None else None


def _evaluate_requirement(
    req: MetricRequirement,
    metrics: dict[str, Any],
    th: dict[str, float],
    *,
    required: bool,
) -> dict[str, Any]:
    """评估单条指标 → item dict。passed 三值：True/False/None(None=N/A)。"""
    item = {
        "metric": req.metric,
        "dimension": req.dimension,
        "requirement": "required" if required else "optional",
        "source": METRIC_SOURCE.get(req.metric, "—"),
        "label": GATE_METRIC_LABELS.get(req.metric, req.metric),
        "passed": None,
        "status": "not_applicable",
        "reason": "",
    }
    val = metrics.get(req.metric)
    threshold = _resolve_threshold(req, th)
    item["threshold"] = threshold

    if not _is_finite_number(val) or threshold is None:
        # 缺失 / NaN / Inf / 无阈值
        if required:
            item.update({
                "passed": False,
                "status": "missing_required",
                "value": None,
                "reason": (
                    f"required 指标缺失或非有限值（value={val!r}）"
                    if threshold is not None else "required 指标无门禁阈值"
                ),
            })
        else:
            item.update({"value": None, "reason": "模块未产出该指标"})
        return item

    item["value"] = round(float(val), 4)
    passed = val <= threshold if req.direction == "lower" else val >= threshold
    item.update({
        "passed": bool(passed),
        "status": "pass" if passed else "fail",
        "reason": "" if passed else (
            f"{item['label']} {item['value']} "
            f"{'>' if req.direction == 'lower' else '<'} 阈值 {threshold}"
        ),
    })
    return item


def _evaluate_tier_gates(tier_summaries: list[Any]) -> dict[str, Any]:
    """层级门禁：存在的层级必须 passed_threshold 且 passed_min_samples。"""
    if not tier_summaries:
        return {
            "metric": "tier_gates", "dimension": "分层",
            "requirement": "required", "source": "自研",
            "label": "分层门禁", "value": None, "threshold": None,
            "passed": None, "status": "not_applicable",
            "reason": "本 run 未声明评测层级",
        }
    failed = [
        f"[{ts.tier}] 通过率 {getattr(ts, 'pass_rate', 0):.1%}"
        + ("" if getattr(ts, "passed_threshold", True) else " 低于阈值")
        + ("" if getattr(ts, "passed_min_samples", True) else " 有效样本量不足（GATE-12）")
        for ts in tier_summaries
        if not getattr(ts, "passed_threshold", True)
        or not getattr(ts, "passed_min_samples", True)
    ]
    return {
        "metric": "tier_gates", "dimension": "分层",
        "requirement": "required", "source": "自研",
        "label": "分层门禁",
        "value": f"{sum(1 for t in tier_summaries if getattr(t, 'passed_threshold', True))}"
                 f"/{len(tier_summaries)}",
        "threshold": None,
        "passed": not failed,
        "status": "pass" if not failed else "fail",
        "reason": "；".join(failed),
    }


def _compute_single_module_gate(
    module: str,
    metrics: dict[str, Any],
    th: dict[str, float],
    tier_summaries: list[Any],
    ragas_mode: bool,
) -> list[dict[str, Any]]:
    policy = policy_for(module)
    items: list[dict[str, Any]] = []
    for req in policy.required:
        items.append(_evaluate_requirement(req, metrics, th, required=True))
    for req in policy.optional:
        if req.ragas_conditional:
            # 条件性 required：evaluator_mode 含 ragas 时按 required 判，
            # 否则 N/A（RAGAS 分值只在启用 RAGAS 评估的 run 上作为门禁）
            items.append(_evaluate_requirement(req, metrics, th, required=ragas_mode))
        else:
            items.append(_evaluate_requirement(req, metrics, th, required=False))
    for metric in policy.not_applicable_display:
        items.append({
            "metric": metric, "dimension": "—",
            "requirement": "not_applicable", "source": METRIC_SOURCE.get(metric, "—"),
            "label": GATE_METRIC_LABELS.get(metric, metric),
            "value": None, "threshold": None,
            "passed": None, "status": "not_applicable",
            "reason": f"{module} 模块不适用",
        })
    if policy.tier_gate:
        items.append(_evaluate_tier_gates(tier_summaries))
    return items


def compute_module_release_gate(
    report: Any,
    thresholds: dict[str, float] | None = None,
    validity: RunValidityVerdict | None = None,
) -> dict[str, Any]:
    """模块感知三态门禁——唯一裁决出口。

    report 需要：module / summaries（.module/.metrics）/ tier_summaries /
    metadata（evaluator_mode、run_validity 可选）。validity 缺失时优先读
    metadata.run_validity，再退回现算（历史报告兼容）。
    module="all" 时按 summary 逐模块裁决，verdict 取最差。
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if validity is None:
        validity = validity_from_report_metadata(getattr(report, "metadata", None))
    if validity is None:
        validity = classify_report_validity(report)

    module = str(getattr(report, "module", "") or "")
    ragas_mode = "ragas" in str((getattr(report, "metadata", None) or {}).get("evaluator_mode", "") or "")
    tier_summaries = list(getattr(report, "tier_summaries", None) or [])

    summaries = list(getattr(report, "summaries", None) or [])
    if module == "all" and summaries:
        targets = [
            (str(s.module), dict(getattr(s, "metrics", None) or {}))
            for s in summaries
        ]
    else:
        summary = next((s for s in summaries if str(getattr(s, "module", "")) == module), None)
        targets = [(module, dict(getattr(summary, "metrics", None) or {} if summary else {}))]

    items: list[dict[str, Any]] = []
    for mod, metrics in targets:
        mod_items = _compute_single_module_gate(mod, metrics, th, tier_summaries, ragas_mode)
        for it in mod_items:
            it["module"] = mod
        items.extend(mod_items)

    quality_fail = [
        it for it in items
        if it["requirement"] in ("required", "optional")
        and it["requirement"] != "not_applicable"
        and it["passed"] is False
    ]
    if validity.validity is not RunValidity.VALID:
        verdict = GateVerdict.INVALID
    elif quality_fail:
        verdict = GateVerdict.FAIL
    else:
        verdict = GateVerdict.PASS

    return {
        "module": module,
        "verdict": verdict.value,
        # compat：旧消费方读 overall 布尔——只有 PASS 为 True
        "overall": verdict is GateVerdict.PASS,
        "invalid_reason": validity.invalid_reason,
        "validity": validity.as_dict(),
        "items": items,
    }


def compute_metrics_gate(
    module: str,
    metrics: dict[str, float],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """仅指标面的轻量裁决（无 report / validity 视为 VALID，层级不判）。

    供 builder.compute_release_gate 旧签名委托；新调用方一律用
    compute_module_release_gate。
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    items = _compute_single_module_gate(module, dict(metrics or {}), th, [], False)
    quality_fail = [
        it for it in items
        if it["requirement"] != "not_applicable" and it["passed"] is False
    ]
    verdict = GateVerdict.FAIL if quality_fail else GateVerdict.PASS
    return {
        "module": module,
        "verdict": verdict.value,
        "overall": verdict is GateVerdict.PASS,
        "invalid_reason": "",
        "items": items,
    }


__all__ = [
    "GateVerdict",
    "MetricRequirement",
    "ModuleGatePolicy",
    "MODULE_GATE_POLICIES",
    "DEFAULT_POLICY",
    "policy_for",
    "compute_module_release_gate",
    "compute_metrics_gate",
    "DEFAULT_THRESHOLDS",
    "LOWER_IS_BETTER",
    "GATE_METRIC_LABELS",
    "METRIC_SOURCE",
    "GATE_METRICS",
]
